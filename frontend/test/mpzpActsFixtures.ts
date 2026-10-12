import type { MpzpActResult, MpzpStatus } from "@/lib/types";

/**
 * Akty i stan MPZP wygenerowane przez backend (`acts_to_contract` + `build_mpzp_status`) z zamrożonych,
 * rzeczywistych odpowiedzi KIMPZP i Rejestru Urbanistycznego (2026-10-10), po jednym przypadku na stan:
 *
 * - `ambiguous`   — Góra Kalwaria 141801_4.0701.23/8: IV/30/2024 i 576/XLVII/2010 z KIMPZP bez rozstrzygnięcia,
 *                    projekt cz. II z RU odrzucony (granica ok. 6 m od działki);
 * - `available`   — Bielsko-Biała 246101_1.0056.155/3: KIMPZP zwraca błąd usługi, RU wskazuje plan „Starówka”
 *                    (LIII/749/1998) pokrywający 11,84% działki;
 * - `no_coverage` — Dygowo: KIMPZP „brak serwisu dla wskazanego obszaru”, RU bez aktu;
 * - `unavailable` — Warszawa: KIMPZP `<oms_error>`, RU bez aktu (błąd źródła nie daje „nie znaleziono”);
 * - `no_match`    — KIMPZP „brak wyniku” (Ruciane-Nida) i pusta odpowiedź RU;
 * - `unknown`     — rozpoznanie KIMPZP się nie wykonało, kanał RU nieodpytany.
 */
export type MpzpActsCase = { mpzp_acts: MpzpActResult[]; mpzp_status: MpzpStatus };

