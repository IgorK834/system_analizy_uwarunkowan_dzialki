"""Artefakty dowodowe BK-202/203: strefy MPZP z wektora i cytowalne parametry.

Uruchamiany w kontenerze backendu (WeasyPrint), bez internetu. Import aktu,
analiza i zapis audytu dokumentu działają w transakcji wycofywanej na końcu.
Wynik trafia do ``EVIDENCE_OUT`` (domyślnie ``/out``); polecenie opisuje
``docs/evaluation/results/bk-202-203-verification.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely import from_wkt  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.schemas.analyze import AnalyzeResponse  # noqa: E402
from app.services.analysis_orchestrator import (  # noqa: E402
    _analyze_mpzp_vector_zones,
    _assess_mpzp_vectors_safely,
)
from app.services.report import (  # noqa: E402
    _build_report_context,
    _html_to_pdf,
    _render_report_html,
)
from tests.mpzp_fixtures import act, document_blob, import_acts, rect, text_pdf, zone  # noqa: E402

OUT = Path(os.environ.get("EVIDENCE_OUT", "/out"))


async def main() -> None:
    session = SessionLocal()
    session.execute(text("SELECT 1"))
    x, y = 350000.0, 650000.0
    identifier = f"plan-dowodowy-{uuid4().hex[:6]}"
    document_url = "https://bip.example.gov.pl/uchwala-XII-100-2026.pdf"
    try:
        with tempfile.TemporaryDirectory() as artifacts:
            outcome = import_acts(
                session, Path(artifacts), f"mpzp_evidence_{uuid4().hex[:6]}",
                act(
                    identifier,
                    rect(x, y, x + 200, y + 100),
                    (
                        zone("1MN", rect(x, y, x + 60, y + 100), "single_family_housing"),
                        zone("2MN", rect(x + 60, y, x + 200, y + 100), "single_family_housing"),
                    ),
                    document_url=document_url,
                ),
            )
        parcel = from_wkt(rect(x + 40, y, x + 60, y + 30)).union(
            from_wkt(rect(x + 60, y, x + 100, y + 10))
        )
        vector, vector_warnings = _assess_mpzp_vectors_safely(
            session, parcel, datetime.now(timezone.utc)
        )
        assert vector is not None
        with patch(
            "app.services.analysis_orchestrator.fetch_mpzp_document",
            new=AsyncMock(return_value=document_blob(text_pdf(), document_url)),
        ):
            zones, warnings, sources, status = await _analyze_mpzp_vector_zones(
                vector, None, "EVIDENCE", session
            )
        response = AnalyzeResponse(
            status="partial",
            analyzed_at=datetime.now(timezone.utc),
            mpzp_zones=zones,
            infrastructure=[],
            risks=[],
            warnings=[*vector_warnings, *warnings],
            sources=sources,
        )
        OUT.mkdir(parents=True, exist_ok=True)
        html = _render_report_html(_build_report_context(response, None, None))
        (OUT / "mpzp-two-zones-report.pdf").write_bytes(_html_to_pdf(html))
        (OUT / "mpzp-two-zones-result.json").write_text(
            json.dumps(
                {
                    "import": {"status": outcome.status, "data_release_id": outcome.data_release_id},
                    "parcel": {
                        "area_sqm": parcel.area,
                        "centroid_x_offset": parcel.centroid.x - x,
                        "zone_split_x_offset": 60,
                    },
                    "parser_status": status,
                    "mpzp_zones": [item.model_dump(mode="json", exclude={"intersection_geojson"}) for item in zones],
                    "warnings": [item.model_dump(mode="json") for item in response.warnings],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print("OK", [(z.zone_symbol, round(z.intersection_pct, 3), z.max_building_height_m) for z in zones])
    finally:
        session.rollback()
        session.close()


asyncio.run(main())
