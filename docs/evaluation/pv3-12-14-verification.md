# Odbiór PV3-12, PV3-13 i PV3-14 (Epic 20A/20B)

Data: 5 października 2026 r. Baza kodu: `644e39a` z niezacommitowanymi zmianami PV3-03–11 i tych zadań
(nic nie zostało zacommitowane; `docs/evaluation` i `docs/adr` są w `.gitignore`, więc nowe pliki tych
katalogów wymagają `git add -f`). Decyzje projektowe: aneks PV3-12–14 w
[ADR-012](../adr/ADR-012-mpzp-llm-extraction.md).

## Stan zadań

Żadne z trzech zadań nie było wcześniej wykonane (przed zmianą nie istniał weryfikator kandydatów, tabela
cache wywołań ani flaga trybu parsera; ścieżka modelu nie była podłączona do analizy).

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| PV3-12 — weryfikator kandydatów (bramki G1–G8) | **wykonane** | Heurystyka terminu parametru i wykrywania „polecenia/JSON” w cytacie to jawna polityka, nie zmierzona jakość |
| PV3-13 — cache i provenance (migracja) | **wykonane** | Migracja ma numer **029**, nie 027 (patrz niżej); żadna odpowiedź nie została zapisana z prawdziwego modelu |
| PV3-14 — tryby parsera i tryb cienia | **wykonane (offline)** | Domyślny tryb pozostaje `legacy`; tryby z modelem nie były uruchomione na żywo; budżet to progi wejściowe z ADR-012 (dopracowanie: Task 20.15) |

Nic nie zostało zmierzone na żywo: model w testach to dostawca skryptowy, złote odpowiedzi (składane z
adnotacji, **nie nagrania modelu**) albo prawdziwy adapter Gemini na zamrożonej odpowiedzi `respx`.

## Polecenia odbiorowe

```bash
cd backend
# bez bazy, bez internetu i bez klucza
python3 -m pytest tests/test_llm_candidate_verifier.py tests/test_mpzp_parser_modes.py tests/test_mpzp_eval_hybrid.py \
  tests/test_llm_repository.py tests/test_migration_029.py tests/test_architecture.py -m "not integration" -q
# zamrożony wynik trybów deterministycznych (punkt odniesienia regresji)
python3 scripts/freeze_mpzp_parser_modes.py --check
# czyszczenie cache modelu poza retencją (MPZP_LLM_CACHE_RETENTION_DAYS, domyślnie 180 dni)
python -m app.modules.planning purge-llm-cache
```

Testy z PostGIS (`integration`: migracja 029, repozytorium, orkiestrator, wznowienie) i pełny zestaw —
w kontenerze skonfigurowanym jak CI; wynik w sekcji „Weryfikacja”.

## PV3-12 — mapowanie kryteriów akceptacji

Moduł: `backend/app/modules/planning/domain/candidate_verifier.py` (czysty, bez sieci, bazy i ustawień).

| Kryterium | Wynik | Dowód |
|---|---|---|
| Test własności: żaden przyjęty kandydat nie ma cytatu spoza bloku strefy ani wartości niezgodnej z wyliczoną z `raw_value` | spełnione | `test_property_…` — 3 ziarna × 1500 losowych odpowiedzi (wbudowany `random` z ustalonym ziarnem, bez Hypothesis): cytat przyjętego = dosłowny tekst bloku pod dopasowaniem, wartość = przeliczenie `raw_value` z bloku, liczba modelu (gdy podana) zgodna, status `ai_candidate`, zakres rozstrzygnięty |
| Testy przeciwników: zmyślony cytat, liczba poza cytatem, zmieniona liczba, cytat z innej strefy, ucięty cytat, tekst udający polecenie lub JSON | spełnione | `test_adversary_*` (6 testów + 3 warianty wstrzyknięcia): G3 `quote_not_in_block`, G4 `raw_value_not_in_quote`, G3 także z tolerancją OCR (cyfry muszą się zgadzać), G3/G7 `quote_names_other_zone`, G3 `quote_truncated` / G4 `raw_value_truncated`, G3 `quote_suspicious` |
| Wartość z modelu nigdy nie ma statusu `verified`; każde odrzucenie niesie kod bramki | spełnione | `AcceptedCandidate` odrzuca w konstruktorze inny status, metodę niż `llm_verified` i brak ręcznej weryfikacji; `validate_planning_rule` (rules.py) odrzuca `llm_verified` bez `ai_candidate`, `ai_candidate` bez `llm_verified` i bez cytatu; więzy bazy `ck_mpzp_parameters_llm_candidate` (migracja 029); `RejectedLlmCandidate` sprawdza, że kod należy do bramki |
| Strona i zakres znaków wyłącznie z dopasowania w bloku | spełnione | schemat odpowiedzi nie ma pola strony; `test_page_and_character_range_…` — strona z `DocumentStructureView.page_at` pozycji wartości, zakres przez `ZoneBlock.locate` (także dla bloku z segmentów); bez widoku i przy bloku na kilku stronach strona jest `None` (bez zgadywania) |
| ≥ 80% pokrycia modułu; progi CI bez zmian | spełnione | `candidate_verifier.py` 99% (pełny bieg); próg `--cov-fail-under=80` bez zmian |

