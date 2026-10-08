# Kontrakty i dostępność danych MPZP (BK-201)

Stan weryfikacji: **2026-09-24**. Dokument obejmuje wszystkie gminy z
`backend/tests/fixtures/reference_corpus/manifest.json`. Maszynowym źródłem
macierzy jest
`backend/tests/fixtures/source_contracts/mpzp/municipality_matrix.json`.

## Reguła wyboru kanału

Importer przyjmuje wyłącznie jedną z czterech klasyfikacji:

| Klasyfikacja | Znaczenie | Dozwolona ścieżka |
|---|---|---|
| `vector_zones` | kontrakt zawiera wielokąty wydzieleń przeznaczenia terenu | obliczenia po transformacji do EPSG:2180, jeśli warunki ponownego wykorzystania pozwalają na publikację |
| `act_boundary_document` | dostępna jest granica aktu, metadane, dokument lub georeferencjonowany rysunek | discovery i ręczny odczyt; bez automatycznej metryki stref |
| `raster` | dostępny jest rysunek rastrowy lub WMS | podgląd i udokumentowany odczyt ręczny; WMS nie jest geometrią obliczeniową |
| `unknown` | nie potwierdzono wiarygodnego kontraktu maszynowego | `unknown`/`manual_review`, bez domyślnego `typeName` |

Nazwa warstwy, obecność słowa „wektor” w stylu WMS ani granica aktu nie
zmieniają klasyfikacji. `DataSourceEntry.mpzp_classification` jest walidowane,
a `ensure_mpzp_vector_zones_source()` zatrzymuje importer przed odczytem, gdy
źródło nie ma jawnego zasobu `zones` w WFS, APP/GML lub pliku wektorowym.

## Potwierdzone kontrakty wspólne

### Rejestr Urbanistyczny MPZP

- WFS 2.0.0:
  `https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs`;
- CRS: EPSG:2180; format schematu: GML 3.2 / APP 3.0;
- `typeNames`: `app-mpzp:AktPlanowaniaPrzestrzennego`,
  `app-mpzp:DokumentFormalny`,
  `app-mpzp:RysunekAktuPlanowaniaPrzestrzennego`;
- `Fees`: „Brak ograniczeń w publicznym dostępie”;
  `AccessConstraints`: „Brak warunków dostępu i użytkowania”;
- **brak warstwy wydzieleń przeznaczenia terenu**.

Publiczne uprawnienie do odczytu jest potwierdzone oddzielnie od zakresu
technicznego. Źródło jest produkcyjne dla granicy aktu, dokumentów i
provenance, ale ma klasyfikację `act_boundary_document` i nie może zasilać
obliczeń stref.

### KIMPZP GUGiK

`https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego`
jest usługą **WMS**. Nazwy `wektor-pow`, `wektor-lin` i podobne są nazwami
warstw obrazu zwracanego przez WMS. Nie są `typeName` WFS i nie udostępniają
geometrii cech do obliczeń. KIMPZP służy do podglądu, GetFeatureInfo i
odnalezienia dokumentu źródłowego.

Oficjalny komunikat Geoportal.gov.pl z 2026-07-06 zapowiada utrzymanie usług
integracyjnych GUGiK tylko do końca okresu przejściowego we wrześniu 2026 r.
Dlatego konfiguracja KIMPZP jest kontraktem dynamicznym i musi zostać
sprawdzona bezpośrednio przed każdym wdrożeniem.

Odpowiedź GetFeatureInfo warstwy `plany_granice` to sklejone odpowiedzi usług
gminnych (separator `<hr/>`) w kilku formatach: bloki „Obowiązujące MPZP” z
tabelami zmian, tabele atrybutów Esri/GeoServer, pary `<th>`/`<td>`, warstwy
QGIS Server oraz komunikaty „brak serwisu dla wskazanego obszaru” (gmina poza
KIMPZP → `no_coverage`) i „<gmina>: brak wyniku…” (`no_match`). Błędy usług
gminnych (`<oms_error>`, `ServiceExceptionReport`) mają HTTP 200 i są
`unavailable`, nie „brakiem planu”. Wynik to lista aktów w punkcie z ich
zmianami; przy kilku aktach system nie wybiera dokumentu (ADR-016, AU-004).
Zamrożone odpowiedzi 10 gmin: `backend/tests/fixtures/source_contracts/kimpzp/`.

### Kraków MSIP

WFS 2.0.0 zawiera granice obowiązujących planów i trzy warstwy przeznaczeń w
EPSG:2178. Kontrakt techniczny potwierdzają zamrożone GetCapabilities i
DescribeFeatureType. Regulamin MSIP wymaga jednak zgody administratora na
ciągłe, zorganizowane dalsze udostępnianie fragmentów serwisu. Do zapisania
takiej zgody źródło `mpzp_pilot_krakow` pozostaje `contract_required`, działa
wyłącznie jako jawny dry-run na lokalnych fixtures i nie publikuje wyniku.

