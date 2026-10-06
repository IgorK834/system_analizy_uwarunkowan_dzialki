# Odbiór BK-401, BK-402 i BK-403

Data odbioru: 28 września 2026 r. Punkt wyjścia: `main` @ `a4599a1`.

- **BK-401** (Task 5.1, P0) — wektorowe kafle MVT POG z aktywnego wydania PostGIS.
- **BK-402** (Task 5.2, P0) — pięć trybów tematycznych POG.
- **BK-403** (Task 5.3, P0) — jedno źródło prawdy dla stylu i legendy.

Decyzje: `docs/adr/ADR-007-pog-vector-tiles-and-shared-presentation.md`.
Artefakty dowodowe: `docs/evaluation/results/bk-401-403/`.

## Zakres zmian

| Warstwa | BK-401 | BK-402 | BK-403 |
|---|---|---|---|
| Domena | `modules/planning/domain/pog_features.py` (wspólna ekstrakcja strefy/parametrów analizy i kafla), `pog_tiles.py` (adres, walidacja, edycje, klucz cache, ETag, atrybuty) | — | — |
| Aplikacja / infrastruktura | `application/pog_tiles.py` (porty, limity, cache HIT/MISS), `infrastructure/mvt.py` (SQL `ST_AsMVT`, LRU), `composition.py` | — | — |
| API | `routers/map_tiles.py`: `/pog/releases/active`, `/pog/releases/{id}`, `/pog/releases/{id}/{z}/{x}/{y}.mvt`; CORS eksponuje `X-Pog-*` | — | — |
| Analiza | `pog_analyzer`/orkiestrator używają `pog_features`; `geometry_geojson` przecięć; `presentation_style` w wyniku | — | wersja + SHA stylu w snapshocie (`POG_RESULT_SCHEMA_VERSION` 2.4) |
| Ustawienia | `POG_TILE_SOURCE_ID`, `POG_TILE_MIN_ZOOM`, `POG_TILE_MAX_ZOOM`, `POG_TILE_MAX_FEATURES`, `POG_TILE_MAX_BYTES`, `POG_TILE_CACHE_MAX_BYTES`, `POG_TILE_BROWSER_TTL_SECONDS`, `POG_TILE_STATEMENT_TIMEOUT_MS` | — | `POG_PRESENTATION_PATH` |
| Config | — | — | `shared/pog-presentation.json`, `app/core/pog_presentation.py`, `report_config.py` (style POG z configu) |
| Frontend | `lib/types.ts`, `lib/api.ts`, `hooks/usePogTileRelease.ts`, `lib/pogLayers.ts`, `MapView.tsx` (źródło raz na wydanie) | `lib/pogThemes.ts`, `PogThemeSelector.tsx`, `PogMapPanel.tsx`, `setPaintProperty`/`setFilter` | `lib/pogZones.ts`, `lib/pogPatterns.ts`, `PogLegend.tsx`, `PogZoneShareChart.tsx` |
| Raport PDF | — | — | miniatura z przecięciami stref (paleta) i OUZ/OZS/OSDIS (wzór + obrys), legenda z wersją stylu, próbki kolorów w tabeli stref |
| Budowanie | — | — | `additional_contexts.shared` (Compose), `--build-context shared=./shared` (CI), `turbopack.root`, `COPY --from=shared` w obu Dockerfile |
| Migracja | brak — istniejące indeksy GiST i kolumny wystarczają | — | brak (nowe pola w JSONB `result_v2`) |

Poprawki wykryte przy realizacji (zmiany obok zakresu, opisane jawnie):

1. **Profile funkcjonalne (BK-105)** — `repository._jsonable` zapisywał dataclassy
   profili jako `repr`, więc ani analiza, ani mapa nie widziały kodów profili.
   Teraz zapis to obiekt JSON; test PostGIS sprawdza kody w analizie i kaflu.
