# Odbiór PV3-04, PV3-05 i PV3-06 (Epic 20A)

Data: 2 października 2026 r. Baza kodu: `0d78dbf` z niezacommitowanymi zmianami (nic nie zostało
zacommitowane; `docs/evaluation` i `docs/adr` są w `.gitignore`, więc nowe pliki tych katalogów
wymagają `git add -f`).

## Stan zadań

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| PV3-04 — normalizacja i walidacja symbolu strefy | **wykonane** | Wersja kontraktu 2.1 → 2.2 (cache). Zapisane formy nie wymagają przeliczenia; patrz „Wpływ na cache” |
| PV3-05 — drzewo struktury dokumentu i `ZoneBlock` | **wykonane** | Węzły `html_list_item` powstają tylko przy nowych ekstrakcjach HTML (wskazówki struktury zapisuje ekstraktor); wiersze tabel, których nie da się zmapować na tekst, są raportowane w `unmapped`, nie gubione |
| PV3-06 — resolver zakresu strefy | **wykonane; wyniki rozwojowe** | Resolver rozwijano na tych samych 21 próbkach BK-603, na których mierzy się wynik; etykiety układów 1–6 nadał asystent AI. To **nie jest** ocena uogólnienia (ta wymaga zbioru końcowego z PV3-02, którego nie ma) |

Czego to zadanie **nie** zmienia: domyślny tryb parsera pozostaje `legacy` (`scope_mode="legacy"`),
więc produkcyjne wyniki analiz są takie jak przed zmianą. Tryb blokowy jest dostępny dla ewaluatora
(silnik `v3`) i dla Tasków 20.7–20.14; jego włączenie w produkcji wymaga kolejnego podniesienia wersji
kontraktu (Task 20.14). Recall wzorców wartości (rozszerzanie wzorców liczbowych) to Task 20.7 —
tu mierzony tylko jako obserwacja.

## Polecenia odbiorowe

```bash
# PV3-06: ewaluacja BK-603 silnikami legacy i v3 jednym poleceniem (z katalogu backend/ — z rootu repo
# ładuje się .env, którego Settings nie akceptuje; to ograniczenie istniało wcześniej)
cd backend
python3 scripts/evaluate_mpzp_parser.py --engine legacy v3 \
  --output-dir ../docs/evaluation/results/parser --repeat 2
# wyniki: results/parser/{legacy,v3,comparison}/ — v3/metrics.json → scope, v3/scope_results.json,
# v3/report.md sekcja „Zakres strefy (PV3-06)”, comparison/comparison.md

# PV3-04/05/06: testy nowych modułów
python3 -m pytest tests/test_zone_symbol.py tests/test_document_tree.py tests/test_mpzp_zone_scope.py \
  tests/test_mpzp_parser_blocks.py tests/test_evaluate_mpzp_parser.py tests/test_mpzp_eval_engines.py \
  tests/test_mpzp_zones.py tests/test_analyze_resume.py -q

# frontend (PV3-04)
cd ../frontend && npm ci && npm run typecheck && npm run test:coverage && npm run build
```

Pełny zestaw w kontenerze skonfigurowanym jak CI — wynik w sekcji „Weryfikacja”.

