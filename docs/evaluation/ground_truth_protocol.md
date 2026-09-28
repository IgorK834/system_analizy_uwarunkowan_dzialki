# Protokół wyznaczania ground truth (BK-003)

## 1. Cel i zasada niezależności

Ten dokument określa, jak ustalić wartość referencyjną bez uruchamiania
analizatora aplikacji. Ground truth powstaje z urzędowego źródła, zamrożonego
artefaktu, ręcznej inspekcji i niezależnego pomiaru przestrzennego. Kod
produkcyjny, jego cache, modele bazy aplikacji i wynik endpointu `/analyze` nie
są wejściem tej procedury.

Każda zaakceptowana wartość ma: identyfikator przypadku, ścieżkę metryki,
źródło i jego wydanie, datę obserwacji, narzędzie z wersją, CRS, jednostkę,
tolerancję ustaloną przed pomiarem, artefakt dowodowy oraz metodę
rozstrzygnięcia. Brak któregokolwiek z tych elementów unieważnia rekord.

## 2. Zamrożone środowisko

Referencyjny profil stanowiska GIS jest zdefiniowany następująco:

| Składnik | Wersja / ustawienie | Rola |
|---|---|---|
| QGIS | **3.44.8 „Solothurn”** | ręczna inspekcja, pomiar kontrolny i podgląd topologii |
| PostgreSQL | **16.4** (`Debian 16.4-1.pgdg110+2`) | niezależne zapytania kontrolne |
| PostGIS | **3.4.3 e365945** | przecięcia, pola, odległości i predykaty |
| Shapely | **2.1.2** | druga lokalna kalkulacja na zamrożonych artefaktach |
| pyproj | **3.7.2** | transformacja z jawnym `always_xy=True` |
| CRS obliczeń | **EPSG:2180** | wszystkie pola, odległości i przecięcia |
| CRS prezentacji | **EPSG:4326** | wyłącznie GeoJSON do prezentacji |

QGIS nie jest częścią obrazu CI i nie był narzędziem w wierszach bieżącego
`verification_log.csv`. Jego wersję zamrożono dla powtarzalnej sesji ręcznej;
każdy przyszły rekord wykonany w QGIS musi podać dokładny numer z okna
**Pomoc → O programie**. Wersje PostgreSQL/PostGIS pochodzą z zachowanego
wyniku `SELECT version(), PostGIS_Full_Version()` w
`docs/evaluation/results/baseline/`. Log bieżącej kontroli podaje rzeczywiście
użyte narzędzie osobno dla każdej obserwacji.

## 3. Źródła

1. **Geometria działki:** ULDK/EGiB GUGiK. Identyfikator działki musi zgadzać
   się z identyfikatorem cechy. Nie pobiera się danych podmiotowych EGiB.
2. **POG i OUZ:** oficjalny GML albo WFS Rejestru Urbanistycznego. Status prawny
   wynika z aktu i metadanych RU, a udziały z geometrii wektorowej.
3. **MPZP:** oficjalna geometria gminy, uchwała i dokument źródłowy. KIMPZP
   służy do discovery. WMS wolno użyć tylko do podglądu; nie mierzy się z
   obrazu WMS. Dla rastra wynik wymaga ręcznego odczytu i pozostaje
   `manual_review`, dopóki nie ma dowodu z uchwały i rysunku.
4. **ISOK, GDOŚ i NMT:** oficjalna usługa oraz data pobrania. Awaria, timeout
   lub nieobsługiwany limit usługi daje `unknown`, nigdy `none` ani zero.

Adres, data pobrania, zasady ponownego wykorzystania i SHA-256 każdego
zamrożonego pliku znajdują się w
`backend/tests/fixtures/reference_corpus/manifest.json`. Artefakt dowodowy jest
identyfikowany przez pole `evidence_artifact_id` w logu weryfikacji.

## 4. Kolejność transformacji i pomiaru

Kolejność jest obowiązkowa i nie może zależeć od wyniku aplikacji:

1. zapisz surowy identyfikator, CRS zadeklarowany przez źródło, datę pobrania
   i SHA-256 odpowiedzi;
