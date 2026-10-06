# Odbiór BK-404, BK-405 i BK-406

Data odbioru: 29 września 2026 r. Punkt wyjścia: `main` @ `0486b4c`
(audyt zadań odnosił się do `8418bdd`; BK-401–403 i BK-104/106 są już w `main`).

- **BK-404** (Task 5.4, P0) — inspektor obiektu POG bez uruchamiania analizy.
- **BK-405** (Task 5.5, P1) — trwałe agregaty powierzchni stref aktu/gminy.
- **BK-406** (Task 5.6, P0) — stan warstwy, projekt vs akt wiążący, brak pokrycia.

Decyzje: `docs/adr/ADR-009-pog-inspector-area-summaries-layer-state.md`.
Artefakty dowodowe: `docs/evaluation/results/bk-404-406/`.

## Zakres zmian

| Warstwa | BK-404 | BK-405 | BK-406 |
|---|---|---|---|
| Migracja | — | `024_pog_area_summaries` (po `023`): `pog_area_summaries`, `pog_area_summary_zones`, CHECK „brak mianownika ≠ komplet” | — |
| Model | — | `models/versioned.py`: `PogAreaSummary`, `PogAreaSummaryZone` | — |
| Domena | `planning/domain/pog_inspector.py` (identyfikator, szczegóły, ETag) | `imports/domain/pog_aggregates.py` (udziały, tolerancje, `is_complete`, priorytet aktów); `planning/domain/pog_area_summary.py` (kontrakt zapytania) | `planning/domain/pog_tiles.py`: `PogCoverageArea` |
| Aplikacja / infrastruktura | `planning/application/pog_release_queries.py`, `planning/infrastructure/pog_release_queries.py` | `imports/infrastructure/pog_aggregates.py` (PostGIS 2180) wołane z `repository.publish_pog` przed aktywacją; `pog_import.py` — statystyki i ostrzeżenia | `planning/infrastructure/mvt.py`: `coverage_areas` w metadanych wydania |
| API (`routers/map_tiles.py`) | `GET …/releases/{id}/features/{feature_id:path}` (ETag/304, 404/409/422) | `GET …/releases/{id}/summary?act_id=` \| `?teryt=&edition=` (ETag/304, 404/422) | `coverage_areas[]` w `GET …/releases/active` i `…/{id}` |
| Styl | — | — | `shared/pog-presentation.json` `2026.09.29-1`: `pattern` + `badge` statusów (projekt: `horizontal-lines`, „projekt / dane niewiążące”); walidacja w `pog_presentation.py` i `pogZones.ts` |
| Frontend | `components/PogFeatureInspector.tsx` (nowy), `MapView.tsx` (`onPogInspect`), `lib/pogLayers.ts` (`pogInspectorHits`, dedup), `lib/api.ts`, `lib/types.ts`, `app/page.tsx` (klik → inspektor, przycisk → analiza) | `components/PogAreaSummary.tsx` (nowy; wykres SVG + tabela z `summaryRows`) | `lib/layerState.ts`, `lib/pogLayerState.ts`, `hooks/usePogTileActivity.ts`, `components/PogLayerStatus.tsx` (nowe); `usePogTileRelease` (ponowienie, `stale`), `PogMapPanel`, `LayerToggle` (`state`), `PreviewOverlays` (stan WMS), `LayerAvailabilityNote` (legenda stanów), `pogThemes`/`pogLayers` (warstwa wzoru projektu), `PogLegend` |
| Testy | `PogFeatureInspector.test.tsx` (nowy), `MapView.test.tsx`, `pogLayers.test.ts`, `page.test.tsx`, `pogMap.integration.test.tsx`, `api.test.ts`; `test_map_tiles.py` (szczegóły, ETag, 404/422, null ≠ 0) | `backend/tests/test_pog_aggregates.py` (nowy), `PogAreaSummary.test.tsx` (nowy) | `pogLayerState.test.ts`, `usePogTileActivity.test.tsx` (nowe), `usePogTileRelease.test.tsx`, `PogMapPanel.test.tsx`, `LayerToggle.test.tsx`, `PreviewOverlays.test.tsx`, `pogZones.test.ts`, `PogLegend.test.tsx`, `test_pog_presentation_contract.py` |

