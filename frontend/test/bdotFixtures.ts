/**
 * Sekcje drogi i zabudowy sąsiedniej wygenerowane przez backend (`build_road_context`, `build_neighbour_buildings`)
 * na zamrożonej geometrii raportu — po jednym przypadku na istotną sytuację (Task 23.2–23.3). Nie edytuj ręcznie:
 * `python scripts/export_bdot_fixtures.py > ../frontend/test/bdotFixtures.ts`.
 */
import type { NeighbourBuildingsResult, RoadContextResult } from "@/lib/types";

export const ROAD_CASES: Record<string, RoadContextResult> = {
  "available": {
    "schema_version": "1.0",
    "status": "available",
    "reason_code": null,
    "distance_reference": "parcel_polygon_to_carriageway_axis",
    "nearest": {
      "road_id": "28BA1C51-4E6D-8554-E053-CC2BA8C0F941",
      "distance_m": 12.0,
      "relation": "disjoint",
      "geometry_kind": "carriageway_axis",
      "category": "gminna",
      "road_class": "droga lokalna",
      "road_number": null,
      "street_name": "ul. Przykładowa",
      "surface_material": "masa bitumiczna",
      "surface_width_m": 6.0,
      "position": "na powierzchni gruntu",
      "feature_version": "2026-01-22T00:00:00+00:00",
      "road_geojson": {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [
            [
              19.922207274692028,
              50.059489428780125
            ],
            [
              19.923394982549304,
              50.05947998601995
            ]
          ]
        },
        "properties": {
          "layer": "road_context",
          "road_id": "28BA1C51-4E6D-8554-E053-CC2BA8C0F941",
          "distance_m": 12.0,
          "category": "gminna",
          "road_class": "droga lokalna"
        }
      },
      "link_geojson": {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [
            [
              19.922768266789962,
              50.05959293255529
            ],
            [
              19.922766196115123,
              50.0594849866419
            ]
          ]
        },
        "properties": {
          "layer": "road_context_link",
          "road_id": "28BA1C51-4E6D-8554-E053-CC2BA8C0F941",
          "distance_m": 12.0,
          "category": "gminna",
          "road_class": "droga lokalna"
        }
      }
    },
    "search_radius_m": 25.0,
    "searched_radii_m": [
      25.0
    ],
    "max_search_radius_m": 500.0,
    "candidate_limit": 25,
    "search_area_within_coverage": true,
    "intersecting_road_count": 0,
    "touching_road_count": 0,
    "release": {
      "data_release_id": 21,
      "version_label": "bdot10k-1261-1355c5a51538",
      "teryt_powiat": "1261",
      "data_as_of": "2026-01-22",
      "package_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "imported_at": "2026-09-02T09:00:00Z"
    },
    "interpretation_note": "Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "intersects": {
    "schema_version": "1.0",
    "status": "available",
    "reason_code": null,
    "distance_reference": "parcel_polygon_to_carriageway_axis",
    "nearest": {
      "road_id": "28BA1C51-4E6D-8554-E053-CC2BA8C0F941",
      "distance_m": 0.0,
      "relation": "intersects",
      "geometry_kind": "carriageway_axis",
      "category": "gminna",
      "road_class": "droga lokalna",
      "road_number": null,
      "street_name": "ul. Przykładowa",
      "surface_material": "masa bitumiczna",
      "surface_width_m": 6.0,
      "position": "na powierzchni gruntu",
      "feature_version": "2026-01-22T00:00:00+00:00",
      "road_geojson": {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [
            [
              19.922207274692028,
              50.059489428780125
            ],
            [
              19.923394982549304,
              50.05947998601995
            ]
          ]
        },
        "properties": {
          "layer": "road_context",
          "road_id": "28BA1C51-4E6D-8554-E053-CC2BA8C0F941",
          "distance_m": 12.0,
          "category": "gminna",
          "road_class": "droga lokalna"
        }
      },
      "link_geojson": null
    },
    "search_radius_m": 25.0,
    "searched_radii_m": [
      25.0
    ],
    "max_search_radius_m": 500.0,
    "candidate_limit": 25,
    "search_area_within_coverage": true,
    "intersecting_road_count": 2,
    "touching_road_count": 1,
    "release": {
      "data_release_id": 21,
      "version_label": "bdot10k-1261-1355c5a51538",
      "teryt_powiat": "1261",
      "data_as_of": "2026-01-22",
      "package_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "imported_at": "2026-09-02T09:00:00Z"
    },
    "interpretation_note": "Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "partial_coverage": {
    "schema_version": "1.0",
    "status": "available",
    "reason_code": null,
    "distance_reference": "parcel_polygon_to_carriageway_axis",
    "nearest": {
      "road_id": "28BA1C51-4E6D-8554-E053-CC2BA8C0F941",
      "distance_m": 12.0,
      "relation": "disjoint",
      "geometry_kind": "carriageway_axis",
      "category": "gminna",
      "road_class": "droga lokalna",
      "road_number": null,
      "street_name": "ul. Przykładowa",
      "surface_material": "masa bitumiczna",
      "surface_width_m": 6.0,
      "position": "na powierzchni gruntu",
      "feature_version": "2026-01-22T00:00:00+00:00",
      "road_geojson": {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [
            [
              19.922207274692028,
              50.059489428780125
            ],
            [
              19.923394982549304,
              50.05947998601995
            ]
          ]
        },
        "properties": {
          "layer": "road_context",
          "road_id": "28BA1C51-4E6D-8554-E053-CC2BA8C0F941",
          "distance_m": 12.0,
          "category": "gminna",
          "road_class": "droga lokalna"
        }
      },
      "link_geojson": {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [
            [
              19.922768266789962,
              50.05959293255529
            ],
            [
              19.922766196115123,
              50.0594849866419
            ]
          ]
        },
        "properties": {
          "layer": "road_context_link",
          "road_id": "28BA1C51-4E6D-8554-E053-CC2BA8C0F941",
          "distance_m": 12.0,
          "category": "gminna",
          "road_class": "droga lokalna"
        }
      }
    },
    "search_radius_m": 25.0,
    "searched_radii_m": [
      25.0
    ],
    "max_search_radius_m": 500.0,
    "candidate_limit": 25,
    "search_area_within_coverage": false,
    "intersecting_road_count": 0,
    "touching_road_count": 0,
    "release": {
      "data_release_id": 21,
      "version_label": "bdot10k-1261-1355c5a51538",
      "teryt_powiat": "1261",
      "data_as_of": "2026-01-22",
      "package_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "imported_at": "2026-09-02T09:00:00Z"
    },
    "interpretation_note": "Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": [
      "Obszar do znalezionej drogi wykracza poza zasięg zaimportowanego powiatu."
    ]
  },
  "no_match": {
    "schema_version": "1.0",
    "status": "no_match",
    "reason_code": "SEARCH_RADIUS_EXHAUSTED",
    "distance_reference": "parcel_polygon_to_carriageway_axis",
    "nearest": null,
    "search_radius_m": 500.0,
    "searched_radii_m": [
      25.0,
      50.0,
      100.0,
      250.0,
      500.0
    ],
    "max_search_radius_m": 500.0,
    "candidate_limit": 25,
    "search_area_within_coverage": true,
    "intersecting_road_count": null,
    "touching_road_count": null,
    "release": {
      "data_release_id": 21,
      "version_label": "bdot10k-1261-1355c5a51538",
      "teryt_powiat": "1261",
      "data_as_of": "2026-01-22",
      "package_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "imported_at": "2026-09-02T09:00:00Z"
    },
    "interpretation_note": "Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "no_coverage": {
    "schema_version": "1.0",
    "status": "no_coverage",
    "reason_code": "OUTSIDE_COVERAGE",
    "distance_reference": "parcel_polygon_to_carriageway_axis",
    "nearest": null,
    "search_radius_m": null,
    "searched_radii_m": [],
    "max_search_radius_m": 500.0,
    "candidate_limit": 25,
    "search_area_within_coverage": null,
    "intersecting_road_count": null,
    "touching_road_count": null,
    "release": null,
    "interpretation_note": "Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "unavailable": {
    "schema_version": "1.0",
    "status": "unavailable",
    "reason_code": "QUERY_TIMEOUT",
    "distance_reference": "parcel_polygon_to_carriageway_axis",
    "nearest": null,
    "search_radius_m": null,
    "searched_radii_m": [],
    "max_search_radius_m": 500.0,
    "candidate_limit": 25,
    "search_area_within_coverage": null,
    "intersecting_road_count": null,
    "touching_road_count": null,
    "release": null,
    "interpretation_note": "Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  }
};

