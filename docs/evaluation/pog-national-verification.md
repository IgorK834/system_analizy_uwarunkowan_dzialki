# POG dla całej Polski — odbiór (ADR-026)

Data: 2026-10-13/14. Zakres: import krajowy z GML Rejestru Urbanistycznego, rejestr aktywnych wersji aktów, kafle MVT dla kraju, mapa i analiza, inspektor, pokrycie, okruchy stref.
Decyzje: [ADR-026](../adr/ADR-026-krajowy-stan-pog-rejestr-wersji-aktow.md). Runbook: [pog-import.md](../operations/pog-import.md). Surowe wyniki i zrzuty ekranu: [`results/pog-national/`](results/pog-national/README.md).

## 1. Issue → status → dowód

| Issue | Status | Dowód | Czego brakuje / odstępstwa |
|---|---|---|---|
| **#300** Import POG „na żądanie” i harmonogram (AU-107) | **Wykonane** | `pog-national` (wykaz RU → GML → rejestr; równolegle, wznawialnie, przyrostowo, wycofania), usługa `pog-sync` z blokadą, kolejka `import_jobs` z deduplikacją, import na żądanie z jawną informacją w odpowiedzi; pomiar próbki 28 gmin i **pełny import krajowy** (434 z 435 aktów, 23,2 min, 3,0 GB); testy: `test_national_import_e2e`, `test_pog_job_queue`, `test_pog_on_demand`, `test_pog_sync_loop`, `test_imports_cli_national` | Jedna usługa `pog-sync` zamiast osobnych `worker` i `schedule`; polecenie krajowe to `pog-national` (nie `--all-published`); wersje aktów porównywane tokenem `publishedActId` i `content_hash`, nie `lastUpdate` z CSW; ADR ma numer 026 (nie 016). |
| **#258** Krajowy ciągły import i monitoring (BK-806) | **Częściowo** | Harmonogram `pog-sync` (24 h), odporność na awarię pojedynczych aktów (izolacja, kody przyczyn, wznawianie tym samym poleceniem), procedura wycofania i cofnięcia (`pog-withdraw`, `pog-rollback`, nowe stany), widoczne pokrycie (`/api/v1/coverage/pog`), status `GET /health/imports` (`stale`, opóźnienie, kolejka), `pog-status` | Brak zewnętrznego dashboardu i alertów (powiadomień) — jest endpoint i flaga `stale`, nie ma integracji z systemem alarmowym. |
| **#301** Ekran pokrycia (część POG) | **Częściowo** | `GET /api/v1/coverage/pog` (+ `/commune/{terc}`, `/point`), panel „Pokrycie importu planów ogólnych” (462 gminy w RU, 461 zaimportowanych, 1 czeka; cztery rozłączne stany; ostatnia nieudana próba z przyczyną), przegląd krajowy z granicami aktów w kolorze statusu (z0–z13); testy `test_pog_coverage_api`, `PogCoveragePanel.test`, zrzuty 07, 09, 10 | Brak koloru „brak lokalnych danych” na mapie (nie mamy geometrii granic gmin — niezaimportowana gmina nie ma obiektu do pokolorowania; jest na liście i w wyszukiwarce TERC); „poproś o import” jest automatyczne (analiza zleca import i mówi o tym), bez przycisku; część BDOT10k i wspólny `GET /api/v1/coverage` należą do innej pracy (`/coverage/bdot10k`). |
| **#335** Inspektor POG: etykiety profili, uchwała, daty (AU-309) | **Wykonane** (porównanie ze źródłem zamiast ze stroną referencyjną) | Inspektor: symbol i pełna nazwa strefy, 4 parametry z jednostkami (`null` ≠ 0), **profile jako etykiety słownika (brak `KPT-*` w UI)**, akt, uchwała, Dziennik Urzędowy, daty (wejście w życie, obowiązuje od, wersja aktu od), odnośniki do karty aktu RU i tekstu uchwały (tylko zweryfikowany HTTPS). **5 punktów w Bielsku-Białej** porównanych ze źródłowym GML (`inspector-vs-gml-bielsko-biala.json`): symbol, nazwa, intensywność, wysokość, pow. zabudowy, pow. biologiczna i profile zgodne **5/5**; uchwała XXV/529/2026 z 25.06.2026, „obowiązuje od 15.08.2026” | Porównanie z rejestrplanowogolnych.pl **nie** było wykonywane — serwis jest wyłącznie wzorcem UX/architektury (zakaz użycia jego danych); jego treść („obowiązuje od 15.08.2026, uchwała XXV/529/2026”) zgadza się z danymi urzędowymi RU, które zweryfikowaliśmy. |
| **#303** Strefy-okruchy w wynikach POG (AU-110) | **Wykonane** | `topological_slivers` (udział < 0,5% **i** pole < 1 m², progi w `POG_SLIVER_MIN_PCT` / `POG_SLIVER_MIN_SQM`), flaga w zapisie, nota w raporcie PDF i w UI, ostrzeżenie `POG_TOPOLOGICAL_SLIVERS_OMITTED`, `POG_RESULT_SCHEMA_VERSION` 2.7, ADR-026 pkt 11; testy `test_pog_analyzer`, `test_report_v2`, `ResultPanel.test`; na żywo: 9 z 30 działek korpusu ma pominięte okruchy, `246101_1.0056.155/3` → 1 okruch 0,11 m² obok SK 100% | Dane RU zmieniły się od audytu (SW 0,10 m² + SU 0,08 m² → jeden okruch 0,11 m² w wersji aktu 20260901T095300); mechanizm ten sam. |

