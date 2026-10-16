"""Czytnik GML aktu z RU: metadane CSW (ostrzeżenia, bramka grzeczności) i obiekty nienależące do aktu (ADR-026)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.modules.imports.infrastructure.pog import ru_gml_reader
from app.modules.imports.infrastructure.pog.csw_metadata import CswMetadataResult
from app.modules.imports.infrastructure.pog.ru_gml_reader import (
    PARSER_CONFIG_ID,
    PARSER_CONFIG_ID_WITH_CSW,
    RuGmlPogReader,
    RuGmlSource,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ru_national"
WFS = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"
CSW = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/csw"


def _source(content: bytes | None = None) -> RuGmlSource:
    return RuGmlSource(
        content=content if content is not None else (FIXTURES / "act_295.gml").read_bytes(),
        filename="act_295.gml",
        published_act_id=295,
        terc="226301",
        source_reference=WFS,
    )


class Gate:
    def __init__(self) -> None:
        self.waits = 0

    def wait(self) -> None:
        self.waits += 1


def _csw_result(error: str | None = None) -> CswMetadataResult:
    return CswMetadataResult(records=(), response=b"", source=None, complete=error is None, error=error)


def test_without_csw_the_parser_config_is_plain_and_the_token_is_kept() -> None:
    batch = RuGmlPogReader(_source()).read()
    assert batch.parser_config_id == PARSER_CONFIG_ID and batch.complete and batch.strict_identifiers
    assert batch.source_tokens == {batch.acts[0].act_identifier: 295}
    assert batch.content == (FIXTURES / "act_295.gml").read_bytes()  # artefakt = bajty pliku bez zmian


def test_csw_metadata_changes_the_parser_config_and_waits_on_the_politeness_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fetch(client: Any, url: str, teryt: str) -> CswMetadataResult:
        calls.append((url, teryt))
        return _csw_result()

    monkeypatch.setattr(ru_gml_reader, "fetch_act_metadata", fetch)
    gate = Gate()

    batch = RuGmlPogReader(_source(), csw_url=CSW, csw_client=object(), csw_gate=gate).read()

    assert batch.parser_config_id == PARSER_CONFIG_ID_WITH_CSW and calls == [(CSW, batch.acts[0].teryt)]
    assert gate.waits == 1


def test_csw_error_and_csw_outage_are_warnings_that_do_not_block_the_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ru_gml_reader, "fetch_act_metadata", lambda *_args: _csw_result("csw_http_503"))
    soft = RuGmlPogReader(_source(), csw_url=CSW, csw_client=object()).read()
    assert any(item.startswith("csw_http_503:") for item in soft.warnings) and len(soft.acts) == 1

    def boom(*_args: Any) -> CswMetadataResult:
        raise TimeoutError("CSW nie odpowiada")

    monkeypatch.setattr(ru_gml_reader, "fetch_act_metadata", boom)
    hard = RuGmlPogReader(_source(), csw_url=CSW, csw_client=object(), csw_gate=Gate()).read()
    assert any(item.startswith("csw_unavailable:") and item.endswith(":TimeoutError") for item in hard.warnings)
    assert len(hard.acts) == 1 and hard.parser_config_id == PARSER_CONFIG_ID_WITH_CSW


def test_features_that_do_not_belong_to_the_act_are_reported_not_dropped_silently() -> None:
    text = (FIXTURES / "act_1735.gml").read_text(encoding="utf-8")
    plan = '<app:plan xlink:href="https://www.gov.pl/zagospodarowanieprzestrzenne/app/AktPlanowaniaPrzestrzennego/PL.ZIPPZP.11530/200901-POG/1POG" />'
    assert plan in text
    # Pierwszy obiekt wskazuje inny akt (wadliwy plik RU): nie jest przypisany do aktu, ale ostrzeżenie o nim zostaje.
    broken = text.replace(plan, plan.replace("/1POG", "/9POG"), 1)
    source = RuGmlSource(broken.encode(), "act_1735.gml", 1735, "200901", WFS)

    batch = RuGmlPogReader(source).read()

    act = batch.acts[0]
    assert [item for item in batch.warnings if item.startswith("gml_orphan_features:")] == [
        f"gml_orphan_features:{act.act_identifier}:1"
    ]
    assert len(act.features) == text.count("<app:StrefaPlanistyczna ") + text.count("<app:ObszarUzupelnieniaZabudowy ") + text.count(
        "<app:ObszarZabudowySrodmiejskiej "
    ) + text.count("<app:ObszarStandardowDostepnosciInfrastrukturySpolecznej ") - 1
