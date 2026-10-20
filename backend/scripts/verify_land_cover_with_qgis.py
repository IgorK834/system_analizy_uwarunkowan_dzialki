#!/usr/bin/env python3
"""Niezależny odbiór AU-202–204 w PyQGIS, bez aplikacji, Shapely i PostGIS.

QGIS/OGR czyta zamrożone GML; QgsGeometry mierzy w EPSG:2180. Uruchomić
systemowym Pythonem oficjalnego obrazu QGIS z QT_QPA_PLATFORM=offscreen.
"""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from qgis.core import Qgis, QgsApplication, QgsGeometry, QgsVectorLayer  # type: ignore[import-not-found]


def parcel_geometry(geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    parts = []
    for polygon in polygons:
        rings = ["(" + ",".join(f"{x} {y}" for x, y, *_ in ring) + ")" for ring in polygon]
        parts.append("(" + ",".join(rings) + ")")
    return QgsGeometry.fromWkt("MULTIPOLYGON(" + ",".join(parts) + ")")


def read_layers(package):
    result = {}
    with zipfile.ZipFile(package) as archive:
        for name in archive.namelist():
            code = name.rsplit("__", 1)[-1].removesuffix(".xml")
            if not (code.startswith("OT_PT") or code in {"OT_SKJZ_L", "OT_BUBD_A"}):
                continue
            # Pusta kolekcja GML bez osadzonego XSD nie tworzy warstwy OGR.
            if len(ElementTree.fromstring(archive.read(name))) == 0:
                result[code] = []
                continue
            layer = QgsVectorLayer(f"/vsizip/{package}/{name}|layername={code}", code, "ogr")
            if not layer.isValid():
                raise ValueError(f"QGIS nie odczytał {code}")
            assert layer.crs().authid() == "EPSG:2180", layer.crs().authid()
            fields = layer.fields().names()
            result[code] = [
                ({key: feature[key] for key in fields}, QgsGeometry(feature.geometry()))
                for feature in layer.getFeatures()
            ]
    return result


def verify(fixtures):
    rows = []
    for county in ("1261", "2816", "1418"):
        folder = fixtures / county
        target = json.loads((folder / "expected.json").read_text())
        package = folder / f"{county}_GML.zip"
        assert hashlib.sha256(package.read_bytes()).hexdigest() == target["fixture_sha256"]
        layers = read_layers(package.resolve())
        roads = [(a, g) for a, g in layers["OT_SKJZ_L"] if a["kategoriaIstnienia"] == "eksploatowany"]
        buildings = [(a, g) for a, g in layers["OT_BUBD_A"] if a["kategoriaIstnienia"] == "eksploatowany"]
        forests = [g for a, g in layers["OT_PTLZ_A"] if a["rodzaj"] == "las"]
        forest_union = QgsGeometry.unaryUnion(forests)
        cover = {code: QgsGeometry.unaryUnion([g for _, g in features])
                 for code, features in layers.items() if code.startswith("OT_PT") and features}
        for parcel_id, expected in target["cases"].items():
            parcel = parcel_geometry(expected["parcel"])
            nearest = min(roads, key=lambda pair: (parcel.distance(pair[1]), str(pair[0]["lokalnyId"])))
            road_distance = parcel.distance(nearest[1])
            relation = "touches" if parcel.touches(nearest[1]) else "intersects" if parcel.intersects(nearest[1]) else "disjoint"
            assert nearest[0]["lokalnyId"] == expected["road"]["id"], parcel_id
            assert relation == expected["road"]["relation"], parcel_id
            road_delta = abs(road_distance - expected["road"]["distance_m"])
            assert road_delta <= 0.05, (parcel_id, road_delta)
            forest_distance = min(parcel.distance(g) for g in forests)
            forest_delta = abs(forest_distance - expected["forest_distance_m"])
            assert forest_delta <= 0.05, (parcel_id, forest_delta)
            actual_buildings = {str(a["lokalnyId"]): parcel.distance(g) for a, g in buildings if parcel.distance(g) <= 50}
            assert set(actual_buildings) == {b["id"] for b in expected["buildings_50m"]}, parcel_id
            building_delta = max((abs(actual_buildings[b["id"]] - b["distance_m"]) for b in expected["buildings_50m"]), default=0)
            assert building_delta <= 0.05, (parcel_id, building_delta)
            area_deltas, sums = [], {}
            for label, zone in (("parcel", parcel), ("surroundings", parcel.buffer(200, 16).difference(parcel))):
                classes = {code: geom.intersection(zone).area() for code, geom in cover.items() if geom.intersection(zone).area() > 0}
                assert set(classes) == set(expected["zones"][label]["classes"]), (parcel_id, label)
                sums[label] = sum(classes.values()) / zone.area() * 100
                assert abs(sums[label] - 100) <= 0.01, (parcel_id, label, sums[label])
                for code, area in classes.items():
                    area_deltas.append(abs(area - expected["zones"][label]["classes"][code]["area_sqm"]))
            for radius in (8, 12, 16):
                area = parcel.intersection(forest_union.buffer(radius, 16)).area()
                area_deltas.append(abs(area - expected["forest_buffers"][str(radius)]))
            assert max(area_deltas) <= 0.05, (parcel_id, max(area_deltas))
            rows.append({"parcel_identifier": parcel_id, "county": county, "road_distance_m": road_distance,
                         "road_relation": relation, "road_delta_m": road_delta, "forest_distance_m": forest_distance,
                         "forest_delta_m": forest_delta, "buildings_50m": len(actual_buildings),
                         "building_max_delta_m": building_delta, "area_max_delta_sqm": max(area_deltas),
                         "share_sums_pct": sums, "fixture_sha256": target["fixture_sha256"]})
    return {"tool": "PyQGIS/OGR", "qgis_version": Qgis.QGIS_VERSION, "crs": "EPSG:2180", "passed": True,
            "distance_tolerance_m": 0.05, "area_tolerance_sqm": 0.05, "share_tolerance_pp": 0.01, "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    application = QgsApplication([], False)
    application.initQgis()
    result = verify(args.fixtures)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"QGIS {result['qgis_version']}: {len(result['cases'])} działek, wszystkie porównania w tolerancji.")
    application.exitQgis()


if __name__ == "__main__":
    main()