2. **„ł” w nazwach stref** — `normalize_name` nie składał litery „ł”, więc
   urzędowa etykieta „strefa usługowa” dawała `unknown`. Test obejmuje wszystkie
   13 etykiet słownika.
3. **Zgodność z BK-306 (za zgodą użytkownika)** — commit `a4599a1` usunął
   `fetch_kiut_networks`, a `context.py` nadal go importował (29 plików testów
   nie przechodziło zbierania, `app.main` nie startował). Minimalna poprawka:
   `context.py` używa `fetch_kiut_network_section`, błędy KIUT mają status
   `unavailable` z `reason_code`, a blokada guardem katalogu
   (`VECTOR_SOURCE_NOT_CONFIRMED`) nie obniża statusu analizy, bo geometria KIUT
   nie wchodzi do obliczeń. Testy `test_kiut.py`/`test_context.py` dopasowano do
   nowego API; logiki BK-306 nie zmieniano.

## Kryteria akceptacji

### BK-401

| Kryterium | Dowód |
|---|---|
| Dekodowany kafel zawiera pięć warstw i wartości zgodne z analizą tej samej strefy i wydania | `test_pog_tile_has_five_layers_and_same_parameters_as_analysis` (PostGIS + HTTP): import realnych rekordów APP Sopotu (`tests/fixtures/ru`) → `_analyze_pog_local_release` dla działki w strefie 1POG-100SU → `GET …/16/x/y.mvt` → dekoder MVT (`tests/mvt_decoder.py`): 4 parametry, `zone_code`, symbol, etykieta, wersja, profile, status, TERYT i `data_release_id` identyczne; `test_pog_tile_matches_analysis_for_raw_alias_attributes` — to samo dla atrybutów surowych (wielkie litery, „ś”, przecinek dziesiętny); `tile-16-36142-20900.decoded.json` |
| Pusty kafel 200, niepoprawne z/x/y 422, 304, rozdzielenie cache A/B | `test_pog_empty_tile_is_200_with_valid_empty_protobuf`, `test_pog_tile_rejects_invalid_coordinates_with_422` (6 przypadków), `test_pog_tile_rejects_non_integer_path_and_unknown_release`, `test_pog_tile_etag_returns_304_and_cache_hit` (także `W/` i obcy ETag), `test_pog_tile_cache_separates_releases_and_pinned_url_reproduces_release_a` (wydania A/B: różne ETagi i treść, URL A nadal odtwarza A po aktywacji B, edycja w kluczu), `test_pog_tile_limits_return_413`; `http-contract.txt` (żywy backend) |
| Mapa działa na lokalnym wydaniu bez RU; projekt i akt wiążący rozróżnione filtrem/statusem | `test_pog_tile_keeps_null_distinct_from_zero_and_marks_project` (edycje `binding`/`project`), `app/pogMap.integration.test.tsx` (filtr `setFilter`, brak wydania ≠ brak planu), odbiór UI poniżej |
| Brak surowego XML/dużych atrybutów | asercja whitelisty atrybutów i długości ≤ 256 w teście pięciu warstw; `test_raw_attribute_subset_gives_same_presentation_as_full_record` |

### BK-402

| Kryterium | Dowód |
|---|---|
| Po ustabilizowaniu mapy 5 trybów = 0 requestów, bez zmiany source URL, bez setData/addSource | `MapView.test.tsx` „BK-402: pięć trybów…” (spy `fetch`, `addSource` 1×, `setTiles/setData/setUrl` 0×, JSON źródła bez zmian); `app/pogMap.integration.test.tsx` na prawdziwej `page.tsx`; w przeglądarce: `performance.getEntriesByType('resource')` — 0 nowych wpisów po przełączeniu 5 trybów |
| Null, 0 i wartość na każdym progu mają odrębnie sprawdzone style; m, %, bezwymiarowa | `pogThemes.test.ts` (każdy próg, próg − ε, `null`, `undefined`, NaN, 0; `case → has/null → to-number → step`), `PogMapPanel.test.tsx` (0% ≠ „brak wartości”), `test_every_threshold_null_and_zero_have_distinct_classes` (Python) |
| Aktualizacja paint i obsługa klawiaturą | `PogThemeSelector.test.tsx` (Tab + strzałki przez 5 trybów), `MapView.test.tsx`, test integracyjny strony (klik + `{ArrowDown}`); tryb zachowany po remoncie mapy (`MapView.test.tsx`, integracja, przeładowanie strony w UI) |