2. sprawdź poprawność geometrii w CRS źródła; naprawę (`make_valid`) wykonaj
   tylko po zapisaniu oryginału i odnotuj ją w uwagach;
3. dla danych innych niż EPSG:2180 utwórz transformację
   `Transformer.from_crs(source_crs, "EPSG:2180", always_xy=True)`;
4. podawaj współrzędne zawsze jako **X, Y**: długość/easting, potem
   szerokość/northing; w QGIS ustaw kolejność osi projektu na XY;
5. przetransformuj geometrię do EPSG:2180 i ponownie sprawdź jej poprawność;
6. wykonaj `intersection`, `touches`, `within` i pomiary wyłącznie w
   EPSG:2180; pole zapisuj w m², odległość w m;
7. udział policz jako `100 * pole_przecięcia / pole_działki` i zapisz w
   **procentach 0–100**. Różnica udziałów ma jednostkę punktu procentowego;
8. EPSG:4326 twórz dopiero po obliczeniach i tylko na potrzeby prezentacji.

Przykład niezależnej kontroli w PostGIS:

```sql
WITH input AS (
  SELECT
    ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(:parcel_geojson), :source_srid), 2180) AS parcel,
    ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(:layer_geojson), :source_srid), 2180) AS layer
)
SELECT
  ST_Area(parcel) AS parcel_area_sqm,
  ST_Area(ST_Intersection(parcel, layer)) AS intersection_area_sqm,
  100.0 * ST_Area(ST_Intersection(parcel, layer)) / NULLIF(ST_Area(parcel), 0)
    AS intersection_percent
FROM input;
```

Jeżeli wejście jest już EPSG:2180, pomija się `ST_Transform`, ale zachowuje
jawne `ST_SetSRID(..., 2180)`.

## 5. Tolerancje ustalone przed badaniem

Tolerancji nie wolno dopasowywać po zobaczeniu różnicy.

| Klasa porównania | Pole | Odległość / położenie | Udział |
|---|---:|---:|---:|
| identyczna geometria, ten sam plik i CRS | 0 m² różnicy symetrycznej | 0 m Hausdorffa | 0 pp |
| transformacja CRS i powrót | 0,05 m² | 0,02 m na wierzchołek | 0,01 pp |
| ręczny odczyt / warstwa urzędowa o ograniczonej precyzji | 1,0 m² | 0,5 m | 0,1 pp |
| NMT | nie dotyczy | 0,1 m wysokości | nie dotyczy |

Porównanie kategorii (`inside`, `outside`, symbol strefy) jest dokładne i ma
tolerancję `0 class_mismatch`. Styk albo ślad mniejszy niż 1 m² nie jest
automatycznie zamieniany na `inside` ani `outside`; metryka relacji staje się
`ambiguous`.

Pola `area_ratio` w kontrakcie aplikacji mają skalę 0–1. Przed porównaniem z
korpusem należy je przemnożyć przez 100. Korpus, log i raport ewaluacji używają
wyłącznie procentów 0–100 oraz błędów w punktach procentowych.

## 6. Procedura dla poszczególnych typów

### Geometria działki

Otwórz lokalny GeoJSON ULDK, sprawdź identyfikator, CRS i poprawność. W QGIS
ustaw projekt EPSG:2180 i użyj kalkulatora pól `$area`/`$perimeter`; niezależnie
wykonaj `ST_Area`/`ST_Perimeter` albo Shapely `.area`/`.length`. Przykład
`real-001-146510-8-0502-1-3`: zaakceptowane pole **15 893,459 m²**.

### POG i OUZ

Wczytaj zamrożony GML/WFS RU, wybierz obiekty przez identyfikator aktu i TERYT,
wykonaj przecięcia w EPSG:2180 i pogrupuj po stabilnym identyfikatorze strefy.
Sprawdź status prawny w metadanych aktu. Przykład
`real-028-246101-1-0004-737-26`: strefa SO ma **19,65934%**, a SJ
**80,34066%**. OUZ jest liczony osobno od stref POG.

### MPZP