## PV3-04 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Forma kanoniczna: NFKC, przycięcie, zwinięcie odstępów; litery, cyfry, `. _ / - , + ( )`, pojedyncze spacje; ≤ 40 znaków; znaki sterujące odrzucone | spełnione | `app/shared/zone_symbol.py` (`canonicalize_zone_symbol`, `validate_zone_symbol`); 41 przypadków w `shared/zone-symbol-rules.json` z kodami błędów (`empty`, `too_long`, `control_characters`, `disallowed_characters`) |
| Jeden wzorzec dla backendu, API i UI; test wykrywa rozjazd | spełnione | backend (`test_zone_symbol.py`) i frontend (`zoneSymbol.test.ts`) iterują ten sam `zone-symbol-rules.json`; `ManualZoneContext` niesie wzorzec, długość i `symbol_rules_version`; obraz frontendu kopiuje plik przez `COPY --from=shared` |
| Dopasowanie w tekście tolerancyjne na odstępy, bez scalania `MN` z `MN.1` | spełnione | `find_zone_symbol_mentions` z granicami tokenu i filtrem ogona symbolu ze spacją (`146 MN`); wyniki `legacy` na korpusie wiersz po wierszu bez różnic |
| 15 kandydatów discovery z `reference_corpus` i 42 symbole z korpusu parsera przechodzą | spełnione | testy korpusów w `test_zone_symbol.py` |
| Wersja sygnatury cache podniesiona, jeśli zmienia się zapisywana postać; wpływ opisany | spełnione | `MPZP_RESULT_SCHEMA_VERSION` 2.2; aneks PV3-04 w ADR-005 (pkt 18–22) |
| Kolumna na symbol, nowa migracja po head, bez przepisywania historii | spełnione | `027_zone_symbol_length` (po `026_section_quality_matrix`): `analyses.resolved_zone_symbol` 20 → 50; downgrade przywraca 20; test migracji |

### Wpływ na cache

Formy zapisane przed zmianą (≤ 20 znaków, bez spacji i przecinków) są już kanoniczne, więc nie ma potrzeby
ich przeliczać. Wersja kontraktu rośnie mimo to, bo kontrakt API się rozszerzył (`entered_symbol_raw`,
`symbol_rules_version`), a wynik parsera dla symboli ze spacjami lub przecinkami może się różnić od
zapisanego. Sygnatura cache (`RESULT_CONTRACT_VERSION`) obejmuje tę wersję, więc starsze snapshoty nie są
serwowane jako trafienie cache; odczyt historyczny działa bez zmian. Downgrade migracji 027 wymaga
wcześniejszego przycięcia wartości dłuższych niż 20 znaków.

### Zmiana zachowania

Symbole wcześniej odrzucane (`1 MN`, `1U,MN`, `MN+U`, do 40 znaków) są teraz przyjmowane, a surowy wpis
trafia do dowodu ręcznego wyboru (`entered_symbol_raw`). Dwa istniejące testy zakładały stary limit
(`symbol_max_length == 20`, odrzucenie `1 MN`) i zostały zaktualizowane do nowej reguły; odrzucenie
sprawdza się teraz symbolem z niedozwolonym znakiem.

## PV3-05 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Drzewo: `chapter`, `paragraph`, `section`, `point`, `letter`, `dash`, `table_row`, `html_list_item`; strona, zakres znaków, ścieżka | spełnione | `app/modules/documents/domain/document_tree.py`; `test_document_tree.py` (38 testów) |
| Konkatenacja liści = znormalizowany tekst; strony monotoniczne | spełnione | `verify_tiling` (bez luk i nakładania), testy na dokumentach korpusu; digest drzewa deterministyczny |
| Warstwa normalizacji flaguje artefakty (łączniki, numery stron, spacje twarde itd.) i zachowuje mapę do tekstu surowego | spełnione | `text_normalization.py`; pozycje bloków sprowadzane do tekstu surowego (`origin`) |
| `ZoneBlock` ma `block_id`, `scope_kind`, `symbols`, `text`, `char_span`, `pages`, `path`, `scope_confidence`, `warnings` | spełnione | `app/modules/planning/domain/zone_blocks.py` (serializacja i skrót `blocks_digest`); dodatkowo `strategy`, `strategy_reason`, `node_ids`, `segments` + `locate` |
| „N) dla terenu X:” jest osobnym blokiem | spełnione | test na korpusie Krakowa (strategia 3) |
| Tryb kompatybilności segmentacji | spełnione | `scope_mode="legacy"` jest domyślny; `test_default_mode_is_the_unchanged_legacy_pipeline` |
| Test architektury ADR-001 | spełnione | `test_architecture.py` (moduły domenowe bez frameworków; `planning/domain` nie importuje `documents`; adapter drzewa → widok leży w `services`) |

## PV3-06 — mapowanie kryteriów akceptacji

Korpus BK-603 (21 próbek, 17 dokumentów, 13 gmin), silniki `legacy` i `v3`, `mpzp-parser/2.0` →
`mpzp-parser/3.0-scope.1` w trybie blokowym.

