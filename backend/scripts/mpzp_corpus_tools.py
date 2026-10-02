#!/usr/bin/env python3
"""Offline helpers for building the fresh final MPZP corpus (PV3-02).

None of these commands uses the network or runs an engine. They support the manual,
human parts of the work described in ``docs/evaluation/mpzp_annotation_protocol.md``:

* ``skeleton``     v1 manifest -> unfrozen v2 draft (old final samples become development round 2)
* ``agreement``    two independent submissions -> agreement report + adjudication log skeleton
* ``coverage``     coverage report (formats, gminas, voivodeships, scope strategies, second annotator)
* ``download-log`` file / source / size / SHA-256 table of every document
* ``freeze``       validate the draft against the final-v2 profile and write the freeze record

Run from the repository root, e.g.::

    python3 backend/scripts/mpzp_corpus_tools.py skeleton --corpus-id PV3-02-... --output manifest.v2-draft.json
"""

from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts import mpzp_annotation_agreement as agreement  # noqa: E402
from scripts import evaluate_mpzp_parser as ev  # noqa: E402

DEFAULT_CORPUS = ev.DEFAULT_MANIFEST
FREEZE_STATEMENT = (
    "Anotacje zbioru końcowego wykonali ludzie według protokołu docs/evaluation/mpzp_annotation_protocol.md, "
    "co najmniej 20% próbek niezależnie przez drugiego anotatora; rozstrzygnięcia są w dzienniku. Żaden silnik "
    "nie został uruchomiony na zbiorze końcowym przed zamrożeniem. Zbiór rozwojowy 2 to poprzedni zbiór końcowy. "
    "Po zamrożeniu nie wolno zmieniać anotacji bez nowego corpus_id i nowego skrótu."
)


class ToolError(ValueError):
    """The requested operation would produce an invalid or misleading corpus."""


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"cannot read {path}: {exc}") from exc


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --- skeleton -------------------------------------------------------------------------------


def build_skeleton(previous: Mapping[str, Any], corpus_id: str, today: str | None = None) -> dict[str, Any]:
    """Unfrozen v2 draft: every existing sample is development; the old final split becomes round 2."""
    if corpus_id == previous.get("corpus_id"):
        raise ToolError("the new corpus_id must differ from the previous one")
    draft = copy.deepcopy(dict(previous))
    documents = draft["documents"]
    draft["previous_corpus"] = {
        "corpus_id": previous["corpus_id"],
        "annotations_sha256": ev.annotations_sha256(previous),
        "gminas": sorted({doc["gmina"] for doc in documents.values()}),
        "document_urls": sorted({doc["url"] for doc in documents.values() if doc.get("url")}),
        "final_promoted_to_development": True,
    }
    draft["corpus_id"] = corpus_id
    draft["schema_version"] = "1.1.0"
    draft["created_at"] = today or date.today().isoformat()
    for document in documents.values():
        document["split"] = "development"
    for sample in draft["samples"]:
        sample["development_round"] = 2 if sample["split"] == "final" else 1
        sample["split"] = "development"
    draft.pop("freeze", None)
    draft.pop("second_annotation", None)
    return draft


# --- coverage -------------------------------------------------------------------------------


def coverage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    documents = manifest["documents"]
    final = [item for item in manifest["samples"] if item.get("split") == "final"]
    previous = manifest.get("previous_corpus") or {}
    old_gminas = set(previous.get("gminas") or [])
    gminas = {documents[item["document_id"]]["gmina"] for item in final}
    strategies: Counter[object] = Counter()
    for sample in final:
        for zone in sample["zones"]:
            strategies[zone.get("scope_strategy")] += 1
    second = manifest.get("second_annotation") or {}
    covered = set(second.get("sample_ids") or [])
    return {
        "samples": {"total": len(manifest["samples"]), "final": len(final),
                    "development": len(manifest["samples"]) - len(final)},
        "final_formats": dict(sorted(Counter(item["format"] for item in final).items())),
        "final_gminas": len(gminas),
        "new_gminas": sorted(gminas - old_gminas),
        "voivodeships": sorted({documents[i["document_id"]].get("voivodeship") or "?" for i in final}),
        "scope_strategy_zones": {str(k): strategies.get(k, 0) for k in ev.SCOPE_STRATEGIES},
        "multi_zone_final": sum(bool(item.get("multi_zone")) for item in final),
        "second_annotator": {"samples": len(covered), "share": len(covered) / len(final) if final else None},
    }


def render_coverage(report: Mapping[str, Any]) -> str:
    formats = report["final_formats"]
    second = report["second_annotator"]
    lines = [
        "| Wymaganie | Wynik |", "|---|---|",
        f"| próbki zbioru końcowego | {report['samples']['final']} (rozwojowe: {report['samples']['development']}) |",
        f"| gminy niewykorzystane wcześniej | {len(report['new_gminas'])} z {report['final_gminas']} gmin zbioru końcowego |",
        f"| województwa | {len(report['voivodeships'])}: {', '.join(report['voivodeships'])} |",
        f"| tabela parametrów w PDF | {formats.get('pdf_table', 0)} |",
        f"| prawdziwe skany (OCR) | {formats.get('ocr_real', 0)} (symulowane: {formats.get('ocr_simulated', 0)}) |",
        f"| dokumenty HTML / wypisy | {formats.get('html', 0)} |",
        "| strefy według strategii zakresu 1–6 | " + ", ".join(f"{k}: {v}" for k, v in report["scope_strategy_zones"].items()) + " |",
        f"| próbki wielostrefowe | {report['multi_zone_final']} |",
        f"| drugi anotator | {second['samples']} próbek"
        + (f" ({second['share']:.0%})" if second["share"] is not None else "") + " |",
    ]
    return "\n".join(lines) + "\n"


