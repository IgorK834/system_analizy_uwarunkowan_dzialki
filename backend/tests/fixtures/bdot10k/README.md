# Fixtures BDOT10k: drogi i zabudowa sąsiednia

Mini-paczki powiatowe BDOT10k (schemat 2021, GML 3.2, EPSG:2180) i wartości oczekiwane dla działek korpusu referencyjnego.
Dane pochodzą z publicznych paczek Geoportalu (`https://opendata.geoportal.gov.pl/bdot10k/schemat2021/<woj>/<powiat>_GML.zip`,
pobrane 2026-10-13); kontrakt i licencja: `docs/data_sources/road_contracts.md`, `docs/data_sources/building_contracts.md`.

Każdy katalog `<powiat>/` zawiera:

* `<powiat>_GML.zip` — mini-paczka w układzie oryginału (te same nazwy plików GML): osie jezdni (`OT_SKJZ_L`) w promieniu
  500 m i obrysy budynków (`OT_BUBD_A`) w promieniu 200 m od działek korpusu z tego powiatu oraz jeden obiekt `powiat` z
  `OT_ADJA_A` (zasięg wydania). Obiekty są dosłownymi fragmentami oryginalnego GML, bez zmian atrybutów i współrzędnych.
  Archiwum jest deterministyczne (stałe znaczniki czasu, stała kolejność wpisów), więc ten sam komplet danych daje ten sam
  SHA-256.
* `expected.json` — wartości oczekiwane policzone **niezależnie od aplikacji**: współrzędne czytane wprost z `gml:posList`
  (bez GDAL, PostGIS i kodu aplikacji), odległości, relacje i pola liczone Shapely w EPSG:2180 według definicji z dokumentów
  kontraktowych. Zawiera też SHA-256 i rozmiar pełnej paczki źródłowej oraz liczności obiektów w mini-paczce.

| Powiat | Pełna paczka (B) | SHA-256 pełnej paczki | Mini-paczka (B) | Drogi | Budynki | Działki |
|---|---:|---|---:|---:|---:|---:|
| 0221 wałbrzyski | 14 454 176 | `06c500fb…` | 102 579 | 172 | 238 | 4 |
| 0262 Legnica | 5 383 751 | `80f974d6…` | 226 534 | 531 | 798 | 5 |
| 1261 Kraków | 35 198 806 | `1355c5a5…` | 362 362 | 499 | 948 | 4 |
| 1465 Warszawa | 89 020 411 | `8cad94ae…` | 227 002 | 583 | 541 | 4 |
| 2461 Bielsko-Biała | 19 169 873 | `560f259c…` | 295 350 | 1 123 | 1 340 | 7 |
| 2816 piski | 54 352 143 | `a887ee22…` | 186 606 | 508 | 573 | 6 |

Łącznie 30 działek korpusu. Zakres danych odpowiada wydaniu z 2026-10-13; najnowsza wersja obiektu w mini-paczce jest podana
w `latest_version_in_fixture`.

## Odtwarzanie

Pełne paczki nie są częścią repozytorium (łącznie ok. 217 MB). Aby odtworzyć fixtures, pobierz paczki do osobnych katalogów
`<katalog>/bdot10k_<powiat>/<powiat>_GML.zip` i uruchom na hoście:

```bash
python3 -I backend/scripts/build_bdot10k_fixtures.py --packages <katalog> --output backend/tests/fixtures/bdot10k
```

Skrypt nie łączy się z siecią. Niezależne porównanie narzędziem GDAL (`ogr2ogr`, nie QGIS):
`backend/scripts/verify_bdot10k_with_gdal.py`, wynik: `docs/evaluation/results/bdot10k/gdal-cross-check.md`.
