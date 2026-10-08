# ADR-017: Token dla `POST /analyze/resume`, jeden limiter z zaufanym proxy i single-flight analiz per działka

- Status: **Zaakceptowany technicznie (2026-10-08)**
- Zakres: AU-005 (Task 21.5, audyt pozycja B6), AU-006 (Task 21.6, B7/R3/R4), AU-007 (Task 21.7, B8/R3).
- Powiązane: ADR-001 (granice modułów), ADR-015 (kontrakt błędów, `request_id`).

## Kontekst

Audyt 2026-10-05 wykazał trzy luki w tej samej warstwie dostępu do analiz:

1. **B6.** Wszystkie endpointy zasobów analizy wymagają `access_token`, a `POST /analyze/resume` nie. `analysis_id` to kolejna
   liczba całkowita, więc odpowiedzi `404` (nie istnieje) i `409` (nie czeka) pozwalały wyliczać cudze analizy, a analizę
   czekającą na symbol strefy mógł „dokończyć” ktoś inny własnym symbolem.
2. **B7/R3/R4.** Lokalny `.env` miał `RATE_LIMIT_TRUST_FORWARDED_FOR=true`, a `docker-compose.yml` publikuje backend na
   `0.0.0.0:8000` bez proxy. `client_key()` brał ostatni wpis `X-Forwarded-For`, czyli wartość podaną przez klienta
   (pomiar: limit 5/min, rotacja nagłówka → 50/50 żądań przeszło). Wyszukiwarka adresów miała osobny limiter (stałe 30/min,
   `request.client.host` — za proxy wspólny licznik wszystkich), a kafle WMS/MVT i `/geocode/suggest` nie miały limitu.
3. **B8/R3.** 6 równoległych `POST /analyze` dla tej samej nowej działki dawało 6 pełnych analiz (id 43–48), 6 wierszy w
   `analyses` i 6× pełne odpytanie usług rządowych (14–26 s każde). Razem z obejściem limitu aplikacja wzmacniała ruch do
   usług zewnętrznych.

## Decyzje

### 1. Token dostępu dla `POST /analyze/resume` (AU-005)

- `AnalyzeResumeRequest.access_token` (pole body) albo nagłówek `X-Analysis-Token`; wystarczy jeden poprawny. Weryfikacja to
  zależność FastAPI wykonywana **przed** `get_db`, więc 403 zapada bez żadnego odczytu bazy (test podmienia `get_db` na funkcję,
  która by wywaliła test).
- Brak tokenu, token błędny, token innej analizy i bajty spoza ASCII dają ten sam `403` z tą samą treścią
  (`ANALYSIS_ACCESS_DENIED_DETAIL`) dla analizy istniejącej i nieistniejącej. `404`/`409` tylko po poprawnym tokenie.
  Wspólna funkcja `ensure_analysis_access` obsługuje też `require_analysis_token` (raport, dokument).
- Limit zapytań (`POST /analyze`, 20/min) liczy się **przed** tokenem, więc zgadywanie tokenów jest ograniczone.
- Frontend: `useResumeAnalysis`/`resumeAnalysis` wysyłają `access_token` z wyniku analizy; bez tokenu w wyniku żądanie nie jest
  wysyłane. Nowy komunikat dla 403.
- Nie zmieniono pól `AnalyzeResponse` → `*_SCHEMA_VERSION`, `RESULT_CONTRACT_VERSION` i `field-mapping.md` bez zmian.

### 2. Jeden limiter, klucz klienta bezpieczny domyślnie (AU-006)

- `rate_limit(limit)` + `client_key(request)` to jedyny mechanizm; usunięto limiter i `_client_key()` z
  `modules/location/api/router.py`. Każda polityka ma własny licznik i próg w `settings`: `rate_limit_tiles_per_minute` (1200,
  kafle WMS i MVT), `rate_limit_geocode_per_minute` (60), `rate_limit_address_search_per_minute` (30, jak dotąd),
  `rate_limit_data_per_minute` (300: metadane map, wydania POG, dokumenty aktów, sondy zdrowia, endpointy z kluczem
  administracyjnym — limit chroni też przed zgadywaniem `X-Admin-Key`). Przekroczenie to `429 RATE_LIMITED` z `Retry-After`
  przez wspólny handler `ErrorResponse`.
