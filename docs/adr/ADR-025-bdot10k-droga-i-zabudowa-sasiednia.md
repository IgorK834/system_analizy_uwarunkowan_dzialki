# ADR-025 — BDOT10k jako źródło drogi i zabudowy sąsiedniej: wydania powiatowe, statusy rozłączne, brak pola prawnego

Status: decyzje techniczne przyjęte 2026-10-13 (Task 4.4/BK-304, 4.5/BK-305, 23.1–23.3; issues #226, #227, #307, #308, #309).
**Wartości wymagające decyzji właściciela danych/produktu są oznaczone „Proponowany” (sekcja „Decyzje do potwierdzenia”) i działają w wariancie ostrożnym, odwracalnym.**
Powiązane: [ADR-001](ADR-001-modular-monolith.md) (granice modułów), [ADR-008](ADR-008-pog-release-complete-snapshot.md) (wydanie jako kompletny snapshot), [ADR-010](ADR-010-report-v2-and-deterministic-maps.md) (raport v2),
[ADR-011](ADR-011-section-quality-matrix-and-audit-package.md) (macierz jakości i pakiet audytowy), [ADR-015](ADR-015-api-errors-request-id-and-text-column-policy.md) (kolumny tekstowe),
[ADR-022](ADR-022-definicja-complete-i-status-naglowkowy.md) (definicja „complete”). Kontrakty: [road_contracts.md](../data_sources/road_contracts.md), [building_contracts.md](../data_sources/building_contracts.md).
Odbiór: [bdot10k-road-buildings-verification.md](../evaluation/bdot10k-road-buildings-verification.md).

## Kontekst

Backlog zakładał dwa osobne źródła: najbliższą drogę (BK-305, początkowo „BDOT10k WFS”) i budynki sąsiednie (BK-304, „EGiB przez KIEG”). Weryfikacja kontraktów 2026-10-13 (spike SW1):

* WFS `PobieranieBDOT10k` zwraca wyłącznie **katalog paczek powiatowych** — obiektów topograficznych (drogi, budynki) nie udostępnia. Jedyną ścieżką jest paczka ZIP powiatu (GML 3.2, EPSG:2180).
* KIEG (EGiB) na żądanie `SERVICE=WFS` odpowiada dokumentem **WMS_Capabilities**; warstwa `budynki` jest niepytalna (`queryable="0"`, skala ≤ 1:2500), a `GetFeatureInfo` zwraca atrybuty działki bez geometrii budynku; warunki usługi
  **zabraniają automatycznego pobierania** informacji opisowych. Nie istnieje krajowy, wektorowy i otwarty kanał budynków EGiB.
* Dotychczasowy model wydań (`data_releases`) dopuszczał **jedno aktywne wydanie na źródło**, a paczka BDOT10k obejmuje jeden powiat — import drugiego powiatu wygasiłby pierwszy.
* Wcześniejsza wersja kodu drogi zakładała jeden plik i jeden obszar (fixture Krakowa) oraz zwracała tylko „najbliższą drogę albo nic”, więc „nic” było nierozróżnialne od braku pokrycia i błędu.

## Decyzje

1. **Jedno źródło `bdot10k` dla drogi i zabudowy.** Z jednej paczki powiatowej importujemy trzy klasy: `OT_SKJZ_L` (osie jezdni), `OT_BUBD_A` (obrysy budynków) i `OT_ADJA_A` (obiekt `powiat` jako zasięg wydania).
   Budynki z EGiB/KIEG są **odrzucone** jako kanał (`kieg_wms` = `research`, `egib` pozostaje `no_redistribution`). Obrys BDOT10k nie jest obrysem ewidencyjnym — wynik jest opisany jako „kontekst istniejącej zabudowy”,
   z datą stanu danych i wydaniem; udział obiektów z geometrią z EGiB w korpusie: 85–97%.
2. **Wydanie = paczka jednego powiatu** (migracja `036_bdot10k_context`). `data_releases.scope_key` (`powiat:<TERYT>`) i unikalny indeks częściowy `(data_source_id, COALESCE(scope_key, ''))` dla `is_active` pozwalają utrzymać
   po jednym aktywnym wydaniu **na powiat**. Dotychczasowe źródła mają `scope_key IS NULL`, więc ich reguła („jedno aktywne wydanie”) się nie zmienia. Ten sam SHA-256 paczki daje to samo wydanie (idempotencja);
   ponowny import innej wersji powiatu aktywuje nowe i wygasza poprzednie tylko dla tego powiatu. Obiekty żyją w `bdot_roads` / `bdot_buildings`, zasięg w `bdot_releases.extent` (EPSG:2180, indeks GiST).
3. **Import jako polecenie CLI modułu `imports`** (`python -m app.modules.imports bdot10k --powiat <TERYT> [--package <plik|https>] [--dry-run]`). Paczka (plik lub pobranie z hosta z allowlisty, bez przekierowań, z limitem
   rozmiaru) trafia do artefaktów z SHA-256, jest czytana strumieniowo przez GDAL (pyogrio) z jawną listą kolumn i limitami `safe_archive` (rozmiar, stosunek kompresji, ścieżki). Geometrie są naprawiane (`make_valid`) do
   `MultiLineString`/`MultiPolygon` i zapisywane masowo; odrzucenia mają zliczone przyczyny. Import zapisuje statystyki per klasa i **ostrzega**, gdy liczność spadła poniżej 80% poprzedniego wydania tego powiatu
   (zmiana nie jest blokowana — to decyzja operatora).
4. **Statusy rozłączne: `available | no_match | no_coverage | unavailable | unknown`** dla obu sekcji. `no_coverage` — brak aktywnego wydania, którego zasięg zawiera punkt reprezentatywny działki
   (`NO_ACTIVE_RELEASE`/`OUTSIDE_COVERAGE`); `no_match` — pokryty obszar, w którym w ograniczonym promieniu nic nie znaleziono (**nie dowodzi braku drogi**); `unavailable` — błąd lub limit czasu/kandydatów;
   `unknown` — zapis sprzed sekcji. Błąd nigdy nie zamienia się w „brak obiektu”; pusty wynik w pokrytym obszarze dla budynków to sprawdzone `0`, a wielkości bez obiektów (minimum, mediana) są `null`, nie `0`.
   Obszar wyszukiwania wykraczający poza zasięg wydania jest oznaczony `search_area_within_coverage = false` i obniża wynik do częściowego.
5. **Droga:** pomiar od poligonu działki do **osi jezdni** w EPSG:2180 (`ST_Distance`), promienie 25 → 50 → 100 → 250 → 500 m (`ST_DWithin`, GiST), relacja `intersects | touches | disjoint`, remis w granicach 1 mm rozstrzyga
   silniejsza relacja, potem identyfikator. Wynik niesie promień, w którym znaleziono drogę, listę sprawdzonych promieni, kategorię zarządzania, klasę drogi, numer, nazwę ulicy, wersję obiektu i wydanie.
   **Nie istnieje pole „prawny dostęp do drogi publicznej” w żadnym kontrakcie** (API, cache, UI, PDF, pakiet audytowy); każda postać wyniku niesie zdanie: „Bliskość lub sąsiedztwo geometryczne nie jest potwierdzeniem prawnego
   dostępu działki do drogi publicznej.” (testy sprawdzają brak takiego pola w schemacie, odpowiedzi API, szablonie i treści PDF, pakiecie audytowym oraz w komponentach UI).
6. **Zabudowa:** pierścienie `buffer(działka, r) − działka` (okrągły, `quad_segs = 16`) dla r = 50/100/200 m, statystyki kumulatywne; budynek w pierścieniu, gdy pole jego części > 0,01 m² (unikalne identyfikatory), pole sumy
   (union) nie liczy nakładających się obrysów podwójnie; budynek wystający poza działkę liczy się na działce i w pierścieniu; wskaźnik pokrycia działki jest „wynikiem obliczenia, nie oceną prawną”. Do tabeli i mapy trafia
   do 100 budynków (na działce i w buforze 50 m); statystyki obejmują wszystkie. Przekroczenie limitu kandydatów (20 000) to `unavailable`, nie zaniżona liczba. Nie zapisujemy nazw, właścicieli ani identyfikatorów ewidencyjnych
   (pola tekstu wolnego i `identyfikatorEGiB` nie są importowane). Wysokość budynku nie istnieje w schemacie 2021 — wynik podaje liczbę kondygnacji.
7. **Przepływ analizy:** zapytania PostGIS są wykonywane w wątku roboczym na tej samej sesji z `SET LOCAL statement_timeout` (3 s droga, 5 s zabudowa) i budżetem czasu (10/15 s); błąd, brak katalogu lub wyłączenie funkcji
   daje `unavailable` z przyczyną, nie wyjątek. Wydanie jest przypięte do snapshotu analizy (`data_release_ids`), więc cache i audyt wskazują dokładnie to, z czego policzono wynik.
8. **Wersjonowanie i cache:** `road-v1.0` i `buildings-v1.0` w `RESULT_CONTRACT_VERSION` (ciąg 97 znaków; kolumna `analyses.result_contract_version` poszerzona do 256 w tej samej migracji); macierz jakości `quality-v1.1`
   (jedenaście sekcji, w tym `buildings`); reguła kompletności `completeness/1.1` — **transport i zabudowa wchodzą do zakresu** (poza zakresem pozostaje domyślnie tylko `utilities`), brak wydania jest `no_coverage`, a nie „brak drogi”,
   i obniża `complete`; układ raportu `report-v2/2026.10.13-1`; eksporter pakietu audytowego `audit-exporter/1.5.0`.
9. **Pakiet audytowy:** warstwy `road_context` (oś i odcinek działka → oś) oraz `neighbour_buildings` (obrysy bez nazw i właścicieli) są dołączane, bo katalog dopuszcza dane pochodne (`derived_only`); surowa paczka źródłowa
   nie jest redystrybuowana — pakiet zawiera jej SHA-256, wydanie i etykietę.
10. **Niezależna weryfikacja:** wartości oczekiwane 30 działek korpusu policzono Shapely z `gml:posList` (bez GDAL/PostGIS/kodu aplikacji), a drugi pomiar wykonał `ogr2ogr` z GDAL (dialekt SQLite) — **nie QGIS** (niedostępny);
    zgodność 30/30. Zamrożone mini-paczki sześciu powiatów (1,4 MB) są w `backend/tests/fixtures/bdot10k/`.

## Odrzucone alternatywy

* **KIEG/EGiB WFS jako źródło budynków** — nie istnieje (WMS-only, zakaz harvestingu). Powiatowe usługi EGiB mają niejednolite kontrakty i nie są objęte tym zadaniem.
* **Zapytania na żywo do WFS BDOT10k** — usługa nie wydaje obiektów topograficznych.
* **Jedno globalne wydanie „BDOT10k Polska”** — import całego kraju jest nieproporcjonalny do potrzeb (same paczki sześciu powiatów korpusu to ok. 217 MB); model per powiat pozwala dokładać zasięg stopniowo i jawnie mówi, gdzie danych nie ma.
* **Pole `legal_public_road_access` ze statusem „unknown”** — nawet jako `unknown` zachęcałoby do interpretacji prawnej geometrii; zamiast tego stałe zdanie i opis pomiaru.
* **Suma `OT_SKJZ_L` i `OT_SKDR_L`** — liczebnie niemal identyczne (oś jezdni i oś „drogi” jako ciągu), sumowanie podwajałoby drogi.

## Decyzje do potwierdzenia (Proponowany)

| # | Wartość / decyzja | Wariant przyjęty (ostrożny, odwracalny) | Kto rozstrzyga |
|---|---|---|---|
| D1 | Redystrybucja danych BDOT10k w pakiecie audytowym | `derived_only`: wyłącznie wyniki pochodne (oś najbliższej drogi, obrysy do 100 budynków); surowych obiektów nie eksportujemy. Zmiana na `allowed` nie wymaga kodu, tylko wpisu w katalogu. | właściciel danych / prawnik |
| D2 | Polityka świeżości danych | 365 dni liczone **od importu paczki** (`fetched_at` = `published_at` wydania; `project_decision`, zgodnie z regułami macierzy jakości). Stan danych (najnowsza `wersja` obiektu) jest podawany obok, ale **nie wchodzi do oceny świeżości**: paczka z 2024-09 zaimportowana dziś jest „świeża”. Wariant ostrzejszy (wiek od stanu danych) oznaczyłby jako przestarzałe powiaty piski (2024-09) i Warszawę (2025-06) — wymaga decyzji, bo zmienia `quality_policy_version` i rozkład `complete`. | właściciel produktu |
| D3 | Klasa osi: `OT_SKJZ_L` (oś jezdni) zamiast `OT_SKDR_L` | `OT_SKJZ_L`; pomiar jest do osi jezdni, nie do granicy pasa drogowego — podane w wyniku i w PDF. | właściciel produktu |
| D4 | Filtr istnienia | wyłącznie `kategoriaIstnienia = eksploatowany` (bez „w budowie”); obiekty w budowie nie są ani drogą, ani budynkiem w wyniku. | właściciel produktu |
| D5 | Próg istotności części budynku (0,01 m²) | 0,01 m² (jak w pomiarze niezależnym). W korpusie jedna działka (Kraków, `real-005-126105-9-0001-580-4`) ma 3 obrysy zachodzące na działkę łącznie na 0,153 m² — to najpewniej niedokładność granicy względem obrysu, a nie budynki na działce; wskaźnik pokrycia jest wtedy 0,0006%. Większy próg (np. 1 m²) usunąłby takie artefakty, ale zmieniłby definicję „na działce”. | właściciel produktu / geodeta |
| D6 | Długość listy budynków | 100 pozycji w API/mapie, 25 w PDF; reszta jest w statystykach i pakiecie audytowym. | właściciel produktu |
| D7 | Zasięg wdrożenia | Zaimportowano 6 powiatów korpusu; dla pozostałych sekcje mają `no_coverage`. Decyzja, które powiaty importować w produkcji (np. wszystkie z analizami użytkowników), należy do właściciela. | właściciel produktu |
| D8 | „Droga publiczna” | Wynik nie rozstrzyga charakteru prawnego drogi; kategorii zarządzania (`wewnętrzna`) nie interpretujemy jako braku dostępu. Źródłem prawnego dostępu jest ewidencja gruntów i księgi wieczyste — poza zakresem. | właściciel produktu / prawnik |

## Konsekwencje

* Analiza działki ma dwie nowe sekcje w API, UI, PDF i pakiecie audytowym; działki poza zaimportowanymi powiatami dostają jawne `no_coverage`, a `complete` jest wtedy nieosiągalne (zgodnie z ADR-022 — to zmiana zakresu, nie błąd).
* Baza rośnie o ok. 156 tys. osi jezdni i 350 tys. budynków dla sześciu powiatów korpusu (Warszawa to 48% budynków); import największego powiatu trwa ok. 25 s (emulacja amd64).
* Odświeżanie danych to ponowny import powiatu (nowa paczka → nowe wydanie); BDOT10k jest aktualizowany nieregularnie, a pole `Data_aktualizacji` katalogu bywa starsze od obiektów w paczce — dlatego świeżość liczymy z `wersja` obiektów.
* Niezależny pomiar GDAL jest częścią zestawu testów (pomijany, gdy brak `ogr2ogr`); frontendowe fixtures sekcji są generowane z backendu (`backend/scripts/export_bdot_fixtures.py`), a test pilnuje ich zgodności.
* Nierozwiązane: mapa PDF nie pokazuje osi drogi ani budynków (tabele i opis wystarczają do kryteriów; dodanie warstw do mapy statycznej zmieni hashe semantyczne fixtures map); brak automatycznego harmonogramu importu.
