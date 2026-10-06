# Odbiór PV3-07, PV3-08 i PV3-09 (Epic 20A)

Data: 3 października 2026 r. Baza kodu: `644e39a` z niezacommitowanymi zmianami (nic nie zostało
zacommitowane; `docs/evaluation` i `docs/adr` są w `.gitignore`, więc nowe pliki tych katalogów
wymagają `git add -f`). Decyzje projektowe: [ADR-013](../adr/ADR-013-mpzp-quantity-engine-conditions-calibration.md).

## Stan zadań

Żadne z trzech zadań nie było wcześniej wykonane (kod `quantity_*`, `value_conditions`, `evidence_confidence`,
migracja `028` i artefakt kalibracji nie istniały).

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| PV3-07 — silnik wartości liczbowych | **wykonane; wynik rozwojowy** | Progi z issue (precision ≥ 0,95, recall ≥ 0,60 na zbiorze rozwojowym) spełnione z dużym zapasem, ale silnik rozwijano na tych samych 21 próbkach, na których mierzy się wynik; to nie jest ocena uogólnienia |
| PV3-08 — warunki wartości vs konflikt | **wykonane** | Pole płaskie API jest teraz `null` tam, gdzie wcześniej parser zwracał konflikt; konsumenci czytają `parameters[]` |
| PV3-09 — skalibrowana pewność | **wykonane; progi do potwierdzenia** | Pasmo „średnie” puste (próg średni = wysoki); tolerancje 5%/10% to polityka czekająca na właściciela; wynik na podziale `final` nie jest niezależny |

Czego te zadania **nie** zmieniają: domyślny tryb zakresu pozostaje `legacy` (`scope_mode="legacy"`);
tryb blokowy włącza dopiero Task 20.14. Ścieżka modelu językowego (ADR-012) nie istnieje — wartość z
modelu nie ma kalibracji, więc dostałaby pewność ograniczoną od góry i zawsze ręczną weryfikację.

## Polecenia odbiorowe

```bash
# PV3-07/09: ewaluacja BK-603 obu silników jednym poleceniem (z katalogu backend/, jak w PV3-04–06)
cd backend
python3 scripts/evaluate_mpzp_parser.py --engine legacy v3 \
  --output-dir ../docs/evaluation/results/parser --repeat 2
# wyniki: results/parser/{legacy,v3,comparison}/ — v3/report.md sekcja „Niezawodność pewności (PV3-09)”;
# poprzednie wyniki (przed PV3-07) zachowano w results/parser/pre-PV3-07/

# PV3-09: odtworzenie albo sprawdzenie artefaktu kalibracji
python3 scripts/calibrate_mpzp_confidence.py            # zapisuje app/core/mpzp_confidence_calibration.json
python3 scripts/calibrate_mpzp_confidence.py --check    # kończy się błędem, gdy artefakt jest nieaktualny

# testy nowych i zmienionych modułów
python3 -m pytest tests/test_quantity_normalization.py tests/test_quantity_lexicon.py \
  tests/test_quantity_engine.py tests/test_quantity_corpus.py tests/test_shared_numbers.py \
  tests/test_value_conditions.py tests/test_evidence_confidence.py tests/test_mpzp_confidence_calibration.py \
  tests/test_mpzp_confidence_contract.py tests/test_mpzp_conditions_contract.py \
  tests/test_mpzp_parser_numeric.py tests/test_mpzp_parser_validate.py tests/test_mpzp_parser_blocks.py \
  tests/test_mpzp_parser_descriptive.py tests/test_planning_rules.py tests/test_evaluate_mpzp_parser.py \
  tests/test_evaluate_reference_corpus.py tests/test_mpzp_parser_regression.py tests/test_mpzp_zone_scope.py -q

# frontend
cd ../frontend && npm ci && npm run typecheck && npm run test:coverage && npm run build
```

Pełny zestaw w kontenerze skonfigurowanym jak CI — wynik w sekcji „Weryfikacja”.

