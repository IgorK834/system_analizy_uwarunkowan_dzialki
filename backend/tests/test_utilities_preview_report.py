from datetime import datetime, timezone

import pytest

from app.schemas.analyze import AnalyzeResponse, UtilitiesPreviewResult
from app.schemas.source import SourceMetadata
from app.services.report import (
    _build_report_context,
    _render_report_html,
)

_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _response(status: str = "covered") -> AnalyzeResponse:
    notes = {
        "covered": (
            "Powiat publikuje dane GESUT. Brak obiektów na podglądzie nie "
            "oznacza braku sieci."
        ),
        "not_covered": (
            "KIUT nie potwierdził publikacji GESUT. Pusty podgląd nie jest "
            "dowodem braku sieci."
        ),
        "unknown": (
            "Nie udało się sprawdzić pokrycia. Pusty podgląd nie oznacza braku sieci."
        ),
    }
    return AnalyzeResponse(
        analysis_id=42,
        status="partial",
        analyzed_at=_NOW,
        parcel=None,
        mpzp_zones=[],
        pog=None,
        infrastructure=[],
        utilities_preview=UtilitiesPreviewResult(
            coverage_status=status,
            county_name="powiat krakowski" if status == "covered" else None,
            layer_available=status == "covered",
            note=notes[status],
            source=SourceMetadata(
                source_name="KIUT (GUGiK)",
                source_url="https://kiut.example.test/wms",
                fetched_at=_NOW,
                response_status=200 if status != "unknown" else None,
                confidence=0.9 if status != "unknown" else 0.0,
                manual_review_required=status == "unknown",
            ),
        ),
        risks=[],
        buildable_area_sqm=None,
        warnings=[],
        sources=[],
    )


def test_utilities_preview_is_rendered_into_report_without_network_metrics() -> None:
    context = _build_report_context(_response())
    html = _render_report_html(context)

    assert "Tabela 7.1. Podgląd uzbrojenia terenu (KIUT)" in html
    assert "powiat publikuje dane GESUT w KIUT" in html
    assert "powiat krakowski" in html
    assert "Raport nie zawiera odległości ani liczby sieci" in html
    assert "Nie da się na podstawie podglądu WMS stwierdzić" in html
    # BK-503: PDF nie osadza rastra WMS KIUT (ani legendy jego kolorów).
    assert "PDF nie osadza rastra WMS" in html
    assert "energetyka — czerwony" not in html
    assert "z nakładką uzbrojenia terenu (KIUT)" not in html


@pytest.mark.parametrize(
    ("status", "expected_limitation"),
    [
        ("not_covered", "Nie jest to potwierdzenie braku sieci"),
        ("unknown", "Pusty podgląd nie oznacza braku sieci"),
    ],
)
def test_uncertain_coverage_states_are_explicit_report_limitations(
    status: str,
    expected_limitation: str,
) -> None:
    context = _build_report_context(_response(status))

    assert any(expected_limitation in item for item in context["limitations"])