- `X-Forwarded-For` jest brany pod uwagę **wyłącznie**, gdy `RATE_LIMIT_TRUST_FORWARDED_FOR=true` **i** adres połączenia
  należy do `RATE_LIMIT_TRUSTED_PROXIES` (adresy/sieci CIDR; błędny wpis zatrzymuje start). Wtedy klientem jest wpis liczony od
  końca o `TRUSTED_PROXY_COUNT` (domyślnie 1): każdy zaufany proxy dopisuje adres swojego nadawcy, więc wpisy z przodu mógł
  podać klient. Za krótki łańcuch, pusty lub nieprawidłowy wpis (np. `host:port`, `unknown`) oznacza użycie adresu połączenia.
  Wszystkie linie nagłówka są łączone. `TRUST=true` bez listy proxy działa jak `false` i zapisuje ostrzeżenie.
- Klucz IPv6 to prefiks /64 (rotacja w obrębie /64 nic nie daje), adres IPv4-mapped sprowadzamy do IPv4.
- Wartości domyślne: `RATE_LIMIT_TRUST_FORWARDED_FOR=false` w `.env.example`, Compose i `Settings`; test
  `test_env_example_*` pilnuje wartości, README opisuje, kiedy włączać. Lokalny, ignorowany `.env` został poprawiony ręcznie.
- Pokrycie wszystkich tras pilnuje `tests/test_rate_limit_coverage.py`: lista operacji pochodzi z OpenAPI, więc nowy endpoint
  bez limitera wywraca test; każda operacja ma też test 429 z `Retry-After`.
- Konsekwencja przyjęta świadomie: limiter pozostaje w procesie (N workerów = N × limit). Rozproszony limiter (Redis) jest
  osobnym zadaniem; `docker-compose.yml` nadal publikuje port 8000 na wszystkich interfejsach — samo wyłączenie zaufania do
  nagłówka usuwa obejście, a zawężenie wiązania portu zostaje decyzją właściciela.

### 3. Single-flight per `parcel_identifier` (AU-007)

- `app/services/singleflight.py`: rejestr lotów w procesie (`threading.Lock`, budzenie przez `call_soon_threadsafe`, bez założenia
  jednej pętli zdarzeń) + sesyjna blokada doradcza PostgreSQL `pg_try_advisory_lock(hashtextextended(klucz, 0))` na osobnym
  połączeniu (`NullPool`, `AUTOCOMMIT`, `app.db.session.advisory_lock_engine`), odpytywana co 250 ms z limitem czasu. Blokada
  znika razem z połączeniem, więc awaria lidera (wyjątek, anulowanie, zabity proces) jej nie zostawia. Wybrano wariant sesyjny,
  a nie `pg_advisory_xact_lock`, bo analiza trwa kilkanaście sekund i składa się z wielu transakcji i wywołań sieciowych —
  transakcja trzymana przez cały ten czas blokowałaby połączenie z puli żądań i vacuum. Wymaga to bezpośredniego połączenia z
  PostgreSQL (nie przez pgbouncer w trybie transaction pooling).
