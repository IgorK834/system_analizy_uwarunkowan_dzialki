"""Czytnik paczki powiatowej BDOT10k: ZIP → trzy pliki GML → obiekty z geometrią WKB (EPSG:2180).

Paczka jest niezaufana: rozpakowanie idzie przez ``app.shared.safe_archive`` (Zip Slip, limity rozmiaru i
współczynnika kompresji), tylko trzy potrzebne pliki trafiają do osobnego katalogu tymczasowego, a GML czyta pyogrio
(GDAL) z jawną listą kolumn — pola tekstu wolnego w ogóle nie opuszczają pliku. Układ musi być zadeklarowany jako
EPSG:2180; importer nie zgaduje CRS.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pyproj import CRS
from pyproj.exceptions import CRSError

from app.modules.imports.application.bdot10k_import import Bdot10kBatch
from app.modules.imports.domain.bdot10k import (
    BOUNDARY_FIELD_KEYS,
    BOUNDARY_LAYER,
    BUILDING_FIELD_KEYS,
    BUILDING_LAYER,
    REQUIRED_LAYERS,
    LAND_COVER_LAYERS,
    ROAD_FIELD_KEYS,
    ROAD_LAYER,
    Bdot10kValidationError,
    RawFeature,
    validate_mapping,
    validate_powiat_teryt,
)
from app.shared.safe_archive import ArchiveLimits, extract_zip
from app.modules.imports.domain.powerlines import POWERLINE_LAYER

MB = 1024 * 1024
# Realne paczki: 51–792 MB po rozpakowaniu, współczynnik kompresji 15–31. Limity zostawiają zapas, ale
# nadal odrzucają zip bomb.
BDOT10K_ARCHIVE_LIMITS = ArchiveLimits(
    max_entries=200,
    max_total_uncompressed_bytes=3 * 1024 * MB,
    max_entry_uncompressed_bytes=1024 * MB,
    max_compression_ratio=100.0,
    compression_ratio_floor_bytes=4 * 1024,
)
_MEMBER_SUFFIXES = tuple(f"__{layer}.xml".lower() for layer in (*REQUIRED_LAYERS, *LAND_COVER_LAYERS, POWERLINE_LAYER))
_MEMBER_TERYT = re.compile(r"\.(\d{4})__ot_[a-z]{4}_[a-z]\.xml$", re.IGNORECASE)
_CANONICAL_CRS = "EPSG:2180"
class Bdot10kReadError(RuntimeError):
    """Paczka nie da się odczytać (uszkodzone GML, brak klasy, niezgodny układ współrzędnych)."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(MB), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ZipBdot10kReader:
    """Czyta ZIP z dysku; cały odczyt dzieje się w katalogu tymczasowym usuwanym po zakończeniu."""

    def __init__(
        self,
        package: str | Path,
        *,
        teryt_powiat: str,
        mapping: Mapping[str, str],
        limits: ArchiveLimits = BDOT10K_ARCHIVE_LIMITS,
    ):
        self._package = Path(package)
        self._teryt = validate_powiat_teryt(teryt_powiat)
        self._mapping = validate_mapping(mapping)
        self._limits = limits

    def _columns(self, keys: tuple[str, ...]) -> tuple[str, ...]:
        # Do GML wchodzą wyłącznie kolumny z mapowania katalogu; tekst wolny nie ma klucza, więc nie jest czytany.
        return tuple(dict.fromkeys(self._mapping[key] for key in keys))

    def read(self) -> Bdot10kBatch:
        if not self._package.is_file():
            raise Bdot10kReadError(f"Plik paczki {self._package.name!r} nie istnieje.")
        sha = sha256_of(self._package)
        with tempfile.TemporaryDirectory(prefix="dzialki-bdot10k-") as temporary:
            extracted = extract_zip(
                self._package, temporary, limits=self._limits, allowed_suffixes=_MEMBER_SUFFIXES
            )
            by_layer = self._members_by_layer(extracted)
            return Bdot10kBatch(
                package_path=self._package,
                package_name=self._package.name,
                package_sha256=sha,
                size_bytes=self._package.stat().st_size,
                teryt_powiat=self._teryt,
                powerlines=(_read_layer(by_layer[POWERLINE_LAYER], self._columns(("local_id", "feature_version", "existence_category")) + ("rodzaj",), POWERLINE_LAYER)
                            if POWERLINE_LAYER in by_layer else None),
                land_cover={layer: _read_layer(path, (self._mapping["local_id"], self._mapping["feature_version"], self._mapping.get("land_cover_kind", "rodzaj")), layer)
                            for layer, path in by_layer.items() if layer in LAND_COVER_LAYERS},
                roads=_read_layer(by_layer[ROAD_LAYER], self._columns(ROAD_FIELD_KEYS), ROAD_LAYER),
                buildings=_read_layer(by_layer[BUILDING_LAYER], self._columns(BUILDING_FIELD_KEYS), BUILDING_LAYER),
                boundaries=_read_layer(by_layer[BOUNDARY_LAYER], self._columns(BOUNDARY_FIELD_KEYS), BOUNDARY_LAYER),
            )

    def _members_by_layer(self, extracted: list[Path]) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for path in extracted:
            for layer in (*REQUIRED_LAYERS, *LAND_COVER_LAYERS, POWERLINE_LAYER):
                if path.name.lower().endswith(f"__{layer}.xml".lower()):
                    found[layer] = path
            match = _MEMBER_TERYT.search(path.name)
            if match and match.group(1) != self._teryt:
                raise Bdot10kValidationError(
                    f"Paczka zawiera dane powiatu {match.group(1)}, a polecenie wskazuje {self._teryt}."
                )
        missing = [layer for layer in REQUIRED_LAYERS if layer not in found]
        if missing:
            raise Bdot10kReadError(f"W paczce brakuje klas obiektów: {', '.join(missing)}.")
        return found


