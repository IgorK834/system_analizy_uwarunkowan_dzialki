"""Adapter Rejestru Urbanistycznego (WFS ``app-pog``) dla discovery POG — kanał ``pog_app`` (AU-105, ADR-023).

Jedno zapytanie ``GetFeature`` na typ obiektu zastępuje pętlę „punkt próbki × ~48 warstw ``APP.POG.*`` WMS”.
Adapter pobiera przez ``OgcClient`` (HTTPS, allowlista hostów, limity bajtów i stron, ponowienia):

* ``app-pog:StrefaPlanistyczna``, ``ObszarUzupelnieniaZabudowy``, ``ObszarZabudowySrodmiejskiej`` i
  ``AktPlanowaniaPrzestrzennego`` z filtrem ``bbox`` kafla;
* **kolejność osi dla URN EPSG:2180 to północ, wschód** — zarówno w parametrze ``bbox``
  (``N_min,E_min,N_max,E_max,urn:ogc:def:crs:EPSG::2180``), jak i w ``gml:posList`` odpowiedzi (zweryfikowana
  2026-10-12 na żywej usłudze; to samo dotyczy WMS 1.3.0, co obnażyło błąd dotychczasowego discovery — zob. ADR-023).

RU zwraca **jeden typ na żądanie** (parametr ``typeNames`` z listą typów daje wyłącznie pierwszy) i strony po
10 obiektów niezależnie od ``count``. Każde żądanie HTTP przechodzi przez budżet analizy (``RequestBudget``):
po jego wyczerpaniu adapter nie wysyła kolejnych żądań, tylko zwraca odpowiedź niepełną.

Adapter nie zgłasza wyjątków sieciowych: błąd źródła wraca jako ``RuPogFeaturesResponse.error_code``, żeby
wywołujący nie mógł pomylić go z pustą odpowiedzią.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Final
from xml.etree import ElementTree

from app.core.ru_contracts import RuContractError, parse_xml_root
from app.modules.imports.composition import (
    OgcClient,
    OgcContractError,
    OgcError,
    OgcExceptionReportError,
    OgcLimitError,
    OgcTransportError,
)
from app.modules.planning.application.ports import (
    RequestBudget,
    RequestBudgetExceeded,
    RuPogFeaturesResponse,
)
from app.modules.planning.domain.kimpzp_discovery import parse_source_date
from app.modules.planning.domain.pog_discovery import (
    ERROR_BUDGET_EXCEEDED,
    ERROR_CONTRACT,
    ERROR_LIMIT_EXCEEDED,
    ERROR_SERVICE_EXCEPTION,
    ERROR_TRANSPORT,
    GEOMETRY_AXIS_SUSPECT,
    GEOMETRY_UNUSABLE,
    RESPONSE_PARTIAL,
    Bbox,
    PogFeatureRecord,
    PogFeatureType,
    clip_to_tile,
    envelope_overlaps,
)
from app.modules.planning.infrastructure.ru_mpzp_acts import (
    NS_APP,
    NS_XLINK,
    SRS_URN_2180,
    parse_extent,
    reference_path,
    without_version,
)

logger = logging.getLogger(__name__)

# Funkcja wołana przed każdym żądaniem HTTP; podnosi ``RequestBudgetExceeded`` po wyczerpaniu budżetu.
BeforeRequest = Callable[[], None]
ClientFactory = Callable[[BeforeRequest | None], OgcClient]

_GEOMETRY_ELEMENT: Final[dict[str, str]] = {
    "AktPlanowaniaPrzestrzennego": "zasiegPrzestrzenny",
    "ObszarZabudowySrodmiejskiej": "geometria",
    "ObszarUzupelnieniaZabudowy": "geometria",
    "StrefaPlanistyczna": "geometria",
}


def _q(name: str) -> str:
    return f"{{{NS_APP}}}{name}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ElementTree.Element, name: str) -> str | None:
    child = element.find(_q(name))
    if child is None or child.text is None:
        return None
    value = " ".join(child.text.split())
    return value or None


def _href(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    value = element.attrib.get(f"{{{NS_XLINK}}}href")
    return value.strip() if value and value.strip() else None


def _bbox_param(bbox: Bbox) -> str:
    """``bbox`` w kolejności osi RU dla URN EPSG:2180: północ, wschód (``N_min,E_min,N_max,E_max``)."""
    min_x, min_y, max_x, max_y = bbox
    return f"{min_y:.3f},{min_x:.3f},{max_y:.3f},{max_x:.3f},{SRS_URN_2180}"


def _error_code(error: OgcError) -> str:
    if isinstance(error, OgcLimitError):
        return ERROR_LIMIT_EXCEEDED
    if isinstance(error, OgcExceptionReportError):
        return ERROR_SERVICE_EXCEPTION
    if isinstance(error, OgcContractError):
        return ERROR_SERVICE_EXCEPTION if "ExceptionReport" in str(error) else ERROR_CONTRACT
    if isinstance(error, OgcTransportError):
        return ERROR_TRANSPORT
    return ERROR_CONTRACT


def parse_pog_feature(feature: bytes, feature_type: PogFeatureType, tile: Bbox) -> PogFeatureRecord | None:
    """Parsuje obiekt APP 3.0; ``None`` dla elementu innego typu albo bez identyfikatora.

    Geometria jest zwracana w (wschód, północ) i przycięta do ``tile``. Obiekt, którego wielokąt nie dotyka kafla
    (RU filtruje po obwiedni), dostaje ``geometry=None`` i ``outside_tile=True`` — nie ma wpływu na żadną działkę
    w kaflu, więc nie jest ani znaleziony, ani problemem. Zamienione osie albo nieczytelny wielokąt dają
    ``geometry=None`` z kodem problemu.
    """
    root = parse_xml_root(feature)
    if _local(root.tag) != feature_type:
        return None
    identifier = root.find(f"{_q('idIIP')}/{_q('Identyfikator')}")
    if identifier is None:
        return None
    namespace, local_id = _text(identifier, "przestrzenNazw"), _text(identifier, "lokalnyId")
    if not namespace or not local_id:
        return None
    status = root.find(_q("status"))
    plan_path = reference_path(_href(root.find(_q("plan"))), "AktPlanowaniaPrzestrzennego")

    geometry, issue, outside_tile = None, None, False
    parsed, reason = parse_extent(root.find(_q(_GEOMETRY_ELEMENT[feature_type])))
    if parsed is None:
        issue = GEOMETRY_UNUSABLE if reason else None
    elif not envelope_overlaps(parsed, tile):
        issue = GEOMETRY_AXIS_SUSPECT
    else:
        geometry = clip_to_tile(parsed, tile)
        outside_tile = geometry is None

    return PogFeatureRecord(
        feature_type=feature_type,
        identifier=f"{namespace}/{local_id}",
        version_id=_text(identifier, "wersjaId"),
        status_href=_href(status),
        status_title=status.attrib.get(f"{{{NS_XLINK}}}title") if status is not None else None,
        plan_ref=without_version(plan_path) if plan_path else None,
        title=_text(root, "tytul") or _text(root, "oznaczenie"),
        valid_from=_parse_date(_text(root, "obowiazujeOd")),
        geometry=geometry,
        geometry_issue=issue,
        outside_tile=outside_tile,
    )


def _parse_date(value: str | None) -> date | None:
    return parse_source_date(value)


class RuPogFeaturesAdapter:
    """Adapter portu ``RuPogFeaturesSource`` na ``OgcClient``.

    ``client_factory`` dostaje funkcję wołaną przed każdym żądaniem (budżet) i zwraca nowego, synchronicznego klienta
    zamykanego po użyciu; w testach wstrzykuje się klienta z ``respx``/``MockTransport``.
    """

    def __init__(self, *, wfs_url: str, client_factory: ClientFactory) -> None:
        self._wfs_url = wfs_url
        self._client_factory = client_factory

    @contextmanager
    def _client(self, before_request: BeforeRequest | None) -> Iterator[OgcClient]:
        client = self._client_factory(before_request)
        try:
            yield client
        finally:
            client.close()

    def fetch_features(
        self, feature_type: PogFeatureType, tile: Bbox, budget: RequestBudget | None = None
    ) -> RuPogFeaturesResponse:
        """Obiekty typu ``feature_type``, których obwiednia przecina ``tile`` (EPSG:2180, x = wschód)."""
        fetched_at = datetime.now(timezone.utc)
        attempts = 0

        def before_request() -> None:
            nonlocal attempts
            if budget is not None:
                budget.acquire()
            attempts += 1

        try:
            with self._client(before_request) as client:
                result = client.fetch_wfs(
                    self._wfs_url,
                    type_name=f"app-pog:{feature_type}",
                    srs_name=SRS_URN_2180,
                    extra_params={"bbox": _bbox_param(tile)},
                )
        except RequestBudgetExceeded:
            logger.warning("ru_pog_budget_exceeded", extra={"feature_type": feature_type})
            return RuPogFeaturesResponse(
                features=(),
                complete=False,
                fetched_at=fetched_at,
                error_code=ERROR_BUDGET_EXCEEDED,
                error_message="Wyczerpano budżet żądań HTTP do Rejestru Urbanistycznego.",
                requests=attempts,
            )
        except OgcError as exc:
            logger.warning("ru_pog_fetch_failed", extra={"error": type(exc).__name__, "feature_type": feature_type})
            return RuPogFeaturesResponse(
                features=(),
                complete=False,
                fetched_at=fetched_at,
                source=exc.source,
                error_code=_error_code(exc),
                error_message=str(exc),
                requests=attempts,
            )
        records: list[PogFeatureRecord] = []
        issues: list[str] = []
        for feature in result.features:
            try:
                record = parse_pog_feature(feature, feature_type, tile)
            except RuContractError:
                issues.append(ERROR_CONTRACT)
                continue
            if record is None:
                issues.append(ERROR_CONTRACT)
                continue
            records.append(record)
        if not result.complete:
            issues.append(RESPONSE_PARTIAL)
        records.sort(key=lambda item: (item.identifier, item.version_id or ""))
        return RuPogFeaturesResponse(
            features=tuple(records),
            complete=result.complete and ERROR_CONTRACT not in issues,
            fetched_at=fetched_at,
            source=result.source,
            issues=tuple(dict.fromkeys(issues)),
            requests=attempts,
        )