## PV3-07 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Jeden silnik z leksykonem: rzeczowniki 9 parametrów katalogu + 4 ustawowych, operatory (przymiotnik przed, „maksimum/minimum”, „nie większą niż”, „do N”, zakresy „od X do Y”/„X – Y”), jednostki (m, metrów, %, stosunek → procent z flagą, `°/º/o/0` z flagą i karą, liczebniki słowne) | spełnione | `quantity_lexicon.py` (dane), `quantity_engine.py` (logika), `quantity_normalization.py`; 4 ustawowe: patrz ADR-013 „Interpretacja” |
| Wykluczenia (wysokość parteru/elewacji/obiektów, szerokość, poziom posadowienia…), notacja podwójna „0,50 (50%)”, podział klauzul ze znacznikami listy i dziedziczeniem rzeczownika | spełnione | `NEUTRALIZING_NOUNS`, `split_clauses`, `test_quantity_engine.py`, `test_quantity_corpus.py` |
| Wynik niesie zakres znaków i strategię | spełnione | `QuantityMatch.span/strategy` → `MpzpParameter.char_start/char_end/extraction_strategy` |
| Jedna implementacja normalizacji dla aplikacji i ewaluatora; test równości | spełnione | `evaluate_mpzp_parser.py` importuje `NORMALIZATION_RULES`/`normalize_annotation_value` z aplikacji; `test_evaluate_mpzp_parser.py`, `test_quantity_normalization.py` |
| Oba stare parsery delegują do silnika | spełnione | `mpzp_parser_numeric.extract_numeric_matches`, `mpzp_parser_descriptive` (handel, geometria dachu), `rules.py` |
| Wersja parsera i sygnatura cache | spełnione | `mpzp-parser/3.0-det` (`+scope.1` w trybie blokowym); `MPZP_RESULT_SCHEMA_VERSION` 2.2 → 2.3; opisane w ADR-013 pkt 4 |
| Zbiór rozwojowy: precision ≥ 0,95, recall ≥ 0,60 | **spełnione** (v3: 1,000 / 1,000) | `results/parser/v3/metrics.json` (tabela niżej) |
| Pokrycie nowych modułów ≥ 80% | spełnione (96–100%) | sekcja „Weryfikacja” |
| „Luki” w testach regresyjnych zaktualizowane | spełnione | `expected.json` czterech fixtur: luki → wartości; README fixtur |
| Dokumentacja | spełnione | ten raport, ADR-013, README, `docs/current_state.md` |

### Wyniki na korpusie BK-603 (21 próbek, 250 par strefa–parametr z wartością w anotacji)

Anotacje nadał asystent AI i czekają na przegląd człowieka. „Precision” w ewaluatorze oznacza: wśród par,
dla których silnik zwrócił wartość, odsetek tych, w których dokument ją zawiera (fałszywe trafienie = wartość
dla pary, której dokument nie ma). Poprawność samej **wartości** mierzy osobno `exact_value_accuracy`.

| Silnik / moment | Zbiór | Precision | Recall | Wartość dokładna wśród znalezionych | `source_consistent` | Dokładnie + z właściwego źródła (z 250 / 89 / 161) |
|---|---|---|---|---|---|---|
| `legacy` przed PV3-07 | razem | 1,000 (62/62) | 0,248 (62/250) | 0,806 (50/62) | 0,680 (51/75) | 0,156 (39/250) |
| `legacy` po PV3-07 (**produkcja**) | razem | 1,000 (208/208) | 0,832 (208/250) | 0,904 (188/208) | 0,768 (209/272) | 0,548 (137/250) |
| | rozwojowy | 1,000 (89/89) | 1,000 (89/89) | 0,843 (75/89) | 0,652 (73/112) | 0,494 (44/89) |
| | końcowy | 1,000 (119/119) | 0,739 (119/161) | 0,950 (113/119) | 0,850 (136/160) | 0,578 (93/161) |
| `v3` (tryb blokowy) przed PV3-07 | razem | 1,000 (73/73) | 0,292 (73/250) | 0,849 (62/73) | 1,000 (88/88) | 0,248 (62/250) |
| `v3` po PV3-07 | razem | **1,000 (248/248)** | **0,992 (248/250)** | 0,940 (233/248) | 0,975 (355/364) | 0,896 (224/250) |
| | rozwojowy | 1,000 (89/89) | 1,000 (89/89) | 0,966 (86/89) | 0,973 (110/113) | 0,933 (83/89) |
| | końcowy | 1,000 (159/159) | 0,988 (159/161) | 0,925 (147/159) | 0,976 (245/251) | 0,876 (141/161) |