| Kryterium | Próg | Wynik | Dowód |
|---|---|---|---|
| `source_consistent` na zbiorze rozwojowym | ≥ 0,98 | **1,000 (88/88)** [0,96; 1,00]; legacy 0,680 (51/75) | `v3/metrics.json` → `detection.source_consistent` |
| Zasięg zakresu per układ 1–6 | ≥ 0,95 każdy | **1,0 w każdym układzie**: 1 — 139/139, 2 — 53/53, 3 — 48/48, 4 — 30/30, 5 — 12/12, 6 — 10/10 (ponadto 0 — 2/2) | `v3/metrics.json` → `scope.by_label`; razem 294/294 |
| Klauzule ogólna i resztowa nie trafiają do `zone_section` | 0 | **0/428** zanieczyszczeń; `general_clause` osobno: 12/12 | `scope.contamination`, `scope.by_applicability`; testy w `test_mpzp_zone_scope.py` |
| Test MN.11 z Krakowa: własna strona i zakres znaków | — | 11 m i 9,5 m ze strony 19, zakres wskazuje dokładnie dopasowaną wartość (`raw_value`) | `test_krakow_mn11_height_has_its_own_page_and_character_range`, `test_blocks_mode_does_not_take_mn11_heights_from_the_neighbouring_zone_pages` |
| Pokrycie testami zmienionych modułów | ≥ 80% | 96–100% (poniżej) | „Weryfikacja” |
| Wyniki BK-603 odtwarzalne jednym poleceniem | — | tak; ten sam manifest daje te same metryki merytoryczne (`determinism.json`) | polecenie wyżej |
| Strona i zakres znaków każdej wartości z własnego dopasowania | — | `_RawMatch.start/end` → `MpzpParameter.page_number/char_start/char_end/block_id/scope_kind/scope_strategy` | `test_mpzp_parser_blocks.py` |
| OCR (`I/1`, `O/0`, `l/1`) tylko z karą i flagą | — | tryb zapasowy: pewność ×0,75 i ostrzeżenie `ZONE_SYMBOL_OCR_MATCH`; nigdy domyślnie | `test_mpzp_zone_scope.py` |

### Porównanie `legacy` → `v3` (te same pary: 423 wiersze, 250 z wartością w anotacji)

| Metryka | legacy | v3 |
|---|---|---|
| precision | 1,000 (62/62) | 1,000 (73/73) |
| recall | 0,248 (62/250) | 0,292 (73/250) |
| wartości dokładne / częściowe / błędne wśród znalezionych | 50 / 9 / 3 | 62 / 8 / 3 |
| `source_consistent` | 0,680 (51/75) | 1,000 (88/88) |
| przypisanie do strefy: wg wartości → ze źródłem | 37/37 → 26/37 | 43/43 → 43/43 |
| dokładność end-to-end: wg wartości → ze źródłem | 50/250 → 39/250 | 62/250 → 62/250 |
| wartości poprawne z niewłaściwego źródła (`wrong_source`) | 13 | 0 |
| klauzule ogólne znalezione / pominięte | 0 / 12 | 0 / 12 |

Różnica „wartość dokładna i z właściwego źródła” w analizie sparowanej (250 par): +0,072 [+0,032; +0,111],
b/c = 1/19, McNemar p < 0,001; per podział: rozwojowy +0,067 (p = 0,031), końcowy +0,037 (p = 0,070).
Poprawa dotyczy głównie PDF tekstowych (+0,089); w skanie symulowanym `v3` ma o jedną parę mniej niż
`legacy` (1/23 → 0/23): skan nie ma struktury, więc zadziałał zapas (strategia 0), a nie rozpoznany układ.

**Czego te liczby nie mówią.** Wzrost recallu 62 → 73 (12 więcej wartości dokładnych) wynika z tego, że `v3`
czyta cały blok strefy zamiast okna po kotwicy; to **nie** jest rozszerzenie wzorców wartości (Task 20.7).
Zasięg 12/12 dla klauzul ogólnych oznacza, że blok klauzuli został wskazany poprawnie, ale wartości z tych
klauzul nadal nie są wydobywane (0/12 w obu silnikach) — to praca kolejnych tasków, nie tego.

