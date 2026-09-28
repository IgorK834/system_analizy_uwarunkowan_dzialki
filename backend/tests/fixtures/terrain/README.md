# Fixtures rastra NMT (BK-302)

Realne odpowiedzi usługi WCS 2.0.1 NMT GRID1 (GUGiK) zamrożone 2026-09-28 —
zwykłe CI nie łączy się z internetem. Endpoint:
`https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF`,
pokrycie `DTM_PL-KRON86-NH_TIFF`.

| Plik | Zapytanie | Co dokumentuje |
|---|---|---|
| `wcs_getcapabilities.xml` | `GetCapabilities` | WCS 2.0.1, `Fees=none`, `AccessConstraints=none`, formaty, CRS 2180/4326/3857 |
| `wcs_describecoverage.xml` | `DescribeCoverage` | siatka 1 m, origin (środek piksela) w kolejności northing/easting, offsetVectors |
| `wcs_getcoverage_warszawa_aligned.tif` | `subset=x(637000.343266,637100.343266)&subset=y(486000.66941,486100.66941)` | Float32 GeoTIFF 100×100, tie-point w rogu żądania, brak `GDAL_NODATA` |
| `wcs_getcoverage_warszawa_parcel_window.tif` | okno działki kontrolnej + bufor 2 px (`x(636997.343266,637102.343266)`, `y(485997.66941,486102.66941)`) | SHA-256 `603a9ddd…76ec` identyczny z przebiegiem na żywo |
| `wcs_getcoverage_outside_poland_zero.tif` | `x(170000,170040)&y(450000,450040)` | obszar bez danych = dokładne 0.0 bez znacznika NoData |
| `wcs_getcoverage_baltic_negative.tif` | `x(480000,480050)&y(760000,760050)` | realne wysokości ujemne (−0,25…−0,04 m) |
| `wcs_exception_extent.xml` | subset poza obwiednią | HTTP 400, `ExceptionReport exceptionCode="ExtentError"` |
| `wcs_exception_no_such_coverage.xml` | `COVERAGEID=NOPE` | HTTP 404, `NoSuchCoverage` |

`real/` — trzy realne działki (ULDK) z wyrównanymi oknami GeoTIFF i manifestem
(SHA-256, URL żądania, czas pobrania) do porównania z referencją `gdaldem`
(`backend/scripts/compare_terrain_reference.py`). Odtworzenie w kontenerze
backendu (wymaga internetu): `python -m scripts.compare_terrain_reference fetch`;
porównanie offline: `python -m scripts.compare_terrain_reference compare`.

Syntetyczne rastry kontrolne (płaszczyzna o zadanym gradiencie, raster
poziomy, NoData, zły CRS) są generowane w testach przez `gdal_translate` z
siatki AAIGrid (`tests/terrain_fixtures.py`), więc ich wartości są dokładnie znane.
