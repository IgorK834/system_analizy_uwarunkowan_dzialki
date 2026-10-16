/**
 * Kompletność analizy wygenerowana przez backend (`build_completeness`, reguła `completeness/1.0`) z zamrożonej
 * odpowiedzi raportu `multizone` — po jednym przypadku na istotną sytuację (AU-104). Nie edytuj ręcznie:
 * `python scripts/export_completeness_fixtures.py > ../frontend/test/completenessFixtures.ts`.
 */
import type { Completeness } from "@/lib/types";

export const COMPLETENESS_CASES: Record<string, Completeness> = {
  "complete_vector": {
    "schema_version": "1.0",
    "rule_version": "completeness/1.0",
    "checked": 8,
    "total": 8,
    "complete": true,
    "headline": "Sprawdzono wszystkie 8 sekcji w zakresie analizy. Poza zakresem: uzbrojenie terenu, droga i dostęp. Do weryfikacji ręcznej: MPZP, relacja MPZP–POG.",
    "notes": [
      "Uzbrojenie terenu: dostępny jest tylko podgląd pokrycia KIUT, bez analizy odległości do sieci."
    ],
    "sections": [
      {
        "section": "parcel",
        "label": "Działka i geometria",
        "short_label": "geometria działki",
        "report_section": "parcel",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "mpzp",
        "label": "MPZP",
        "short_label": "MPZP",
        "report_section": "mpzp",
        "state": "checked",
        "quality_status": "available",
        "basis": "vector_intersection",
        "manual_review_required": true,
        "reason_codes": [
          "MPZP_REVIEW_REQUIRED"
        ]
      },
      {
        "section": "pog",
        "label": "POG — strefy planistyczne",
        "short_label": "strefy POG",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "pog_overlays",
        "label": "POG — OUZ, OZS, OSDIS",
        "short_label": "OUZ, OZS, OSDIS",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "flood",
        "label": "Zagrożenie powodziowe (ISOK)",
        "short_label": "zagrożenie powodziowe",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "nature",
        "label": "Formy ochrony przyrody (GDOŚ)",
        "short_label": "ochrona przyrody",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "terrain",
        "label": "Teren (NMT)",
        "short_label": "teren (NMT)",
        "report_section": "terrain",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "utilities",
        "label": "Uzbrojenie terenu (KIUT)",
        "short_label": "uzbrojenie terenu",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "partial",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "KIUT_PREVIEW_ONLY"
        ]
      },
      {
        "section": "transport",
        "label": "Transport i dostęp do drogi",
        "short_label": "droga i dostęp",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "out_of_scope",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "NO_SOURCE_CONTRACT"
        ]
      },
      {
        "section": "mpzp_pog_relation",
        "label": "Relacja MPZP–POG",
        "short_label": "relacja MPZP–POG",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": true,
        "reason_codes": [
          "COMPATIBILITY_REVIEW_REQUIRED",
          "DERIVED_SECTION"
        ]
      }
    ],
    "origin": "stored"
  },
  "complete_document": {
    "schema_version": "1.0",
    "rule_version": "completeness/1.0",
    "checked": 7,
    "total": 7,
    "complete": true,
    "headline": "Sprawdzono wszystkie 7 sekcji w zakresie analizy. Poza zakresem: uzbrojenie terenu, droga i dostęp, relacja MPZP–POG. Do weryfikacji ręcznej: MPZP.",
    "notes": [
      "MPZP ustalono z aktu i dokumentu (assignment_method=document_candidate), bez lokalnego wektora stref — udział strefy w działce jest nieustalony.",
      "Uzbrojenie terenu: dostępny jest tylko podgląd pokrycia KIUT, bez analizy odległości do sieci."
    ],
    "sections": [
      {
        "section": "parcel",
        "label": "Działka i geometria",
        "short_label": "geometria działki",
        "report_section": "parcel",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "mpzp",
        "label": "MPZP",
        "short_label": "MPZP",
        "report_section": "mpzp",
        "state": "checked",
        "quality_status": "partial",
        "basis": "document_candidate",
        "manual_review_required": true,
        "reason_codes": [
          "MPZP_DOCUMENT_CANDIDATE",
          "SOURCE_REVIEW_REQUIRED"
        ]
      },
      {
        "section": "pog",
        "label": "POG — strefy planistyczne",
        "short_label": "strefy POG",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "pog_overlays",
        "label": "POG — OUZ, OZS, OSDIS",
        "short_label": "OUZ, OZS, OSDIS",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "flood",
        "label": "Zagrożenie powodziowe (ISOK)",
        "short_label": "zagrożenie powodziowe",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "nature",
        "label": "Formy ochrony przyrody (GDOŚ)",
        "short_label": "ochrona przyrody",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "terrain",
        "label": "Teren (NMT)",
        "short_label": "teren (NMT)",
        "report_section": "terrain",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "utilities",
        "label": "Uzbrojenie terenu (KIUT)",
        "short_label": "uzbrojenie terenu",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "partial",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "KIUT_PREVIEW_ONLY"
        ]
      },
      {
        "section": "transport",
        "label": "Transport i dostęp do drogi",
        "short_label": "droga i dostęp",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "out_of_scope",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "NO_SOURCE_CONTRACT"
        ]
      },
      {
        "section": "mpzp_pog_relation",
        "label": "Relacja MPZP–POG",
        "short_label": "relacja MPZP–POG",
        "report_section": "pog",
        "state": "out_of_scope",
        "quality_status": "unknown",
        "basis": null,
        "manual_review_required": true,
        "reason_codes": [
          "MPZP_WITHOUT_GEOMETRY",
          "PAIRS_EVALUATED",
          "COMPATIBILITY_REVIEW_REQUIRED",
          "DERIVED_SECTION"
        ]
      }
    ],
    "origin": "stored"
  },
  "partial_pog": {
    "schema_version": "1.0",
    "rule_version": "completeness/1.0",
    "checked": 6,
    "total": 8,
    "complete": false,
    "headline": "6 z 8 sekcji sprawdzono; brak: strefy POG, OUZ, OZS, OSDIS. Poza zakresem: uzbrojenie terenu, droga i dostęp. Do weryfikacji ręcznej: MPZP, relacja MPZP–POG.",
    "notes": [
      "Uzbrojenie terenu: dostępny jest tylko podgląd pokrycia KIUT, bez analizy odległości do sieci."
    ],
    "sections": [
      {
        "section": "parcel",
        "label": "Działka i geometria",
        "short_label": "geometria działki",
        "report_section": "parcel",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "mpzp",
        "label": "MPZP",
        "short_label": "MPZP",
        "report_section": "mpzp",
        "state": "checked",
        "quality_status": "available",
        "basis": "vector_intersection",
        "manual_review_required": true,
        "reason_codes": [
          "MPZP_REVIEW_REQUIRED"
        ]
      },
      {
        "section": "pog",
        "label": "POG — strefy planistyczne",
        "short_label": "strefy POG",
        "report_section": "pog",
        "state": "missing",
        "quality_status": "partial",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "POG_COVERAGE_PARTIAL"
        ]
      },
      {
        "section": "pog_overlays",
        "label": "POG — OUZ, OZS, OSDIS",
        "short_label": "OUZ, OZS, OSDIS",
        "report_section": "pog",
        "state": "missing",
        "quality_status": "partial",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "POG_COVERAGE_PARTIAL"
        ]
      },
      {
        "section": "flood",
        "label": "Zagrożenie powodziowe (ISOK)",
        "short_label": "zagrożenie powodziowe",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "nature",
        "label": "Formy ochrony przyrody (GDOŚ)",
        "short_label": "ochrona przyrody",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "terrain",
        "label": "Teren (NMT)",
        "short_label": "teren (NMT)",
        "report_section": "terrain",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "utilities",
        "label": "Uzbrojenie terenu (KIUT)",
        "short_label": "uzbrojenie terenu",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "partial",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "KIUT_PREVIEW_ONLY"
        ]
      },
      {
        "section": "transport",
        "label": "Transport i dostęp do drogi",
        "short_label": "droga i dostęp",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "out_of_scope",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "NO_SOURCE_CONTRACT"
        ]
      },
      {
        "section": "mpzp_pog_relation",
        "label": "Relacja MPZP–POG",
        "short_label": "relacja MPZP–POG",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": true,
        "reason_codes": [
          "COMPATIBILITY_REVIEW_REQUIRED",
          "DERIVED_SECTION"
        ]
      }
    ],
    "origin": "stored"
  },
  "complete_no_match": {
    "schema_version": "1.0",
    "rule_version": "completeness/1.0",
    "checked": 7,
    "total": 7,
    "complete": true,
    "headline": "Sprawdzono wszystkie 7 sekcji w zakresie analizy. Poza zakresem: uzbrojenie terenu, droga i dostęp, relacja MPZP–POG.",
    "notes": [
      "MPZP: odpytane kanały nie wskazały obowiązującego planu; Rejestr Urbanistyczny jest niepełny, więc to nie jest dowód braku planu.",
      "Uzbrojenie terenu: dostępny jest tylko podgląd pokrycia KIUT, bez analizy odległości do sieci."
    ],
    "sections": [
      {
        "section": "parcel",
        "label": "Działka i geometria",
        "short_label": "geometria działki",
        "report_section": "parcel",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "mpzp",
        "label": "MPZP",
        "short_label": "MPZP",
        "report_section": "mpzp",
        "state": "checked",
        "quality_status": "unknown",
        "basis": "no_match",
        "manual_review_required": false,
        "reason_codes": [
          "MPZP_NO_ACT_AT_PARCEL"
        ]
      },
      {
        "section": "pog",
        "label": "POG — strefy planistyczne",
        "short_label": "strefy POG",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "pog_overlays",
        "label": "POG — OUZ, OZS, OSDIS",
        "short_label": "OUZ, OZS, OSDIS",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "flood",
        "label": "Zagrożenie powodziowe (ISOK)",
        "short_label": "zagrożenie powodziowe",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "nature",
        "label": "Formy ochrony przyrody (GDOŚ)",
        "short_label": "ochrona przyrody",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "terrain",
        "label": "Teren (NMT)",
        "short_label": "teren (NMT)",
        "report_section": "terrain",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "utilities",
        "label": "Uzbrojenie terenu (KIUT)",
        "short_label": "uzbrojenie terenu",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "partial",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "KIUT_PREVIEW_ONLY"
        ]
      },
      {
        "section": "transport",
        "label": "Transport i dostęp do drogi",
        "short_label": "droga i dostęp",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "out_of_scope",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "NO_SOURCE_CONTRACT"
        ]
      },
      {
        "section": "mpzp_pog_relation",
        "label": "Relacja MPZP–POG",
        "short_label": "relacja MPZP–POG",
        "report_section": "pog",
        "state": "out_of_scope",
        "quality_status": "unknown",
        "basis": null,
        "manual_review_required": true,
        "reason_codes": [
          "NO_MPZP_ZONE_TO_COMPARE",
          "MPZP_ZONES_MISSING",
          "COMPATIBILITY_REVIEW_REQUIRED",
          "DERIVED_SECTION"
        ]
      }
    ],
    "origin": "stored"
  },
  "waiting": {
    "schema_version": "1.0",
    "rule_version": "completeness/1.0",
    "checked": 7,
    "total": 8,
    "complete": false,
    "headline": "7 z 8 sekcji sprawdzono; brak: MPZP. Poza zakresem: uzbrojenie terenu, droga i dostęp. Do weryfikacji ręcznej: relacja MPZP–POG.",
    "notes": [
      "Uzbrojenie terenu: dostępny jest tylko podgląd pokrycia KIUT, bez analizy odległości do sieci."
    ],
    "sections": [
      {
        "section": "parcel",
        "label": "Działka i geometria",
        "short_label": "geometria działki",
        "report_section": "parcel",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "mpzp",
        "label": "MPZP",
        "short_label": "MPZP",
        "report_section": "mpzp",
        "state": "missing",
        "quality_status": "awaiting_input",
        "basis": null,
        "manual_review_required": true,
        "reason_codes": [
          "MPZP_MANUAL_ZONE_REQUIRED"
        ]
      },
      {
        "section": "pog",
        "label": "POG — strefy planistyczne",
        "short_label": "strefy POG",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "pog_overlays",
        "label": "POG — OUZ, OZS, OSDIS",
        "short_label": "OUZ, OZS, OSDIS",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "flood",
        "label": "Zagrożenie powodziowe (ISOK)",
        "short_label": "zagrożenie powodziowe",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "nature",
        "label": "Formy ochrony przyrody (GDOŚ)",
        "short_label": "ochrona przyrody",
        "report_section": "environment",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "terrain",
        "label": "Teren (NMT)",
        "short_label": "teren (NMT)",
        "report_section": "terrain",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": []
      },
      {
        "section": "utilities",
        "label": "Uzbrojenie terenu (KIUT)",
        "short_label": "uzbrojenie terenu",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "partial",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "KIUT_PREVIEW_ONLY"
        ]
      },
      {
        "section": "transport",
        "label": "Transport i dostęp do drogi",
        "short_label": "droga i dostęp",
        "report_section": "infrastructure",
        "state": "out_of_scope",
        "quality_status": "out_of_scope",
        "basis": null,
        "manual_review_required": false,
        "reason_codes": [
          "NO_SOURCE_CONTRACT"
        ]
      },
      {
        "section": "mpzp_pog_relation",
        "label": "Relacja MPZP–POG",
        "short_label": "relacja MPZP–POG",
        "report_section": "pog",
        "state": "checked",
        "quality_status": "available",
        "basis": null,
        "manual_review_required": true,
        "reason_codes": [
          "COMPATIBILITY_REVIEW_REQUIRED",
          "DERIVED_SECTION"
        ]
      }
    ],
    "origin": "stored"
  }
};
