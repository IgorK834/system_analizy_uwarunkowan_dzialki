import type { MpzpDiscoverySection } from "@/lib/types";

/**
 * Sekcja `mpzp_discovery` wygenerowana przez backend z zamrożonej odpowiedzi KIMPZP
 * dla działki 141801_4.0701.23/8 (Góra Kalwaria, AU-004); pole `source` pominięte.
 */
export const GORA_KALWARIA_DISCOVERY: MpzpDiscoverySection = {
  "schema_version": "1.0",
  "status": "available",
  "reason_codes": [
    "MPZP_MULTIPLE_ACTS_AT_POINT"
  ],
  "acts": [
    {
      "resolution_number": "IV/30/2024",
      "resolution_date": "2024-06-05",
      "name": "Miejscowy plany zagospodarowania przestrzennego dla fragmentu miasta Góra Kalwaria - rejon ul. Lipkowskiej cz. I",
      "valid_from": "2024-06-26",
      "repealed_on": null,
      "legal_status": "binding",
      "text_url": "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf",
      "legend_url": "http://mpzp.gorakalwaria.pl/portal/mpzp/leg/IV_30_2024_leg.pdf",
      "drawing_url": null,
      "bip_url": "https://bip.gorakalwaria.pl/wiadomosci/14246/wiadomosc/757992",
      "www_url": null,
      "journal": "Dz. Urz. Woj. Maz. 2024 poz. 6032 z 11.06.2024r.",
      "informatization": "unknown",
      "zone_symbols": [],
      "amendments": [],
      "source_format": "plan_block",
      "verified_links": [
        "bip_url"
      ]
    },
    {
      "resolution_number": "576/XLVII/2010",
      "resolution_date": "2010-04-28",
      "name": "Miejscowy plan zagospodarowania przestrzennego dla fragmentu miasta Góra Kalwaria oraz dla fragmentu wsi Moczydłów - rejon ul. Lipkowskiej",
      "valid_from": "2010-08-24",
      "repealed_on": null,
      "legal_status": "binding",
      "text_url": "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/576_XLVII_2010.pdf",
      "legend_url": "http://mpzp.gorakalwaria.pl/portal/mpzp/leg/576_XLVII_2010_leg.pdf",
      "drawing_url": null,
      "bip_url": "https://bip.gorakalwaria.pl/wiadomosci/14246/wiadomosc/554058",
      "www_url": null,
      "journal": "Dz. Urz. Woj. Maz. Nr 142, poz. 3400",
      "informatization": "unknown",
      "zone_symbols": [],
      "amendments": [
        {
          "kind": "note",
          "resolution_number": null,
          "name": null,
          "adopted_on": null,
          "valid_from": null,
          "document_url": null,
          "bip_url": null,
          "raw_text": "text planu zmieniony uchwałą XXXIX/366/2017 (patrz poz. 122); Wyrok Sądu IV SA/Wa 1818/18\" z dnia 13 listopada 2018 r. dla działki 13 z obrębu 7-01; fragment planu zmieniony uchwałą LIV/467/2021 (patrz poz. 154); text planu zmieniony uchwałą LIV/467/2021 (patrz poz. 176);",
          "document_url_verified": false,
          "bip_url_verified": false
        },
        {
          "kind": "text_change",
          "resolution_number": "LIV/467/2021",
          "name": "Zmiana miejscowego planu zagospodarowania przestrzennego dla fragmentu wsi Moczydłów – rejon ul. Lipkowskiej – ETAP I",
          "adopted_on": "2021-06-23",
          "valid_from": "2021-07-17",
          "document_url": "https://bip-v1-files.idcom-jst.pl/sites/47313/wiadomosci/582692/files/liv_467_2021.pdf",
          "bip_url": "https://bip.gorakalwaria.pl/wiadomosci/14246/wiadomosc/582692",
          "raw_text": null,
          "document_url_verified": true,
          "bip_url_verified": true
        },
        {
          "kind": "text_change",
          "resolution_number": "XXXIX/366/2017",
          "name": "Miejscowy plan zagospodarowania przestrzennego dla fragmentu miasta Góra Kalwaria oraz dla fragmentu wsi Moczydłów – rejon ul. Lipkowskiej",
          "adopted_on": "2017-01-25",
          "valid_from": "2017-03-16",
          "document_url": "https://bip-v1-files.idcom-jst.pl/sites/47313/wiadomosci/554352/files/xxxix_366_2017.pdf",
          "bip_url": "https://bip.gorakalwaria.pl/wiadomosci/14246/wiadomosc/554352",
          "raw_text": null,
          "document_url_verified": true,
          "bip_url_verified": true
        }
      ],
      "source_format": "plan_block",
      "verified_links": [
        "bip_url"
      ]
    }
  ],
  "selected_act": null,
  "multiple_acts_at_point": true,
  "multiple_acts_on_parcel": true,
  "candidate_zone_symbols": [],
  "sampled_points": 1,
  "failed_points": 0,
  "is_discovery_only": true,
  "source": null
};

/** Gmina poza KIMPZP: „brak serwisu dla wskazanego obszaru”. */
export const NO_COVERAGE_DISCOVERY: MpzpDiscoverySection = {
  ...GORA_KALWARIA_DISCOVERY,
  status: "no_coverage",
  reason_codes: ["KIMPZP_NO_SERVICE_FOR_AREA"],
  acts: [],
  selected_act: null,
  multiple_acts_at_point: false,
  multiple_acts_on_parcel: false,
};

export function buildDiscovery(overrides: Partial<MpzpDiscoverySection> = {}): MpzpDiscoverySection {
  return { ...GORA_KALWARIA_DISCOVERY, ...overrides };
}
