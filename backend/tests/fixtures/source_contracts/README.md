# Fixtures kontraktów źródeł danych

Małe, zanonimizowane fragmenty odpowiedzi urzędowych usług, potwierdzające
kontrakt zadeklarowany w `docs/data_sources/catalog.yaml`. Służą testom
kontraktowym, aby deklaracje katalogu (warstwy, CRS, wersja protokołu) były
weryfikowane wobec realnej struktury odpowiedzi usługi, a nie wymyślane.

| Plik | Źródło | Zakres |
|---|---|---|
| `kimpzp_getcapabilities.xml` | KIMPZP (WMS), GUGiK | Przycięty GetCapabilities: wersja WMS 1.1.1, CRS EPSG:2180, nazwy warstw z warstwą queryable `plany_granice`. |
| `kiut_getcapabilities.xml` | KIUT (WMS), GUGiK | Przycięty GetCapabilities zweryfikowany 2026-09-02: brak opłat i ograniczeń, warstwa pokrycia `gesut`, warstwy sieci oraz ich maksymalna skala 1:1000. |
| `warsaw_parcels_getcapabilities.xml` / `warsaw_parcels_describe.xml` | WFS BGiK Warszawa | Przycięte kontrakty warstwy `wfs:dzialki`: WFS 2.0, EPSG:2178 i dozwolone pola geometrii działek bez danych właścicieli. |
| `krakow_mpzp_getcapabilities.xml` / `krakow_mpzp_describe.xml` | WFS MSIP Kraków | Przycięty techniczny kontrakt warstw granic i przeznaczeń MPZP. Nie jest potwierdzeniem prawa do produkcyjnej redystrybucji. |
| `kimpzp/` | KIMPZP (WMS GetFeatureInfo `plany_granice`), 10 gmin | Nieprzetworzone odpowiedzi HTML z manifestem SHA-256 (AU-004): bloki „Obowiązujące MPZP” ze zmianami, tabele Esri/GeoServer/QGIS, pary th/td, „brak serwisu”, „brak wyniku” oraz błędy usług gminnych z HTTP 200. Opis i oczekiwania: `kimpzp/README.md`. |
| `mpzp/` | RU MPZP, KIMPZP i siedem gmin korpusu | Pełne Capabilities/DescribeFeatureType RU MPZP, odpowiedzi `resultType=hits`, filtrowane strony rejestru KIMPZP oraz walidowana macierz BK-201. |
| `gdos_getcapabilities.xml` | WFS GDOŚ | Przycięty GetCapabilities: WFS 2.0.0, `Fees: brak`, `AccessConstraints: brak`, warstwy `GDOS:*` używane przez adapter i `DefaultCRS` w formie URN. |
| `gdos_describe.xml` | WFS GDOŚ | Przycięty DescribeFeatureType dowodzący, że warstwy form ochrony przyrody nie mają atrybutu rodzaju ochrony (tylko `gid`, `nazwa`, `kodinspire`, `kod`, `geom`). |
| `gdos_getfeature.xml` | WFS GDOŚ | Pełna realna odpowiedź z jedną cechą: `gml:MultiSurface` z `srsName` na kontenerze, brak opcjonalnego `nazwa`. |
| `gdos_exception_report.xml` | WFS GDOŚ | Realny `ows:ExceptionReport` zwrócony ze statusem **HTTP 200** dla zapytania bez `typeNames`. Fixture regresyjny przeciw cichej zamianie błędu na „brak kolizji”. |
| `isok_getcapabilities.xml` | WFS INSPIRE MZP/MRP, PGW Wody Polskie | Przycięty GetCapabilities: WFS 2.0.0, brak opłat i ograniczeń, warstwy `nz-core:*`, `DefaultCRS` EPSG:4258 z EPSG:2180 wyłącznie jako `OtherCRS`. |
| `isok_getfeature.xml` | WFS INSPIRE MZP/MRP | Przycięta realna cecha `nz-core:HazardArea`: klasa prawdopodobieństwa w `qualitativeLikelihood`, pierścienie wewnętrzne, `srsName` w formie HTTP. |
| `nmt_getminmaxbypolygon.txt` | REST NMT, GUGiK | Pełna realna odpowiedź `GetMinMaxByPolygon` (`text/plain`, format `Klucz<TAB>wartość`). |
| `nmt_getminmaxbypolygon_brak_pokrycia.txt` | REST NMT, GUGiK | Pełna realna odpowiedź dla obszaru bez danych wysokościowych: sentinel `Hmin 2500` / `Hmax 0`, `POINT(0 0 ...)`. |