export const BUILDINGS_CASES: Record<string, NeighbourBuildingsResult> = {
  "available": {
    "schema_version": "1.0",
    "status": "available",
    "reason_code": null,
    "ring_definition": "buffer(parcel, r) - parcel",
    "distance_reference": "parcel_polygon_to_building_footprint",
    "buffers_m": [
      50.0,
      100.0,
      200.0
    ],
    "parcel_area_sqm": 2400.0,
    "on_parcel": {
      "count": 2,
      "union_area_in_parcel_sqm": 194.0,
      "coverage_pct": 8.0833
    },
    "buffers": [
      {
        "radius_m": 50.0,
        "ring_area_sqm": 17841.3712,
        "count": 2,
        "straddling_parcel_count": 1,
        "footprint_area_in_ring_sqm": 150.0,
        "union_area_in_ring_sqm": 150.0,
        "built_share_pct": 0.8407,
        "min_distance_m": 0.0,
        "footprint_min_sqm": 100.0,
        "footprint_median_sqm": 100.0,
        "footprint_max_sqm": 100.0
      },
      {
        "radius_m": 100.0,
        "ring_area_sqm": 51365.4849,
        "count": 3,
        "straddling_parcel_count": 1,
        "footprint_area_in_ring_sqm": 250.0,
        "union_area_in_ring_sqm": 250.0,
        "built_share_pct": 0.4867,
        "min_distance_m": 0.0,
        "footprint_min_sqm": 100.0,
        "footprint_median_sqm": 100.0,
        "footprint_max_sqm": 100.0
      },
      {
        "radius_m": 200.0,
        "ring_area_sqm": 165461.9396,
        "count": 3,
        "straddling_parcel_count": 1,
        "footprint_area_in_ring_sqm": 250.0,
        "union_area_in_ring_sqm": 250.0,
        "built_share_pct": 0.1511,
        "min_distance_m": 0.0,
        "footprint_min_sqm": 100.0,
        "footprint_median_sqm": 100.0,
        "footprint_max_sqm": 100.0
      }
    ],
    "buildings": [
      {
        "building_id": "B-0001",
        "distance_m": 0.0,
        "relation": "on_parcel",
        "overlap_with_parcel_sqm": 144.0,
        "footprint_sqm": 144.0,
        "general_function": "budynki mieszkalne",
        "detailed_function": "budynek jednorodzinny",
        "predominant_function": "budynek jednorodzinny",
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": "2026-01-22T00:00:00+00:00",
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.922658207628746,
                  50.05968377613063
                ],
                [
                  19.922660278075853,
                  50.05979172204359
                ],
                [
                  19.922492600589482,
                  50.05979305482051
                ],
                [
                  19.922490530518516,
                  50.05968510890249
                ],
                [
                  19.922658207628746,
                  50.05968377613063
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "B-0001",
            "relation": "on_parcel",
            "distance_m": 0.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "B-0002",
        "distance_m": 0.0,
        "relation": "straddling",
        "overlap_with_parcel_sqm": 50.0,
        "footprint_sqm": 100.0,
        "general_function": "budynki produkcyjne, usługowe i gospodarcze dla rolnictwa",
        "detailed_function": "budynek gospodarczy",
        "predominant_function": "budynek gospodarczy",
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": "2026-01-22T00:00:00+00:00",
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.9232599137503,
                  50.05972397583251
                ],
                [
                  19.92326164024957,
                  50.05981393074426
                ],
                [
                  19.923121908970945,
                  50.05981504213275
                ],
                [
                  19.923120182732887,
                  50.05972508721749
                ],
                [
                  19.9232599137503,
                  50.05972397583251
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "B-0002",
            "relation": "straddling",
            "distance_m": 0.0,
            "floors": 1,
            "function": "budynki produkcyjne, usługowe i gospodarcze dla rolnictwa"
          }
        }
      },
      {
        "building_id": "B-0003",
        "distance_m": 20.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 100.0,
        "general_function": "budynki mieszkalne",
        "detailed_function": "budynek jednorodzinny",
        "predominant_function": "budynek jednorodzinny",
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": "2026-01-22T00:00:00+00:00",
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92207133768801,
                  50.05968843977254
                ],
                [
                  19.92207306196266,
                  50.059778394714925
                ],
                [
                  19.921933330740522,
                  50.05977950467211
                ],
                [
                  19.921931606727078,
                  50.05968954972621
                ],
                [
                  19.92207133768801,
                  50.05968843977254
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "B-0003",
            "relation": "neighbour",
            "distance_m": 20.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      }
    ],
    "buildings_truncated": false,
    "listed_radius_m": 50.0,
    "candidates_considered": 4,
    "search_area_within_coverage": true,
    "release": {
      "data_release_id": 21,
      "version_label": "bdot10k-1261-1355c5a51538",
      "teryt_powiat": "1261",
      "data_as_of": "2026-01-22",
      "package_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "imported_at": "2026-09-02T09:00:00Z"
    },
    "interpretation_note": "Kontekst istniejącej zabudowy z obrysów BDOT10k (w większości z EGiB): opis tego, co stoi w otoczeniu działki, a nie ocena możliwości zabudowy. Dane bez nazw i właścicieli; stan na datę wskazaną przy wydaniu danych.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "crowded": {
    "schema_version": "1.0",
    "status": "available",
    "reason_code": null,
    "ring_definition": "buffer(parcel, r) - parcel",
    "distance_reference": "parcel_polygon_to_building_footprint",
    "buffers_m": [
      50.0,
      100.0,
      200.0
    ],
    "parcel_area_sqm": 2400.0,
    "on_parcel": {
      "count": 0,
      "union_area_in_parcel_sqm": 0.0,
      "coverage_pct": 0.0
    },
    "buffers": [
      {
        "radius_m": 50.0,
        "ring_area_sqm": 17841.3712,
        "count": 30,
        "straddling_parcel_count": 0,
        "footprint_area_in_ring_sqm": 45.0,
        "union_area_in_ring_sqm": 45.0,
        "built_share_pct": 0.2522,
        "min_distance_m": 2.0,
        "footprint_min_sqm": 1.5,
        "footprint_median_sqm": 1.5,
        "footprint_max_sqm": 1.5
      },
      {
        "radius_m": 100.0,
        "ring_area_sqm": 51365.4849,
        "count": 30,
        "straddling_parcel_count": 0,
        "footprint_area_in_ring_sqm": 45.0,
        "union_area_in_ring_sqm": 45.0,
        "built_share_pct": 0.0876,
        "min_distance_m": 2.0,
        "footprint_min_sqm": 1.5,
        "footprint_median_sqm": 1.5,
        "footprint_max_sqm": 1.5
      },
      {
        "radius_m": 200.0,
        "ring_area_sqm": 165461.9396,
        "count": 30,
        "straddling_parcel_count": 0,
        "footprint_area_in_ring_sqm": 45.0,
        "union_area_in_ring_sqm": 45.0,
        "built_share_pct": 0.0272,
        "min_distance_m": 2.0,
        "footprint_min_sqm": 1.5,
        "footprint_median_sqm": 1.5,
        "footprint_max_sqm": 1.5
      }
    ],
    "buildings": [
      {
        "building_id": "CROWD-000-0000-0000",
        "distance_m": 2.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92322937789334,
                  50.05958926575157
                ],
                [
                  19.923229636857872,
                  50.059602758988866
                ],
                [
                  19.923215663791613,
                  50.059602870131094
                ],
                [
                  19.923215404830994,
                  50.05958937689375
                ],
                [
                  19.92322937789334,
                  50.05958926575157
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-000-0000-0000",
            "relation": "neighbour",
            "distance_m": 2.0,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-010-0000-0000",
        "distance_m": 2.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92322972317943,
                  50.05960725673461
                ],
                [
                  19.92322998214422,
                  50.05962074997186
                ],
                [
                  19.92321600907274,
                  50.059620861114155
                ],
                [
                  19.92321575011186,
                  50.05960736787686
                ],
                [
                  19.92322972317943,
                  50.05960725673461
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-010-0000-0000",
            "relation": "neighbour",
            "distance_m": 2.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-020-0000-0000",
        "distance_m": 2.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923230068465866,
                  50.0596252477176
                ],
                [
                  19.92323032743092,
                  50.059638740954796
                ],
                [
                  19.923216354354214,
                  50.05963885209715
                ],
                [
                  19.923216095393073,
                  50.05962535885991
                ],
                [
                  19.923230068465866,
                  50.0596252477176
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-020-0000-0000",
            "relation": "neighbour",
            "distance_m": 2.0,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-001-0000-0000",
        "distance_m": 3.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92325033748669,
                  50.059589099035165
                ],
                [
                  19.9232505964571,
                  50.05960259227238
                ],
                [
                  19.92323662339097,
                  50.05960270341712
                ],
                [
                  19.923236364424476,
                  50.05958921017986
                ],
                [
                  19.92325033748669,
                  50.059589099035165
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-001-0000-0000",
            "relation": "neighbour",
            "distance_m": 3.5,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-011-0000-0000",
        "distance_m": 3.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923250682780616,
                  50.0596070900181
                ],
                [
                  19.923250941751288,
                  50.05962058325526
                ],
                [
                  19.923236968679934,
                  50.05962069440009
                ],
                [
                  19.923236709713176,
                  50.05960720116286
                ],
                [
                  19.923250682780616,
                  50.0596070900181
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-011-0000-0000",
            "relation": "neighbour",
            "distance_m": 3.5,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-021-0000-0000",
        "distance_m": 3.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92325102807489,
                  50.05962508100097
                ],
                [
                  19.923251287045822,
                  50.059638574238086
                ],
                [
                  19.923237313969246,
                  50.059638685382986
                ],
                [
                  19.92323705500223,
                  50.05962519214581
                ],
                [
                  19.92325102807489,
                  50.05962508100097
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-021-0000-0000",
            "relation": "neighbour",
            "distance_m": 3.5,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-002-0000-0000",
        "distance_m": 5.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923271297079843,
                  50.059588932314966
                ],
                [
                  19.92327155605613,
                  50.05960242555209
                ],
                [
                  19.923257582990132,
                  50.059602536699366
                ],
                [
                  19.923257324017758,
                  50.05958904346219
                ],
                [
                  19.923271297079843,
                  50.059588932314966
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-002-0000-0000",
            "relation": "neighbour",
            "distance_m": 5.0,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-012-0000-0000",
        "distance_m": 5.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923271642381604,
                  50.05960692329779
                ],
                [
                  19.923271901358152,
                  50.05962041653488
                ],
                [
                  19.923257928286933,
                  50.05962052768222
                ],
                [
                  19.9232576693143,
                  50.05960703444509
                ],
                [
                  19.923271642381604,
                  50.05960692329779
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-012-0000-0000",
            "relation": "neighbour",
            "distance_m": 5.0,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-022-0000-0000",
        "distance_m": 5.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923271987683716,
                  50.05962491428057
                ],
                [
                  19.923272246660527,
                  50.05963840751759
                ],
                [
                  19.92325827358408,
                  50.05963851866501
                ],
                [
                  19.923258014611186,
                  50.05962502542792
                ],
                [
                  19.923271987683716,
                  50.05962491428057
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-022-0000-0000",
            "relation": "neighbour",
            "distance_m": 5.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-003-0000-0000",
        "distance_m": 6.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923292256672802,
                  50.05958876559098
                ],
                [
                  19.923292515654964,
                  50.059602258828036
                ],
                [
                  19.9232785425891,
                  50.05960236997783
                ],
                [
                  19.923278283610852,
                  50.059588876740726
                ],
                [
                  19.923292256672802,
                  50.05958876559098
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-003-0000-0000",
            "relation": "neighbour",
            "distance_m": 6.5,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-013-0000-0000",
        "distance_m": 6.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923292601982396,
                  50.05960675657371
                ],
                [
                  19.923292860964825,
                  50.059620249810706
                ],
                [
                  19.923278887893733,
                  50.05962036096057
                ],
                [
                  19.923278628915224,
                  50.05960686772352
                ],
                [
                  19.923292601982396,
                  50.05960675657371
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-013-0000-0000",
            "relation": "neighbour",
            "distance_m": 6.5,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-023-0000-0000",
        "distance_m": 6.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923292947292346,
                  50.05962474755638
                ],
                [
                  19.923293206275034,
                  50.059638240793326
                ],
                [
                  19.923279233198716,
                  50.05963835194327
                ],
                [
                  19.923278974219947,
                  50.05962485870626
                ],
                [
                  19.923292947292346,
                  50.05962474755638
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-023-0000-0000",
            "relation": "neighbour",
            "distance_m": 6.5,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-004-0000-0000",
        "distance_m": 8.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923313216265562,
                  50.05958859886322
                ],
                [
                  19.923313475253604,
                  50.059602092100185
                ],
                [
                  19.923299502187866,
                  50.0596022032525
                ],
                [
                  19.923299243203743,
                  50.05958871001548
                ],
                [
                  19.923313216265562,
                  50.05958859886322
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-004-0000-0000",
            "relation": "neighbour",
            "distance_m": 8.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-014-0000-0000",
        "distance_m": 8.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923313561582997,
                  50.059606589845835
                ],
                [
                  19.923313820571302,
                  50.059620083082756
                ],
                [
                  19.92329984750034,
                  50.05962019423514
                ],
                [
                  19.923299588515953,
                  50.059606700998174
                ],
                [
                  19.923313561582997,
                  50.059606589845835
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-014-0000-0000",
            "relation": "neighbour",
            "distance_m": 8.0,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-024-0000-0000",
        "distance_m": 8.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92331390690078,
                  50.0596245808284
                ],
                [
                  19.923314165889344,
                  50.05963807406527
                ],
                [
                  19.92330019281316,
                  50.05963818521773
                ],
                [
                  19.923299933828513,
                  50.0596246919808
                ],
                [
                  19.92331390690078,
                  50.0596245808284
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-024-0000-0000",
            "relation": "neighbour",
            "distance_m": 8.0,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-005-0000-0000",
        "distance_m": 9.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923334175858127,
                  50.059588432131676
                ],
                [
                  19.923334434852045,
                  50.05960192536856
                ],
                [
                  19.923320461786442,
                  50.0596020365234
                ],
                [
                  19.923320202796436,
                  50.05958854328645
                ],
                [
                  19.923334175858127,
                  50.059588432131676
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-005-0000-0000",
            "relation": "neighbour",
            "distance_m": 9.5,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-015-0000-0000",
        "distance_m": 9.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923334521183396,
                  50.05960642311419
                ],
                [
                  19.92333478017758,
                  50.05961991635101
                ],
                [
                  19.923320807106748,
                  50.059620027505936
                ],
                [
                  19.923320548116486,
                  50.05960653426904
                ],
                [
                  19.923334521183396,
                  50.05960642311419
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-015-0000-0000",
            "relation": "neighbour",
            "distance_m": 9.5,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-025-0000-0000",
        "distance_m": 9.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923334866509016,
                  50.05962441409663
                ],
                [
                  19.92333512550346,
                  50.05963790733343
                ],
                [
                  19.923321152427405,
                  50.059638018488414
                ],
                [
                  19.92332089343688,
                  50.059624525251564
                ],
                [
                  19.923334866509016,
                  50.05962441409663
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-025-0000-0000",
            "relation": "neighbour",
            "distance_m": 9.5,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-006-0000-0000",
        "distance_m": 11.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923355135450496,
                  50.059588265396336
                ],
                [
                  19.923355394450294,
                  50.05960175863314
                ],
                [
                  19.923341421384816,
                  50.05960186979051
                ],
                [
                  19.923341162388937,
                  50.05958837655364
                ],
                [
                  19.923355135450496,
                  50.059588265396336
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-006-0000-0000",
            "relation": "neighbour",
            "distance_m": 11.0,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-016-0000-0000",
        "distance_m": 11.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923355480783602,
                  50.05960625637874
                ],
                [
                  19.923355739783663,
                  50.05961974961551
                ],
                [
                  19.923341766712962,
                  50.05961986077293
                ],
                [
                  19.92334150771682,
                  50.05960636753613
                ],
                [
                  19.923355480783602,
                  50.05960625637874
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-016-0000-0000",
            "relation": "neighbour",
            "distance_m": 11.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-026-0000-0000",
        "distance_m": 11.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923355826117056,
                  50.059624247361086
                ],
                [
                  19.923356085117376,
                  50.05963774059781
                ],
                [
                  19.923342112041453,
                  50.05963785175532
                ],
                [
                  19.923341853045052,
                  50.05962435851855
                ],
                [
                  19.923355826117056,
                  50.059624247361086
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-026-0000-0000",
            "relation": "neighbour",
            "distance_m": 11.0,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-007-0000-0000",
        "distance_m": 12.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92337609504267,
                  50.05958809865722
                ],
                [
                  19.92337635404834,
                  50.05960159189394
                ],
                [
                  19.923362380982997,
                  50.059601703053836
                ],
                [
                  19.923362121981242,
                  50.05958820981705
                ],
                [
                  19.92337609504267,
                  50.05958809865722
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-007-0000-0000",
            "relation": "neighbour",
            "distance_m": 12.5,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-017-0000-0000",
        "distance_m": 12.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92337644038361,
                  50.059606089639516
                ],
                [
                  19.923376699389546,
                  50.0596195828762
                ],
                [
                  19.92336272631898,
                  50.059619694036165
                ],
                [
                  19.92336246731696,
                  50.05960620079942
                ],
                [
                  19.92337644038361,
                  50.059606089639516
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-017-0000-0000",
            "relation": "neighbour",
            "distance_m": 12.5,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-027-0000-0000",
        "distance_m": 12.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.9233767857249,
                  50.05962408062176
                ],
                [
                  19.9233770447311,
                  50.059637573858396
                ],
                [
                  19.923363071655306,
                  50.05963768501843
                ],
                [
                  19.92336281265303,
                  50.05962419178173
                ],
                [
                  19.9233767857249,
                  50.05962408062176
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-027-0000-0000",
            "relation": "neighbour",
            "distance_m": 12.5,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-008-0000-0000",
        "distance_m": 14.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923397054634645,
                  50.059587931914315
                ],
                [
                  19.9233973136462,
                  50.059601425150966
                ],
                [
                  19.92338334058098,
                  50.05960153631337
                ],
                [
                  19.92338308157335,
                  50.05958804307667
                ],
                [
                  19.923397054634645,
                  50.059587931914315
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-008-0000-0000",
            "relation": "neighbour",
            "distance_m": 14.0,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-018-0000-0000",
        "distance_m": 14.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923397399983422,
                  50.0596059228965
                ],
                [
                  19.923397658995235,
                  50.05961941613311
                ],
                [
                  19.923383685924797,
                  50.059619527295595
                ],
                [
                  19.923383426916903,
                  50.059606034058945
                ],
                [
                  19.923397399983422,
                  50.0596059228965
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-018-0000-0000",
            "relation": "neighbour",
            "distance_m": 14.0,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-028-0000-0000",
        "distance_m": 14.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92339774533255,
                  50.05962391387865
                ],
                [
                  19.923398004344623,
                  50.05963740711521
                ],
                [
                  19.923384031268963,
                  50.059637518277746
                ],
                [
                  19.923383772260806,
                  50.05962402504114
                ],
                [
                  19.92339774533255,
                  50.05962391387865
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-028-0000-0000",
            "relation": "neighbour",
            "distance_m": 14.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-009-0000-0000",
        "distance_m": 15.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.923418014226424,
                  50.05958776516763
                ],
                [
                  19.923418273243854,
                  50.0596012584042
                ],
                [
                  19.92340430017877,
                  50.05960136956914
                ],
                [
                  19.92340404116526,
                  50.05958787633251
                ],
                [
                  19.923418014226424,
                  50.05958776516763
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-009-0000-0000",
            "relation": "neighbour",
            "distance_m": 15.5,
            "floors": 1,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-019-0000-0000",
        "distance_m": 15.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92341835958304,
                  50.05960575614972
                ],
                [
                  19.923418618600728,
                  50.05961924938624
                ],
                [
                  19.92340464553042,
                  50.05961936055125
                ],
                [
                  19.92340438651665,
                  50.059605867314666
                ],
                [
                  19.92341835958304,
                  50.05960575614972
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-019-0000-0000",
            "relation": "neighbour",
            "distance_m": 15.5,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "CROWD-029-0000-0000",
        "distance_m": 15.5,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 1.5,
        "general_function": "budynki mieszkalne",
        "detailed_function": null,
        "predominant_function": null,
        "floors": 3,
        "geometry_source": "EGiB",
        "feature_version": null,
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92341870494,
                  50.059623747131745
                ],
                [
                  19.923418963957953,
                  50.059637240368225
                ],
                [
                  19.923404990882425,
                  50.0596373515333
                ],
                [
                  19.92340473186839,
                  50.05962385829677
                ],
                [
                  19.92341870494,
                  50.059623747131745
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "CROWD-029-0000-0000",
            "relation": "neighbour",
            "distance_m": 15.5,
            "floors": 3,
            "function": "budynki mieszkalne"
          }
        }
      }
    ],
    "buildings_truncated": false,
    "listed_radius_m": 50.0,
    "candidates_considered": 30,
    "search_area_within_coverage": true,
    "release": {
      "data_release_id": 21,
      "version_label": "bdot10k-1261-1355c5a51538",
      "teryt_powiat": "1261",
      "data_as_of": "2026-01-22",
      "package_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "imported_at": "2026-09-02T09:00:00Z"
    },
    "interpretation_note": "Kontekst istniejącej zabudowy z obrysów BDOT10k (w większości z EGiB): opis tego, co stoi w otoczeniu działki, a nie ocena możliwości zabudowy. Dane bez nazw i właścicieli; stan na datę wskazaną przy wydaniu danych.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "empty": {
    "schema_version": "1.0",
    "status": "available",
    "reason_code": null,
    "ring_definition": "buffer(parcel, r) - parcel",
    "distance_reference": "parcel_polygon_to_building_footprint",
    "buffers_m": [
      50.0,
      100.0,
      200.0
    ],
    "parcel_area_sqm": 2400.0,
    "on_parcel": {
      "count": 0,
      "union_area_in_parcel_sqm": 0.0,
      "coverage_pct": 0.0
    },
    "buffers": [
      {
        "radius_m": 50.0,
        "ring_area_sqm": 17841.3712,
        "count": 0,
        "straddling_parcel_count": 0,
        "footprint_area_in_ring_sqm": 0.0,
        "union_area_in_ring_sqm": 0.0,
        "built_share_pct": 0.0,
        "min_distance_m": null,
        "footprint_min_sqm": null,
        "footprint_median_sqm": null,
        "footprint_max_sqm": null
      },
      {
        "radius_m": 100.0,
        "ring_area_sqm": 51365.4849,
        "count": 0,
        "straddling_parcel_count": 0,
        "footprint_area_in_ring_sqm": 0.0,
        "union_area_in_ring_sqm": 0.0,
        "built_share_pct": 0.0,
        "min_distance_m": null,
        "footprint_min_sqm": null,
        "footprint_median_sqm": null,
        "footprint_max_sqm": null
      },
      {
        "radius_m": 200.0,
        "ring_area_sqm": 165461.9396,
        "count": 0,
        "straddling_parcel_count": 0,
        "footprint_area_in_ring_sqm": 0.0,
        "union_area_in_ring_sqm": 0.0,
        "built_share_pct": 0.0,
        "min_distance_m": null,
        "footprint_min_sqm": null,
        "footprint_median_sqm": null,
        "footprint_max_sqm": null
      }
    ],
    "buildings": [],
    "buildings_truncated": false,
    "listed_radius_m": 50.0,
    "candidates_considered": 0,
    "search_area_within_coverage": true,
    "release": {
      "data_release_id": 21,
      "version_label": "bdot10k-1261-1355c5a51538",
      "teryt_powiat": "1261",
      "data_as_of": "2026-01-22",
      "package_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "imported_at": "2026-09-02T09:00:00Z"
    },
    "interpretation_note": "Kontekst istniejącej zabudowy z obrysów BDOT10k (w większości z EGiB): opis tego, co stoi w otoczeniu działki, a nie ocena możliwości zabudowy. Dane bez nazw i właścicieli; stan na datę wskazaną przy wydaniu danych.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "partial_coverage": {
    "schema_version": "1.0",
    "status": "available",
    "reason_code": null,
    "ring_definition": "buffer(parcel, r) - parcel",
    "distance_reference": "parcel_polygon_to_building_footprint",
    "buffers_m": [
      50.0,
      100.0,
      200.0
    ],
    "parcel_area_sqm": 2400.0,
    "on_parcel": {
      "count": 2,
      "union_area_in_parcel_sqm": 194.0,
      "coverage_pct": 8.0833
    },
    "buffers": [
      {
        "radius_m": 50.0,
        "ring_area_sqm": 17841.3712,
        "count": 2,
        "straddling_parcel_count": 1,
        "footprint_area_in_ring_sqm": 150.0,
        "union_area_in_ring_sqm": 150.0,
        "built_share_pct": 0.8407,
        "min_distance_m": 0.0,
        "footprint_min_sqm": 100.0,
        "footprint_median_sqm": 100.0,
        "footprint_max_sqm": 100.0
      },
      {
        "radius_m": 100.0,
        "ring_area_sqm": 51365.4849,
        "count": 3,
        "straddling_parcel_count": 1,
        "footprint_area_in_ring_sqm": 250.0,
        "union_area_in_ring_sqm": 250.0,
        "built_share_pct": 0.4867,
        "min_distance_m": 0.0,
        "footprint_min_sqm": 100.0,
        "footprint_median_sqm": 100.0,
        "footprint_max_sqm": 100.0
      },
      {
        "radius_m": 200.0,
        "ring_area_sqm": 165461.9396,
        "count": 3,
        "straddling_parcel_count": 1,
        "footprint_area_in_ring_sqm": 250.0,
        "union_area_in_ring_sqm": 250.0,
        "built_share_pct": 0.1511,
        "min_distance_m": 0.0,
        "footprint_min_sqm": 100.0,
        "footprint_median_sqm": 100.0,
        "footprint_max_sqm": 100.0
      }
    ],
    "buildings": [
      {
        "building_id": "B-0001",
        "distance_m": 0.0,
        "relation": "on_parcel",
        "overlap_with_parcel_sqm": 144.0,
        "footprint_sqm": 144.0,
        "general_function": "budynki mieszkalne",
        "detailed_function": "budynek jednorodzinny",
        "predominant_function": "budynek jednorodzinny",
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": "2026-01-22T00:00:00+00:00",
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.922658207628746,
                  50.05968377613063
                ],
                [
                  19.922660278075853,
                  50.05979172204359
                ],
                [
                  19.922492600589482,
                  50.05979305482051
                ],
                [
                  19.922490530518516,
                  50.05968510890249
                ],
                [
                  19.922658207628746,
                  50.05968377613063
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "B-0001",
            "relation": "on_parcel",
            "distance_m": 0.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      },
      {
        "building_id": "B-0002",
        "distance_m": 0.0,
        "relation": "straddling",
        "overlap_with_parcel_sqm": 50.0,
        "footprint_sqm": 100.0,
        "general_function": "budynki produkcyjne, usługowe i gospodarcze dla rolnictwa",
        "detailed_function": "budynek gospodarczy",
        "predominant_function": "budynek gospodarczy",
        "floors": 1,
        "geometry_source": "EGiB",
        "feature_version": "2026-01-22T00:00:00+00:00",
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.9232599137503,
                  50.05972397583251
                ],
                [
                  19.92326164024957,
                  50.05981393074426
                ],
                [
                  19.923121908970945,
                  50.05981504213275
                ],
                [
                  19.923120182732887,
                  50.05972508721749
                ],
                [
                  19.9232599137503,
                  50.05972397583251
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "B-0002",
            "relation": "straddling",
            "distance_m": 0.0,
            "floors": 1,
            "function": "budynki produkcyjne, usługowe i gospodarcze dla rolnictwa"
          }
        }
      },
      {
        "building_id": "B-0003",
        "distance_m": 20.0,
        "relation": "neighbour",
        "overlap_with_parcel_sqm": 0.0,
        "footprint_sqm": 100.0,
        "general_function": "budynki mieszkalne",
        "detailed_function": "budynek jednorodzinny",
        "predominant_function": "budynek jednorodzinny",
        "floors": 2,
        "geometry_source": "EGiB",
        "feature_version": "2026-01-22T00:00:00+00:00",
        "geometry_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.92207133768801,
                  50.05968843977254
                ],
                [
                  19.92207306196266,
                  50.059778394714925
                ],
                [
                  19.921933330740522,
                  50.05977950467211
                ],
                [
                  19.921931606727078,
                  50.05968954972621
                ],
                [
                  19.92207133768801,
                  50.05968843977254
                ]
              ]
            ]
          },
          "properties": {
            "layer": "neighbour_buildings",
            "building_id": "B-0003",
            "relation": "neighbour",
            "distance_m": 20.0,
            "floors": 2,
            "function": "budynki mieszkalne"
          }
        }
      }
    ],
    "buildings_truncated": false,
    "listed_radius_m": 50.0,
    "candidates_considered": 4,
    "search_area_within_coverage": false,
    "release": {
      "data_release_id": 21,
      "version_label": "bdot10k-1261-1355c5a51538",
      "teryt_powiat": "1261",
      "data_as_of": "2026-01-22",
      "package_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "imported_at": "2026-09-02T09:00:00Z"
    },
    "interpretation_note": "Kontekst istniejącej zabudowy z obrysów BDOT10k (w większości z EGiB): opis tego, co stoi w otoczeniu działki, a nie ocena możliwości zabudowy. Dane bez nazw i właścicieli; stan na datę wskazaną przy wydaniu danych.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "no_coverage": {
    "schema_version": "1.0",
    "status": "no_coverage",
    "reason_code": "OUTSIDE_COVERAGE",
    "ring_definition": "buffer(parcel, r) - parcel",
    "distance_reference": "parcel_polygon_to_building_footprint",
    "buffers_m": [
      50.0,
      100.0,
      200.0
    ],
    "parcel_area_sqm": null,
    "on_parcel": null,
    "buffers": [],
    "buildings": [],
    "buildings_truncated": false,
    "listed_radius_m": null,
    "candidates_considered": null,
    "search_area_within_coverage": null,
    "release": null,
    "interpretation_note": "Kontekst istniejącej zabudowy z obrysów BDOT10k (w większości z EGiB): opis tego, co stoi w otoczeniu działki, a nie ocena możliwości zabudowy. Dane bez nazw i właścicieli; stan na datę wskazaną przy wydaniu danych.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  },
  "unavailable": {
    "schema_version": "1.0",
    "status": "unavailable",
    "reason_code": "QUERY_TIMEOUT",
    "ring_definition": "buffer(parcel, r) - parcel",
    "distance_reference": "parcel_polygon_to_building_footprint",
    "buffers_m": [
      50.0,
      100.0,
      200.0
    ],
    "parcel_area_sqm": null,
    "on_parcel": null,
    "buffers": [],
    "buildings": [],
    "buildings_truncated": false,
    "listed_radius_m": null,
    "candidates_considered": null,
    "search_area_within_coverage": null,
    "release": null,
    "interpretation_note": "Kontekst istniejącej zabudowy z obrysów BDOT10k (w większości z EGiB): opis tego, co stoi w otoczeniu działki, a nie ocena możliwości zabudowy. Dane bez nazw i właścicieli; stan na datę wskazaną przy wydaniu danych.",
    "source": {
      "data_as_of": "2026-01-22",
      "data_as_of_basis": "BDOT10k: data wersji obiektów w paczce",
      "data_as_of_reason": null,
      "source_id": "bdot10k",
      "source_version": "bdot10k-1261-1355c5a51538",
      "artifact_sha256": "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd4",
      "data_release_id": 21,
      "act_version": null,
      "source_name": "BDOT10k",
      "source_url": "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip",
      "fetched_at": "2026-09-02T09:00:00Z",
      "response_status": null,
      "confidence": 0.9,
      "manual_review_required": false,
      "data_as_of_status": "known"
    },
    "warnings": []
  }
};