Klauzule ogólne (12 par): wcześniej 0/12 znalezionych, teraz 12/12 (`general_clause_found`). Wynik ten sam
w dwóch powtórzeniach (`v3/determinism.json`, `identical: true`).

**Czego te liczby nie mówią (czytać przed wnioskami).**

1. **To wynik rozwojowy.** Podział `final` zamrożono 2026-09-30 przed pierwszym biegiem parsera, ale po nim
   leksykon i reguły poprawiano, także na jego błędach — 21 próbek nie ma niezależnego drugiego podziału.
   `final` jest tu *obserwacją z korpusu*, nie estymatą uogólnienia. Silnik sprawdzono dodatkowo na
   syntetycznych brzmieniach spoza korpusu (`test_quantity_engine.py`), co jest sprawdzeniem
   odporności, nie pomiarem recallu.
2. **Wartość ≠ wykrycie.** Precision 1,000 oznacza brak wartości dla par, których dokument nie ma; wśród
   znalezionych 15 z 248 (v3) i 20 z 208 (`legacy`) nie jest dokładnie zgodne z anotacją (nadmiarowe
   wartości obok właściwej). Dokładność wartości `v3`: 0,966 (rozwojowy), 0,925 (końcowy).
3. **Produkcja nadal używa `legacy`.** Realny zysk dla użytkownika to kolumna `legacy po`: recall 0,25 → 0,83,
   ale tylko 55% wartości jest jednocześnie dokładne i z właściwego źródła (`v3`: 90%). Blokowy tryb zakresu
   to Task 20.14.
4. **Skan symulowany** (`ocr_simulated`): dokładność wartości `v3` 0,43 (9/21) — 12 z 15 błędów wartości; to
   wymieszane bloki wielostrefowe bez struktury (zapas strategii 0): wartości sąsiednich stref trafiają do
   zwracanego zbioru razem z właściwą (Falenica, 12 par), dwie pary pominięte (Libertów: linia zabudowy i
   intensywność przy szumie OCR).
5. **Pozostałe błędy `v3` (26 wierszy w `errors.csv`)**: `wrong_source` 9 (wartość poprawna, ale cytat w
   innym bloku niż anotowany: powtarzające się 6/10 m i kąty dachu w Szczytnie, wysokość 21 m w Łodzi),
   `value_error` 15 (12 w skanie symulowanym, 3 w HTML Łodzi: dwie wartości 60% procentu biologicznie czynnego
   z klauzuli resztowej obok właściwej), `detection_fn` 2.
6. **Wyniki zbioru nie mówią o czterech „ustawowych” parametrach** (`parking_minimum`,
   `min_building_coverage_percent`, `min_plot_area_m2`, `max_retail_sales_area_m2`): korpus BK-603 ich nie
   anotuje, więc dokładność jest niezmierzona.
7. **Złagodzone gwarancje** (jawnie, bo test dotychczas wymagał więcej): ewaluator pomija starą weryfikację
   strony dla wartości z dokładnym zakresem znaków (`check_source`), a próg `test_mpzp_zone_scope` ustawiono
   na ≥ 0,95 i `wrong_block ≤ 9` (silnik zwraca wartości, które legitymnie powtarzają się w kilku blokach).

## PV3-08 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Warunek `{kind: building_type\|roof_type\|subzone\|location\|other, label, quote}` | spełnione | `value_conditions.ValueCondition`; schematy `MpzpValueCondition` (API) i `ValueCondition` (parser); frontend `types.ts` |
| `conflict` vs `conditional`: konflikt tylko dla tej samej przesłanki z różnymi wartościami | spełnione | `value_kind`; `_conflicting_keys` po (nazwa, rodzaj zakresu, klucz warunków); `test_value_conditions.py`, `test_mpzp_conditions_contract.py` |
| Pole płaskie `null`, gdy brak jednej wartości bezwarunkowej | spełnione | `apply_parser_zone`; `test_mpzp_conditions_contract.py` |
| UI, PDF i paczka audytowa pokazują warunki | spełnione | `MpzpZoneCard` (znacznik, warunki, nota); `report.html` + `report.py` (wiersze „warunkowa”, kolumna warunków, `.tag-cond`); `audit-exporter/1.1.0`; `test_report_conditions.py`, `test_audit_export.py`, `MpzpZoneCard.test.tsx` |
| Migracja po bieżącym head, tylko gdy dowód jest kolumnowy | spełnione | `028_mpzp_parameter_condition` po `027_zone_symbol_length`: JSONB `conditions`, `value_kind` z `CHECK`; pełny downgrade; `test_migration_028.py` (część z bazą — kontener) |
| Stare snapshoty czytane jako bezwarunkowe | spełnione | `conditions IS NULL` → bezwarunkowa; `value_kind` wyprowadzany w `MpzpParameterEvidence`; test odczytu historycznego |
| Sygnatura cache podniesiona; dokumentacja | spełnione | `MPZP_RESULT_SCHEMA_VERSION` 2.4; ADR-013; `docs/report/field-mapping.md` zregenerowany (550 ścieżek; test `test_report_field_mapping`) |

