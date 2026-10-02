"""The PV3-01 spike is a manual, networked tool: it must stay out of pytest, CI and the repository's secrets.

These tests exercise only the offline parts (``prepare``) and the refusals; nothing here calls an API
and no test can: the spike has no network path without ``GEMINI_API_KEY`` and ``--confirm-public-text``.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from scripts import llm_spike
from tests.parcel_fixtures_config import find_repo_root

REPO_ROOT = find_repo_root()


def test_spike_is_outside_the_test_tree_and_ci() -> None:
    config = tomllib.loads((REPO_ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
    assert (REPO_ROOT / "backend/scripts/llm_spike.py").is_file()
    assert not (REPO_ROOT / "backend/tests/llm_spike.py").exists()
    for workflow in (REPO_ROOT / ".github/workflows").glob("*.y*ml"):
        assert "llm_spike" not in workflow.read_text(encoding="utf-8"), workflow.name


def test_prepare_is_offline_deterministic_and_keeps_every_annotated_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(llm_spike.API_KEY_ENV, raising=False)
    first, second = tmp_path / "first", tmp_path / "second"
    assert llm_spike.main(["prepare", "--results-dir", str(first)]) == 0
    assert llm_spike.main(["prepare", "--results-dir", str(second)]) == 0
    a = json.loads((first / "inputs.json").read_text(encoding="utf-8"))
    b = json.loads((second / "inputs.json").read_text(encoding="utf-8"))
    assert [row["input_sha256"] for row in a["blocks"]] == [row["input_sha256"] for row in b["blocks"]]
    assert [row["block_id"] for row in a["blocks"]] == [f"B{n:02d}" for n in range(1, 11)]
    assert all(len(row["input_sha256"]) == 64 and row["chars"] > 0 and not row["truncated"] for row in a["blocks"])
    assert {row["format"] for row in a["blocks"]} >= {"pdf_text", "pdf_table", "html", "ocr_real"}
    # no document text and no parcel or user identifier is stored in the inputs table
    assert "text" not in a["blocks"][0] and "user_text" not in a["blocks"][0]
    blocks, _ = llm_spike.build_blocks(llm_spike.ev.DEFAULT_MANIFEST)
    assert all(block["input_sha256"] == row["input_sha256"] for block, row in zip(blocks, a["blocks"], strict=True))
    assert all(block["user_text"].startswith("Zone symbols to extract for:") for block in blocks)
    assert "B05" in capsys.readouterr().out


def test_commands_that_spend_money_refuse_without_key_confirmation_or_outside_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    args = ["--results-dir", str(tmp_path)]
    monkeypatch.delenv(llm_spike.API_KEY_ENV, raising=False)
    assert llm_spike.main(["measure", *args]) == 1
    assert "--confirm-public-text" in capsys.readouterr().err  # asks before sending text
    assert llm_spike.main(["measure", "--confirm-public-text", *args]) == 1
    assert llm_spike.API_KEY_ENV in capsys.readouterr().err  # and needs the key from the environment
    assert llm_spike.main(["models", *args]) == 1
    monkeypatch.setenv(llm_spike.API_KEY_ENV, "not-a-real-key")
    monkeypatch.setenv("CI", "true")
    assert llm_spike.main(["measure", "--confirm-public-text", *args]) == 1
    assert "must not run in CI" in capsys.readouterr().err
    assert not list(tmp_path.glob("measurements*"))  # nothing was measured or written
    with pytest.raises(SystemExit):  # there is no way to pass the key on the command line
        llm_spike.main(["models", "--api-key", "x"])


def test_the_key_is_only_ever_sent_to_the_official_host() -> None:
    with pytest.raises(llm_spike.SpikeError, match="official host"):
        llm_spike.Client("k", "https://example.com", 5.0)
    llm_spike.Client("k", "http://127.0.0.1:9", 5.0).close()
    client = llm_spike.Client("AIzaSyA-secret-value-0123456789", llm_spike.DEFAULT_BASE_URL, 5.0)
    try:
        redacted = client.redact("error for AIzaSyA-secret-value-0123456789 and AIzaSyB-another-0123456789")
        assert "AIza" not in redacted and redacted.count("<redacted>") == 2
    finally:
        client.close()
