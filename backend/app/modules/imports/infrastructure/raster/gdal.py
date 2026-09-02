"""Adapter operacji rastrowych oparty o CLI GDAL i PyMuPDF.

DECYZJA (gap_5): do budowy i walidacji COG shellujemy do narzędzi ``gdal_translate``,
``gdalwarp`` i ``gdalinfo`` już obecnych w obrazie (``gdal-bin``), zamiast dodawać
``rasterio``/bindings GDAL do ``requirements.txt``. Powody:

* obraz backendu ma już systemowy GDAL (Dockerfile: ``gdal-bin``), a projekt
  świadomie unika kruchych bindings GDAL na ``python:3.13-slim``;
* dodanie ``rasterio`` z własnym wheelem GDAL grozi konfliktem wersji z systemowym
  ``gdal-bin`` używanym też przez inne narzędzia.

DECYZJA (gap_9): każde wywołanie GDAL biegnie w osobnym procesie z twardym
limitem czasu (``subprocess.run(timeout=...)``), co daje izolację i ograniczenie
zasobów, których dostarczyłby ``ProcessPoolExecutor`` workera dokumentów — dlatego
nie duplikujemy tego mechanizmu. Render strony PDF (PyMuPDF) jest pojedynczą,
ograniczoną operacją (jedna strona, limit DPI).
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.modules.imports.application.raster_import import (
    CogValidation,
    RasterSource,
    RenderedRaster,
)
from app.modules.imports.domain.raster import ControlPoint

logger = logging.getLogger(__name__)


class RasterProcessingError(RuntimeError):
    """Operacja rastrowa GDAL/PDF nie powiodła się w sposób kontrolowany."""


@dataclass(frozen=True)
class GdalRasterProcessor:
    """Konkretny adapter: PyMuPDF do renderu PDF, GDAL CLI do COG."""

    render_dpi: int = 200
    command_timeout_s: float = 120.0
    max_render_pixels: int = 40_000_000  # ~ zabezpieczenie przed olbrzymią stroną

    # --- render -------------------------------------------------------------

    def render(self, source: RasterSource) -> RenderedRaster:
        media = source.media_type.lower()
        if "pdf" in media or source.filename.lower().endswith(".pdf"):
            return self._render_pdf(source)
        return self._probe_image(source)

    def _render_pdf(self, source: RasterSource) -> RenderedRaster:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:  # pragma: no cover - błąd obrazu
            raise RasterProcessingError(
                "Brak PyMuPDF. Zbuduj ponownie obraz backendu."
            ) from exc
        try:
            document = fitz.open(stream=source.content, filetype="pdf")
        except Exception as exc:
            raise RasterProcessingError(f"Nie udało się otworzyć PDF: {exc}") from exc
        with document:
            if source.page_number < 0 or source.page_number >= document.page_count:
                raise RasterProcessingError(
                    f"Strona {source.page_number} spoza zakresu dokumentu "
                    f"({document.page_count} stron)."
                )
            page = document[source.page_number]
            zoom = self.render_dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            estimated = (page.rect.width * zoom) * (page.rect.height * zoom)
            if estimated > self.max_render_pixels:
                raise RasterProcessingError(
                    "Rozdzielczość renderu przekracza limit bezpieczeństwa."
                )
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            return RenderedRaster(
                image=pixmap.tobytes("png"),
                media_type="image/png",
                width_px=pixmap.width,
                height_px=pixmap.height,
            )

    def _probe_image(self, source: RasterSource) -> RenderedRaster:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            raise RasterProcessingError("Brak Pillow w obrazie backendu.") from exc
        try:
            with Image.open(io.BytesIO(source.content)) as image:
                width, height = image.size
        except Exception as exc:
            raise RasterProcessingError(
                f"Nie udało się odczytać obrazu źródłowego: {exc}"
            ) from exc
        return RenderedRaster(
            image=source.content,
            media_type=source.media_type,
            width_px=width,
            height_px=height,
        )

    # --- COG ----------------------------------------------------------------

    def build_cog(
        self,
        image: bytes,
        *,
        control_points: Sequence[ControlPoint],
        dst_crs: str,
        transform_method: str,
    ) -> bytes:
        """Buduje COG w ``dst_crs`` z punktów kontrolnych przez GDAL CLI."""
        if len(control_points) < 3:
            raise RasterProcessingError(
                "Georeferencja wymaga co najmniej 3 punktów kontrolnych."
            )
        with tempfile.TemporaryDirectory(prefix="cog-build-") as workdir:
            work = Path(workdir)
            source_path = work / "source.img"
            gcp_path = work / "with_gcp.tif"
            cog_path = work / "output_cog.tif"
            source_path.write_bytes(image)

            gcp_args: list[str] = []
            for point in control_points:
                gcp_args += [
                    "-gcp",
                    f"{point.pixel_col}",
                    f"{point.pixel_row}",
                    f"{point.map_x}",
                    f"{point.map_y}",
                ]
            # 1) Przypnij GCP do obrazu źródłowego.
            self._run(
                [
                    "gdal_translate",
                    "-of",
                    "GTiff",
                    *gcp_args,
                    str(source_path),
                    str(gcp_path),
                ]
            )
            # 2) Reprojekcja do układu docelowego i zapis jako COG z overview.
            method_arg = "-tps" if transform_method == "gcp_tps" else "-order"
            warp_cmd = [
                "gdalwarp",
                "-t_srs",
                dst_crs,
                "-r",
                "near",
                "-of",
                "COG",
                "-co",
                "COMPRESS=DEFLATE",
                "-co",
                "OVERVIEWS=AUTO",
            ]
            if method_arg == "-order":
                warp_cmd += ["-order", "1"]
            else:
                warp_cmd.append("-tps")
            warp_cmd += [str(gcp_path), str(cog_path)]
            self._run(warp_cmd)
            return cog_path.read_bytes()

    def validate_cog(self, cog: bytes) -> CogValidation:
        """Waliduje układ COG przez ``gdalinfo -json`` (LAYOUT=COG, poprawny CRS)."""
        with tempfile.TemporaryDirectory(prefix="cog-validate-") as workdir:
            path = Path(workdir) / "candidate.tif"
            path.write_bytes(cog)
            try:
                result = self._run(["gdalinfo", "-json", str(path)])
            except RasterProcessingError as exc:
                return CogValidation(valid=False, messages=(str(exc),))
            try:
                info = json.loads(result)
            except json.JSONDecodeError as exc:
                return CogValidation(
                    valid=False, messages=(f"gdalinfo zwrócił niepoprawny JSON: {exc}",)
                )
            messages: list[str] = []
            layout = (
                info.get("metadata", {})
                .get("IMAGE_STRUCTURE", {})
                .get("LAYOUT")
            )
            if layout != "COG":
                messages.append("Plik nie ma układu Cloud Optimized (LAYOUT != COG).")
            srs = info.get("coordinateSystem", {}).get("wkt", "")
            if not srs:
                messages.append("Brak zdefiniowanego układu współrzędnych.")
            return CogValidation(valid=not messages, messages=tuple(messages))

    # --- pomocnicze ---------------------------------------------------------

    def _run(self, command: list[str]) -> str:
        try:
            completed = subprocess.run(  # noqa: S603 - stała lista argumentów GDAL
                command,
                capture_output=True,
                timeout=self.command_timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RasterProcessingError(
                f"Brak narzędzia GDAL: {command[0]!r}. Wymagany pakiet gdal-bin."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RasterProcessingError(
                f"Operacja GDAL {command[0]!r} przekroczyła limit czasu."
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")[:500]
            raise RasterProcessingError(
                f"GDAL {command[0]!r} zakończył się błędem: {stderr}"
            )
        return completed.stdout.decode("utf-8", errors="replace")
