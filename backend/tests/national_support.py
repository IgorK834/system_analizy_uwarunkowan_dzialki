"""Wspólne elementy testów importu krajowego POG: zamrożony wykaz i pliki GML z RU, brama bez sieci, składanie importu."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.core.data_sources import get_catalog
from app.db.session import SessionLocal
from app.modules.imports.application.national_import import (
    NationalImportOptions,
    NationalImportResult,
    NationalProgress,
    run_national_import,
)
from app.modules.imports.domain.ru_catalog import RuListing, RuPublishedAct, assemble_listing, parse_listing_page
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.national_runtime import (
    NationalRunRecorder,
    SqlActPublisher,
    SqlRegistryGateway,
)
from app.modules.imports.infrastructure.ru_catalog_store import SqlAlchemyRuCatalogStore
from app.modules.imports.infrastructure.ru_published import RuDownloadError

FIXTURES = Path(__file__).parent / "fixtures" / "ru_national"
WFS_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"
# Akty z zamrożonych plików: Jejkowice (binding), Sejny (binding, inna strefa PL-2000), Słupsk i Barczewo (projekty).
JEJKOWICE, SEJNY, SLUPSK, BARCZEWO = 1152, 1735, 295, 1894
FIXTURE_ACTS = (JEJKOWICE, SEJNY, SLUPSK, BARCZEWO)


def frozen_listing() -> RuListing:
    pages = [
        parse_listing_page(json.loads((FIXTURES / f"listing_current_page_{number}.json").read_text(encoding="utf-8")))
        for number in range(1, 6)
    ]
    return assemble_listing(pages)


def fixture_listing(*ids: int) -> RuListing:
    """Wykaz złożony z prawdziwych wierszy RU dla podanych aktów (kompletny — jak wykaz serwera po filtrze)."""
    wanted = ids or FIXTURE_ACTS
    full = frozen_listing()
    acts = tuple(act for act in full.acts if act.published_act_id in wanted)
    assert {act.published_act_id for act in acts} == set(wanted), "zamrożony wykaz nie zawiera wszystkich aktów"
    return RuListing(acts, len(acts), 1, 1, True, None)


class FixtureGateway:
    """Brama RU bez sieci: wykaz i pliki GML z zamrożonych odpowiedzi; ``overrides`` podmienia treść pliku aktu."""

    def __init__(
        self,
        listing: RuListing,
        *,
        overrides: dict[int, bytes] | None = None,
        failing: dict[int, str] | None = None,
    ) -> None:
        self.listing = listing
        self.overrides = overrides or {}
        self.failing = failing or {}
        self.downloads: list[int] = []
        self._lock = threading.Lock()

    def list_current(self) -> RuListing:
        return self.listing

    def download(self, act: RuPublishedAct) -> Any:
        with self._lock:
            self.downloads.append(act.published_act_id)
        if act.published_act_id in self.failing:
            raise RuDownloadError(self.failing[act.published_act_id], "pobranie zablokowane w teście")
        source_id = act.published_act_id if (FIXTURES / f"act_{act.published_act_id}.gml").exists() else None
        content = self.overrides.get(act.published_act_id)
        if content is None:
            assert source_id is not None, f"brak pliku GML dla aktu {act.published_act_id}"
            content = (FIXTURES / f"act_{source_id}.gml").read_bytes()
        return SimpleNamespace(content=content, filename=f"act_{act.published_act_id}.gml", url="fixture://ru")


@dataclass
class Stack:
    registry: SqlRegistryGateway
    publisher: SqlActPublisher
    recorder: NationalRunRecorder

    def run(
        self,
        gateway: FixtureGateway,
        *,
        cancel: threading.Event | None = None,
        **option_overrides: Any,
    ) -> NationalImportResult:
        options = NationalImportOptions(concurrency=2, memory_budget_bytes=64 * 1024 * 1024)
        if option_overrides:
            options = replace(options, **option_overrides)
        return run_national_import(
            gateway, self.registry, self.publisher, self.recorder, options,
            progress=NationalProgress(), cancel=cancel, sleep=lambda _seconds: None,
        )


def build_stack(tmp_path: Path, *, with_csw: bool = False) -> Stack:
    source = get_catalog().get("pog_app")
    store = LocalArtifactStore(tmp_path)
    catalog = SqlAlchemyRuCatalogStore(SessionLocal)
    return Stack(
        registry=SqlRegistryGateway(SessionLocal, source, store, catalog),
        publisher=SqlActPublisher(
            SessionLocal, source, store, wfs_url=WFS_URL, csw_url=None, csw_client_factory=None, csw_gate=None,
            xml_max_nodes=8_000_000, publication_allowed=True,
        ),
        recorder=NationalRunRecorder(SessionLocal, source, store),
    )