Najpierw potwierdź obowiązywanie uchwały i link do dokumentu, następnie użyj
oficjalnej geometrii gminy. Symbol musi pochodzić z atrybutu warstwy i być
zgodny z rysunkiem/uchwałą. Przykład `real-005-126105-9-0001-580-4`:
**KP.1, 100,0%**. Wynik tylko z GetFeatureInfo WMS pozostaje
`vector_discovery` lub `raster_manual`, a udział pozostaje `null`.

### ISOK

Przetnij każdą klasę zagrożenia oddzielnie; klasy mogą na siebie zachodzić,
więc nie sumuj ich do 100%. Przykład `real-030-026201-1-0009-578-2`:
pierwsze przecięcie ma **4 728,919 m²**. Sam styk jest osobnym faktem i nie
otrzymuje dodatniego pola.

### GDOŚ

Przetnij każdą formę ochrony oddzielnie i zachowaj jej typ oraz nazwę.
Przykład `real-025-281603-4-0001-431-66`: przecięcie z obszarem
„Puszczy i Jezior Piskich” ma **1,574 m²**. Dodatniej wartości nie zaokrągla się
do braku ograniczenia.

### NMT

Zapisz minimalną i maksymalną wysokość z oficjalnej usługi, a deniwelację
policz niezależnie jako `maximum - minimum`. Przykład
`real-009-246101-1-0056-155-3`: **319,5 m − 317,6 m = 1,9 m**.

## 7. Druga sesja i rozstrzyganie

Dla korpusu o rozmiarze `N` należy przed losowaniem ustalić seed, a następnie
powtórzyć co najmniej `ceil(0.2 * N)` różnych przypadków. Dla obecnych 30
przypadków minimum wynosi 6; log zawiera 7. Dla N=24 minimum wynosi 5.

Druga obserwacja:

1. odbywa się w innym `session_id`, po zamknięciu materiałów z pierwszej sesji;
2. zaczyna od artefaktu źródłowego, nie od wartości `expected` ani raportu
   analizatora;
3. używa drugiego narzędzia albo niezależnie powtórzonego procesu;
4. zapisuje własną wartość przed porównaniem;
5. rejestruje bezwzględną rozbieżność, wynik tolerancji, decyzję i jej powód;
6. może mieć drugiego recenzenta w kolumnie `reviewer`; puste pole oznacza tę
   samą osobę w odrębnej sesji i nie unieważnia rekordu.

Przy różnicy w tolerancji przyjmuje się wartość o większej rozdzielczości albo
wartość źródłową zaokrągloną według schematu. Przy różnicy ponad tolerancję nie
uśrednia się obserwacji: trzeba wrócić do źródła, sprawdzić CRS i wydanie, a
następnie wykonać trzecią kontrolę i opisać decyzję.

## 8. `ambiguous`, `unknown`, `null` i kompletność

- `unknown` oznacza brak wystarczającego dowodu lub niedostępne źródło.
- `null` oznacza brak ustalonej wartości; nie wolno zastępować go zerem.
- `ambiguous` dotyczy **wyłącznie wskazanej metryki** w `ambiguity_scope`.
- przypadek z jedną metryką `ambiguous` pozostaje w liczbie wszystkich
  przypadków oraz w mianowniku kompletności;
- metryka `ambiguous` nie wchodzi tylko do mianownika accuracy/MAE dla tej
  konkretnej metryki;
- pozostałe metryki tego samego przypadku są nadal oceniane.

Przykład GT-004 zachowuje pole przecięcia OUZ **0,158 m²**, lecz relacja
`ouz.relation` pozostaje nieokreślona. Cały przypadek nie jest usuwany.

## 9. Walidacja i artefakty odbiorowe

Plik `docs/evaluation/verification_log.csv` przechowuje obie obserwacje.
Walidator odrzuca brak jednostki, daty, źródła, wydania, narzędzia, tolerancji,
artefaktu, drugiej sesji, zapisanej rozbieżności lub uzasadnienia. Sprawdza też
próbę 20% i zakaz wymuszonego `resolved_value` dla metryki `ambiguous`.

```bash
cd backend
python -m pytest tests/test_reference_corpus.py -q
```

Testy działają bez sieci. Dowodami są manifest i artefakty korpusu, ten
protokół, `verification_log.csv` oraz raporty w `docs/evaluation/results/`.