export const MPZP_ACTS_CASES = {
  "ambiguous": {
    "mpzp_acts": [
      {
        "act_identifier": null,
        "title": "Miejscowy plany zagospodarowania przestrzennego dla fragmentu miasta Góra Kalwaria - rejon ul. Lipkowskiej cz. I",
        "resolution_number": "IV/30/2024",
        "resolution_number_basis": "kimpzp",
        "resolution_date": "2024-06-05",
        "effective_from": "2024-06-26",
        "effective_to": null,
        "legal_status": "binding",
        "raw_status": "binding",
        "document_url": "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf",
        "document_kind": "adopting",
        "legend_url": "http://mpzp.gorakalwaria.pl/portal/mpzp/leg/IV_30_2024_leg.pdf",
        "drawing_url": null,
        "journal": "Dz. Urz. Woj. Maz. 2024 poz. 6032 z 11.06.2024r.",
        "amendments": [],
        "intersection_area_sqm": null,
        "intersection_pct": null,
        "intersection_geojson": null,
        "boundary_geojson": null,
        "sources": [
          "kimpzp"
        ],
        "precedence": "unresolved",
        "manual_review_required": true,
        "reason_codes": [
          "MPZP_ACT_OVERLAP_UNRESOLVED",
          "MPZP_ACT_NO_GEOMETRY"
        ],
        "verified_links": []
      },
      {
        "act_identifier": null,
        "title": "Miejscowy plan zagospodarowania przestrzennego dla fragmentu miasta Góra Kalwaria oraz dla fragmentu wsi Moczydłów - rejon ul. Lipkowskiej",
        "resolution_number": "576/XLVII/2010",
        "resolution_number_basis": "kimpzp",
        "resolution_date": "2010-04-28",
        "effective_from": "2010-08-24",
        "effective_to": null,
        "legal_status": "binding",
        "raw_status": "binding",
        "document_url": "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/576_XLVII_2010.pdf",
        "document_kind": "adopting",
        "legend_url": "http://mpzp.gorakalwaria.pl/portal/mpzp/leg/576_XLVII_2010_leg.pdf",
        "drawing_url": null,
        "journal": "Dz. Urz. Woj. Maz. Nr 142, poz. 3400",
        "amendments": [
          {
            "resolution_number": null,
            "effective_from": null,
            "document_url": null,
            "name": "text planu zmieniony uchwałą XXXIX/366/2017 (patrz poz. 122); Wyrok Sądu IV SA/Wa 1818/18\" z dnia 13 listopada 2018 r. dla działki 13 z obrębu 7-01; fragment planu zmieniony uchwałą LIV/467/2021 (patrz poz. 154); text planu zmieniony uchwałą LIV/467/2021 (patrz poz. 176);",
            "source": "kimpzp",
            "document_url_verified": false
          },
          {
            "resolution_number": "LIV/467/2021",
            "effective_from": "2021-07-17",
            "document_url": "https://bip-v1-files.idcom-jst.pl/sites/47313/wiadomosci/582692/files/liv_467_2021.pdf",
            "name": "Zmiana miejscowego planu zagospodarowania przestrzennego dla fragmentu wsi Moczydłów – rejon ul. Lipkowskiej – ETAP I",
            "source": "kimpzp",
            "document_url_verified": true
          },
          {
            "resolution_number": "XXXIX/366/2017",
            "effective_from": "2017-03-16",
            "document_url": "https://bip-v1-files.idcom-jst.pl/sites/47313/wiadomosci/554352/files/xxxix_366_2017.pdf",
            "name": "Miejscowy plan zagospodarowania przestrzennego dla fragmentu miasta Góra Kalwaria oraz dla fragmentu wsi Moczydłów – rejon ul. Lipkowskiej",
            "source": "kimpzp",
            "document_url_verified": true
          }
        ],
        "intersection_area_sqm": null,
        "intersection_pct": null,
        "intersection_geojson": null,
        "boundary_geojson": null,
        "sources": [
          "kimpzp"
        ],
        "precedence": "unresolved",
        "manual_review_required": true,
        "reason_codes": [
          "MPZP_ACT_HAS_AMENDMENTS",
          "MPZP_ACT_OVERLAP_UNRESOLVED",
          "MPZP_ACT_NO_GEOMETRY"
        ],
        "verified_links": []
      }
    ],
    "mpzp_status": {
      "schema_version": "1.0",
      "status": "ambiguous",
      "reason_code": "MPZP_ACTS_UNRESOLVED",
      "reason_codes": [
        "MPZP_ACTS_UNRESOLVED",
        "MPZP_MULTIPLE_ACTS_AT_POINT",
        "MPZP_RU_NO_ACT_AT_PARCEL"
      ],
      "label": "Wiele planów — brak rozstrzygnięcia",
      "message": "Działkę obejmuje więcej niż jeden akt MPZP, a dane nie rozstrzygają, który z nich decyduje o przeznaczeniu. System nie wybiera aktu: wszystkie uchwały są wymienione i wymagają ręcznej weryfikacji w treści dokumentów.",
      "channels": [
        {
          "channel": "kimpzp",
          "label": "KIMPZP (GUGiK)",
          "status": "available",
          "status_label": "wskazał akt",
          "reason_codes": [
            "MPZP_MULTIPLE_ACTS_AT_POINT"
          ],
          "acts_found": 2,
          "source": {
            "source_id": "kimpzp",
            "source_version": null,
            "artifact_sha256": null,
            "data_release_id": null,
            "act_version": null,
            "source_name": "KIMPZP",
            "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
            "fetched_at": "2026-10-10T20:23:12.974001Z",
            "response_status": null,
            "confidence": 0.6,
            "manual_review_required": true
          }
        },
        {
          "channel": "mpzp_ru",
          "label": "Rejestr Urbanistyczny (MRiT)",
          "status": "no_match",
          "status_label": "nie wskazał aktu",
          "reason_codes": [
            "MPZP_RU_NO_ACT_AT_PARCEL"
          ],
          "acts_found": 0,
          "source": {
            "source_id": "mpzp_ru",
            "source_version": null,
            "artifact_sha256": "bf9f5dc5bbaf4034b1c9174fa450d714dca72bc34ce5ebfa5fdbe78b2cdbb94e",
            "data_release_id": null,
            "act_version": null,
            "source_name": "Rejestr Urbanistyczny (MPZP)",
            "source_url": "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs",
            "fetched_at": "2026-10-10T20:23:13.052287Z",
            "response_status": 200,
            "confidence": 0.6,
            "manual_review_required": true
          }
        }
      ],
      "channel_agreement": "single_channel",
      "precedence_rule_version": "mpzp-act-precedence/1.0",
      "rejected_acts": [
        {
          "act_identifier": "PL.ZIPPZP.6728/141801-MPZP/XXIV_302_2026_P1",
          "title": "Miejscowy plan zagospodarowania przestrzennego dla fragmentu miasta Góra Kalwaria – rejon ul. Lipkowskiej – cz. II",
          "resolution_number": null,
          "legal_status": "not_yet_binding",
          "raw_status": "w opracowaniu",
          "reason": "no_overlap",
          "distance_m": 5.994,
          "area_sqm": 0.0,
          "channel": "mpzp_ru"
        }
      ],
      "source": {
        "source_id": "kimpzp",
        "source_version": null,
        "artifact_sha256": null,
        "data_release_id": null,
        "act_version": null,
        "source_name": "KIMPZP",
        "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
        "fetched_at": "2026-10-10T20:23:12.974001Z",
        "response_status": null,
        "confidence": 0.6,
        "manual_review_required": true
      }
    }
  },
  "available": {
    "mpzp_acts": [
      {
        "act_identifier": "PL.ZIPPZP.2617/246101-MPZP/006_G_1-2",
        "title": "Miejscowy plan zagospodarowania przestrzennego bielskiej Starówki",
        "resolution_number": "LIII/749/1998",
        "resolution_number_basis": "document_number",
        "resolution_date": "1998-03-17",
        "effective_from": "1998-04-01",
        "effective_to": null,
        "legal_status": "binding",
        "raw_status": "prawnie wiążący lub realizowany",
        "document_url": "https://brm.bielsko-biala.pl/wp-content/uploads/2021/01/006_uchwala.pdf",
        "document_kind": "adopting",
        "legend_url": "https://brm.bielsko-biala.pl/wp-content/uploads/2022/01/006_L-scaled.jpg",
        "drawing_url": "https://brm.bielsko-biala.pl/wp-content/uploads/2022/01/006_R_1.tif",
        "journal": "Dziennik Urzędowy Woj. Śląskiego",
        "amendments": [
          {
            "resolution_number": "XLIV/1038/2022",
            "effective_from": "2022-06-02",
            "document_url": "https://brm.bielsko-biala.pl/wp-content/uploads/2022/05/243_Uchwala.pdf",
            "name": "UCHWAŁA NR XLIV/1038/2022 RADY MIEJSKIEJ W BIELSKU-BIAŁEJ z dnia 21 kwietnia 2022 r. w sprawie zmiany miejscowego planu zagospodarowania przestrzennego bielskiej Starówki",
            "source": "mpzp_ru",
            "document_url_verified": true
          }
        ],
        "intersection_area_sqm": 167.44,
        "intersection_pct": 11.84,
        "intersection_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.044987,
                  49.822194
                ],
                [
                  19.044835,
                  49.82223
                ],
                [
                  19.044836,
                  49.822292
                ],
                [
                  19.044837,
                  49.822339
                ],
                [
                  19.04484,
                  49.82234
                ],
                [
                  19.045172,
                  49.822179
                ],
                [
                  19.044987,
                  49.822194
                ]
              ]
            ]
          },
          "properties": {
            "layer": "mpzp_act_intersection",
            "resolution_number": "LIII/749/1998",
            "act_identifier": "PL.ZIPPZP.2617/246101-MPZP/006_G_1-2",
            "legal_status": "binding",
            "precedence": "applies",
            "sources": [
              "mpzp_ru"
            ],
            "label": "LIII/749/1998"
          }
        },
        "boundary_geojson": {
          "type": "Feature",
          "geometry": {
            "type": "Polygon",
            "coordinates": [
              [
                [
                  19.043444,
                  49.822533
                ],
                [
                  19.04414,
                  49.822637
                ],
                [
                  19.04422,
                  49.822406
                ],
                [
                  19.044198,
                  49.822403
                ],
                [
                  19.044198,
                  49.822313
                ],
                [
                  19.044334,
                  49.82231
                ],
                [
                  19.04484,
                  49.82234
                ],
                [
                  19.045225,
                  49.822154
                ],
                [
                  19.045234,
                  49.82193
                ],
                [
                  19.045142,
                  49.821565
                ],
                [
                  19.045055,
                  49.821417
                ],
                [
                  19.044934,
                  49.821271
                ],
                [
                  19.043443,
                  49.821271
                ],
                [
                  19.043444,
                  49.822533
                ]
              ]
            ]
          },
          "properties": {
            "layer": "mpzp_act_boundary",
            "resolution_number": "LIII/749/1998",
            "act_identifier": "PL.ZIPPZP.2617/246101-MPZP/006_G_1-2",
            "legal_status": "binding",
            "precedence": "applies",
            "sources": [
              "mpzp_ru"
            ],
            "label": "LIII/749/1998"
          }
        },
        "sources": [
          "mpzp_ru"
        ],
        "precedence": "applies",
        "manual_review_required": false,
        "reason_codes": [
          "MPZP_ACT_HAS_AMENDMENTS"
        ],
        "verified_links": [
          "document_url",
          "legend_url",
          "drawing_url"
        ]
      }
    ],
    "mpzp_status": {
      "schema_version": "1.0",
      "status": "available",
      "reason_code": "MPZP_ACT_FOUND",
      "reason_codes": [
        "MPZP_ACT_FOUND",
        "KIMPZP_SERVICE_ERROR",
        "MPZP_DISCOVERY_UNAVAILABLE"
      ],
      "label": "Ustalono akt MPZP",
      "message": "Dla działki ustalono co najmniej jeden akt MPZP przecinający jej obszar (źródło, udział powierzchni i dokument są w tabeli aktów). Wynik jest informacyjny i nie zastępuje wypisu z planu miejscowego.",
      "channels": [
        {
          "channel": "kimpzp",
          "label": "KIMPZP (GUGiK)",
          "status": "unavailable",
          "status_label": "błąd lub brak odpowiedzi",
          "reason_codes": [
            "KIMPZP_SERVICE_ERROR",
            "MPZP_DISCOVERY_UNAVAILABLE"
          ],
          "acts_found": 0,
          "source": {
            "source_id": "kimpzp",
            "source_version": null,
            "artifact_sha256": null,
            "data_release_id": null,
            "act_version": null,
            "source_name": "KIMPZP",
            "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
            "fetched_at": "2026-10-10T20:23:13.056190Z",
            "response_status": null,
            "confidence": 0.3,
            "manual_review_required": true
          }
        },
        {
          "channel": "mpzp_ru",
          "label": "Rejestr Urbanistyczny (MRiT)",
          "status": "available",
          "status_label": "wskazał akt",
          "reason_codes": [],
          "acts_found": 1,
          "source": {
            "source_id": "mpzp_ru",
            "source_version": null,
            "artifact_sha256": "9c94296808083e001dd8ad5ae50fbbd22978c057cfe79ccd36e31d20e1fa9b54",
            "data_release_id": null,
            "act_version": null,
            "source_name": "Rejestr Urbanistyczny (MPZP)",
            "source_url": "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs",
            "fetched_at": "2026-10-10T20:23:13.063454Z",
            "response_status": 200,
            "confidence": 0.8,
            "manual_review_required": false
          }
        }
      ],
      "channel_agreement": "single_channel",
      "precedence_rule_version": "mpzp-act-precedence/1.0",
      "rejected_acts": [],
      "source": {
        "source_id": "mpzp_ru",
        "source_version": null,
        "artifact_sha256": "9c94296808083e001dd8ad5ae50fbbd22978c057cfe79ccd36e31d20e1fa9b54",
        "data_release_id": null,
        "act_version": null,
        "source_name": "Rejestr Urbanistyczny (MPZP)",
        "source_url": "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs",
        "fetched_at": "2026-10-10T20:23:13.063454Z",
        "response_status": 200,
        "confidence": 0.8,
        "manual_review_required": false
      }
    }
  },
  "no_coverage": {
    "mpzp_acts": [],
    "mpzp_status": {
      "schema_version": "1.0",
      "status": "no_coverage",
      "reason_code": "KIMPZP_NO_SERVICE_FOR_AREA",
      "reason_codes": [
        "KIMPZP_NO_SERVICE_FOR_AREA",
        "MPZP_RU_NO_ACT_AT_PARCEL"
      ],
      "label": "Brak pokrycia danymi",
      "message": "Dla obszaru działki nie ma usługi planistycznej (KIMPZP: „brak serwisu dla wskazanego obszaru”), a Rejestr Urbanistyczny nie wskazał aktu. To brak danych, a nie brak planu — sprawdź MPZP w gminie.",
      "channels": [
        {
          "channel": "kimpzp",
          "label": "KIMPZP (GUGiK)",
          "status": "no_coverage",
          "status_label": "brak usługi dla obszaru",
          "reason_codes": [
            "KIMPZP_NO_SERVICE_FOR_AREA"
          ],
          "acts_found": 0,
          "source": {
            "source_id": "kimpzp",
            "source_version": null,
            "artifact_sha256": null,
            "data_release_id": null,
            "act_version": null,
            "source_name": "KIMPZP",
            "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
            "fetched_at": "2026-10-10T20:23:13.077592Z",
            "response_status": null,
            "confidence": 0.3,
            "manual_review_required": true
          }
        },
        {
          "channel": "mpzp_ru",
          "label": "Rejestr Urbanistyczny (MRiT)",
          "status": "no_match",
          "status_label": "nie wskazał aktu",
          "reason_codes": [
            "MPZP_RU_NO_ACT_AT_PARCEL"
          ],
          "acts_found": 0,
          "source": {
            "source_id": "mpzp_ru",
            "source_version": null,
            "artifact_sha256": "d24342127e9c092cb5096a94abf661a4d88c61df3171d1e4bb70c5fcdfa157a9",
            "data_release_id": null,
            "act_version": null,
            "source_name": "Rejestr Urbanistyczny (MPZP)",
            "source_url": "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs",
            "fetched_at": "2026-10-10T20:23:13.085796Z",
            "response_status": 200,
            "confidence": 0.6,
            "manual_review_required": true
          }
        }
      ],
      "channel_agreement": "none",
      "precedence_rule_version": "mpzp-act-precedence/1.0",
      "rejected_acts": [],
      "source": {
        "source_id": "kimpzp",
        "source_version": null,
        "artifact_sha256": null,
        "data_release_id": null,
        "act_version": null,
        "source_name": "KIMPZP",
        "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
        "fetched_at": "2026-10-10T20:23:13.077592Z",
        "response_status": null,
        "confidence": 0.3,
        "manual_review_required": true
      }
    }
  },
  "unavailable": {
    "mpzp_acts": [],
    "mpzp_status": {
      "schema_version": "1.0",
      "status": "unavailable",
      "reason_code": "MPZP_SOURCE_UNAVAILABLE",
      "reason_codes": [
        "MPZP_SOURCE_UNAVAILABLE",
        "KIMPZP_SERVICE_ERROR",
        "MPZP_DISCOVERY_UNAVAILABLE",
        "MPZP_RU_NO_ACT_AT_PARCEL"
      ],
      "label": "Nie sprawdzono — błąd źródła",
      "message": "Usługa MPZP nie odpowiedziała poprawnie (błąd albo przekroczony czas), więc nie ustalono, czy dla działki obowiązuje plan. Błąd źródła nie oznacza braku ograniczeń — ponów analizę później albo sprawdź MPZP w gminie.",
      "channels": [
        {
          "channel": "kimpzp",
          "label": "KIMPZP (GUGiK)",
          "status": "unavailable",
          "status_label": "błąd lub brak odpowiedzi",
          "reason_codes": [
            "KIMPZP_SERVICE_ERROR",
            "MPZP_DISCOVERY_UNAVAILABLE"
          ],
          "acts_found": 0,
          "source": {
            "source_id": "kimpzp",
            "source_version": null,
            "artifact_sha256": null,
            "data_release_id": null,
            "act_version": null,
            "source_name": "KIMPZP",
            "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
            "fetched_at": "2026-10-10T20:23:13.089191Z",
            "response_status": null,
            "confidence": 0.3,
            "manual_review_required": true
          }
        },
        {
          "channel": "mpzp_ru",
          "label": "Rejestr Urbanistyczny (MRiT)",
          "status": "no_match",
          "status_label": "nie wskazał aktu",
          "reason_codes": [
            "MPZP_RU_NO_ACT_AT_PARCEL"
          ],
          "acts_found": 0,
          "source": {
            "source_id": "mpzp_ru",
            "source_version": null,
            "artifact_sha256": "d5da7368ecb16e60b5cbdabf5e294552a76a3b89643d7acdbbd269113f45116e",
            "data_release_id": null,
            "act_version": null,
            "source_name": "Rejestr Urbanistyczny (MPZP)",
            "source_url": "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs",
            "fetched_at": "2026-10-10T20:23:13.096402Z",
            "response_status": 200,
            "confidence": 0.6,
            "manual_review_required": true
          }
        }
      ],
      "channel_agreement": "none",
      "precedence_rule_version": "mpzp-act-precedence/1.0",
      "rejected_acts": [],
      "source": {
        "source_id": "kimpzp",
        "source_version": null,
        "artifact_sha256": null,
        "data_release_id": null,
        "act_version": null,
        "source_name": "KIMPZP",
        "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
        "fetched_at": "2026-10-10T20:23:13.089191Z",
        "response_status": null,
        "confidence": 0.3,
        "manual_review_required": true
      }
    }
  },
  "no_match": {
    "mpzp_acts": [],
    "mpzp_status": {
      "schema_version": "1.0",
      "status": "no_match",
      "reason_code": "MPZP_NO_ACT_AT_PARCEL",
      "reason_codes": [
        "MPZP_NO_ACT_AT_PARCEL",
        "KIMPZP_NO_RESULT_FOR_AREA",
        "MPZP_RU_NO_ACT_AT_PARCEL"
      ],
      "label": "Nie znaleziono planu",
      "message": "Usługi odpowiedziały, ale nie wskazały aktu MPZP przecinającego działkę. To obserwacja z dnia analizy, a nie dowód braku planu ani braku ograniczeń — sprawdź MPZP w gminie.",
      "channels": [
        {
          "channel": "kimpzp",
          "label": "KIMPZP (GUGiK)",
          "status": "no_match",
          "status_label": "nie wskazał aktu",
          "reason_codes": [
            "KIMPZP_NO_RESULT_FOR_AREA"
          ],
          "acts_found": 0,
          "source": {
            "source_id": "kimpzp",
            "source_version": null,
            "artifact_sha256": null,
            "data_release_id": null,
            "act_version": null,
            "source_name": "KIMPZP",
            "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
            "fetched_at": "2026-10-10T20:23:13.099204Z",
            "response_status": null,
            "confidence": 0.3,
            "manual_review_required": true
          }
        },
        {
          "channel": "mpzp_ru",
          "label": "Rejestr Urbanistyczny (MRiT)",
          "status": "no_match",
          "status_label": "nie wskazał aktu",
          "reason_codes": [
            "MPZP_RU_NO_ACT_AT_PARCEL"
          ],
          "acts_found": 0,
          "source": {
            "source_id": "mpzp_ru",
            "source_version": null,
            "artifact_sha256": "d5da7368ecb16e60b5cbdabf5e294552a76a3b89643d7acdbbd269113f45116e",
            "data_release_id": null,
            "act_version": null,
            "source_name": "Rejestr Urbanistyczny (MPZP)",
            "source_url": "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs",
            "fetched_at": "2026-10-10T20:23:13.106864Z",
            "response_status": 200,
            "confidence": 0.6,
            "manual_review_required": true
          }
        }
      ],
      "channel_agreement": "none",
      "precedence_rule_version": "mpzp-act-precedence/1.0",
      "rejected_acts": [],
      "source": {
        "source_id": "kimpzp",
        "source_version": null,
        "artifact_sha256": null,
        "data_release_id": null,
        "act_version": null,
        "source_name": "KIMPZP",
        "source_url": "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
        "fetched_at": "2026-10-10T20:23:13.099204Z",
        "response_status": null,
        "confidence": 0.3,
        "manual_review_required": true
      }
    }
  },
  "unknown": {
    "mpzp_acts": [],
    "mpzp_status": {
      "schema_version": "1.0",
      "status": "unknown",
      "reason_code": "MPZP_NOT_DETERMINED",
      "reason_codes": [
        "MPZP_NOT_DETERMINED",
        "MPZP_DISCOVERY_ERROR"
      ],
      "label": "Nie ustalono",
      "message": "Odpowiedź źródeł nie pozwala ustalić, czy dla działki obowiązuje plan (nierozpoznany format albo rozpoznanie się nie wykonało). Wymagana ręczna weryfikacja MPZP w gminie.",
      "channels": [
        {
          "channel": "kimpzp",
          "label": "KIMPZP (GUGiK)",
          "status": "unknown",
          "status_label": "odpowiedź nierozpoznana",
          "reason_codes": [
            "MPZP_DISCOVERY_ERROR"
          ],
          "acts_found": 0,
          "source": null
        }
      ],
      "channel_agreement": "none",
      "precedence_rule_version": "mpzp-act-precedence/1.0",
      "rejected_acts": [],
      "source": null
    }
  }
} as unknown as Record<
  "ambiguous" | "available" | "no_coverage" | "unavailable" | "no_match" | "unknown",
  MpzpActsCase
>;
