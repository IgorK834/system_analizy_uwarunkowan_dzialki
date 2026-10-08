# Odbiór AU-004 (Task 21.4): parser KIMPZP — akty w punkcie, zmiany planu, „brak serwisu”

- Data: 2026-10-06. Stan: **zaimplementowane i zacommitowane w `main` (AU-009, 2026-10-08)**.
- Decyzje: [ADR-016](../adr/ADR-016-kimpzp-discovery-acts-and-source-status.md). Audyt: pozycje B4, R3 (dokumenty
  `docs/audit/…` wskazane w zadaniu nie istnieją w repozytorium — źródłem była treść zadania).
- Artefakty: [`results/au-004/`](results/au-004/).

## Kryteria akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| `141801_4.0701.23/8` zawiera `IV/30/2024` z `uchwala_url=http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf` oraz `576/XLVII/2010`; `LIV/467/2021` wyłącznie jako zmiana `576/XLVII/2010` | **spełnione** — zamrożona odpowiedź i żywa usługa | `mpzp_discovery.acts[0].text_url` w [01-api-response](results/au-004/01-api-response-141801_4.0701.23_8.json); `tests/test_mpzp.py::test_gora_kalwaria_parcel_lists_both_acts_with_text_links_without_choosing`, `tests/test_kimpzp_feature_info.py::test_gora_kalwaria_acceptance_values_from_the_audit`, `tests/test_analysis_orchestrator.py::test_gora_kalwaria_acts_reach_the_api_response_and_the_saved_snapshot` |
| Test własności: żaden `plan_id` z tabeli zagnieżdżonej | **spełnione** | `test_nested_amendment_table_never_yields_a_plan_id` (tabela zmian przeniesiona do komórki bloku), `test_amendment_table_without_any_plan_block_is_dropped_not_promoted`, `test_no_act_number_comes_from_a_nested_or_amendment_table[*]` (10 gmin) |
| Test odporności: losowa kolejność wierszy, brakujące pola, nieznane nagłówki — brak wyjątku i błędnego `plan_id` | **spełnione** | `test_fuzzed_responses_never_raise_and_never_produce_a_wrong_plan_id[*]` — 60 mutacji × 10 rzeczywistych odpowiedzi (tasowanie wierszy i kolumn, usuwanie ok. 20% pól, nieznane nagłówki z wartościami wyglądającymi jak numer uchwały); `test_truncated_and_garbled_responses_never_raise`, `test_degenerate_inputs_do_not_raise` |
| „brak serwisu dla wskazanego obszaru” → `no_coverage`, `KIMPZP_NO_SERVICE_FOR_AREA` | **spełnione** | fixture Dygowo; `test_no_service_for_area_is_no_coverage_not_mpzp_not_found` (API: kod ostrzeżenia, brak `MPZP_NOT_FOUND`, macierz jakości `no_coverage`) |
| Flaga `MPZP_MULTIPLE_ACTS_AT_POINT`, bez cichego wyboru | **spełnione** | `selected_act=null`, dokument nie jest pobierany (`fetch.assert_not_called()`) |
| Zamrożone odpowiedzi z ≥ 5 gmin w `tests/fixtures/source_contracts/kimpzp/` | **spełnione — 10 gmin** (Góra Kalwaria, Kraków, Bielsko-Biała, Pisz, Legnica, Warszawa, Dygowo, Ruciane-Nida, Kalety, Inowrocław) | nieprzetworzone pliki + `manifest.json` (URL, czas, SHA-256); `test_fixtures_are_unmodified_service_responses_listed_in_the_manifest` |
| Dokumentacja: `docs/current_state.md` i ADR | **spełnione** | sekcja AU-004 w `current_state.md`, ADR-016, `docs/data_sources/mpzp_contracts.md`, README fixtures |
| ≥ 80% pokrycia | **spełnione** | backend 94,50% (`kimpzp_discovery.py` 99%, `kimpzp_feature_info.py` 99%, `services/mpzp.py` 100%, `section_quality.py` 100%); frontend 97,43%, `lib/mpzpDiscovery.ts` 100% linii, nowe moduły w `coverage.include` |
| Zmiana `AnalyzeResponse` → `*_SCHEMA_VERSION`, `RESULT_CONTRACT_VERSION`, `field-mapping.md` | **spełnione** | `MPZP_RESULT_SCHEMA_VERSION` 2.6 → 2.7 (`pog-v2.4+mpzp-v2.7+…`), `MPZP_DISCOVERY_SCHEMA_VERSION` 1.0, `docs/report/field-mapping.md` wygenerowany ponownie (601 ścieżek liści) |

## Weryfikacja

- **Backend** — kontener jak w `.github/workflows/ci.yml` (kopia repozytorium bez `.env` zamontowana tylko do odczytu jako
  `REPO_ROOT=/repo`, osobny projekt compose z PostGIS):
  `pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-fail-under=80` → **3902 passed**, 3 deselected,
  pokrycie **94,50%**.
  Pierwszy przebieg wykazał 5 testów do aktualizacji: trzy testy z PV3 przypinały dokładną wersję `2.6` (teraz `≥ 2.6`,
  bo kolejne podniesienie zachowuje unieważnienie cache), zamknięta lista pominięć mapowania raportu (format odpowiedzi
  jest teraz pokazany w Tabeli 3.5 zamiast pomijany) oraz sonda defektu RC-01 badania korpusu — patrz niżej.
