# ADR-023 — Discovery POG w Rejestrze Urbanistycznym przez WFS: budżet żądań, kafle, cache i oś północ,wschód

Status: przyjęta 2026-10-12 (AU-105, Task 22.5, audyt R3). Powiązane: [ADR-001](ADR-001-modular-monolith.md) (port w `application`,
adapter w `infrastructure`), [ADR-002](ADR-002-pog-legal-status-and-coverage.md) (status prawny i pokrycie POG),
[ADR-017](ADR-017-resume-token-rate-limit-and-single-flight.md) (single-flight analiz, AU-007 — blokada zadania),
[ADR-020](ADR-020-mpzp-acts-ru-channel-precedence-and-state.md) (ten sam wzorzec adaptera RU dla aktów MPZP).

## Kontekst

Jedna analiza wykonywała 46–288 żądań `GetFeatureInfo` do WMS Rejestru Urbanistycznego (2–6 punktów próbki × ~48 warstw `APP.POG.*`; pomiar na
korpusie 30 działek z 2026-10-12: **4 967 żądań na 30 analiz**, w logach audytu 384 na 2 analizy). Przy limicie 20 analiz/min to tysiące zapytań
na minutę do usługi rządowej — ryzyko blokady adresu i wizerunkowe. Pomiary na żywej usłudze (2026-10-12) ujawniły ponadto **błąd poprawności**, nie tylko
koszt:

1. **Kolejność osi.** RU czyta `bbox` dla `EPSG:2180` w kolejności **północ, wschód** — i w WMS 1.3.0, i w WFS 2.0 (`urn:ogc:def:crs:EPSG::2180`), i tak samo
   zwraca `gml:posList` (to samo ustalenie co w ADR-020 dla `app-mpzp`). Dotychczasowy klient WMS miał w tabeli osi `EPSG:2180 → xy` i wysyłał wschód, północ,
   czyli **lustrzane współrzędne**. Dowód: zapytanie o punkt (E=503240, N=217270) w kolejności `x,y` zwraca obowiązujący POG gminy **Rzepin** (Lubuskie), w kolejności
   `y,x` — „Plan ogólny Bielska-Białej”, czyli właściwą gminę tej działki. Dla Bielska-Białej wynik zgadzał się przypadkiem; dla innych działek nie.
2. **RU zwraca jeden typ na żądanie WFS.** Parametr `typeNames` z listą typów daje wyłącznie pierwszy; strony mają **10 obiektów niezależnie od `count`**
   (`count=100` i `count=1000` zwracają 10). `PROPERTYNAME` na typie aktu kończy się błędem, więc nie da się pominąć ciężkiej geometrii granicy aktu
   (Bielsko-Biała: 303 kB GML).
3. RU filtruje po **obwiedni** obiektu — wielokąt zwróconego obiektu może nie dotykać `bbox`.

## Decyzja

1. **Discovery przez WFS `app-pog`, nie WMS.** `services/pog._discover_from_catalog` pobiera równolegle cztery typy
   (`AktPlanowaniaPrzestrzennego`, `ObszarZabudowySrodmiejskiej`, `ObszarUzupelnieniaZabudowy`, `StrefaPlanistyczna`) — **po jednym zapytaniu `GetFeature` z `bbox`
   kafla na typ**. Czas discovery: 0,1–0,7 s na analizę (jednorazowo 12 s przy pierwszym, zimnym połączeniu; przed: 1,1–3,7 s przy do 288 równoległych żądaniach, a w pełnej analizie działek Krakowa 34–56 s). WMS RU służy wyłącznie do
   podglądu mapy; discovery nie ma awaryjnego powrotu do WMS (błąd WFS to `unknown` / `source_responded=false`, nie „brak POG”). WMS gminnych źródeł
   (`PogGminaSources.wms_url`) działa jak dotąd — zmiana dotyczy tylko kanału katalogowego RU.
2. **Port i adapter (ADR-001).** `RuPogFeaturesSource` (`modules/planning/application/ports.py`) + adapter `RuPogFeaturesAdapter`
   (`infrastructure/ru_pog_features.py`) na wspólnym `OgcClient` (HTTPS, allowlista hostów, limity bajtów, ponowienia). Domena
   (`domain/pog_discovery.py`) jest czysta: kafle, rekordy obiektów i `observe_layer`. Fabryka `composition.ru_pog_features_source()` przechodzi guard katalogu
   (`ensure_source_runnable("pog_app")`); wyłącznik: `RU_POG_DISCOVERY_ENABLED`.
