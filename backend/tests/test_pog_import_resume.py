"""Import POG z WFS (AU-106): równoległe pobieranie, wznawianie po przerwaniu i ten sam artefakt.

Obiekty pochodzą z rzeczywistych próbek RU (``tests/fixtures/ru``); strony po 10 obiektów składa atrapa usługi (RU
zwraca strony po 10 niezależnie od ``count``), a strefy są powielane z unikalnymi identyfikatorami, żeby powstało
kilka stron. Reader, pobieranie stron, magazyn stron, checkpoint i kompozycja są prawdziwe.
"""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from xml.etree import ElementTree

import httpx
import pytest
import respx

from app.modules.imports import composition
from app.modules.imports.application.common import ImportOutcome
from app.modules.imports.application.progress import ImportProgress
from app.modules.imports.composition import PogImportOptions
from app.modules.imports.domain.pog import pog_record_to_dict
from app.modules.imports.infrastructure.run_checkpoint import CheckpointRun
from app.modules.imports.infrastructure.wfs import WfsFetchError, WfsFetchInterrupted

FIXTURES = Path(__file__).parent / "fixtures" / "ru"
WFS_NS = "http://www.opengis.net/wfs/2.0"
TYPE_TO_FIXTURE = {
    "AktPlanowaniaPrzestrzennego": "act",
    "DokumentFormalny": "document",
    "StrefaPlanistyczna": "zone",
    "ObszarUzupelnieniaZabudowy": "ouz",
    "ObszarZabudowySrodmiejskiej": "ozs",
}
PAGE = 10
ZONES = 34  # 4 strony stref po 10 obiektów


def _catalog_urls() -> tuple[str, str]:
    resources = composition.get_catalog().get("pog_app").resources
    wfs = next(item.url for item in resources if item.role == "ru_wfs")
    csw = next(item.url for item in resources if item.role == "ru_csw")
    return wfs, csw


WFS_URL, CSW_URL = _catalog_urls()


def _members(fixture: str, copies: int = 1) -> tuple[ElementTree.Element, list[ElementTree.Element]]:
    """Korzeń odpowiedzi i obiekty: oryginał z fixtury plus kopie z unikalnym oznaczeniem (``100SU`` → ``101SU``…)."""
    root = ElementTree.fromstring((FIXTURES / f"wfs_pog_getfeature_{fixture}.xml").read_bytes())
    original = root.find(f"{{{WFS_NS}}}member")
    assert original is not None
    members = [original]
    for number in range(1, copies):
        clone = copy.deepcopy(original)
        for element in clone.iter():
            if element.text and "100SU" in element.text:
                element.text = element.text.replace("100SU", f"{100 + number}SU")
            for key, value in list(element.attrib.items()):
                if "100SU" in value:
                    element.attrib[key] = value.replace("100SU", f"{100 + number}SU")
        members.append(clone)
    return root, members


class RuWfs:
    """Atrapa WFS RU: strony po 10 obiektów, zmienne ``timeStamp``, wstrzykiwane awarie i opóźnienia."""

    def __init__(self, *, stamp: str = "t0", delay: float = 0.0, zones: int = ZONES) -> None:
        self.stamp = stamp
        self.delay = delay
        self.roots: dict[str, ElementTree.Element] = {}
        self.members: dict[str, list[ElementTree.Element]] = {}
        for type_name, fixture in TYPE_TO_FIXTURE.items():
            root, members = _members(fixture, zones if fixture == "zone" else 1)
            self.roots[type_name], self.members[type_name] = root, members
        self.requests: list[tuple[str, int]] = []
        self.fail: set[tuple[str, int]] = set()
        self.lock = threading.Lock()

    def total(self) -> int:
        return sum(len(items) for items in self.members.values())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        type_name = params["typeNames"].rsplit(":", 1)[-1]
        start = int(params["startIndex"])
        with self.lock:
            self.requests.append((type_name, start))
        if self.delay:
            time.sleep(self.delay)
        if (type_name, start) in self.fail:
            return httpx.Response(503)
        members = self.members.get(type_name, [])
        root = copy.deepcopy(self.roots[type_name] if type_name in self.roots else next(iter(self.roots.values())))
        for child in list(root):
            root.remove(child)
        page = members[start : start + PAGE]
        for member in page:
            root.append(copy.deepcopy(member))
        root.set("numberMatched", str(len(members)))
        root.set("numberReturned", str(len(page)))
        root.set("timeStamp", self.stamp)
        return httpx.Response(
            200,
            content=ElementTree.tostring(root, encoding="utf-8", xml_declaration=True),
            headers={"content-type": "application/gml+xml; version=3.2"},
        )


class FakeCheckpoints:
    def __init__(self, *, resumed: bool = False) -> None:
        self.resumed = resumed
        self.saved: list[tuple[str | None, dict[str, Any]]] = []
        self.begun: list[tuple[str, bool]] = []

    def begin(self, resume_key: str, *, fresh: bool = False) -> CheckpointRun:
        self.begun.append((resume_key, fresh))
        return CheckpointRun(1, self.resumed and not fresh, None)

    def save(self, run_id: int, payload: dict[str, Any], *, status: str | None = None) -> None:
        self.saved.append((status, payload))


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(composition.settings, "import_artifact_storage_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
    )