- **Frontend** — obraz `--target test` (Node 22): `typecheck`, `test:coverage` (**522 testy**, pokrycie 97,43%) i `next build`.
- **Na żywych usługach** (2026-10-06, stos z bieżącym kodem, migracja `032_mpzp_discovery` zastosowana przez entrypoint):
  - `POST /analyze` dla `141801_4.0701.23/8` → 200; `mpzp_discovery`: `IV/30/2024` (obowiązuje od 2024-06-26, tekst
    `…/uch/IV_30_2024.pdf`) i `576/XLVII/2010` ze zmianami `LIV/467/2021`, `XXXIX/366/2017`; ostrzeżenie
    `MPZP_MULTIPLE_ACTS_AT_POINT`, brak `MPZP_DOCUMENT_OR_SYMBOL_MISSING`; sekcja MPZP w macierzy jakości `partial`
    (`MPZP_ACT_WITHOUT_ZONE`, `MPZP_MULTIPLE_ACTS_AT_POINT`).
  - Drugie wywołanie (cache) zwraca tę samą analizę i identyczną sekcję `mpzp_discovery` (odczyt ze snapshotu).
  - `GET /report/{id}` → PDF z Tabelą 3.5 (oba akty, linki, zmiany, „nie wybrano automatycznie”) i wersją sekcji w Tabeli 9.3
    ([02-report](results/au-004/02-report-141801_4.0701.23_8.pdf)).
  - UI (przeglądarka, obraz produkcyjny frontendu): karta „Akty wskazane przez KIMPZP” z oboma aktami; linki HTTPS (BIP,
    teksty zmian) klikalne, linki HTTP Góry Kalwarii pokazane jako tekst; zmiany wyłącznie przy `576/XLVII/2010`
    ([04-ui](results/au-004/04-ui-mpzp-discovery.jpg)).
- **Smoke korpusu referencyjnego** (30 działek + działka z audytu, kolejno, z przerwą na limit żądań; skrypt Task 21.11
  `backend/scripts/live_smoke_corpus.py` jeszcze nie istnieje, użyto skryptu ad hoc): **31/31 HTTP 200, 0 odpowiedzi 5xx**,
  najdłużej 35,3 s ([03-live-smoke](results/au-004/03-live-smoke-corpus.json)).

| Gmina | Działki | Status discovery | Uwagi |
|---|---|---|---|
| Góra Kalwaria | 1 | `available` | 2 akty, flaga wielu aktów |
| Kraków | 4 | `available` | `XII/131/11` ×3, `LIII/1464/21`; strefy przypisane jak dotąd (`document_candidate`) |
| Legnica | 5 | `available` | numer uchwały odczytany (wcześniej `plan_id=null`); brak linku do tekstu → `MPZP_DOCUMENT_OR_SYMBOL_MISSING` z numerem planu |
| Pisz | 5 | `available` | numer uchwały odczytany; symbol `NULL` nie jest już kandydatem; usługa podaje nazwy plików, nie linki |
| Czarny Bór | 4 | `available` | `XXVIII/139/2026` |
| Warszawa | 4 | `unavailable` | `<oms_error>` z HTTP 200 — wcześniej raportowane jako „nie znaleziono MPZP” |
| Bielsko-Biała | 7 | `unavailable` | `ServiceExceptionReport` z HTTP 200 — jw. |
| Ruciane-Nida | 1 | `no_match` | „brak wyniku dla wskazanego obszaru” → `MPZP_NOT_FOUND` (jak dotąd) |

## Zmiany zachowania i uwagi dla właściciela

1. **Wiele aktów → brak cichego wyboru.** Dotyczy też różnych aktów w różnych punktach działki
   (`MPZP_MULTIPLE_ACTS_ON_PARCEL`): wcześniej parser brał pierwszy plan i parsował jego dokument. Wybór aktu — AU-101.
2. **Błąd usługi gminnej nie jest „brakiem planu”.** 11 z 30 działek korpusu (Warszawa, Bielsko-Biała) ma teraz
   `MPZP_DISCOVERY_UNAVAILABLE` (`severity=error`) i sekcję MPZP `unavailable` zamiast `MPZP_NOT_FOUND`/`unknown`.
   Zamrożone artefakty korpusu (`status: no_mpzp`) opisują stan sprzed zmiany i nie były przeliczane.
3. **RC-01 z badania korpusu (literał `NULL` jako symbol strefy) jest usunięty w kodzie** jako skutek uboczny walidacji
   wartości. Sonda `probe_null_symbol` w `backend/scripts/evaluate_reference_corpus.py` uruchamia bieżący parser i teraz
   zwraca `None`; test oczekuje tej wartości. Rejestr błędów badania nadal opisuje defekt zamrożonych artefaktów.
4. Linki `http://` (np. Góra Kalwaria) nie są klikalne w UI — zgodnie z istniejącą polityką `*_verified` (tylko HTTPS).
5. Plan rastrowy z odpowiedzi HTML (Kalety) nie uruchamia trybu ręcznego symbolu; ścieżka rastrowa pozostała jak była
   (kontrakt JSON `vector_available=false`). Do decyzji w AU-101.
6. Kraków: link „WWW” to strona BIP; jak dotychczas jest używany jako dokument dla parsera (`document_url` = tekst uchwały,
   a gdy go brak — ogólny link WWW). BIP i legenda nigdy nie są dokumentem parsera.
