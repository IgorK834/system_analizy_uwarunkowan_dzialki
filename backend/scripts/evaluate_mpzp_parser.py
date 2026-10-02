#!/usr/bin/env python3
"""Offline evaluation of MPZP extraction engines against manual annotations (BK-603, PV3-03).

The evaluator never downloads anything. It reads frozen text snapshots of public
planning resolutions (``pages.json``, ``source.json``, optional ``tables.json``),
runs an engine (``legacy`` = the production ``parse_mpzp_document`` pipeline with the
frozen extraction patched in; ``v3`` and ``hybrid`` register themselves when they
exist) and compares every returned parameter with annotations that were written by
hand *before* an engine was run on the final split.

Besides the value, the evaluator checks the **source** of every correct value
(``source_consistent``): the returned page and location must agree with the annotated
evidence in the zone's block. A correct value taken from another page or zone counts
as an assignment error, not as a hit. Several engines can be run in one invocation
and are then compared pairwise on identical (sample, zone, parameter) pairs.

Scoring vocabulary (per zone and catalog parameter, ``A_req`` = required
annotated values, ``A_acc`` = acceptable values that are neither required nor
errors, ``P`` = values returned by the parser):

``tp_exact``   ``A_req`` is non-empty and ``A_req <= P <= A_req | A_acc``
``tp_partial`` ``P`` intersects ``A_req`` but is not exact
``tp_wrong``   ``P`` is non-empty and disjoint from ``A_req``
``fn``         ``A_req`` is non-empty and ``P`` is empty
``fp``         ``A_req`` is empty and ``P`` has a value outside ``A_acc``
``tn``         both empty
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    # app.core.settings reads a relative .env while importing; keep the import
    # anchored in backend and restore the caller's working directory afterwards.
    os.chdir(BACKEND_DIR)
    from app.schemas.mpzp import MpzpParseResult  # noqa: E402
    from app.schemas.source import SourceMetadata  # noqa: E402
    from app.services.mpzp_fetch import DocumentBlob  # noqa: E402
    from app.services.mpzp_parser import MPZP_PARSER_VERSION, parse_mpzp_document  # noqa: E402
    from app.services.mpzp_parser_extract import (  # noqa: E402
        ExtractedTable,
        TextExtractionResult,
    )
finally:
    os.chdir(_IMPORT_CWD)

from scripts import mpzp_annotation_agreement as agreement  # noqa: E402
from scripts import mpzp_eval_compare as compare  # noqa: E402
from scripts.mpzp_eval_engines import (  # noqa: E402
    FORBIDDEN_REVIEW_STATUSES,
    LLM_API_KEY_ENV,
    Engine,
    EngineContext,
    EngineResult,
    EngineSpec,
    EngineUnavailableError,
    EngineUsage,
    EngineValue,
    LiveModeError,
    ReplayIntegrityError,
    ReplayMissError,
    build_gateway,
    create_engine,
    get_engine_spec,
    register_engine,
)

SCHEMA_VERSION = "2.0.0"
DEFAULT_MANIFEST = BACKEND_DIR / "tests" / "fixtures" / "mpzp_evaluation" / "manifest.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "evaluation" / "results" / "parser"
MIN_SAMPLES = 20
MIN_GMINAS = 5
VALUE_TOLERANCE = 1e-6
WILSON_Z = 1.959964

# name -> (implied operators, unit). The parser encodes the operator in the name.
CATALOG: dict[str, dict[str, Any]] = {
    "max_building_height_m": {"operators": ("max",), "unit": "m"},
    "min_intensity": {"operators": ("min", "range_lower"), "unit": "ratio"},
    "max_intensity": {"operators": ("max", "range_upper"), "unit": "ratio"},
    "max_building_coverage_percent": {"operators": ("max",), "unit": "percent"},
    "min_biologically_active_percent": {"operators": ("min",), "unit": "percent"},
    "roof_angle_min_deg": {"operators": ("min", "range_lower"), "unit": "deg"},
    "roof_angle_max_deg": {"operators": ("max", "range_upper"), "unit": "deg"},
    "max_storeys": {"operators": ("max",), "unit": "count"},
    "setback_m": {"operators": ("exact",), "unit": "m"},
}
NORMALIZATION_RULES = (
    "identity",
    "ratio_to_percent",
    "range_lower",
    "range_upper",
    "word_number",
    "manual",
)
APPLICABILITY = ("zone_section", "general_clause")
STATUSES = ("required", "acceptable")
SAMPLE_FORMATS = ("pdf_text", "pdf_table", "html", "ocr_real", "ocr_simulated")
SPLITS = ("development", "final")
CONFIDENCE_BANDS = (("low", 0.0, 0.5), ("medium", 0.5, 0.8), ("high", 0.8, 1.0000001))

_WORD_NUMBERS = {
    "jeden": 1, "jedna": 1, "jednokondygnacyjne": 1, "jednokondygnacyjny": 1,
    "dwa": 2, "dwie": 2, "dwóch": 2, "dwu": 2,
    "trzy": 3, "trzech": 3, "cztery": 4, "czterech": 4, "pięć": 5, "pięciu": 5,
}


class EvaluationError(ValueError):
    """The corpus or the evaluation contract is invalid."""


# --- normalization and evidence ----------------------------------------------


def _numbers(raw: str) -> list[float]:
    return [float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[.,]\d+)?", raw)]


def normalize_value(rule: str, raw: str) -> float | None:
    """Explicit normalization of an annotated raw fragment; ``None`` = manual."""
    if rule == "manual":
        return None
    if rule == "word_number":
        for token in re.findall(r"[^\W\d_]+", raw.lower()):
            if token in _WORD_NUMBERS:
                return float(_WORD_NUMBERS[token])
        raise EvaluationError(f"No number word in {raw!r}.")
    numbers = _numbers(raw)
    if rule in {"identity", "ratio_to_percent", "range_lower"}:
        if not numbers:
            raise EvaluationError(f"No number in {raw!r}.")
        value = numbers[0]
        return round(value * 100.0, 9) if rule == "ratio_to_percent" else value
    if rule == "range_upper":
        if len(numbers) < 2:
            raise EvaluationError(f"No range in {raw!r}.")
        return numbers[1]
    raise EvaluationError(f"Unknown normalization rule {rule!r}.")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """``normalize_text`` plus, for every normalized character, its index in ``text``."""
    chars: list[str] = []
    origin: list[int] = []
    pending_space = False
    for index, char in enumerate(text):
        if char.isspace():
            pending_space = bool(chars)
            if pending_space:
                space_index = index
            continue
        if pending_space:
            chars.append(" ")
            origin.append(space_index)
            pending_space = False
        chars.append(char)
        origin.append(index)
    return "".join(chars), origin


class TruthText:
    """Normalized text of a document with page offsets for evidence lookup."""

    def __init__(self, pages: Sequence[str], page_numbers: Sequence[int]) -> None:
        self.page_numbers = list(page_numbers)
        self._starts: list[int] = []
        self._lengths: list[int] = []
        self._raw_origin: list[list[int]] = []
        parts: list[str] = []
        offset = 0
        for page in pages:
            normalized, origin = normalize_with_map(page)
            self._starts.append(offset)
            self._lengths.append(len(normalized))
            self._raw_origin.append(origin)
            parts.append(normalized)
            offset += len(normalized) + 1
        self.text = " ".join(parts)

    def anchor_start(self, anchor: str) -> int:
        start = self.text.find(normalize_text(anchor))
        if start < 0:
            raise EvaluationError(f"Anchor not found: {anchor!r}")
        return start

    def locate_span(self, evidence: str, anchor: str | None, window: int = 5000) -> tuple[int, int, int]:
        """Returns ``(source page number, start, end)`` of ``evidence`` (after ``anchor``)."""
        needle = normalize_text(evidence)
        start = self.anchor_start(anchor) if anchor else 0
        end = start + window + len(needle) if anchor else len(self.text)
        position = self.text.find(needle, start, end)
        if position < 0:
            where = "after the anchor" if anchor else "in the document"
            raise EvaluationError(f"Evidence not found {where}: {evidence!r}")
        index = max(i for i, offset in enumerate(self._starts) if offset <= position)
        return self.page_numbers[index], position, position + len(needle)

    def locate(self, evidence: str, anchor: str | None, window: int = 5000) -> int:
        """Returns the source page number of ``evidence`` (after ``anchor``)."""
        return self.locate_span(evidence, anchor, window)[0]

    def find_in_page(self, needle: str, page_number: int) -> list[int]:
        """Start offsets of every occurrence of the (normalized) ``needle`` on one page."""
        if page_number not in self.page_numbers or not needle:
            return []
        index = self.page_numbers.index(page_number)
        start, length = self._starts[index], self._lengths[index]
        page_text = self.text[start : start + length]
        found: list[int] = []
        position = page_text.find(needle)
        while position >= 0:
            found.append(start + position)
            position = page_text.find(needle, position + 1)
        return found

    def raw_span_to_doc(self, page_number: int, raw_start: int, raw_end: int) -> tuple[int, int] | None:
        """Maps a character range of the raw page string to document offsets."""
        if page_number not in self.page_numbers or raw_end <= raw_start:
            return None
        index = self.page_numbers.index(page_number)
        origin = self._raw_origin[index]
        first = bisect_left(origin, raw_start)
        last = bisect_left(origin, raw_end) - 1
        if first >= len(origin) or last < first:
            return None
        return self._starts[index] + first, self._starts[index] + last + 1


# --- zone blocks and the source of a returned value ------------------------------------

# A zone block runs from the zone anchor to the last annotated evidence of that anchor
# (plus a tolerance for the rest of the sentence), never into the next annotated anchor.
BLOCK_TOLERANCE = 300
SOURCE_STATUSES = (
    "consistent", "indeterminate", "wrong_block", "wrong_page", "unlocatable", "not_applicable", "not_checked",
)
_SOURCE_RANK = {"consistent": 0, "indeterminate": 1, "wrong_block": 2, "wrong_page": 3, "unlocatable": 4}


@dataclass(frozen=True)
class AnnotationLocation:
    value: float
    page: int
    evidence: tuple[int, int]
    block: tuple[int, int] | None  # None: no anchor, only the evidence span is known
    applicability: str
    status: str


def zone_block_spans(sample: Mapping[str, Any], truth: TruthText) -> dict[str, tuple[int, int]]:
    """Block (document offsets) of every anchor used by the annotations of one sample."""
    starts: dict[str, int] = {}
    last_end: dict[str, int] = {}
    for zone in sample["zones"]:
        for annotation in zone.get("annotations", []):
            anchor = annotation.get("anchor")
            if not anchor:
                continue
            starts.setdefault(anchor, truth.anchor_start(anchor))
            end = truth.locate_span(annotation["evidence"], anchor)[2]
            last_end[anchor] = max(last_end.get(anchor, 0), end)
    ordered = sorted(set(starts.values()))
    blocks: dict[str, tuple[int, int]] = {}
    for anchor, start in starts.items():
        later = [other for other in ordered if other > start]
        limit = min(last_end[anchor] + BLOCK_TOLERANCE, later[0] if later else len(truth.text))
        blocks[anchor] = (start, max(limit, last_end[anchor]))
    return blocks


def annotation_locations(
    sample: Mapping[str, Any], truth: TruthText
) -> dict[tuple[str, str], list[AnnotationLocation]]:
    """Where each annotated value sits: page, evidence span and zone block."""
    blocks = zone_block_spans(sample, truth)
    result: dict[tuple[str, str], list[AnnotationLocation]] = defaultdict(list)
    for zone in sample["zones"]:
        for annotation in zone.get("annotations", []):
            anchor = annotation.get("anchor")
            page, start, end = truth.locate_span(annotation["evidence"], anchor)
            result[(zone["symbol"], annotation["parameter"])].append(
                AnnotationLocation(
                    value=float(annotation["normalized_value"]),
                    page=page,
                    evidence=(start, end),
                    block=blocks.get(anchor) if anchor else None,
                    applicability=annotation.get("applicability", "zone_section"),
                    status=annotation.get("status", "required"),
                )
            )
    return result


def _value_positions(output: EngineValue, truth: TruthText, page: int) -> list[tuple[int, int]] | None:
    """Document spans of the returned value; ``None`` when it cannot be located."""
    if output.span is not None:
        mapped = truth.raw_span_to_doc(output.span.page, output.span.start, output.span.end)
        return [mapped] if mapped else None
    snippet = normalize_text(output.source_text or "")
    raw = normalize_text(output.raw_value or "")
    if snippet:
        starts = truth.find_in_page(snippet, page)
        if not starts:
            return None
        inner = snippet.find(raw) if raw else -1
        if inner >= 0:
            return [(start + inner, start + inner + len(raw)) for start in starts]
        return [(start, start + len(snippet)) for start in starts]
    if raw:
        starts = truth.find_in_page(raw, page)
        return [(start, start + len(raw)) for start in starts] or None
    return None


def _inside(position: tuple[int, int], location: AnnotationLocation) -> bool:
    if location.block is not None:
        middle = (position[0] + position[1]) / 2
        return location.block[0] <= middle < location.block[1]
    return position[0] < location.evidence[1] and location.evidence[0] < position[1]


def _check_against(
    output: EngineValue, page: int, location: AnnotationLocation, truth: TruthText, page_only: bool
) -> tuple[str, str | None]:
    if page != location.page:
        return "wrong_page", f"returned page {page}, annotated page {location.page}"
    if page_only:
        return "consistent", "page_only"
    positions = _value_positions(output, truth, page)
    if positions is None:
        return "unlocatable", "source text not found on the returned page"
    inside = [_inside(position, location) for position in positions]
    if all(inside):
        return "consistent", None
    if not any(inside):
        return "wrong_block", "value lies outside the annotated zone block"
    return "indeterminate", "identical source text occurs inside and outside the zone block"


def check_source(
    output: EngineValue,
    locations: Sequence[AnnotationLocation],
    truth: TruthText | None,
    page_only: bool = False,
) -> dict[str, Any]:
    """Does the returned value come from the annotated page and zone block?

    ``locations`` are the annotations of the same zone and parameter that carry the
    returned value; with several evidences the most favourable one decides. With
    ``page_only`` (the annotation text is not the text the engine read, e.g. a
    simulated scan) only the page can be compared.
    """
    if truth is None:
        return {"status": "not_checked", "basis": None, "reason": "no source text"}
    if not locations:
        return {"status": "not_applicable", "basis": None, "reason": "value not in the annotation"}
    page = output.span.page if output.span is not None else output.page
    if page is None:
        return {"status": "unlocatable", "basis": None, "reason": "engine returned no page"}
    best: tuple[str, str | None] | None = None
    for location in locations:
        outcome = _check_against(output, page, location, truth, page_only)
        if best is None or _SOURCE_RANK[outcome[0]] < _SOURCE_RANK[best[0]]:
            best = outcome
    assert best is not None
    status, reason = best
    basis = "page" if page_only else ("block" if any(loc.block for loc in locations) else "evidence")
    return {
        "status": status,
        "basis": basis,
        "reason": None if reason == "page_only" else reason,
        "returned_page": page,
        "annotation_pages": sorted({loc.page for loc in locations}),
    }


# --- corpus loading and validation ---------------------------------------------


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def annotations_sha256(manifest: Mapping[str, Any]) -> str:
    """Hash of everything that defines ground truth (excludes the freeze record)."""
    frozen = {
        key: manifest[key]
        for key in ("catalog_version", "documents", "samples", "normalization_rules")
        if key in manifest
    }
    return sha256_bytes(canonical_json(frozen).encode("utf-8"))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_document(base_dir: Path, document: Mapping[str, Any]) -> dict[str, Any]:
    """Loads a frozen text snapshot: pages, source metadata and tables."""
    directory = (base_dir / document["path"]).resolve()
    pages = _read_json(directory / "pages.json")["pages"]
    source = _read_json(directory / "source.json")
    tables_path = directory / "tables.json"
    tables = _read_json(tables_path) if tables_path.is_file() else []
    page_numbers = source.get("source_page_numbers") or list(range(1, len(pages) + 1))
    return {
        "directory": directory,
        "pages": pages,
        "source": source,
        "tables": tables,
        "page_numbers": page_numbers,
        "pages_sha256": sha256_bytes((directory / "pages.json").read_bytes()),
    }


def load_corpus(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = path.read_bytes()
        manifest = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"Cannot load corpus {path}: {exc}") from exc
    errors = validate_corpus(manifest, path.parent)
    if errors:
        raise EvaluationError("Invalid corpus: " + "; ".join(errors[:20]))
    return manifest, sha256_bytes(payload)


def validate_corpus(manifest: Mapping[str, Any], base_dir: Path) -> list[str]:
    """Structural and evidence validation; returns every problem found."""
    errors: list[str] = []
    documents = manifest.get("documents")
    samples = manifest.get("samples")
    if not isinstance(documents, Mapping) or not isinstance(samples, list):
        return ["manifest needs `documents` (object) and `samples` (list)"]
    loaded: dict[str, dict[str, Any]] = {}
    texts: dict[str, TruthText] = {}
    for doc_id, document in documents.items():
        try:
            loaded[doc_id] = load_document(base_dir, document)
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            errors.append(f"document {doc_id}: cannot load ({exc})")
            continue
        if loaded[doc_id]["pages_sha256"] != document.get("pages_sha256"):
            errors.append(f"document {doc_id}: pages.json hash differs from manifest")
        if document.get("split") not in SPLITS:
            errors.append(f"document {doc_id}: invalid split")
        texts[doc_id] = TruthText(loaded[doc_id]["pages"], loaded[doc_id]["page_numbers"])

    if len(samples) < MIN_SAMPLES:
        errors.append(f"{len(samples)} samples < {MIN_SAMPLES}")
    gminas = {documents[s["document_id"]].get("gmina") for s in samples if s.get("document_id") in documents}
    if len(gminas) < MIN_GMINAS:
        errors.append(f"{len(gminas)} gminas < {MIN_GMINAS}")
    formats = {sample.get("format") for sample in samples}
    for required in ("pdf_text", "pdf_table", "html"):
        if required not in formats:
            errors.append(f"format {required} is not represented")
    if not formats & {"ocr_real", "ocr_simulated"}:
        errors.append("no OCR sample")
    if sum(bool(sample.get("multi_zone")) for sample in samples) < 3:
        errors.append("fewer than 3 multi-zone samples")

    seen_ids: set[str] = set()
    for sample in samples:
        sample_id = str(sample.get("sample_id"))
        if sample_id in seen_ids:
            errors.append(f"duplicate sample_id {sample_id}")
        seen_ids.add(sample_id)
        errors.extend(_validate_sample(sample, documents, texts))

    freeze = manifest.get("freeze")
    if not isinstance(freeze, Mapping):
        errors.append("missing freeze record")
    elif freeze.get("annotations_sha256") != annotations_sha256(manifest):
        errors.append("annotations changed after freeze (annotations_sha256 differs)")
    return errors


def _validate_sample(
    sample: Mapping[str, Any],
    documents: Mapping[str, Any],
    texts: Mapping[str, TruthText],
) -> list[str]:
    sid = str(sample.get("sample_id"))
    errors: list[str] = []
    doc_id = sample.get("document_id")
    truth_id = sample.get("truth_document_id") or doc_id
    if doc_id not in documents or truth_id not in texts:
        return [f"{sid}: unknown document"]
    if sample.get("format") not in SAMPLE_FORMATS:
        errors.append(f"{sid}: invalid format {sample.get('format')!r}")
    if sample.get("split") != documents[doc_id].get("split"):
        errors.append(f"{sid}: split differs from its document")
    zones = sample.get("zones")
    if not isinstance(zones, list) or not zones:
        return [*errors, f"{sid}: no zones"]
    if bool(sample.get("multi_zone")) != (len(zones) > 1):
        errors.append(f"{sid}: multi_zone flag does not match zone count")
    seen: set[tuple[str, str, float | None, str]] = set()
    for zone in zones:
        symbol = str(zone.get("symbol"))
        for index, annotation in enumerate(zone.get("annotations", [])):
            label = f"{sid}/{symbol}#{index}"
            errors.extend(_validate_annotation(label, annotation, texts[truth_id]))
            key = (
                symbol,
                str(annotation.get("parameter")),
                annotation.get("normalized_value"),
                str(annotation.get("applicability")),
            )
            if key in seen:
                errors.append(f"{label}: duplicate annotation")
            seen.add(key)
    return errors


def _validate_annotation(label: str, annotation: Mapping[str, Any], truth: TruthText) -> list[str]:
    errors: list[str] = []
    parameter = annotation.get("parameter")
    entry = CATALOG.get(str(parameter))
    if entry is None:
        return [f"{label}: parameter {parameter!r} is not in the catalog"]
    if annotation.get("operator") not in entry["operators"]:
        errors.append(f"{label}: operator {annotation.get('operator')!r} not allowed for {parameter}")
    if annotation.get("unit") != entry["unit"]:
        errors.append(f"{label}: unit must be {entry['unit']}")
    if annotation.get("status", "required") not in STATUSES:
        errors.append(f"{label}: invalid status")
    if annotation.get("applicability", "zone_section") not in APPLICABILITY:
        errors.append(f"{label}: invalid applicability")
    rule = annotation.get("normalization")
    raw, evidence = annotation.get("raw_value"), annotation.get("evidence")
    value = annotation.get("normalized_value")
    if rule not in NORMALIZATION_RULES:
        errors.append(f"{label}: invalid normalization rule {rule!r}")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        errors.append(f"{label}: normalized_value must be a number")
    if not isinstance(raw, str) or not isinstance(evidence, str) or not raw or not evidence:
        return [*errors, f"{label}: raw_value and evidence are required"]
    if normalize_text(raw) not in normalize_text(evidence):
        errors.append(f"{label}: raw_value is not a substring of evidence")
    if rule in NORMALIZATION_RULES and isinstance(value, (int, float)):
        try:
            expected = normalize_value(str(rule), raw)
        except EvaluationError as exc:
            errors.append(f"{label}: {exc}")
        else:
            if expected is None:
                ambiguity = annotation.get("ambiguity")
                if not isinstance(ambiguity, Mapping) or not ambiguity.get("note"):
                    errors.append(f"{label}: manual normalization needs an ambiguity note")
            elif abs(expected - float(value)) > VALUE_TOLERANCE:
                errors.append(f"{label}: normalization gives {expected}, annotated {value}")
    try:
        page = truth.locate(evidence, annotation.get("anchor"))
    except EvaluationError as exc:
        errors.append(f"{label}: {exc}")
    else:
        if annotation.get("page") != page:
            errors.append(f"{label}: page is {annotation.get('page')}, evidence is on page {page}")
    return errors


# --- fresh final corpus profile (PV3-02) -----------------------------------------------------

SCOPE_STRATEGIES = (1, 2, 3, 4, 5, 6)
DOCUMENT_FIELDS_FINAL = (
    "url", "fetched_at", "content_length", "document_sha256", "legal_basis", "gmina", "voivodeship",
    "tls_verification", "pages_sha256",
)
# Minimum content of a fresh final set (Task 20.2). ``new`` means: not used by the previous corpus.
PROFILES: dict[str, dict[str, Any]] = {
    "final-v2": {
        "min_samples": 20,
        "min_new_gminas": 10,
        "min_voivodeships": 4,
        "min_pdf_table": 5,
        "min_ocr_real": 4,
        "min_html": 4,
        "second_annotator_share": 0.20,
    }
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _file_sha256(base_dir: Path, relative: object) -> str | None:
    if not isinstance(relative, str) or not relative:
        return None
    path = (base_dir / relative).resolve()
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def validate_corpus_profile(manifest: Mapping[str, Any], base_dir: Path, profile: str = "final-v2") -> list[str]:
    """Checks that a corpus is a fresh, human-annotated, independently double-checked final set.

    Complements ``validate_corpus`` (structure, quotes, freeze); returns every problem.
    """
    rules = PROFILES[profile]
    documents = manifest.get("documents") or {}
    samples = manifest.get("samples") or []
    errors: list[str] = []
    final = [item for item in samples if item.get("split") == "final"]
    final_docs = {item["document_id"]: documents.get(item["document_id"], {}) for item in final}

    if len(final) < rules["min_samples"]:
        errors.append(f"{len(final)} final samples < {rules['min_samples']}")
    for sample in samples:
        if sample.get("split") != "final" and sample.get("development_round") not in (1, 2):
            errors.append(f"{sample.get('sample_id')}: a development sample needs development_round 1 or 2")

    previous = manifest.get("previous_corpus")
    old_gminas: set[str] = set()
    old_urls: set[str] = set()
    if not isinstance(previous, Mapping) or not previous.get("corpus_id") or not isinstance(previous.get("gminas"), list):
        errors.append("previous_corpus (corpus_id, gminas, document_urls) is required to prove the set is new")
    else:
        old_gminas, old_urls = set(previous["gminas"]), set(previous.get("document_urls") or [])
        if previous["corpus_id"] == manifest.get("corpus_id"):
            errors.append("corpus_id must differ from previous_corpus.corpus_id")
    gminas = {doc.get("gmina") for doc in final_docs.values()}
    if len(gminas - old_gminas) < rules["min_new_gminas"]:
        errors.append(f"{len(gminas - old_gminas)} new gminas < {rules['min_new_gminas']}")
    voivodeships = {doc.get("voivodeship") for doc in final_docs.values() if doc.get("voivodeship")}
    if len(voivodeships) < rules["min_voivodeships"]:
        errors.append(f"{len(voivodeships)} voivodeships < {rules['min_voivodeships']}")
    for doc_id, doc in final_docs.items():
        for field in DOCUMENT_FIELDS_FINAL:
            if doc.get(field) in (None, ""):
                errors.append(f"document {doc_id}: {field} is required for a final document")
        if isinstance(doc.get("document_sha256"), str) and not _SHA256.match(doc["document_sha256"]):
            errors.append(f"document {doc_id}: document_sha256 is not a SHA-256")
        if not isinstance(doc.get("content_length"), int) or doc.get("content_length", 0) <= 0:
            errors.append(f"document {doc_id}: content_length must be a positive integer")
        if doc.get("tls_verification") not in (None, "", "enabled"):
            errors.append(f"document {doc_id}: TLS verification must not be disabled")
        if doc.get("url") in old_urls:
            errors.append(f"document {doc_id}: already used by the previous corpus")
        if doc.get("split") != "final":
            errors.append(f"document {doc_id}: split must be final")

    formats = Counter(item.get("format") for item in final)
    for key, field in (("pdf_table", "min_pdf_table"), ("ocr_real", "min_ocr_real"), ("html", "min_html")):
        if formats[key] < rules[field]:
            errors.append(f"{formats[key]} final samples of format {key} < {rules[field]}")
    if formats["ocr_simulated"]:
        errors.append("simulated scans do not count and must not be in the final set")

    strategies: Counter[object] = Counter()
    for sample in final:
        annotator = sample.get("annotator")
        if not isinstance(annotator, Mapping) or annotator.get("kind") != "human" or not annotator.get("id"):
            errors.append(f"{sample.get('sample_id')}: annotator must be a human ({{id, kind: human}})")
        for zone in sample.get("zones", []):
            strategy = zone.get("scope_strategy")
            if strategy not in SCOPE_STRATEGIES:
                errors.append(f"{sample.get('sample_id')}/{zone.get('symbol')}: scope_strategy must be one of {SCOPE_STRATEGIES}")
            else:
                strategies[strategy] += 1
    for strategy in SCOPE_STRATEGIES:
        if not strategies[strategy]:
            errors.append(f"scope strategy {strategy} is not represented in the final set")

    errors.extend(_validate_second_annotation(manifest, base_dir, final, rules))
    freeze = manifest.get("freeze")
    if isinstance(freeze, Mapping):
        if freeze.get("engines_run_before_freeze") != []:
            errors.append("freeze.engines_run_before_freeze must be an empty list")
        if not freeze.get("frozen_at"):
            errors.append("freeze.frozen_at is required")
    return errors


def _validate_second_annotation(
    manifest: Mapping[str, Any], base_dir: Path, final: Sequence[Mapping[str, Any]], rules: Mapping[str, Any]
) -> list[str]:
    block = manifest.get("second_annotation")
    if not isinstance(block, Mapping):
        return ["second_annotation block is required (second annotator, files, agreement report, adjudication log)"]
    errors: list[str] = []
    annotator = block.get("annotator")
    if not isinstance(annotator, Mapping) or annotator.get("kind") != "human" or not annotator.get("id"):
        errors.append("second_annotation.annotator must be a human ({id, kind: human})")
    final_ids = {item["sample_id"] for item in final}
    covered = set(block.get("sample_ids") or [])
    if not covered <= final_ids:
        errors.append(f"second_annotation covers unknown samples: {sorted(covered - final_ids)[:3]}")
    needed = math.ceil(rules["second_annotator_share"] * len(final))
    if len(covered) < needed:
        errors.append(f"second annotator covers {len(covered)} samples < {needed} (20% of {len(final)})")
    primary_ids = {
        item["annotator"]["id"] for item in final
        if item["sample_id"] in covered and isinstance(item.get("annotator"), Mapping)
    }
    if isinstance(annotator, Mapping) and annotator.get("id") in primary_ids:
        errors.append("the second annotator must be a different person than the first")
    contents: dict[str, str | None] = {}
    for key in ("first_file", "file", "agreement_report", "adjudication_log"):
        contents[key] = None
        actual = _file_sha256(base_dir, block.get(key))
        if actual is None:
            errors.append(f"second_annotation.{key} is missing or unreadable")
        elif actual != block.get(f"{key}_sha256"):
            errors.append(f"second_annotation.{key} differs from its recorded SHA-256")
        else:
            contents[key] = (base_dir / block[key]).read_text(encoding="utf-8")
    if None in contents.values():
        return errors
    first = json.loads(contents["first_file"] or "{}").get("samples", [])
    second = json.loads(contents["file"] or "{}").get("samples", [])
    for label, items in (("first", first), ("second", second)):
        if sorted(item["sample_id"] for item in items) != sorted(covered):
            errors.append(f"the {label} independent annotation file does not contain exactly second_annotation.sample_ids")
    report = json.loads(contents["agreement_report"] or "{}")
    # the agreement is between the two independent submissions, not the adjudicated corpus
    fresh = agreement.compute_agreement(first, second, list(CATALOG))
    for key in ("pairs", "presence", "exact_required_value", "all_values", "disagreements", "samples_compared"):
        if report.get(key) != fresh[key]:
            errors.append(f"agreement report does not match the two independent annotations ({key}); recompute it")
    log = contents["adjudication_log"] or ""
    errors.extend(agreement.check_adjudication(log, fresh["disagreements"]))
    errors.extend(agreement.check_resolutions_applied(log, final, first, second))
    return errors


# --- running the parser -------------------------------------------------------


def build_extraction(loaded: Mapping[str, Any]) -> tuple[TextExtractionResult, Mapping[str, Any]]:
    """Reconstructs the frozen extraction exactly as the parser would receive it."""
    source = loaded["source"]
    pages = loaded["pages"]
    tables = [
        ExtractedTable(page_number=int(item["page_number"]), rows=item["rows"])
        for item in loaded["tables"]
    ]
    needs_ocr = bool(source.get("needs_ocr", False))
    ocr_used = bool(source.get("ocr_used", False))
    manual_review = bool(source.get("manual_review_required", needs_ocr or ocr_used))
    if ocr_used:
        warnings = ["Tekst odczytano przez OCR; wynik zachowano do ręcznej weryfikacji."]
    elif needs_ocr:
        warnings = [
            "Dokument PDF ma bardzo mało tekstu na stronę — prawdopodobnie skan "
            "wymagający OCR. Wynik ekstrakcji może być niepełny."
        ]
    else:
        warnings = []
    quality = float(source.get("quality_score", 1.0))
    extraction = TextExtractionResult(
        pages=list(pages),
        tables=tables,
        page_qualities=[quality for _ in pages],
        blocks=[[] for _ in pages],
        quality_score=quality,
        needs_ocr=needs_ocr,
        ocr_used=ocr_used,
        extraction_method=source.get("extraction_method", "ocr" if ocr_used else "pdf_text"),
        ocr_engine_version=source.get("ocr_engine_version"),
        manual_review_required=manual_review,
        warnings=warnings,
    )
    return extraction, source


def _blob(source: Mapping[str, Any]) -> DocumentBlob:
    media_type = source.get("media_type", "application/pdf")
    return DocumentBlob(
        content=b"%PDF-frozen" if media_type == "application/pdf" else b"<html>",
        media_type=media_type,
        filename=source.get("filename", "frozen"),
        source_metadata=SourceMetadata(
            source_name="MPZP_BIP",
            source_url=source.get("url"),
            confidence=0.9,
            manual_review_required=bool(source.get("needs_ocr", False)),
        ),
    )


async def run_parser(loaded: Mapping[str, Any], symbols: Sequence[str]) -> MpzpParseResult:
    extraction, source = build_extraction(loaded)
    blob = _blob(source)
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(return_value=extraction),
    ):
        return await parse_mpzp_document(blob, list(symbols))


def engine_result_from_parse(parse_result: MpzpParseResult, page_numbers: Sequence[int]) -> EngineResult:
    """Neutral engine result from the production parser output (pages → source numbers)."""
    page_of = {index + 1: number for index, number in enumerate(page_numbers)}
    zones = {
        zone.zone_symbol: [
            EngineValue(
                parameter=p.name,
                value=p.normalized_value,
                confidence=p.confidence,
                manual_review_required=p.manual_review_required,
                page=page_of.get(p.page_number or -1, p.page_number),
                source_text=p.source_text,
                raw_value=p.raw_value,
                conflict_group_id=p.conflict_group_id,
            )
            for p in zone.parameters
        ]
        for zone in parse_result.zones
    }
    return EngineResult(
        zones=zones,
        status=parse_result.status,
        warning_codes=tuple(sorted({w.code for w in parse_result.warnings})),
        usage=EngineUsage(),
    )


class LegacyEngine:
    """The production ``parse_mpzp_document`` pipeline on the frozen extraction."""

    name = "legacy"
    version = MPZP_PARSER_VERSION
    supports_discovery = True

    def run(self, loaded: Mapping[str, Any], symbols: Sequence[str]) -> EngineResult:
        return engine_result_from_parse(asyncio.run(run_parser(loaded, symbols)), loaded["page_numbers"])


register_engine(
    EngineSpec(name="legacy", description="produkcyjny parser MPZP (mpzp-parser/2.x)", factory=lambda _ctx: LegacyEngine()),
    replace=True,
)
register_engine(
    EngineSpec(
        name="v3",
        description="rdzeń deterministyczny v3 (bloki stref, resolver zakresu, leksykon ilości)",
        unavailable_reason="not implemented yet (Tasks 20.4–20.7, 20.12)",
    ),
    replace=True,
)
register_engine(
    EngineSpec(
        name="hybrid",
        description="rdzeń v3 + ekstrakcja modelem językowym z weryfikacją cytatu",
        uses_llm=True,
        unavailable_reason="not implemented yet (Tasks 20.10–20.14)",
    ),
    replace=True,
)


# --- scoring ------------------------------------------------------------------


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= VALUE_TOLERANCE


def _value_set(values: Sequence[float]) -> list[float]:
    result: list[float] = []
    for value in sorted(values):
        if not result or not _close(result[-1], value):
            result.append(value)
    return result


def _subset(left: Sequence[float], right: Sequence[float]) -> bool:
    return all(any(_close(item, other) for other in right) for item in left)


def _intersects(left: Sequence[float], right: Sequence[float]) -> bool:
    return any(_close(item, other) for item in left for other in right)


def _annotation_sets(zone: Mapping[str, Any], parameter: str) -> dict[str, list[float]]:
    sets: dict[str, list[float]] = {"required": [], "acceptable": [], "general": []}
    for item in zone.get("annotations", []):
        if item["parameter"] != parameter:
            continue
        value = float(item["normalized_value"])
        if item.get("applicability", "zone_section") == "general_clause":
            sets["general"].append(value)
        elif item.get("status", "required") == "acceptable":
            sets["acceptable"].append(value)
        else:
            sets["required"].append(value)
    return {key: _value_set(values) for key, values in sets.items()}


def classify(required: list[float], acceptable: list[float], general: list[float], returned: list[float]) -> str:
    if required:
        if not returned:
            return "fn"
        if _subset(required, returned) and _subset(returned, [*required, *acceptable]):
            return "tp_exact"
        if _intersects(returned, required):
            return "tp_partial"
        return "tp_wrong"
    if not returned:
        return "general_missed" if general else "tn"
    if _subset(returned, [*acceptable, *general]):
        return "general_found" if general and _intersects(returned, general) else "acceptable_only"
    return "fp"


def _source_assignment(assignment: str | None, outputs: Sequence[Mapping[str, Any]], required: Sequence[float]) -> str | None:
    """Zone assignment that also demands the right source for a correct value."""
    if assignment != "correct":
        return assignment
    statuses = [
        output["source_check"]["status"]
        for output in outputs
        if isinstance(output["value"], (int, float)) and any(_close(float(output["value"]), item) for item in required)
    ]
    if any(status in {"wrong_page", "wrong_block"} for status in statuses):
        return "wrong_source"
    if any(status in {"indeterminate", "unlocatable"} for status in statuses):
        return "source_unverified"
    return "correct"


def score_sample(
    sample: Mapping[str, Any],
    document: Mapping[str, Any],
    parse_result: MpzpParseResult | EngineResult,
    page_numbers: Sequence[int],
    parser_version: str,
    *,
    truth: TruthText | None = None,
    engine: str = "legacy",
    page_only: bool = False,
) -> list[dict[str, Any]]:
    """One row per (zone, catalog parameter), plus one row per returned value.

    With ``truth`` (the annotated source text) every correct value also gets a
    ``source_check``; without it the source is reported as ``not_checked``.
    """
    result = (
        parse_result
        if isinstance(parse_result, EngineResult)
        else engine_result_from_parse(parse_result, page_numbers)
    )
    zones = sample["zones"]
    locations = annotation_locations(sample, truth) if truth is not None else {}
    scored_symbols = [zone["symbol"] for zone in zones]
    rows: list[dict[str, Any]] = []
    for zone in zones:
        symbol = zone["symbol"]
        parsed_zone = result.zones.get(symbol)
        parameters = parsed_zone or []
        for parameter, info in CATALOG.items():
            sets = _annotation_sets(zone, parameter)
            found = [p for p in parameters if p.parameter == parameter]
            returned = _value_set(
                [float(p.value) for p in found if isinstance(p.value, (int, float))]
            )
            verdict = classify(sets["required"], sets["acceptable"], sets["general"], returned)
            other_required = [
                _annotation_sets(other, parameter)["required"]
                for other in zones
                if other["symbol"] != symbol
            ]
            discriminating = bool(sets["required"]) and any(
                other and (
                    not _subset(other, sets["required"]) or not _subset(sets["required"], other)
                )
                for other in other_required
            )
            assignment = None
            if returned and sets["required"]:
                if _intersects(returned, sets["required"]):
                    assignment = "correct"
                elif any(_intersects(returned, other) for other in other_required):
                    assignment = "cross_zone"
                else:
                    assignment = "value_error"
            annotation_items = [
                item for item in zone.get("annotations", []) if item["parameter"] == parameter
            ]
            allowed = [*sets["required"], *sets["acceptable"], *sets["general"]]
            outputs = []
            for p in found:
                number = float(p.value) if isinstance(p.value, (int, float)) and not isinstance(p.value, bool) else None
                matches = (
                    [loc for loc in locations.get((symbol, parameter), []) if _close(loc.value, number)]
                    if number is not None
                    else []
                )
                outputs.append(
                    {
                        "value": p.value,
                        "confidence": p.confidence,
                        "manual_review_required": p.manual_review_required,
                        "page": p.span.page if p.span is not None else p.page,
                        "source_text": p.source_text,
                        "raw_value": p.raw_value,
                        "conflict_group_id": p.conflict_group_id,
                        "review_status": p.review_status,
                        "value_correct": number is not None and any(_close(number, item) for item in allowed),
                        "source_check": check_source(p, matches, truth, page_only),
                    }
                )
            rows.append(
                {
                    "engine": engine,
                    "sample_id": sample["sample_id"],
                    "document_id": sample["document_id"],
                    "gmina": document.get("gmina"),
                    "split": sample["split"],
                    "format": sample["format"],
                    "multi_zone": bool(sample.get("multi_zone")),
                    "zone_symbol": symbol,
                    "zone_found_by_parser": parsed_zone is not None,
                    "parameter": parameter,
                    "unit": info["unit"],
                    "required_values": sets["required"],
                    "acceptable_values": sets["acceptable"],
                    "general_values": sets["general"],
                    "returned_values": returned,
                    "verdict": verdict,
                    "assignment": assignment,
                    "source_assignment": _source_assignment(assignment, outputs, sets["required"]),
                    "discriminating": discriminating,
                    "scored_zones": scored_symbols,
                    "annotation_evidence": [
                        {"page": item["page"], "evidence": item["evidence"],
                         "ambiguity": item.get("ambiguity")}
                        for item in annotation_items
                    ],
                    "parser_outputs": outputs,
                    "parser_version": parser_version,
                    "document_sha256": document.get("document_sha256"),
                    "document_url": document.get("url"),
                }
            )
    return rows


def value_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per value returned by the parser, with its correctness label."""
    result: list[dict[str, Any]] = []
    for row in rows:
        allowed = [*row["required_values"], *row["acceptable_values"], *row["general_values"]]
        for output in row["parser_outputs"]:
            value = output["value"]
            if not isinstance(value, (int, float)):
                continue
            result.append(
                {
                    "engine": row.get("engine", "legacy"),
                    "sample_id": row["sample_id"],
                    "zone_symbol": row["zone_symbol"],
                    "parameter": row["parameter"],
                    "split": row["split"],
                    "format": row["format"],
                    "gmina": row.get("gmina"),
                    "value": float(value),
                    "confidence": float(output["confidence"]),
                    "manual_review_required": bool(output["manual_review_required"]),
                    "correct": any(_close(float(value), other) for other in allowed),
                    "source_status": output["source_check"]["status"],
                    "verdict": row["verdict"],
                }
            )
    return result