## 2. Wynik testów i bramek

| Bramka | Wynik |
|---|---|
| Backend w kontenerze jak w CI (kopia repo bez `.env`, `pytest -m 'not docker_cli' --cov=app --cov-fail-under=80`) | **5233 passed, 0 failed, pokrycie 95,29%** (11 min). Moduły nowe/zmienione ≥ 86%: `api/sync_loop` 100%, `sync_status` 100%, `ru_gml_reader` 100%, `pog_map_tables` 100%, `mvt` 99%, `pog_coverage` 99%, `national_import` 92%, `imports/__main__` 90%, `composition` 86% |
| `ruff check .` | czysto |
| `python scripts/check_mypy_baseline.py` | brak nowych zgłoszeń (17 znanych, 17 bieżących) |
| Frontend w obrazie `--target test` | `eslint` czysto; `tsc --noEmit` czysto; **56 plików / 661 testów**; pokrycie **97,97%** instrukcji (90–100% na modułach POG; nowe w `coverage.include`: `pogCoverage`, `PogCoveragePanel`, `usePogTileState`); `next build` OK; `npm audit --omit=dev --audit-level=high`: 0 podatności |
| Test odtwarzający błąd A/B | czerwony przed poprawką (`regression-before-fix.txt`), zielony na trzech ścieżkach: analiza, kafle, inspektor (`test_pog_multi_commune.py`) |
| Testy PostGIS | model wersji i migracja (`test_migration_037*`), pełny/przyrostowy/wznowienie/wycofanie/projekt vs wiążący (`test_national_import_e2e`, `test_pog_multi_commune`), kolejka z deduplikacją (`test_pog_job_queue`), kontrole per akt i zakres cache (`test_pog_per_act_controls`), cache stanu (`test_pog_state_cache`), **end-to-end ≥ 2 gminy → kafle → `/analyze` → zapis → cache → PDF → ZIP** (`test_pog_national_analysis_e2e`) |

Błędy znalezione dzięki testom i żywemu przebiegowi (wszystkie naprawione i objęte testem): wyścig przy tworzeniu wiersza `data_sources` przy równoległych publikacjach; BOM UTF-8 w pliku GML RU (Jejkowice) odrzucany jako „nie XML”;
`'{{}}'::jsonb` po usunięciu przedrostka `f` przez autopoprawkę lintera; stale cache numerów stanów między bazami testów; sprawdzenie kompletności tabel mapy trwające > 1 s na stanie krajowym; komunikaty inspektora twierdzące „brak obszarów OUZ”
przy zoomie, na którym warstwa nie jest jeszcze w kaflach.

## 3. Żywy import (RU, 2026-10-13)

Dane: [`results/pog-national/live/`](results/pog-national/live/).