Zgodność warunków z anotacją `conditional_value` w korpusie: 58 z 59 wartości warunkowych anotacji
znalezionych jako warunkowe; silnik oznaczył ponadto 66 wartości jako warunkowe, których anotacje tak nie
opisują. To **nie jest pomiar precyzji** (anotacje nie wymieniają wszystkich warunków) — to lista do
przeglądu przez człowieka.

## PV3-09 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Brak stałych mnożników; pewność = prawdopodobieństwo z cech (strategia, zakres, cytat, metoda, kandydaci, konflikty, rodzaj wartości) | spełnione | `evidence_confidence.py` (22 cechy), `mpzp_parser_validate._confidence_features`; stałe `OCR_CONFIDENCE_PENALTY` i spółka usunięte |
| Artefakt kalibracji z wersją i skrótem danych, na podziale kalibracyjnym (nie końcowym) | spełnione | `app/core/mpzp_confidence_calibration.json`: dane `development`, 241 wartości (16 błędów), SHA-256 `24d19835…`, korpus `BK-603-…-2026-09-30` |
| Pasma low/medium/high z zmierzonymi odsetkami błędów; próg ręcznej weryfikacji z pomiaru | spełnione (pasmo średnie puste) | tabela niżej; artefakt → `bands`, `manual_review`, `measurements` |
| Procedura rekalibracji | spełnione | skrypt `--check`, ADR-013 „Rekalibracja”; test pilnuje zgodności artefaktu z kodem i danymi |
| Samoocena modelu NIE jest cechą | spełnione | `FORBIDDEN_FEATURE_FRAGMENTS` w walidacji; wartość z modelu: pewność ≤ `uncalibrated_cap`, zawsze ręczna weryfikacja (test) |
| Raport niezawodności (krzywa, Brier, ECE z liczebnościami) | spełnione | `v3/report.md` „Niezawodność pewności (PV3-09)”, `metrics.json` → `calibration`, `calibration.svg` |
| ECE ≤ 0,10 | **spełnione**: 0,059 (`final`, n = 434), 0,028 (kalibracja, n = 241) | `reliability_metrics` |
| Pasma monotoniczne | **spełnione** (`is_monotone`: low 25%/16, high 0%) | artefakt → `measurements.calibration` |
| Błąd w paśmie wysokim ≤ 5% przy ≥ 100 wartościach | **spełnione**: kalibracja 0/177, `final` 0/297 | tabela niżej |

### Zmierzone pasma (artefakt `mpzp-confidence/1.0`; 22 cechy, `l2 = 5,0`, próg średni = wysoki = 0,9431)

| Pasmo | Kalibracja (`development`, n = 241): wartości / błędy | Niezależnie od doboru progów (`final`, n = 434): wartości / błędy |
|---|---|---|
| high (≥ 0,943) | 177 / 0 (0%) | 297 / 0 (0%) |
| medium | 0 / — | 0 / — |
| low (< 0,943) | 64 / 16 (25%) | 137 / 23 (16,8%) |
| Brier / ECE | 0,0476 / 0,0278 | 0,0343 / 0,0586 |

„Błąd” = wartość zwrócona nie należy do anotacji strefy. Próg ręcznej weryfikacji = 0,9431 (ten sam), więc
`manual_review_required` dostają wszystkie wartości z pasma niskiego i wszystkie opisowe/z modelu.

**Uwagi do tych liczb.**

