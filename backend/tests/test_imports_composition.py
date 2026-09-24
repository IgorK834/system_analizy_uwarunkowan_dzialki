"""Kompozycja komend importu: wybór czytnika, CSW (BK-107) i transakcje.

Import właściwy jest podmieniony — test sprawdza wyłącznie złożenie zależności
z katalogu źródeł i obsługę commit/rollback, bez sieci i bazy.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.modules.imports import composition
from app.modules.imports.application.common import ImportOutcome
from app.modules.imports.infrastructure.mpzp.reader import (
    PyogrioMpzpReader,
    RasterOnlyMpzpReader,
    WfsMpzpReader,
)
from app.modules.imports.infrastructure.pog.reader import PyogrioPogReader, WfsPogReader


def _capture(monkeypatch: pytest.MonkeyPatch, name: str, *, status: str = "succeeded", error=None):
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        if error is not None:
            raise error
        return ImportOutcome(status=status, stats={})

    monkeypatch.setattr(composition, name, fake_run)
    monkeypatch.setattr(composition, "_repository", lambda *_args: object())
    return captured


def test_pog_wfs_reader_gets_catalog_csw_for_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch, "run_pog_import")
    session = MagicMock()

    composition.run_pog_command(
        session, source_id="pog_app", dry_run=True, act_identifier="226401-POG", teryt="226401"
    )

    reader = captured["args"][0]
    assert isinstance(reader, WfsPogReader)
    catalog_csw = next(
        item for item in composition.get_catalog().get("pog_app").resources if item.role == "ru_csw"
    )
    assert reader._csw_url == catalog_csw.url
    session.commit.assert_called_once()


def test_pog_local_resources_keep_raw_official_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _capture(monkeypatch, "run_pog_import", status="dry_run_only")
    session = MagicMock()

    composition.run_pog_command(
        session,
        source_id="pog_pilot_krakow",
        dry_run=True,
        local_resources=(("planning_zone", str(tmp_path / "strefy.gml")),),
        act_identifier="pog-krakow",
        resolution_number="I/1/2026",
        resolution_date=date(2026, 1, 10),
        legal_status="legalForce",
        boundary_path=str(tmp_path / "granica.gml"),
    )

    reader = captured["args"][0]
    assert isinstance(reader, PyogrioPogReader)
    assert reader._metadata.legal_status == "binding"
    assert reader._metadata.raw_legal_status == "legalForce"
    assert reader._boundary is not None
    session.rollback.assert_called_once()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"act_identifier": None, "teryt": "1261011"}, "--act-id"),
        ({"act_identifier": "x", "teryt": "12A"}, "6 albo 7 cyfr"),
    ],
)
def test_pog_command_validates_identifiers(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        composition.run_pog_command(MagicMock(), source_id="pog_pilot_krakow", dry_run=True, **kwargs)


def test_pog_command_without_teryt_and_wildcard_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="TERYT"):
        composition.run_pog_command(
            MagicMock(), source_id="pog_app", dry_run=True, act_identifier="x"
        )


def test_pog_command_rolls_back_on_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch, "run_pog_import", error=RuntimeError("QA"))
    session = MagicMock()
    with pytest.raises(RuntimeError, match="QA"):
        composition.run_pog_command(
            session, source_id="pog_app", dry_run=True, act_identifier="x", teryt="226401"
        )
    session.rollback.assert_called_once()


def test_mpzp_command_selects_local_wfs_and_raster_readers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _capture(monkeypatch, "run_mpzp_import")
    session = MagicMock()

    composition.run_mpzp_command(
        session, source_id="mpzp_pilot_krakow", dry_run=True,
        local_resources=(("zones", str(tmp_path / "zones.gml")),),
    )
    assert isinstance(captured["args"][0], PyogrioMpzpReader)

    composition.run_mpzp_command(session, source_id="mpzp_pilot_krakow", dry_run=True)
    assert isinstance(captured["args"][0], WfsMpzpReader)

    with pytest.raises(ValueError, match="Brak zasobu roli"):
        composition.run_mpzp_command(
            session, source_id="mpzp_pilot_krakow", dry_run=True,
            local_resources=(("nieznana", "x.gml"),),
        )

    with pytest.raises(ValueError, match="jawnych metadanych"):
        composition.run_mpzp_command(session, source_id="kimpzp", dry_run=True)

    composition.run_mpzp_command(
        session, source_id="kimpzp", dry_run=True, act_identifier="mpzp-1",
        resolution_number="I/1", resolution_date=date(2020, 1, 1), raster_teryt="1261011",
    )
    reader = captured["args"][0]
    assert isinstance(reader, RasterOnlyMpzpReader)
    assert session.commit.call_count == 3


def test_mpzp_command_rolls_back_failed_outcome_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch, "run_mpzp_import", status="dry_run_only")
    session = MagicMock()
    composition.run_mpzp_command(session, source_id="mpzp_pilot_krakow", dry_run=True)
    session.rollback.assert_called_once()

    _capture(monkeypatch, "run_mpzp_import", error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        composition.run_mpzp_command(session, source_id="mpzp_pilot_krakow", dry_run=True)
    assert session.rollback.call_count == 2


def test_raster_command_reads_control_points_and_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raster = tmp_path / "plan.TIFF"
    raster.write_bytes(b"II*\x00")
    points = tmp_path / "gcp.json"
    points.write_text(json.dumps([{"pixel": [0, 0], "world": [1, 1]}]), encoding="utf-8")
    captured = _capture(monkeypatch, "run_raster_import")
    monkeypatch.setattr(composition, "SqlAlchemyRasterRepository", lambda *args: object())
    monkeypatch.setattr(composition, "GdalRasterProcessor", lambda: object())
    session = MagicMock()

    composition.run_raster_command(
        session, source_id="kimpzp", dry_run=True, input_path=str(raster),
        control_points_path=str(points), act_version_id=3, transform_method="",
    )

    raster_source = captured["args"][0]
    assert raster_source.media_type == "image/tiff"
    assert captured["kwargs"]["transform_method"] == "gcp_affine"
    assert captured["kwargs"]["planning_act_version_id"] == 3
    session.commit.assert_called_once()

    points.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="listą"):
        composition.run_raster_command(
            session, source_id="kimpzp", dry_run=True, input_path=str(raster),
            control_points_path=str(points),
        )

    points.write_text("[]", encoding="utf-8")
    _capture(monkeypatch, "run_raster_import", error=RuntimeError("gdal"))
    with pytest.raises(RuntimeError):
        composition.run_raster_command(
            session, source_id="kimpzp", dry_run=True, input_path=str(raster),
            control_points_path=str(points),
        )
    session.rollback.assert_called_once()
    assert composition._raster_media_type(Path("a.unknown")) == "application/octet-stream"
