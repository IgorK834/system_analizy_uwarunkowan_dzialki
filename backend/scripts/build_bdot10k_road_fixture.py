"""Buduje zamrożony wycinek BDOT10k (drogi) dla testów kontekstu drogowego (BK-305).

Wejście: oficjalna paczka powiatowa BDOT10k w schemacie 2021, np.
``https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip``
(adres podaje atrybut ``URL_GML`` warstwy ``ms:BDOT10k_powiaty`` usługi WFS
``PZGIK/BDOT/WFS/PobieranieBDOT10k``).

Wyjście (katalog ``--output``):

* ``OT_SKJZ_L.xml`` — jezdnie (osie) w oryginalnym schemacie GML, wyłącznie
  obiekty w promieniu ``--radius`` od działek korpusu referencyjnego;
* ``OT_ADJA_A.xml`` — obiekt ``powiat`` z paczki (zasięg wydania);
* ``expected.json`` — wartości oczekiwane policzone NIEZALEŻNIE od aplikacji:
  współrzędne czytane wprost z ``gml:posList`` (bez GDAL/PostGIS), odległość i
  relacja liczone Shapely (GEOS) w EPSG:2180. Te same wartości służą do ręcznej
  kontroli w QGIS (``docs/data_sources/road_contracts.md``).

Skrypt nie łączy się z siecią; paczkę pobiera się ręcznie (``curl -O``).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from shapely.geometry import LineString, MultiLineString, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

GML = "http://www.opengis.net/gml/3.2"
OT = "urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0"
_NAMESPACES = {
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xlink": "http://www.w3.org/1999/xlink",
    "gml": GML,
    "ot": OT,
}
_INCLUDED_EXISTENCE = {None, "eksploatowany"}


def _register_namespaces() -> None:
    for prefix, uri in _NAMESPACES.items():
        ElementTree.register_namespace(prefix, uri)


def _text(feature: ElementTree.Element, name: str) -> str | None:
    element = feature.find(f"{{{OT}}}{name}")
    return element.text.strip() if element is not None and element.text else None


def _line_geometry(feature: ElementTree.Element) -> BaseGeometry:
    parts = []
    for pos_list in feature.iter(f"{{{GML}}}posList"):
        values = [float(value) for value in (pos_list.text or "").split()]
        parts.append(list(zip(values[0::2], values[1::2])))
    if len(parts) == 1:
        return LineString(parts[0])
    return MultiLineString(parts)


def _read_member(archive: zipfile.ZipFile, suffix: str) -> tuple[bytes, str]:
    name = next(item for item in archive.namelist() if item.endswith(suffix))
    return archive.read(name), name


def _parcels(corpus_dir: Path, teryt_prefix: str) -> dict[str, BaseGeometry]:
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    parcels: dict[str, BaseGeometry] = {}
    for case in manifest["cases"]:
        if not str(case["teryt"]).startswith(teryt_prefix):
            continue
        path = corpus_dir / "artifacts" / "parcels" / f"{case['case_id']}.geojson"
        feature = json.loads(path.read_text(encoding="utf-8"))
        parcels[case["case_id"]] = shape(feature["geometry"])
    return parcels


def build(package: Path, corpus_dir: Path, output: Path, teryt: str, radius: float) -> dict:
    _register_namespaces()
    package_bytes = package.read_bytes()
    parcels = _parcels(corpus_dir, teryt)
    if not parcels:
        raise SystemExit(f"Korpus nie zawiera działek z TERYT {teryt}.")
    search_area = unary_union([geometry.buffer(radius) for geometry in parcels.values()])

    with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
        roads_bytes, roads_name = _read_member(archive, "__OT_SKJZ_L.xml")
        admin_bytes, admin_name = _read_member(archive, "__OT_ADJA_A.xml")

    roads_root = ElementTree.fromstring(roads_bytes)
    kept_roads: list[tuple[ElementTree.Element, BaseGeometry]] = []
    for member in list(roads_root):
        feature = next(iter(member))
        geometry = _line_geometry(feature)
        if geometry.intersects(search_area):
            kept_roads.append((member, geometry))
        else:
            roads_root.remove(member)

    admin_root = ElementTree.fromstring(admin_bytes)
    for member in list(admin_root):
        feature = next(iter(member))
        if not (_text(feature, "rodzaj") == "powiat" and _text(feature, "identyfikatorTERYTjednostki") == teryt):
            admin_root.remove(member)
    if len(admin_root) != 1:
        raise SystemExit("Paczka nie zawiera jednoznacznego obiektu powiatu.")

    output.mkdir(parents=True, exist_ok=True)
    for root, filename in ((roads_root, "OT_SKJZ_L.xml"), (admin_root, "OT_ADJA_A.xml")):
        ElementTree.ElementTree(root).write(
            output / filename, encoding="UTF-8", xml_declaration=True
        )

    expected: dict[str, dict] = {}
    for case_id, parcel in sorted(parcels.items()):
        candidates = []
        for member, geometry in kept_roads:
            feature = next(iter(member))
            if _text(feature, "kategoriaIstnienia") not in _INCLUDED_EXISTENCE:
                continue
            intersects = geometry.intersects(parcel)
            touches = geometry.touches(parcel)
            relation = "touches" if touches else "intersects" if intersects else "disjoint"
            rank = {"intersects": 0, "touches": 1, "disjoint": 2}[relation]
            candidates.append(
                (
                    round(parcel.distance(geometry), 6),
                    rank,
                    _text(feature, "lokalnyId"),
                    relation,
                    _text(feature, "kategoriaZarzadzania"),
                    _text(feature, "klasaDrogi"),
                )
            )
        distance, _rank, road_id, relation, category, road_class = min(candidates)
        expected[case_id] = {
            "road_id": road_id,
            "distance_m": round(distance, 2),
            "relation": relation,
            "category": category,
            "road_class": road_class,
            "intersecting_road_count": sum(1 for item in candidates if item[3] == "intersects"),
            "touching_road_count": sum(1 for item in candidates if item[3] == "touches"),
        }

    metadata = {
        "package": package.name,
        "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
        "package_members": [roads_name, admin_name],
        "teryt": teryt,
        "radius_m": radius,
        "road_feature_count": len(kept_roads),
        "method": (
            "gml:posList czytane bez GDAL; odległość/relacja Shapely (GEOS) w "
            "EPSG:2180 od poligonu działki ULDK do osi jezdni OT_SKJZ_L; "
            "kategoriaIstnienia w {eksploatowany, brak}."
        ),
        "cases": expected,
    }
    (output / "expected.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "reference_corpus",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--teryt", default="1261")
    parser.add_argument("--radius", type=float, default=600.0)
    args = parser.parse_args()
    metadata = build(args.package, args.corpus, args.output, args.teryt, args.radius)
    print(json.dumps(metadata["cases"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