Bramki (kolejność i kody stałe, `GATE_CODES`):

| Bramka | Kody |
|---|---|
| G1 katalog | `parameter_not_in_catalog` |
| G2 operator | `operator_not_allowed` |
| G3 cytat w bloku | `quote_empty`, `quote_suspicious`, `quote_not_in_block`, `quote_truncated`, `quote_lacks_parameter_term`, `condition_quote_not_in_block` |
| G4 surowa wartość w cytacie | `raw_value_empty`, `raw_value_not_in_quote`, `raw_value_truncated` |
| G5 przeliczenie | `raw_value_not_numeric`, `range_incomplete`, `unit_mismatch`, `value_mismatch` |
| G6 dziedzina | `domain_invalid`, `range_inverted`, `min_exceeds_max` (para kandydatów tej samej strefy i warunków) |
| G7 zakres | `symbol_not_in_block`, `quote_names_other_zone`, `scope_unresolved` (→ `applicability=unresolved`, wartość nieprzypisana) |
| G8 deduplikacja | `duplicate` |

Decyzje (szczegóły w aneksie ADR-012): G5 używa tej samej normalizacji co silnik deterministyczny
(`quantity_normalization.normalize_quantity`), wartość liczona z zapisu **w bloku**, nie z cytatu modelu;
liczba modelu różna od wyliczonej odrzuca kandydata (nie jest „ignorowana”); reguła `manual` wyłącznie dla
zapisów z flagą artefaktu (`degree_artifact`, `degree_letter`, `degree_ocr`, `double_notation_mismatch`,
`percent_to_ratio`). Tolerancja OCR: tylko dla dokumentów `ocr`, cytat ≥ 24 znaki, najwyżej
min(6, 8% długości) edycji, cyfry bez zmian; flaga `quote_ocr_fuzzy` i kara ×0,8. Próg zakresu G7: 0,6
(ten sam, poniżej którego tryb blokowy wymusza ręczną weryfikację). Pewność: model pewności z PV3-09 z
`origin=llm` (zawsze ręczna weryfikacja, poniżej pasma `high`). Liczniki odrzuceń per bramka trafiają do
`llm_metrics` (`llm.verifier.rejected.G1…G8`) i do logu zdarzeń bez treści.

Na złotych odpowiedziach (13 bloków, 7 przypadków) weryfikator przyjmuje 47 kandydatów, a odrzuca 3 — wszystkie
w G7 (`scope_unresolved`: blok zapasowy o pewności 0,3 i pusty `scope_quote`, układ 4, Łódź). To potwierdza
potok, nie jakość modelu (odpowiedzi złożono z adnotacji).

