# Kontrakty usług NMT (GUGiK)

Stan na 28 września 2026 r. Fixtures: `backend/tests/fixtures/source_contracts/nmt_*.txt`
(REST) i `backend/tests/fixtures/terrain/` (WCS). Decyzje: `docs/adr/ADR-006-terrain-result-and-raster-derivatives.md`.

## 1. REST `GetMinMaxByPolygon` (`source_id: nmt`)

Potwierdzony 2026-07-30, ponownie użyty 2026-09-28.

- `GET https://services.gugik.gov.pl/nmt/?request=GetMinMaxByPolygon&polygon=<WKT EPSG:2180>`
- WKT w kolejności `(easting, northing)` — geometria ULDK bez transformacji.
- Odpowiedź `text/plain`, wiersze `Klucz<TAB>wartość`: `Polygon area`,
  `Points count`, `Grid size [m]`, `Hmin`, `Hmax` (+ punkty skrajne).
- Siatka próbkowania zależy od wielkości poligonu (4 m dla 1 ha, 2 m dla ok.
  0,3 ha — obserwacja z 2026-09-28).
- **Brak pokrycia**: HTTP 200 z sentinelem `Hmin 2500` / `Hmax 0` →
  `TerrainResult.status = no_coverage`, wysokości `null`.
- **Błąd wejścia**: HTTP 200 z wierszem `error<TAB>…` → `unavailable`
  (`SERVICE_REPORTED_ERROR`), nigdy 0.
- Kontrolny pomiar: kwadrat `637000–637100 × 486000–486100` → Hmin 112,3 m,
  Hmax 115,7 m, deniwelacja 3,4 m, siatka 4 m, 676 punktów.

## 2. WCS 2.0.1 NMT GRID1 GeoTIFF (`source_id: nmt_wcs`)

Potwierdzony realnymi zapytaniami 2026-09-28.

| Element | Wartość |
|---|---|
| Endpoint | `https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF` |
| Serwer / wersje | MapServer; WCS 2.0.1 (także 1.1.1, 1.0.0), KVP GET |
| Licencja | `ows:Fees=none`, `ows:AccessConstraints=none`; dane NMT udostępniane nieodpłatnie |
| Pokrycie | `DTM_PL-KRON86-NH_TIFF` (`RectifiedGridCoverage`), układ wysokości PL-KRON86-NH |
| Siatka | EPSG:2180, piksel 1 × 1 m, `gml:origin` = środek piksela `796521.169410 160828.843266` (northing, easting), offsetVectors `0 1` i `-1 0` |
| Krawędzie siatki | easting `160828.343266 + k`, northing `796521.669410 − k` |
| Formaty | `image/tiff` (Float32, bez kompresji), PNG/JPEG (podgląd) |
| Rozszerzenia | CRS (2180/4326/3857), scaling, interpolation (NEAREST/AVERAGE/BILINEAR) |
| Serwis ASCII | `…/DigitalTerrainModel` publikuje też `DTM_PL-EVRF2007-NH` |

Zachowania istotne dla adaptera:

1. `subset=x(minE,maxE)&subset=y(minN,maxN)` — `x` to easting, `y` northing
   (potwierdzone prostokątem 200×100 m → raster 200×100 px i zgodnością z
   `GetHByXY` w 32 punktach do ~0,3 m).
2. GeoTIFF ma tie-point dokładnie w rogu żądania; bbox niewyrównany do
   krawędzi natywnych pikseli jest **przepróbkowywany** przez serwer (różnice
   kilku cm) — adapter zawsze wyrównuje okno do siatki z DescribeCoverage.
3. GDAL 3.10 odczytuje CRS jako `ETRF2000-PL / CS92` z `ID["EPSG",2180]`
   (`stac.proj:epsg = 2180`).
4. Poza obwiednią pokrycia: **HTTP 400** `ows:ExceptionReport`
   `exceptionCode="ExtentError"` → `no_coverage` (`OUTSIDE_COVERAGE`).
5. Nieznane pokrycie: **HTTP 404** `NoSuchCoverage` → `unavailable`.
6. W obwiedni, ale bez danych (np. za granicą państwa): raster wypełniony
   **dokładnym 0.0 bez `GDAL_NODATA`** → maskowane; działka bez danych →
   `no_coverage` (`NO_DATA_IN_PARCEL`).
7. Wartości ujemne są realne (np. −0,25 m przy Zatoce Gdańskiej) i nie są
   odrzucane.

Środowisko dekodowania: obraz backendu `python:3.13-slim` + `gdal-bin`,
`gdalinfo --version` → `GDAL 3.10.3, released 2025/04/01`; `rasterio` nie jest
zainstalowane. Wersja jest zapisywana w wyniku (`relief.raster.gdal_version`).

## 3. Porównanie z referencją GIS

Realne działki (ULDK) porównano z `gdaldem slope/aspect -alg Horn` — implementacją
algorytmów GDAL „Slope”/„Aspect” w QGIS Processing — piksel po pikselu (skrypt
`backend/scripts/compare_terrain_reference.py`, test
`backend/tests/test_terrain_reference_comparison.py`):

| Działka | Teren | Piksele | maks. Δ spadku | maks. Δ ekspozycji |
|---|---|---|---|---|
| `121701_1.0013.38` (Zakopane) | stok | 3 192 | 0,0058° | 0,028° |
| `126104_9.0009.191/13` (Kraków) | falisty/kamieniołom | 92 264 | 0,0011° | 0,040° |
| `146518_8.0406.23` (Warszawa) | płaski | 1 913 | 0,0006° | 0,021° |

Różnice wynikają z zapisu referencji w Float32. Porównania nie wykonano w
interfejsie QGIS Desktop (brak w środowisku) — referencją jest ta sama
biblioteka GDAL, której QGIS używa dla tych algorytmów.
