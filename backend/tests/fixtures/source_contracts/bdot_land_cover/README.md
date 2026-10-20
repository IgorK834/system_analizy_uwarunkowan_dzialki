# Zamrożone wydania kontraktu AU-204 (2026-10-17)

Trzy mini-paczki są dosłownymi wycinkami oficjalnych GML BDOT10k z powiatów 1261, 2816 i 1418.
Źródłowe URL, SHA-256 pełnych paczek oraz SHA-256 wycinków są w każdym `expected.json`.
Geometrie działek EPSG:2180 pochodzą z korpusu i ULDK (`1418/uldk_response.txt`).
`1418/api_result.json` to wynik backendu na tym wycinku, użyty także do testów UI.

Odtworzenie: `python backend/scripts/build_land_cover_fixtures.py --packages <pełne ZIP-y> --output backend/tests/fixtures/source_contracts/bdot_land_cover --parcel-response <odpowiedź ULDK>`.
Skrypt czyta `gml:posList` bezpośrednio i liczy oczekiwania niezależnie od adaptera GDAL/PostGIS.
Testy produkcyjnych obliczeń porównują pola, udziały, odległości, relacje i bufory.

**To wycinki wyłącznie do testów.** Granica powiatu jest pełna, ale obiekty są tylko przy 11 badanych działkach:
PT do 200 m, lasy do 5 km, budynki do 200 m, drogi do 500 m. Nie wolno używać ich jako pokrycia produkcyjnego.
Wszystkie geometrie są niezmienione, wraz z otworami i pełnymi współrzędnymi. Nie zawierają danych właścicieli.
Kontrakt, sposób liczenia i ograniczenia: `docs/data_sources/land_cover_contracts.md`.

Kontrola odbiorowa 2026-10-19: `backend/scripts/verify_land_cover_with_qgis.py`
odczytuje te GML przez QGIS/OGR i powtarza pomiary przez QgsGeometry (bez Shapely/aplikacji/PostGIS).
Wynik 11/11, wersja QGIS i digest obrazu: `docs/evaluation/results/au202-204/qgis-cross-check.json`.