## PV3-13 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Migracja działa w górę i w dół na danych schematu 026 (PostGIS); historia nieprzepisana | spełnione | `test_migration_029.py`: baza cofnięta do `026` z danymi, `upgrade head` (027 → 028 → 029), stare wiersze i snapshot bez zmian, unikalność `cache_key` i więzy statusu, `downgrade` do `028` usuwa tabelę i kolumny, wiersze zostają; test statyczny: head = `029`, łańcuch 029→028→027→026, żadna historyczna migracja nie zna tabeli |
| Ten sam klucz = trafienie bez wywołania sieci; zmiana modelu, promptu albo schematu unieważnia cache | spełnione | `test_the_same_key_is_a_hit_without_calling_the_model` (dostawca szpiegujący zgłasza błąd przy wywołaniu), wersja na PostGIS w `test_a_new_analysis_with_the_same_key_uses_the_database_cache…`; `test_a_change_of_model_prompt_schema_or_parameters_invalidates_the_cache` (4 warianty) i test klucza (każde z 6 pól klucza i parametry: dostawca, limity, `thinking_level`, symbole) |
| Odczyt zapisanej analizy identyczny ze snapshotem i bez adaptera (adapter szpiegujący) | spełnione | `test_reading_a_saved_analysis_equals_the_snapshot_and_never_calls_the_adapter` (fabryki dostawcy i potoku oraz `extract_structured` podmienione na błąd); `test_hybrid_mode_persists_ai_candidates…` (odczyt = odpowiedź analizy) |
| W bazie nie ma treści żądania ani danych użytkownika; retencja i czyszczenie opisane | spełnione | `test_the_table_has_no_column_for_request_content_or_user_data` (dokładna lista kolumn), `test_the_record_holds_only_model_output_and_hashes_never_the_request`, test na PostGIS sprawdzający brak tekstu żądania w `response`; retencja: zapis poza `MPZP_LLM_CACHE_RETENTION_DAYS` nie jest trafieniem i jest odświeżany nowszym wywołaniem, `purge-llm-cache` go usuwa (testy w pamięci i na PostGIS) |
| ≥ 80% pokrycia zmienionych modułów | spełnione | `repository.py` 98%, `llm_pipeline.py` 99%, model ORM 100%, `__main__.py` 88%, `composition.py` 89% |

Kontrakt: tabela `mpzp_llm_extractions` — `cache_key` (SHA-256 z `document_sha256`, `block_sha256`,
`prompt_version`, `schema_version`, `model_id`, `params_hash`; unikalny), te pola osobno, `response_sha256`,
`response` (JSONB: wyłącznie wyjście modelu per część bloku z jej skrótami), tokeny wejścia i wyjścia (wyjście
z myśleniem), opóźnienie, koszt szacowany (cena od 2027 z ADR-012), status `ok`/`rejected_schema`/`error`,
`error_code`, `created_at`. Zapis idempotentny (`INSERT … ON CONFLICT`): `ok` w retencji jest niezmienny,
`error`/`rejected_schema` albo zapis przeterminowany zastępuje nowsza próba; tylko `ok` jest trafieniem.
Repozytorium otwiera własne krótkie sesje (cache nie zatwierdza ani nie wycofuje transakcji analizy).
Evidence parametru z modelu: `extraction_method=llm_verified`, `review_status=ai_candidate`, `model_id`,
`prompt_version`, `response_sha256` — w snapshocie i w kolumnach `mpzp_parameters`; dla wartości
deterministycznych pola **nie są emitowane** w JSON (bajtowo ten sam snapshot co wcześniej).

## PV3-14 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| `legacy`: odpowiedzi i snapshoty identyczne z poprzednimi (zamrożony snapshot) | spełnione | `tests/fixtures/mpzp_parser_modes/frozen_parse_results.json` zamrożony **na kodzie sprzed zmian** (10 dokumentów regresji × tryby `legacy`/`blocks`: kontrakt parsera i snapshot strefy API); `test_legacy_and_default_results_are_identical_to_the_frozen_snapshot` (bez trybu, z `mode="legacy"`, z podanym potokiem modelu — nie jest wołany); `v3` i `hybrid_shadow` = zamrożony wynik blokowy |
| Macierz trybów × dostępność modelu (działa, timeout, budżet wyczerpany); cień nie zmienia odpowiedzi ani zapisu | spełnione | parser: `hybrid` × {działa, timeout, 429, wyjątek, budżet żądań, budżet tokenów, brak potoku, błąd konfiguracji, prawdziwy adapter na `respx`: 200 i timeout}; `hybrid_shadow` × {działa, timeout, wyjątek} = odpowiedź `v3`; orkiestrator na PostGIS: odpowiedź **i zapisany snapshot oraz wiersze** cienia = `v3`, `hybrid` z modelem zapisuje kandydatów z provenance, `hybrid` bez modelu → `partial` + `MPZP_LLM_UNAVAILABLE`, `legacy` bez tworzenia potoku |
| Ręczny symbol: ten sam potok na przypiętej kopii bez ponownego pobrania | spełnione | `test_resume_runs_the_same_hybrid_pipeline_on_the_pinned_copy_without_fetching` (pobieranie zablokowane, parser czyta bajty kopii przypiętej, model dostaje jej tekst, kandydat `ai_candidate` w odpowiedzi) i wariant bez modelu (`partial` + ostrzeżenie); obie ścieżki wołają `parse_mpzp_document(..., **build_mpzp_parser_options().kwargs())` |
| Wywołania ograniczone do par z polityki (licznik wywołań) | spełnione | `test_model_calls_are_limited_to_blocks_of_the_targeted_pairs` (7 przypadków złotych: liczba wywołań = liczba bloków stref z parami bez wartości/konfliktem/niskim zakresem; cień = wszystkie bloki), brak par → 0 wywołań, blok wielosymbolowy raz, `select_targets` per reguła, przyjęte wartości tylko dla par z polityki |
| ≥ 80% pokrycia zmienionych modułów | spełnione | `mpzp_parser_hybrid.py` 93%, `mpzp_parser_options.py` 100%, `mpzp_parser.py` 98%, `cache.py` 100%, `analysis_resume.py` 99%, `analysis_orchestrator.py` 89%, `mpzp_zones.py` 96% |

