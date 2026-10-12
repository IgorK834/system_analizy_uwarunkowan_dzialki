"""Ewaluacja parsera MPZP na zbiorze realnych uchwał ``tests/fixtures/mpzp_live`` (AU-102, Task 22.2).

Mierzy, jaki odsetek adnotowanych stref ma co najmniej jeden parametr albo jawny, poprawny powód braku
(``ZONE_NOT_IN_DOCUMENT`` dla symbolu spoza uchwały, ``SCANNED_NO_OCR`` dla skanu bez tekstu), osobno dla trybów
``legacy`` i ``v3``. Metryka zadania („co najmniej jeden parametr albo powód”) nie sprawdza, SKĄD parametr
pochodzi, więc obok niej raportowana jest metryka surowa (``strict``): parametr liczy się tylko wtedy, gdy jego
strona leży w adnotowanej sekcji strefy. Raportowana jest też odtwarzalność oczekiwanych parametrów (recall).
Zbiór jest odrębny od 21 próbek BK-603.

Uruchomienie (z katalogu ``backend``)::

    python -m scripts.evaluate_mpzp_live [--mode legacy v3] [--out wynik.json] [--split development holdout]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

LIVE_DIR = BACKEND_DIR / "tests" / "fixtures" / "mpzp_live"

REASON_CODES = ("ZONE_NOT_IN_DOCUMENT", "SCANNED_NO_OCR")
_NUMERIC_TOLERANCE = 1e-6
_WORD_COVERAGE = 0.6


def norm(text: str | None) -> str:
    return " ".join((text or "").replace(" ", " ").split()).casefold()


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[^\W\d_]{4,}|\d+", text)}


def _coverage(quote: str, evidence: str) -> float:
    """Jaki ułamek słów (od 4 liter) i liczb cytatu występuje w dowodzie."""
    wanted = _words(quote)
    return len(wanted & _words(evidence)) / len(wanted) if wanted else 0.0


def load_manifest() -> dict[str, Any]:
    return json.loads((LIVE_DIR / "manifest.json").read_text("utf-8"))


def build_extraction(document: dict[str, Any]):  # noqa: ANN201
    """Wynik ekstrakcji odtworzony z zamrożonych stron (jak w ``test_mpzp_parser_regression``)."""
    from app.services.mpzp_parser_extract import ExtractedTable, TextExtractionResult

    directory = LIVE_DIR / document["slug"]
    pages = json.loads((directory / "pages.json").read_text("utf-8"))["pages"]
    source = json.loads((directory / "source.json").read_text("utf-8"))
    tables_path = directory / "tables.json"
    tables = [ExtractedTable(**item) for item in json.loads(tables_path.read_text("utf-8"))] if tables_path.exists() else []
    needs_ocr = bool(source["needs_ocr"])
    return TextExtractionResult(
        pages=pages,
        tables=tables,
        page_qualities=[float(source["quality_score"])] * len(pages),
        blocks=[[] for _ in pages],
        quality_score=float(source["quality_score"]),
        needs_ocr=needs_ocr,
        ocr_used=bool(source.get("ocr_used", False)),
        extraction_method=source.get("extraction_method", "pdf_text"),
        ocr_engine_version=source.get("ocr_engine_version"),
        manual_review_required=bool(source.get("manual_review_required", needs_ocr)),
        warnings=(
            ["Dokument wymaga OCR, ale OCR nie został użyty; tekst stron jest pusty."] if needs_ocr else []
        ),
    ), source


def document_blob(source: dict[str, Any]):  # noqa: ANN201
    from app.schemas.source import SourceMetadata
    from app.services.mpzp_fetch import DocumentBlob

    return DocumentBlob(
        content=b"%PDF-fixture",
        media_type=source["media_type"],
        filename=source["filename"],
        source_metadata=SourceMetadata(
            source_name="MPZP_BIP",
            source_url=source["url"],
            confidence=0.9,
            manual_review_required=bool(source["needs_ocr"]),
        ),
    )


def group_zones(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Strefy dokumentu zgrupowane po symbolu (ten sam symbol w dwóch sekcjach = jedna strefa)."""
    grouped: dict[str, dict[str, Any]] = {}
    for zone in document["zones"]:
        entry = grouped.setdefault(
            zone["zone_symbol"],
            {"zone_symbol": zone["zone_symbol"], "expected": zone["expected"], "layouts": [], "sections": [],
             "parameters": [], "corpus_parcel": zone.get("corpus_parcel")},
        )
        entry["layouts"].append(zone["layout"])
        if zone["section"]:
            entry["sections"].append((zone["section"]["first_page"], zone["section"]["last_page"]))
        entry["parameters"].extend(zone["parameters"])
    return grouped


