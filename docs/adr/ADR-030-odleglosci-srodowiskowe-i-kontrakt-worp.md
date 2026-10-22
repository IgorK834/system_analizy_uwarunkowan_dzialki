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
mają jawne timeouty, dwa podejścia dla błędów przejściowych, limity bajtów i
allowlistę hostów HTTPS. GDOŚ pozostaje atomowe na poziomie sekcji: błąd jednej
z 14 warstw unieważnia wynik „brak obiektów”, zamiast zwracać fałszywie pełny
wynik. API, UI, PDF i pakiet audytowy pokazują promień pomiaru oraz zastrzeżenia.