## Macierz gmin korpusu

`Liczba RU` jest wynikiem WFS `resultType=hits` po przestrzeni nazw zawierającej
TERYT gminy. Zero znaczy wyłącznie „brak rekordu w tej obserwacji RU”. Nie jest
dowodem braku obowiązującego MPZP.

| Gmina (TERYT urzędowy) | Przypadki | RU | JST / BIP | KIMPZP | Wynik | Produkcja |
|---|---:|---|---|---|---|---|
| Warszawa (146501) | 4 | 14 aktów; granica+dokument+rysunek | mapa i dokumenty, brak zamrożonego kontraktu stref | WMS; 0 wierszy rejestru | `act_boundary_document` | RU tylko discovery |
| Kraków (126101) | 4 | 7 aktów; granica+dokument+rysunek | WFS granic i wydzieleń | WMS; 0 wierszy rejestru | `vector_zones` | nie, brak zgody na dalsze udostępnianie |
| Bielsko-Biała (246101) | 7 | 8 aktów; granica+dokument+rysunek | i.Mapa ma charakter informacyjny; brak kontraktu pobierania stref | WMS; 0 wierszy rejestru | `act_boundary_document` | RU tylko discovery |
| Legnica (026201) | 5 | 0 aktów | SIP poglądowy i BIP; brak kontraktu stref | WMS; 0 wierszy rejestru | `unknown` | nie |
| Pisz (281603) | 5 | 0 aktów | uchwały i załączniki BIP; brak kontraktu stref | WMS; 0 wierszy rejestru | `unknown` | nie |
| Czarny Bór (022104) | 4 | 0 aktów | dokumenty gminne/BIP; brak kontraktu maszynowego | WMS; 0 wierszy rejestru | `unknown` | nie |
| Ruciane-Nida (281604) | 1 | 0 aktów | uchwałę trzeba potwierdzić dla konkretnego planu | 32 rastrowe, 0 wektorowych | `raster` | podgląd/manual review |

Pełne pola macierzy obejmują: wynik każdego kanału RU/JST/KIMPZP/BIP, status
aktu, obecność granic i stref, `typeNames`, CRS, format, datę weryfikacji,
warunki licencyjne, fallback oraz URL i SHA-256 dowodu.

## Postępowanie bez wektora stref

1. Zlokalizować działkę w EPSG:2180 i użyć RU lub KIMPZP tylko do wskazania
   kandydującego aktu.
2. Potwierdzić w BIP lub dzienniku urzędowym numer uchwały, datę wejścia w życie,
   zmiany i uchylenia. Status mapy nie zastępuje statusu prawnego uchwały.
3. Otworzyć dokument źródłowy i rysunek. Zapisać URL, datę dostępu, stronę lub
   arkusz, symbol oraz sposób ręcznego odczytu.
4. Jeżeli dostępny jest tylko raster, nie polygonizować koloru jako
   autorytatywnej granicy. Wartości powierzchniowe pozostawić `unknown`, chyba
   że osobna procedura ground truth dopuszcza i opisuje ręczny pomiar wraz z
   tolerancją.
5. Brak rekordu, awaria i brak obowiązującego planu to trzy różne wyniki.
   Zachować `null`, status i provenance; nie zastępować ich zerem.

## Odświeżenie przed wdrożeniem

1. Pobrać ponownie GetCapabilities i DescribeFeatureType wszystkich
   skonfigurowanych WFS oraz GetCapabilities KIMPZP/RU WMS.
2. Porównać SHA-256, wersję protokołu, namespace, CRS, formaty, `typeNames`,
   `Fees` i `AccessConstraints` z fixtures.
3. Powtórzyć `resultType=hits` dla siedmiu TERYT i zaktualizować macierz tylko
   po ręcznym sprawdzeniu rozbieżności.
4. Sprawdzić regulamin właściciela niezależnie od technicznej dostępności
   endpointu. Nowe źródło pozostaje nieprodukcyjne do potwierdzenia prawa do
   zakładanego ponownego wykorzystania.
5. Uruchomić testy kontraktowe. Nie wdrażać po zmianie kontraktu, dopóki nowy
   artefakt, SHA i decyzja klasyfikacyjna nie przejdą przeglądu.

## Artefakty i odtworzenie

Zamrożone pliki znajdują się w
`backend/tests/fixtures/source_contracts/mpzp/`. Obejmują pełne RU
GetCapabilities i DescribeFeatureType, siedem odpowiedzi `hits` oraz siedem
filtrowanych stron urzędowego rejestru KIMPZP. Pliki nie zawierają danych
osobowych.

Walidacja offline:

```bash
cd backend
pytest tests/test_data_sources.py tests/test_imports_composition.py -q
```

