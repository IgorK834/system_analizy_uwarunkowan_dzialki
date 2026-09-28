# ADR-006: Rzeźba terenu (NMT) jako sekcja wyniku i pochodne rastra WCS

- Status: Zaakceptowany
- Data: 2026-09-28
- Dotyczy: BK-301 (Task 4.1) i BK-302 (Task 4.2)
- Kontrakt źródeł: `docs/data_sources/nmt_contracts.md`, `docs/data_sources/catalog.yaml` (`nmt`, `nmt_wcs`)
- Odbiór: `docs/evaluation/results/bk-301-302-verification.md`

## Kontekst

Adapter `GetMinMaxByPolygon` (`backend/app/services/nmt.py`) liczył Hmin/Hmax i
deniwelację, a `context.py` uruchamiał sekcję NMT, ale wynik nie trafiał do
`AnalyzeResponse`, bazy, cache, UI ani PDF. Brak pokrycia był zamieniany na
pustą listę — nieodróżnialną od „sprawdzono, nic nie ma”, co w prezentacji
groziło pokazaniem płaskiego terenu. Backlog wymagał też modułu GIS liczącego
spadek, ekspozycję i profil z oficjalnego rastra wysokościowego z jawną
rozdzielczością i ograniczeniami danych.

## Decyzja

### 1. Kontrakt `TerrainResult` (BK-301)

`AnalyzeResponse.terrain: TerrainResult | None` (schemat `1.0`):

| Pole | Znaczenie |
|---|---|
| `status` | `available` \| `no_coverage` \| `unavailable` \| `unknown` |
| `reason_code` | przyczyna statusu innego niż `available` (np. `NO_COVERAGE_SENTINEL`, `SERVICE_TIMEOUT`, `SERVICE_REPORTED_ERROR`, `LEGACY_SNAPSHOT`) |
| `min_height_m`, `max_height_m` | m n.p.m.; **bez zakazu wartości ujemnych** (Żuławy, wybrzeże) |
| `height_difference_m` | `max − min`, ≥ 0; walidowane w tolerancji 0,0015 m |
| `grid_size_m`, `sampled_points` | metadane próbkowania raportowane przez usługę |
| `source` | provenance zapytania (URL, czas, HTTP, SHA-256 odpowiedzi) — **także dla wyniku pustego**; `null` wyłącznie dla `unknown` |
| `warnings` | ostrzeżenia sekcji |
| `relief` | pochodne rastra (BK-302), `null` gdy nie liczono |

Cztery zachowania są rozłączne i egzekwowane walidatorem Pydantic:

| Sytuacja | Status | Wysokości |
|---|---|---|
| pomiar | `available` | liczby; `0` = zmierzony płaski teren |
| sentinel `Hmin=2500`/`Hmax=0` | `no_coverage` | `null` |
| timeout, HTTP ≠ 2xx, `error<TAB>…` z HTTP 200, odpowiedź bez Hmin/Hmax | `unavailable` | `null` |
| snapshot sprzed BK-301 (kolumna `NULL`) | `unknown` | `null` |

Rozpoznanie sentinela (relacja `min > max`) i błędu z HTTP 200 pozostaje w
adapterze — nie jest „naprawiane” do zera. `fetch_terrain_extremes` zwraca
`TerrainExtremes` albo `TerrainNoCoverage` (nigdy `None`), a
`NmtServiceUnavailableError` niesie `reason_code` i `source_metadata` nieudanej
próby. `ContextSectionResult` zyskał `reason_code`; provenance z wyjątku jest
zachowywane w sekcji.

NMT pozostaje sekcją informacyjną: nie wchodzi do `critical_sections()` i jego
niedostępność nie obniża statusu analizy. Źródło nieudanej próby jest zapisane
w sekcji i w rejestrze `source_records` (status `unavailable`/`no_coverage`),
ale nie trafia do listy źródeł oceniających status.

### 2. Persystencja, migracja i cache

- Migracja `022_analysis_terrain` (po head `021_compatibility_assessment`):
  kolumna `analyses.terrain JSONB NULL` bez wypełniania starych wierszy — brak
  zapisu jest odczytywany jako `unknown`, nigdy jako 0 m. Kolumna
  `result_contract_version` poszerzona z `VARCHAR(20)` do `VARCHAR(64)`;
  downgrade przycina etykietę i usuwa kolumnę.
- Snapshot jest źródłem prawdy odczytu historycznego, trafienia cache,
  wznowienia MPZP (`resume` nie modyfikuje kolumny) i raportu PDF.
- `RESULT_CONTRACT_VERSION = pog-v2.3+mpzp-v2.1+terrain-v1.0` — snapshot bez
  sekcji NMT nie jest serwowany jako trafienie cache.

### 3. Pochodne rastra (BK-302) w module `analysis`

Zgodnie z ADR-001:

| Warstwa | Plik | Odpowiedzialność |
|---|---|---|
| domain | `app/modules/analysis/domain/terrain.py` | czysty Python: Horn 3×3, statystyki, klasy, ekspozycja kołowa, profil, wyrównanie okna |
| application | `app/modules/analysis/application/terrain.py` | porty `ElevationRasterSource`, `ParcelFootprint`; przypadek użycia `analyze_parcel_relief`; kody przyczyn |
| infrastructure | `app/modules/analysis/infrastructure/terrain_raster.py` | WCS (klient OGC BK-102), dekoder GeoTIFF (GDAL CLI), obrys Shapely, parser DescribeCoverage |
| composition | `app/modules/analysis/composition.py` | guard katalogu `nmt_wcs`, limity z ustawień, wątek + timeout |