def _run(server: RuWfs, options: PogImportOptions, *, dry_run: bool = True, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    """Uruchamia ``run_pog_command`` z prawdziwym czytnikiem; zamiast publikacji przechwytuje wsad (``batch``)."""
    captured: dict[str, Any] = {}

    def fake_import(reader, source_id, repository, *, release, progress=None):  # noqa: ANN001, ANN202
        if progress is not None:
            progress.set_phase("validate")
        captured["batch"] = reader.read()
        return ImportOutcome(status="dry_run_only" if release.dry_run else "succeeded", stats={})

    monkeypatch.setattr(composition, "run_pog_import", fake_import)
    monkeypatch.setattr(composition, "_repository", lambda *_args: object())
    session = MagicMock()
    with respx.mock:
        respx.get(WFS_URL).mock(side_effect=server)
        respx.get(CSW_URL).mock(
            return_value=httpx.Response(
                200,
                content=(FIXTURES / "csw_getrecords_iso_226401.xml").read_bytes(),
                headers={"content-type": "application/xml"},
            )
        )
        outcome = composition.run_pog_command(
            session,
            source_id="pog_app",
            dry_run=dry_run,
            act_identifier="226401-POG",
            teryt="226401",
            legal_status="legalForce",
            options=options,
        )
    return outcome, captured.get("batch"), session


def _stable_records(batch: Any) -> str:
    """Rekordy aktów bez chwil pobrania (``fetched_at`` metadanych CSW jest czasem odczytu, nie treścią danych)."""

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: clean(value) for key, value in item.items() if key != "fetched_at"}
        if isinstance(item, list):
            return [clean(value) for value in item]
        return item

    records = json.loads(json.dumps([pog_record_to_dict(act) for act in batch.acts], sort_keys=True, default=str))
    return json.dumps(clean(records), sort_keys=True)


def _hash(batch: Any) -> str:
    return hashlib.sha256(batch.content).hexdigest()


def test_uninterrupted_import_reads_every_object_with_parallel_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    server = RuWfs()
    outcome, batch, _session = _run(server, PogImportOptions(concurrency=4), monkeypatch=monkeypatch)

    assert outcome.status == "dry_run_only"
    zones = sum(1 for kind, _start in server.requests if kind == "StrefaPlanistyczna")
    assert zones == 4  # 34 stref = 4 strony po 10
    assert batch.complete and len(batch.acts) == 1
    assert len(batch.acts[0].features) >= ZONES


def test_resumed_import_gives_the_same_artifact_hash_and_the_same_records(monkeypatch: pytest.MonkeyPatch) -> None:
    clean_server = RuWfs(stamp="2026-10-12T10:00:00Z")
    _, clean_batch, _ = _run(clean_server, PogImportOptions(concurrency=4), monkeypatch=monkeypatch)

    # 1. Przebieg przerwany awarią usługi na trzeciej stronie stref (dry-run zostawia strony do wznowienia).
    broken = RuWfs(stamp="2026-10-12T10:05:00Z")
    broken.fail = {("StrefaPlanistyczna", 20)}
    checkpoints = FakeCheckpoints()
    options = PogImportOptions(concurrency=2, resume=True, checkpoints=checkpoints, progress=ImportProgress())
    with pytest.raises(WfsFetchError):
        _run(broken, options, monkeypatch=monkeypatch)
    assert checkpoints.saved[-1][0] == "failed"
    assert checkpoints.saved[-1][1]["resume_key"] == options.resume_key
    resume_dir = Path(composition.settings.import_artifact_storage_dir) / "resume" / options.resume_key[:32]
    assert resume_dir.is_dir() and any(resume_dir.rglob("page-*.xml"))

    # 2. Wznowienie w innym czasie (inne ``timeStamp``): tylko brakujące strony, ten sam artefakt.
    healthy = RuWfs(stamp="2026-10-12T11:30:00Z")
    progress = ImportProgress()
    resumed_checkpoints = FakeCheckpoints(resumed=True)
    resumed_options = PogImportOptions(concurrency=4, resume=True, checkpoints=resumed_checkpoints, progress=progress)
    _, resumed_batch, _ = _run(healthy, resumed_options, dry_run=False, monkeypatch=monkeypatch)

    assert _hash(resumed_batch) == _hash(clean_batch)
    assert resumed_batch.content == clean_batch.content
    assert _stable_records(resumed_batch) == _stable_records(clean_batch)
    assert resumed_options.resumed is True
    assert len(healthy.requests) < len(clean_server.requests)  # część stron wzięto z magazynu
    assert progress.snapshot()["objects_from_store"] > 0
    assert resumed_checkpoints.saved[-1][0] == "succeeded"
    assert not resume_dir.exists()  # po udanym imporcie magazyn stron jest zbędny