### BK-403

| Kryterium | Dowód |
|---|---|
| Zmiana jednej wartości configu zmienia legendę i style mapy/raportu; test wykrywa rozjazd | `PogLegend.test.tsx` (legenda = wyrażenie warstwy = JSON dla 5 trybów; przesunięty próg / zmieniony kolor wykryte), `test_changing_one_config_value_changes_report_style`, `test_report_layer_styles_and_legend_come_from_the_same_config`, walidacja artefaktu w obu adapterach (`pogZones.test.ts`, `test_invalid_presentation_is_rejected`) |
| Wszystkie strefy, unknown i null mają etykietę; OUZ/OZS/OSDIS rozróżnialne bez koloru | `test_zone_list_matches_frozen_official_codelist` (13 stref: kod, identyfikator, etykieta, kolejność = słownik urzędowy), `test_codelist_provenance_is_recorded_and_matches_fixture`, `pogZones.test.ts` (3 różne wzory i `dasharray`), `test_report_map_distinguishes_overlays_by_pattern_and_dash`, `report-pog-page.png` |
| Build obu obrazów zawiera tę samą wersję configu; stary raport używa zapisanej wersji | SHA-256 `4049c516…cd4` identyczny w repo, `/app/shared` obrazu backendu i loaderze; `style_version` w bundlu obrazu frontendu (polecenia niżej); `test_both_images_copy_presentation_from_shared_build_context`, `test_backend_loads_the_same_artifact_as_the_repository` (w kontenerze z `REPO_ROOT`); `test_old_report_uses_saved_style_version` (piksel mapy i legenda ze snapshotu `2020.01.01-1`, analiza bez snapshotu → bieżący styl z adnotacją) |

## Polecenia i wyniki

Backend — jak `.github/workflows/ci.yml` (obraz zbudowany z repozytorium, bez `.env`):

```bash
docker compose build backend frontend
docker compose run --rm -v "$PWD:/repo:ro" -e REPO_ROOT=/repo backend \
  pytest -m 'not docker_cli' --cov=app --cov-report=term-missing --cov-fail-under=80 -v
```

Wynik: **1666 passed, 4 failed, 3 deselected; pokrycie 91,55%** (próg 80%
spełniony). Zmienione moduły: `planning/domain/pog_tiles.py` 99%,
`pog_features.py` 98%, `application/pog_tiles.py` 100%, `infrastructure/mvt.py`
100%, `routers/map_tiles.py` 95%, `core/pog_presentation.py` 91%,
`report_config.py` 96%, `report_map.py` 94%, `report.py` 98%, `pog_analyzer.py`
94%, `context.py` 99% (`backend-pytest-summary.txt`).

Cztery niepowodzenia **nie wynikają z BK-401–403**: to testy buforów sieci po
commicie `2a1b2ea` (BK-306, `simulation_only`), który zmienił `geometry.py` bez
aktualizacji testów. Trzy z nich (`test_geometry.py`) odtworzono na czystym
`a4599a1` bez żadnych zmian; czwarty (`test_unavailable_isok_keeps_kiut_and_gdos_results`)
ma tę samą przyczynę (oczekuje redukcji powierzchni przez bufor wody, a orkiestrator
nadal raportuje `affects_buildable_area=True`) i na czystym HEAD nie dawał się
nawet zebrać.

Frontend (Node 22, jak CI):

```bash
docker build --build-context shared=./shared --target test -t dzialki-frontend-test ./frontend
docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 dzialki-frontend-test \
  sh -c "npm run typecheck && npm run test:coverage"
docker compose build frontend   # npm ci + npm run build
```