| Miara | Wynik |
|---|---|
| Próbka: 28 gmin (30 aktów; 9 wskazanych — Bielsko-Biała, Legnica, Sopot, Poznań, Gdańsk, Jejkowice, Sejny, Słupsk, Barczewo — i 19 dobranych po 1–2 na województwo), współbieżność 3 | 154 s, 225 MB GML, 35 żądań do RU, 0 × HTTP 429; 29 z 30 aktów (Jejkowice odrzucone przez BOM — poprawione, wznowienie 7,7 s) |
| Ekstrapolacja z próbki na 465 aktów | ok. 40 min, 3,6 GB GML, baza ok. 4,5 GB |
| **Pełny import** (435 aktów ponad próbkę) | **1392 s (23,2 min)**, **2996 MB** pobrane, 440 żądań do RU, 0 × HTTP 429, 434 z 435 aktów; stan krajowy #464 |
| Wynik | 464 aktywne akty w 461 gminach (327 `binding`, 112 `in_progress`, 25 `project`), 518 338 obiektów planistycznych, 441 537 stref i 76 801 obszarów OUZ/OZS/OSDIS w tabelach mapy |
| Akt odrzucony | Bolków, akt 3438: `PL.ZIPPZP.11945/020502-POG/1POG-51SU` ma `minUdzialPowierzchniBiologicznieCzynnej` = 150 → `qa_rejected` (wada danych źródła); gmina w pokryciu „plan w RU — jeszcze niezaimportowany” z przyczyną |
| Rozmiar | baza **3,96 GB** (`planning_features` 2,7 GB, `pog_map_zones` 0,8 GB, `pog_map_overlays` 0,4 GB), wolumen artefaktów **3,0 GB**, `GET /state` 112 KB |
| Przebieg przyrostowy bez zmian | **10,2 s** (5 stron wykazu, 464 akty bez pobierania, ponowienie jednego aktu) |
| Wycofanie i wznowienie | `test_national_import_e2e` na realnych plikach (wycofanie jednego aktu, stary stan odtwarzalny; przerwanie i ponowienie; błąd przejściowy) |

## 4. Pomiary wydajności (stan krajowy #464, edycja `binding`, 250 kafli na zoom, limit API respektowany)

| Zoom | Zimne p50 / p95 [ms] (niepuste) | Rozmiar p50 / p95 / maks [KB] | Ciepłe p95 [ms] | Puste kafle |
|---|---:|---:|---:|---:|
| z6 | 3,8 / 13,1 (6 kafli) | 12,2 / 36,5 / 36,5 | 9,2 | 0 |
| z8 | 9,8 / 13,7 | 2,8 / 7,5 / 8,9 | 12,6 | 1 |
| z10 | 13,6 / 19,1 | 1,0 / 2,6 / 3,8 | 13,2 | 45 |
| **z11** | 28,6 / **56,6** | 28,6 / 136,3 / **184,8** | 11,6 | 65 |
| z12 | 25,5 / 56,4 | 11,4 / 68,8 / 127,4 | 11,1 | 99 |
| z13 | 24,9 / 40,0 | 10,3 / 37,7 / 80,5 | 11,1 | 111 |
| **z14** | 22,6 / **30,3** | 3,5 / 13,7 / 32,7 | 11,6 | 101 |
| z16 | 22,0 / 28,1 | 1,4 / 5,4 / 9,2 | 12,3 | 127 |

Rozmiary w tabeli w KiB (1024 B); największy kafel całego pomiaru ma 198 888 B (edycja `project`, z11). Wszystkie odpowiedzi poprawne (0 błędów), `ETag` → `304` dla 100% powtórzeń, **0 kafli > 500 KB**. Edycja `project`: z11 zimne p95 57 ms, maks. 194 KB. Pomiar przez zapętlenie Docker Desktop (host → kontener), więc
wartości zawierają kilka ms narzutu sieci. Metadane stanu: zimne 1,6 s (raz na proces i stan), ciepłe 3–7 ms.

| Krok analizy | Wynik |
|---|---|
| Lokalna analiza POG działki (120 działek z losowych stref wszystkich aktów) | p50 **51 ms**, p95 **83 ms**, maks. 127 ms (poprzednio discovery RU: 4–5 żądań HTTP do RU na analizę, rząd sekund) |
| Live smoke korpusu referencyjnego, 30 działek, `POST /analyze` (zob. §5) | 5xx: **0**; brak odpowiedzi: 0; p50 21,0 s / p95 62,2 s (krok lokalnego POG: 51–83 ms) |

