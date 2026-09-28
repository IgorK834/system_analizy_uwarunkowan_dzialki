# ADR-004: Strefy MPZP z wersjonowanego wektora i cytowalne parametry uchwały

- Status: Zaakceptowany
- Data: 2026-09-25
- Zadania: BK-202 (Task 3.2) i BK-203 (Task 3.3); zależności BK-201, BK-102
- Kontekst szerszy: `docs/data_sources/mpzp_contracts.md`, ADR-001, ADR-003

## Kontekst

Do BK-201 analiza MPZP przypisywała działce strefę na podstawie punktowego
discovery KIMPZP (WMS GetFeatureInfo) i kandydatów symboli, a następnie
zakładała, że cała działka leży w jednej strefie. Importer wersjonował
wydzielenia (`land_use_areas`), ale bez stabilnego ID, a `run_analysis` z nich
nie korzystał. Parser uchwał dopasowywał symbole przez `\b{symbol}\b` (co
łączyło `MN` z `MN.1` i `U` z `MW/U`), a mapowanie do API działało jako
„ostatnia wartość wygrywa”. Snapshot analizy nie zachowywał wersji aktu,
styczności ani dowodu wartości.

## Decyzja — przypisanie przestrzenne (BK-202)

1. **Stabilne ID wydzielenia** (`land_use_areas.zone_identifier`, unikalne w
   wersji aktu): `akt:identyfikator_źródła`, a bez niego
   `akt:symbol:sha256(ST_AsBinary(ST_Normalize(geometria)))[:16]`
   (`stable_zone_identifier`). Ten sam obiekt w kolejnym imporcie ma to samo ID;
   duplikat ID odrzuca akt (`duplicate_zone_identifier`), nie scala stref.
2. **Zapytanie** `find_mpzp_zone_intersections(parcel, as_of, data_release_id)`:
   indeks GiST (`geometry && parcel`) → `ST_Intersects` → `ST_Intersection`
   i `ST_Area` w EPSG:2180. Wersje aktów wybiera jawny `as_of` (chwila startu
   analizy) i opcjonalnie `data_release_id`; akty `raster_only` nie mają stref.
3. **Wynik** (`assess_vector_zones`): każda strefa przecinająca pełny obrys
   działki ma `zone_id`, `act_identifier`, `act_version` (hash snapshotu wersji),
   `act_version_id`, `data_release_id`, `intersection_area_sqm`,
   `intersection_pct`, geometrię przecięcia (GeoJSON EPSG:4326) i źródło.
   Pole ≤ 1e-6 m² to **styczność** (`touches_boundary=True`, pole 0) — osobny
   wpis, nigdy strefa dominująca. `is_dominant` jest polem pomocniczym
   (największe dodatnie pole). Wynik nie zależy od centroidu.
4. **Bez normalizacji sum.** Nakładanie wydzieleń/planów (`MPZP_ZONES_OVERLAP`,
   `MPZP_MULTIPLE_ACTS`) i niepełne pokrycie (`MPZP_PARTIAL_COVERAGE`, „brak
   danych nie oznacza braku planu”) są raportowane; tolerancja 0,1% działki.
   Wynik `complete` wymaga pełnego pokrycia bez nakładania.
5. **Fallback** dokument/raster/ręczny działa wyłącznie bez dodatniego przecięcia
   z wiarygodnym wektorem. Strefa ma `assignment_method`
   (`document_candidate` albo `manual_user_input`), `manual_review_required`
   i confidence ≤ 0,5 (`FALLBACK_ZONE_CONFIDENCE_CAP`); ostrzeżenie
   `MPZP_VECTOR_UNAVAILABLE`. Przypisanie z wektora ma confidence 0,95.