def _normalize_crs(value: Any) -> str | None:
    if not value:
        return None
    try:
        epsg = CRS.from_user_input(value).to_epsg()
    except (CRSError, TypeError, ValueError):
        return None
    return f"EPSG:{epsg}" if epsg is not None else None


def _scalar(value: Any) -> Any:
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            return value
    return value


def _read_layer(path: Path, columns: tuple[str, ...], layer: str) -> tuple[RawFeature, ...]:
    try:
        from pyogrio import read_info
        from pyogrio.raw import read
    except ImportError as exc:  # pragma: no cover - błąd obrazu
        raise Bdot10kReadError("Brak pyogrio. Zbuduj ponownie obraz backendu.") from exc
    from xml.etree import ElementTree
    with path.open("rb") as stream:
        starts = ElementTree.iterparse(stream, events=("start",))
        _, root = next(starts)
        if next(starts, None) is None and root.tag == "{http://www.opengis.net/gml/3.2}FeatureCollection":
            return ()
    try:
        info = read_info(path)
        available = {str(name) for name in info.get("fields", ())}
        if layer.startswith("OT_PT"):
            required = set(columns if layer == "OT_PTLZ_A" else columns[:2])
            if missing := required - available:
                raise Bdot10kReadError(f"Klasa {layer} nie ma wymaganych pól: {sorted(missing)}")
        wanted = [name for name in columns if name in available]
        metadata, _fids, geometries, field_data = read(path, columns=wanted, force_2d=True)
    except Exception as exc:  # noqa: BLE001 - GDAL zgłasza różne typy błędów
        raise Bdot10kReadError(f"Nie udało się odczytać klasy {layer}: {type(exc).__name__}.") from exc
    crs = _normalize_crs(info.get("crs") or metadata.get("crs"))
    if crs != _CANONICAL_CRS:
        raise Bdot10kReadError(
            f"Klasa {layer} deklaruje układ {crs or 'nieznany'}, a kontrakt wymaga {_CANONICAL_CRS}."
        )
    names = [str(name) for name in metadata.get("fields", wanted)]
    features: list[RawFeature] = []
    for row, wkb in enumerate(geometries):
        attributes: Mapping[str, Any] = {
            name: _scalar(field_data[column][row]) for column, name in enumerate(names)
        }
        features.append(RawFeature(attributes=attributes, geometry_wkb=bytes(wkb) if wkb is not None else None))
    return tuple(features)