## 5. Live smoke korpusu referencyjnego (30 działek) na stanie krajowym

Pliki: `results/pog-national/live/live-smoke-national.{md,json}` (przebieg 1) i `live-smoke-national-final.{md,json}` (przebieg końcowy na kodzie finalnym). Wynik przebiegu 1 (2026-10-13 20:07 UTC):

- HTTP 200 dla 30/30, **0 × 5xx**, brak odpowiedzi 0.
- **Działki w gminach z wiążącym POG w RU (Bielsko-Biała ×7, Legnica ×5): 12/12 ma strefy POG, `legal_status = binding`, pokrycie `available`** (poprzednio 0/12: pusty import). Pozostałe 18 działek (Warszawa, Kraków, 281603, 022104, 281604 — brak aktu w wykazie RU)
  idzie ścieżką discovery z jawnymi ostrzeżeniami (`POG_DISCOVERY_ONLY`, `POG_NOT_FOUND_IN_SOURCE`), nigdy „brak planu”.
- **`complete`: 0/30** (tak samo jak 2026-10-12 i 2026-10-13). Rozkład sprawdzonych sekcji w przebiegu 1: 2 z 10 ×2, 3 ×7, 4 ×10, 5 ×4, 6 ×7. Powody braku `complete` **nie dotyczą już POG** dla 12 działek z lokalnym stanem: brakują MPZP (27 z 30; KIMPZP
  niedostępny w 16 przypadkach), droga i zabudowa (baza z tego przebiegu nie ma importu BDOT10k — `no_coverage` 30/30, w przebiegu z 2026-10-13 BDOT był zasilony) oraz relacja MPZP–POG (30/30).
- Czasy `POST /analyze` (liczone przez raport smoke): przebieg 1 p50 22,4 s / p95 57,0 s; **przebieg końcowy** (kod finalny, osobny przebieg) p50 **21,0 s** / p95 **62,2 s** / maks. 82,2 s; poprzedni przebieg (2026-10-13 13:34, bez lokalnego POG,
  z zasilonym BDOT10k) p50 12,2 s / p95 45,3 s. Spowolnienie dotyczy **obu** grup: 12 działek z lokalnym POG p50 11,9 → 15,7 s (+3,8 s), 18 pozostałych (nietknięte zmianą) 12,5 → 24,2 s (+11,6 s); w przebiegu końcowym 11 z 30 odpowiedzi miało
  `MPZP_DISCOVERY_UNAVAILABLE`, a cztery najwolniejsze analizy (50–82 s) dotyczyły działek z Krakowa (TERC 126105), bez lokalnego POG. Sam krok lokalnego POG to 51–83 ms, więc nie jest on źródłem tej regresji. Najbardziej prawdopodobna
  przyczyna to czas odpowiedzi usług zewnętrznych w chwili pomiaru (KIMPZP/MPZP discovery). To hipoteza: nie powtórzono pomiaru w identycznych warunkach i nie ma tu profilu czasu per usługa.
- Przebieg końcowy: 30/30 HTTP 200, **0 × 5xx**, `complete` 0/30, **12/12 działek w gminach z wiążącym POG w RU ma strefy POG (`available`)**, 9 działek z ostrzeżeniem o pominiętych okruchach stref; rozkład sprawdzonych sekcji: 5 z 10 ×12 (11 z nich to działki z lokalnym POG, 1 to działka krakowska bez niego),
  3 z 10 ×10, 4 z 10 ×6, 2 z 10 ×1, 6 z 10 ×1 (działka z lokalnym POG).

## 6. Przeglądarka (wersja produkcyjna MapLibre, Docker, `http://localhost:13000`)

Zrzuty: [`results/pog-national/screenshots/`](results/pog-national/screenshots/). Sprawdzone przy 1440×900 i 375×812 (bez przewijania poziomego, `scrollWidth = innerWidth`), konsola bez błędów:

