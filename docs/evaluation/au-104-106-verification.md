# Odbiór AU-104, AU-105, AU-106 — kompletność analizy, discovery POG przez WFS, import POG (Tasks 22.4–22.6)

Data: 12 października 2026 r. Baza kodu: `fcf6a9b` z **niezacommitowanymi** zmianami (wszystkie trzy zadania razem; nic nie zacommitowano ani nie wypchnięto; nowe pliki są nieśledzone).
Decyzje: [ADR-022](../adr/ADR-022-definicja-complete-i-status-naglowkowy.md) (AU-104), [ADR-023](../adr/ADR-023-discovery-pog-ru-wfs-budzet-zadan.md) (AU-105),
[ADR-024](../adr/ADR-024-import-pog-rownolegly-wznawianie-postep.md) (AU-106); runbook i pomiary importu: [operations/pog-import.md](../operations/pog-import.md).

## Stan zadań: **wykonane; trzy zastrzeżenia opisane niżej**

Przed pracą żadne z trzech zadań nie było wykonane: `complete` wymagał lokalnego wektora MPZP, discovery POG w RU wysyłał 46–288 żądań WMS na analizę, a import POG pobierał strony po kolei, bez postępu i bez wznawiania.
Zastrzeżenia:

1. **AU-104 — kryterium „≥ 1 działka korpusu `complete` albo ADR wskazuje źródło blokujące”: spełniona druga część.** Na korpusie `complete` = **0/30**; blokuje to POG (pokrycie `partial`: discovery wskazuje akt i strefy,
   ale bez odnośnika do APP/GML nie liczy udziałów powierzchniowych). Źródło jest nazwane w ADR-022. `complete` jest natomiast osiągalny od końca do końca (orkiestrator → PostGIS → API → cache → PDF) — test integracyjny.
2. **AU-105 — kryterium „wyniki POG dla 12 działek `binding` identyczne jak przed zmianą” nie jest spełnione dosłownie.** Poprzedni silnik miał błąd osi współrzędnych (odpytywał lustrzane punkty), więc dla 9 działek jego wynik
   `binding` był fałszywy, a dla 5 przeoczył obowiązujący POG. Siedem działek (Bielsko-Biała) jest identycznych w polach kontraktu POG. **Odchylenie wymaga potwierdzenia właściciela** (ADR-023, pkt 1).
3. **AU-106 — próg „≤ 60% stanu wyjściowego” spełniony, ale w najgorszym przebiegu bez dużego zapasu** (Bielsko-Biała 52%; pozostałe 34–49%).

## AU-104 — graduowany status nagłówkowy i nowa definicja `complete` (Task 22.4)

| Kryterium | Stan | Dowód |
|---|---|---|
| `completeness {checked, total, sections[]}` i `headline`; `complete` ⇔ wszystkie sekcje w zakresie sprawdzone; MPZP wystarczy z aktu i dokumentu | spełnione | `services/completeness.py` (reguła `completeness/1.0` w jednym miejscu), `schemas/analyze.py`, `schemas/source.py`; `test_completeness.py` |
| ADR z definicją | spełnione | ADR-022 (numer 022, bo 015 zajmuje ADR-015 z AU-003; w opisie zadania ADR nazywał się `ADR-015-definicja-complete.md`) |
| UI: pasek kompletności zamiast sztywnej plakietki `partial` | spełnione | `components/CompletenessBar.tsx` (+ `CompletenessBar.test.tsx`, 16 testów), `lib/completeness.ts`, fixtures generowane z backendu (`scripts/export_completeness_fixtures.py` → `test/completenessFixtures.ts`) |
| PDF, strona 1: ten sam komunikat | spełnione | `templates/report.html` (komórka `data-completeness`, tabela `data-table="completeness"`), layout `report-v2/2026.10.12-1`; `test_report_completeness.py`; **żywo**: tekst strony 1 PDF działki `246101_1.0056.155/3` zawiera wiersz „Kompletność analizy” z tym samym komunikatem co API |
| Wersje kontraktu podniesione; migracja | spełnione | `COMPLETENESS_RESULT_SCHEMA_VERSION` 1.0 w `RESULT_CONTRACT_VERSION`; migracja `035_analysis_completeness` (head; `analyses.completeness` JSONB, `result_contract_version` poszerzone 64 → 128 znaków); eksporter `audit-exporter/1.4.0`; `test_migration_035.py` |
| `docs/report/field-mapping.md` odświeżony | spełnione | wygenerowany w obrazie backendu (693 ścieżki); test zgodności z kodem zielony |
| Macierz warunków `complete` | spełnione | `test_completeness.py` (sekcje × stany: `checked`/`missing`/`out_of_scope`, `no_match`, `document_candidate`, ręczna weryfikacja, konflikt parametrów, niejednoznaczny status, degradacja LLM), `test_completeness_persistence.py` (zapis, odczyt `stored`/`reconstructed`, cache) |
| Od końca do końca | spełnione | `test_completeness_e2e.py`: strefa z aktu i dokumentu + POG z wektorami + ISOK, GDOŚ i NMT → `complete`; niedostępne pochodne rastra NMT → `partial` z nazwanym brakiem; ten sam komunikat po odczycie z bazy, z cache i w PDF |
| ≥ 1 działka korpusu `complete` w live smoke **albo** ADR wskazuje źródło blokujące | **spełnione drugą częścią** | live smoke 2026-10-12: `complete` = 0/30; ADR-022 („Wynik na korpusie…”): blokuje POG; kolejne blokady zewnętrzne i zmienne (KIMPZP, WCS NMT) |
| `docs/current_state.md`, README | spełnione | sekcja AU-104, krok 5/6 przeglądu, wiersz „Status analizy” w README |
| ≥ 80% pokrycia | spełnione | zob. „Weryfikacja” |