# --- download log ---------------------------------------------------------------------------

LOG_COLUMNS = (
    "document_id", "split", "gmina", "voivodeship", "path", "url", "fetched_at", "content_length",
    "document_sha256", "pages_sha256", "tls_verification", "legal_basis",
)


def download_log(manifest: Mapping[str, Any], only_final: bool = False) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(LOG_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for doc_id, document in manifest["documents"].items():
        if only_final and document.get("split") != "final":
            continue
        row = {column: document.get(column) for column in LOG_COLUMNS}
        row["document_id"] = doc_id
        writer.writerow({k: "" if v is None else v for k, v in row.items()})
    return buffer.getvalue()


# --- freeze ---------------------------------------------------------------------------------

_FREEZE_ONLY = ("missing freeze record", "annotations changed after freeze")


def freeze(manifest: dict[str, Any], base_dir: Path, today: str | None = None) -> dict[str, Any]:
    """Writes the freeze record if, and only if, everything else is valid."""
    errors = [e for e in ev.validate_corpus(manifest, base_dir) if not e.startswith(_FREEZE_ONLY)]
    errors += ev.validate_corpus_profile(manifest, base_dir, "final-v2")
    if errors:
        raise ToolError("cannot freeze, the corpus is not valid:\n  " + "\n  ".join(errors))
    manifest["freeze"] = {
        "annotations_sha256": ev.annotations_sha256(manifest),
        "frozen_at": today or date.today().isoformat(),
        "engines_run_before_freeze": [],
        "statement": FREEZE_STATEMENT,
    }
    return manifest


# --- command line ----------------------------------------------------------------------------


def _cmd_skeleton(args: argparse.Namespace) -> int:
    draft = build_skeleton(_read(args.corpus), args.corpus_id)
    if args.output.exists():
        raise ToolError(f"{args.output} exists; refusing to overwrite a draft")
    _write_json(args.output, draft)
    print(f"draft written: {args.output} ({len(draft['samples'])} development samples; add the new final samples, "
          "second annotation and freeze it)")
    return 0


def _cmd_agreement(args: argparse.Namespace) -> int:
    first, second = _read(args.first), _read(args.second)
    report = agreement.compute_agreement(first.get("samples", []), second.get("samples", []), list(ev.CATALOG))
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "agreement_report.json", report)
    log_path = out / "adjudication_log.csv"
    if log_path.exists():
        print(f"{log_path} exists and is kept; compare it with the new disagreements before relying on it")
    else:
        log_path.write_text(agreement.adjudication_skeleton(report["disagreements"]), encoding="utf-8")
    presence = report["presence"]
    print(f"compared {len(report['samples_compared'])} samples, {report['pairs']} pairs: presence agreement "
          f"{presence['agreement']['numerator']}/{presence['agreement']['denominator']}, kappa={presence['cohen_kappa']}, "
          f"exact value {report['exact_required_value']['numerator']}/{report['exact_required_value']['denominator']}, "
          f"{len(report['disagreements'])} disagreements -> {out}")
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    print(render_coverage(coverage(_read(args.corpus))))
    return 0


def _cmd_download_log(args: argparse.Namespace) -> int:
    text = download_log(_read(args.corpus), only_final=args.only_final)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"written: {args.output}")
    else:
        print(text, end="")
    return 0


def _cmd_freeze(args: argparse.Namespace) -> int:
    manifest = freeze(_read(args.corpus), args.corpus.parent)
    _write_json(args.corpus, manifest)
    print(f"frozen: annotations_sha256={manifest['freeze']['annotations_sha256']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    skeleton = sub.add_parser("skeleton")
    skeleton.add_argument("--previous", dest="corpus", type=Path, default=DEFAULT_CORPUS)
    skeleton.add_argument("--corpus-id", required=True)
    skeleton.add_argument("--output", type=Path, required=True)
    skeleton.set_defaults(handler=_cmd_skeleton)
    agree = sub.add_parser("agreement")
    agree.add_argument("--first", type=Path, required=True,
                       help="first annotator's independent submission for the double-annotated samples")
    agree.add_argument("--second", type=Path, required=True, help="second annotator's independent submission")
    agree.add_argument("--output-dir", type=Path, required=True)
    agree.set_defaults(handler=_cmd_agreement)
    cov = sub.add_parser("coverage")
    cov.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    cov.set_defaults(handler=_cmd_coverage)
    log = sub.add_parser("download-log")
    log.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    log.add_argument("--output", type=Path)
    log.add_argument("--only-final", action="store_true")
    log.set_defaults(handler=_cmd_download_log)
    frz = sub.add_parser("freeze")
    frz.add_argument("--corpus", type=Path, required=True)
    frz.set_defaults(handler=_cmd_freeze)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (ToolError, OSError, KeyError) as exc:
        print(f"corpus tool failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
