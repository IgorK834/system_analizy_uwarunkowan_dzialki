"""Generator zamrożonych fixtures raportu PDF v2 (BK-501–503).

Uruchomienie (w kontenerze backendu, z katalogu ``/app``)::

    python -m tests.fixtures.reports.build_fixtures            # JSON wejściowe
    python -m tests.fixtures.reports.build_fixtures --maps     # + mapy referencyjne

Fixtures są syntetyczne, ale geometrycznie spójne: działka i wszystkie
warstwy są zdefiniowane w ``EPSG:2180`` (rejon Krakowa), a GeoJSON w odpowiedzi
API jest przeliczony do ``EPSG:4326`` — tak jak robi to analiza. Pola i udziały
wynikają z tej samej geometrii (3 strefy po 1/3 działki dają sumę udziałów
99,99% po zaokrągleniu — raport nie może jej „poprawiać” do 100%).

Wynik:
- ``multizone.json`` — 3 strefy POG (akt obowiązujący), 2 strefy MPZP z evidence,
  sprzecznością i wartościami 0/null, OUZ/OZS/OSDIS, ISOK i GDOŚ, NMT;
- ``project.json`` — ten sam układ, akt POG w statusie ``project``;
- ``long_tables.json`` — 32 strefy POG, 36 wpisów evidence z długim polskim
  tekstem, wartości 0 i null;
- ``maps/*.png`` + ``maps/reference.json`` — mapy referencyjne (``--maps``).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

HERE = Path(__file__).resolve().parent
_TO_4326 = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
ANALYZED_AT = datetime(2026, 9, 20, 9, 30, tzinfo=timezone.utc)
FETCHED_AT = "2026-09-20T09:29:00+00:00"
DOC_SHA = "a" * 63 + "1"
DOC_SHA_2 = "b" * 63 + "2"
ARTIFACT_SHA = "c" * 63 + "3"

PARCEL_2180 = box(566000.0, 244000.0, 566060.0, 244040.0)  # 60 × 40 m = 2400 m²
PARCEL_AREA = PARCEL_2180.area

LONG_POLISH_TEXT = (
    "Dla terenu oznaczonego symbolem ustala się następujące zasady kształtowania "
    "zabudowy oraz wskaźniki zagospodarowania terenu: maksymalna wysokość "
    "zabudowy mierzona od średniego poziomu terenu przed głównym wejściem do "
    "budynku do najwyżej położonej krawędzi dachu, z wyłączeniem kominów, "
    "masztów i urządzeń technicznych — żółć, gęś, źdźbło, łódź, ćma, ńwe; "
    "dopuszcza się lokalizację budynków gospodarczych i garaży wolnostojących "
    "o łącznej powierzchni zabudowy nieprzekraczającej wskazanej w ust. 4."
)


def _geojson(geometry: BaseGeometry, layer: str, **properties: Any) -> dict[str, Any]:
    projected = transform(_TO_4326.transform, geometry)
    return {"type": "Feature", "geometry": mapping(projected), "properties": {"layer": layer, **properties}}


def _share(geometry: BaseGeometry) -> tuple[float, float]:
    area = geometry.intersection(PARCEL_2180).area
    return round(area, 4), round(area / PARCEL_AREA * 100.0, 4)


# Identyfikatory z katalogu źródeł (docs/data_sources/catalog.yaml), tak jak w
# realnym wyniku analizy — macierz jakości (BK-504) rozpoznaje źródło po nich.
CATALOG_IDS = {
    "ULDK": "uldk",
    "MPZP wektor gminy": "mpzp_ru",
    "RU WFS APP": "pog_app",
    "ISOK WFS": "isok",
    "GDOŚ WFS": "gdos",
    "NMT GUGiK": "nmt",
    "KIUT (GUGiK)": "kiut_wms",
}


def _source(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "source_id": CATALOG_IDS.get(name),
        "source_name": name,
        "source_url": f"https://{name.lower().replace(' ', '-')}.example.test/usluga",
        "fetched_at": FETCHED_AT,
        "response_status": 200,
        "confidence": 0.9,
        "manual_review_required": False,
        **extra,
    }


def _evidence(name: str, value: Any, unit: str | None, page: int, segment: str, text: str,
              *, conflict: str | None = None, doc: str = DOC_SHA, confidence: float = 0.86) -> dict[str, Any]:
    return {
        "name": name,
        "normalized_value": value,
        "raw_value": f"{value} {unit}" if unit and value is not None else str(value),
        "unit": unit,
        "evidence_text": text,
        "page_number": page,
        "segment_id": segment,
        "legal_unit_id": page * 10,
        "document_sha256": doc,
        "document_version_id": 41,
        "parser_version": "mpzp-parser/3.2",
        "extraction_method": "pdf_text",
        "confidence": confidence,
        "conflict_group_id": conflict,
        "manual_review_required": conflict is not None,
    }


def _mpzp_zones() -> list[dict[str, Any]]:
    mn = box(566000.0, 244000.0, 566060.0, 244025.0)
    u = box(566000.0, 244025.0, 566060.0, 244040.0)
    mn_area, mn_pct = _share(mn)
    u_area, u_pct = _share(u)
    common = {
        "act_identifier": "PL.ZIPOZ.1261.MPZP-2019-17",
        "act_version": "20190517T000000",
        "act_version_id": 17,
        "data_release_id": 7,
        "document_url": "https://bip.example.test/uchwala-XVII-2019.pdf",
        "touches_boundary": False,
        "assignment_method": "vector_intersection",
        "manual_review_required": False,
        "source": _source("MPZP wektor gminy", data_release_id=7, artifact_sha256=ARTIFACT_SHA),
    }
    mn_parameters = [
        _evidence("max_building_height_m", 9.0, "m", 12, "§8 ust. 2 pkt 1",
                  "Maksymalna wysokość zabudowy mieszkaniowej: 9 m, dla budynków gospodarczych 5 m."),
        _evidence("max_storeys", 2, None, 12, "§8 ust. 2 pkt 2",
                  "Maksymalna liczba kondygnacji nadziemnych: 2.", conflict="storeys-1"),
        _evidence("max_storeys", 3, None, 31, "§21 ust. 1",
                  "Dopuszcza się trzy kondygnacje nadziemne, w tym poddasze użytkowe.",
                  conflict="storeys-1", confidence=0.61),
        _evidence("min_biologically_active_percent", 40.0, "percent", 12, "§8 ust. 2 pkt 4",
                  "Minimalny udział powierzchni biologicznie czynnej: 40% powierzchni działki."),
        _evidence("max_intensity", 0.8, None, 13, "§8 ust. 2 pkt 5",
                  "Maksymalny wskaźnik intensywności zabudowy: 0,8."),
        _evidence("min_intensity", 0.0, None, 13, "§8 ust. 2 pkt 6",
                  "Minimalny wskaźnik intensywności zabudowy: 0."),
        _evidence("max_building_coverage_percent", 30.0, "percent", 13, "§8 ust. 2 pkt 3",
                  "Maksymalna powierzchnia zabudowy: 30% powierzchni działki budowlanej."),
    ]
    u_parameters = [
        _evidence("max_building_height_m", 12.0, "m", 15, "§9 ust. 2 pkt 1",
                  "Maksymalna wysokość zabudowy usługowej: 12 m.", doc=DOC_SHA_2),
        _evidence("max_building_coverage_percent", 0.0, "percent", 15, "§9 ust. 2 pkt 3",
                  "Zakaz lokalizacji nowych budynków — powierzchnia zabudowy 0%.", doc=DOC_SHA_2),
    ]
    return [
        {
            **common,
            "zone_symbol": "MN.1",
            "zone_id": "PL.ZIPOZ.1261.MPZP-2019-17/wydzielenie/MN.1",
            "primary_use": "zabudowa mieszkaniowa jednorodzinna",
            "supplementary_use": None,
            "max_building_height_m": 9.0,
            "max_floors": None,
            "min_biologically_active_pct": 40.0,
            "max_floor_area_ratio": 0.8,
            "min_floor_area_ratio": 0.0,
            "max_building_coverage_pct": 30.0,
            "intersection_area_sqm": mn_area,
            "intersection_pct": mn_pct,
            "is_dominant": True,
            "intersection_geojson": _geojson(mn, "mpzp_zone", zone_symbol="MN.1"),
            "parameters": mn_parameters,
            "manual_review_required": True,
        },
        {
            **common,
            "zone_symbol": "U.2",
            "zone_id": "PL.ZIPOZ.1261.MPZP-2019-17/wydzielenie/U.2",
            "primary_use": "zabudowa usługowa",
            "supplementary_use": "zieleń urządzona",
            "max_building_height_m": 12.0,
            "max_floors": None,
            "min_biologically_active_pct": None,
            "max_floor_area_ratio": None,
            "min_floor_area_ratio": None,
            "max_building_coverage_pct": 0.0,
            "intersection_area_sqm": u_area,
            "intersection_pct": u_pct,
            "is_dominant": False,
            "intersection_geojson": _geojson(u, "mpzp_zone", zone_symbol="U.2"),
            "parameters": u_parameters,
        },
    ]


_POG_TYPES = ("SW", "SJ", "SU", "SZ", "SP", "SO", "SR", "SI", "SN", "SC", "SG", "SH", "SK")


def _pog_zone(index: int, geometry: BaseGeometry, zone_type: str, params: tuple[Any, ...]) -> dict[str, Any]:
    area, pct = _share(geometry)
    intensity, height, coverage, bio = params
    symbol = f"{index}{zone_type}"
    return {
        "id": f"PL.ZIPOG.1261.POG/strefa/{symbol}",
        "symbol": symbol,
        "type": zone_type,
        "label": None,
        "area_sqm": area,
        "area_pct": pct,
        "max_overground_floor_area_ratio": intensity,
        "max_building_height_m": height,
        "max_building_coverage_pct": coverage,
        "min_biologically_active_pct": bio,
        "primary_profile": [
            {"code": "zabudowaMieszkaniowa", "label": "zabudowa mieszkaniowa",
             "dictionary_source": "ProfilFunkcjonalnyKod (APP 2024)"}
        ],
        "additional_profiles": (
            [{"code": "uslugi", "label": "usługi", "dictionary_source": "ProfilFunkcjonalnyKod (APP 2024)"}]
            if index % 2
            else []
        ),
        "source": _source("RU WFS APP", data_release_id=12, artifact_sha256=ARTIFACT_SHA),
        "feature_version": f"2026031{index % 10}T120000",
        "gml_url": f"https://rejestr-urbanistyczny.example.test/gml/{symbol}",
        "gml_url_verified": False,
        "geometry_geojson": _geojson(geometry, "pog_zone", zone_type=zone_type),
    }


def _pog_area(identifier: str, symbol: str, label: str, geometry: BaseGeometry) -> dict[str, Any]:
    area, pct = _share(geometry)
    return {
        "id": identifier,
        "symbol": symbol,
        "label": label,
        "area_sqm": area,
        "area_pct": pct,
        "touches_boundary": False,
        "source": _source("RU WFS APP", data_release_id=12),
        "feature_version": "20260315T120000",
        "gml_url": None,
        "gml_url_verified": False,
        "geometry_geojson": _geojson(geometry, "pog_area"),
    }


def _pog(zones: list[dict[str, Any]], legal_status: str) -> dict[str, Any]:
    ouz = box(566000.0, 244000.0, 566030.0, 244040.0)
    ozs = box(566040.0, 244030.0, 566060.0, 244040.0)
    osdis = box(565950.0, 243950.0, 566100.0, 244100.0)
    ouz_area, ouz_pct = _share(ouz)
    evidence = {
        "source_name": "Rejestr Urbanistyczny — CSW",
        "official": True,
        "reference": "data_release:12; RU CSW rekord PL.ZIPOG.1261.POG",
        "source_id": "pog_app",
        "raw_value": "wybranyPrzedmiotUstaleń" if legal_status == "project" else "obowiazujacy",
        "confirmed_at": FETCHED_AT,
    }
    return {
        "schema_version": "2.4",
        "legal_status": legal_status,
        "coverage_status": "available",
        "data_availability": "current",
        "status_confirmed_at": FETCHED_AT,
        "legal_status_evidence": evidence,
        "coverage_evidence": None,
        "act": {
            "id": "PL.ZIPOG.1261.POG",
            "version": "20260315T120000",
            "title": "Plan ogólny gminy Kraków — zażółć gęślą jaźń",
            "resolution_number": "CXII/3021/2026",
            "resolution_date": "2026-03-11",
            "act_identifier": "PL.ZIPOG.1261.POG",
            "act_version": "20260315T120000",
            "publication_id": "PL.ZIPOG.1261.POG_20260315T120000",
            "version_started_at": "2026-03-15T12:00:00+00:00",
            "publication_date": "2026-03-20",
            "valid_from": "2026-04-01",
            "valid_to": None,
            "gml_url": "https://integracja.gugik.gov.pl/cgi-bin/PlanOgolnyGminy?id=1261",
            "gml_url_verified": True,
            "card_url": None,
            "card_url_verified": False,
            "data_release_id": 12,
            "release_label": "RU POG 2026-09-19",
            "artifact_sha256": ARTIFACT_SHA,
            "fetched_at": FETCHED_AT,
            "metadata": None,
            "formal_documents": [],
        },
        "zones": zones,
        "dominant_zone_id": max(zones, key=lambda item: item["area_pct"])["id"] if zones else None,
        "ouz": [_pog_area("PL.ZIPOG.1261.POG/ouz/1", "OUZ-1", "obszar uzupełnienia zabudowy", ouz)],
        "downtown_areas": [_pog_area("PL.ZIPOG.1261.POG/ozs/1", "OZS-1", "obszar zabudowy śródmiejskiej", ozs)],
        "social_infrastructure_standard_areas": [
            _pog_area("PL.ZIPOG.1261.POG/osdis/1", "OSDIS-1", "standard dostępności — szkoła podstawowa", osdis)
        ],
        "status": "adopted" if legal_status == "binding" else "project",
        "planning_zone": zones[0]["type"] if zones else None,
        "zone_type": zones[0]["type"] if zones else None,
        "in_ouz": True,
        "area_ratio": None,
        "in_downtown_area": True,
        "uchwala_nr": "CXII/3021/2026",
        "uchwala_date": "2026-03-11",
        "manual_review_required": legal_status != "binding",
        "compatibility_assessment": None,
        "raw_attributes": {"przestrzenNazw": "PL.ZIPOG.1261.POG"},
        "ouz_intersection_area_sqm": ouz_area,
        "ouz_intersection_pct": ouz_pct,
        "touches_ouz_boundary": False,
        "presentation_style": None,
        "source": _source("RU WFS APP", data_release_id=12, artifact_sha256=ARTIFACT_SHA),
    }


def _risks() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    flood = box(565990.0, 243990.0, 566020.0, 244015.0)
    nature = box(565900.0, 243900.0, 566200.0, 244200.0)
    flood_area, flood_pct = _share(flood)
    nature_area, nature_pct = _share(nature)
    risks = [
        {
            "risk_type": "flood_zone",
            "section": "flood",
            "feature_id": "ISOK.OZP.Q1.4123",
            "severity": "medium",
            "probability_class": "obszar szczególnego zagrożenia powodzią Q1%",
            "return_period_years": 100,
            "intersection_area_sqm": flood_area,
            "intersection_pct": flood_pct,
            "touches_boundary": False,
            "description": "Część działki leży w obszarze szczególnego zagrożenia powodzią (Q1%).",
            "geometry_geojson": _geojson(flood, "risk", risk_type="flood_zone"),
            "warnings": [],
            "source": _source("ISOK WFS"),
        },
        {
            "risk_type": "nature_protection",
            "section": "nature",
            "feature_id": "GDOS.OChK.12",
            "severity": "low",
            "protection_type": "obszar chronionego krajobrazu",
            "name": "Bielańsko-Tyniecki Obszar Chronionego Krajobrazu",
            "intersection_area_sqm": nature_area,
            "intersection_pct": nature_pct,
            "touches_boundary": False,
            "description": "Działka leży w obszarze chronionego krajobrazu.",
            "geometry_geojson": _geojson(nature, "risk", risk_type="nature_protection"),
            "warnings": [],
            "source": _source("GDOŚ WFS"),
        },
    ]
    sections = [
        {
            "schema_version": "1.0", "section": "flood", "status": "available", "reason_code": None,
            "relation": "intersection", "feature_count": 1, "intersecting_feature_count": 1,
            "boundary_feature_count": 0, "union_intersection_area_sqm": flood_area,
            "union_intersection_pct": flood_pct, "feature_ids": ["ISOK.OZP.Q1.4123"],
            "source": _source("ISOK WFS"), "warnings": [],
        },
        {
            "schema_version": "1.0", "section": "nature", "status": "available", "reason_code": None,
            "relation": "intersection", "feature_count": 1, "intersecting_feature_count": 1,
            "boundary_feature_count": 0, "union_intersection_area_sqm": nature_area,
            "union_intersection_pct": nature_pct, "feature_ids": ["GDOS.OChK.12"],
            "source": _source("GDOŚ WFS"), "warnings": [],
        },
    ]
    return risks, sections


def _parcel() -> dict[str, Any]:
    buildable = PARCEL_2180.buffer(-4.0, join_style="mitre")
    return {
        "parcel_identifier": "126101_1.0001.2417/5",
        "geometry_geojson": _geojson(PARCEL_2180, "parcel"),
        "metrics": {
            "area_sqm": PARCEL_AREA,
            "area_ha": PARCEL_AREA / 10_000.0,
            "perimeter_m": PARCEL_2180.length,
            "is_valid": True,
            "geometry_repaired": False,
        },
        "source": _source("ULDK"),
        "buildable_area_geojson": _geojson(buildable, "buildable_area"),
    }


def _base(zones: list[dict[str, Any]], legal_status: str) -> dict[str, Any]:
    risks, sections = _risks()
    return {
        "analysis_id": None,
        "status": "partial",
        "analyzed_at": ANALYZED_AT.isoformat(),
        "parcel": _parcel(),
        "mpzp_zones": _mpzp_zones(),
        "pog": _pog(zones, legal_status),
        "infrastructure": [],
        "utilities_preview": {
            "coverage_status": "covered",
            "county_name": "powiat m. Kraków",
            "layer_available": True,
            "note": "Powiat publikuje dane GESUT; podgląd nie służy do obliczania odległości.",
            "source": _source("KIUT (GUGiK)"),
        },
        "risks": risks,
        "risk_sections": sections,
        "terrain": {
            "schema_version": "1.1", "status": "available", "reason_code": None,
            "min_height_m": 211.4, "max_height_m": 213.0, "height_difference_m": 1.6,
            "grid_size_m": 5.0, "sampled_points": 96, "source": _source("NMT GUGiK"),
            "warnings": [], "relief": None,
        },
        "buildable_area_sqm": 1664.0,
        "manual_zone_required": False,
        "manual_zone_context": None,
        "warnings": [
            {"code": "MPZP_PARAMETER_CONFLICT", "message": "Uchwała zawiera sprzeczne wartości liczby kondygnacji.",
             "severity": "warning", "source_name": "mpzp"},
        ],
        "sources": [
            _source("ULDK"),
            _source("RU WFS APP", data_release_id=12, artifact_sha256=ARTIFACT_SHA,
                    source_id="pog_app", source_version="2026-09-19"),
            _source("MPZP wektor gminy", data_release_id=7),
            _source("ISOK WFS"),
            _source("GDOŚ WFS"),
            _source("NMT GUGiK"),
        ],
    }


_MULTIZONE_PARAMS = (
    ("SW", (1.2, 16.0, 40.0, 30.0)),
    ("SJ", (0.6, 12.0, 35.0, 50.0)),
    ("SU", (1.0, None, 60.0, 0.0)),
)


def multizone(legal_status: str = "binding") -> dict[str, Any]:
    zones = [
        _pog_zone(index + 1, box(566000.0 + 20 * index, 244000.0, 566020.0 + 20 * index, 244040.0),
                  zone_type, params)
        for index, (zone_type, params) in enumerate(_MULTIZONE_PARAMS)
    ]
    return _base(zones, legal_status)


def long_tables() -> dict[str, Any]:
    count = 32
    width = 60.0 / count
    zones = []
    for index in range(count):
        zone_type = _POG_TYPES[index % len(_POG_TYPES)]
        params = (
            None if index % 5 == 0 else round(0.1 * (index % 7), 2),
            None if index % 4 == 0 else float(8 + index),
            0.0 if index % 6 == 0 else float(20 + index),
            None if index % 3 == 0 else float(10 + index),
        )
        geometry = box(566000.0 + width * index, 244000.0, 566000.0 + width * (index + 1), 244040.0)
        zones.append(_pog_zone(index + 1, geometry, zone_type, params))
    data = _base(zones, "binding")
    parameters = []
    for index in range(36):
        parameters.append(
            _evidence(
                f"ustalenie_szczegolowe_{index + 1:02d}",
                None if index % 4 == 0 else (0.0 if index % 4 == 1 else float(index)),
                "m" if index % 2 else "percent",
                20 + index,
                f"§{30 + index} ust. {1 + index % 3}",
                f"[{index + 1}] {LONG_POLISH_TEXT}",
            )
        )
    data["mpzp_zones"][0]["parameters"] = [*data["mpzp_zones"][0]["parameters"], *parameters]
    data["pog"]["act"]["title"] = "Plan ogólny gminy — " + LONG_POLISH_TEXT[:160]
    return data


def parcel_wkt() -> str:
    return PARCEL_2180.wkt


FIXTURES = {
    "multizone": lambda: multizone("binding"),
    "project": lambda: multizone("project"),
    "long_tables": long_tables,
}


def write_fixtures() -> None:
    for name, factory in FIXTURES.items():
        payload = {"parcel_wkt_2180": parcel_wkt(), "response": factory()}
        (HERE / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", "utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maps", action="store_true", help="odśwież mapy referencyjne")
    args = parser.parse_args()
    write_fixtures()
    if args.maps:
        from tests.report_map_reference import write_reference_maps

        write_reference_maps()


if __name__ == "__main__":
    main()

