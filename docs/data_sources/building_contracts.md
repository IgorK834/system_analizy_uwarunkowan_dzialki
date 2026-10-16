# Kontrakt źródła budynków: zabudowa sąsiednia (BDOT10k `OT_BUBD_A`; EGiB/KIEG jako kanał odrzucony)

Stan na 13 października 2026 r. (spike SW1 zadań 4.4/BK-304 i 23.3, issues #226 i #309). Wpisy katalogu: `bdot10k` (źródło
runtime) i `kieg_wms` (kanał odrzucony) w `docs/data_sources/catalog.yaml`. Droga z tej samej paczki: `road_contracts.md`.
Decyzje: `docs/adr/ADR-025-bdot10k-droga-i-zabudowa-sasiednia.md`.

## 1. Co sprawdzono i dlaczego nie EGiB/KIEG

Zadanie 4.4 (#226) zakładało oficjalny kanał budynków EGiB (KIEG WFS). Weryfikacja 2026-10-13:

| Kanał | Wynik |
|---|---|
| `https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow` — żądanie WFS (`SERVICE=WFS`, `GetCapabilities`, wersje 1.1.0 i 2.0.0) | HTTP 200, ale odpowiedź to **WMS_Capabilities** (identyczny SHA-256 `4498bb8c…` dla WFS 2.0.0, WFS 1.1.0 i WMS 1.3.0, 32 318 B); fixture `source_contracts/kieg_wfs_request_returns_wms.txt` |
| KIEG `GetFeature` (WFS) | strona HTML usługi WMS, nie dane |
| KIEG, warstwa `budynki` | `queryable="0"`; `MaxScaleDenominator 2500`; pojedyncze `GetFeatureInfo` zwraca atrybuty **działki** (Geomedia FeatureCollection) bez geometrii budynku |
| Warunki | `Fees` brak opłat; **zakaz automatycznego pobierania** obrazów i informacji opisowych z `GetFeatureInfo` (harvesting) |
| Krajowy WFS z budynkami EGiB | nie znaleziono (w tej weryfikacji sprawdzono wyłącznie KIEG); powiatowe usługi EGiB mają niejednolite kontrakty i nie są objęte tym zadaniem |

Wniosek: **nie istnieje krajowy, wektorowy i jawnie otwarty kanał budynków EGiB**, z którego dałoby się liczyć odległości i
pola. Wpis `egib` w katalogu pozostaje `no_redistribution` (dane osobowe właścicieli), a `kieg_wms` jest `research` jako
podgląd. Źródłem obliczeń są obrysy budynków BDOT10k (`OT_BUBD_A`): w sześciu paczkach korpusu **od 85% (powiat piski) do 97% (Legnica) obiektów ma geometrię z EGiB** (atrybut
`zrodloDanychGeometrycznych = EGiB`: 85,3% / 87,2% / 91,5% / 93,5% / 95,5% / 96,8% dla powiatów piskiego, wałbrzyskiego, Krakowa,
Warszawy, Bielska-Białej i Legnicy), a reszta pochodzi z ortofotomapy, pomiaru terenowego lub BDOT500. Obrys BDOT10k nie jest obrysem ewidencyjnym: bywa uogólniony i ma opóźnienie względem EGiB —
dlatego wynik jest nazwany „kontekst istniejącej zabudowy”, a data stanu danych jest podana przy wydaniu.

## 2. Kontrakt `OT_BUBD_A`

| Element | Wartość |
|---|---|
| Paczka, układ, format | jak w `road_contracts.md` (EPSG:2180, GML 3.2, bez XSD) |
| Geometria | `gml:Polygon` z `gml:exterior` i `gml:interior` (pierścienie wewnętrzne występują: od 2 w powiecie piskim do 710 w Warszawie); w sześciu sprawdzonych paczkach nie ma łuków (`gml:Arc`, `Circle`), `CompoundCurve` ani geometrii wielokrotnych |
| Atrybuty zapisywane | `lokalnyId`, `kategoriaIstnienia` (`eksploatowany`, `w budowie`, `nieczynny`, `zniszczony`), `funkcjaOgolnaBudynku`, `funkcjaSzczegolowaBudynku`, `przewazajacaFunkcjaBudynku`, `liczbaKondygnacji`, `zrodloDanychGeometrycznych`, `wersja`; pole obrysu liczy PostGIS z geometrii |
| Brak w źródle | **wysokość budynku** (schemat 2021 ma tylko liczbę kondygnacji), kubatura; wynik podaje kondygnacje, a wysokość nie istnieje (nie `0`) |
| Nie importowane | `nazwa`, `informacjaDodatkowa`, `uwagi` (tekst wolny), `identyfikatorEGiB` — wynik i eksport są bez nazw, właścicieli i identyfikatorów ewidencyjnych |
| Uwzględniane | wyłącznie `kategoriaIstnienia = eksploatowany` |

Liczności klasy w paczkach korpusu: 12 302 (Legnica), 15 543 (wałbrzyski), 27 183 (piski), 37 591 (Bielsko-Biała), 90 563
(Kraków), 167 099 (Warszawa); pełna tabela z hashami: `road_contracts.md`, sekcja 3.

## 3. Definicje obliczeń (EPSG:2180)

* **Sąsiedztwo** o promieniu `r` to pierścień `buffer(działka, r) − działka`; bufor okrągły (`quad_segs = 16`), promienie 50 /
  100 / 200 m, statystyki **kumulatywne** (pierścień 100 m zawiera pierścień 50 m). Mianownikiem udziału jest pole pierścienia.
* Budynek jest **w pierścieniu**, gdy pole jego części w pierścieniu przekracza 0,01 m²; liczymy **unikalne identyfikatory**
  (budynek raz, także gdy ma kilka części). Pole zabudowy w pierścieniu to suma pól części przyciętych do pierścienia, a
  pokrycie (union) — pole sumy tych części (nakładające się obrysy nie liczą się podwójnie).
* Budynek jest **na działce**, gdy pole jego części w działce przekracza 0,01 m². Jeśli wystaje poza granicę, liczy się także w
  pierścieniu (część zewnętrzna) i jest oznaczony „zachodzi na działkę”. Obrys stykający się z brzegiem bez zachodzenia jest
  „przylegający” (odległość 0).
* **Odległość** = `ST_Distance` od poligonu działki do obrysu; minimum, mediana i maksimum dotyczą **pełnych** obrysów
  budynków liczonych w pierścieniu. Wielkości bez obiektów są `null` (nie 0); zero budynków w pokrytym obszarze to sprawdzone 0.
* **Wskaźnik pokrycia działki** = pole sumy części obrysów w działce / pole działki — wynik obliczenia, nie ocena prawna.
* Budynki do tabeli i warstwy mapy: na działce oraz w najbliższym buforze (50 m), do 100 pozycji; statystyki obejmują wszystkie.
* Kandydaci: `ST_DWithin` w promieniu największego bufora (200 m), limit 20 000; przekroczenie to `unavailable`
  (`TOO_MANY_CANDIDATES`), nie zaniżona liczba.
* Pierścień poza zasięgiem wydania (granica powiatu): wynik niesie `search_area_within_coverage = false`; pusty wynik w takim
  przypadku to `no_match`, nie „0 budynków”.

## 4. Weryfikacja

* `expected.json` (Shapely, `gml:posList`, definicje wyżej) dla wszystkich 30 działek; testy PostGIS porównują liczności,
  pola (tolerancja 0,05 m²), udziały i odległości (0,011 m) dla trzech buforów.
* **Niezależny pomiar GDAL (nie QGIS)**: `backend/scripts/verify_bdot10k_with_gdal.py` (`ogr2ogr`, dialekt SQLite) — 30 z 30
  działek zgodnych w liczbie budynków na działce i w trzech buforach, sumie pól części w pierścieniu i minimalnej odległości
  (`docs/evaluation/results/bdot10k/gdal-cross-check.md`).
* Kontrolne geometrie po obu stronach granicy bufora (49,9 m i 50,1 m), obrysy zachodzące na działkę, nakładające się i
  zdublowane identyfikatory mają testy jednostkowe (`backend/tests/test_building_context.py`).
* Kryterium #309 „11 budynków w 50 m dla `141801_4.0701.23/8` (OnGeo)”: ta działka nie należy do korpusu referencyjnego, a
  powiat piaseczyński (1418) nie został zaimportowany, więc porównania nie wykonano (raport odbioru: niespełnione, z przyczyną).