1. `final` nie służył do wag i progów, ale cechy i silnik poprawiano po zobaczeniu jego błędów (jak wyżej),
   więc 0/297 to wynik na korpusie, nie gwarancja dla nowych dokumentów.
2. Medium puste: między „błąd ≤ 10%” a „błąd ≤ 5%” (wygładzenie izotoniczne, minimum 20 wartości) dane
   kalibracyjne nie dają przedziału. Pasma rozróżniają więc dwie klasy; trzecią da dopiero więcej błędów w
   danych, czyli większy korpus (PV3-02).
3. 16 błędów w danych kalibracyjnych to mało — dlatego wagi mają priory i ograniczenia znaku (swobodna
   regresja dawała sprzeczne z sensem znaki). Walidacja krzyżowa „bez jednej próbki” dobrała `l2 = 5`.
4. Dla wartości opisowych (listy ustaleń) nie ma etykiet kalibracyjnych: pewność = min(parser, p, limit),
   bez pasma. W API `confidence_band` jest wtedy `null`.
5. Wartości z `legacy` i `v3` kalibrowano razem (oba silniki, ten sam korpus); artefakt zapisuje obie
   wersje parsera i silnika.

## Wpływ na kontrakt i cache

| Co | Przed | Po |
|---|---|---|
| `MPZP_RESULT_SCHEMA_VERSION` | 2.2 | 2.3 (silnik) → 2.4 (warunki) → **2.5** (kalibracja; stan końcowy) |
| `MPZP_PARSER_VERSION` | `mpzp-parser/2.0` | `mpzp-parser/3.0-det` (blokowy: `3.0-det+scope.1`) |
| `AUDIT_EXPORTER_VERSION` | 1.0.x | 1.1.0 |
| Alembic head | `027_zone_symbol_length` | `028_mpzp_parameter_condition` |
| `RESULT_CONTRACT_VERSION` (sygnatura cache) | `…+mpzp-v2.2+…` | `…+mpzp-v2.5+…` |

Stare snapshoty z `mpzp-v2.2–2.4` nie są serwowane jako trafienie cache (analiza liczy się od nowa); odczyt
historyczny działa bez zmian i traktuje stare wartości jako bezwarunkowe, bez pasm. Po wdrożeniu pierwsza
analiza każdej działki jest liczona ponownie. Wersja parsera nie wchodzi do sygnatury, dlatego **każda
przyszła zmiana wartości zwracanych przez parser musi podnieść wersję kontraktu** (komentarz w
`services/cache.py`). Downgrade migracji 028 usuwa warunki i `value_kind` z tabeli (snapshoty JSON zachowują
je w treści).

## Zmiany zachowania widoczne dla użytkownika

- Wartość z warunkiem (np. wysokość inna dla dachu płaskiego) nie jest już „konfliktem wymagającym
  ręcznego wyboru”: UI/PDF pokazują obie wartości z warunkami, pole płaskie zostaje puste, jakość sekcji nie
  spada do „częściowej” z tego powodu. Prawdziwy konflikt (ta sama przesłanka, różne wartości) pozostaje.
- Wartości z klauzul wymieniających inne symbole stref są odrzucane (wcześniej mogły trafić do każdej strefy
  bloku).
- Pewność w UI/PDF/API to skalibrowane prawdopodobieństwo z pasmem; wartości w paśmie niskim mają
  `manual_review_required` (także wartości warunkowe, bo cecha `conditional` obniża pewność w modelu).

## Ograniczenia i ryzyka (czytać przed wnioskami)

1. **Wynik rozwojowy** i **anotacje AI** — patrz wyżej; niezależny zbiór końcowy (PV3-02) nie istnieje.
2. **Leksykon to wiedza ręczna.** Nowe brzmienia (inne gminy, inne rzeczowniki) wymagają wpisu w leksykonie.
   Zamiar projektu: brak dopasowania zamiast zgadywania (`null` ≠ 0), więc ryzyko to brak wartości, nie
   błędna wartość — ale tego nie zmierzono poza korpusem.
3. **Nadmiarowe wartości w skanach** i blokach bez struktury (strategia 0): silnik zwraca wartości sąsiednich
   stref obok właściwej; pasmo niskie je oznacza do ręcznej weryfikacji (sprawdza to raport niezawodności:
   23/137 błędów w paśmie niskim na `final`).