def _values_equal(expected: Any, actual: Any) -> bool:
    if expected is None:
        return True
    try:
        return abs(float(expected) - float(actual)) <= max(_NUMERIC_TOLERANCE, abs(float(expected)) * 1e-3)
    except (TypeError, ValueError):
        return norm(str(expected)) == norm(str(actual))


def _in_sections(page: int | None, sections: list[tuple[int, int]]) -> bool:
    return page is not None and any(first <= page <= last for first, last in sections)


def _matches(expected: dict[str, Any], parameter: Any, sections: list[tuple[int, int]]) -> bool:
    if parameter.name != expected["name"]:
        return False
    if expected["normalized_value"] is not None:
        if not _values_equal(expected["normalized_value"], parameter.normalized_value):
            return False
    elif expected["quote"]:
        # Parametr opisowy: dowód musi się pokrywać z cytatem — zawieranie w którąkolwiek stronę albo pokrycie
        # co najmniej ``_WORD_COVERAGE`` słów cytatu (parser zwraca wartość bez etykiety, cytat adnotacji ją ma).
        quote, evidence = norm(expected["quote"]), norm(parameter.source_text) + " " + norm(str(parameter.normalized_value))
        if quote not in evidence and norm(parameter.source_text) not in quote and _coverage(quote, evidence) < _WORD_COVERAGE:
            return False
    # Strona: sekcja strefy, a gdy sekcje nieznane (strefa zbiorcza w tekście ogólnym) — dowolna strona dokumentu.
    return _in_sections(parameter.page_number, sections) if sections else True


def evaluate_zone(zone: dict[str, Any], result: Any) -> dict[str, Any]:
    symbol = zone["zone_symbol"]
    parsed = next((item for item in result.zones if item.zone_symbol == symbol), None)
    parameters = list(parsed.parameters) if parsed else []
    codes = sorted({w.code for w in result.warnings if w.zone_symbol == symbol})
    sections = zone["sections"]
    expected_kind = zone["expected"]
    in_section = [p for p in parameters if _in_sections(p.page_number, sections)]
    outside = [p for p in parameters if sections and not _in_sections(p.page_number, sections)]
    matched = [item for item in zone["parameters"] if any(_matches(item, p, sections) for p in parameters)]
    verdict: str
    if expected_kind == "parameters":
        verdict = "ok_parameters" if parameters else "fail_no_parameters"
    else:
        has_reason = expected_kind in codes
        if parameters:
            verdict = "fail_unexpected_parameters"
        elif has_reason:
            verdict = "ok_reason"
        else:
            verdict = "fail_wrong_reason"
    strict_ok = (
        bool(in_section) if sections and expected_kind == "parameters" else verdict.startswith("ok")
    )
    return {
        "zone_symbol": symbol,
        "expected": expected_kind,
        "strict_ok": strict_ok,
        "layouts": sorted(set(zone["layouts"])),
        "corpus_parcel": zone["corpus_parcel"],
        "verdict": verdict,
        "parameters": len(parameters),
        "parameter_names": sorted({p.name for p in parameters}),
        "in_section": len(in_section),
        "outside_section": len(outside),
        "outside_pages": sorted({p.page_number for p in outside if p.page_number is not None}),
        "expected_parameters": len(zone["parameters"]),
        "matched_parameters": len(matched),
        "missing_parameters": [f'{i["name"]}={i["normalized_value"]}' for i in zone["parameters"] if i not in matched],
        "codes": codes,
    }