3. **Kafel.** Obwiednia działki zaokrąglona **na zewnątrz** do siatki 50 m w EPSG:2180 (`snap_bbox_outward`). Przynależność obiektu do działki rozstrzyga lokalne
   przecięcie w EPSG:2180 (próg 0,5 m² — szum digitalizacji granicy), a nie obwiednia z odpowiedzi. Geometria obiektu jest **przycinana do kafla** przed
   zapamiętaniem, więc pamięć cache zależy od rozmiaru kafla, nie aktu (granica aktu ma setki kB).
4. **Budżet żądań.** `RU_POG_REQUEST_BUDGET` (domyślnie **6**) liczy każde żądanie HTTP analizy, także ponowienie i kolejną stronę. Licznik jest
   wołany przed wysłaniem (`OgcClient(before_request=…)`), więc po wyczerpaniu żądanie **nie opuszcza procesu**. Wynik: warstwa `unavailable` (nigdy `empty`),
   `source_responded=false`, gdy nic nie znaleziono, ostrzeżenie `POG_SOURCE_UNAVAILABLE` / `POG_RU_LAYER_UNAVAILABLE` i **alarm**: licznik `ru_pog.budget_exceeded`
   w `GET /health/upstream` oraz zdarzenie analizy `pog_ru_discovery status=budget_exceeded`. Typowa analiza zużywa 4 żądania (28 z 30 działek korpusu), pozostałe 5
   (strefy w dwóch stronach po 10); budżet 6 zostawia 2 żądania na ponowienie albo kolejną stronę. Awaria RU (5xx) zużywa budżet na ponowienia (4 warstwy × 2 próby > 6),
   więc alarm budżetu pojawia się także podczas awarii usługi — to zamierzone: operator widzi, że analiza przestała odpytywać RU.
5. **Cache zapytań i deduplikacja.** Klucz: (typ obiektu, kafel). TTL **24 h** (`RU_POG_CACHE_TTL_SECONDS`), LRU do `RU_POG_CACHE_MAX_ENTRIES` wpisów. Równoległe analizy
   tego samego kafla wysyłają **jedno** żądanie (lider pobiera, reszta czeka na wynik, `DEDUP_WAIT_SECONDS` = 60 s); błąd lidera nie jest zapamiętany, a czekający pobierają
   sami. Błąd źródła jest zapamiętywany krótko (60 s) — awaria RU nie powoduje lawiny ponowień; wynik niepełny i wyczerpany budżet nie są zapamiętywane wcale.
   Cache jest w pamięci procesu (jak kafle POG): po restarcie zaczyna od zera, a przy N procesach każdy ma własny widok; analizy tej samej działki są i tak
   serializowane przez single-flight (AU-007). `fetched_at` w provenance to chwila pobrania z usługi, nie odczytu z cache.
6. **Stan sekcji.** Pusta odpowiedź działającej usługi to `empty` (`POG_NOT_FOUND_IN_SOURCE`, „nie oznacza braku planu”); błąd, odpowiedź niepełna, obiekt bez użytecznej
   geometrii albo geometria z zamienionymi osiami (`POG_RU_GEOMETRY_AXIS_SUSPECT`) to `unavailable`. Brak odpowiedzi jednej warstwy nie unieważnia znalezisk w pozostałych
   (`POG_RU_LAYER_UNAVAILABLE`), ale bez znaleziska nie wolno uznać, że usługa „odpowiedziała pusto”.
7. **Kontrakt.** `POG_RESULT_SCHEMA_VERSION` 2.4 → **2.5** (zmiana semantyki i źródła: wyniki 2.4 mogły być fałszywe z powodu osi i nie są serwowane z cache),
   `raw_attributes.discovery.layers[*]` niesie `matched_wfs_type` (`matched_wms_layer` jest `null`), `raw_legal_status` to URI kodu INSPIRE z atrybutu `status`
   (np. `…/ProcessStepGeneralValue/legalForce`), `source_name` = `REJESTR_URBANISTYCZNY_WFS`. `uchwala_nr`, `uchwala_date` i `links` pozostają puste jak dotąd — wektory
   APP/GML (`fetch_pog_vector_data`) wymagałyby odnośnika i kolejnych żądań poza budżetem.

## Wynik pomiaru na korpusie referencyjnym (30 działek, żywy RU, 2026-10-12)

| | Przed (WMS) | Po (WFS) |
|---|---:|---:|
| Żądania HTTP do `rejestr-urbanistyczny.gov.pl`, suma | 4 967 | 123 (w tym jedno ponowienie po wolnej odpowiedzi) |
| Na analizę, min–max | 46–288 | 4–5 |
| Czas discovery na analizę (mediana, zakres) | 2,3 s (1,1–3,7 s) | 0,13 s (0,11–0,65 s; jedna analiza z ponowieniem 12,4 s) |

