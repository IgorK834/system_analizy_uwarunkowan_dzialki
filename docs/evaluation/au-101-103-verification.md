# Odbiór AU-101, AU-102, AU-103 — akty z Rejestru Urbanistycznego, sekcje stref, jawny stan MPZP (Tasks 22.1–22.3)

Data: 10–11 października 2026 r. Baza kodu: `61f26da` z **niezacommitowanymi** zmianami w chwili odbioru (wszystkie trzy zadania razem; nic nie zacommitowano ani nie wypchnięto). Stan na 13 października 2026 r.: zmiany są w historii `main` (commity `ecbcbb7`…`fcf6a9b`).
Decyzje: [ADR-020](../adr/ADR-020-mpzp-acts-ru-channel-precedence-and-state.md) (AU-101/103), [ADR-021](../adr/ADR-021-mpzp-zone-section-detection.md) (AU-102);
analiza przyczyn AU-102: [au-102-zone-section-causes.md](au-102-zone-section-causes.md).

## Stan zadań: **wykonane; zastrzeżenia opisane niżej**

Przed pracą żadne z trzech zadań nie było wykonane: w repozytorium nie było `mpzp_acts`, `mpzp_status`, adaptera RU ani zbioru uchwał, a pliki `docs/audit/*`
wskazane w opisach zadań nie istnieją. Zastrzeżenia (każde uzasadnione w sekcjach poniżej): (1) adnotacje zbioru AU-102 przygotował asystent AI bez przeglądu człowieka, a zbiór
nie jest ślepy; (2) w zbiorze 9 z 10 wskazanych uchwał — dziennik Warszawy nie odpowiada; (3) wartości wymagające decyzji właściciela (niżej).

## AU-101 — akty MPZP z Rejestru Urbanistycznego (Task 22.1)

| Kryterium | Stan | Dowód |
|---|---|---|
| Adapter RU na `OgcClient` (`modules/planning/infrastructure/ru_mpzp_acts.py`), kolejność osi północ,wschód dla URN EPSG:2180, stronicowanie po 10 | spełnione | `test_ru_mpzp_acts.py` (parametr `bbox`, `posList`, strony, `resourceId`, błędy 400/200 `ExceptionReport`); katalog `docs/data_sources/catalog.yaml` (`mpzp_ru`, kontrakt i znane ograniczenia) |
| Przecięcie granicy aktu z działką w EPSG:2180 (`intersection_area_sqm`, `intersection_pct`); akt tylko dotykający obwiedni odrzucony z diagnostyką | spełnione | `modules/planning/domain/mpzp_acts.py`; `test_krakow_draft_inside_the_bbox_never_becomes_a_binding_act`, `test_axis_order_change_on_the_service_side_is_detected_not_read_as_no_overlap`; Góra Kalwaria: cz. II odrzucona (styk 5,994 m) |
| Jawna, wersjonowana, informacyjna reguła pierwszeństwa; nierozstrzygnięte → oba akty + `manual_review_required` | spełnione | `mpzp-act-precedence/1.0` (ADR-020); `test_overlapping_acts_are_unresolved_and_newer_date_does_not_decide` |
| `AnalyzeResponse.mpzp_acts[]` (numer, tytuł, daty, status, link, udział, `sources: kimpzp|mpzp_ru`, zgodność kanałów, `manual_review_required`); `MPZP_CHANNEL_MISMATCH` | spełnione | `schemas/analyze.py`; `test_mpzp_acts_service.py`, `test_mpzp_acts_pipeline.py` |
| Wersja kontraktu podniesiona, `docs/report/field-mapping.md` odświeżony, migracja po head | spełnione | `MPZP_RESULT_SCHEMA_VERSION` 2.7 → 3.0 (2.8 akty; 2.9 parser AU-102; 3.0 stan AU-103); migracja `033_analysis_mpzp_acts`, `034_analysis_mpzp_status` (head); `test_migration_033_034.py` |
| UI: lista aktów z linkiem do uchwały i mapa granic aktów; PDF: tabela + mapa; ZIP: `layers/mpzp_acts.geojson` (RU `redistribution: allowed`) | spełnione | `MpzpActsList` (+ testy); raport: Tabele 3.6/3.7, „Mapa 3a” tylko gdy są akty (`test_report_mpzp_acts.py`; mapy referencyjne bez zmian); eksporter 1.3.0 (`test_audit_mpzp_acts.py`); **żywo**: PDF i UI dla działki audytowej, zob. niżej |
| RU nigdy nie zasila `vector_zones`; akty nigdy nie są raportowane jako strefy | spełnione | `test_mpzp_acts_pipeline.py::test_ru_act_is_reported_when_kimpzp_has_a_service_error` (akt RU → `mpzp_zones == []`, `source_id` w macierzy jakości); adapter RU nie jest importowany przez ścieżkę `vector_zones` (kod: tylko `services/mpzp_acts.py`) |
| Architektura ADR-001 (port w `application`, adapter w `infrastructure`), brak sekretów | spełnione | test architektury zielony; `ruff`, `mypy` bez nowych zgłoszeń |
| Akceptacja: `141801_4.0701.23/8` wskazuje `IV/30/2024` i opisuje cz. II, jeśli przecina | spełnione (z zastrzeżeniem) | **żywo (2026-10-11):** stan `ambiguous` (`MPZP_ACTS_UNRESOLVED`), akty `IV/30/2024` i `576/XLVII/2010` (KIMPZP), RU: „nie wskazał aktu” (RU nie ma tam obowiązującego `IV/30/2024` — pokrycie RU jest niepełne, ADR-020); zamrożona odpowiedź RU: cz. II odrzucona jako styk (5,994 m) z diagnostyką |
| Akceptacja: gmina bez KIMPZP, z RU → akt z RU (`status=available`, `source=mpzp_ru`) | spełnione | Bielsko-Biała: akt tylko z RU, 11,84% działki (zamrożona odpowiedź + test) |
| Akceptacja: oba kanały → sprawdzenie zgodności | spełnione | `channel_agreement` (`agree|mismatch|single_channel|none`) + `MPZP_CHANNEL_MISMATCH`, testy jednostkowe i potoku |
| ≥ 80% pokrycia | spełnione | zob. „Weryfikacja” |