6. **Snapshot i cache.** `mpzp_zones.result_snapshot` (pełny `MpzpZoneResult`)
   jest źródłem prawdy odczytu historycznego; przełączenie wydania nie zmienia
   zapisanej analizy. Sygnatura cache obejmuje aktywne wydania i wersję kontraktu
   `pog-v2.2+mpzp-v2.0`, więc nowe wydanie MPZP powoduje nową analizę.

## Decyzja — parametry z dowodem (BK-203)

1. Symbole stref z geometrii są wejściem parsera **osobno dla każdego aktu i
   wersji**. Dokument aktu pochodzi z wersji aktu (`document_url` z importu);
   gdy go brak, a działkę obejmuje jeden akt, używany jest dokument z discovery
   z ostrzeżeniem `MPZP_DOCUMENT_FROM_DISCOVERY`. Bez dokumentu parametry są
   `null` (`MPZP_ACT_DOCUMENT_MISSING`), nigdy zero.
2. **Dopasowanie symbolu jest dokładnym tokenem**: `.`, `/`, `_`, `-` należą do
   symbolu (`symbol_token_pattern`), więc podobne symbole nie są łączone przez
   podciąg. Parametr trafia tylko do strefy o identycznym symbolu w tym samym
   akcie (`apply_parser_zone`).
3. **Evidence każdej kandydatury** (`MpzpParameterEvidence`): `normalized_value`,
   `raw_value`, `unit`, `evidence_text`, `page_number` i `segment_id` segmentu
   zawierającego fragment, `legal_unit_id` (najmniejsza jednostka redakcyjna
   uchwały zapisana w `legal_units`), `document_sha256`, `document_version_id`,
   `parser_version` (`mpzp-parser/2.0`), `extraction_method`, `confidence`,
   `conflict_group_id`, `manual_review_required`.
4. **Konflikty**: wszystkie sprzeczne kandydatury tej samej wartości liczbowej
   w strefie są zachowane ze wspólnym `conflict_group_id`
   (`wersja_aktu:strefa:parametr`). Płaskie pole API pozostaje `null`, strefa
   wymaga weryfikacji, wynik jest częściowy (`MPZP_PARAMETER_CONFLICT`).
   Parametry opisowe (listy ustaleń) nie są konfliktem.
5. **OCR**: tekst odczytany przez OCR (`extraction_method == "ocr"`) lub
   ostrzeżenie OCR etapu ekstrakcji mnoży confidence każdego parametru przez
   `OCR_CONFIDENCE_PENALTY = 0,85` — raz na dokument. Równoważny tekstowy PDF i
   OCR dają tę samą wartość, OCR niższe confidence.
6. **Odczyt historyczny** korzysta ze snapshotu (`result_snapshot`,
   `mpzp_parameters` z evidence) i zapisanego audytu dokumentu (strony,
   jednostki redakcyjne, SHA), a nie z ponownego pobrania uchwały.

## Migracje

- `018_mpzp_vector_zones` (po `017_pog_act_provenance`): `zone_identifier` z
  uzupełnieniem istniejących wierszy tą samą formułą co import (duplikaty
  otrzymują sufiks `:id`), `planning_act_versions.document_url`, kolumny
  snapshotu w `mpzp_zones` z `CHECK` metody przypisania (stare wiersze:
  `legacy`).
- `019_mpzp_parameter_evidence`: kolumny evidence w `mpzp_parameters` i indeks
  `conflict_group_id`.

Obie migracje mają pełny downgrade (usuwa dodane kolumny/ograniczenia).

## Konsekwencje

- UI (`MpzpZoneCard`) i PDF pokazują pełną listę stref, sposób przypisania,
  plan i wersję oraz dla każdej wartości stronę, segment, jednostkę, fragment,
  metodę ekstrakcji i SHA-256 dokumentu; konflikty są widoczne bez wyboru.
- Import MPZP może przyjąć opcjonalne pola `zone_identifier` i `document_url`
  w `field_mapping` źródła.
- Ścieżka rastrowa/ręczna pozostaje działająca, ale nie zgłasza pełnej pewności.