Moduł `imports` udostępnia klienta OGC i dekoder GDAL przez publiczne fabryki
`build_ogc_client`/`build_raster_decoder` w swoim root kompozycji — `analysis`
nie importuje cudzej warstwy `infrastructure`.

Algorytm (`ALGORITHM_VERSION = horn1981-3x3-v1`):

- okno = bbox działki + bufor 2 pikseli, przyciągnięte na zewnątrz do natywnej
  siatki z DescribeCoverage (inaczej MapServer przepróbkowuje dane);
- gradient Horna (1981) na oknie 3×3 — ta sama metoda co `gdaldem` i QGIS;
  piksel bez pełnego okna z danymi nie ma pochodnej (nie dostaje 0);
- statystyki tylko z pikseli, których środek leży w działce: średnia, mediana,
  P90 (interpolacja liniowa R-7), maksimum — w stopniach i procentach;
- klasy `slope-classes-pl-v1` (granica dolna włącznie): płaski < 2%,
  łagodny 2–5%, umiarkowany 5–10%, znaczny 10–15%, stromy 15–30%,
  bardzo stromy ≥ 30% — konwencja inżynierska systemu, nie norma prawna;
- ekspozycja: średnia kołowa azymutów spadku pikseli o spadku ≥ 2%; `flat`
  (wszystkie wartości kierunku `null`), gdy nachylone < 25% zmierzonej
  powierzchni; `dispersed`, gdy długość wypadkowej < 0,3; `defined` z sektorem
  8-kierunkowym w przeciwnym razie;
- profil: odcinek wzdłuż dłuższej osi minimalnego prostokąta obrysu przez jego
  środek, przycięty do działki, końce uporządkowane W→E; krok = rozdzielczość
  (rośnie do limitu 401 próbek), interpolacja dwuliniowa, NoData → `null`;
  zapisane końce, długość, krok i próbki (EPSG:2180) oraz linia GeoJSON WGS84.

Wynik `TerrainReliefResult` przechowuje status, `reason_code`, rozdzielczość,
liczby pikseli (działka / poprawne / NoData), statystyki, klasy, ekspozycję,
profil, metadane rastra (pokrycie, bbox, bufor, rozmiar, polityka NoData,
układ wysokości, wersja GDAL) i provenance (URL GetCoverage, SHA-256 GeoTIFF,
wersja pokrycia). Rozbieżność Hmin/Hmax między WCS 1 m a GetMinMaxByPolygon
większa niż 1 m jest jawnym ostrzeżeniem.

Bezpieczeństwo i limity (`backend/app/core/settings.py`):

| Ustawienie | Domyślnie | Rola |
|---|---|---|
| `terrain_relief_enabled` | `true` | wyłącznik funkcji (testy: `false` w `tests/conftest.py`) |
| `terrain_raster_max_pixels` | 1 000 000 | limit okna sprawdzany **przed** pobraniem |
| `terrain_raster_max_bytes` | 8 MiB | limit odpowiedzi WCS i dekodera |
| `terrain_raster_timeout_seconds` | 20 s | timeout klienta OGC i procesu GDAL; fasada async `2×t+15 s` |

Klient OGC wymusza HTTPS, allowlistę hosta z katalogu, blokadę adresów
prywatnych i limity; GDAL działa przez `subprocess.run` z listą argumentów (bez
`shell=True`), minimalnym środowiskiem i `-if GTiff`. NoData: `GDAL_NODATA`,
NaN oraz **niezadeklarowane 0.0**, którym WCS wypełnia obszar bez danych.

## Alternatywy odrzucone

- `rasterio`/`numpy` jako zależności — konflikt z systemowym `gdal-bin` (ADR
  gap_5 w `gdal.py`); czysty Python liczy 490 tys. pikseli w ok. 1 s.
- Odrzucanie wartości ujemnych — realne wysokości depresji i wybrzeża.
- Profil po kierunku największego spadku — zależny od danych i szumu; oś
  geometrii jest powtarzalna przy tych samych danych wejściowych.
- Wypełnianie brzegów (`-compute_edges`) — tworzy pochodne bez pełnego okna.

## Konsekwencje

- Snapshot rośnie o ok. 15–40 kB (profil do 401 próbek).
- Zmiana progów klas, algorytmu lub kontraktu wymaga podniesienia
  `SLOPE_CLASSES_VERSION`/`ALGORITHM_VERSION`/`TERRAIN_RESULT_SCHEMA_VERSION`
  (ta ostatnia unieważnia cache).
- Pokrycie `DTM_PL-KRON86-NH_TIFF` jest w układzie wysokości PL-KRON86-NH;
  GetMinMaxByPolygon może pochodzić z innej aktualizacji NMT — stąd ostrzeżenie
  spójności.
- Maskowanie 0.0 może ukryć realny piksel o wysokości dokładnie 0,00 m (wybrzeże);
  takie piksele są liczone jako NoData i zgłaszane w ostrzeżeniu.