Konfiguracja: bez nowych zmiennych środowiskowych (`Cache-Control` nowych
endpointów używa `POG_TILE_BROWSER_TTL_SECONDS`). Coverage: nowe moduły
dopisane do `coverage.include` w `frontend/vitest.config.ts` (także `app/page.tsx`).

## Kryteria akceptacji

### BK-404

| Kryterium | Dowód |
|---|---|
| Kliknięcie obiektu nie wysyła `POST /analyze`; dopiero przycisk wykonuje dokładnie jedno żądanie z właściwym punktem | `pogMap.integration.test.tsx` „BK-404: klik obiektu…” (prawdziwe `page.tsx` + `MapView` + inspektor: `analyzeParcel` 0× po kliknięciu i po otwarciu struktury stref, 1× `{method:"map", lon:18.538, lat:54.456}` po przycisku); `page.test.tsx`; `PogFeatureInspector.test.tsx` („dopiero przycisk…”, kliknięcia nie propagują do rodzica, blokada podczas analizy); odbiór UI: `performance` — 0 żądań `/analyze` po kliknięciach mapy |
| Dwa nakładające się obiekty oraz OUZ/OZS/OSDIS widoczne, bez duplikatów kaflowych i z tym samym release | `pogLayers.test.ts` (dedup po id MVT/`feature_id`, kolejność strefy → nakładki), `PogFeatureInspector.test.tsx` („dwie nakładające się strefy…”: 8 cech z kafli → 2 strefy + 3 nakładki, wszystkie `#42`), integracja strony; odbiór UI na realnych danych: MapLibre zwrócił cechę SJ dwukrotnie (dwa kafle), inspektor pokazał SJ (projekt) + SU (obowiązuje) + OUZ/OZS/OSDIS, wszystkie z wydania #1 |
| Loading/error/no_coverage mają odrębne komunikaty; parametry null nie są zerowane | `pogLayerState.test.ts` („inspectorEmptyMessage…”: 6 rodzajów, żaden tekst nie opisuje dwóch rodzajów), `PogFeatureInspector.test.tsx` (tabela 6 przypadków pustego punktu, stan szczegółów ładowanie ≠ błąd), `null` → „brak wartości w danych”, `0` → „0%” (inspektor, integracja, backend `test_pog_feature_details_keep_null_and_describe_overlays`: `max_building_height_m: null`, `max_building_coverage_pct: 0.0`) |
| Szczegóły spoza kafla z wersjonowanego endpointu | `test_pog_feature_details_match_tile_and_analysis` (PostGIS + HTTP, realny akt Sopotu: parametry = kafel = analiza, nazwy profili, `legal_status_code` legalForce, ETag/304, zapis `planning_feature:<pk>`), `test_pog_feature_details_errors` (404/422), `http-contract.txt` |
| Focus/Escape/czytnik ekranu | `PogFeatureInspector.test.tsx` („focus trafia na nagłówek, Escape zamyka i przywraca focus”, `region` z nazwą); odbiór UI: po kliknięciu focus na `H2`, `Escape` → focus na `maplibregl-canvas` |

### BK-405