Wynik: typecheck OK, **284 passed (33 pliki)**, pokrycie całości 98,16% linii /
90,27% gałęzi; nowe moduły 81,8–100% gałęzi i 95,8–100% linii
(`frontend-coverage-summary.txt`); `next build` bez błędów. Nowe moduły dopisane
do `coverage.include`.

Ta sama wersja configu w obu obrazach:

```bash
shasum -a 256 shared/pog-presentation.json
docker run --rm --entrypoint sh system_analizy_uwarunkowan_dzialki-backend -c 'sha256sum /app/shared/pog-presentation.json'
docker run --rm --entrypoint sh system_analizy_uwarunkowan_dzialki-frontend -c 'grep -rl "2026.09.28-1" /app/.next'
```

## Scenariusz końcowy i odbiór UI (przed BK-701)

Automatycznie: `app/pogMap.integration.test.tsx` (prawdziwe `page.tsx`, `MapView`,
`PogMapPanel`, adaptery; atrapą tylko MapLibre i klient HTTP) oraz testy PostGIS
w `test_map_tiles.py`.

Ręcznie (28.09.2026, izolowana baza `dzialki_bk401_demo` usunięta po odbiorze):
realne rekordy APP Sopotu + syntetyczny projekt zaimportowano `run_pog_import`
do wydania #1; backend `uvicorn` :8001, frontend `next dev` :3001, przeglądarka
1440×900.

1. `GET /api/v1/map/pog/releases/active` → wydanie #1, URL kafli
   `/api/v1/map/pog/releases/1/{z}/{x}/{y}.mvt` (`release-active.json`).
2. Po wyłączeniu podglądów WMS widoczne: granica aktu (ciągła), OUZ (ukośne
   kreski, granat, obrys przerywany), OZS (kropki, śliwka), OSDIS (kratka,
   zieleń), strefy wg palety; projekt z obrysem przerywanym i plakietką.
3. Kliknięcie strefy 1POG-100SU → panel „Strefa pod kursorem (z kafla)”:
   0,9 / 90% / 4 m / 5%, „obowiązuje”, wydanie #1. Ta sama akcja uruchomiła
   analizę działki `226401_1.0002.2/66`: tabela POG SU 98,2% — 0,9 / 4 m / 90% / 5%,
   wykres udziałów w kolorze SU (zgodność kafel ↔ analiza).
4. Przełączenie 5 trybów: 0 nowych żądań, URL źródła bez zmian; tryb
   „udział zabudowy”: SU (90%) najciemniejsza klasa, projekt (0%) najjaśniejsza
   klasa — nie wzór „brak wartości”; tryb „wysokość”: projekt bez wysokości ma
   wzór „brak wartości”. Wybrany tryb przetrwał przeładowanie strony.
5. Raport PDF tej analizy: miniatura z przecięciami SU i OUZ/OZS/OSDIS, legenda
   „styl 2026.09.28-1 (4049c516926b)” z opisem wzorów (`report-map.png`,
   `report-pog-page.png`, `report.pdf`).

## Znane ograniczenia i zadania następcze

- **BK-104 (naprawione, ADR-008):** nowe wydanie nie zawierało aktów
  niezmienionych względem poprzedniego, więc po aktywacji analiza i mapa ich nie
  widziały. Teraz akt o niezmienionej treści jest przenoszony do nowego wydania
  (`import_runs.stats.carried_forward`); dowód:
  `test_new_release_is_complete_snapshot_including_unchanged_act`.
- **BK-306:** dokończyć integrację flag `simulation_only`/`affects_buildable_area`
  w orkiestratorze i zaktualizować 4 testy buforów.
- BK-404 (inspektor) i BK-406 (pełne stany warstwy i plakietka) pozostają
  osobnymi zadaniami; panel pokazuje już atrybuty klikniętej strefy z kafla.