## AU-102 — sekcje stref w uchwale (Task 22.2)

| Kryterium | Stan | Dowód |
|---|---|---|
| Zbiór ≥ 10 realnych uchwał z zamrożonymi stronami/zakresami sekcji (Kraków ×3, Bielsko-Biała, Pisz, Legnica, Góra Kalwaria, Warszawa, Czarny Bór, Ruciane-Nida) | **częściowo: 9/10** | `backend/tests/fixtures/mpzp_live` (bez PDF-ów; skróty SHA-256); **brak Warszawy** — `edziennik.mazowieckie.pl` nie odpowiada (`ReadTimeout` 150 s, 10 i 11 października; TLS nie wyłączano); jawna decyzja o „innej uchwale tej gminy” użyta tam, gdzie plan z korpusu był niedostępny |
| Raport przyczyn z kategoriami | spełnione | [au-102-zone-section-causes.md](au-102-zone-section-causes.md): 5 przyczyn (C1–C5), liczby przed → po, co pozostaje |
| Poprawka rdzenia deterministycznego (aliasy symboli, symbol w nagłówku vs tabeli, strefy zbiorcze „1MN, 2MN”) **bez dostrajania do 21 próbek BK-603** | spełnione z zastrzeżeniem | zakresy symboli, wprowadzenia sekcji, silnik opisowy `descriptive/2`, jawne powody; migawka BK-603 po zmianach: **same dodatki, brak ubytków i zmian wartości**; zastrzeżenie: zbiór `mpzp_live` nie jest ślepy (część poprawek wyprowadzono z dokumentów `holdout`) |
| Metryka: odsetek stref z ≥ 1 parametrem zamrożona w `docs/evaluation/` | spełnione | `results/au-102/before-head.json`, `after.json`; 56% → **100%** w obu trybach (próg 70%); metryka surowa 46%/49% → 100%/100%; recall `v3` 94% |
| Akceptacja: `MW/U.4` ≥ 3 parametry z dowodem (strona, cytat) | spełnione | `legacy`: 6, `v3`: 8 parametrów, każdy ze stroną 42–44 i cytatem znajdującym się na tej stronie (`test_mpzp_live_corpus.py::test_mw_u_4_…`); `v3` także ze znakami w tekście strony |
| Akceptacja: ≥ 70% stref z ≥ 1 parametrem **albo** poprawnym jawnym powodem (`SCANNED_NO_OCR`, `ZONE_NOT_IN_DOCUMENT`) | spełnione | 41/41 (100%); 7 stref z jawnym powodem (4 × `ZONE_NOT_IN_DOCUMENT`, 3 × `SCANNED_NO_OCR`); **wynik opisuje znany zbiór — nie uogólnienie** |
| Dokumentacja + ADR; ≥ 80% pokrycia | spełnione | ADR-021, `docs/current_state.md`, README; zob. „Weryfikacja” |

