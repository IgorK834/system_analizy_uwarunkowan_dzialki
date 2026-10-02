"""Fresh final corpus tooling: profile validator, second annotator, freeze (PV3-02).

The real fresh corpus needs human annotators and an approved source list, so these tests
build a synthetic v2 corpus (24 final samples over 12 new gminas) in ``tmp_path`` and prove
that the whole chain — skeleton, independent double annotation, agreement, adjudication,
freeze, evaluation — works and that every rule of the final-v2 profile is enforced.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import evaluate_mpzp_parser as ev
from scripts import mpzp_annotation_agreement as agreement
from scripts import mpzp_corpus_tools as tools
from tests.parcel_fixtures_config import find_repo_root

FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"
FORMATS = ["pdf_table"] * 5 + ["ocr_real"] * 4 + ["html"] * 4 + ["pdf_text"] * 11  # 24 final samples
N_FINAL = len(FORMATS)
COVERED = [f"N{i:02d}" for i in range(5)]  # 5 of 24 = 20.8% >= 20%


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _annotation(i: int, **changes: Any) -> dict[str, Any]:
    value = 10.0 + i
    base = {
        "parameter": "max_building_height_m", "operator": "max", "raw_value": f"{10 + i} m", "normalized_value": value,
        "unit": "m", "normalization": "identity", "evidence": f"wysokość zabudowy do {10 + i} m",
        "anchor": f"Dla terenu Z{i}:", "status": "required", "applicability": "zone_section", "page": 1,
    }
    return {**base, **changes}


def _sample(i: int, doc_id: str) -> dict[str, Any]:
    return {
        "sample_id": f"N{i:02d}", "document_id": doc_id, "split": "final", "format": FORMATS[i], "multi_zone": False,
        "zones": [{"symbol": f"Z{i}", "scope_strategy": i % 6 + 1, "annotations": [_annotation(i)]}],
        "annotator": {"id": "annotator-a", "kind": "human"}, "annotated_at": "2026-10-20",
    }


def _submission(samples: list[dict[str, Any]], annotator: str) -> dict[str, Any]:
    return {"annotator": {"id": annotator, "kind": "human"}, "samples": samples}


@pytest.fixture(scope="module")
def v1() -> dict[str, Any]:
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def corpus(tmp_path: Path, v1: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """A complete, frozen v2 corpus: the v1 samples as development plus 24 fresh final samples."""
    draft = tools.build_skeleton(v1, "PV3-02-test-corpus", today="2026-10-01")
    for document in draft["documents"].values():  # the draft lives in tmp_path, the snapshots do not
        document["path"] = str((FIXTURES / document["path"]).resolve())
    for i in range(N_FINAL):
        doc_id = f"new_doc_{i:02d}"
        directory = tmp_path / "documents" / doc_id
        directory.mkdir(parents=True)
        pages = {"pages": [f"Dla terenu Z{i}: wysokość zabudowy do {10 + i} m. Udział zabudowy do 40%."]}
        (directory / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
        (directory / "source.json").write_text(json.dumps({"source_page_numbers": [1]}), encoding="utf-8")
        draft["documents"][doc_id] = {
            "path": str(directory), "gmina": f"Nowa gmina {i % 12}", "voivodeship": f"woj-{i % 4}", "split": "final",
            "format_family": "pdf_text", "title": f"akt {i}", "url": f"https://bip.example.invalid/akt-{i}.pdf",
            "fetched_at": "2026-10-20T10:00:00+00:00", "content_length": 1000 + i, "document_sha256": _sha(f"doc{i}"),
            "pages_sha256": hashlib.sha256((directory / "pages.json").read_bytes()).hexdigest(),
            "page_count": 1, "source_page_numbers": [1], "tls_verification": "enabled",
            "legal_basis": "Akt prawa miejscowego, jawny publicznie (BIP).",
        }
        draft["samples"].append(_sample(i, doc_id))
    first_samples = [copy.deepcopy(s) for s in draft["samples"] if s["sample_id"] in COVERED]
    second_samples = copy.deepcopy(first_samples)
    del second_samples[1]["zones"][0]["annotations"][0]  # N01: the second annotator found no value (presence)
    second_samples[2]["zones"][0]["annotations"][0]["normalized_value"] = 99.0  # N02: a different value
    second_samples[3]["zones"][0]["annotations"][0]["status"] = "acceptable"  # N03: same value, other status
    for sample in second_samples:
        sample["annotator"] = {"id": "annotator-b", "kind": "human"}
    out = tmp_path / "second"
    out.mkdir()
    (out / "first_annotation.json").write_text(json.dumps(_submission(first_samples, "annotator-a")), encoding="utf-8")
    (out / "second_annotation.json").write_text(json.dumps(_submission(second_samples, "annotator-b")), encoding="utf-8")
    assert tools.main(["agreement", "--first", str(out / "first_annotation.json"),
                       "--second", str(out / "second_annotation.json"), "--output-dir", str(out)]) == 0
    rows = list(csv.DictReader((out / "adjudication_log.csv").open(encoding="utf-8")))
    assert {r["type"] for r in rows} == {"presence", "value", "status_or_scope"}
    for row in rows:  # the adjudicator sides with the source text, i.e. with the first annotation
        row.update(resolution="a", resolved_by="adjudicator", rationale="cytat w tekście źródłowym", resolved_at="2026-10-25")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(agreement.ADJUDICATION_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    (out / "adjudication_log.csv").write_text(buffer.getvalue(), encoding="utf-8")
    draft["second_annotation"] = {
        "annotator": {"id": "annotator-b", "kind": "human"}, "sample_ids": COVERED,
        **{key: f"second/{name}" for key, name in (
            ("first_file", "first_annotation.json"), ("file", "second_annotation.json"),
            ("agreement_report", "agreement_report.json"), ("adjudication_log", "adjudication_log.csv"))},
    }
    for key in ("first_file", "file", "agreement_report", "adjudication_log"):
        draft["second_annotation"][f"{key}_sha256"] = hashlib.sha256((tmp_path / draft["second_annotation"][key]).read_bytes()).hexdigest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    assert tools.main(["freeze", "--corpus", str(path)]) == 0
    return path, json.loads(path.read_text(encoding="utf-8"))


# --- skeleton -------------------------------------------------------------------------------------


def test_skeleton_promotes_the_old_final_split_to_development_round_2(v1: dict[str, Any]) -> None:
    draft = tools.build_skeleton(v1, "PV3-02-x", today="2026-10-01")
    assert draft["corpus_id"] == "PV3-02-x" and draft["created_at"] == "2026-10-01" and "freeze" not in draft
    assert {s["split"] for s in draft["samples"]} == {"development"}
    assert {d["split"] for d in draft["documents"].values()} == {"development"}
    rounds = [s["development_round"] for s in draft["samples"]]
    assert rounds.count(2) == sum(s["split"] == "final" for s in v1["samples"]) == 14 and rounds.count(1) == 7
    previous = draft["previous_corpus"]
    assert previous["corpus_id"] == v1["corpus_id"] and previous["annotations_sha256"] == ev.annotations_sha256(v1)
    assert len(previous["gminas"]) == 13 and all(url.startswith("http") for url in previous["document_urls"])
    assert v1["samples"][0]["split"] != "x"  # the source manifest is not modified
    assert any(s["split"] == "final" for s in v1["samples"])
    with pytest.raises(tools.ToolError):
        tools.build_skeleton(v1, v1["corpus_id"])


def test_skeleton_cli_refuses_to_overwrite_a_draft(tmp_path: Path) -> None:
    target = tmp_path / "draft.json"
    args = ["skeleton", "--previous", str(FIXTURES / "manifest.json"), "--corpus-id", "PV3-02-cli", "--output", str(target)]
    assert tools.main(args) == 0 and json.loads(target.read_text())["corpus_id"] == "PV3-02-cli"
    assert tools.main(args) == 1  # the draft may already hold hours of annotation work


def test_the_current_corpus_does_not_meet_the_fresh_final_profile(v1: dict[str, Any]) -> None:
    errors = ev.validate_corpus_profile(v1, FIXTURES, "final-v2")
    assert any("final samples < 20" in e for e in errors) and any("previous_corpus" in e for e in errors)
    assert any("annotator must be a human" in e for e in errors) and any("second_annotation" in e for e in errors)


# --- the whole chain on a synthetic corpus ---------------------------------------------------------


def test_complete_frozen_corpus_passes_validation_and_evaluation(
    corpus: tuple[Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, manifest = corpus
    assert ev.validate_corpus_profile(manifest, path.parent) == []
    assert ev.validate_corpus(manifest, path.parent) == []
    assert manifest["freeze"]["engines_run_before_freeze"] == []
    assert manifest["freeze"]["annotations_sha256"] == ev.annotations_sha256(manifest)
    assert ev.main(["--corpus", str(path), "--validate-only", "--profile", "final-v2"]) == 0
    assert "profile final-v2 satisfied" in capsys.readouterr().out
    # an engine can now be run on the frozen set; every final sample is scored
    out = tmp_path / "results"
    assert ev.main(["--corpus", str(path), "--output-dir", str(out), "--repeat", "1"]) == 0
    metrics = json.loads((out / "legacy" / "metrics.json").read_text())
    assert len(metrics["by_gmina"]) == 13 + 12 and set(metrics["by_split"]) == {"development", "final"}
    # changing one annotation after the freeze is an error of the evaluator
    tampered = copy.deepcopy(manifest)
    tampered["samples"][-1]["zones"][0]["annotations"][0]["raw_value"] += " "
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(tampered), encoding="utf-8")
    assert ev.main(["--corpus", str(bad), "--output-dir", str(tmp_path / "x")]) == 1
    assert ev.main(["--corpus", str(bad), "--validate-only"]) == 1
    assert "annotations changed after freeze" in capsys.readouterr().err


def test_an_unfrozen_corpus_runs_no_engine(corpus: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    path, manifest = corpus
    manifest.pop("freeze")
    unfrozen = tmp_path / "unfrozen.json"
    unfrozen.write_text(json.dumps(manifest), encoding="utf-8")
    assert ev.main(["--corpus", str(unfrozen), "--output-dir", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out").exists()  # nothing ran, so nothing could have looked at the final split
    assert ev.main(["--corpus", str(unfrozen), "--validate-only"]) == 1  # validation does not freeze either


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        (lambda m: [m["samples"].pop() for _ in range(5)], "19 final samples < 20"),
        (lambda m: m["samples"][-1].update(annotator={"id": "claude", "kind": "ai"}), "annotator must be a human"),
        (lambda m: m["samples"][-1]["zones"][0].pop("scope_strategy"), "scope_strategy must be one of"),
        (lambda m: [z.update(scope_strategy=1) for s in m["samples"] if s["split"] == "final" for z in s["zones"]],
         "scope strategy 2 is not represented"),
        (lambda m: m["previous_corpus"].update(gminas=[f"Nowa gmina {i}" for i in range(12)]), "new gminas < 10"),
        (lambda m: m["previous_corpus"].update(corpus_id=m["corpus_id"]), "corpus_id must differ"),
        (lambda m: m["previous_corpus"].update(document_urls=["https://bip.example.invalid/akt-0.pdf"]), "already used"),
        (lambda m: m.pop("previous_corpus"), "previous_corpus"),
        (lambda m: [d.update(voivodeship="woj-0") for d in m["documents"].values() if d["split"] == "final"], "voivodeships < 4"),
        (lambda m: m["documents"]["new_doc_00"].update(tls_verification="disabled"), "TLS verification"),
        (lambda m: m["documents"]["new_doc_00"].update(document_sha256="xyz"), "not a SHA-256"),
        (lambda m: m["documents"]["new_doc_00"].update(content_length=0), "content_length"),
        (lambda m: m["documents"]["new_doc_00"].pop("legal_basis"), "legal_basis is required"),
        (lambda m: [s.update(format="pdf_text") for s in m["samples"] if s["format"] == "pdf_table"], "format pdf_table"),
        (lambda m: [s.update(format="pdf_text") for s in m["samples"] if s["format"] == "ocr_real"], "format ocr_real"),
        (lambda m: [s.update(format="pdf_text") for s in m["samples"] if s["split"] == "final" and s["format"] == "html"], "format html"),
        (lambda m: m["samples"][-1].update(format="ocr_simulated"), "simulated scans"),
        (lambda m: m["samples"][0].pop("development_round"), "development_round"),
        (lambda m: m["freeze"].update(engines_run_before_freeze=["legacy"]), "engines_run_before_freeze"),
        (lambda m: m["freeze"].pop("frozen_at"), "frozen_at"),
        (lambda m: m.pop("second_annotation"), "second_annotation block is required"),
        (lambda m: m["second_annotation"].update(sample_ids=COVERED[:2]), "second annotator covers 2 samples < 5"),
        (lambda m: m["second_annotation"].update(annotator={"id": "annotator-a", "kind": "human"}), "different person"),
        (lambda m: m["second_annotation"].update(annotator={"id": "x", "kind": "ai"}), "second_annotation.annotator must be a human"),
        (lambda m: m["second_annotation"].update(sample_ids=[*COVERED, "ghost"]), "unknown samples"),
        (lambda m: m["second_annotation"].update(agreement_report_sha256="0" * 64), "differs from its recorded SHA-256"),
        (lambda m: m["second_annotation"].update(file="second/missing.json"), "missing or unreadable"),
    ],
)
def test_profile_rejects_what_the_final_set_must_not_contain(
    corpus: tuple[Path, dict[str, Any]], mutation: Any, fragment: str
) -> None:
    path, manifest = corpus
    tampered = copy.deepcopy(manifest)
    mutation(tampered)
    errors = ev.validate_corpus_profile(tampered, path.parent)
    assert any(fragment in error for error in errors), errors


def test_agreement_report_and_adjudication_log_must_be_real(corpus: tuple[Path, dict[str, Any]]) -> None:
    path, manifest = corpus
    base = path.parent / "second"
    original = {name: (base / name).read_bytes() for name in ("agreement_report.json", "adjudication_log.csv")}

    def rehash(name: str, key: str) -> dict[str, Any]:
        tampered = copy.deepcopy(manifest)
        tampered["second_annotation"][f"{key}_sha256"] = hashlib.sha256((base / name).read_bytes()).hexdigest()
        return tampered

    try:
        report = json.loads(original["agreement_report.json"])
        report["presence"]["agreement"]["numerator"] += 1  # a flattering number nobody computed
        (base / "agreement_report.json").write_text(json.dumps(report), encoding="utf-8")
        errors = ev.validate_corpus_profile(rehash("agreement_report.json", "agreement_report"), path.parent)
        assert any("agreement report does not match" in e for e in errors)
        (base / "agreement_report.json").write_bytes(original["agreement_report.json"])

        rows = list(csv.DictReader(io.StringIO(original["adjudication_log.csv"].decode())))
        for label, edit, fragment in (
            ("unresolved", lambda rs: rs[0].update(resolution=""), "resolution must be one of"),
            ("no author", lambda rs: rs[1].update(resolved_by=" "), "resolved_by is empty"),
            ("no reason", lambda rs: rs[1].update(rationale=""), "rationale is empty"),
            ("missing row", lambda rs: rs.pop(), "has no adjudication row"),
            ("foreign row", lambda rs: rs.append({**rs[0], "parameter": "setback_m"}), "not a disagreement"),
            ("duplicate", lambda rs: rs.append(dict(rs[0])), "duplicated"),
            ("not applied", lambda rs: rs[0].update(resolution="b"), "not reflected in the frozen annotations"),
        ):
            edited = copy.deepcopy(rows)
            edit(edited)
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(agreement.ADJUDICATION_COLUMNS), lineterminator="\n")
            writer.writeheader()
            writer.writerows(edited)
            (base / "adjudication_log.csv").write_text(buffer.getvalue(), encoding="utf-8")
            errors = ev.validate_corpus_profile(rehash("adjudication_log.csv", "adjudication_log"), path.parent)
            assert any(fragment in e for e in errors), (label, errors)
    finally:
        for name, content in original.items():
            (base / name).write_bytes(content)


def test_freeze_refuses_an_invalid_corpus_and_never_freezes_silently(
    corpus: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    path, manifest = corpus
    broken = copy.deepcopy(manifest)
    broken.pop("freeze")
    broken["samples"][-1]["annotator"] = {"id": "claude", "kind": "ai"}
    target = tmp_path / "broken.json"
    target.write_text(json.dumps(broken), encoding="utf-8")
    assert tools.main(["freeze", "--corpus", str(target)]) == 1
    assert "freeze" not in json.loads(target.read_text())  # the file was not modified
    with pytest.raises(tools.ToolError, match="not valid"):
        tools.freeze(copy.deepcopy(broken), path.parent)


# --- reports -----------------------------------------------------------------------------------------


def test_coverage_report_and_download_log(corpus: tuple[Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, manifest = corpus
    report = tools.coverage(manifest)
    assert report["samples"] == {"total": 21 + N_FINAL, "final": N_FINAL, "development": 21}
    assert report["final_formats"] == {"html": 4, "ocr_real": 4, "pdf_table": 5, "pdf_text": 11}
    assert len(report["new_gminas"]) == 12 and len(report["voivodeships"]) == 4
    assert sum(report["scope_strategy_zones"].values()) == N_FINAL and report["second_annotator"]["samples"] == 5
    text = tools.render_coverage(report)
    for fragment in ("gminy niewykorzystane wcześniej | 12 z 12", "województwa | 4", "tabela parametrów w PDF | 5",
                     "prawdziwe skany (OCR) | 4", "drugi anotator | 5 próbek (21%)"):
        assert fragment in text
    log = list(csv.DictReader(io.StringIO(tools.download_log(manifest, only_final=True))))
    assert len(log) == N_FINAL and {row["tls_verification"] for row in log} == {"enabled"}
    assert all(len(row["document_sha256"]) == 64 and row["url"].startswith("https://") for row in log)
    assert len(list(csv.DictReader(io.StringIO(tools.download_log(manifest))))) == len(manifest["documents"])
    out = tmp_path / "download_log.csv"
    assert tools.main(["download-log", "--corpus", str(path), "--only-final", "--output", str(out)]) == 0
    assert out.read_text().count("\n") == N_FINAL + 1
    assert tools.main(["download-log", "--corpus", str(path)]) == 0 and "document_id" in capsys.readouterr().out
    assert tools.main(["coverage", "--corpus", str(path)]) == 0 and "Wymaganie" in capsys.readouterr().out
    assert tools.main(["coverage", "--corpus", str(tmp_path / "missing.json")]) == 1


def test_agreement_cli_keeps_an_existing_adjudication_log(corpus: tuple[Path, dict[str, Any]], capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = corpus
    base = path.parent / "second"
    log_before = (base / "adjudication_log.csv").read_bytes()
    assert tools.main(["agreement", "--first", str(base / "first_annotation.json"),
                       "--second", str(base / "second_annotation.json"), "--output-dir", str(base)]) == 0
    assert "kept" in capsys.readouterr().out
    assert (base / "adjudication_log.csv").read_bytes() == log_before  # hours of adjudication are never overwritten


# --- agreement statistics with hand-calculated values ------------------------------------------------


def _ann(parameter: str, value: float, status: str = "required", applicability: str = "zone_section") -> dict[str, Any]:
    return {"parameter": parameter, "normalized_value": value, "status": status, "applicability": applicability}


def test_agreement_matches_the_hand_calculation() -> None:
    first = [{"sample_id": "S", "zones": [
        {"symbol": "Z1", "annotations": [_ann("p1", 10), _ann("p2", 5), _ann("p3", 4, "acceptable")]},
        {"symbol": "Z2", "annotations": [_ann("p1", 7)]},
    ]}, {"sample_id": "not compared", "zones": []}]
    second = [{"sample_id": "S", "zones": [
        {"symbol": "Z1", "annotations": [_ann("p1", 10), _ann("p3", 4, "acceptable", "general_clause")]},
        {"symbol": "Z2", "annotations": [_ann("p1", 8), _ann("p3", 3)]},
        {"symbol": "Z3", "annotations": []},
    ]}]
    report = agreement.compute_agreement(first, second, ["p1", "p2", "p3"])
    assert report["samples_compared"] == ["S"] and report["pairs"] == 6  # Z3 is annotated by one person only
    # presence: Z1p1 both, Z1p2 only A, Z1p3 neither, Z2p1 both, Z2p2 neither, Z2p3 only B
    assert report["presence"]["table"] == {"both": 2, "only_a": 1, "only_b": 1, "neither": 2}
    assert (report["presence"]["agreement"]["numerator"], report["presence"]["agreement"]["denominator"]) == (4, 6)
    assert report["presence"]["cohen_kappa"] == pytest.approx((4 / 6 - 0.5) / 0.5)  # = 1/3
    assert (report["exact_required_value"]["numerator"], report["exact_required_value"]["denominator"]) == (1, 2)
    # all values: Z1p1 same, Z1p2 differs, Z1p3 same values, Z2p1 differs, Z2p3 differs
    assert (report["all_values"]["numerator"], report["all_values"]["denominator"]) == (2, 5)
    found = {(d["zone_symbol"], d["parameter"]): d["type"] for d in report["disagreements"]}
    assert found == {("Z1", "p2"): "presence", ("Z1", "p3"): "status_or_scope", ("Z2", "p1"): "value",
                     ("Z2", "p3"): "presence", ("Z3", "*"): "zone_only_one"}


def test_kappa_has_no_value_when_chance_agreement_is_total() -> None:
    assert agreement.kappa(0, 0, 0, 5) is None and agreement.kappa(0, 0, 0, 0) is None
    assert agreement.kappa(5, 0, 0, 0) is None
    assert agreement.kappa(3, 1, 1, 3) == pytest.approx(0.5)  # observed 6/8, expected 1/2
    nothing = agreement.compute_agreement([], [], ["p1"])
    assert nothing["pairs"] == 0 and nothing["presence"]["agreement"]["value"] is None


def test_adjudication_skeleton_lists_every_disagreement_unresolved() -> None:
    disagreements = [{"sample_id": "S", "zone_symbol": "Z1", "parameter": "p1", "type": "value",
                      "annotator_a": "10/required/zone_section", "annotator_b": "12/required/zone_section"}]
    rows = list(csv.DictReader(io.StringIO(agreement.adjudication_skeleton(disagreements))))
    assert rows == [{"sample_id": "S", "zone_symbol": "Z1", "parameter": "p1", "type": "value",
                     "annotator_a": "10/required/zone_section", "annotator_b": "12/required/zone_section",
                     "resolution": "", "resolved_by": "", "rationale": "", "resolved_at": ""}]
    problems = agreement.check_adjudication(agreement.adjudication_skeleton(disagreements), disagreements)
    assert len(problems) == 3  # resolution, resolved_by and rationale are all still empty
    assert agreement.check_adjudication("", disagreements) == [f"disagreement {('S', 'Z1', 'p1')} has no adjudication row"]
