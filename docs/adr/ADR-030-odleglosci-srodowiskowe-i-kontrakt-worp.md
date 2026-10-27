# ADR-030: odległości środowiskowe i warunek kontraktu WORP

- Status: zaakceptowana
- Data: 2026-10-21
- Zadania: AU-205 / Task 23.5, AU-206 / Task 23.6

## Kontekst

Dotychczas sekcje ISOK i GDOŚ odpowiadały wyłącznie na pytanie o przecięcie
działki. Publiczne WFS-y udostępniają rzeczywistą geometrię także poza obrysem
działki, więc możliwy jest pomiar do najbliższej strefy lub obiektu. Jednocześnie
`nz-core:RiskZone` i `nz-core:ExposedElement` mają semantykę INSPIRE, która nie
potwierdza sama w sobie równoważności z komercyjną wstępną oceną WORP.

## Decyzja

1. Każdy pomiar bliskości ma jeden z rozłącznych stanów `intersection`, `near`
   albo `none_within_radius`. Awaria źródła pozostaje stanem sekcji
   `unavailable`/`error` i nigdy nie jest zamieniana na brak obiektu.
2. Odległość jest minimalną odległością euklidesową od pełnego poligonu działki
   do rzeczywistej geometrii źródłowej, liczoną przez Shapely w EPSG:2180.
   `null` oznacza brak pomiaru lub potwierdzony brak obiektu w promieniu; nie jest
   zastępowany zerem. Zero występuje wyłącznie dla przecięcia.
3. ISOK odpytuje `nz-core:HazardArea` w buforze 1 km i zwraca najbliższą bazową
   klasę Q10, Q100 i Q500. Scenariusz zniszczenia wału może pozostać widocznym
   przecięciem, ale nie jest klasyfikowany jako bazowe Q100.
4. GDOŚ odpytuje 14 warstw osobnymi żądaniami. Pomniki punktowe i
   powierzchniowe mają promień 200 m, pozostałe warstwy 1 km. Ramsar, korytarze
   ekologiczne i elektrownie wiatrowe są `informational_only`; z samej geometrii
   nie wyprowadza się strefy wyłączenia ani oceny prawnej.
5. WORP ma status `contract_required` i kod
   `WORP_CONTRACT_NOT_CONFIRMED`. Do czasu potwierdzenia reguły agregacji,
   klas i porównywalności z oczekiwanym produktem aplikacja nie podaje pozornej
   klasy ryzyka. Ten warunek stopu nie blokuje pomiarów HazardArea.
6. Kontrakt `RiskResult`/`RiskSectionResult` ma wersję 2.0, a podpis cache zawiera
   `risk-v2.0`. Nowe pola są zachowywane w istniejących snapshotach JSON analiz,
   dlatego nie jest potrzebna migracja tabel; historyczne snapshoty 1.0 są nadal
   odczytywane jako kontrakt legacy.
7. W rejestrze źródeł i macierzy jakości źródło jest deduplikowane po
   `source_id`. Niezależnie od liczby obiektów występuje jedna pozycja „GDOŚ”.
8. Orkiestrator kontekstu korzysta z portu `EnvironmentalSectionPort` warstwy
   application. Adaptery `IsokWfsAdapter` i `GdosWfsAdapter` leżą w warstwie
   infrastructure, przyjmują geometrię jako `GeometryPayload` i odrzucają CRS
   inny niż EPSG:2180 przed wykonaniem I/O. Kompatybilne serwisy transportowe
   pozostają miejscem timeoutów, retry, limitów bajtów i allowlisty hostów.

## Konsekwencje

Zapytania mają większy BBOX i odpowiedzi ISOK mogą być duże, dlatego adaptery
mają jawne timeouty, ograniczone ponowienia błędów przejściowych (GDOŚ dwa
podejścia, ISOK trzy od aktualizacji 2026-10-27), limity bajtów i allowlistę hostów
HTTPS. GDOŚ pozostaje atomowe na poziomie sekcji: błąd jednej
z 14 warstw unieważnia wynik „brak obiektów”, zamiast zwracać fałszywie pełny
wynik. API, UI, PDF i pakiet audytowy pokazują promień pomiaru oraz zastrzeżenia.

## Aktualizacja 2026-10-27: odporność na przejściowe awarie źródeł

Powtórny przebieg na żywych usługach dla działki `141801_4.0701.23/8` zwrócił
`unavailable/SERVICE_HTTP_ERROR` zarówno dla ISOK, jak i GDOŚ. Kontrakt zadziałał
poprawnie (awaria nie została pokazana jako brak ograniczenia), ale przyczyny były
przejściowe po stronie usług, więc decyzja 8 jest uzupełniona:

1. ISOK: GeoServer sporadycznie zwraca wewnętrzny `java.lang.NullPointerException`
   jako HTTP 400 z `ows:ExceptionReport`. Dla BBOX-u działki odbiorowej dotyczyło to
   5 z 11 żądań (ok. połowy pierwszych prób), a następna próba zwykle przechodziła;
   nieudana odpowiedź wraca po 2–3 s. Taki 400 jest ponawiany, a `ISOK_MAX_ATTEMPTS`
   rośnie z 2 do 3 (przy dwóch próbach ok. 1/4 analiz traciła sekcję powodziową).
   Każdy inny 400 pozostaje błędem klienta bez ponowienia. Po wyczerpaniu prób wynik
   to nadal `unavailable/SERVICE_HTTP_ERROR`. Trzecia próba zwiększa najgorszy czas
   sekcji przy zawieszonym ISOK z ok. 60 s do ok. 90 s. Limit pojedynczej operacji
   `ISOK_TIMEOUT_S` pozostaje 30 s; jest to limit odczytu httpx, nie całkowitego czasu
   pobierania, więc pobranie 15,7 MB w kilkanaście sekund go nie narusza. Sekcje KIUT,
   GDOŚ i NMT działają równolegle i nie czekają na ISOK.
2. GDOŚ: sekcja jest atomowa, więc pojedyncze przejściowe HTTP 500 jednej z 14
   warstw unieważniało całość. HTTP 500 jest ponawiane tak jak 429/502/503/504.
   Trwałe 500 nadal daje `unavailable`, nigdy brak obiektów.

Zmiana nie modyfikuje kontraktu API ani danych, więc nie podnosi `RISK_RESULT_SCHEMA_VERSION`.
Raport live smoke (`backend/scripts/live_smoke_corpus.py`) rozkłada teraz statusy
`risk_sections` i stan WORP, żeby awaria źródła była widoczna osobno od
`available (none_within_radius)`.