Dodatkowo (poza listą kryteriów, ale potrzebne, żeby dokument w ogóle trafił do parsera): `mpzp_fetch` idzie za linkami stron BIP (dokument → karta → plik, ≤ 2 skoki, ta sama witryna,
SSRF na każdym skoku). Na żywo trzy `landing_url` planów krakowskich dały pliki o SHA-256 identycznym z zamrożonymi dokumentami; w przebiegu smoke (niżej) analiza działki
krakowskiej z KIMPZP (`?dok_id=137657`) po odświeżeniu zwróciła strefę `ZPb.9` z 5 parametrami z właściwego PDF-a (wcześniej parser dostawał stronę HTML).

## AU-103 — jawny stan MPZP (Task 22.3)

| Kryterium | Stan | Dowód |
|---|---|---|
| `mpzp_status` (`available|no_match|no_coverage|unavailable|unknown|ambiguous`) + `reason_code` | spełnione | `modules/planning/domain/mpzp_status.py`, `schemas/analyze.py`; migracja `034` |
| Osobne komunikaty UI/PDF zamiast „Nie sprawdzono albo nie znaleziono stref MPZP…” | spełnione | `shared/mpzp_status_text.py` (jedno źródło); `MpzpStatusBanner`; PDF (callout, §3); pakiet ZIP (README); starego zdania nie ma w żywym PDF-ie (sprawdzone) |
| Macierz jakości `mpzp` z właściwym `source_id` (było „brak źródła”) | spełnione | `section_quality._mpzp_from_status`; testy macierzy |
| Mapowanie: „brak serwisu” → `no_coverage`; pusta odpowiedź działającej usługi → `no_match`; timeout/5xx → `unavailable`; dwa nierozstrzygnięte akty → `ambiguous`; **`no_match` nigdy przy błędzie źródła** | spełnione `test_mpzp_acts_service.py` — tabela decyzyjna `test_status_table_without_acts` (wszystkie 6 stanów), `test_source_error_in_kimpzp_is_never_no_match_even_when_ru_answers_empty`, `test_no_coverage_in_kimpzp_and_nothing_in_ru_is_no_coverage_not_no_match`, `test_incomplete_ru_response_without_acts_is_unknown_not_no_match`; potok: `test_mpzp_acts_pipeline.py` (`…kimpzp_error_with_empty_ru_is_unavailable_never_no_match`, `…no_coverage_state_reaches_the_response_and_the_quality_matrix`); frontend: `MpzpStatusBanner.test.tsx` (6 stanów z fixtures generowanych z backendu); zamrożone odpowiedzi (Dygowo → `no_coverage`, Warszawa → `unavailable`)
| Stany widoczne w UI, PDF i pakiecie audytowym; wersja kontraktu podniesiona | spełnione | żywo: UI (baner, kanały, kod powodu, wersja reguły), PDF (26 str.), ZIP (README + `analysis.json: result.mpzp_status`); `MPZP_RESULT_SCHEMA_VERSION` 3.0 |
| Moduły frontendu w `coverage.include` w `frontend/vitest.config.ts`; dokumentacja | spełnione | `vitest.config.ts` (+ `MpzpStatusBanner`, `MpzpActsList`, `lib/mpzpActs`); `docs/current_state.md`, README |

## Weryfikacja

Backend (kontener `backend-test` jak w CI: repo tylko do odczytu, kopia bez `.env`, zapis tylko do `tmp_path`;
`pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-fail-under=80`):
**4491 passed, 1 failed, 3 deselected (`docker_cli`)**, pokrycie **94,59%** (próg 80%). Jedyny nieudany test to `test_readme_links` — niezwiązany z tą pracą (zob. „Ustalenia poza zakresem”, pkt 1).
- `ruff check .` — czysto; `python scripts/check_mypy_baseline.py` — „brak nowych zgłoszeń” (17 znanych; w tym przebiegu przesunęły się tylko numery linii dwóch wpisów w `quantity_engine.py`,
  jeden wpis `descriptive_engine.py` zniknął po naprawie typu).
- Frontend (`docker build --target test` i `npm run lint && npm run typecheck && npm run test:coverage && npm run build`): **49 plików, 573 testy zielone**, pokrycie instrukcji 97,78%, `eslint`,
  `tsc` i `next build` zielone.
- Odtwarzalność: migawka BK-603 (`freeze_mpzp_parser_modes.py --check`) aktualna; kalibracja pewności (`calibrate_mpzp_confidence.py --check`) odtwarza się z danych; złote odpowiedzi modelu
  (`build_llm_replay_fixtures.py --check`) zgodne.
