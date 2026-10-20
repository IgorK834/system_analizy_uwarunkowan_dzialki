# Kontrakt BDOT10k: drogi (paczki powiatowe)

Stan na 13 października 2026 r. (spike SW1 zadań 23.1–23.2, issues #307 i #308). Wpis w katalogu: `bdot10k` w
`docs/data_sources/catalog.yaml`. Zamrożone dane: `backend/tests/fixtures/bdot10k/<powiat>/` (mini-paczki z prawdziwych
wydań i wartości oczekiwane), kontrakt WFS: `backend/tests/fixtures/source_contracts/bdot10k_wfs_*.xml`. Zabudowa z tej samej
paczki: `docs/data_sources/building_contracts.md`. Decyzje: `docs/adr/ADR-025-bdot10k-droga-i-zabudowa-sasiednia.md`.

## 1. Kanał danych

| Element | Wartość |
|---|---|
| Paczka powiatowa | `https://opendata.geoportal.gov.pl/bdot10k/schemat2021/<woj>/<powiat>_GML.zip` (`<woj>` = dwie pierwsze cyfry TERYT powiatu) |
| Katalog paczek (WFS) | `https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/PobieranieBDOT10k`, jedna warstwa `ms:BDOT10k_powiaty` |
| Pola katalogu | `TERYT`, `NAZWA_POWIATU`, `Data_aktualizacji`, `URL_GML` (geometria powiatu); filtr po `TERYT` — nazwa `JPT_KOD_JE` nie istnieje |
| Licencja | strona BDOT10k w Geoportalu: baza dostępna bezpłatnie i do dowolnego wykorzystania; `Fees=NONE`, `AccessConstraints=NONE` |
| Redystrybucja w katalogu | `derived_only` — ostrożnie; decyzja właściciela danych (ADR-025) |
| Układ | EPSG:2180; w `gml:posList` kolejność `(easting northing)` (np. `567459.55 244869.42` w Krakowie) |
| Format | GML 3.2, przestrzeń nazw `urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0`, jeden plik na klasę |
| Schemat | `../XSD/BDOT10k_BDOO.xsd` jest wskazany w nagłówku, ale **nie ma go w paczce** — GDAL wyprowadza pola ze struktury pliku |

WFS `PobieranieBDOT10k` **nie udostępnia obiektów topograficznych** (drogi, budynki) — tylko katalog paczek. Pobranie
paczki ZIP jest jedyną ścieżką; zerwanie połączenia przy `GetFeature` obiektów, które notowano 2026-10-05, jest więc
oczekiwane, a nie awarią. Pole `Data_aktualizacji` katalogu bywa **starsze od obiektów w paczce** (Kraków: 2024-04-22, a
najnowsza wersja obiektu w paczce pobranej 2026-10-13 to 2026-05-27), dlatego datą stanu danych jest najnowsze pole `wersja`
obiektów, nie to pole.

## 2. Zawartość paczki

Paczka ma 75–80 plików `PL.PZGiK.<n>.<powiat>/BDOT10k/PL.PZGiK.<n>.<powiat>__OT_<KLASA>.xml`. Importujemy trzy klasy:

| Klasa | Geometria | Znaczenie | Wykorzystanie |
|---|---|---|---|
| `OT_SKJZ_L` | `gml:Curve` z `LineStringSegment` | oś jezdni | droga (`bdot_roads`) |
| `OT_BUBD_A` | `gml:Polygon` (pierścienie wewnętrzne występują) | obrys budynku | zabudowa (`bdot_buildings`) |
| `OT_ADJA_A` | `gml:Polygon` | jednostki administracyjne; obiekt `rodzaj = powiat` o danym TERYT | zasięg wydania (`bdot_releases.extent`) |

Pominięte świadomie: `OT_SKDR_L` (osie „dróg” w sensie ciągu — liczebnie niemal identyczne z `OT_SKJZ_L`, więc jezdnia i
droga nie są sumowane), pokrycie terenu i linie energetyczne (nie są potrzebne do kontekstu drogi i zabudowy) oraz pola tekstu
wolnego (`nazwa`, `informacjaDodatkowa`, `uwagi`) i `identyfikatorEGiB`.

Atrybuty osi jezdni zapisywane w `bdot_roads` (nazwy źródłowe w `field_mapping` katalogu): `lokalnyId`, `kategoriaIstnienia`
(`eksploatowany`, `w budowie`, `nieczynny`), `kategoriaZarzadzania` (`krajowa`, `wojewódzka`, `powiatowa`, `gminna`,
`wewnętrzna`), `klasaDrogi` (`autostrada` … `droga dojazdowa`, `droga wewnętrzna`), `materialNawierzchni`, `polozenie`
(`na powierzchni gruntu`, `ponad powierzchnią gruntu poziom 1`), `numerDrogi`, `ulicaNazwa1`, `szerokoscNawierzchni`, `wersja`.
Zapytania uwzględniają wyłącznie `kategoriaIstnienia = eksploatowany`.

Kategoria zarządzania i klasa drogi opisują drogę w źródle; **nie są oceną jej prawnego charakteru**. W korpusie 5 z 30
najbliższych osi to `droga wewnętrzna` (m.in. w powiatach piskim, wałbrzyskim i Legnicy) — jest to widoczne w wyniku i nie
oznacza „dostępu do drogi publicznej”.

## 3. Wydanie, zasięg i import

* Wydanie to jedna paczka powiatu: `data_releases.scope_key = powiat:<TERYT>`, etykieta `bdot10k-<TERYT>-<sha256:12>`;
  aktywne wydania różnych powiatów współistnieją (migracja `036_bdot10k_context`).
* Zasięgiem jest granica powiatu z `OT_ADJA_A`. Działka bez wydania, którego granica zawiera jej punkt reprezentatywny, ma
  status `no_coverage` (`OUTSIDE_COVERAGE` albo `NO_ACTIVE_RELEASE`) — **nigdy „brak drogi”**.
* Import jest odtwarzalny: ta sama paczka (SHA-256) daje to samo wydanie i te same liczności obiektów per klasa.
* Polecenie: `python -m app.modules.imports bdot10k --powiat 1261 [--package <plik|adres>] [--dry-run]` (README, sekcja
  „Import BDOT10k”).

Zaimportowane paczki korpusu (2026-10-13; czas importu w kontenerze deweloperskim, PostGIS 16 w emulacji amd64):

| Powiat | Paczka (B) | SHA-256 paczki | Drogi | Budynki | Stan danych (`wersja`) | Czas importu |
|---|---:|---|---:|---:|---|---:|
| 0221 wałbrzyski | 14 454 176 | `06c500fb…` | 14 185 | 15 543 | 2026-05-06 | 3,0 s |
| 0262 Legnica | 5 383 751 | `80f974d6…` | 5 230 | 12 302 | 2026-05-18 | 1,8 s |
| 1261 Kraków | 35 198 806 | `1355c5a5…` | 33 585 | 90 563 | 2026-05-27 | 12,6 s |
| 1465 Warszawa | 89 020 411 | `8cad94ae…` | 69 505 | 167 099 | 2025-06-01 | 24,6 s |
| 2461 Bielsko-Biała | 19 169 873 | `560f259c…` | 13 323 | 37 591 | 2026-06-03 | 5,1 s |
| 2816 piski | 54 352 143 | `a887ee22…` | 20 358 | 27 183 | 2024-09-11 | 5,1 s |

Odrzucone przy imporcie: 0 obiektów w żadnej paczce; naprawianych geometrii: 0.

## 4. Pomiar odległości

* Mierzymy **od poligonu działki do osi jezdni** (`parcel_polygon_to_carriageway_axis`) w EPSG:2180 (`ST_Distance`), nie do
  granicy pasa drogowego. Oś, która przecina działkę albo dotyka jej brzegu, daje 0 m i relację `intersects` / `touches`;
  inaczej `disjoint`.
* Wyszukiwanie rośnie promieniami 25 → 50 → 100 → 250 → 500 m (`ST_DWithin`, indeks GiST); wynik niesie promień, w którym
  znaleziono drogę, listę sprawdzonych promieni i limit kandydatów. Remis w granicach 1 mm rozstrzyga silniejsza relacja,
  potem identyfikator obiektu.
* Pusty ograniczony zasięg to `no_match` (`SEARCH_RADIUS_EXHAUSTED`) — **nie dowodzi braku drogi**. Obszar wyszukiwania
  wykraczający poza granicę powiatu wydania jest oznaczony (`search_area_within_coverage = false`) i obniża wynik do
  częściowego.
* Wynik **nie zawiera pola prawnego dostępu do drogi publicznej** i nie wyprowadza go z geometrii; każda postać wyniku (API,
  UI, PDF, pakiet audytowy) niesie zdanie: „Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu
  działki do drogi publicznej.”

## 5. Weryfikacja

* Wartości oczekiwane 30 działek korpusu (`expected.json`) policzono Shapely ze współrzędnych odczytanych wprost z
  `gml:posList` — bez GDAL, PostGIS i kodu aplikacji (`backend/scripts/build_bdot10k_fixtures.py`).
* **Drugi, niezależny pomiar — GDAL, nie QGIS.** `backend/scripts/verify_bdot10k_with_gdal.py` powtarza pomiar narzędziem
  `ogr2ogr` (GDAL 3.10.3, dialekt SQLite, GEOS) na tych samych mini-paczkach: 30 z 30 działek zgodnych w tolerancji 0,011 m
  (odległość) i 0,05 m² (pole); wynik: `docs/evaluation/results/bdot10k/gdal-cross-check.md`. QGIS nie był dostępny, więc
  kryterium „porównanie w QGIS” jest spełnione innym narzędziem, o czym mówi wprost raport odbioru.
* Testy aplikacji porównują wynik PostGIS z `expected.json` dla wszystkich 30 działek (tolerancja 0,05 m dla odległości).

## Domknięcie podstawy BDOT10k (AU-201, Task 23.1; 2026-10-17)

Oprócz osi jezdni i budynków importer odczytuje 12 klas `OT_PT*_A` (AU-204)
oraz `OT_SULN_L`. Tę ostatnią klasę potwierdzono w niezmienionych paczkach
Krakowa (1261: 607 linii), Legnicy (0262: 154) i Bielska-Białej (2461: 291).
`rodzaj` rozróżnia linie elektroenergetyczne średniego, wysokiego i najwyższego
napięcia. Nie wyprowadzamy napięcia w kV ani pasów ochronnych z tych nazw.
Geometrie `gml:Curve/LineStringSegment` są w EPSG:2180 (wschód,północ).

Zamrożone pełne klasy SULN i wycinki dróg/budynków:
`backend/tests/fixtures/source_contracts/bdot10k_base/<powiat>/` (manifest z
SHA-256 paczki źródłowej, XML i mini-paczki). Budowanie bez sieci:

```bash
python backend/scripts/build_bdot10k_base_fixtures.py --packages <katalog-oryginalnych-ZIP> --output backend/tests/fixtures/source_contracts/bdot10k_base
```

Mini-paczki służą wyłącznie testom. Wdrożenie używa pełnych paczek powiatowych.
Importer `bdot10k-import/3` i etykieta wydania z końcówką `-v3` umożliwiają
uzupełnienie tego samego SHA-256 paczki wcześniej zaimportowanego bez klas
PT/SULN. Powtórzenie importu tym samym importerem odtwarza to samo wydanie
i liczności; stare wydania i snapshoty nie są przepisywane. Nieznany rodzaj
SULN albo brak geometrii odrzuca publikację, zamiast udawać kompletny zbiór.
Starszy wycinek bez SULN daje `class_stats.powerlines.complete=false`,
`powerline_count=null`; potwierdzony pusty zbiór ma liczność 0.

Migracja `040_bdot_powerlines` dodaje tabelę z FK do `data_releases`, geometrią
`MULTILINESTRING(2180)` i GiST. Zlecenia `bdot_county` korzystają z istniejącej
kolejki `import_jobs` (deduplikacja aktywnego zakresu, dzierżawa, trzy próby).

```bash
curl -X POST http://localhost:8000/api/v1/coverage/bdot10k/county/1261/import
# Worker kolejki bez uruchamiania harmonogramu krajowego POG:
python -m app.modules.imports pog-sync --once --no-schedule
```

Ekran „Pokrycie → Pokrycie BDOT10k (powiaty)” udostępnia zlecenie i pokazuje
liczności czterech grup danych. `GET /api/v1/coverage` oraz `/coverage/bdot10k`
wykazują liczności; awaria odczytu daje 503 i stan nieustalony. Dróg i budynków
nadal dotyczą opisane wyżej kontrakty analizy, UI, PDF i eksportu. Analiza
pokrycia terenu ma osobny kontrakt `land_cover_contracts.md`; Task 23.1 dostarcza
podstawę wektorową linii, a nie interpretację stref prawnych sieci energetycznej.
Licencja i atrybucja pozostają zgodne z katalogiem: [oficjalny opis BDOT10k](https://www.geoportal.gov.pl/pl/dane/baza-danych-obiektow-topograficznych-bdot10k/).