## Ograniczenia i ryzyka (czytać przed wnioskami)

1. **Wynik jest rozwojowy.** Resolver rozwijano iteracyjnie na 21 próbkach, na których mierzy się wynik;
   1,0 w układach 1–6 oznacza „pokrywa przypadki, które widział”, nie „uogólnia”. Niezależna ocena wymaga
   zbioru końcowego (PV3-02), którego nie zbudowano.
2. **Etykiety układów** (`scope_strategies.json`) nadano z lektury tekstu; są poza
   zamrożonymi anotacjami i czekają na przegląd człowieka. Układ „5” obejmuje klauzule ogólne
   (`applicability = general_clause`).
3. **Pojedyncze literówki źródłowe** (np. `6.8MW/U` zamiast `6.8.MW/U` w Łodzi) obsługuje dopasowanie
   przybliżone (różnica kropek) z ostrzeżeniem `ZONE_SYMBOL_NEAR_MATCH`; to heurystyka dobrana do korpusu.
4. **Skany bez struktury** (strategia 0) dostają blok `fallback` (cały paragraf, pewność 0,3) i ostrzeżenie
   `ZONE_SCOPE_AMBIGUOUS`; to zapas, nie rozstrzygnięcie.
5. **Zasięg ≠ poprawność wartości.** Metryka mierzy, czy cytat anotacji leży w wybranym bloku; recall
   wartości pozostaje 0,29 i jest przedmiotem Tasków 20.7–20.12.
6. Wpływ na produkcję: brak do Task 20.14 (tryb domyślny `legacy`); jedyna zmiana widoczna dla użytkownika
   to szersza reguła ręcznego symbolu (PV3-04).

## Weryfikacja

Pełny zestaw w kontenerze jak w CI (obraz backendu, PostgreSQL z migracjami do `027`, repozytorium jako
`/repo:ro` bez `.env`, kod z bieżącego drzewa, `pytest -m 'not docker_cli' --cov=app --cov-report=term-missing
--cov-fail-under=80`): **2304 testy przeszły, 3 odrzucone znacznikiem `docker_cli`, 0 niepowodzeń, pokrycie
`app` 93,07%** (próg 80% bez zmian; 2 października 2026 r., 3 min 43 s). Poprzedni przebieg tego samego dnia
wykrył dwa testy zakładające stary limit symbolu (`test_analyze_returns_manual_zone_required_when_no_vectors`:
`symbol_max_length == 20`; `test_bk204_waiting_preview_resume_partial_and_pdf`: odrzucenie `1 MN`) —
zaktualizowano je do reguły PV3-04 i przebieg powtórzono w całości.

Lokalnie (macOS), nowe i zmienione moduły (268 testów): `zone_symbol` 99%, `document_tree` 97%,
`text_normalization` 99%, `zone_blocks` 99%, `zone_scope` 97%, `mpzp_parser_blocks` 96%,
`mpzp_parser_structure` 100%, `mpzp_eval_engines` 100%, `evaluate_mpzp_parser` 86% (próg 80%).
Testy wymagające PostgreSQL/WeasyPrint/pełnego repozytorium (`test_migration_*`, `test_report_*`,
`test_repo_structure`, `test_rate_limit_dependency` i in.) nie działają lokalnie na macOS i przechodzą
w kontenerze.

Frontend w Dockerze (`docker build --target test` + `npm run typecheck && npm run test:coverage &&
npm run build`): **39 plików, 439 testów, 0 niepowodzeń; build produkcyjny bez błędów**; pokrycie
`lib/zoneSymbol.ts` zgodnie z progami `vitest.config.ts` (moduł już na liście `coverage.include`;
nowych modułów frontendu nie dodano).

Zmienione/nowe artefakty: `docs/report/field-mapping.md` zregenerowany (542 pola; test
`test_report_field_mapping` pilnuje zgodności z modelem).