## AU-105 — discovery POG w RU: ≤ 6 żądań HTTP na analizę (Task 22.5)

| Kryterium | Stan | Dowód |
|---|---|---|
| ≤ 6 żądań HTTP do RU na analizę, także przy 2 i 6 punktach próbki; jedno zapytanie WFS na typ/kafel zamiast pętli punkt × warstwa | spełnione | `services/pog._discover_from_catalog` → `RuPogDiscovery` (cztery typy: akt, OZS, OUZ, strefy; kafel 50 m); `test_pog_ru_discovery.py::test_every_corpus_parcel_stays_within_the_request_budget` (30 działek), `…request_count_does_not_depend_on_the_number_of_sample_points`, `…multipart_parcel_with_many_sample_points…`. **Żywo (30 działek):** 27 × 4 i 3 × 5 żądań = **123** (poprzednio 4 967), 0 żądań WMS discovery; maks. 5 ≤ 6 |
| Cache zapytań RU per (typ, kafel bbox), TTL 24 h; deduplikacja | spełnione | `CachedRuPogFeaturesSource`; `test_ru_pog_cache.py` (trafienie, wygaśnięcie, LRU, jeden lider przy równoległych analizach, błąd lidera nie jest cache'owany, błąd źródła zapamiętany 60 s), `test_pog_ru_discovery.py::test_parallel_analyses_of_one_parcel_share_the_queries` |
| Test budżetu z licznikiem `respx` i alarm w metrykach | spełnione | budżet `RU_POG_REQUEST_BUDGET` = 6 egzekwowany w `OgcClient(before_request=…)` także dla ponowień po 5xx (`…retries_after_5xx_consume_the_budget…`); po wyczerpaniu warstwy są `unavailable` (nie „brak”), licznik `ru_pog.budget_exceeded` w `/health/upstream` (`…exhausted_budget_makes_layers_unavailable_and_raises_the_alarm`) |
| Wyniki POG dla 12 działek `binding` identyczne jak przed zmianą | **częściowo** | `test_pog_discovery_baseline.py` z zamrożonym stanem „przed” (`fixtures/pog_discovery/legacy_baseline.json`) i niezależnym parserem prawdy terenowej: **7 identycznych `binding`** (Bielsko-Biała; pola kontraktu POG bez zmian), **9 fałszywych `binding` poprzedniego silnika → `unknown`** (Kraków i gmina 281603: RU nie ma tam żadnych obiektów POG), **5 przeoczonych → `binding`** (Legnica). Przyczyna: poprzedni klient wysyłał `bbox` WMS dla `EPSG:2180` jako wschód,północ, a RU czyta północ,wschód |
| Poprawność geometrii w odpowiedzi | spełnione | `ru_pog_features.py`: oś północ,wschód w `bbox` i `posList`; geometria przycinana do kafla; obiekt z obwiednią w kaflu, ale wielokątem poza nim, nie jest błędem (regresja wykryta w live smoke: fałszywe `unavailable` warstwy OUZ — `test_object_whose_envelope_hits_the_tile_but_polygon_does_not_is_not_a_failure`) |
| ≥ 80% pokrycia | spełnione | zob. „Weryfikacja” |

## AU-106 — import POG: postęp, równoległość, wznawianie (Task 22.6)

| Kryterium | Stan | Dowód |
|---|---|---|
| Postęp na stderr co ≤ 10 s oraz końcowy JSON na stdout | spełnione | `application/progress.py` (`HeartbeatReporter`, domyślnie 10 s, także bez nowych stron); `test_import_progress.py::test_default_interval_meets_the_ten_second_contract`, `test_heartbeat_prints_at_most_every_interval_and_also_without_new_pages`; `test_imports_cli_pog.py::test_successful_import_prints_json_on_stdout_and_progress_on_stderr`; **żywo**: 8–20 linii postępu na import |
| Równoległe pobieranie stron (np. 4) | spełnione | `OgcClient.fetch_wfs(…, concurrency=N)`, `IMPORT_WFS_CONCURRENCY` = 4, `--concurrency` 1–8; `test_ogc_client_paged.py` (kolejność, powtórzona strona, limity, pierwszy błąd jako przyczyna), `test_pog_import_resume.py::test_parallel_download_is_much_faster_than_sequential_under_the_same_latency` |
| Checkpoint w `import_runs.checkpoint` i wznawianie | spełnione | `infrastructure/run_checkpoint.py` (osobna transakcja, `pog-download-v1`: `running|interrupted|failed|fetched|succeeded|abandoned`), `page_store.py` (SHA-256, token zapytania); `test_import_run_checkpoint.py` (integracja z bazą), `test_page_store.py` |
| Kod wyjścia ≠ 0 przy niepełnym imporcie | spełnione | `0` ukończony, `1` błąd/odrzucony QA, `2` użycie, **`3` niepełny** (`interrupted`, `incomplete`); `test_imports_cli_pog.py` (kody, SIGINT/SIGTERM, drugi sygnał) |
| Przerwany i wznowiony import daje ten sam hash artefaktu i to samo wydanie | spełnione | artefakt bez atrybutów zmiennych (`timeStamp`, `next`); `test_pog_import_resume.py::test_resumed_import_gives_the_same_artifact_hash_and_the_same_records`, `…artifact_hash_is_stable_across_independent_downloads`, `…cancel_interrupts_the_import_marks_the_checkpoint_and_allows_a_later_resume` |
| Pomiar czasu: Bielsko-Biała i 2 inne gminy w `docs/operations/`, ≤ 60% stanu wyjściowego | spełnione | żywy RU, `--dry-run`, stary i nowy kod naprzemiennie ([`import-timing.json`](results/au-104-106/import-timing.json), [runbook](../operations/pog-import.md)) — tabela niżej |
| ≥ 80% pokrycia | spełnione | zob. „Weryfikacja” |

Pomiar czasu importu (czas ścienny polecenia, `--dry-run`):

| Gmina | Cechy APP | Stan wyjściowy | Po zmianie | Stosunek |
|---|---:|---:|---:|---:|
| Bielsko-Biała | 1397 | 359 s | 187 s (pierwszy przebieg), 123 s (`--fresh`) | 52% / 34% |
| Legnica | 983 | 213 s | 105 s | 49% |
| Sopot | 785 | 205 s | 68 s | 33% |

Równoległość 8 nie dała zysku względem 4 (123 s) — usługa RU skaluje się nieregularnie; w Legnicy 24% czasu to sekwencyjne pobieranie metadanych CSW po pobraniu stron (nie zmierzono jego zrównoleglenia).

## Weryfikacja

Backend (kontener `backend-test` jak w CI: repo tylko do odczytu, kopia bez `.env`, `pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-fail-under=80`):
**4744 passed, 0 failed, 3 deselected (`docker_cli`)**, pokrycie **94,76%** (próg 80%) — przebieg na końcowym drzewie roboczym, już z tą notą (`test_readme_links` zielony w kopii bez `.git`, gdzie sprawdza istnienie plików;
wariant z `git ls-files` wymaga wcześniejszego `git add` nowych plików — zob. „Ustalenia”, pkt 5). Poprzedni pełny przebieg (4741 passed, 1 failed) padł tylko na `test_readme_links`, bo README linkowało do tej noty, zanim powstała.
- `ruff check` (app, tests, scripts, alembic) — czysto; `python scripts/check_mypy_baseline.py` — „brak nowych zgłoszeń” (17 znanych, 17 bieżących).
- Frontend (`docker build --target test` i `npm run lint && npm run typecheck && npm run test:coverage && npm run build`): **51 plików, 597 testów zielonych**, pokrycie instrukcji 97,8%; `eslint`, `tsc` i `next build` zielone.
- **Live smoke** (`python backend/scripts/live_smoke_corpus.py`, korpus referencyjny 30 działek, backend z drzewa roboczego, świeża baza z migracjami do `035`, świeże cache'e):
  **5xx = 0, brak odpowiedzi = 0**, 30 × HTTP 200 (`partial`), p50 22,6 s, p95 50,5 s — [wynik](results/live-smoke/2026-10-12.md). Rozkład sprawdzonych sekcji: 2 z 8 — 1, 3 z 8 — 17, 4 z 8 — 10, 5 z 8 — 2; w ostrzeżeniach nie ma
  `POG_RU_LAYER_UNAVAILABLE` ani kodów geometrii. Dwa wcześniejsze przebiegi tego dnia wykryły: (a) 503 `PERSISTENCE_FAILED` — kontrakt wyniku (71–72 znaki) nie mieścił się w `varchar(64)` (migracja 035 poszerza kolumnę),
  (b) fałszywe `unavailable` warstwy OUZ przy obiekcie z obwiednią w kaflu i wielokątem poza nim (poprawione, z testem regresji).
- **Żywe usługi i interfejs** (Bielsko-Biała `246101_1.0056.155/3`): `partial`, komunikat „4 z 8 sekcji sprawdzono; brak: MPZP, strefy POG, OUZ, OZS, OSDIS, relacja MPZP–POG. Poza zakresem: uzbrojenie terenu, droga i dostęp.”,
  POG `binding` z pokryciem `partial` i polami `matched_wfs_type`; pasek kompletności w przeglądarce (build produkcyjny); strona 1 PDF zawiera ten sam komunikat i tabelę sekcji.

## Ustalenia poza zakresem zadań (do wiadomości)

1. **Zmienność usług zewnętrznych wpływa na rozkład „n z 8”**: WCS NMT („Server disconnected”) niedostępny w 12/30 analiz w jednym przebiegu i 20/30 godzinę później; KIMPZP zgłasza błędy. Liczby w rozkładzie nie są więc
   stałą kodu — stałe są: `complete` = 0/30 (blokuje POG) i 0 × 5xx.
2. **Metadane CSW w imporcie nie są zrównoleglone** (Legnica: 25 s po pobraniu stron) — potencjalna dalsza oszczędność, niezmierzona.
3. **Limity klienta WFS** (`max_pages=500`, `max_features=10 000` na typ; przy stronach po 10 ≤ 5 000 obiektów typu) zakończą import największych gmin jawnym błędem limitu — decyzja o podniesieniu to osobna sprawa.
4. **Reguła `completeness/1.0` zmieniała się w trakcie pracy bez podnoszenia wersji reguły** — nie wydano jeszcze żadnej wersji z wcześniejszą treścią, więc nie ma zapisów do odróżnienia. Od teraz każda zmiana semantyki wymaga `completeness/1.1`.
5. **Nowe pliki są nieśledzone** — `tests/test_readme_links.py` sprawdza linki względem `git ls-files`, więc przed push trzeba dodać do indeksu m.in. ADR-022…024, tę notę, `docs/operations/pog-import.md`, `docs/evaluation/results/au-104-106/` i raporty live smoke.

## Do potwierdzenia przez właściciela

Pełne listy: ADR-022 (definicja zakresu `complete`, `no_match` jako „sprawdzono”, status prawny POG i ręczna weryfikacja bez wpływu na `complete`, NMT w zakresie), ADR-023 (traktowanie fałszywych `binding`
Krakowa i gminy 281603 jako poprawki, próg przecięcia 0,5 m² i kafel 50 m, TTL 24 h, analiza powierzchniowa POG na danych WFS), ADR-024 (równoległość 4, wznawianie domyślnie włączone, kod wyjścia 3 jako kontrakt).