| Zrzut | Co pokazuje |
|---|---|
| 01 | z6: przegląd krajowy, granice gmin w kolorze statusu aktu (zielony — obowiązujący, pomarańczowy — w opracowaniu, żółty — projekt); stan warstwy „dane niepełne: 26 z 464 aktów w widoku…” |
| 02, 03, 04 | z17 (działka `246101_1.0056.155/3`, strefa SK, obszar OZS, symbole obszarów od z16), z15 i z14 (symbole stref od z14) |
| 05, 06 | z12 (strefy i OUZ) i z11 (strefy całej gminy Bielsko-Biała) |
| 07, 08 | z9: granica gminy w kolorze statusu (strefy poniżej z11 ukryte) i inspektor granicy aktu (status, uchwała XXV/529/2026, daty, „Przybliż mapę (od 11)”) |
| 09, 10 | panel pokrycia: Bolków „plan w RU — jeszcze niezaimportowany” z przyczyną odrzucenia; sprawdzenie TERC 146501 (Warszawa): „brak aktu POG w wykazie RU — to nie dowodzi braku planu” |
| 11, 12, 13 | przełącznik „tylko projekty”, „tylko obowiązujące” i tryb „Maksymalna wysokość zabudowy” z legendą |
| 14, 15 | Legnica: motyw wysokości na z17 i inspektor strefy 42SP (parametry z jednostkami, profile jako etykiety, uchwała „DZ. URZ. WOJ. 2026.2727 z dnia 10.06.2026”, Dziennik Urzędowy, daty, karta aktu RU, tekst uchwały) |
| 16 | 375×812: panele ułożone w kolumnę, przegląd krajowy z granicami aktów |
| 18 | Bielsko-Biała, inspektor strefy 27SW na wersji końcowej: parametry z jednostkami, profile jako etykiety („teren usług”…), `KPT-` nie występuje nigdzie w UI (sprawdzone `document.body.innerText`), uchwała XXV/529/2026 z 25.06.2026, „obowiązuje od 15.08.2026” |
| 17 | Działka w Warszawie (gmina bez aktu w RU): status prawny „nieustalony”, „zakres danych nieustalony”, „brak geometrii lub pusta odpowiedź nie oznacza braku planu”; panel mapy: „widok poza zasięgiem zaimportowanych gmin” |

Link z inspektora prowadzi do `rejestr-urbanistyczny.gov.pl` (karta aktu) i do tekstu uchwały; na stronie nie ma żadnego odnośnika do rejestrplanowogolnych.pl.

PDF i ZIP dla `246101_1.0056.155/3` z żywego stanu: `report-246101_1.0056.155-3.pdf` (182 KB, 10 sekcji) i `audit-246101_1.0056.155-3.zip` (31 KB, 10 plików, `verify_audit_package.py`: „OK: 10 plików zgodnych z manifest.json”;
`analysis.json` zawiera `registry_state_id` = 464, `pinned_act_versions` i `topological_slivers`).

## 7. Odstępstwa od zakresu zadania

- **Brak widoku 3D zabudowy (z13)** — wzorzec UX go ma, tu nie zaimplementowano (brak danych o wysokości wektorowych budynków w stanie POG; to osobne zadanie).
- **Brak kafli granic gmin i powiatów** — przegląd krajowy używa granic aktów; brak ortofotomapy i wyszukiwarki działek na mapie POG.
- **Porównanie z serwisem referencyjnym nie było wykonywane** (zakaz użycia jego danych); parytet pól inspektora zweryfikowano ze źródłowym GML RU (5/5).
- Kontekst: `complete` nadal 0/30 — blokują MPZP i BDOT10k, nie POG.
- Przeglądarka wbudowana: schowek systemowy nie jest dostępny, więc wartości wpisywano przez zdarzenia klawiatury; okno przeglądarki zamykało się po kilku minutach bezczynności — sesja była otwierana ponownie.

## 8. Dane pobrane i pozostałości

Pobrano z `rejestr-urbanistyczny.gov.pl` **3,2 GB** GML (225 MB próbka + 2996 MB import krajowy; to 3,0 GiB na dysku) do wolumenu `pognat_import_artifacts`. Baza krajowa: projekt Compose `pognat`, wolumen `pognat_postgres_data` (3,96 GB),
`pognat_map_tile_cache`. Instrukcja usunięcia: `docker compose -p pognat down -v` (zob. raport końcowy).
