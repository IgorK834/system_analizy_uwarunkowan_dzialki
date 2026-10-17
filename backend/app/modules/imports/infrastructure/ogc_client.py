"""Ograniczony zasobowo klient odczytu WFS 2.0, WMS 1.3, CSW 2.0.2 i WCS 2.0.1."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import ipaddress
import socket
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, cast
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from app.core.ru_contracts import (
    RuContractError,
    exception_report_message,
    parse_xml_root,
)
from app.shared.provenance import Provenance


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_XML_MEDIA_TYPES = {"application/xml", "text/xml", "application/gml+xml"}
_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
# Kolejność osi `bbox` w WMS 1.3.0 wynika z definicji układu: EPSG:4326 i EPSG:2180 podają współrzędne w
# kolejności (szerokość/północ, długość/wschód) → "yx" w zapisie (x = wschód, y = północ); CRS:84 jest
# wyjątkiem (długość, szerokość). Dla EPSG:2180 wcześniej było "xy", co wysyłało lustrzane współrzędne — Rejestr
# Urbanistyczny odpowiadał wtedy POG innej gminy (zmierzone 2026-10-12, ADR-023).
_WMS_AXIS_ORDER = {
    "EPSG:4326": "yx",
    "CRS:84": "xy",
    "EPSG:2180": "yx",
}


@dataclass(frozen=True)
class OgcResult:
    """Artefakt odpowiedzi, wyodrębnione rekordy i jego provenance."""

    artifact: bytes
    features: tuple[bytes, ...]
    source: Provenance
    complete: bool
    headers: Mapping[str, str] = field(default_factory=dict)


class OgcError(RuntimeError):
    """Bazowy jawny błąd OGC z provenance także dla nieudanego odczytu."""

    def __init__(
        self,
        message: str,
        *,
        source: Provenance,
        partial: OgcResult | None = None,
    ) -> None:
        super().__init__(message)
        self.source = source
        self.partial = partial


class OgcTransportError(OgcError):
    """Błąd HTTPS, DNS, przekierowania, timeoutu albo statusu HTTP."""


class OgcContractError(OgcError):
    """Odpowiedź nie spełnia oczekiwanego kontraktu OGC/XML/MIME."""


class OgcLimitError(OgcError):
    """Osiągnięto jawny limit bajtów, stron albo cech."""


class OgcExceptionReportError(OgcContractError):
    """Usługa odpowiedziała ``ows:ExceptionReport`` z jawnym kodem wyjątku.

    WCS zgłasza w ten sposób m.in. ``ExtentError`` (żądany obszar nie przecina
    pokrycia) i ``NoSuchCoverage`` — wywołujący rozróżnia je po ``exception_code``.
    """

    def __init__(
        self,
        message: str,
        *,
        source: Provenance,
        exception_code: str | None,
        http_status: int | None,
    ) -> None:
        super().__init__(message, source=source)
        self.exception_code = exception_code
        self.http_status = http_status


class OgcCancelled(OgcError):
    """Operacja przerwana na żądanie operatora (sygnał, ``cancel``); pobrane strony zostają w magazynie stron."""


# Atrybuty odpowiedzi WFS, które opisują chwilę i sposób jej wydania, a nie dane. Artefakt złożony z wielu stron ma
# być treścią (ten sam zbiór obiektów = te same bajty = ten sam skrót), więc nie niesie ich dalej: ``timeStamp``
# różni się w każdej odpowiedzi, a ``next``/``previous`` wskazują strony pojedynczego zapytania.
VOLATILE_WFS_ATTRIBUTES = frozenset({"timeStamp", "next", "previous"})


@dataclass(frozen=True)
class WfsPageEvent:
    """Postęp pobierania WFS po każdej stronie (pobranej z sieci albo odczytanej z magazynu stron)."""

    type_name: str
    start_index: int
    returned: int
    fetched: int
    matched: int | None
    pages_done: int
    pages_total: int | None
    from_store: bool


class WfsPageStore(Protocol):
    """Magazyn stron dla wznawiania pobierania (implementacja plikowa: ``infrastructure/page_store.py``).

    ``prepare`` dostaje token zapytania (typ, filtr, ``numberMatched``, identyfikatory pierwszej strony): zapisane strony
    z innym tokenem pochodzą z innego stanu usługi i muszą zostać odrzucone.
    """

    def prepare(self, token: str) -> None: ...

    def load(self, start_index: int) -> bytes | None: ...

    def save(self, start_index: int, content: bytes) -> None: ...


@dataclass(frozen=True)
class _WfsPage:
    start: int
    payload: "_HttpPayload"
    root: ElementTree.Element
    members: list[ElementTree.Element]
    from_store: bool = False


@dataclass(frozen=True)
class OgcClientConfig:
    """Limity jednego klienta; wszystkie wartości mają bezpieczne granice."""

    allowed_hosts: frozenset[str]
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    total_timeout_seconds: float = 120.0
    max_response_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_pages: int = 500
    max_features: int = 10_000
    max_redirects: int = 3
    max_xml_depth: int = 64
    max_xml_nodes: int = 100_000
    retries: int = 2
    backoff_seconds: float = 0.1

    def __post_init__(self) -> None:
        if not self.allowed_hosts:
            raise ValueError("Allowlista hostów OGC nie może być pusta.")
        positive = (
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
            self.total_timeout_seconds,
            self.max_response_bytes,
            self.max_total_bytes,
            self.max_pages,
            self.max_features,
            self.max_xml_depth,
            self.max_xml_nodes,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Timeouty i limity OGC muszą być dodatnie.")
        if self.max_redirects < 0 or self.retries < 0 or self.backoff_seconds < 0:
            raise ValueError("Liczby prób, przekierowań i backoff nie mogą być ujemne.")


@dataclass(frozen=True)
class _HttpPayload:
    content: bytes
    content_type: str
    url: str
    status_code: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpResult:
    """Odpowiedź ogólnego zasobu HTTP (np. publiczne API Rejestru Urbanistycznego) z provenance."""

    content: bytes
    status_code: int
    content_type: str
    headers: Mapping[str, str]
    source: Provenance


Resolver = Callable[[str], Iterable[str]]


class OgcClient:
    """Wspólny klient bez automatycznych redirectów i nieograniczonych odczytów."""

    def __init__(
        self,
        *,
        source_id: str,
        config: OgcClientConfig,
        client: httpx.Client | None = None,
        resolver: Resolver | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        """``before_request`` jest wołane przed KAŻDYM żądaniem HTTP (także ponowieniem i przekierowaniem).

        Wyjątek z tej funkcji przerywa operację bez wysłania żądania i nie jest przechwytywany przez klienta —
        służy to egzekwowaniu budżetu żądań na analizę (AU-105).
        """
        self._source_id = source_id
        self._config = config
        self._before_request = before_request
        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.read_timeout_seconds,
            pool=config.connect_timeout_seconds,
        )
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None
        self._resolver = resolver or self._system_resolver
        self._sleep = sleep
        self._monotonic = monotonic

    @classmethod
    def for_urls(
        cls,
        *,
        source_id: str,
        urls: Iterable[str],
        config: OgcClientConfig | None = None,
        config_overrides: Mapping[str, object] | None = None,
        **kwargs: object,
    ) -> "OgcClient":
        hosts = frozenset(
            host
            for value in urls
            if (host := (urlsplit(value).hostname or "").casefold())
        )
        if config is None:
            config = OgcClientConfig(
                allowed_hosts=hosts,
                **cast("dict[str, Any]", dict(config_overrides or {})),
            )
        elif config_overrides:
            raise ValueError("Nie łącz config z config_overrides.")
        return cls(source_id=source_id, config=config, **kwargs)  # type: ignore[arg-type]

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OgcClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def fetch_wfs(
        self,
        url: str,
        *,
        type_name: str,
        srs_name: str,
        extra_params: Mapping[str, str] | None = None,
        concurrency: int = 1,
        page_store: WfsPageStore | None = None,
        on_page: Callable[[WfsPageEvent], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> OgcResult:
        """Pobiera wszystkie strony WFS przy jawnym ``count=100``.

        Serwery mogą zwracać strony krótsze niż zamówione (np. RU: zawsze 10),
        więc ``startIndex`` wynika z liczby już pobranych obiektów, a koniec
        paginacji z ``numberMatched``, a nie z rozmiaru strony.

        Domyślnie strony są pobierane po kolei. Gdy podano ``concurrency`` > 1, ``page_store``
        (wznawianie), ``on_page`` (postęp) albo ``cancel``, działa tryb stronicowany
        (``_fetch_wfs_paged``): pierwsza strona ujawnia ``numberMatched`` i rozmiar strony, a reszta jest
        pobierana równolegle (limit grzeczności ``concurrency``) i składana w kolejności ``startIndex``.
        """

        if concurrency > 1 or page_store is not None or on_page is not None or cancel is not None:
            return self._fetch_wfs_paged(
                url,
                type_name=type_name,
                srs_name=srs_name,
                extra_params=extra_params,
                concurrency=concurrency,
                page_store=page_store,
                on_page=on_page,
                cancel=cancel,
            )

        operation = "WFS:GetFeature"
        started_at = datetime.now(timezone.utc)
        deadline = self._monotonic() + self._config.total_timeout_seconds
        features: list[bytes] = []
        members: list[ElementTree.Element] = []
        seen_hashes: set[str] = set()
        seen_page_ids: set[tuple[str, ...]] = set()
        seen_feature_ids: set[str] = set()
        first_root: ElementTree.Element | None = None
        total_bytes = 0

        for page_index in range(self._config.max_pages + 1):
            if page_index >= self._config.max_pages:
                partial = self._wfs_result(
                    url, operation, started_at, first_root, members, features, False
                )
                raise self._limit_error(
                    "WFS przekroczył limit liczby stron.",
                    url,
                    operation,
                    started_at,
                    partial=partial,
                )
            params = {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": type_name,
                "outputFormat": "application/gml+xml; version=3.2",
                "srsName": srs_name,
                "count": "100",
                "startIndex": str(len(features)),
            }
            if extra_params:
                params.update(extra_params)
            try:
                # RU sporadycznie odpowiada przejściowym HTTP 400 „Invalid
                # request” na poprawne zapytanie; ta sama strona przy ponowieniu
                # przechodzi, więc błędy transportu ponawiamy per strona.
                for attempt in range(self._config.retries + 1):
                    try:
                        payload = self._request(
                            url,
                            params=params,
                            operation=operation,
                            started_at=started_at,
                            deadline=deadline,
                            byte_limit=min(
                                self._config.max_response_bytes,
                                self._config.max_total_bytes - total_bytes,
                            ),
                        )
                        break
                    except OgcTransportError as exc:
                        # Timeouty i 5xx ponawia już ``_request``.
                        if attempt >= self._config.retries or not re.search(
                            r"HTTP 4\d\d", str(exc)
                        ):
                            raise
                        self._backoff(attempt, deadline, url, operation, started_at)
                total_bytes += len(payload.content)
                root = self._xml_root(payload, operation, started_at)
            except OgcLimitError as exc:
                partial = self._wfs_result(
                    url, operation, started_at, first_root, members, features, False
                )
                exc.partial = partial
                raise

            if first_root is None:
                first_root = root
            page_hash = hashlib.sha256(payload.content).hexdigest()
            page_members = self._feature_members(root)
            page_features = tuple(
                ElementTree.tostring(next(iter(member)), encoding="utf-8")
                for member in page_members
                if next(iter(member), None) is not None
            )
            page_ids = tuple(
                filter(None, (self._feature_id(item) for item in page_members))
            )
            page_id_signature = tuple(sorted(page_ids))
            if page_hash in seen_hashes or (
                page_id_signature and page_id_signature in seen_page_ids
            ):
                return self._wfs_result(
                    payload.url,
                    operation,
                    started_at,
                    first_root,
                    members,
                    features,
                    False,
                )
            if set(page_ids) & seen_feature_ids:
                return self._wfs_result(
                    payload.url,
                    operation,
                    started_at,
                    first_root,
                    members,
                    features,
                    False,
                )
            seen_hashes.add(page_hash)
            if page_id_signature:
                seen_page_ids.add(page_id_signature)
            seen_feature_ids.update(page_ids)

            raw_returned = root.attrib.get("numberReturned")
            try:
                returned = (
                    len(page_members) if raw_returned is None else int(raw_returned)
                )
            except ValueError as exc:
                raise self._contract_error(
                    f"WFS zwrócił niepoprawne numberReturned={raw_returned!r}.",
                    payload.url,
                    operation,
                    started_at,
                    content=payload.content,
                ) from exc
            if returned != len(page_members):
                raise self._contract_error(
                    "WFS numberReturned nie zgadza się z liczbą elementów member.",
                    payload.url,
                    operation,
                    started_at,
                    content=payload.content,
                )
            if len(features) + returned > self._config.max_features:
                partial = self._wfs_result(
                    payload.url,
                    operation,
                    started_at,
                    first_root,
                    members,
                    features,
                    False,
                )
                raise self._limit_error(
                    "WFS przekroczył limit liczby cech.",
                    payload.url,
                    operation,
                    started_at,
                    partial=partial,
                )

            members.extend(copy.deepcopy(member) for member in page_members)
            features.extend(page_features)
            matched_raw = root.attrib.get("numberMatched")
            matched = (
                int(matched_raw) if matched_raw and matched_raw.isdigit() else None
            )
            if returned == 0:
                complete = matched is None or len(features) >= matched
                return self._wfs_result(
                    payload.url,
                    operation,
                    started_at,
                    first_root,
                    members,
                    features,
                    complete,
                )
            if matched is not None and len(features) >= matched:
                return self._wfs_result(
                    payload.url,
                    operation,
                    started_at,
                    first_root,
                    members,
                    features,
                    True,
                )
            if matched is None and returned < 100:
                return self._wfs_result(
                    payload.url,
                    operation,
                    started_at,
                    first_root,
                    members,
                    features,
                    True,
                )

        raise AssertionError("Nieosiągalna gałąź paginacji WFS.")

    # --- tryb stronicowany: równoległe strony, wznawianie i postęp (AU-106) ---------------------------------

    def _wfs_page(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        start: int,
        operation: str,
        started_at: datetime,
        deadline: float,
        byte_limit: int,
        cancel: threading.Event | None,
    ) -> _WfsPage:
        """Jedna strona z siecią: ponowienia przejściowego HTTP 4xx, parsowanie i spójność ``numberReturned``."""
        if cancel is not None and cancel.is_set():
            raise OgcCancelled(
                "Pobieranie przerwane na żądanie operatora.",
                source=self._error_provenance(url, operation, started_at, "cancelled"),
            )
        payload: _HttpPayload | None = None
        # RU sporadycznie odpowiada przejściowym HTTP 400 „Invalid request” na poprawne zapytanie; ta sama strona
        # przy ponowieniu przechodzi, więc błędy transportu ponawiamy per strona (timeouty i 5xx ponawia ``_request``).
        for attempt in range(self._config.retries + 1):
            try:
                payload = self._request(
                    url,
                    params=params,
                    operation=operation,
                    started_at=started_at,
                    deadline=deadline,
                    byte_limit=byte_limit,
                )
                break
            except OgcTransportError as exc:
                if attempt >= self._config.retries or not re.search(r"HTTP 4\d\d", str(exc)):
                    raise
                self._backoff(attempt, deadline, url, operation, started_at)
        assert payload is not None
        return self._parse_wfs_page(payload, start, operation, started_at)

    def _parse_wfs_page(
        self, payload: _HttpPayload, start: int, operation: str, started_at: datetime, *, from_store: bool = False
    ) -> _WfsPage:
        root = self._xml_root(payload, operation, started_at)
        members = self._feature_members(root)
        raw_returned = root.attrib.get("numberReturned")
        try:
            returned = len(members) if raw_returned is None else int(raw_returned)
        except ValueError as exc:
            raise self._contract_error(
                f"WFS zwrócił niepoprawne numberReturned={raw_returned!r}.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            ) from exc
        if returned != len(members):
            raise self._contract_error(
                "WFS numberReturned nie zgadza się z liczbą elementów member.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        return _WfsPage(start=start, payload=payload, root=root, members=members, from_store=from_store)

    def _fetch_wfs_paged(
        self,
        url: str,
        *,
        type_name: str,
        srs_name: str,
        extra_params: Mapping[str, str] | None,
        concurrency: int,
        page_store: WfsPageStore | None,
        on_page: Callable[[WfsPageEvent], None] | None,
        cancel: threading.Event | None,
    ) -> OgcResult:
        operation = "WFS:GetFeature"
        started_at = datetime.now(timezone.utc)
        deadline = self._monotonic() + self._config.total_timeout_seconds
        workers = max(1, min(int(concurrency), 8))
        lock = threading.Lock()
        state = {"bytes": 0, "pages_done": 0, "fetched": 0}

        def params_for(start: int) -> dict[str, str]:
            params = {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": type_name,
                "outputFormat": "application/gml+xml; version=3.2",
                "srsName": srs_name,
                "count": "100",
                "startIndex": str(start),
            }
            if extra_params:
                params.update(extra_params)
            return params

        def byte_limit() -> int:
            with lock:
                return min(self._config.max_response_bytes, self._config.max_total_bytes - state["bytes"])

        def account(page: _WfsPage, matched: int | None, pages_total: int | None) -> None:
            with lock:
                state["bytes"] += len(page.payload.content)
                state["pages_done"] += 1
                state["fetched"] += len(page.members)
                event = WfsPageEvent(
                    type_name=type_name,
                    start_index=page.start,
                    returned=len(page.members),
                    fetched=state["fetched"],
                    matched=matched,
                    pages_done=state["pages_done"],
                    pages_total=pages_total,
                    from_store=page.from_store,
                )
            if on_page is not None:
                on_page(event)

        first = self._wfs_page(
            url,
            params_for(0),
            start=0,
            operation=operation,
            started_at=started_at,
            deadline=deadline,
            byte_limit=byte_limit(),
            cancel=cancel,
        )
        matched_raw = first.root.attrib.get("numberMatched")
        if not (matched_raw and matched_raw.isdigit()):
            # Brak deklaracji liczby wyników: nie da się zaplanować stron z góry — stary tryb sekwencyjny.
            return self.fetch_wfs(url, type_name=type_name, srs_name=srs_name, extra_params=extra_params)
        matched = int(matched_raw)
        page_size = len(first.members)
        first_ids = sorted(filter(None, (self._feature_id(item) for item in first.members)))
        starts = list(range(page_size, matched, page_size)) if page_size > 0 else []
        pages_total = 1 + len(starts)
        if page_store is not None:
            token = hashlib.sha256(
                "\n".join(
                    [
                        type_name,
                        srs_name,
                        json.dumps(dict(extra_params or {}), sort_keys=True),
                        str(matched),
                        str(page_size),
                        *first_ids,
                    ]
                ).encode("utf-8")
            ).hexdigest()
            page_store.prepare(token)
            page_store.save(0, first.payload.content)
        account(first, matched, pages_total)
        pages: dict[int, _WfsPage] = {0: first}

        def load(start: int) -> _WfsPage:
            if page_store is not None:
                stored = page_store.load(start)
                if stored is not None:
                    try:
                        page = self._parse_wfs_page(
                            _HttpPayload(stored, "application/gml+xml", url), start, operation, started_at, from_store=True
                        )
                        if len(page.members) == min(page_size, matched - start):
                            return page
                    except OgcContractError:
                        pass  # uszkodzona strona z magazynu — pobierz ponownie
            page = self._wfs_page(
                url,
                params_for(start),
                start=start,
                operation=operation,
                started_at=started_at,
                deadline=deadline,
                byte_limit=byte_limit(),
                cancel=cancel,
            )
            if page_store is not None:
                page_store.save(start, page.payload.content)
            return page

        def work(start: int) -> _WfsPage:
            page = load(start)
            account(page, matched, pages_total)
            return page

        if starts:
            abort = threading.Event()

            def guarded(start: int) -> _WfsPage:
                if abort.is_set():
                    raise OgcCancelled(
                        "Pobieranie przerwane po błędzie innej strony.",
                        source=self._error_provenance(url, operation, started_at, "cancelled"),
                    )
                return work(start)

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wfs-page") as pool:
                futures = {pool.submit(guarded, start): start for start in starts}
                done, pending = wait(futures, return_when=FIRST_EXCEPTION)
                failure = next((f.exception() for f in done if f.exception() is not None), None)
                if failure is not None:
                    abort.set()
                    for future in pending:
                        future.cancel()
                    pool.shutdown(wait=True, cancel_futures=True)
                    # Najpierw prawdziwa przyczyna (np. limit bajtów), dopiero potem wtórne przerwania.
                    primary = next(
                        (
                            f.exception()
                            for f in futures
                            if f.done() and not f.cancelled() and f.exception() is not None
                            and not isinstance(f.exception(), OgcCancelled)
                        ),
                        failure,
                    )
                    raise cast(BaseException, primary)
                for future, start in futures.items():
                    pages[start] = future.result()

        # Składanie w kolejności startIndex: wynik nie zależy od kolejności zakończenia równoległych żądań.
        features: list[bytes] = []
        members: list[ElementTree.Element] = []
        seen_ids: set[str] = set()
        seen_hashes: set[str] = set()
        complete = True
        fetched = 0
        for start in sorted(pages):
            page = pages[start]
            if start != fetched:
                break  # luka po krótkiej stronie — reszta w trybie sekwencyjnym poniżej
            page_ids = tuple(filter(None, (self._feature_id(item) for item in page.members)))
            page_hash = hashlib.sha256(page.payload.content).hexdigest()
            if page_hash in seen_hashes or set(page_ids) & seen_ids:
                return self._wfs_result(
                    url, operation, started_at, first.root, members, features, False
                )
            seen_hashes.add(page_hash)
            seen_ids.update(page_ids)
            page_features = [
                ElementTree.tostring(next(iter(member)), encoding="utf-8")
                for member in page.members
                if next(iter(member), None) is not None
            ]
            if len(features) + len(page_features) > self._config.max_features:
                partial = self._wfs_result(url, operation, started_at, first.root, members, features, False)
                raise self._limit_error(
                    "WFS przekroczył limit liczby cech.", url, operation, started_at, partial=partial
                )
            members.extend(copy.deepcopy(member) for member in page.members)
            features.extend(page_features)
            fetched += len(page.members)
            if len(page.members) < min(page_size, matched - start):
                break  # serwer skrócił stronę — dalej według faktycznej liczby obiektów

        pages_used = state["pages_done"]
        while fetched < matched:
            if pages_used >= self._config.max_pages:
                partial = self._wfs_result(url, operation, started_at, first.root, members, features, False)
                raise self._limit_error(
                    "WFS przekroczył limit liczby stron.", url, operation, started_at, partial=partial
                )
            page = self._wfs_page(
                url,
                params_for(fetched),
                start=fetched,
                operation=operation,
                started_at=started_at,
                deadline=deadline,
                byte_limit=byte_limit(),
                cancel=cancel,
            )
            pages_used += 1
            if page_store is not None and fetched in set(starts):
                page_store.save(fetched, page.payload.content)
            account(page, matched, None)
            if not page.members:
                complete = False
                break
            page_ids = tuple(filter(None, (self._feature_id(item) for item in page.members)))
            page_hash = hashlib.sha256(page.payload.content).hexdigest()
            if page_hash in seen_hashes or set(page_ids) & seen_ids:
                complete = False
                break
            seen_hashes.add(page_hash)
            seen_ids.update(page_ids)
            page_features = [
                ElementTree.tostring(next(iter(member)), encoding="utf-8")
                for member in page.members
                if next(iter(member), None) is not None
            ]
            if len(features) + len(page_features) > self._config.max_features:
                partial = self._wfs_result(url, operation, started_at, first.root, members, features, False)
                raise self._limit_error(
                    "WFS przekroczył limit liczby cech.", url, operation, started_at, partial=partial
                )
            members.extend(copy.deepcopy(member) for member in page.members)
            features.extend(page_features)
            fetched += len(page.members)

        return self._wfs_result(
            url, operation, started_at, first.root, members, features, complete and fetched >= matched
        )

    def fetch_csw(
        self,
        url: str,
        *,
        type_names: str = "csw:Record",
        output_schema: str = "http://www.opengis.net/cat/csw/2.0.2",
        max_records: int = 10,
        extra_params: Mapping[str, str] | None = None,
    ) -> OgcResult:
        """Pobiera strony CSW 2.0.2 zgodnie z ``nextRecord``."""

        operation = "CSW:GetRecords"
        started_at = datetime.now(timezone.utc)
        deadline = self._monotonic() + self._config.total_timeout_seconds
        records: list[bytes] = []
        seen_hashes: set[str] = set()
        seen_positions: set[int] = set()
        start_position = 1
        total_bytes = 0

        for page_index in range(self._config.max_pages + 1):
            if page_index >= self._config.max_pages:
                partial = self._plain_result(
                    b"\n".join(records), records, url, operation, started_at, False
                )
                raise self._limit_error(
                    "CSW przekroczył limit liczby stron.",
                    url,
                    operation,
                    started_at,
                    partial=partial,
                )
            params = {
                "service": "CSW",
                "version": "2.0.2",
                "request": "GetRecords",
                "resultType": "results",
                "elementSetName": "full",
                "typeNames": type_names,
                "outputSchema": output_schema,
                "outputFormat": "application/xml",
                "maxRecords": str(max_records),
                "startPosition": str(start_position),
            }
            if extra_params:
                params.update(extra_params)
            payload = self._request(
                url,
                params=params,
                operation=operation,
                started_at=started_at,
                deadline=deadline,
                byte_limit=min(
                    self._config.max_response_bytes,
                    self._config.max_total_bytes - total_bytes,
                ),
            )
            total_bytes += len(payload.content)
            root = self._xml_root(payload, operation, started_at)
            digest = hashlib.sha256(payload.content).hexdigest()
            search_results = next(
                (
                    element
                    for element in root.iter()
                    if self._local_name(element.tag) == "SearchResults"
                ),
                None,
            )
            if search_results is None:
                raise self._contract_error(
                    "CSW nie zawiera SearchResults.",
                    payload.url,
                    operation,
                    started_at,
                    content=payload.content,
                )
            page_records = [
                ElementTree.tostring(item, encoding="utf-8") for item in search_results
            ]
            try:
                next_record = int(search_results.attrib.get("nextRecord", "0"))
            except ValueError as exc:
                raise self._contract_error(
                    "CSW zwrócił niepoprawne nextRecord.",
                    payload.url,
                    operation,
                    started_at,
                    content=payload.content,
                ) from exc
            if digest in seen_hashes or next_record in seen_positions:
                return self._plain_result(
                    b"\n".join(records),
                    records,
                    payload.url,
                    operation,
                    started_at,
                    False,
                )
            if len(records) + len(page_records) > self._config.max_features:
                partial = self._plain_result(
                    b"\n".join(records),
                    records,
                    payload.url,
                    operation,
                    started_at,
                    False,
                )
                raise self._limit_error(
                    "CSW przekroczył limit liczby rekordów.",
                    payload.url,
                    operation,
                    started_at,
                    partial=partial,
                )
            seen_hashes.add(digest)
            records.extend(page_records)
            if next_record == 0:
                return self._plain_result(
                    b"\n".join(records),
                    records,
                    payload.url,
                    operation,
                    started_at,
                    True,
                )
            if not page_records or next_record <= start_position:
                return self._plain_result(
                    b"\n".join(records),
                    records,
                    payload.url,
                    operation,
                    started_at,
                    False,
                )
            seen_positions.add(start_position)
            start_position = next_record

        raise AssertionError("Nieosiągalna gałąź paginacji CSW.")

    def fetch_resource(
        self,
        url: str,
        *,
        method: str = "GET",
        params: Mapping[str, str] | None = None,
        json_body: Any = None,
        operation: str = "HTTP",
        byte_limit: int | None = None,
        total_timeout_seconds: float | None = None,
        readable_error_statuses: frozenset[int] = frozenset(),
    ) -> HttpResult:
        """Ogólne żądanie HTTPS (GET albo POST JSON) przez te same zabezpieczenia co usługi OGC.

        Allowlista hostów, sprawdzenie DNS/IP, brak automatycznych przekierowań, limity bajtów i czasu oraz
        ponowienia dla 5xx/timeoutów obowiązują także tutaj. Status z ``readable_error_statuses`` (np. 429) jest
        zwracany do wołającego zamiast błędu — to on decyduje o odczekaniu.
        """
        started_at = datetime.now(timezone.utc)
        payload = self._request(
            url,
            params=dict(params or {}),
            operation=operation,
            started_at=started_at,
            deadline=self._monotonic() + (total_timeout_seconds or self._config.total_timeout_seconds),
            byte_limit=byte_limit or self._config.max_response_bytes,
            readable_error_statuses=readable_error_statuses,
            method=method,
            json_body=json_body,
        )
        result = self._plain_result(payload.content, (), payload.url, operation, started_at, True)
        return HttpResult(
            content=payload.content,
            status_code=payload.status_code,
            content_type=payload.content_type,
            headers=dict(payload.headers),
            source=result.source,
        )

    def fetch_wms(
        self,
        url: str,
        *,
        layers: Iterable[str],
        bbox: tuple[float, float, float, float],
        crs: str,
        width: int,
        height: int,
        image_format: str = "image/png",
        styles: str = "",
    ) -> OgcResult:
        """Pobiera wyłącznie obraz podglądowy WMS 1.3.0 z poprawną osią CRS."""

        operation = "WMS:GetMap"
        started_at = datetime.now(timezone.utc)
        params = {
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": ",".join(layers),
            "styles": styles,
            "format": image_format,
            "transparent": "true",
            "crs": crs,
            "bbox": self.wms_bbox(crs, bbox),
            "width": str(width),
            "height": str(height),
        }
        payload = self._request(
            url,
            params=params,
            operation=operation,
            started_at=started_at,
            deadline=self._monotonic() + self._config.total_timeout_seconds,
            byte_limit=self._config.max_response_bytes,
        )
        media_type = self._media_type(payload.content_type)
        if media_type in _XML_MEDIA_TYPES or media_type.endswith("+xml"):
            self._xml_root(payload, operation, started_at)
            raise self._contract_error(
                "WMS GetMap zwrócił XML zamiast obrazu.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        if media_type != image_format.casefold():
            raise self._contract_error(
                f"WMS zwrócił nieoczekiwany Content-Type {payload.content_type!r}.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        return self._plain_result(
            payload.content, (), payload.url, operation, started_at, True
        )

    def fetch_wms_feature_info(
        self,
        url: str,
        *,
        layer: str,
        bbox: tuple[float, float, float, float],
        crs: str = "EPSG:2180",
        width: int = 2,
        height: int = 2,
        i: int = 1,
        j: int = 1,
        info_format: str = "application/json",
        feature_count: int = 10,
    ) -> OgcResult:
        """Bezpieczny odczyt GetFeatureInfo na potrzeby discovery, bez geometrii pól."""

        operation = "WMS:GetFeatureInfo"
        started_at = datetime.now(timezone.utc)
        payload = self._request(
            url,
            params={
                "service": "WMS",
                "version": "1.3.0",
                "request": "GetFeatureInfo",
                "layers": layer,
                "query_layers": layer,
                "styles": "",
                "crs": crs,
                "bbox": self.wms_bbox(crs, bbox),
                "width": str(width),
                "height": str(height),
                "i": str(i),
                "j": str(j),
                "info_format": info_format,
                "feature_count": str(feature_count),
            },
            operation=operation,
            started_at=started_at,
            deadline=self._monotonic() + self._config.total_timeout_seconds,
            byte_limit=self._config.max_response_bytes,
        )
        media_type = self._media_type(payload.content_type)
        if media_type in _XML_MEDIA_TYPES or media_type.endswith("+xml"):
            self._xml_root(payload, operation, started_at)
        elif media_type and media_type not in {
            "application/json",
            "text/json",
            "text/plain",
        }:
            raise self._contract_error(
                f"GetFeatureInfo zwrócił nieoczekiwany MIME {payload.content_type!r}.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        return self._plain_result(
            payload.content, (), payload.url, operation, started_at, True
        )

    def fetch_wcs_description(
        self,
        url: str,
        *,
        coverage_id: str,
        version: str = "2.0.1",
    ) -> OgcResult:
        """Pobiera ``DescribeCoverage`` jednego pokrycia jako zweryfikowany XML."""

        operation = "WCS:DescribeCoverage"
        started_at = datetime.now(timezone.utc)
        payload = self._request(
            url,
            params={
                "service": "WCS",
                "version": version,
                "request": "DescribeCoverage",
                "coverageId": coverage_id,
            },
            operation=operation,
            started_at=started_at,
            deadline=self._monotonic() + self._config.total_timeout_seconds,
            byte_limit=self._config.max_response_bytes,
            readable_error_statuses=frozenset({400, 404}),
        )
        if payload.status_code != 200:
            raise self._exception_report_error(payload, operation, started_at)
        self._xml_root(payload, operation, started_at)
        return self._plain_result(
            payload.content, (), payload.url, operation, started_at, True
        )

    def fetch_wcs_coverage(
        self,
        url: str,
        *,
        coverage_id: str,
        subsets: Sequence[tuple[str, float, float]],
        media_type: str = "image/tiff",
        version: str = "2.0.1",
    ) -> OgcResult:
        """Pobiera wycinek pokrycia WCS 2.0.1 (GetCoverage, KVP) jako GeoTIFF.

        Subsety są przekazywane jako powtórzony parametr ``subset=oś(min,max)``
        w kolejności wywołującego. Odpowiedź ``ExceptionReport`` (także z HTTP
        400/404) jest mapowana na ``OgcExceptionReportError`` z kodem wyjątku.
        """

        if not subsets:
            raise ValueError("GetCoverage wymaga co najmniej jednego subsetu.")
        operation = "WCS:GetCoverage"
        started_at = datetime.now(timezone.utc)
        params: list[tuple[str, str]] = [
            ("service", "WCS"),
            ("version", version),
            ("request", "GetCoverage"),
            ("coverageId", coverage_id),
            ("format", media_type),
        ]
        for axis, low, high in subsets:
            if not low < high:
                raise ValueError(f"Subset osi {axis!r} musi mieć min < max.")
            params.append(
                ("subset", f"{axis}({format(low, '.15g')},{format(high, '.15g')})")
            )
        payload = self._request(
            url,
            params=params,
            operation=operation,
            started_at=started_at,
            deadline=self._monotonic() + self._config.total_timeout_seconds,
            byte_limit=self._config.max_response_bytes,
            readable_error_statuses=frozenset({400, 404}),
        )
        received_type = self._media_type(payload.content_type)
        if (
            payload.status_code != 200
            or received_type in _XML_MEDIA_TYPES
            or received_type.endswith("+xml")
        ):
            raise self._exception_report_error(payload, operation, started_at)
        if received_type != media_type.casefold():
            raise self._contract_error(
                f"WCS zwrócił nieoczekiwany Content-Type {payload.content_type!r}.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        if media_type.casefold() == "image/tiff" and not payload.content.startswith(
            _TIFF_MAGIC
        ):
            raise self._contract_error(
                "Odpowiedź WCS deklaruje image/tiff, ale nie jest plikiem TIFF.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        return replace(self._plain_result(
            payload.content, (), payload.url, operation, started_at, True
        ), headers=payload.headers)

    def _exception_report_error(
        self, payload: _HttpPayload, operation: str, started_at: datetime
    ) -> OgcError:
        """Mapuje odpowiedź błędu na jawny wyjątek z kodem ``exceptionCode``."""

        media_type = self._media_type(payload.content_type)
        is_xml = media_type in _XML_MEDIA_TYPES or media_type.endswith("+xml")
        if not is_xml or not payload.content.lstrip().startswith(b"<"):
            if payload.status_code != 200:
                return self._transport_error(
                    f"Usługa OGC zwróciła HTTP {payload.status_code}.",
                    payload.url,
                    operation,
                    started_at,
                )
            return self._contract_error(
                "Odpowiedź OGC nie jest oczekiwanym dokumentem.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        try:
            root = parse_xml_root(
                payload.content,
                max_depth=self._config.max_xml_depth,
                max_nodes=self._config.max_xml_nodes,
            )
        except RuContractError as exc:
            return self._contract_error(
                str(exc), payload.url, operation, started_at, content=payload.content
            )
        code: str | None = None
        for element in root.iter():
            if self._local_name(element.tag) == "Exception":
                code = element.attrib.get("exceptionCode")
                break
        message = exception_report_message(root)
        if message is None and code is None:
            return self._contract_error(
                f"Usługa OGC zwróciła HTTP {payload.status_code} bez ExceptionReport.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        source = self._error_provenance(payload.url, operation, started_at, "contract")
        source = Provenance(
            **{
                **source.__dict__,
                "content_hash": hashlib.sha256(payload.content).hexdigest(),
            }
        )
        return OgcExceptionReportError(
            f"OGC ExceptionReport ({code or 'bez kodu'}): {message or ''}".strip(),
            source=source,
            exception_code=code,
            http_status=payload.status_code,
        )

    @staticmethod
    def wms_bbox(crs: str, bbox: tuple[float, float, float, float]) -> str:
        """Formatuje BBOX według tabeli osi WMS 1.3.0."""

        order = _WMS_AXIS_ORDER.get(crs.upper())
        if order is None:
            raise ValueError(f"Brak jawnej reguły osi WMS dla CRS {crs!r}.")
        min_x, min_y, max_x, max_y = bbox
        values = (min_y, min_x, max_y, max_x) if order == "yx" else bbox
        return ",".join(format(value, ".15g") for value in values)

    def _request(
        self,
        url: str,
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]],
        operation: str,
        started_at: datetime,
        deadline: float,
        byte_limit: int,
        readable_error_statuses: frozenset[int] = frozenset(),
        method: str = "GET",
        json_body: Any = None,
    ) -> _HttpPayload:
        if byte_limit <= 0:
            raise self._limit_error(
                "OGC przekroczył łączny limit bajtów.", url, operation, started_at
            )
        current_url = url
        current_params: Mapping[str, str] | Sequence[tuple[str, str]] | None = params
        redirects = 0
        while True:
            self._validate_url(current_url, operation, started_at)
            last_error: Exception | None = None
            for attempt in range(self._config.retries + 1):
                self._check_deadline(deadline, current_url, operation, started_at)
                if self._before_request is not None:
                    self._before_request()
                try:
                    with self._client.stream(
                        method,
                        current_url,
                        params=cast(Any, current_params),
                        **({"json": json_body} if json_body is not None else {}),
                    ) as response:
                        if response.status_code >= 500:
                            last_error = RuntimeError(f"HTTP {response.status_code}")
                            if attempt < self._config.retries:
                                self._backoff(
                                    attempt,
                                    deadline,
                                    current_url,
                                    operation,
                                    started_at,
                                )
                                continue
                            raise self._transport_error(
                                f"Usługa OGC zwróciła HTTP {response.status_code}.",
                                str(response.request.url),
                                operation,
                                started_at,
                            )
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise self._transport_error(
                                    "Przekierowanie OGC nie zawiera Location.",
                                    str(response.request.url),
                                    operation,
                                    started_at,
                                )
                            redirects += 1
                            if redirects > self._config.max_redirects:
                                raise self._transport_error(
                                    "Usługa OGC przekroczyła limit przekierowań.",
                                    str(response.request.url),
                                    operation,
                                    started_at,
                                )
                            current_url = urljoin(str(response.request.url), location)
                            current_params = None
                            break
                        if (
                            response.status_code < 200 or response.status_code >= 300
                        ) and response.status_code not in readable_error_statuses:
                            raise self._transport_error(
                                f"Usługa OGC zwróciła HTTP {response.status_code}.",
                                str(response.request.url),
                                operation,
                                started_at,
                            )
                        raw_length = response.headers.get("content-length")
                        if (
                            raw_length
                            and raw_length.isdigit()
                            and int(raw_length) > byte_limit
                        ):
                            raise self._limit_error(
                                "Odpowiedź OGC przekracza limit bajtów.",
                                str(response.request.url),
                                operation,
                                started_at,
                            )
                        chunks: list[bytes] = []
                        size = 0
                        for chunk in response.iter_bytes():
                            self._check_deadline(
                                deadline,
                                str(response.request.url),
                                operation,
                                started_at,
                            )
                            size += len(chunk)
                            if size > byte_limit:
                                raise self._limit_error(
                                    "Odpowiedź OGC przekracza limit bajtów.",
                                    str(response.request.url),
                                    operation,
                                    started_at,
                                )
                            chunks.append(chunk)
                        return _HttpPayload(
                            content=b"".join(chunks),
                            content_type=response.headers.get("content-type", ""),
                            url=str(response.request.url),
                            status_code=response.status_code,
                            headers={
                                key: response.headers[key]
                                for key in ("content-disposition", "retry-after", "content-length", "last-modified")
                                if key in response.headers
                            },
                        )
                except OgcError:
                    raise
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                    if attempt < self._config.retries:
                        self._backoff(
                            attempt, deadline, current_url, operation, started_at
                        )
                        continue
                    raise self._transport_error(
                        f"Nie udało się odczytać usługi OGC: {exc}",
                        current_url,
                        operation,
                        started_at,
                    ) from exc
            else:
                raise self._transport_error(
                    f"Nie udało się odczytać usługi OGC: {last_error}",
                    current_url,
                    operation,
                    started_at,
                )
            # Redirect wychodzi z pętli prób i wraca przez walidację URL/DNS/IP.
            continue

    def _xml_root(
        self, payload: _HttpPayload, operation: str, started_at: datetime
    ) -> ElementTree.Element:
        media_type = self._media_type(payload.content_type)
        if (
            media_type
            and media_type not in _XML_MEDIA_TYPES
            and not media_type.endswith("+xml")
        ):
            raise self._contract_error(
                f"OGC zwrócił nieoczekiwany Content-Type {payload.content_type!r}.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        if not payload.content.lstrip().startswith(b"<"):
            raise self._contract_error(
                "Odpowiedź OGC nie jest XML.",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        try:
            root = parse_xml_root(
                payload.content,
                max_depth=self._config.max_xml_depth,
                max_nodes=self._config.max_xml_nodes,
            )
        except RuContractError as exc:
            raise self._contract_error(
                str(exc), payload.url, operation, started_at, content=payload.content
            ) from exc
        message = exception_report_message(root)
        if message is not None:
            raise self._contract_error(
                f"OGC ExceptionReport: {message}",
                payload.url,
                operation,
                started_at,
                content=payload.content,
            )
        return root

    def _validate_url(self, url: str, operation: str, started_at: datetime) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme.casefold() != "https" or not host:
            raise self._transport_error(
                "OGC wymaga pełnego adresu HTTPS.", url, operation, started_at
            )
        if parsed.username or parsed.password:
            raise self._transport_error(
                "Adres OGC nie może zawierać danych logowania.",
                url,
                operation,
                started_at,
            )
        if host not in {item.casefold() for item in self._config.allowed_hosts}:
            raise self._transport_error(
                f"Host {host!r} nie znajduje się na allowliście OGC.",
                url,
                operation,
                started_at,
            )
        try:
            addresses = tuple(self._resolver(host))
        except OSError as exc:
            raise self._transport_error(
                f"Nie udało się rozwiązać DNS hosta {host!r}.",
                url,
                operation,
                started_at,
            ) from exc
        if not addresses:
            raise self._transport_error(
                f"DNS hosta {host!r} nie zwrócił adresu.",
                url,
                operation,
                started_at,
            )
        for raw in addresses:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError as exc:
                raise self._transport_error(
                    f"DNS hosta {host!r} zwrócił niepoprawny adres IP.",
                    url,
                    operation,
                    started_at,
                ) from exc
            if any(
                (
                    address.is_private,
                    address.is_loopback,
                    address.is_link_local,
                    address.is_multicast,
                    address.is_reserved,
                    address.is_unspecified,
                )
            ):
                raise self._transport_error(
                    f"Host OGC {host!r} rozwiązuje się do niedozwolonego IP.",
                    url,
                    operation,
                    started_at,
                )

    @staticmethod
    def _system_resolver(host: str) -> Iterable[str]:
        return {item[4][0] for item in socket.getaddrinfo(host, 443)}

    def _backoff(
        self,
        attempt: int,
        deadline: float,
        url: str,
        operation: str,
        started_at: datetime,
    ) -> None:
        delay = self._config.backoff_seconds * (2**attempt)
        if self._monotonic() + delay > deadline:
            raise self._transport_error(
                "Usługa OGC przekroczyła całkowity timeout.",
                url,
                operation,
                started_at,
            )
        self._sleep(delay)

    def _check_deadline(
        self, deadline: float, url: str, operation: str, started_at: datetime
    ) -> None:
        if self._monotonic() > deadline:
            raise self._transport_error(
                "Usługa OGC przekroczyła całkowity timeout.",
                url,
                operation,
                started_at,
            )

    def _wfs_result(
        self,
        url: str,
        operation: str,
        started_at: datetime,
        first_root: ElementTree.Element | None,
        members: list[ElementTree.Element],
        features: list[bytes],
        complete: bool,
    ) -> OgcResult:
        if first_root is None:
            artifact = b""
        else:
            merged = copy.deepcopy(first_root)
            for child in list(merged):
                if self._local_name(child.tag) in {"member", "featureMember"}:
                    merged.remove(child)
            for member in members:
                merged.append(copy.deepcopy(member))
            for attribute in VOLATILE_WFS_ATTRIBUTES:
                merged.attrib.pop(attribute, None)
            merged.set("numberReturned", str(len(features)))
            if complete:
                merged.set("numberMatched", str(len(features)))
            artifact = ElementTree.tostring(
                merged, encoding="utf-8", xml_declaration=True
            )
        return self._plain_result(
            artifact, features, url, operation, started_at, complete
        )

    def _plain_result(
        self,
        artifact: bytes,
        features: Iterable[bytes],
        url: str,
        operation: str,
        started_at: datetime,
        complete: bool,
    ) -> OgcResult:
        return OgcResult(
            artifact=artifact,
            features=tuple(features),
            source=Provenance(
                source_id=self._source_id,
                fetched_at=started_at,
                content_hash=hashlib.sha256(artifact).hexdigest(),
                request_url=url,
                operation=operation,
                complete=complete,
            ),
            complete=complete,
        )

    def _transport_error(
        self, message: str, url: str, operation: str, started_at: datetime
    ) -> OgcTransportError:
        return OgcTransportError(
            message,
            source=self._error_provenance(url, operation, started_at, "transport"),
        )

    def _contract_error(
        self,
        message: str,
        url: str,
        operation: str,
        started_at: datetime,
        *,
        content: bytes | None = None,
    ) -> OgcContractError:
        source = self._error_provenance(url, operation, started_at, "contract")
        if content is not None:
            source = Provenance(
                **{
                    **source.__dict__,
                    "content_hash": hashlib.sha256(content).hexdigest(),
                }
            )
        return OgcContractError(message, source=source)

    def _limit_error(
        self,
        message: str,
        url: str,
        operation: str,
        started_at: datetime,
        *,
        partial: OgcResult | None = None,
    ) -> OgcLimitError:
        return OgcLimitError(
            message,
            source=self._error_provenance(url, operation, started_at, "limit"),
            partial=partial,
        )

    def _error_provenance(
        self, url: str, operation: str, started_at: datetime, code: str
    ) -> Provenance:
        return Provenance(
            source_id=self._source_id,
            fetched_at=started_at,
            request_url=url,
            operation=operation,
            complete=False,
            error_code=code,
        )

    @staticmethod
    def _feature_members(root: ElementTree.Element) -> list[ElementTree.Element]:
        return [
            element
            for element in root.iter()
            if OgcClient._local_name(element.tag) in {"member", "featureMember"}
            and next(iter(element), None) is not None
        ]

    @staticmethod
    def _feature_id(member: ElementTree.Element) -> str | None:
        feature = next(iter(member), None)
        if feature is None:
            return None
        for key, value in feature.attrib.items():
            if OgcClient._local_name(key) == "id" and value:
                return value
        for element in feature.iter():
            if (
                OgcClient._local_name(element.tag)
                in {
                    "identifier",
                    "lokalnyId",
                }
                and element.text
            ):
                return element.text.strip()
        return None

    @staticmethod
    def _media_type(content_type: str) -> str:
        return content_type.split(";", 1)[0].strip().casefold()

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
