# Kontrakt pokrycia terenu BDOT10k — AU-204 / Task 23.4

Weryfikacja SW1: 2026-10-17. Źródło `bdot10k` (GUGiK), paczki
`https://opendata.geoportal.gov.pl/bdot10k/schemat2021/{woj}/{powiat}_GML.zip`.
Dokumentacja właściciela: https://www.geoportal.gov.pl/pl/dane/baza-danych-obiektow-topograficznych-bdot10k/.
Pobieranie jest bezpłatne do powszechnego wykorzystania; zachowujemy projektową politykę `derived_only`.
Atrybucja: „Źródło: Baza Danych Obiektów Topograficznych (BDOT10k), GUGiK — paczka powiatowa”.
Brak opublikowanego harmonogramu aktualizacji i SLA. Obowiązuje reguła świeżości `bdot10k` z katalogu (365 dni od importu).

## Potwierdzony format i limity

Trzy pobrane w całości paczki: 1261 (Kraków), 2816 (Pisz i Ruciane-Nida), 1418 (Góra Kalwaria).
ZIP/GML 3.2, schemat 2021, EPSG:2180, osie **easting, northing**. Klasy:
`OT_PTWP_A`, `OT_PTZB_A`, `OT_PTLZ_A`, `OT_PTRK_A`, `OT_PTUT_A`, `OT_PTTR_A`,
`OT_PTKM_A`, `OT_PTGN_A`, `OT_PTPL_A`, `OT_PTSO_A`, `OT_PTWZ_A`, `OT_PTNZ_A`.
`rodzaj` jest atrybutem źródła (nie występuje w każdej klasie). **Las** to wyłącznie `OT_PTLZ_A/rodzaj=las`:
zagajnik i zadrzewienie pozostają klasą pokrycia, lecz nie są lasem w pomiarze odległości.
WMS służy do podglądu/katalogowania paczek; obliczenia używają wektora.

Importer dziedziczy allowlistę hostów, timeout, limit 512 MiB paczki, bezpieczne rozpakowanie ZIP
(3 GiB całości, 1 GiB wpisu, maks. 200 wpisów, ograniczenie kompresji). Zapytania mają `statement_timeout`
i limit kandydatów; przekroczenie daje `unavailable`, bez obciętych statystyk.
Brak którejkolwiek klasy lub odrzucone geometrie oznaczają niekompletny import PT. Starszy import samych dróg i
budynków pozostaje użyteczny dla tych sekcji, lecz daje `land_cover=no_coverage/LAND_COVER_NOT_IMPORTED`.
Ponowny import pełnej paczki tworzy wydanie nowej wersji importera; nie zmienia historycznych wydań.

## Metoda

Działka i pierścień `buffer(działka, 200 m) − działka` są osobnymi mianownikami. Dla każdej klasy
`ST_Intersection` i unia geometrii w EPSG:2180 dają pole i udział procentowy. Nie normalizujemy sum do 100%.
Kontrola sumy i unii klas odróżnia luki oraz nakładanie klas; odchylenie ponad 0,01 punktu procentowego
powoduje `unknown/LAND_COVER_GEOMETRY_GAPS_OR_OVERLAPS`. Obszar poza wydaniem to `no_coverage`.
Zero nie zastępuje braku danych. Brak obiektów w działającym źródle pokrycia to `unknown`, nie pusty teren.

Las: odległość poligon działki → poligon lasu; PostGIS KNN i `ST_DWithin`, maksymalnie 5000 m
(`FOREST_SEARCH_RADIUS_M`). Sprawdzamy pokrycie całego obszaru do znalezionego lasu, a przy pustym
wyszukiwaniu całego promienia. Brak lasu w pokrytym ograniczonym promieniu to `no_match`, a nie dowód braku lasu.
Bufory 8/12/16 m powstają z unii wszystkich lasów w odległości do 16 m; tabela i warstwa podają część działki w buforze.
To **orientacyjne przybliżenie geometryczne, bez interpretacji przepisów**. Dla łuków bufora `quad_segs=16`.

## Zamrożone dowody

`backend/tests/fixtures/source_contracts/bdot_land_cover/<powiat>/` zawiera dosłowne fragmenty oryginalnych GML,
SHA-256 źródła i mini-paczki, geometrie 11 działek oraz niezależne oczekiwania. Wycinki są wyłącznie testowe:
PT do 200 m, lasy do 5 km, budynki do 200 m, drogi do 500 m; nie stanowią pełnych wydań powiatowych.
Oczekiwania liczy osobny skrypt `backend/scripts/build_land_cover_fixtures.py` bez importowania aplikacji,
GDAL ani PostGIS: odczyt współrzędnych bezpośrednio z `gml:posList`, następnie Shapely.
W produkcji importujemy pełną paczkę, nigdy mini-paczkę testową.

Dla `141801_4.0701.23/8` zamrożono też rzeczywistą odpowiedź ULDK. Aktualna paczka powiatu 1418
(SHA-256 w `expected.json`, stan danych 2024-12-19) daje **10 budynków w odległości ≤50 m**,
las **12,7291147 m** od działki. OnGeo: 11 budynków — sanity check nie jest oczekiwaniem dla innej migawki źródła.
Nie wymuszamy liczby 11. Ruciane-Nida `281604_5.0011.107` obejmuje ok. 26,6 ha i przecina las (odległość 0 m).
Testy porównują wszystkie udziały/pola, odległości i bufory (0,05 m / 0,05 m²) z niezależnymi oczekiwaniami;
failure injection obejmuje timeout, pusty wynik, zmianę CRS, niekompletny import i uszkodzony snapshot.

Kontrola QGIS 2026-10-19: oficjalny QGIS 4.2.3 odczytał te same GML przez OGR.
Wszystkie 11 działek spełnia tolerancje odległości/relacji dróg, budynków, lasów, pól i udziałów.
Skrypt `backend/scripts/verify_land_cover_with_qgis.py` nie importuje aplikacji, Shapely ani PostGIS.
Wynik i digest obrazu: `docs/evaluation/results/au202-204/qgis-cross-check.json`.