4. **Klasyfikacja warunków jest słownikowa** (dach, podstrefa, typ budynku, położenie); nieznany warunek
   trafia do `other` z cytatem lub nie jest wykryty (wartość pozostaje wtedy bezwarunkowa i może wejść w
   konflikt z wartością warunkową). 66 wartości oznaczonych jako warunkowe poza anotacją czeka na przegląd.
5. **Pasmo średnie jest puste**, a progi pasm zależą od polityki tolerancji (do potwierdzenia przez właściciela).
6. **Cztery parametry „ustawowe”** są zinterpretowane (ADR-013) i niezmierzone na korpusie.
7. Wpływ na produkcję obejmuje tylko tryb `legacy` (domyślny), dopóki Task 20.14 nie włączy bloków.
8. Frontend: moduł `lib/mpzpConditions.ts` dodano do `coverage.include` (`vitest.config.ts`); brak zmian
   w pozostałych progach.

## Weryfikacja

Pełny zestaw w kontenerze jak w CI (obraz backendu, PostgreSQL z migracjami do `028`, repozytorium jako
`/repo:ro` bez `.env`, kod z bieżącego drzewa, `pytest -m 'not docker_cli' --cov=app --cov-report=term-missing
--cov-fail-under=80`): **2571 testów przeszło, 3 odrzucone znacznikiem `docker_cli`, 0 niepowodzeń, pokrycie
`app` 93,55%** (próg 80% bez zmian; 3 października 2026 r., 4 min 42 s).

Wcześniejszy pełny przebieg tego samego dnia (2565 przeszło) wykrył 4 niepowodzenia, wszystkie z powodu
testów zakładających stare zachowanie, nie błędów kodu:

- `test_audit_e2e` — oczekiwał `audit-exporter/1.0.0`; zaktualizowano do 1.1.0 (zamierzone podniesienie
  wersji: paczka pokazuje warunki).
- `test_conflicting_values_are_kept_and_not_resolved` — scenariusz „9 m” + „dla budynków gospodarczych 6 m”
  jest od PV3-08 wartością warunkową, nie konfliktem. Test podzielono: prawdziwy konflikt (dwie wartości
  bez warunku) i `test_conditional_values_are_not_a_conflict`.
- `test_ocr_gives_same_value_with_lower_confidence` — importował usuniętą stałą `OCR_CONFIDENCE_PENALTY`;
  teraz sprawdza niższą pewność OCR i cechę `extraction_method` w `confidence_features`.
- `test_mapping_documentation_is_in_sync_with_code` — `docs/report/field-mapping.md` zregenerowano
  (`scripts/export_report_field_mapping.py`: 542 → 550 ścieżek liści).

Po poprawkach uruchomiono ponownie pełny zestaw (wynik wyżej). Ponadto dodano test czwartego parametru
„ustawowego” (`min_building_coverage_percent`).

Lokalnie (macOS), 547 testów nowych i zmienionych modułów przeszło; pokrycie: `quantity_lexicon` 100%,
`value_conditions` 100%, `shared/numbers` 100%, `mpzp_parser_numeric` 100%, `mpzp_parser_validate` 100%,
`evidence_confidence` 99%, `rules` 99%, `quantity_normalization` 98%, `mpzp_parser_descriptive` 98%,
`quantity_engine` 96%, `mpzp_parser_blocks` 96%, `evaluate_mpzp_parser` 86% (próg 80%). Testy wymagające
PostgreSQL/WeasyPrint/pełnego repozytorium (`test_migration_*`, `test_report_*`, `test_mpzp_parameter_evidence`
i in.) działają w kontenerze.

Frontend w Dockerze (`docker build --target test` + `npm run typecheck && npm run test:coverage &&
npm run build`): **40 plików, 449 testów, 0 niepowodzeń; build produkcyjny bez błędów**; `lib/mpzpConditions.ts`
100% pokrycia (dodany do `coverage.include`), `MpzpZoneCard.tsx` 100% linii.

Artefakt kalibracji: `scripts/calibrate_mpzp_confidence.py --check` potwierdza, że odtwarza się z danych
(SHA-256 danych `24d19835…`). Wyniki ewaluatora (`results/parser/{legacy,v3,comparison}`) wygenerowano
ponownie z bieżącego kodu; poprzednie zachowano w `results/parser/pre-PV3-07/`.