# --- metrics ------------------------------------------------------------------


def wilson(successes: int, total: int) -> dict[str, float | None]:
    if total == 0:
        return {"low": None, "high": None}
    p = successes / total
    z2 = WILSON_Z**2
    centre = (p + z2 / (2 * total)) / (1 + z2 / total)
    half = WILSON_Z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / (1 + z2 / total)
    return {"low": max(0.0, centre - half), "high": min(1.0, centre + half)}


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator == 0:
        return {"value": None, "numerator": numerator, "denominator": 0,
                "ci95": wilson(0, 0), "reason": "no_eligible_cases"}
    return {"value": numerator / denominator, "numerator": numerator,
            "denominator": denominator, "ci95": wilson(numerator, denominator), "reason": None}


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def detection_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["verdict"] for row in rows)
    tp = counts["tp_exact"] + counts["tp_partial"] + counts["tp_wrong"]
    fp, fn = counts["fp"], counts["fn"]
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    exact_found = ratio(counts["tp_exact"], tp)
    end_to_end = ratio(counts["tp_exact"], tp + fn)
    assignment_pairs = [
        row for row in rows
        if row["multi_zone"] and row["discriminating"] and row["assignment"] is not None
    ]
    assignment_ok = sum(row["assignment"] == "correct" for row in assignment_pairs)
    cross = sum(row["assignment"] == "cross_zone" for row in assignment_pairs)
    source_ok = sum(row.get("source_assignment", row["assignment"]) == "correct" for row in assignment_pairs)
    strict_exact = sum(
        row["verdict"] == "tp_exact" and row.get("source_assignment", row["assignment"]) == "correct"
        for row in rows
    )
    return {
        "counts": dict(sorted(counts.items())),
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision["value"], recall["value"]),
        "exact_value_accuracy_among_found": exact_found,
        "exact_value_accuracy_end_to_end": end_to_end,
        "zone_assignment_accuracy": ratio(assignment_ok, len(assignment_pairs)),
        "cross_zone_errors": cross,
        "general_clause_found": counts["general_found"],
        "general_clause_missed": counts["general_missed"],
        **source_metrics(rows),
        "zone_assignment_source_aware": ratio(source_ok, len(assignment_pairs)),
        "strict_end_to_end": ratio(strict_exact, tp + fn),
        "wrong_source_errors": sum(row.get("source_assignment") == "wrong_source" for row in rows),
    }