Scenariusz końcowy sprawdza jednocześnie macierz korpusu, SHA dowodów,
`typeNames` względem zamrożonych kontraktów oraz guard importera. Oczekiwany
wynik: RU i WMS są odrzucone jako źródło `vector_zones`, a krakowski WFS jest
wybierany tylko w dry-run i nadal nie ma prawa publikacji.

## Odbiór 2026-09-24

Test integracyjny zmienionych ścieżek, z lokalnym PostgreSQL 16/PostGIS 3.4:

```bash
DATABASE_URL=postgresql+psycopg2://app:app@localhost:5432/dzialki \
python3 -m pytest \
  tests/test_imports_mpzp.py tests/test_data_sources.py \
  tests/test_imports_composition.py -q \
  --cov=app.core.data_sources \
  --cov=app.modules.imports.composition \
  --cov=app.modules.imports.application.mpzp_import \
  --cov-report=term-missing --cov-fail-under=80
```

Wynik: **67 passed**, pokrycie wskazanych modułów **87,45%**.

Pełna bramka backendu została wykonana w świeżej bazie, po automatycznym
przejściu wszystkich migracji, w obrazie zbudowanym z `backend/Dockerfile`:

```bash
docker compose run --rm \
  -v "$REPO_ROOT:/repo:ro" \
  -e REPO_ROOT=/repo \
  backend \
  pytest -m 'not docker_cli' \
    --cov=app --cov-report=term-missing --cov-fail-under=80 -q
```

Wynik: **1358 passed, 3 deselected**, globalne pokrycie aplikacji
**89,84%**. Nie zmieniano frontendu, migracji ani kontraktu HTTP API.

## Wektor stref w analizie działki (BK-202/BK-203)

Źródło `vector_zones` po imporcie zasila analizę bezpośrednio z PostGIS:
`find_mpzp_zone_intersections` wybiera wydzielenia wersji aktów obowiązujących
w chwili `as_of` analizy (opcjonalnie w jednym `data_release_id`) i liczy
`ST_Intersection`/`ST_Area` z pełnym obrysem działki w EPSG:2180. Każde
wydzielenie ma stabilne `zone_identifier`. Zasady wyniku, fallbacku i
evidence parametrów opisuje
`docs/adr/ADR-004-mpzp-vector-zones-and-parameter-evidence.md`.

Opcjonalne pola `field_mapping` zasobów MPZP:

| Klucz | Znaczenie |
|---|---|
| `zone_identifier` | identyfikator obiektu wydzielenia nadany przez źródło; bez niego ID jest deterministyczne z aktu, symbolu i SHA-256 geometrii |
| `document_url` | adres uchwały wersji aktu; parser dostaje symbole stref z geometrii tego aktu |

Bez dodatniego przecięcia z wektorem analiza pozostaje przy discovery
KIMPZP/dokumencie albo odczycie ręcznym, z `assignment_method`
`document_candidate`/`manual_user_input` i confidence nie wyższym niż 0,5.
Takie strefy mają udział powierzchniowy `null` (nieustalony, nie 100%) i
`is_dominant=false` (BK-204).

## Tryb ręczny bez wektora (BK-204)

Decyzje: `docs/adr/ADR-005-manual-mpzp-zone-and-mpzp-pog-compatibility.md`.

| Etap | Kontrakt |
|---|---|
| Wstrzymanie | `status=waiting_for_user_input` (baza: `waiting_for_zone_symbol`), `manual_zone_context` z `plan_id`, `candidate_zone_symbols`, `document_status`, `document` (SHA-256, `preview_path`) |
| Przypięcie | uchwała pobrana przy wstrzymaniu → `analysis_pending_documents` + `DocumentVersion`; błąd pobrania → `MPZP_PENDING_DOCUMENT_UNAVAILABLE` |
| Podgląd | `GET /analyze/{id}/pending-document` (kopia przypięta, `nosniff`, HTML tylko jako załącznik z CSP `sandbox`) i kafle `/api/v1/map/tiles/mpzp/...` |
| Walidacja | 1–20 znaków po obcięciu spacji, `^[A-Za-z0-9ĄąĆćĘęŁłŃńÓóŚśŹźŻż._/-]+$`, identycznie w UI i API (422) |
| Resume | parsowanie wyłącznie przypiętego artefaktu (SHA → 503 przy niezgodności), `FOR UPDATE` + ponowny test statusu (409), jeden commit; brak ponownego pobrania |
| Wynik | `partial`, `assignment_method=manual_user_input`, udział `null`, `manual_review_required=true` strefy i każdego parametru, `manual_selection` z decyzją i SHA dokumentu |
| Raport | baner „symbol strefy podano ręcznie”, decyzja użytkownika, dokument i „udział nieustalony” |

Punkt 4 procedury „Postępowanie bez wektora stref” (wartości powierzchniowe
`unknown`) jest więc egzekwowany przez kontrakt API, a nie tylko zalecany.