- `run_analysis`: trafienie w cache wraca bez blokady. Chybienie albo `force_refresh` wchodzi do blokady; lider po jej zdobyciu
  ponownie sprawdza cache (`reuse`), więc inny worker, który czekał, nie liczy drugi raz. Czekający w tym samym procesie dostają
  odpowiedź lidera (to samo `analysis_id`, także dla analizy `waiting_for_user_input`, która nie jest cache'owalna).
- `force_refresh` jest objęty blokadą. Wynik lidera lub zapisany przez innego workera jest akceptowany tylko wtedy, gdy
  `analyzed_at ≥ moment żądania` (`get_analysis_completed_since`), więc żądanie odświeżenia nigdy nie dostaje wyniku starszego niż
  samo żądanie (np. trafienia w cache przejętego przez lidera), a N równoległych odświeżeń daje jedną nową analizę.
- Identyfikacja działki (ULDK) też jest współdzielona przez identyczne równoległe żądania (klucz: treść żądania, błędy
  współdzielone), żeby „każde źródło wywołane 1×” obejmowało również ULDK. Żądania tej samej działki różnymi metodami
  (identyfikator, punkt) robią po jednym zapytaniu ULDK, ale jedną analizę.
- Awaria lidera: czekający nie dziedziczą jego błędu — jeden przejmuje rolę lidera (ponowna próba), reszta czeka na niego. Anulowanie
  lidera (rozłączony klient) nie liczy się jako błąd. Po drugim nieudanym liderze błąd jest propagowany, żeby deterministyczna
  awaria nie mnożyła pracy. Oczekiwanie ma limit (`ANALYSIS_SINGLEFLIGHT_WAIT_SECONDS`, 90 s): po nim `503
  ANALYSIS_IN_PROGRESS` z `Retry-After` (ponowienie trafia w cache). Wybrano porażkę zamkniętą zamiast „po timeoutcie licz sam”,
  bo to właśnie ten wariant odtwarza wzmacnianie ruchu do usług rządowych.
- Awaria samej blokady doradczej (brak połączenia, błąd zapytania) degraduje się do blokady w procesie z ostrzeżeniem i licznikiem
  `analysis_singleflight.lock_unavailable`; awarię bazy i tak zgłosi zwykła ścieżka analizy.
- „Unikalność logiczna” zrealizowano kontrolą po blokadzie (`reuse`), a nie indeksem: historia analiz jest celowo
  wielokrotna (`force_refresh`, zmiana sygnatury cache), więc częściowy indeks unikalny odrzucałby prawidłowe zapisy, a okno N
  sekund jest tu dynamiczne (czas oczekiwania żądania). Dodatkowo `get_or_create_parcel` używa savepointu i ponownego odczytu przy
  kolizji `parcels.parcel_identifier`: równoległy pierwszy zapis tej samej działki (inny worker, wyłączony single-flight) kończył
  się 503 `PERSISTENCE_FAILED` — teraz zwycięzca wyścigu jest współdzielony.
- Obserwowalność: gauge `analysis_singleflight_waiters` i liczniki `analysis_singleflight.{leader,wait,takeover,timeout,
  lock_unavailable}` w `GET /health/upstream` (`gauges` to nowe pole odpowiedzi), wpisy logu `analysis_event=singleflight
  singleflight=leader|wait` oraz `singleflight_shared … waited_ms=…`. Wartości są lokalne dla procesu.
- Wyłącznik: `ANALYSIS_SINGLEFLIGHT_ENABLED=false` przywraca zachowanie sprzed zmiany (poza utwardzonym `get_or_create_parcel`).

## Konsekwencje i ograniczenia

- Czekanie na blokadę między workerami to odpytywanie co 250 ms (jedno połączenie na czekającego) — wystarczające przy
  limicie 20 analiz/min na klienta; przy dużym ruchu warto rozważyć powiadomienia `LISTEN/NOTIFY`.
- Liczba jednoczesnych połączeń blokad równa się liczbie analiz w toku (różne działki) plus czekający innych workerów; nie
  korzystają z puli żądań, ale liczą się do `max_connections` PostgreSQL.
- Metryki są per proces; przy N workerach widok operatora jest cząstkowy.
- Wyścig `force_refresh` vs lider: wynik lidera, który zaczął się chwilę przed żądaniem, ale zapisał po nim, jest akceptowany —
  to ta sama minuta danych, a celem jest uniknięcie lawiny identycznych analiz.
