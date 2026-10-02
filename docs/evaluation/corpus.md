# Rzeczywisty korpus referencyjny działek (BK-002)

## Wynik

Korpus znajduje się w
`backend/tests/fixtures/reference_corpus/manifest.json`. Zawiera 30 unikalnych
działek z 7 jednostek TERYT gmin, 5 województw oraz środowisk miejskich i
wiejskich. Zakres wielkości wynosi od około 192 m² do 266 tys. m². Zachowano
działki zwarte i działki o złożonym obrysie.

| Wymiar | Pokrycie |
|---|---:|
| działki | 30 |
| jednostki TERYT gmin | 7 |
| województwa | 5 |
| miejskie / wiejskie | 25 / 5 |
| POG: jedna / wiele stref | 5 / 2 |
| OUZ: wewnątrz / poza / granica / częściowo | 2 / 2 / 1 / 2 |
| MPZP: wektor / dokument / raster-manual | 3 / 1 / 8 |
| powódź: brak / przecięcie / granica | 21 / 8 / 1 |
| ochrona przyrody: brak / przecięcie | 26 / 2 |
| teren: płaski / wyraźna deniwelacja | 16 / 2 |

Pozostałe przypadki zachowują status `unknown` albo `manual_review`. Nie są
zamieniane na brak ograniczenia.

## Protokół wyboru bez dopasowania do algorytmu

Rdzeń 24 działek wybrano przed odczytem wyników domenowych: po cztery pierwsze
unikalne wyniki ULDK z ustalonej siatki punktów w Warszawie, Krakowie,
Bielsku-Białej, Legnicy, Piszu i Czarnym Borze. Wyniki NMT, ISOK, GDOŚ, MPZP,
POG i OUZ nie wpływały na wejście do rdzenia.

Sześć dalszych działek dobrano warstwowo do brakujących klas macierzy badawczej:
dwie rzeczywiście wielostrefowe POG, granica OUZ, przecięcie formy ochrony,
duża działka oraz granica jednej cechy powodziowej. To rozszerzenie opisuje
zakres badania, a nie zachowanie analizatora aplikacji. Oczekiwane wartości
powstały z urzędowych geometrii i niezależnych przecięć w `EPSG:2180`.

Dwie działki wielostrefowe mają ręcznie sprawdzone udziały:

- `246101_1.0004.737/26`: `SO` 19,65934% i `SJ` 80,34066%;
- `246101_1.0004.741/132`: `SO` 50,070792% i `SJ` 49,929208%.

## Źródła i redystrybucja

- Geometrie działek: [ULDK GUGiK](https://uldk.gugik.gov.pl/) oraz oficjalny
  opis [EGiB i ULDK](https://www.geoportal.gov.pl/pl/dane/ewidencja-gruntow-i-budynkow-egib/).
- Podstawa ponownego wykorzystania danych GUGiK:
  [informacja sektora publicznego](https://www.gov.pl/web/gugik/ponowne-wykorzystanie-informacji-sektora-publicznego).
- POG i OUZ: publiczny WFS
  [Rejestru Urbanistycznego](https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs).
- Powódź: WFS MZP/MRP ISOK; warunki udostępniania publikuje
  [PGW Wody Polskie](https://www.gov.pl/web/wody-polskie/udostepnianie-danych-z-systemu-informacyjnego-gospodarowania-wodami).
- Ochrona przyrody: publiczny WFS GDOŚ. Jego GetCapabilities deklaruje brak
  opłat i ograniczeń dostępu.
- Rzeźba terenu: publiczna usługa NMT GUGiK.
- MPZP discovery: KIMPZP GUGiK. WMS służy wyłącznie do wykrycia planu i trybu
  danych, bez obliczeń powierzchniowych.

Wektor MPZP Krakowa posłużył do własnego pomiaru symboli i udziałów. Katalog
źródeł projektu oznacza ten WFS jako `contract_required`, dlatego korpus nie
redystrybuuje surowych odpowiedzi ani geometrii. Zachowuje wynik faktograficzny,
identyfikator aktu, link BIP i SHA-256 odpowiedzi użytej podczas pomiaru.

Nie zapisano nazw właścicieli, numerów ksiąg wieczystych, adresów osób ani
innych danych podmiotowych EGiB.

## Znaczenie statusów

- `available`: źródło i oczekiwana wartość zostały sprawdzone;
- `manual_review`: wynik istnieje, ale przypadek graniczny, raster albo tekst
  aktu wymaga kontroli człowieka;
- `unknown`: nie ma wystarczającego dowodu. `unknown` nie oznacza `none`.

Przypadek OUZ z przecięciem 0,158 m² zachowuje `manual_review` i tolerancję
1 m². Przypadek granicy powodzi opisuje jednocześnie styczność z jedną cechą
Q0,2% i powierzchniowe przecięcie innych stref. Dwa chwilowe błędy GDOŚ oraz
limit NMT dla poligonów powyżej 100 tys. m² pozostały jako `unknown`.

## Odtwarzanie offline

Walidator `tests.parcel_fixtures_config.load_reference_corpus` używa wyłącznie
`pathlib`, `json` i `hashlib`. Sprawdza:

- duplikaty `case_id` i `parcel_identifier`;
- co najmniej 24 działki, 5 jednostek TERYT i 3 województwa;
- pełną macierz scenariuszy i minimum dwie działki wielostrefowe;
- istnienie źródła dla każdego artefaktu;
- bezpieczne ścieżki lokalne, CRS i SHA-256;
- status, metodę, wartość z jednostką i tolerancje każdej sekcji.

Polecenie odbiorowe:

```bash
cd backend
pytest tests/test_reference_corpus.py tests/test_parcel_fixtures.py -q
```

Test `test_loader_replays_with_egress_blocked` blokuje `socket.connect` i
`socket.create_connection`, po czym ładuje oraz weryfikuje wszystkie 61 plików
korpusu. Zwykłe testy i CI nie uruchamiają procedury pobierania.

## Aktualizacja

Odświeżenie jest świadomą czynnością badawczą poza CI:

1. zachować poprzedni manifest do porównania;
2. ponownie pobrać geometrię po identyfikatorze ULDK;
3. zweryfikować datę, CRS, identyfikator i brak danych osobowych;
4. wykonać pomiary w `EPSG:2180` i ręcznie sprawdzić przypadki graniczne;
5. zaktualizować artefakty, SHA-256, `verified_at`, metodę i notatki;
6. przejrzeć różnicę expected przed akceptacją.

Zmiana źródła albo geometrii nie może automatycznie nadpisać expected w CI.

## Badania oparte na korpusie (BK-601, BK-602)

Korpus jest wejściem badania poprawności (`evaluate_reference_corpus.py --study`, wyniki w
`results/accuracy/`, analiza w `error_analysis.md`) i eksperymentu centroid vs przecięcie
(`compare_centroid_intersection.py`, `results/centroid/`). Korpus nie zawiera geometrii
stref POG/MPZP, tylko ich udziały, więc dokładną część eksperymentu wykonuje się na
warstwach zamrożonych osobno narzędziem `freeze_pog_zone_layers.py`. Opis wyników i
ograniczeń: [odbiór BK-601–603](bk-601-603-verification.md).