Polityka scalania: wartość deterministyczna ma pierwszeństwo; zgodna wartość modelu nie dodaje kopii; brak
wartości deterministycznej → dodany `ai_candidate`; rozbieżność (ta sama przesłanka, inna wartość) →
obie zostają, wartość rdzenia dostaje ręczną weryfikację, pole płaskie API jest `null` i jest ostrzeżenie
o konflikcie. **Decyzja dodatkowa:** wartość `ai_candidate` nigdy nie wypełnia płaskiego pola strefy (np.
`max_floors`) — jest tylko w `parameters` z evidence, dopóki nie przejdzie ręcznej weryfikacji (ADR-012:
do bramki z Task 20.17 wartości modelu nie trafiają do wyniku jako ustalone). Strefa z kandydatem modelu
ma `manual_review_required`, więc analiza nie jest `complete`.

Sygnatura cache analizy zawiera `mpzp_parser` = {tryb, `parser_version`, `prompt_version`,
`schema_version`, `model_id`, `llm_enabled`} (wersje promptu i modelu tylko w trybach z modelem).
`MPZP_RESULT_SCHEMA_VERSION` **nie** rośnie: tryby deterministyczne dają ten sam wynik, a różnice trybów
rozróżnia nowa część sygnatury. Skutek wdrożenia: sygnatura każdej analizy zmienia się jednorazowo (nowe
pole), więc istniejące wpisy cache przestaną być trafieniem — analizy policzą się ponownie (TTL `complete`
7 dni, `partial` 15 min).

Budżet: jeden licznik na analizę (wspólny dla dokumentów kilku aktów), domyślnie 6 żądań i 12 000
szacowanych tokenów wejścia (progi wejściowe z ADR-012; szacunek przed wywołaniem: znaki/4, po wywołaniu —
liczba zgłoszona). Trafienie cache nie zużywa budżetu. Tryb cienia liczy model w zadaniu w tle
(`asyncio`), a różnice (zgodne/rozbieżne/tylko model/tylko rdzeń) trafiają do logu `app.mpzp_llm` i
liczników `llm.shadow.*` — bez tabeli porównawczej (issue dopuszcza log).

Ewaluator: silnik `hybrid` jest zarejestrowany (`--engine hybrid --llm-replay DIR`, a ręcznie `--live` przez
produkcyjny adapter w osobnym wątku). Brak zapisanej odpowiedzi przerywa przebieg (`ReplayMissError`) zamiast
cicho mieszać wynik deterministyczny — złote odpowiedzi pokrywają tylko 13 bloków, więc przebieg na całym
korpusie wymaga nagrania odpowiedzi (`--live`, poza CI; Task 20.17).

## Odstępstwa od opisu zadań (jawne)

1. **Numer migracji 029 zamiast 027** i plik testu `test_migration_029.py` zamiast `test_migration_027.py`:
   numery 027 (PV3-04) i 028 (PV3-08) są zajęte, a „po head 026” oznaczałoby rozgałęzienie historii.
2. **Klucz cache bloku** zgodny z opisem PV3-13 (skróty dokumentu i bloku, wersje, model, `params_hash`), a
   nie per-żądanie `extraction_cache_key` z PV3-11; ten drugi nadal kluczuje złote odpowiedzi i ewaluator, a
   w zapisie cache są skróty żądań części (`request_sha256`, `input_sha256`), więc oba się łączą.