## Weryfikacja kolejności osi EPSG:2180 (GDOŚ, ISOK)

Kolejności osi nie da się potwierdzić samym plikiem odpowiedzi, dlatego zapis
metody jest częścią kontraktu. Weryfikacja z 2026-07-30:

1. Geometria kanoniczna systemu pochodzi z ULDK i jest w kolejności
   `(easting, northing)`: zapytanie `GetParcelByXY&xy=637000,486000,2180` zwraca
   działkę w Warszawie o WKT rozpoczynającym się od `POLYGON((637343.32 485978.26`,
   czyli pierwsza wartość to easting.
2. GDOŚ: `GetFeature&typeNames=GDOS:ParkiNarodowe&bbox=605600,493700,605700,493900,EPSG:2180`
   (okno w Puszczy Kampinoskiej) zwraca 2 cechy o zakresach współrzędnych
   `486726..508957` i `581371..632378`. Okno pokrywa się z tą kopertą wyłącznie
   przy odczycie pierwszej wartości jako northing. Ten sam bbox podany odwrotnie
   zwraca `numberReturned="0"`, więc **parametr bbox** ma kolejność
   `(minE,minN,maxE,maxN)`, a **zwracana geometria** kolejność `(northing, easting)`.
3. ISOK: przy `srsName=urn:ogc:def:crs:EPSG::2180` odpowiedź ma zakresy
   `340638..534029` i `509017..708254`, co odpowiada odpowiednio szerokości
   50,9-52,7 i długości 19,1-22,0 tej samej cechy zwróconej w EPSG:4258 —
   czyli również `(northing, easting)`.

Bez zamiany osi `parcel.intersects(zone)` byłoby zawsze fałszem, a obie sekcje
raportowałyby brak kolizji. Regresję pilnują testy `tests/test_gml.py`.

Uwagi:

- Fixture może potwierdzać sam kontrakt techniczny źródła
  `contract_required`, ale nie zmienia jego statusu prawnego. Tak jest dla
  Krakowa: URL, warstwy, pola i CRS są znane, natomiast guard nadal blokuje
  publikację do czasu uzyskania pisemnej zgody wymaganej przez warunki MSIP.
- Źródła `research` i `placeholder` bez potwierdzonego kontraktu technicznego
  celowo nie mają fixtures kontraktowych.
- Macierz MPZP i regułę odróżniającą strefy od granicy aktu opisuje
  `docs/data_sources/mpzp_contracts.md`. Zero w odpowiedzi RU/KIMPZP jest
  obserwacją kanału z podanego dnia, a nie dowodem braku obowiązującego aktu.
- Usługi ULDK i UUG są usługami REST (nie WMS/WFS), więc nie mają
  odpowiednika GetCapabilities/DescribeFeatureType — ich kontrakt potwierdza
  publiczna dokumentacja GUGiK oraz testy adapterów `test_uldk.py` /
  `test_geocoding.py`. Wyjątkiem jest NMT: jest usługą REST, ale jej format
  odpowiedzi jest nieoczywisty (tekst, nie JSON) i ma sentinel braku danych,
  więc realne odpowiedzi są zapisane jako fixtures.
- Fixtures GDOŚ i ISOK potwierdzają też przypadki błędne, nie tylko poprawne.
  To celowe: obie usługi zwracają błędy ze statusem HTTP 200, więc test
  „szczęśliwej ścieżki” nie wykryłby regresji w rozpoznawaniu awarii.
- Zawartość jest przycięta i zanonimizowana: bez danych osobowych i bez pełnej
  listy metadanych usługi.

Kontrakty Rejestru Urbanistycznego mają osobną politykę wersjonowania z
manifestem URL/czas/SHA-256 i znajdują się w katalogu `../ru/`. Instrukcja
odświeżania oraz opis zmierzonych rozbieżności są w
`docs/data_sources/ru_contracts.md`; plików RU nie duplikujemy w tym katalogu.
