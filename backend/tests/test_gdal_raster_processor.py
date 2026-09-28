"""Adapter GDAL/PyMuPDF: render źródła, budowa i walidacja COG, izolacja procesu.

Testy uruchamiają prawdziwe ``gdal-bin`` z obrazu backendu (bez sieci) i są
pomijane tylko wtedy, gdy narzędzi GDAL nie ma w środowisku.
"""

from __future__ import annotations

import io
import subprocess

import fitz
import pytest
from PIL import Image

from app.modules.imports.application.raster_import import RasterSource
from app.modules.imports.domain.raster import ControlPoint
from app.modules.imports.infrastructure.raster import gdal as gdal_module
from app.modules.imports.infrastructure.raster.gdal import (
    GdalRasterProcessor,
    RasterProcessingError,
)
from tests.terrain_fixtures import make_geotiff, requires_gdal


def _pdf(pages: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(pages):
        document.new_page(width=200, height=100)
    return document.tobytes()


def _png(width: int = 30, height: int = 20) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 10, 10)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_render_pdf_page_to_png_with_dpi_and_limits() -> None:
    processor = GdalRasterProcessor(render_dpi=72)

    rendered = processor.render(RasterSource(content=_pdf(), filename="plan.pdf", media_type="application/pdf"))

    assert rendered.media_type == "image/png"
    assert (rendered.width_px, rendered.height_px) == (200, 100)
    assert rendered.image.startswith(b"\x89PNG")
    with pytest.raises(RasterProcessingError, match="spoza zakresu"):
        processor.render(RasterSource(content=_pdf(), filename="plan.pdf", media_type="application/pdf", page_number=3))
    with pytest.raises(RasterProcessingError, match="limit bezpieczeństwa"):
        GdalRasterProcessor(render_dpi=72, max_render_pixels=10).render(
            RasterSource(content=_pdf(), filename="plan.PDF", media_type="application/octet-stream")
        )
    with pytest.raises(RasterProcessingError, match="otworzyć PDF"):
        processor.render(RasterSource(content=b"not a pdf", filename="x.pdf", media_type="application/pdf"))


def test_render_image_probes_dimensions_and_rejects_garbage() -> None:
    processor = GdalRasterProcessor()

    rendered = processor.render(RasterSource(content=_png(), filename="plan.png", media_type="image/png"))

    assert (rendered.width_px, rendered.height_px) == (30, 20)
    assert rendered.image == _png()
    with pytest.raises(RasterProcessingError, match="obrazu źródłowego"):
        processor.render(RasterSource(content=b"xx", filename="x.png", media_type="image/png"))


@requires_gdal
def test_build_and_validate_cog_from_control_points() -> None:
    processor = GdalRasterProcessor(command_timeout_s=120)
    points = (
        ControlPoint(0, 0, 637000.0, 486020.0),
        ControlPoint(30, 0, 637030.0, 486020.0),
        ControlPoint(0, 20, 637000.0, 486000.0),
        ControlPoint(30, 20, 637030.0, 486000.0),
    )

    for method in ("gcp_affine", "gcp_tps"):
        cog = processor.build_cog(
            _png(), control_points=points, dst_crs="EPSG:2180", transform_method=method
        )
        validation = processor.validate_cog(cog)
        assert validation.valid, validation.messages

    with pytest.raises(RasterProcessingError, match="3 punktów"):
        processor.build_cog(_png(), control_points=points[:2], dst_crs="EPSG:2180", transform_method="gcp_affine")


@requires_gdal
def test_validate_cog_reports_layout_crs_and_unreadable_input() -> None:
    processor = GdalRasterProcessor(command_timeout_s=60)
    plain = make_geotiff([[1.0, 2.0], [3.0, 4.0]], origin_x=0, origin_y=2)

    not_cog = processor.validate_cog(plain)
    garbage = processor.validate_cog(b"not a raster")

    assert not not_cog.valid
    assert any("COG" in message for message in not_cog.messages)
    assert not garbage.valid


@requires_gdal
def test_validate_cog_rejects_invalid_json_and_missing_crs(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = GdalRasterProcessor(command_timeout_s=60)
    monkeypatch.setattr(GdalRasterProcessor, "_run", lambda self, command: "{not json")
    assert not processor.validate_cog(b"x").valid
    monkeypatch.setattr(
        GdalRasterProcessor,
        "_run",
        lambda self, command: '{"metadata": {"IMAGE_STRUCTURE": {"LAYOUT": "COG"}}}',
    )
    result = processor.validate_cog(b"x")
    assert not result.valid and any("układu współrzędnych" in item for item in result.messages)


def test_run_maps_missing_tool_timeout_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = GdalRasterProcessor(command_timeout_s=1)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("gdalinfo")

    def slow(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="gdalinfo", timeout=1)

    def failing(*_args, **kwargs):
        # Proces dostaje minimalne środowisko bez sekretów aplikacji.
        assert "DATABASE_URL" not in kwargs["env"]
        assert kwargs["env"]["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] == ".none"
        return subprocess.CompletedProcess(args=["gdalinfo"], returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    for fake, message in ((missing, "Brak narzędzia"), (slow, "limit czasu"), (failing, "boom")):
        monkeypatch.setattr(gdal_module.subprocess, "run", fake)
        with pytest.raises(RasterProcessingError, match=message):
            processor.gdal_version()


def test_decoder_helpers_parse_geotransform_epsg_and_nodata() -> None:
    assert GdalRasterProcessor._epsg({"coordinateSystem": {"wkt": 'PROJCRS[..., ID["EPSG",2180]]'}}) == 2180
    assert GdalRasterProcessor._epsg({"coordinateSystem": {"wkt": 'ID["EPSG",abc]'}}) is None
    assert GdalRasterProcessor._epsg({}) is None
    assert GdalRasterProcessor._float_or_none("-9999") == -9999.0
    assert GdalRasterProcessor._float_or_none("nan") != GdalRasterProcessor._float_or_none("nan")
    assert GdalRasterProcessor._float_or_none(None) is None
    assert GdalRasterProcessor._float_or_none("x") is None
    with pytest.raises(RasterProcessingError):
        GdalRasterProcessor._geotransform({})


def test_decoder_rejects_unexpected_gdalinfo_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = GdalRasterProcessor(command_timeout_s=1)
    tiff_header = b"II*\x00" + b"\x00" * 16
    payloads = (
        "[]",
        "{bad",
        '{"driverShortName": "PNG"}',
        '{"driverShortName": "GTiff", "bands": [{"type": "Float32"}], "size": [0, 1]}',
    )
    for payload in payloads:
        monkeypatch.setattr(GdalRasterProcessor, "_run", lambda self, command, p=payload: p)
        with pytest.raises(RasterProcessingError):
            processor.read_float_band(tiff_header, max_bytes=100)


@requires_gdal
def test_decoder_detects_inconsistent_export(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = GdalRasterProcessor(command_timeout_s=60)
    tiff = make_geotiff([[1.0, 2.0], [3.0, 4.0]], origin_x=0, origin_y=2)
    real_run = GdalRasterProcessor._run

    def truncated(self, command):
        output = real_run(self, command)
        if command[0] == "gdal_translate":
            target = command[-1]
            with open(target, "r+b") as handle:
                handle.truncate(4)
        return output

    monkeypatch.setattr(GdalRasterProcessor, "_run", truncated)
    with pytest.raises(RasterProcessingError, match="niespójny"):
        processor.read_float_band(tiff, max_bytes=10**6)