3. **Kody G1–G8 rozszerzone o kody szczegółowe** (np. G3 `quote_suspicious`, `quote_lacks_parameter_term`):
   numer bramki i kolejność są stałe, kod mówi, który warunek bramki zawiódł.
4. **Metryki** to liczniki w pamięci procesu + log (projekt nie ma systemu metryk); eksport — Task 20.19.

## Ograniczenia i ryzyka (czytać przed wnioskami)

1. **Nic nie zmierzono na żywo** i nie ma nagranych odpowiedzi modelu; jakość ścieżki `hybrid` mierzy bramka
   z Task 20.17 na zbiorze końcowym z Task 20.2 (nie istnieje). Na złotych przypadkach rdzeń v3 znajduje już
   wszystkie wartości adnotacji, więc `hybrid` nie dodaje tam żadnej wartości (to nie jest pomiar korzyści).
2. Bramka terminu parametru (G3) i wykrywanie tekstu „polecenia/JSON” to heurystyki: mogą odrzucić poprawną
   wartość (np. punkt listy daleko od nagłówka z terminem, > 400 znaków) — odrzucenie zostawia wynik
   deterministyczny, nigdy nie dodaje błędnej wartości.
3. Weryfikator nie sprawdza semantyki operatora (`max` vs `min`) ani tego, czy cytat nie pomija warunku
   stojącego poza cytatem; to pozostaje zadaniem ręcznej weryfikacji (`ai_candidate`).
4. Dostawca jest tworzony per analiza, więc wyłącznik awaryjny adaptera nie przenosi stanu między analizami
   (Task 20.15/20.19).
5. Tryb cienia działa w tle procesu API: zadania przerwane restartem procesu przepadają (wynik analizy
   nie zależy od nich).
6. Domyślny tryb pozostaje `legacy`; przełączenie wymaga decyzji po bramce jakości (Task 20.17) i — dla
   `v3` — zgody właściciela na wyniki PV3-07–09 (rozwojowe).

## Weryfikacja

Backend — kontener jak w CI (obraz backendu, PostGIS w osobnym projekcie compose, kopia repozytorium bez
`.env` zamontowana tylko do odczytu jako `REPO_ROOT`), 2026-10-05:

```text
pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-fail-under=80
3001 passed, 3 deselected, 2 warnings in 321.57s
Required test coverage of 80% reached. Total coverage: 94.04%
```

Nowe testy: `test_llm_candidate_verifier.py` 45, `test_mpzp_parser_modes.py` 53, `test_llm_repository.py` 22,
`test_migration_029.py` 2, `test_mpzp_eval_hybrid.py` 11, oraz 7 w `test_analysis_orchestrator.py` i
`test_analyze_resume.py` (tryby, budżet analizy, wznowienie); zmienione: `test_architecture.py` (+2, reguła
adaptera dopuszcza w repozytorium cache wyłącznie jego model ORM), `test_evaluate_mpzp_parser.py` i
`test_mpzp_eval_engines.py` (hybryda nie jest już „niezaimplementowana” — wymaga `--llm-replay`/`--live`).
Dokument `docs/report/field-mapping.md` odtworzono z kodu (4 nowe ścieżki evidence: 550 → 554).

Pokrycie (pełny bieg): `candidate_verifier` 99%, `llm_pipeline` 99%, `llm_metrics` 100%, `repository` 98%,
model ORM 100%, `mpzp_parser_hybrid` 93%, `mpzp_parser_options` 100%, `mpzp_parser` 98%, `cache` 100%,
`analysis_resume` 99%, `analysis_orchestrator` 89%, `mpzp_zones` 96%, `rules` 99%, `composition` 90%,
`__main__` 88%.

Frontend (zmieniony tylko typ `MpzpParameterEvidence` w `frontend/lib/types.ts` — nowe opcjonalne pola, bez
nowych modułów, więc `coverage.include` bez zmian) — obraz `--target test` (Node 22, `npm ci` w obrazie):
`npm run typecheck` bez błędów, `npm run test:coverage` 40 plików / 449 testów (pokrycie 97,26% wierszy),
`npm run build` — kompilacja udana.

Regresja trybu `legacy`: `python3 scripts/freeze_mpzp_parser_modes.py --check` → „up to date” (plik zamrożony
przed zmianą kodu, sprawdzony też po każdej zmianie).