| Kryterium | Dowód |
|---|---|
| Akt 1 km² 0,6/0,4 → 60/40%; luka 0,1 km² → `is_complete=false` i jawny mianownik | `test_one_km2_act_split_sixty_forty_via_import_and_http`, `test_gap_of_point_one_km2_is_persisted_as_incomplete_with_denominator` (PostGIS: prawdziwy `run_pog_import` → `publish_pog` → HTTP), testy domenowe; brak granicy → `share_pct = null`, nie 100% (`test_act_without_source_boundary_has_null_denominator_not_hundred_percent`, CHECK w bazie `test_null_denominator_cannot_be_stored_as_complete`); żywy backend: akt syntetyczny `SW 60,0 / SU 40,0`, mianownik 1,0 km², `is_complete: true` (`http-contract.txt`) |
| Odczyt gotowych agregatów bez `ST_Intersection` podczas HTTP | `test_http_reads_ready_aggregates_without_spatial_functions` — listener `before_cursor_execute` na silniku: SQL odpowiedzi czyta `pog_area_summaries`, 0 wystąpień `st_intersection`, `st_area`, `st_union`, `st_difference`, `st_intersects` |
| Przełączenie release atomowo przełącza agregaty; wykres i tabela pokazują identyczne liczby | `test_release_switch_atomically_switches_aggregates` (A=60/40, B=70/30; A nadal odtwarza własne agregaty; awaria obliczenia agregatów wycofuje publikację C — aktywne zostaje B, brak wydania bez agregatów); `test_reimport_of_identical_artifact_recomputes_without_duplicates`; `PogAreaSummary.test.tsx` (napisy słupków = kolumna tabeli dla 60/40, luki i braku mianownika), integracja strony (`60,0%`, `40,0%` w obu widokach) |
| Gmina bez dublowania nakładających się aktów | `test_municipality_summary_does_not_double_count_overlapping_acts` (dwa akty 1 km² nachodzące na 0,5 km² → mianownik 1,5 km², `deduplicated_area_sqm` 0,5 km², SU 1,0 / SW 0,5 km², suma 100%; projekt w osobnej edycji) |
| Dane częściowe zawsze z oznaczeniem | `PogAreaSummary.test.tsx` („Dane niepełne.” + przyczyny), ostrzeżenia importu `pog_aggregate_incomplete:<akt>:<przyczyny>`, `import_runs.stats.area_summaries_incomplete` (`demo-import.json`: realny akt Sopotu z jedną strefą w granicy całej gminy → `missing_area`) |

### BK-406

| Kryterium | Dowód |
|---|---|
| Testy stanów obejmują wszystkie 6 `LayerState` i kombinacje z project/binding/unknown | `pogLayerState.test.ts` — 18 przypadków (6 stanów × 3 statusy wydania): stan, komunikat, obecność `#release`, plakietka zależna tylko od statusu (projekt → „projekt / dane niewiążące”, unknown → „status nieustalony”, binding → brak) |
| Przy awarii/niepełnym pokryciu nigdzie nie ma „brak planu” | ten sam test (wszystkie teksty), `PogFeatureInspector.test.tsx`, `LayerToggle.test.tsx`, `PreviewOverlays.test.tsx` (legenda stanów), `pogZones.test.ts` i `test_project_style_differs_by_colour_pattern_and_text` (artefakt stylu), integracja (`document.body` po awarii i stale) |
| E2E: przełączenie edycji i awaria kafla; plakietka projektu i data stale widoczne | `pogMap.integration.test.tsx` „BK-406: edycja klawiaturą, awaria kafla i nieudane ponowienie…” (strzałki w grupie radiowej → `setFilter`; zdarzenie `error` źródła → „dane niepełne”; ponowienie z 503 metadanych → „dane nieaktualne” z datą danych i chwilą potwierdzenia, plakietka nadal widoczna, źródło mapy nieusunięte, `refreshTiles`); odbiór UI (niżej) |
| Projekt: kolor + wzór + tekst; stan z metadanych, nie z pikseli | warstwa `pog-zones-status-pattern` (`pogLayers.test.ts`), `PogLegend.test.tsx`, pokrycie z `coverage_areas` (`test_pog_release_metadata_describes_coverage_per_act`, `pogLayerState.test.ts` „pusty kafel w zasięgu to nie no_coverage”) |

## Polecenia i wyniki