def test_artifact_hash_is_stable_across_independent_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _run(RuWfs(stamp="a"), PogImportOptions(concurrency=4), monkeypatch=monkeypatch)[1]
    second = _run(RuWfs(stamp="b"), PogImportOptions(concurrency=1), monkeypatch=monkeypatch)[1]

    assert _hash(first) == _hash(second)


def test_dry_run_keeps_the_pages_so_that_the_real_run_does_not_download_again(monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoints = FakeCheckpoints()
    options = PogImportOptions(concurrency=4, resume=True, checkpoints=checkpoints)
    _run(RuWfs(), options, dry_run=True, monkeypatch=monkeypatch)

    assert checkpoints.saved[-1][0] == "fetched"
    second = RuWfs()
    _run(second, PogImportOptions(concurrency=4, resume=True), dry_run=False, monkeypatch=monkeypatch)
    zone_pages = [start for kind, start in second.requests if kind == "StrefaPlanistyczna"]
    assert zone_pages == [0]  # tylko pierwsza strona (walidacja stanu usługi); reszta z magazynu


def test_fresh_discards_saved_pages_and_the_previous_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _run(RuWfs(), PogImportOptions(concurrency=4, resume=True), monkeypatch=monkeypatch)
    checkpoints = FakeCheckpoints(resumed=True)
    server = RuWfs()

    options = PogImportOptions(concurrency=4, fresh=True, checkpoints=checkpoints)
    _run(server, options, monkeypatch=monkeypatch)

    assert sorted(start for kind, start in server.requests if kind == "StrefaPlanistyczna") == [0, 10, 20, 30]
    assert checkpoints.begun[-1][1] is True and options.resumed is False


def test_without_resume_no_pages_are_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    _run(RuWfs(), PogImportOptions(concurrency=4, resume=False), monkeypatch=monkeypatch)

    assert not (Path(composition.settings.import_artifact_storage_dir) / "resume").exists()


def test_cancel_interrupts_the_import_marks_the_checkpoint_and_allows_a_later_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = threading.Event()
    server = RuWfs(delay=0.01)
    seen = {"pages": 0}
    progress = ImportProgress()
    original = progress.on_page

    def stop_after_some_pages(event: Any) -> None:
        original(event)
        seen["pages"] += 1
        if seen["pages"] == 3:
            cancel.set()

    progress.on_page = stop_after_some_pages  # type: ignore[method-assign]
    checkpoints = FakeCheckpoints()
    options = PogImportOptions(concurrency=2, resume=True, progress=progress, cancel=cancel, checkpoints=checkpoints)

    with pytest.raises(WfsFetchInterrupted):
        _run(server, options, monkeypatch=monkeypatch)

    assert checkpoints.saved[-1][0] == "interrupted"
    _, batch, _ = _run(RuWfs(), PogImportOptions(concurrency=4, resume=True), monkeypatch=monkeypatch)
    assert batch.complete


def test_parallel_download_is_much_faster_than_sequential_under_the_same_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kryterium AU-106: czas ≤ 60% stanu wyjściowego — tu przy kontrolowanym opóźnieniu 30 ms na stronę."""
    sequential_server = RuWfs(delay=0.03, zones=300)  # 30 stron stref + po jednej stronie pozostałych typów
    started = time.monotonic()
    _run(sequential_server, PogImportOptions(concurrency=1), monkeypatch=monkeypatch)
    sequential = time.monotonic() - started

    parallel_server = RuWfs(delay=0.03, zones=300)
    started = time.monotonic()
    _run(parallel_server, PogImportOptions(concurrency=4), monkeypatch=monkeypatch)
    parallel = time.monotonic() - started

    assert sorted(sequential_server.requests) == sorted(parallel_server.requests)
    assert parallel <= 0.6 * sequential, (sequential, parallel)


def test_checkpoint_payload_is_json_serialisable_and_identifies_the_import(monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoints = FakeCheckpoints()
    options = PogImportOptions(concurrency=4, resume=True, checkpoints=checkpoints, progress=ImportProgress())

    _run(RuWfs(), options, monkeypatch=monkeypatch)

    status, payload = checkpoints.saved[-1]
    json.dumps(payload)
    assert status == "fetched"
    assert payload["teryt"] == "226401" and payload["act_identifier"] == "226401-POG"
    assert payload["progress"]["objects_fetched"] > 0
    assert len(options.resume_key or "") == 64


def test_resume_key_depends_on_what_is_imported() -> None:
    base = composition.pog_resume_key("pog_app", "226401", "226401-POG", "https://x", ["a", "b"], "<f/>")
    assert base == composition.pog_resume_key("pog_app", "226401", "226401-POG", "https://x", ["a", "b"], "<f/>")
    assert base != composition.pog_resume_key("pog_app", "246101", "226401-POG", "https://x", ["a", "b"], "<f/>")
    assert base != composition.pog_resume_key("pog_app", "226401", "226401-POG", "https://x", ["a", "b"], "<g/>")