- **Live smoke** (`python backend/scripts/live_smoke_corpus.py` na korpusie referencyjnym, 30 działek, backend z drzewa roboczego, świeża baza z migracjami do `034`):
  **5xx = 0, brak odpowiedzi = 0**, 30 × HTTP 200 (`partial`), p50 9,9 s, p95 51,4 s — [wynik](results/live-smoke/2026-10-11.md). Raport sygnalizuje 1 „regresję” względem 2026-10-08
  (`real-008-126105-9-0119-181-10`: strefy MPZP 1 → 0). Przyczyna: przejściowy błąd połączenia przy pobieraniu dokumentu (`MpzpDocumentFetchError`, sekcja `mpzp_document`), zapisany
  w cache analiz na 7 dni; po `force_refresh=true` ta sama działka ma strefę `ZPb.9` z 5 parametrami z właściwego PDF-a — czyli wynik lepszy niż 2026-10-08 (wtedy HTML bez parametrów).
- **Żywe usługi i interfejs** (działka audytowa `141801_4.0701.23/8`): stan `ambiguous`, akty `IV/30/2024` i `576/XLVII/2010`; w przeglądarce (build produkcyjny, `localhost:13000`) widoczny baner
  „Stan ustalenia MPZP: Wiele planów — brak rozstrzygnięcia”, lista kanałów, kod powodu i wersja reguły pierwszeństwa, lista aktów, wyłączony przełącznik granic aktów z wyjaśnieniem
  („akt znany tylko z KIMPZP nie ma granicy w źródle”); w konsoli jedyny błąd to oczekiwane 404 `/api/v1/map/pog/releases/active` (świeża baza bez wydania POG). PDF (26 str.): „Stan ustalenia MPZP: Wiele planów — brak
  rozstrzygnięcia. Akty: IV/30/2024, 576/XLVII/2010.”, Tabele 3.6 i 3.7; stare zdanie „Nie sprawdzono albo nie znaleziono…” nieobecne. ZIP: README z opisem stanu, `analysis.json` z `mpzp_status` i `mpzp_acts`
  (`layers/mpzp_acts.geojson` powstaje tylko dla aktów z granicą z RU, a tu RU nie wskazał aktu).

## Ustalenia poza zakresem zadań (do wiadomości)

1. `tests/test_readme_links.py` zawodził **niezależnie od tej pracy**: README linkowało do nieistniejącej noty `docs/evaluation/au-011-013-verification.md` (nigdy jej nie było w historii git). Naprawione 2026-10-12: link zastąpiono odwołaniami do faktycznych dowodów (raport live smoke, skrypt, workflow, ADR-019).
2. Na hoście (poza kontenerem) zawodzi `frontend/lib/maplibreWorker.test.ts` (zależność z `node_modules`); w kontenerze frontend jest zielony.
3. Analiza z przejściowym błędem pobrania dokumentu jest cache'owana na 7 dni (`ANALYSIS_CACHE_MAX_AGE_DAYS`) razem z ostrzeżeniem `MPZP_DOCUMENT_UNAVAILABLE` — zachowanie sprzed AU-101;
   `force_refresh` ją odświeża (limit 5/min).
4. Luki leksykonu wielkości (np. „wysokość elewacji lub attyki budynku”) — osobne zadanie z ponowną kalibracją (zob. au-102-zone-section-causes.md).

## Do potwierdzenia przez właściciela

- AU-101: minimalne pole przecięcia 0,5 m² (szum granic); informacyjny charakter reguły pierwszeństwa `mpzp-act-precedence/1.0` (czy nowszy plan ma domyślnie wypierać starszy — dziś nie);
  `redistribution: allowed` dla `mpzp_ru` (ostrożna interpretacja publicznego GetCapabilities, nie opinia prawna); brak reguły świeżości dla `mpzp_ru` (to samo `source_id` opisuje wektorowe strefy z importu).
- AU-102: niezależny przegląd adnotacji `mpzp_live` przez człowieka (`independent_human_review: pending`); ślepy zestaw nowych uchwał (w tym Warszawa); decyzja o wersji `ENGINE_VERSION` silnika ilości
  (zmiana dotyczy rozpoznania zakresu stref w nagłówku klauzuli; kalibracja powtórzona, wersji nie podniesiono).
- Nie zacommitowano niczego, nie utworzono gałęzi ani PR (zgodnie z ustaleniem: tylko na wyraźne polecenie).