async def evaluate(mode: str, splits: set[str] | None = None, slugs: set[str] | None = None) -> dict[str, Any]:
    """Wynik ewaluacji jednego trybu parsera na całym zbiorze (albo jego części)."""
    from app.services.mpzp_parser import parse_mpzp_document

    manifest = load_manifest()
    zones: list[dict[str, Any]] = []
    for document in manifest["documents"]:
        if splits and document["split"] not in splits:
            continue
        if slugs and document["slug"] not in slugs:
            continue
        extraction, source = build_extraction(document)
        grouped = group_zones(document)
        blob = document_blob(source)
        with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=extraction)):
            result = await parse_mpzp_document(blob, list(grouped), mode=mode)  # type: ignore[arg-type]
        for zone in grouped.values():
            item = evaluate_zone(zone, result)
            item.update(slug=document["slug"], municipality=document["municipality"], split=document["split"])
            zones.append(item)
    return summarize(mode, zones)


def summarize(mode: str, zones: list[dict[str, Any]]) -> dict[str, Any]:
    def share(subset: list[dict[str, Any]]) -> dict[str, Any]:
        ok = sum(1 for z in subset if z["verdict"].startswith("ok"))
        return {"zones": len(subset), "ok": ok, "share": round(ok / len(subset), 4) if subset else None}

    expecting = [z for z in zones if z["expected"] == "parameters"]
    with_parameter = sum(1 for z in expecting if z["parameters"] > 0)
    expected_total = sum(z["expected_parameters"] for z in zones)
    matched_total = sum(z["matched_parameters"] for z in zones)
    reported = sum(z["parameters"] for z in zones)
    outside = sum(z["outside_section"] for z in zones)
    by_layout: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for zone in zones:
        for layout in zone["layouts"]:
            by_layout[layout].append(zone)
    strict_ok = sum(1 for z in zones if z["strict_ok"])
    return {
        "mode": mode,
        "overall": share(zones),
        "strict": {"zones": len(zones), "ok": strict_ok, "share": round(strict_ok / len(zones), 4) if zones else None},
        "zones_with_parameter": {
            "zones": len(expecting),
            "with_parameter": with_parameter,
            "share": round(with_parameter / len(expecting), 4) if expecting else None,
        },
        "by_split": {split: share([z for z in zones if z["split"] == split]) for split in sorted({z["split"] for z in zones})},
        "by_municipality": {m: share([z for z in zones if z["municipality"] == m]) for m in sorted({z["municipality"] for z in zones})},
        "by_layout": {layout: share(items) for layout, items in sorted(by_layout.items())},
        "reported_parameters": reported,
        "parameters_outside_section": outside,
        "expected_parameters": expected_total,
        "matched_expected_parameters": matched_total,
        "expected_recall": round(matched_total / expected_total, 4) if expected_total else None,
        "verdicts": dict(Counter(z["verdict"] for z in zones)),
        "zones": zones,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", nargs="+", default=["legacy", "v3"])
    parser.add_argument("--split", nargs="*", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    splits = set(args.split) if args.split else None
    report: dict[str, Any] = {}
    for mode in args.mode:
        summary = asyncio.run(evaluate(mode, splits))
        report[mode] = summary
        print(
            f"[{mode}] strefy: {summary['overall']['ok']}/{summary['overall']['zones']} "
            f"({summary['overall']['share']:.0%}) ok; surowo (parametr w sekcji strefy): {summary['strict']['ok']}/"
            f"{summary['strict']['zones']} ({summary['strict']['share']:.0%}); z parametrem: {summary['zones_with_parameter']['with_parameter']}/"
            f"{summary['zones_with_parameter']['zones']}; recall oczekiwanych parametrów: {summary['expected_recall']}; "
            f"parametry poza sekcją: {summary['parameters_outside_section']}"
        )
        if args.verbose:
            for zone in summary["zones"]:
                print(f"   {zone['slug'][:26]:26} {zone['zone_symbol']:12} {zone['verdict']:26} params={zone['parameters']:3} "
                      f"match={zone['matched_parameters']}/{zone['expected_parameters']} codes={zone['codes']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8")


if __name__ == "__main__":
    main()