def source_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``source_consistent``: correct returned values that come from the annotated source.

    Denominator: every returned value that equals an annotated value of its zone
    (required, acceptable or general) and could be checked. A value whose source
    cannot be confirmed (``indeterminate``, ``unlocatable``) is not counted as consistent.
    """
    statuses: Counter[str] = Counter()
    required_statuses: Counter[str] = Counter()
    bases: Counter[str] = Counter()
    for row in rows:
        for output in row["parser_outputs"]:
            if not output.get("value_correct"):
                continue
            check = output["source_check"]
            statuses[check["status"]] += 1
            if any(_close(float(output["value"]), item) for item in row["required_values"]):
                required_statuses[check["status"]] += 1
            if check.get("basis"):
                bases[check["basis"]] += 1
    checked = sum(count for status, count in statuses.items() if status != "not_checked")
    metric = ratio(statuses["consistent"], checked)
    if checked == 0:
        metric["reason"] = "no_source_checks"
    return {
        "source_consistent": metric,
        "source_status_counts": dict(sorted(statuses.items())),
        "source_status_counts_required_values": dict(sorted(required_statuses.items())),
        "source_basis_counts": dict(sorted(bases.items())),
    }


def _fisher_upper(a: int, b: int, c: int, d: int) -> float | None:
    """One-sided Fisher exact p for more errors in group 1 than in group 2."""
    n1, n2, errors = a + b, c + d, a + c
    if n1 == 0 or n2 == 0:
        return None
    total = n1 + n2
    denominator = math.comb(total, errors)
    p = 0.0
    for k in range(a, min(n1, errors) + 1):
        if errors - k <= n2:
            p += math.comb(n1, k) * math.comb(n2, errors - k) / denominator
    return p


def calibration_metrics(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Does lower confidence really mean a higher error rate?"""
    def bucket(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        wrong = sum(not row["correct"] for row in rows)
        rate = ratio(wrong, len(rows))
        return {"n": len(rows), "errors": wrong, "error_rate": rate["value"], "ci95": rate["ci95"],
                "mean_confidence": (sum(r["confidence"] for r in rows) / len(rows)) if rows else None}

    by_confidence: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in values:
        by_confidence[f"{row['confidence']:.2f}"].append(row)
    bands = {
        name: bucket([row for row in values if low <= row["confidence"] < high])
        for name, low, high in CONFIDENCE_BANDS
    }
    n = len(values)
    brier = (
        sum((row["confidence"] - (1.0 if row["correct"] else 0.0)) ** 2 for row in values) / n
        if n else None
    )
    ece = 0.0
    for info in bands.values():
        if info["n"]:
            accuracy = 1 - info["error_rate"]
            ece += info["n"] / n * abs(accuracy - info["mean_confidence"])
    low, high = bands["low"], bands["high"]
    comparison = None
    if low["n"] and high["n"]:
        comparison = {
            "low_error_rate": low["error_rate"],
            "high_error_rate": high["error_rate"],
            "low_more_error_prone": low["error_rate"] > high["error_rate"],
            "fisher_one_sided_p": _fisher_upper(
                low["errors"], low["n"] - low["errors"], high["errors"], high["n"] - high["errors"]
            ),
        }
    flagged = [row for row in values if row["manual_review_required"]]
    wrong_rows = [row for row in values if not row["correct"]]
    return {
        "n_values": n,
        "by_confidence_value": {key: bucket(rows) for key, rows in sorted(by_confidence.items())},
        "by_band": bands,
        "brier_score": brier,
        "expected_calibration_error": ece if n else None,
        "low_vs_high": comparison,
        "manual_review_flag": {
            "flagged": len(flagged),
            "errors_flagged": ratio(sum(not r["correct"] for r in flagged), len(flagged)),
            "error_recall_of_flag": ratio(sum(r["manual_review_required"] for r in wrong_rows), len(wrong_rows)),
        },
    }


def slice_metrics(rows: Sequence[Mapping[str, Any]], values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"detection": detection_metrics(rows), "calibration": calibration_metrics(values)}


def build_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = value_rows(rows)
    result: dict[str, Any] = {"overall": slice_metrics(rows, values)}
    for dimension in ("split", "format"):
        result[f"by_{dimension}"] = {
            key: slice_metrics(
                [r for r in rows if r[dimension] == key], [v for v in values if v[dimension] == key]
            )
            for key in sorted({r[dimension] for r in rows})
        }
    result["by_gmina"] = {
        str(key): slice_metrics(
            [r for r in rows if r.get("gmina") == key], [v for v in values if v.get("gmina") == key]
        )
        for key in sorted({r.get("gmina") for r in rows}, key=str)
    }
    result["by_multi_zone"] = {
        str(flag).lower(): slice_metrics(
            [r for r in rows if r["multi_zone"] == flag],
            [v for v in values if any(
                r["sample_id"] == v["sample_id"] and r["multi_zone"] == flag for r in rows
            )],
        )
        for flag in (True, False)
    }
    result["by_parameter"] = {
        name: detection_metrics([r for r in rows if r["parameter"] == name]) for name in CATALOG
    }
    result["by_split_and_format"] = {
        f"{split}/{fmt}": slice_metrics(
            [r for r in rows if r["split"] == split and r["format"] == fmt],
            [v for v in values if v["split"] == split and v["format"] == fmt],
        )
        for split, fmt in sorted({(r["split"], r["format"]) for r in rows})
    }
    return result


# --- evaluation run -----------------------------------------------------------


def _check_contract(engine: str, result: EngineResult) -> None:
    """A model-derived value is a candidate for manual review, never a verified one."""
    for symbol, values in result.zones.items():
        for value in values:
            if value.review_status in FORBIDDEN_REVIEW_STATUSES:
                raise EvaluationError(
                    f"engine {engine!r} returned {value.parameter} for {symbol!r} as {value.review_status!r}; "
                    "an automatic reading may only be a candidate for manual review"
                )


def _usage_record(usage: EngineUsage) -> dict[str, Any]:
    return {
        "calls": usage.calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": usage.latency_ms,
        "cost_usd": usage.cost_usd,
    }


def _rejection_records(
    sample: Mapping[str, Any], sample_rows: Sequence[Mapping[str, Any]], rejections: Sequence[Any]
) -> list[dict[str, Any]]:
    by_key = {(row["zone_symbol"], row["parameter"]): row for row in sample_rows}
    records = []
    for item in rejections:
        row = by_key.get((item.zone_symbol, item.parameter))
        correct: bool | None = None
        if row is not None and isinstance(item.value, (int, float)):
            allowed = [*row["required_values"], *row["acceptable_values"], *row["general_values"]]
            correct = any(_close(float(item.value), other) for other in allowed)
        records.append(
            {
                "sample_id": sample["sample_id"],
                "document_id": sample["document_id"],
                "gmina": row.get("gmina") if row else None,
                "split": sample["split"],
                "format": sample["format"],
                "zone_symbol": item.zone_symbol,
                "parameter": item.parameter,
                "gate": item.gate,
                "value": item.value,
                "reason": item.reason,
                "value_was_correct": correct,
            }
        )
    return records


def evaluate(
    manifest: Mapping[str, Any],
    base_dir: Path,
    parser_version: str | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Runs one engine over the corpus.

    The result separates **substance** (``rows``, ``metrics``, ``rejection_records``;
    identical on every repeat of the same manifest and engine) from **observations**
    (wall time, model latency, tokens, cost), which never enter the determinism digest.
    """
    engine = engine or LegacyEngine()
    version = parser_version or engine.version
    loaded = {doc_id: load_document(base_dir, doc) for doc_id, doc in manifest["documents"].items()}
    truths = {doc_id: TruthText(item["pages"], item["page_numbers"]) for doc_id, item in loaded.items()}
    rows: list[dict[str, Any]] = []
    discovery: list[dict[str, Any]] = []
    rejection_records: list[dict[str, Any]] = []
    per_sample: list[dict[str, Any]] = []
    for sample in manifest["samples"]:
        doc_id = sample["document_id"]
        truth_id = sample.get("truth_document_id") or doc_id
        symbols = [zone["symbol"] for zone in sample["zones"]]
        started = time.perf_counter()
        result = engine.run(loaded[doc_id], symbols)
        wall_ms = (time.perf_counter() - started) * 1000
        _check_contract(engine.name, result)
        document = {**manifest["documents"][doc_id], "document_sha256": loaded[doc_id]["source"].get("document_sha256")}
        sample_rows = score_sample(
            sample, document, result, loaded[doc_id]["page_numbers"], version,
            truth=truths[truth_id], engine=engine.name, page_only=truth_id != doc_id,
        )
        rows.extend(sample_rows)
        rejection_records.extend(_rejection_records(sample, sample_rows, result.rejections))
        observation = {
            "sample_id": sample["sample_id"],
            "document_id": doc_id,
            "zones": len(symbols),
            "wall_ms": round(wall_ms, 3),
            **_usage_record(result.usage),
            "discovery": None,
        }
        if engine.supports_discovery:
            started = time.perf_counter()
            discovered = engine.run(loaded[doc_id], [])
            observation["discovery"] = {
                "wall_ms": round((time.perf_counter() - started) * 1000, 3), **_usage_record(discovered.usage)
            }
            _check_contract(engine.name, discovered)
            found_symbols = set(discovered.zones)
            discovery.append(
                {
                    "sample_id": sample["sample_id"],
                    "annotated_symbols": symbols,
                    "discovered_symbols": sorted(found_symbols),
                    "recalled": [s for s in symbols if s in found_symbols],
                    "status_with_symbols": result.status,
                    "warning_codes": sorted(result.warning_codes),
                }
            )
        per_sample.append(observation)
    rows.sort(key=lambda r: (r["sample_id"], r["zone_symbol"], r["parameter"]))
    rejection_records.sort(key=lambda r: (r["sample_id"], r["zone_symbol"], str(r["parameter"]), r["gate"], str(r["value"])))
    metrics = build_metrics(rows)
    metrics["rejections"] = {
        "total": len(rejection_records),
        "by_gate": compare.rejection_gate_table(rejection_records),
    }
    if engine.supports_discovery:
        recalled = sum(len(item["recalled"]) for item in discovery)
        annotated = sum(len(item["annotated_symbols"]) for item in discovery)
        discovery_block: dict[str, Any] = {"recall": ratio(recalled, annotated), "per_sample": discovery}
    else:
        discovery_block = {
            "recall": {"value": None, "numerator": 0, "denominator": 0, "ci95": wilson(0, 0),
                       "reason": "engine_does_not_discover_zones"},
            "per_sample": [],
        }
    return {
        "engine": engine.name,
        "rows": rows,
        "values": value_rows(rows),
        "metrics": metrics,
        "zone_symbol_discovery": discovery_block,
        "rejection_records": rejection_records,
        "parser_version": version,
        "observations": {"engine": engine.name, "version": version, "per_sample": per_sample},
    }


# The study separates recognition (was a value found at all), normalization or
# value (was the found value right) and assignment (was it given to the right zone).
ERROR_CATEGORY = {
    "detection_fn": "recognition",
    "detection_fp": "recognition",
    "zone_not_found": "recognition",
    "value_error": "normalization_or_value",
    "zone_assignment": "assignment",
    "wrong_source": "assignment",
}
HINT_PRIORITY = (
    "implicit_percent",
    "number_word",
    "extraction_artifact",
    "building_line_not_setback_phrase",
    "residual_clause",
    "shared_section",
    "conditional_value",
    "general_clause",
)


def cause_hint(row: Mapping[str, Any]) -> str:
    """Cause candidate derived from the annotation, not proven by parser internals."""
    verdict = row["verdict"]
    kinds = {
        item["ambiguity"]["kind"]
        for item in row["annotation_evidence"]
        if isinstance(item.get("ambiguity"), Mapping)
    }
    if verdict in {"fn", "tp_partial"}:
        for kind in HINT_PRIORITY:
            if kind in kinds:
                return kind
    if verdict == "fp":
        return "value_without_annotation"
    if verdict in {"tp_wrong", "tp_partial"}:
        allowed = [*row["required_values"], *row["acceptable_values"], *row["general_values"]]
        extras = [v for v in row["returned_values"] if not any(_close(v, other) for other in allowed)]
        return "value_from_other_context" if extras else "missing_conditional_value"
    if row["format"].startswith("ocr"):
        return "ocr_noise_or_wording"
    return "wording_not_matched"


def build_error_trace(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every wrong outcome with a pointer to document, page and parser version.

    A correct value taken from another page or zone block (``wrong_source``) is an
    assignment error even though the value matches the annotation.
    """
    errors = []
    for row in rows:
        wrong_source = row.get("source_assignment") == "wrong_source"
        if row["verdict"] not in {"fn", "fp", "tp_wrong", "tp_partial"} and not wrong_source:
            continue
        if row["assignment"] == "cross_zone":
            error_type = "zone_assignment"
        elif wrong_source:
            error_type = "wrong_source"
        elif row["verdict"] == "fn":
            error_type = "detection_fn" if row["zone_found_by_parser"] else "zone_not_found"
        elif row["verdict"] == "fp":
            error_type = "detection_fp"
        else:
            error_type = "value_error"
        errors.append(
            {
                "error_id": "",
                "engine": row.get("engine", "legacy"),
                "error_type": error_type,
                "category": ERROR_CATEGORY[error_type],
                "cause_hint": "value_from_other_source" if error_type == "wrong_source" else cause_hint(row),
                "sample_id": row["sample_id"],
                "split": row["split"],
                "format": row["format"],
                "document_id": row["document_id"],
                "document_url": row["document_url"],
                "document_sha256": row["document_sha256"],
                "zone_symbol": row["zone_symbol"],
                "parameter": row["parameter"],
                "verdict": row["verdict"],
                "required_values": row["required_values"],
                "acceptable_values": row["acceptable_values"],
                "returned_values": row["returned_values"],
                "source_checks": [
                    {"value": output["value"], **output["source_check"]}
                    for output in row["parser_outputs"]
                    if output["source_check"]["status"] not in {"not_applicable", "not_checked"}
                ],
                "annotation_evidence": row["annotation_evidence"],
                "parser_outputs": row["parser_outputs"],
                "parser_version": row["parser_version"],
            }
        )
    for number, item in enumerate(errors, start=1):
        item["error_id"] = f"P-{number:03d}"
    return errors


def _git_commit_sha(repo_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_run_manifest(
    manifest: Mapping[str, Any],
    corpus_sha256: str,
    evaluator_sha256: str,
    *,
    engine: str = "legacy",
    engine_version: str | None = None,
    run_mode: str = "offline",
    llm: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    import shapely  # noqa: F401  (recorded for the environment block)

    version = engine_version or MPZP_PARSER_VERSION
    frozen = {
        "schema_version": SCHEMA_VERSION,
        "commit_sha": _git_commit_sha(REPO_ROOT),
        "corpus_id": manifest.get("corpus_id"),
        "corpus_sha256": corpus_sha256,
        "annotations_sha256": annotations_sha256(manifest),
        "freeze": manifest.get("freeze"),
        "engine": engine,
        "engine_version": version,
        "parser_version": version,
        "evaluator_sha256": evaluator_sha256,
        "parameters": {
            "mode": run_mode,
            "catalog": CATALOG,
            "value_tolerance": VALUE_TOLERANCE,
            "confidence_bands": [list(band[:2]) for band in CONFIDENCE_BANDS],
            "parser_called_with": "annotated zone symbols of the sample",
            "source_block_tolerance_chars": BLOCK_TOLERANCE,
        },
        "llm": dict(llm) if llm else None,
        "environment": {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
        },
    }
    frozen["manifest_sha256"] = sha256_bytes(canonical_json(frozen).encode("utf-8"))
    return frozen


# --- reports ------------------------------------------------------------------


def _cell(metric: Mapping[str, Any]) -> str:
    if metric["value"] is None:
        return f"null ({metric['reason']})"
    ci = metric["ci95"]
    interval = f" [{ci['low']:.2f}; {ci['high']:.2f}]" if ci["low"] is not None else ""
    return f"{metric['value']:.3f} ({metric['numerator']}/{metric['denominator']}){interval}"


def _detection_table(title: str, sliced: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = [
        f"### {title}", "",
        "| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |",
        "|---|---|---|---|---|---|---:|---|---|",
    ]
    for key, item in sliced.items():
        d = item["detection"] if "detection" in item else item
        lines.append(
            f"| `{key}` | {_cell(d['precision'])} | {_cell(d['recall'])} | "
            f"{_cell(d['exact_value_accuracy_among_found'])} | {_cell(d['exact_value_accuracy_end_to_end'])} | "
            f"{_cell(d['zone_assignment_accuracy'])} | {d['cross_zone_errors']} | "
            f"{_cell(d['source_consistent'])} | {_cell(d['strict_end_to_end'])} |"
        )
    lines.append("")
    return lines


def _calibration_lines(calibration: Mapping[str, Any]) -> list[str]:
    lines = [
        "| Przedział confidence | n | błędne | odsetek błędów | 95% CI | średnie confidence |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for name, info in calibration["by_band"].items():
        rate = "n/a" if info["error_rate"] is None else f"{info['error_rate']:.3f}"
        ci = info["ci95"]
        interval = "n/a" if ci["low"] is None else f"[{ci['low']:.2f}; {ci['high']:.2f}]"
        mean = "n/a" if info["mean_confidence"] is None else f"{info['mean_confidence']:.2f}"
        lines.append(f"| `{name}` | {info['n']} | {info['errors']} | {rate} | {interval} | {mean} |")
    lines.append("")
    lines.append("| Wartość confidence | n | błędne | odsetek błędów |")
    lines.append("|---:|---:|---:|---:|")
    for key, info in calibration["by_confidence_value"].items():
        lines.append(f"| {key} | {info['n']} | {info['errors']} | {info['error_rate']:.3f} |")
    lines.append("")
    return lines


def render_report(
    run_manifest: Mapping[str, Any], manifest: Mapping[str, Any], result: Mapping[str, Any], errors: Sequence[Mapping[str, Any]]
) -> str:
    metrics = result["metrics"]
    overall = metrics["overall"]
    samples = manifest["samples"]
    formats = Counter(sample["format"] for sample in samples)
    splits = Counter(sample["split"] for sample in samples)
    lines = [
        f"# Ewaluacja silnika `{run_manifest['engine']}` parsera MPZP (BK-603, PV3-03)", "",
        "Plik jest generowany z `parameter_results.json`; nie jest edytowany ręcznie.", "",
        "## Zamrożony manifest", "",
        "| Pole | Wartość |", "|---|---|",
        f"| `engine` / `engine_version` | `{run_manifest['engine']}` / `{run_manifest['engine_version']}` |",
        f"| `run_mode` | `{run_manifest['parameters']['mode']}` |",
        f"| `commit_sha` | `{run_manifest['commit_sha']}` |",
        f"| `manifest_sha256` | `{run_manifest['manifest_sha256']}` |",
        f"| `corpus_sha256` | `{run_manifest['corpus_sha256']}` |",
        f"| `annotations_sha256` | `{run_manifest['annotations_sha256']}` |",
        f"| `freeze` | `{canonical_json(run_manifest['freeze'])}` |",
        *([f"| `llm` | `{canonical_json(run_manifest['llm'])}` |"] if run_manifest.get("llm") else []), "",
        "## Korpus", "",
        f"- próbek: {len(samples)} (rozwojowe: {splits['development']}, końcowe: {splits['final']});",
        f"- dokumentów: {len(manifest['documents'])} z {len({d['gmina'] for d in manifest['documents'].values()})} gmin;",
        f"- formaty próbek: `{canonical_json(dict(sorted(formats.items())))}`;",
        f"- próbek wielostrefowych: {sum(bool(s['multi_zone']) for s in samples)};",
        f"- zonów z adnotacją: {sum(len(s['zones']) for s in samples)}; "
        f"adnotacji parametrów: {sum(len(z['annotations']) for s in samples for z in s['zones'])}.", "",
        "Jednostką detekcji jest para (strefa, parametr katalogu). `fp` to wartość zwrócona dla "
        "pary, której dokument nie zawiera; wartość akceptowalna (`acceptable`) nie jest błędem. "
        "Ustalenia z klauzul ogólnych (`general_clause`) raportowane są osobno i nie wchodzą do "
        "recall, bo parser przypisuje wartości z sekcji strefy.", "",
        "## Wyniki łączne", "",
    ]
    lines.extend(_detection_table("Łącznie", {"overall": overall}))
    lines.extend(_detection_table("Według podziału (rozwojowy / końcowy)", metrics["by_split"]))
    lines.extend(_detection_table("Według formatu", metrics["by_format"]))
    lines.extend(_detection_table("Podział × format", metrics["by_split_and_format"]))
    lines.extend(_detection_table("Według gminy", metrics["by_gmina"]))
    lines.extend(_detection_table("Wielostrefowe vs jednostrefowe", metrics["by_multi_zone"]))
    lines.extend(_detection_table("Według parametru", metrics["by_parameter"]))
    lines.extend(
        ["### Liczniki werdyktów", "", "| Wycinek | `" + "` | `".join(
            ("tp_exact", "tp_partial", "tp_wrong", "fn", "fp", "tn", "acceptable_only", "general_found", "general_missed")
        ) + "` |", "|---|" + "---:|" * 9]
    )
    for key, item in {"overall": overall, **metrics["by_split"], **metrics["by_format"]}.items():
        counts = item["detection"]["counts"]
        lines.append(
            f"| `{key}` | "
            + " | ".join(str(counts.get(name, 0)) for name in (
                "tp_exact", "tp_partial", "tp_wrong", "fn", "fp", "tn",
                "acceptable_only", "general_found", "general_missed"))
            + " |"
        )
    discovery = result["zone_symbol_discovery"]["recall"]
    lines.extend(["", f"Odkrywanie symboli bez podpowiedzi (recall symboli z anotacji): {_cell(discovery)}.", ""])
    lines.extend(_source_section(overall["detection"]))
    lines.extend(["## Kalibracja confidence", ""])
    lines.extend(
        [
            f"Wartości zwróconych parametrów w strefach z adnotacją: n = {overall['calibration']['n_values']}; "
            f"Brier = {overall['calibration']['brier_score']:.3f}; "
            f"ECE = {overall['calibration']['expected_calibration_error']:.3f}.", "",
        ]
        if overall["calibration"]["n_values"]
        else ["Brak zwróconych wartości."]
    )
    lines.extend(_calibration_lines(overall["calibration"]))
    comparison = overall["calibration"]["low_vs_high"]
    if comparison:
        p_value = comparison["fisher_one_sided_p"]
        lines.append(
            f"Niskie vs wysokie confidence: odsetek błędów {comparison['low_error_rate']:.3f} vs "
            f"{comparison['high_error_rate']:.3f}; niskie częściej błędne: "
            f"**{'tak' if comparison['low_more_error_prone'] else 'nie'}**; "
            f"test Fishera (jednostronny) p = {p_value:.4f}."
            if p_value is not None else "Niskie vs wysokie: brak danych do testu."
        )
    else:
        lines.append("Niskie vs wysokie confidence: co najmniej jeden przedział jest pusty, porównanie niemożliwe.")
    flag = overall["calibration"]["manual_review_flag"]
    lines.extend(
        [
            "",
            f"Flaga `manual_review_required`: oznaczono {flag['flagged']} wartości; błędnych wśród oznaczonych: "
            f"{_cell(flag['errors_flagged'])}; odsetek błędów wychwyconych flagą: {_cell(flag['error_recall_of_flag'])}.", "",
            "Kalibracje dla wycinków (split, format) są w `metrics.json`; małe n oznacza szerokie przedziały.", "",
            "## Błędy", "",
            f"Wpisów w śladzie błędów: {len(errors)} (`errors.json`, `errors.csv`). Każdy ma dokument, SHA-256, "
            "stronę anotacji, wynik parsera i wersję parsera.", "",
            "| Typ | liczba |", "|---|---:|",
        ]
    )
    for kind, count in sorted(Counter(item["error_type"] for item in errors).items()):
        lines.append(f"| `{kind}` | {count} |")
    lines.extend(
        [
            "",
            "Rozdzielenie błędów: **rozpoznanie** (`detection_fn`, `detection_fp`, `zone_not_found`: czy wartość "
            "została znaleziona), **normalizacja/wartość** (`value_error`: wartość znaleziona, ale inna niż w "
            "adnotacji lub z dodatkową wartością) i **przypisanie** (`zone_assignment`: wartość innej strefy).", "",
            "| Kategoria | " + " | ".join(("pdf_text", "pdf_table", "html", "ocr_real", "ocr_simulated")) + " | razem |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for category in ("recognition", "normalization_or_value", "assignment"):
        counts = Counter(item["format"] for item in errors if item["category"] == category)
        lines.append(
            f"| `{category}` | "
            + " | ".join(str(counts.get(fmt, 0)) for fmt in ("pdf_text", "pdf_table", "html", "ocr_real", "ocr_simulated"))
            + f" | {sum(counts.values())} |"
        )
    lines.extend(
        [
            "", "Wskazówki przyczyn (wyprowadzone z oznaczeń niejednoznaczności w adnotacji; nie są dowodem "
            "przyczyny w kodzie parsera):", "", "| Wskazówka | liczba |", "|---|---:|",
        ]
    )
    for hint, count in sorted(Counter(item["cause_hint"] for item in errors).items(), key=lambda pair: -pair[1]):
        lines.append(f"| `{hint}` | {count} |")
    lines.append("")
    lines.extend(_rejection_section(metrics["rejections"]))
    lines.extend(_observation_section(result["observations"]))
    return "\n".join(lines)


def _source_section(detection: Mapping[str, Any]) -> list[str]:
    counts = detection["source_status_counts"]
    required = detection["source_status_counts_required_values"]
    lines = [
        "## Źródło wartości (`source_consistent`)", "",
        "Wartość zwrócona przez silnik jest *poprawna co do wartości*, gdy równa się wartości z adnotacji tej strefy. "
        "Jest *spójna ze źródłem*, gdy jej strona jest stroną cytatu adnotacji, a miejsce dopasowania leży w bloku "
        "strefy (od kotwicy do ostatniego cytatu tej kotwicy plus 300 znaków, nie dalej niż następna kotwica). "
        "Poprawna wartość z innej strony lub z bloku innej strefy jest **błędem przypisania, nie trafieniem**. "
        "Wartości, których źródła nie da się potwierdzić (`indeterminate`: identyczny tekst także poza blokiem; "
        "`unlocatable`: brak strony lub tekstu), nie są liczone jako spójne.", "",
        f"`source_consistent` = {_cell(detection['source_consistent'])}; "
        f"przypisanie do strefy z uwzględnieniem źródła = {_cell(detection['zone_assignment_source_aware'])} "
        f"(wg samej wartości: {_cell(detection['zone_assignment_accuracy'])}); "
        f"dokładność end-to-end ze źródłem = {_cell(detection['strict_end_to_end'])} "
        f"(wg samej wartości: {_cell(detection['exact_value_accuracy_end_to_end'])}); "
        f"pary z poprawną wartością z niewłaściwego źródła: {detection['wrong_source_errors']}.", "",
        "| Status źródła | wszystkie poprawne wartości | w tym równe wartości wymaganej |", "|---|---:|---:|",
    ]
    for status in ("consistent", "wrong_page", "wrong_block", "indeterminate", "unlocatable", "not_checked"):
        lines.append(f"| `{status}` | {counts.get(status, 0)} | {required.get(status, 0)} |")
    lines.append(
        f"| razem | {sum(counts.values())} | {sum(required.values())} |"
    )
    lines.extend(
        [
            "", f"Podstawa porównania: `{canonical_json(detection['source_basis_counts'])}` "
            "(`block` = kotwica strefy, `evidence` = tylko cytat adnotacji bez kotwicy, `page` = tylko strona, gdy tekst "
            "anotowany nie jest tekstem odczytanym przez silnik, np. skan symulowany).", "",
        ]
    )
    return lines


def _rejection_section(rejections: Mapping[str, Any]) -> list[str]:
    lines = ["## Bramki odrzuceń kandydatów", ""]
    if not rejections["by_gate"]:
        return [*lines, "Silnik nie zgłosił odrzuceń (nie ma bramek albo żaden kandydat nie został odrzucony).", ""]
    lines.extend(["| Bramka | odrzucone | wartość była poprawna | wartość była błędna | bez wartości liczbowej |", "|---|---:|---:|---:|---:|"])
    for gate, counts in rejections["by_gate"].items():
        lines.append(
            f"| `{gate}` | {counts.get('rejected', 0)} | {counts.get('value_correct', 0)} | "
            f"{counts.get('value_wrong', 0)} | {counts.get('value_unknown', 0)} |"
        )
    lines.append("")
    return lines


def _observation_section(observations: Mapping[str, Any]) -> list[str]:
    summary = compare.summarize_observations(observations)
    fmt = compare._fmt
    return [
        "## Koszt i opóźnienie (obserwacja, nie wynik merytoryczny)", "",
        "Czas ścienny, opóźnienie modelu, tokeny i koszt zależą od środowiska i biegu i są poza skrótem determinizmu. "
        "`—` oznacza, że silnik tego nie raportuje (nie zero). W trybie odtwarzania wartości modelu pochodzą z nagrania.", "",
        "| Wielkość | Wartość |", "|---|---:|",
        f"| próbek / dokumentów / stref | {summary['samples']} / {summary['documents']} / {summary['zones']} |",
        f"| wywołania modelu | {fmt(summary['calls'])} |",
        f"| tokeny wejściowe / wyjściowe | {fmt(summary['input_tokens'])} / {fmt(summary['output_tokens'])} |",
        f"| czas ścienny na próbkę p50 / p95 [ms] | {fmt(summary['wall_ms_p50'], 1)} / {fmt(summary['wall_ms_p95'], 1)} |",
        f"| opóźnienie modelu p50 / p95 [ms] | {fmt(summary['model_latency_ms_p50'], 1)} / {fmt(summary['model_latency_ms_p95'], 1)} |",
        f"| koszt łącznie [USD] | {fmt(summary['cost_usd_total'], 4)} |",
        f"| koszt na dokument / strefę [USD] | {fmt(summary['cost_usd_per_document'], 5)} / {fmt(summary['cost_usd_per_zone'], 5)} |",
        "",
    ]


def _svg(result: Mapping[str, Any]) -> str:
    bands = result["metrics"]["overall"]["calibration"]["by_band"]
    body = []
    for index, (name, info) in enumerate(bands.items()):
        rate = info["error_rate"] or 0.0
        y = 60 + index * 40
        body.append(f'<text x="10" y="{y}" font-size="13">{html.escape(name)} (n={info["n"]})</text>')
        body.append(f'<rect x="170" y="{y - 14}" width="{300 * rate:.3f}" height="18" fill="#c0392b"/>')
        body.append(f'<text x="480" y="{y}" font-size="13">{rate:.1%}</text>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="560" height="200" viewBox="0 0 560 200">'
        '<rect width="100%" height="100%" fill="white"/>'
        '<text x="10" y="24" font-size="15" font-weight="bold">Odsetek błędnych wartości według confidence</text>'
        + "".join(body) + "</svg>\n"
    )


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: canonical_json(value) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })


def _dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_outputs(
    output_dir: Path, run_manifest: Mapping[str, Any], manifest: Mapping[str, Any],
    result: Mapping[str, Any], errors: Sequence[Mapping[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = run_manifest["manifest_sha256"]
    _dump(output_dir / "run_manifest.json", run_manifest)
    _dump(output_dir / "parameter_results.json", {"manifest_sha256": digest, "rows": result["rows"]})
    _dump(output_dir / "metrics.json", {"manifest_sha256": digest, **result["metrics"],
                                          "zone_symbol_discovery": result["zone_symbol_discovery"]})
    _dump(output_dir / "errors.json", {"manifest_sha256": digest, "errors": list(errors)})
    # time, tokens and cost are observations: kept apart from every substantive file
    _dump(
        output_dir / "observations.json",
        {
            "manifest_sha256": digest,
            "note": "observations only; excluded from the determinism digest",
            "summary": compare.summarize_observations(result["observations"]),
            **result["observations"],
        },
    )
    _write_csv(
        output_dir / "parameter_results.csv",
        ("engine", "sample_id", "document_id", "gmina", "split", "format", "multi_zone", "zone_symbol", "parameter",
         "verdict", "assignment", "source_assignment", "discriminating", "required_values", "acceptable_values",
         "general_values", "returned_values", "parser_version", "document_sha256", "document_url"),
        result["rows"],
    )
    _write_csv(
        output_dir / "values.csv",
        ("engine", "sample_id", "zone_symbol", "parameter", "split", "format", "gmina", "value", "confidence",
         "manual_review_required", "correct", "source_status", "verdict"),
        result["values"],
    )
    _write_csv(
        output_dir / "errors.csv",
        ("error_id", "engine", "error_type", "category", "cause_hint", "sample_id", "split", "format", "document_id",
         "document_url", "document_sha256", "zone_symbol", "parameter", "verdict", "required_values",
         "acceptable_values", "returned_values", "source_checks", "parser_version"),
        errors,
    )
    _write_csv(
        output_dir / "rejections.csv",
        ("sample_id", "document_id", "gmina", "split", "format", "zone_symbol", "parameter", "gate", "value",
         "reason", "value_was_correct"),
        result["rejection_records"],
    )
    (output_dir / "report.md").write_text(render_report(run_manifest, manifest, result, errors), encoding="utf-8")
    (output_dir / "calibration.svg").write_text(_svg(result), encoding="utf-8")


def write_comparison(
    output_dir: Path, comparison: Mapping[str, Any], observations: Mapping[str, Mapping[str, Any]],
    run_info: Mapping[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _dump(output_dir / "comparison.json", {**run_info, **comparison})
    _dump(output_dir / "observations.json", {
        "note": "observations only; excluded from the determinism digest",
        "summary": compare.build_observation_summary({n: {"observations": o} for n, o in observations.items()}),
    })
    flat = []
    for name, paired in comparison["paired"].items():
        for outcome in compare.OUTCOMES:
            slices = [("overall", "overall", paired["overall"][outcome])]
            for dimension in ("format", "gmina", "split"):
                slices.extend((dimension, value, item[outcome]) for value, item in paired[f"by_{dimension}"].items())
            for dimension, value, stats in slices:
                flat.append({
                    "baseline": comparison["baseline"], "candidate": name, "outcome": outcome,
                    "dimension": dimension, "slice": value, "n_pairs": stats["n_pairs"], "n_samples": stats["n_samples"],
                    "baseline_successes": stats["baseline_successes"], "candidate_successes": stats["candidate_successes"],
                    "difference": stats["difference"], "ci_low": stats["bootstrap_ci95"]["low"],
                    "ci_high": stats["bootstrap_ci95"]["high"], "only_baseline": stats["only_baseline"],
                    "only_candidate": stats["only_candidate"], "mcnemar_exact_p": stats["mcnemar_exact_p"],
                })
    _write_csv(
        output_dir / "comparison.csv",
        ("baseline", "candidate", "outcome", "dimension", "slice", "n_pairs", "n_samples", "baseline_successes",
         "candidate_successes", "difference", "ci_low", "ci_high", "only_baseline", "only_candidate", "mcnemar_exact_p"),
        flat,
    )
    (output_dir / "comparison.md").write_text(compare.render_comparison_report(comparison, observations, run_info), encoding="utf-8")
    (output_dir / "comparison.svg").write_text(compare.comparison_svg(comparison), encoding="utf-8")


class UsageError(ValueError):
    """The command line asks for something that cannot or must not run."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate MPZP extraction engines on the frozen annotated corpus.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="results root; every engine writes to <output-dir>/<engine>/, a comparison to <output-dir>/comparison/",
    )
    parser.add_argument("--mode", choices=("offline",), default="offline")
    parser.add_argument(
        "--engine", nargs="+", default=["legacy"], metavar="NAME",
        help="engines to run (registered: legacy, v3, hybrid); several engines are compared on identical pairs",
    )
    parser.add_argument("--baseline", help="engine the others are compared with (default: the first one)")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--llm-replay", type=Path, metavar="DIR",
                        help="directory of recorded model responses, read offline by cache key")
    parser.add_argument("--live", action="store_true",
                        help="call the model for real and record responses in --llm-replay DIR; needs "
                             f"{LLM_API_KEY_ENV} in the environment, never runs in CI")
    parser.add_argument(
        "--validate-only", action="store_true",
        help="validate the corpus (structure, quotes, freeze, optional --profile) and exit; runs no engine",
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), help="additional corpus profile to check (with --validate-only)")
    parser.add_argument("--bootstrap-resamples", type=int, default=compare.DEFAULT_RESAMPLES)
    parser.add_argument("--seed", type=int, default=compare.DEFAULT_SEED)
    return parser


def _resolve_engines(args: argparse.Namespace) -> tuple[list[str], Any]:
    names = list(dict.fromkeys(args.engine))
    specs = []
    for name in names:
        try:
            spec = get_engine_spec(name)
        except EngineUnavailableError as exc:
            raise UsageError(str(exc)) from exc
        if spec.factory is None:
            raise UsageError(f"engine {name!r} is not available: {spec.unavailable_reason or 'no implementation'}")
        specs.append(spec)
    needs_model = any(spec.uses_llm for spec in specs)
    if (args.llm_replay is not None or args.live) and not needs_model:
        raise UsageError("--llm-replay/--live apply only to engines that use a model (e.g. hybrid)")
    gateway = None
    if needs_model:
        try:
            gateway = build_gateway(replay_dir=args.llm_replay, live=args.live)
        except LiveModeError as exc:
            raise UsageError(str(exc)) from exc
    if args.baseline is not None and args.baseline not in names:
        raise UsageError(f"--baseline {args.baseline!r} is not among the evaluated engines")
    if args.baseline is not None and len(names) < 2:
        raise UsageError("--baseline needs at least two engines")
    return names, gateway


def validate_only(corpus_path: Path, profile: str | None) -> int:
    """Corpus validation without running any engine; prints every problem."""
    try:
        manifest = json.loads(corpus_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot load corpus {corpus_path}: {exc}", file=sys.stderr)
        return 1
    errors = validate_corpus(manifest, corpus_path.parent)
    if profile:
        errors.extend(validate_corpus_profile(manifest, corpus_path.parent, profile))
    for error in errors:
        print(f"corpus problem: {error}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} problems; corpus is not valid", file=sys.stderr)
        return 1
    print(f"corpus valid: {len(manifest['samples'])} samples, annotations_sha256={annotations_sha256(manifest)}"
          + (f", profile {profile} satisfied" if profile else ""))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.validate_only:
        return validate_only(args.corpus.resolve(), args.profile)
    if args.profile:
        print("usage error: --profile needs --validate-only", file=sys.stderr)
        return 2
    if args.repeat < 1:
        print("evaluation failed: --repeat must be at least 1", file=sys.stderr)
        return 1
    try:
        names, gateway = _resolve_engines(args)
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    try:
        corpus_path = args.corpus.resolve()
        manifest, corpus_sha256 = load_corpus(corpus_path)
        evaluator_sha256 = sha256_bytes(Path(__file__).read_bytes())
        results: dict[str, dict[str, Any]] = {}
        run_manifests: dict[str, dict[str, Any]] = {}
        for name in names:
            spec = get_engine_spec(name)
            engine = create_engine(name, EngineContext(gateway=gateway if spec.uses_llm else None))
            runs = [evaluate(manifest, corpus_path.parent, engine=engine) for _ in range(args.repeat)]
            digests = [
                sha256_bytes(canonical_json({"rows": r["rows"], "metrics": r["metrics"]}).encode()) for r in runs
            ]
            if len(set(digests)) != 1:
                print(f"evaluation failed: repeated runs of engine {name!r} differ", file=sys.stderr)
                return 1
            results[name] = {**runs[0], "digests": digests}
            llm_info = None
            if spec.uses_llm and gateway is not None:
                llm_info = {"mode": gateway.mode, "replay_store_sha256": gateway.store.digest(),
                            "responses_in_store": len(gateway.store.keys()),
                            "replayed": gateway.replayed, "recorded": gateway.recorded}
            run_manifests[name] = build_run_manifest(
                manifest, corpus_sha256, evaluator_sha256, engine=name, engine_version=runs[0]["parser_version"],
                run_mode=(gateway.mode if spec.uses_llm and gateway else "offline"), llm=llm_info,
            )
    except (EvaluationError, OSError, ValueError, ReplayMissError, ReplayIntegrityError, compare.ComparisonError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 1
    root = args.output_dir.resolve()
    for name in names:
        result = results[name]
        errors = build_error_trace(result["rows"])
        engine_dir = root / name
        write_outputs(engine_dir, run_manifests[name], manifest, result, errors)
        _dump(engine_dir / "determinism.json", {"engine": name, "repeat": args.repeat,
                                                "substantive_sha256": result["digests"], "identical": True,
                                                "excluded": ["observations"], "timestamp": _timestamp()})
        detection = result["metrics"]["overall"]["detection"]
        print(
            f"evaluation complete [{name}]: {len(manifest['samples'])} samples, {len(result['rows'])} parameter rows, "
            f"precision={_cell(detection['precision'])}, recall={_cell(detection['recall'])}, "
            f"source_consistent={_cell(detection['source_consistent'])} -> {engine_dir}"
        )
    if len(names) > 1:
        baseline = args.baseline or names[0]
        try:
            comparison = compare.build_comparison(results, baseline, args.bootstrap_resamples, args.seed)
        except compare.ComparisonError as exc:
            print(f"evaluation failed: {exc}", file=sys.stderr)
            return 1
        run_info = {
            "annotations_sha256": annotations_sha256(manifest),
            "corpus_sha256": corpus_sha256,
            "engine_manifest_sha256": {n: run_manifests[n]["manifest_sha256"] for n in names},
        }
        write_comparison(root / "comparison", comparison, {n: results[n]["observations"] for n in names}, run_info)
        print(f"comparison written -> {root / 'comparison'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
