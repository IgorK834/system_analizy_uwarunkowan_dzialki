import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.mpzp import (
    MpzpParameter,
    MpzpParseResult,
    MpzpZoneResult,
)
from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_parser_extract import ExtractedTable, TextExtractionResult

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "mpzp"
BIELSKO_DATA_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "mpzp_documents"
    / "bielsko_biala_uchwala_viii_187_2024"
)
FIXTURE_DIRS = sorted(
    path
    for path in FIXTURE_ROOT.iterdir()
    if path.is_dir() and (path / "expected.json").is_file()
)

_OCR_WARNING = (
    "Dokument PDF ma bardzo mało tekstu na stronę — prawdopodobnie skan "
    "wymagający OCR. Wynik ekstrakcji może być niepełny."
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _data_dir_for(fixture_dir: Path) -> Path:
    if fixture_dir.name == "bielsko_biala":
        return BIELSKO_DATA_DIR
    return fixture_dir


def _build_extraction(
    fixture_dir: Path,
) -> tuple[TextExtractionResult, dict[str, Any]]:
    data_dir = _data_dir_for(fixture_dir)
    pages = _load_json(data_dir / "pages.json")["pages"]
    source = _load_json(data_dir / "source.json")
    tables_path = data_dir / "tables.json"
    tables = (
        [ExtractedTable(**table) for table in _load_json(tables_path)]
        if tables_path.exists()
        else []
    )
    needs_ocr = bool(source["needs_ocr"])
    ocr_used = bool(source.get("ocr_used", False))
    manual_review_required = bool(
        source.get("manual_review_required", needs_ocr)
    )
    if ocr_used:
        warnings = [
            "Tekst odczytano przez OCR; wynik zachowano do ręcznej weryfikacji."
        ]
    elif needs_ocr:
        warnings = [_OCR_WARNING]
    else:
        warnings = []
    extraction = TextExtractionResult(
        pages=pages,
        tables=tables,
        page_qualities=[
            float(source["quality_score"]) for _ in pages
        ],
        blocks=[[] for _ in pages],
        quality_score=float(source["quality_score"]),
        needs_ocr=needs_ocr,
        ocr_used=ocr_used,
        extraction_method=source.get(
            "extraction_method", "ocr" if ocr_used else "pdf_text"
        ),
        ocr_engine_version=source.get("ocr_engine_version"),
        manual_review_required=manual_review_required,
        warnings=warnings,
    )
    return extraction, source


def _build_document_blob(source: dict[str, Any]) -> DocumentBlob:
    media_type = source["media_type"]
    content = b"%PDF-fixture" if media_type == "application/pdf" else b"<html>"
    return DocumentBlob(
        content=content,
        media_type=media_type,
        filename=source["filename"],
        source_metadata=SourceMetadata(
            source_name="MPZP_BIP",
            source_url=source["url"],
            confidence=0.9,
            manual_review_required=bool(source["needs_ocr"]),
        ),
    )


def _zone_requires_manual_review(
    result: MpzpParseResult,
    zone: MpzpZoneResult,
    extraction: TextExtractionResult,
) -> bool:
    if extraction.manual_review_required:
        return True
    if any(parameter.manual_review_required for parameter in zone.parameters):
        return True
    return any(
        warning.zone_symbol == zone.zone_symbol for warning in result.warnings
    )


def _assert_parameter_matches(
    zone: MpzpZoneResult,
    parameter_expected: dict[str, Any],
) -> None:
    name = parameter_expected["name"]
    actual = [parameter for parameter in zone.parameters if parameter.name == name]

    if not parameter_expected["expected_found"]:
        assert parameter_expected["reason_if_not_found"], (
            f"Brak parametru {name} musi mieć udokumentowaną przyczynę"
        )
        assert not actual, (
            f"Strefa {zone.zone_symbol}: parametr {name} miał pozostać "
            f"nieznaleziony, ale zwrócono {[p.normalized_value for p in actual]}"
        )
        return

    assert actual, f"Strefa {zone.zone_symbol}: nie znaleziono parametru {name}"
    allowed_values = parameter_expected["allowed_values"]
    min_confidence = parameter_expected["min_confidence"]
    expected_conditions = parameter_expected.get("expected_conditions")
    if expected_conditions is not None:
        # Wartości warunkowe (PV3-08): każda wartość ma dokładnie oczekiwane warunki ``rodzaj:etykieta``,
        # nie jest sprzecznością i nie wymusza ręcznej weryfikacji.
        found = {
            str(parameter.normalized_value): sorted(f"{c.kind}:{c.label}" for c in parameter.conditions)
            for parameter in actual
        }
        assert found == {key: sorted(value) for key, value in expected_conditions.items()}, (
            f"Strefa {zone.zone_symbol}: {name} ma inne warunki niż oczekiwane"
        )
        # Wartość warunkowa nie jest sprzecznością: bez grupy konfliktu i bez rodzaju ``conflict``. Ręczna
        # weryfikacja może jej wymagać z powodu skalibrowanej pewności (np. wieloznaczna sekcja w trybie
        # dotychczasowym), ale nigdy z powodu konfliktu.
        assert all(
            parameter.value_kind == ("conditional" if parameter.conditions else "unconditional")
            and parameter.conflict_group_id is None
            for parameter in actual
        ), f"Strefa {zone.zone_symbol}: wartości warunkowe {name} nie są sprzecznością"
    elif allowed_values is not None:
        actual_values = {parameter.normalized_value for parameter in actual}
        assert actual_values <= set(allowed_values), (
            f"Strefa {zone.zone_symbol}: {name}={actual_values} wykracza poza "
            f"dozwolone wartości konfliktu {set(allowed_values)}"
        )
        assert all(parameter.manual_review_required for parameter in actual), (
            f"Strefa {zone.zone_symbol}: wszystkie wartości konfliktu {name} "
            "muszą wymagać ręcznej weryfikacji"
        )
    else:
        assert len(actual) == 1, (
            f"Strefa {zone.zone_symbol}: oczekiwano jednej wartości {name}, "
            f"otrzymano {[p.normalized_value for p in actual]}"
        )
        actual_value = actual[0].normalized_value
        expected_value = parameter_expected["normalized_value"]
        if isinstance(expected_value, (int, float)):
            assert isinstance(actual_value, (int, float))
            assert actual_value == pytest.approx(expected_value, abs=1e-6)
        else:
            assert actual_value == expected_value

    if min_confidence is not None:
        assert all(
            parameter.confidence >= min_confidence for parameter in actual
        ), (
            f"Strefa {zone.zone_symbol}: confidence {name} spadło poniżej "
            f"{min_confidence}"
        )


def _assert_matches_expected(
    result: MpzpParseResult,
    expected: dict[str, Any],
    extraction: TextExtractionResult,
) -> None:
    structural = expected.get("structural_expectations")
    if structural is not None:
        assert extraction.needs_ocr is structural["needs_ocr"]
        assert (
            extraction.manual_review_required
            is structural["manual_review_required"]
        )
        assert result.status == structural["status"]
        matching_warnings = [
            warning
            for warning in result.warnings
            if warning.code == structural["warning_code"]
        ]
        assert matching_warnings
        assert any(
            structural["warning_text_contains"] in warning.message
            for warning in matching_warnings
        )

    zones_by_symbol = {zone.zone_symbol: zone for zone in result.zones}
    for zone_expected in expected["expectations"]:
        zone_symbol = zone_expected["zone_symbol"]
        assert zone_symbol in zones_by_symbol
        zone = zones_by_symbol[zone_symbol]
        expected_manual = zone_expected["expected_manual_review_required"]
        if expected_manual is not None:
            assert (
                _zone_requires_manual_review(result, zone, extraction)
                is expected_manual
            )
        for parameter_expected in zone_expected["parameters"]:
            _assert_parameter_matches(zone, parameter_expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_dir",
    FIXTURE_DIRS,
    ids=[path.name for path in FIXTURE_DIRS],
)
async def test_mpzp_parser_regression_fixture(fixture_dir: Path) -> None:
    expected = _load_json(fixture_dir / "expected.json")
    extraction, source = _build_extraction(fixture_dir)
    document_blob = _build_document_blob(source)

    if expected["document_format"] == "pdf_table":
        assert extraction.tables
    if expected["document_format"] == "html":
        assert source["media_type"] == "text/html"
        assert extraction.tables == []
    if expected["document_format"] == "pdf_scan_real":
        assert extraction.pages
        assert all(not page.strip() for page in extraction.pages)
    if expected["document_format"] == "pdf_scan_ocr_real":
        assert extraction.pages
        assert all(page.strip() for page in extraction.pages)
        assert extraction.ocr_used is True
        assert extraction.extraction_method == "ocr"
        assert extraction.ocr_engine_version
        assert "Tesseract" in extraction.ocr_engine_version

    extract_mock = AsyncMock(return_value=extraction)
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=extract_mock,
    ):
        result = await parse_mpzp_document(
            document_blob,
            expected["zone_symbols_to_test"],
        )

    extract_mock.assert_awaited_once_with(document_blob)
    _assert_matches_expected(result, expected, extraction)


def test_assert_matches_expected_detects_changed_parameter_value() -> None:
    result = MpzpParseResult(
        status="complete",
        zones=[
            MpzpZoneResult(
                zone_symbol="MN",
                parameters=[
                    MpzpParameter(
                        name="max_building_height_m",
                        normalized_value=12.0,
                        confidence=0.9,
                        manual_review_required=False,
                    )
                ],
            )
        ],
    )
    expected = {
        "structural_expectations": None,
        "expectations": [
            {
                "zone_symbol": "MN",
                "expected_manual_review_required": None,
                "parameters": [
                    {
                        "name": "max_building_height_m",
                        "expected_found": True,
                        "normalized_value": 9.0,
                        "allowed_values": None,
                        "min_confidence": 0.8,
                        "reason_if_not_found": None,
                    }
                ],
            }
        ],
    }

    with pytest.raises(AssertionError):
        _assert_matches_expected(
            result,
            expected,
            TextExtractionResult(pages=["fixture"], quality_score=1.0),
        )
