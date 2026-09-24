"""Artefakty dowodowe BK-106/107: raport PDF i JSON wyniku dla próbki Sopotu.

Uruchamiany w kontenerze backendu (WeasyPrint), bez internetu: WFS i CSW RU
pochodzą z zamrożonych fixtur tests/fixtures/ru. Import, analiza i zapis
snapshotu działają w transakcji wycofywanej na końcu — baza nie jest trwale
zmieniana. Wynik trafia do katalogu podanego w EVIDENCE_OUT (domyślnie
/out). Polecenie opisuje docs/evaluation/results/bk-106-107-verification.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.modules.imports.application.common import ImportRelease  # noqa: E402
from app.modules.imports.application.pog_import import run_pog_import  # noqa: E402
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore  # noqa: E402
from app.modules.imports.infrastructure.repository import (  # noqa: E402
    SqlAlchemyImportRepository,
    active_pog_release,
)
from app.schemas.analyze import AnalyzeResponse  # noqa: E402
from app.services.analysis_orchestrator import _analyze_pog_best_effort  # noqa: E402
from app.services.persistence import build_analyze_response_from_analysis, save_analysis  # noqa: E402
from app.services.report import (  # noqa: E402
    _build_report_context,
    _html_to_pdf,
    _render_report_html,
)
from tests.test_imports_pog import _source  # noqa: E402
from tests.test_pog_provenance_chain import _csw_client, _parcel_inside_zone, _reader  # noqa: E402

OUT = Path(os.environ.get("EVIDENCE_OUT", "/out"))


async def main() -> None:
    session = SessionLocal()
    session.execute(text("SELECT 1"))
    source_id = f"pog_evidence_{uuid4().hex[:8]}"
    try:
        outcome = run_pog_import(
            _reader(_csw_client([])),
            source_id,
            SqlAlchemyImportRepository(session, _source(source_id), LocalArtifactStore("/tmp/evidence")),
            release=ImportRelease(
                source_id, "ru-sopot-2026-09-24", datetime(2026, 9, 24, tzinfo=timezone.utc),
                publication_allowed=True, dry_run=False, teryt_scope=("226401",),
            ),
        )
        pinned = active_pog_release(session, source_id=source_id)
        parcel = _parcel_inside_zone()
        with (
            patch("app.services.analysis_orchestrator.active_pog_release", return_value=pinned),
            patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock()),
        ):
            pog, _ouz, warnings, sources = await _analyze_pog_best_effort(parcel, "226401", session)
        response = AnalyzeResponse(
            status="partial", analyzed_at=datetime.now(timezone.utc), mpzp_zones=[], pog=pog,
            infrastructure=[], risks=[], warnings=warnings, sources=sources,
        )
        with patch.object(session, "commit", session.flush):
            saved = save_analysis(response, f"EVIDENCE_{uuid4().hex[:6]}", parcel, session)
        rebuilt = build_analyze_response_from_analysis(saved, session)
        html = _render_report_html(_build_report_context(rebuilt, None, None))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "sopot-pog-report.pdf").write_bytes(_html_to_pdf(html))
        (OUT / "sopot-pog-result.json").write_text(
            json.dumps(rebuilt.pog.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (OUT / "sopot-import-outcome.json").write_text(
            json.dumps(
                {"status": outcome.status, "warnings": list(outcome.warnings), "stats": outcome.stats},
                ensure_ascii=False, indent=2, default=str,
            ) + "\n",
            encoding="utf-8",
        )
        print("OK", outcome.status, rebuilt.pog.legal_status, rebuilt.pog.coverage_status)
    finally:
        session.rollback()
        session.close()


asyncio.run(main())
