"""Zamraża dosłowne fragmenty GML PT z paczek i liczy oczekiwania bez kodu aplikacji/PostGIS/GDAL.

python backend/scripts/build_land_cover_fixtures.py --packages /tmp/packages --output backend/tests/fixtures/source_contracts/bdot_land_cover
Wymaga geometrii 141801_4.0701.23/8 z ULDK (--parcel-response).
"""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from shapely import from_wkt
from shapely.geometry import mapping
from shapely.ops import unary_union
from build_bdot10k_fixtures import _split, _feature, _text, _polygon_geometry, _parcels, _select, _line_geometry

LAYERS = tuple(
    f"OT_{c}_A"
    for c in ("PTWP", "PTZB", "PTLZ", "PTRK", "PTUT", "PTTR", "PTKM", "PTGN", "PTPL", "PTSO", "PTWZ", "PTNZ")
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parcel-response", type=Path, required=True)
    args = parser.parse_args()
    corpus = Path(__file__).resolve().parents[1] / "tests/fixtures/reference_corpus"
    for county in ("1261", "2816", "1418"):
        parcels = _parcels(corpus, county)
        if county == "1418":
            parcels = {
                "141801_4.0701.23/8": from_wkt(
                    args.parcel_response.read_text().strip().splitlines()[-1].removeprefix("SRID=2180;")
                )
            }
        # Wycinek zawiera pełne niezmienione obiekty do 5 km od każdej działki, w tym najbliższy las.
        area = unary_union([p.buffer(5000, quad_segs=16) for p in parcels.values()])
        source = args.packages / f"{county}_GML.zip"
        dest = args.output / county
        dest.mkdir(parents=True, exist_ok=True)
        expected = {
            "source_url": f"https://opendata.geoportal.gov.pl/bdot10k/schemat2021/{county[:2]}/{county}_GML.zip",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "crs": "EPSG:2180",
            "cases": {},
        }
        covers = []
        buildings = []
        roads = []
        with (
            zipfile.ZipFile(source) as z,
            zipfile.ZipFile(dest / f"{county}_GML.zip", "w", zipfile.ZIP_DEFLATED) as out,
        ):
            for layer in (*LAYERS, "OT_ADJA_A", "OT_BUBD_A", "OT_SKJZ_L"):
                name = next(n for n in z.namelist() if n.endswith(f"__{layer}.xml"))
                header, members, footer = _split(z.read(name))
                geom_of = _line_geometry if layer == "OT_SKJZ_L" else _polygon_geometry
                selection_area = (
                    area
                    if layer == "OT_PTLZ_A"
                    else unary_union(
                        [p.buffer(500 if layer == "OT_SKJZ_L" else 200, quad_segs=16) for p in parcels.values()]
                    )
                )
                selected = _select(members, selection_area, geom_of)
                if layer == "OT_ADJA_A":
                    selected = [
                        (m, _feature(m), _polygon_geometry(_feature(m)))
                        for m in members
                        if _text(_feature(m), "rodzaj") == "powiat"
                    ]
                for _, f, g in selected:
                    if layer in LAYERS:
                        covers.append((layer, _text(f, "rodzaj"), _text(f, "lokalnyId"), g))
                    if layer == "OT_BUBD_A" and _text(f, "kategoriaIstnienia") == "eksploatowany":
                        buildings.append((_text(f, "lokalnyId"), g))
                    if layer == "OT_SKJZ_L" and _text(f, "kategoriaIstnienia") == "eksploatowany":
                        roads.append((_text(f, "lokalnyId"), g))
                info = zipfile.ZipInfo(name, (2026, 10, 17, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                out.writestr(info, header + b"\n".join(m for m, _, _ in selected) + footer)
        for case, p in parcels.items():
            data = {"parcel": mapping(p), "zones": {}}
            for label, zone in [("parcel", p), ("surroundings", p.buffer(200, quad_segs=16).difference(p))]:
                classes = {}
                for code in LAYERS:
                    g = unary_union([g for c, _, _, g in covers if c == code]).intersection(zone)
                    if g.area > 0:
                        classes[code] = {"area_sqm": g.area, "share_pct": g.area / zone.area * 100}
                data["zones"][label] = {"area_sqm": zone.area, "classes": classes}
            forests = [(i, g) for c, k, i, g in covers if c == "OT_PTLZ_A" and k == "las" and p.distance(g) <= 5000]
            nearest = min(forests, key=lambda it: (p.distance(it[1]), it[0]), default=None)
            data["forest_distance_m"] = p.distance(nearest[1]) if nearest else None
            f = unary_union([g for _, g in forests])
            data["forest_buffers"] = {str(r): p.intersection(f.buffer(r, quad_segs=16)).area for r in (8, 12, 16)}
            data["buildings_50m"] = [
                {"id": i, "distance_m": p.distance(g), "area_sqm": g.area} for i, g in buildings if p.distance(g) <= 50
            ]
            nr = min(roads, key=lambda it: (p.distance(it[1]), it[0]))
            data["road"] = {
                "id": nr[0],
                "distance_m": p.distance(nr[1]),
                "relation": "touches" if p.touches(nr[1]) else "intersects" if p.intersects(nr[1]) else "disjoint",
            }
            expected["cases"][case] = data
        expected["fixture_sha256"] = hashlib.sha256((dest / f"{county}_GML.zip").read_bytes()).hexdigest()
        (dest / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n")
        print(county, len(covers), len(buildings), [(c, len(d["buildings_50m"])) for c, d in expected["cases"].items()])


if __name__ == "__main__":
    main()