Zamrożone dowody: `backend/tests/fixtures/pog_discovery/legacy_baseline.json` (stan przed), `backend/tests/fixtures/source_contracts/ru_pog_features` (122 odpowiedzi
WFS, bajt w bajt), `tests/test_pog_discovery_baseline.py` (porównanie), skrypty `scripts/capture_pog_discovery.py` i `scripts/capture_ru_pog_fixtures.py`.

### „Wyniki POG dla działek ze statusem `binding` identyczne jak przed zmianą” — wynik i zastrzeżenie

Porównanie z zamrożonym stanem „przed” (16 działek miało `binding`, w opisie zadania 12) daje **cztery grupy**; różnice wynikają z błędu osi opisanego wyżej, a nie z nowego
silnika. Dla każdej grupy prawdę terenową liczy niezależny parser (czysty `ElementTree` + Shapely, bez kodu produkcyjnego) z tych samych zamrożonych odpowiedzi RU:

| Grupa | Działki | Przed → po | Uwagi |
|---|---:|---|---|
| `binding` bez zmian | 7 (Bielsko-Biała) | `binding` → `binding` | pola kontraktu POG identyczne (status prawny, kod statusu, uchwała, odnośniki, `source_responded`, warstwy akt i strefy) |
| `unknown` bez zmian | 9 (Warszawa, gmina 022104, 281604) | `unknown` → `unknown` | RU nie ma tam obiektów POG |
| fałszywe `binding` poprzedniego silnika | 9 (Kraków, gmina 281603) | `binding` → `unknown` | RU nie ma żadnego obiektu POG dla tych działek; poprzedni silnik odpytywał lustrzany punkt i trafiał w POG innej gminy |
| przeoczone `binding` | 5 (Legnica) | `unknown` → `binding` | RU ma akt, strefy i obszar zabudowy śródmiejskiej obowiązujące |

Kryterium „identyczne” jest więc spełnione tam, gdzie poprzedni wynik był poprawny (7 z 7 działek, dla których `binding` nie było artefaktem osi), a nie spełnione — z
uzasadnieniem — dla 9 działek, gdzie wynik „przed” był błędny. Po zmianie `binding` ma **12** działek korpusu (7 + 5; live smoke 2026-10-12) — liczba z opisu zadania odpowiada wynikowi po korekcie osi,
nie stanowi zbioru „przed” (16 działek w przebiegu z 2026-10-11, 9 w przebiegu z 2026-10-08, w obu przypadkach z fałszywymi trafieniami Krakowa i gminy 281603). **Odtworzenie błędnego wyniku dla zgodności z liczbą nie jest rozwiązaniem**; ta decyzja czeka na
potwierdzenie właściciela (patrz niżej).

## Konsekwencje

- Ruch do RU spada ~40-krotnie; analiza gminy bez lokalnego wydania nie obciąża już usługi rządowej. W razie awarii RU analiza degraduje się jawnie (`unknown`).
- 5 działek korpusu (Legnica) dostaje poprawny status `binding` + pokrycie `partial` (`POG_VECTOR_LINK_MISSING`), 9 działek traci fałszywy `binding`. Cache analiz jest
  unieważniony (kontrakt 2.5).
- Odpowiedź WFS dla kafla zawiera **geometrię stref w EPSG:2180**, więc analiza powierzchniowa POG na żywych danych RU (udziały stref, OUZ) byłaby możliwa bez lokalnego
  importu — świadomie poza zakresem AU-105 (zmieniłaby wynik `binding` ponad kryterium „identyczne” i wymaga decyzji o statusie `coverage`). To najkrótsza droga
  do `coverage_status=available` w konfiguracji domyślnej (wniosek, nie pomiar) — zob. ADR-022.
- Kafel 50 m ma kompromis: im większy, tym lepsze trafienia cache i więcej obiektów na stronie (RU: 10 na stronę); im mniejszy, tym mniej obiektów, ale mniej wspólnych kafli.

## Do potwierdzenia przez właściciela

1. Czy wyniki `binding` dla Krakowa i gminy 281603 (fałszywe w poprzednim silniku) mają być traktowane jako poprawka (domyślnie tak) — wpływa na raporty zapisane wcześniej, które pokazują POG tych gmin.
2. Próg przecięcia 0,5 m² i kafel 50 m (decyzje inżynierskie, nie prawne).
3. TTL cache 24 h (zgodny z opisem zadania): nowo opublikowany akt RU może być niewidoczny do 24 h.
4. Czy włączyć analizę powierzchniową POG na danych WFS z kafla (obecnie `coverage=partial`, brak odnośnika do APP/GML).
