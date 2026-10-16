"""Buduje zamrożone wycinki paczek powiatowych BDOT10k (drogi, budynki, zasięg powiatu) dla testów BK-304/BK-305.

Wejście: katalog z oryginalnymi paczkami (``--packages``, układ ``<katalog>/<powiat>_GML.zip`` albo
``<katalog>/bdot10k_<powiat>/<powiat>_GML.zip``), pobranymi ręcznie z
``https://opendata.geoportal.gov.pl/bdot10k/schemat2021/<woj>/<powiat>_GML.zip``, oraz korpus referencyjny.

Wyjście (``--output``/``<powiat>/``):

* ``<powiat>_GML.zip`` — mini-paczka w układzie oryginału (te same nazwy plików GML) z obiektami w promieniu
  ``--road-radius`` (osie jezdni) i ``--building-radius`` (budynki) od działek korpusu oraz z jednym obiektem
  ``powiat`` z ``OT_ADJA_A`` (zasięg wydania). Obiekty są dosłownymi fragmentami oryginalnego GML; ZIP jest
  deterministyczny (stałe znaczniki czasu), więc ten sam komplet danych daje ten sam SHA-256;
* ``expected.json`` — wartości oczekiwane policzone NIEZALEŻNIE od aplikacji: współrzędne czytane wprost z
  ``gml:posList`` (bez GDAL, PostGIS i kodu aplikacji), odległości/relacje/pola liczone Shapely (GEOS) w EPSG:2180
  według definicji z ``docs/data_sources/building_contracts.md`` i ``road_contracts.md``.

Skrypt nie łączy się z siecią. Uruchamiać na hoście: ``python3 -I backend/scripts/build_bdot10k_fixtures.py …``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import statistics
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from shapely.geometry import LineString, MultiLineString, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

GML = "http://www.opengis.net/gml/3.2"
OT = "urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XLINK = "http://www.w3.org/1999/xlink"
_WRAP = f'<r xmlns:gml="{GML}" xmlns:ot="{OT}" xmlns:xsi="{XSI}" xmlns:xlink="{XLINK}">'.encode()
_MEMBER = re.compile(rb"<gml:featureMember>.*?</gml:featureMember>", re.DOTALL)
_POSLIST = re.compile(rb"<gml:posList>([^<]*)</gml:posList>")

ROAD_RADII_M = (25.0, 50.0, 100.0, 250.0, 500.0)
BUFFERS_M = (50.0, 100.0, 200.0)
# Parametry definicji pierścienia — muszą być identyczne jak w aplikacji (modules/analysis/domain/buildings.py).
QUAD_SEGS = 16
AREA_EPS_SQM = 0.01
TIE_TOLERANCE_M = 1e-3
IN_OPERATION = "eksploatowany"
ZIP_TIMESTAMP = (2026, 10, 2, 0, 0, 0)


def _find_package(packages: Path, powiat: str) -> Path:
    for candidate in (packages / f"{powiat}_GML.zip", packages / f"bdot10k_{powiat}" / f"{powiat}_GML.zip"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Brak paczki {powiat}_GML.zip w {packages}.")


def _member_name(archive: zipfile.ZipFile, suffix: str) -> str:
    return next(name for name in archive.namelist() if name.endswith(suffix))


def _split(data: bytes) -> tuple[bytes, list[bytes], bytes]:
    members = list(_MEMBER.finditer(data))
    if not members:
        return data, [], b""
    return data[: members[0].start()], [match.group(0) for match in members], data[members[-1].end() :]


def _bbox(member: bytes) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for match in _POSLIST.finditer(member):
        values = [float(item) for item in match.group(1).split()]
        xs.extend(values[0::2])
        ys.extend(values[1::2])
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _text(feature: ElementTree.Element, name: str) -> str | None:
    element = feature.find(f"{{{OT}}}{name}")
    return element.text.strip() if element is not None and element.text else None


def _feature(member: bytes) -> ElementTree.Element:
    root = ElementTree.fromstring(_WRAP + member + b"</r>")
    return next(iter(next(iter(root))))


def _pairs(text: str) -> list[tuple[float, float]]:
    values = [float(item) for item in text.split()]
    return list(zip(values[0::2], values[1::2]))


def _line_geometry(feature: ElementTree.Element) -> BaseGeometry:
    parts = [LineString(_pairs(item.text or "")) for item in feature.iter(f"{{{GML}}}posList")]
    return parts[0] if len(parts) == 1 else MultiLineString(parts)


def _polygon_geometry(feature: ElementTree.Element) -> BaseGeometry:
    polygons: list[Polygon] = []
    for polygon in feature.iter(f"{{{GML}}}Polygon"):
        exterior = polygon.find(f"{{{GML}}}exterior//{{{GML}}}posList")
        holes = [item.find(f".//{{{GML}}}posList") for item in polygon.findall(f"{{{GML}}}interior")]
        polygons.append(
            Polygon(_pairs(exterior.text or ""), [_pairs(hole.text or "") for hole in holes if hole is not None])
        )
    return unary_union(polygons) if len(polygons) != 1 else polygons[0]


def _parcels(corpus_dir: Path, powiat: str) -> dict[str, BaseGeometry]:
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    parcels: dict[str, BaseGeometry] = {}
    for case in manifest["cases"]:
        if not str(case["teryt"]).startswith(powiat):
            continue
        path = corpus_dir / "artifacts" / "parcels" / f"{case['case_id']}.geojson"
        parcels[case["case_id"]] = shape(json.loads(path.read_text(encoding="utf-8"))["geometry"])
    return parcels


def _select(
    members: list[bytes], area: BaseGeometry, geometry_of
) -> list[tuple[bytes, ElementTree.Element, BaseGeometry]]:
    minx, miny, maxx, maxy = area.bounds
    kept = []
    for member in members:
        box = _bbox(member)
        if box is None or box[2] < minx or box[0] > maxx or box[3] < miny or box[1] > maxy:
            continue
        feature = _feature(member)
        geometry = geometry_of(feature)
        if geometry.intersects(area):
            kept.append((member, feature, geometry))
    return kept


def _road_expected(parcel: BaseGeometry, roads) -> dict:
    candidates = []
    for _member, feature, geometry in roads:
        if _text(feature, "kategoriaIstnienia") != IN_OPERATION:
            continue
        intersects, touches = geometry.intersects(parcel), geometry.touches(parcel)
        relation = "touches" if touches else "intersects" if intersects else "disjoint"
        rank = {"intersects": 0, "touches": 1, "disjoint": 2}[relation]
        candidates.append(
            (parcel.distance(geometry), rank, _text(feature, "lokalnyId"), relation, feature)
        )
    result: dict = {"status": "not_found_within_radius", "max_radius_m": ROAD_RADII_M[-1]}
    for radius in ROAD_RADII_M:
        within = [item for item in candidates if item[0] <= radius]
        if not within:
            continue
        best = min(within, key=lambda item: (round(item[0] / TIE_TOLERANCE_M), item[1], item[2]))
        distance, _rank, road_id, relation, feature = best
        result = {
            "status": "available",
            "search_radius_m": radius,
            "distance_m": round(distance, 2),
            "relation": relation,
            "road_id": road_id,
            "management_category": _text(feature, "kategoriaZarzadzania"),
            "road_class": _text(feature, "klasaDrogi"),
            "road_number": _text(feature, "numerDrogi"),
            "intersecting_road_count": sum(1 for item in candidates if item[3] == "intersects"),
            "touching_road_count": sum(1 for item in candidates if item[3] == "touches"),
        }
        break
    return result


def _building_expected(parcel: BaseGeometry, buildings) -> dict:
    live = [
        (_text(feature, "lokalnyId"), geometry)
        for _member, feature, geometry in buildings
        if _text(feature, "kategoriaIstnienia") == IN_OPERATION
    ]
    on_parcel = [
        (building_id, geometry.intersection(parcel).area)
        for building_id, geometry in live
        if geometry.intersection(parcel).area > AREA_EPS_SQM
    ]
    covered = unary_union([geometry.intersection(parcel) for _id, geometry in live if geometry.intersection(parcel).area > AREA_EPS_SQM])
    result: dict = {
        "on_parcel": {
            "count": len(on_parcel),
            "union_area_in_parcel_sqm": round(covered.area, 4),
            "coverage_pct": round(100.0 * covered.area / parcel.area, 4),
        },
        "buffers": {},
    }
    for radius in BUFFERS_M:
        ring = parcel.buffer(radius, quad_segs=QUAD_SEGS).difference(parcel)
        counted = []
        for building_id, geometry in live:
            clipped = geometry.intersection(ring)
            if clipped.area > AREA_EPS_SQM:
                counted.append((building_id, geometry, clipped))
        full_areas = [geometry.area for _id, geometry, _clip in counted]
        union = unary_union([clip for _id, _geometry, clip in counted]) if counted else None
        result["buffers"][str(int(radius))] = {
            "count": len(counted),
            "straddling_parcel_count": sum(
                1 for _id, geometry, _clip in counted if geometry.intersection(parcel).area > AREA_EPS_SQM
            ),
            "ring_area_sqm": round(ring.area, 4),
            "footprint_area_in_ring_sqm": round(sum(clip.area for _id, _g, clip in counted), 4),
            "union_area_in_ring_sqm": round(union.area, 4) if union is not None else 0.0,
            "built_share_pct": round(100.0 * union.area / ring.area, 4) if union is not None else 0.0,
            "min_distance_m": round(min(parcel.distance(g) for _id, g, _c in counted), 2) if counted else None,
            "footprint_min_sqm": round(min(full_areas), 4) if counted else None,
            "footprint_median_sqm": round(statistics.median(full_areas), 4) if counted else None,
            "footprint_max_sqm": round(max(full_areas), 4) if counted else None,
        }
    return result


def _deterministic_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name], compresslevel=9)
    return buffer.getvalue()


def build(package: Path, corpus: Path, output: Path, powiat: str, road_radius: float, building_radius: float) -> dict:
    parcels = _parcels(corpus, powiat)
    if not parcels:
        raise SystemExit(f"Korpus nie zawiera działek powiatu {powiat}.")
    road_area = unary_union([item.buffer(road_radius + 0.5) for item in parcels.values()])
    building_area = unary_union([item.buffer(building_radius + 0.5) for item in parcels.values()])
    source_bytes = package.read_bytes()
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
        road_name = _member_name(archive, "__OT_SKJZ_L.xml")
        building_name = _member_name(archive, "__OT_BUBD_A.xml")
        admin_name = _member_name(archive, "__OT_ADJA_A.xml")
        road_head, road_members, road_tail = _split(archive.read(road_name))
        building_head, building_members, building_tail = _split(archive.read(building_name))
        admin_head, admin_members, admin_tail = _split(archive.read(admin_name))

    roads = _select(road_members, road_area, _line_geometry)
    buildings = _select(building_members, building_area, _polygon_geometry)
    admin = [
        member
        for member in admin_members
        if _text(_feature(member), "rodzaj") == "powiat" and _text(_feature(member), "identyfikatorTERYTjednostki") == powiat
    ]
    if len(admin) != 1:
        raise SystemExit("Paczka nie zawiera jednoznacznego obiektu powiatu.")

    def assemble(head: bytes, members: list[bytes], tail: bytes) -> bytes:
        return head + b"\n".join(members) + tail

    files = {
        road_name: assemble(road_head, [m for m, _f, _g in roads], road_tail),
        building_name: assemble(building_head, [m for m, _f, _g in buildings], building_tail),
        admin_name: assemble(admin_head, admin, admin_tail),
    }
    mini = _deterministic_zip(files)
    versions = [
        _text(feature, "wersja") for _m, feature, _g in (*roads, *buildings) if _text(feature, "wersja")
    ]
    cases = {
        case_id: {
            "parcel_area_sqm": round(parcel.area, 4),
            "roads": _road_expected(parcel, roads),
            "buildings": _building_expected(parcel, buildings),
        }
        for case_id, parcel in sorted(parcels.items())
    }
    metadata = {
        "teryt_powiat": powiat,
        "source_package": {
            "name": package.name,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "size_bytes": len(source_bytes),
        },
        "fixture_package": {"name": f"{powiat}_GML.zip", "sha256": hashlib.sha256(mini).hexdigest(), "size_bytes": len(mini)},
        "members": sorted(files),
        "road_radius_m": road_radius,
        "building_radius_m": building_radius,
        "road_feature_count": len(roads),
        "building_feature_count": len(buildings),
        "latest_version_in_fixture": max(versions) if versions else None,
        "road_search_radii_m": list(ROAD_RADII_M),
        "building_buffers_m": list(BUFFERS_M),
        "buffer_quad_segs": QUAD_SEGS,
        "area_epsilon_sqm": AREA_EPS_SQM,
        "method": (
            "gml:posList czytane bez GDAL i bez kodu aplikacji; odległość/relacja/pola liczone Shapely (GEOS) w EPSG:2180. "
            "Drogi: OT_SKJZ_L o kategoriaIstnienia=eksploatowany, odległość od poligonu działki do osi, wyszukiwanie "
            "rosnącym promieniem. Budynki: OT_BUBD_A o kategoriaIstnienia=eksploatowany, pierścień "
            "buffer(działka, r, quad_segs=16) − działka; budynek liczony, gdy pole jego części w pierścieniu > 0,01 m²."
        ),
        "cases": cases,
    }
    target = output / powiat
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{powiat}_GML.zip").write_bytes(mini)
    (target / "expected.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "reference_corpus",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--powiat", action="append", required=True, help="TERYT powiatu (można powtarzać)")
    parser.add_argument("--road-radius", type=float, default=500.0)
    parser.add_argument("--building-radius", type=float, default=200.0)
    args = parser.parse_args()
    for powiat in args.powiat:
        metadata = build(
            _find_package(args.packages, powiat), args.corpus, args.output, powiat, args.road_radius, args.building_radius
        )
        print(
            powiat,
            f"roads={metadata['road_feature_count']}",
            f"buildings={metadata['building_feature_count']}",
            f"zip={metadata['fixture_package']['size_bytes']}B",
            metadata["fixture_package"]["sha256"][:12],
        )


if __name__ == "__main__":
    main()