Backend — jak `.github/workflows/ci.yml`. Lokalny `.env` użytkownika nie może
trafić do przebiegu (CI robi `rm -f .env`), dlatego Compose dostaje
`--env-file /dev/null`, a `/repo` to kopia drzewa bez plików ignorowanych
(`git ls-files -co --exclude-standard`):

```bash
docker compose build backend
docker compose --env-file /dev/null run --rm -v "$REPO_COPY:/repo:ro" -e REPO_ROOT=/repo backend \
  pytest -m 'not docker_cli' --cov=app --cov-report=term-missing --cov-fail-under=80 -v
```

Wynik: **1739 passed, 0 failed, 3 deselected; pokrycie 92,10%** (próg 80%).
Zmienione moduły: `imports/domain/pog_aggregates.py` 100%,
`imports/infrastructure/pog_aggregates.py` 99%, `planning/domain/pog_area_summary.py`
100%, `planning/domain/pog_inspector.py` 99%, `planning/application/pog_release_queries.py`
98%, `planning/infrastructure/pog_release_queries.py` 100%,
`planning/infrastructure/mvt.py` 100%, `routers/map_tiles.py` 97%,
`imports/infrastructure/repository.py` 93%, `imports/application/pog_import.py` 87%,
`core/pog_presentation.py` 92% (`backend-pytest-summary.txt`). Przebieg z
lokalnym `.env` w drzewie daje 2 niepowodzenia niezwiązane z BK-404–406
(`test_no_env_files_in_repository` wykrywa `.env`, a `RATE_LIMIT_*` z `.env`
zmienia `test_forwarded_for_is_ignored_unless_trusted`).

Testy nowych plików osobno (PostGIS):

```bash
docker compose run --rm backend pytest tests/test_pog_aggregates.py tests/test_map_tiles.py \
  tests/test_pog_presentation_contract.py tests/test_imports_pog.py tests/test_architecture.py
```

Frontend (Node 22.23.3, jak CI):

```bash
docker build --build-context shared=./shared --target test -t dzialki-frontend-test ./frontend
docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 dzialki-frontend-test \
  sh -c "npm run typecheck && npm run test:coverage"
docker compose build frontend   # npm ci + next build (standalone)
```

Wynik: typecheck OK, **353 passed (37 plików), 0 niepowodzeń**; pokrycie
całości 97,15% instrukcji / 90,69% gałęzi / 98,49% linii (progi 80%
niezmienione); nowe moduły:
`pogLayerState.ts` 100% linii, `PogFeatureInspector.tsx` 100% linii, `PogAreaSummary.tsx`
98% linii, `PogLayerStatus.tsx` 100%, `usePogTileActivity.ts` 100% linii
(`frontend-coverage-summary.txt`); `next build` bez błędów.

## Scenariusz końcowy i odbiór UI (przed BK-701)

Automatycznie: `app/pogMap.integration.test.tsx` (prawdziwe `page.tsx`,
`MapView`, `PogMapPanel`, `PogFeatureInspector`, `PogAreaSummary`, adaptery;
atrapą tylko MapLibre i klient HTTP) oraz testy PostGIS + HTTP
(`test_pog_aggregates.py`, `test_map_tiles.py`).

Ręcznie (29.09.2026) na **produkcyjnych obrazach** z Compose (izolowany projekt
`bk404`, osobna baza `dzialki_demo` usunięta po odbiorze): backend `:8000`,
frontend (`node server.js`, standalone) `:3001`. Dane: wydanie #1 zaimportowane
prawdziwym `run_pog_import` (`demo-import.json`) — realny akt APP Sopotu z RU
(strefa 1POG-100SU, OUZ/OZS/OSDIS), syntetyczny projekt ze strefą SJ nachodzącą
na część SU oraz syntetyczny kompletny akt 1 km² (60% SW / 40% SU).

Wyniki szczegółowe: `ui-acceptance.md`.
