# Odbiór BK-301 i BK-302

Data odbioru: 28 września 2026 r.

- **BK-301** (Task 4.1, P0) — NMT jako pierwszoklasowy wynik analizy.
- **BK-302** (Task 4.2, P1) — spadek, ekspozycja i profil z oficjalnego rastra NMT.

Decyzje: `docs/adr/ADR-006-terrain-result-and-raster-derivatives.md`.
Kontrakty usług: `docs/data_sources/nmt_contracts.md`, katalog `nmt`, `nmt_wcs`.

## Zakres zmian

| Warstwa | BK-301 | BK-302 |
|---|---|---|
| Adapter | `services/nmt.py`: `TerrainNoCoverage` zamiast `None`, provenance i `reason_code` porażek, SHA-256 odpowiedzi | `modules/analysis/infrastructure/terrain_raster.py` (nowy): WCS + GDAL + Shapely |
| Klient OGC (BK-102) | — | `fetch_wcs_description`, `fetch_wcs_coverage`, `OgcExceptionReportError` (odczyt 400/404 z `ExceptionReport`) |
| GDAL | — | `GdalRasterProcessor.read_float_band` (ścisły GeoTIFF, `-if GTiff`, ENVI → `array`), `gdal_version`; minimalne środowisko procesu |
| Domena / aplikacja | — | `modules/analysis/domain/terrain.py`, `modules/analysis/application/terrain.py`, `modules/analysis/composition.py` |
| Kontekst | `ContextSectionResult.reason_code`, provenance porażki | — |
| Mapowanie | `services/terrain.py` (nowy): 4 statusy, odczyt snapshotu, ostrzeżenia, źródła | spójność WCS ↔ GetMinMax (> 1 m) |
| Schematy API | `TerrainResult` (schemat 1.0), `AnalyzeResponse.terrain` | `TerrainReliefResult`, `TerrainSlopeStatistics`, `TerrainSlopeClass`, `TerrainAspectResult`, `TerrainProfileResult`, `TerrainRasterMetadata` |
| ORM / migracja | `Analysis.terrain` JSONB; `022_analysis_terrain`; `result_contract_version` 20 → 64 | (w tym samym snapshocie) |
| Persystencja | zapis/odczyt, rekordy źródeł ze statusem `no_coverage`/`unavailable` | źródło `NMT_WCS` z SHA-256 GeoTIFF |
| Cache | `pog-v2.3+mpzp-v2.1+terrain-v1.0` | — |
| Katalog | — | `access_type: wcs` (+ walidacja `layers`), wpis `nmt_wcs` |
| Ustawienia | — | `terrain_relief_enabled`, `terrain_raster_max_pixels`, `terrain_raster_max_bytes`, `terrain_raster_timeout_seconds` |
| UI | `lib/types.ts`, `lib/terrain.ts`, `components/TerrainCard.tsx` w `ResultPanel` | tabele spadku i klas, ekspozycja, profil SVG, metadane rastra |
| PDF | sekcja „Rzeźba terenu (NMT)” + ograniczenia | „Spadek, ekspozycja i profil (raster NMT)” z SVG i provenance |

## Kryteria akceptacji

### BK-301

| Kryterium | Dowód |
|---|---|
| Hmin 112,3 / Hmax 115,7 → 3,4 m; API, DB, cache, UI i PDF zachowują pomiar i metadane | `test_control_measurement_survives_api_db_cache_and_pdf`: `POST /analyze` → realny adapter NMT na fixture → `analyses.terrain` == odpowiedź API == odczyt z bazy == trafienie cache (NMT nie odpytany ponownie) → `GET /report` z „Deniwelacja (Hmax − Hmin) 3,4 m” i SHA-256; UI: `TerrainCard.test.tsx` („3,4 m”); `e2e/01-api-response.json`, `e2e/02-report.pdf` |
| Brak pokrycia, timeout, stary snapshot i faktyczne 0 m to cztery różne zachowania | `test_no_coverage_timeout_legacy_and_real_zero_are_distinct`: statusy `no_coverage`/`unavailable`/`unknown`/`available` (0,0 m), wysokości `null` poza pomiarem, rekordy źródeł `no_coverage`/`unavailable`, cztery różne HTML raportu (`e2e/03-report-*.html`, `e2e/04-four-behaviours.json`) |
| Wznowienie MPZP nie usuwa terrain | `test_manual_zone_resume_keeps_terrain_snapshot` (`e2e/05-resume.json`) |
| Migracja działa na starej bazie i nie dopisuje zera | `test_old_database_upgrades_without_fake_zero_and_downgrade_roundtrips`: downgrade → wiersz bez kolumny → upgrade → `NULL` → `unknown` → downgrade/upgrade |
| Sentinel i błąd z HTTP 200 nie są „naprawiane” do zera | `tests/test_nmt.py` na zamrożonych odpowiedziach |

### BK-302

| Kryterium | Dowód |
|---|---|
| Płaszczyzna o zadanym gradiencie i raster poziomy = rozwiązanie analityczne | `test_terrain_domain.py` (tolerancja 1e-9 gradientu, 1e-4 statystyk; 5 kierunków) i `test_synthetic_plane_through_gdal_matches_analytic_solution` (GeoTIFF Float32 przez GDAL, 0,01 pp) |
| NoData, zły CRS, za duży raster i timeout bez statystyk ze sztucznych zer | `test_terrain_raster.py`: niezadeklarowane 0.0 → `no_coverage`, `GDAL_NODATA` maskowane, EPSG:4326 → `RASTER_CRS_MISMATCH`, limit pikseli sprawdzony przed pobraniem, limit bajtów, `ExtentError`, timeout (także z przyczyny httpx) — zawsze `slope=null`, provenance zachowane |
| Realne działki porównane z referencją GIS | `gdaldem-comparison.json`, `test_terrain_reference_comparison.py` — tabela w `nmt_contracts.md` (maks. Δ spadku 0,006°, ekspozycji 0,04°) |
| Ponowny odczyt snapshotu odtwarza klasy, profil i rozdzielczość | e2e: `reread["terrain"] == terrain` (w tym `relief`), żywa analiza nr 2732 w bazie |
| Wersja GDAL i format GeoTIFF | `GDAL 3.10.3, released 2025/04/01` w `relief.raster.gdal_version`; ścisły Float32 GeoTIFF |

## Polecenia i wyniki

Backend — jak `.github/workflows/ci.yml` (obraz zbudowany z repozytorium, bez `.env`):

```bash
docker compose build backend
docker compose run --rm -v "$PWD:/repo:ro" -e REPO_ROOT=/repo backend \
  pytest -m 'not docker_cli' --cov=app --cov-report=term-missing --cov-fail-under=80 -v
```

Wynik: **1578 passed, 3 deselected; pokrycie 92,19%** (baseline 1433 passed,
91%). Pokrycie zmienionych modułów: `backend-pytest-summary.txt` — m.in.
`services/nmt.py` 99% (baseline 39%), `raster/gdal.py` 99% (baseline 24%),
domena/aplikacja `terrain` 100%, adapter WCS 95%, `services/terrain.py` 98%.

Frontend (Node 22, jak CI):

```bash
docker build --target test -t dzialki-frontend-test ./frontend
docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 dzialki-frontend-test \
  sh -c "npm run typecheck && npm run test:coverage"
docker compose build frontend   # npm ci + npm run build
```

Wynik: typecheck OK, **225 passed**, pokrycie całości 97,79% linii / 88,84%
gałęzi; `TerrainCard.tsx` 100% / 89,79%, `lib/terrain.ts` 100% / 88,63%
(`frontend-coverage-summary.txt`); `next build` bez błędów. Nowe moduły
dopisane do `coverage.include`.

Artefakty scenariusza końcowego (offline):

```bash
docker compose run --rm -v "$PWD:/repo:ro" -e REPO_ROOT=/repo \
  -v "$PWD/docs/evaluation/results/bk-301-302:/evidence" -e EVIDENCE_OUT=/evidence \
  backend pytest tests/test_terrain_e2e.py
docker compose run --rm backend python -m scripts.compare_terrain_reference compare
```

## Ręczny odbiór UI (przed BK-701)

Stos `docker compose up -d db backend frontend`, przeglądarka
`http://localhost:3000`, zakładka „Identyfikator działki”, działka
`121701_1.0013.38` (Zakopane, żywe usługi GUGiK):

- sekcja „Rzeźba terenu (NMT)”: zmierzono, Hmin 1062,9 m, Hmax 1094,4 m,
  deniwelacja 31,5 m, siatka 2 m, 797 punktów, źródło NMT (pewność 90%);
- „Spadek, ekspozycja i profil”: rozdzielczość 1 m, zmierzona część 100%,
  ekspozycja południowa (158,07°), spadek średni 19,61° / 35,78%, P90 24,05°,
  klasy 81,17% „bardzo stromy (≥ 30%)”, profil 82 próbki (80,9 m, krok 1 m),
  raster 102×90 px, GDAL 3.10.3;
- ponowne uruchomienie tej samej działki (trafienie cache) pokazuje identyczne
  wartości; w bazie `analyses.terrain.status = available`,
  `result_contract_version = pog-v2.3+mpzp-v2.1+terrain-v1.0`, rekordy
  `NMT`/`NMT_WCS` z HTTP 200;
- SHA-256 GeoTIFF z żywej usługi (`1fbc031b84c8…`) jest identyczny z fixture'em
  porównania `gdaldem`, a statystyki są równe wartościom z porównania offline.

Dowody: `live/zakopane_api.json`, `live/zakopane_report.pdf`.

## Ograniczenia

- Porównanie „z QGIS” wykonano względem `gdaldem` (biblioteka GDAL, na której
  opierają się algorytmy GDAL w QGIS Processing), nie w interfejsie QGIS Desktop.
- GetMinMaxByPolygon i WCS mogą pochodzić z różnych aktualizacji NMT (kontrolny
  kwadrat: WCS 105,19–116,22 m wobec 112,3–115,7 m) — rozbieżność > 1 m jest
  ostrzeżeniem w wyniku, UI i PDF.
- Piksel o wysokości dokładnie 0,00 m jest maskowany jak NoData (kontrakt WCS).
- Katalog `docs/` jest wyłączony z repozytorium (`.gitignore`); runtime wymaga
  wpisu `nmt_wcs` w katalogu (montowanym przez Compose), inaczej wynik
  pochodnych ma status `unavailable` (`SOURCE_NOT_RUNNABLE`), a sekcja BK-301
  działa niezależnie.
