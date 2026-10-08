# ADR-015: Błędy API z `request_id`, polityka długości kolumn tekstowych i klasyfikacja odpowiedzi ULDK

- Status: **Zaakceptowany technicznie (2026-10-06)**
- Zakres: AU-001, AU-002, AU-003 (Task 21.1–21.3, audyt 2026-10-05, pozycje B1–B3).
- Powiązane: ADR-001 (modularny monolit — mapowanie HTTP zostaje w warstwie routerów, wyjątki domenowe w
  serwisach), ADR-011 (provenance i pakiet audytowy), ADR-014 (redakcja sekretów w logach).

## Kontekst

Test na żywych usługach (2026-10-05) wykazał trzy powiązane awarie, wszystkie kończące się surowym HTTP 500:

1. **8 z 30 działek korpusu** (Warszawa 1/4, Kraków 2/4, Legnica 3/5, Pisz 2/5) — `StringDataRightTruncation` w
   `save_analysis`. Adres zapytania NMT `GetMinMaxByPolygon` zawierał cały wielokąt działki (2874 znaki dla 76
   wierzchołków), a `source_records.source_url` było `VARCHAR(1000)`. Powtórzenie na korpusie: dokładnie te 8 działek
   ma adres dłuższy niż 1000 znaków (`tests/test_long_geometry_analysis.py`).
2. **ULDK** odpowiada `-1 brak wyników` w jednej linii (kod i komunikat). Parser porównywał pierwszą linię z
   `"-1"`/`"0"`, więc „brak działki” (Bałtyk, nieistniejący numer) i przejściowy błąd usługi powiatowej za ULDK
   kończyły się nieobsłużonym `InvalidUldkResponseError`.
3. **Nieobsłużony wyjątek** zwracał `text/plain` bez nagłówków CORS. Przeglądarka zgłaszała błąd sieci, UI pisał
   „Sprawdź połączenie”, a użytkownik ponawiał żądania, które zawsze padną i zużywają limit 20/min.

## Decyzje

### 1. Provenance nie przenosi wielokąta; kolumny adresów są `Text`

`SourceMetadata.source_url` źródła NMT to adres bazowy usługi i skrócona informacja o zapytaniu:
`…/nmt/?request=GetMinMaxByPolygon&polygon_sha256=<SHA-256 WKT>&vertex_count=<n>`. Skrót jednoznacznie wskazuje, o jaką
geometrię pytano, a długość adresu nie zależy od jej złożoności. Pełny adres jest wyłącznie w logu `DEBUG`. Usługa nadal
dostaje pełny wielokąt. Komunikaty błędów adaptera nie zawierają adresu (komunikat wyjątku `httpx` go zawiera).

Migracja `031_source_url_text` zmienia na `TEXT` 18 kolumn adresów i odnośników zasilanych z zewnątrz (`source_url`
w pięciu tabelach wyników, `analyses.pending_uchwala_url`, `requested_url`/`final_url`, `source_artifacts.uri` oraz
odniesienia APP/CSW w modelu wersjonowanym). `VARCHAR → TEXT` nie przepisuje tabel. Rollback przycina wartości dłuższe niż
poprzedni limit do `n` znaków ze znacznikiem `…` (wiersz zostaje) i loguje liczbę przyciętych wierszy każdej kolumny.
Adres musi być `Text`, a nie przycinany: odczyt historyczny dopasowuje rekord źródła po `source_url` i `fetched_at`.

### 2. Każda kolumna `String(n)` ma jawną decyzję o źródle wartości

Rejestr `backend/tests/column_length_policy.py` klasyfikuje wszystkie 174 kolumny `String(n)` (tabele `app/models/` oraz
`address_search_entries`). Nowa kolumna bez wpisu przerywa `tests/test_column_length_contract.py`.

| Kategoria | Typ | Zapis wartości `n+1` | Dla jakich wartości |
|---|---|---|---|
| `text` | `Text` | zapisuje się w całości | adresy i odnośniki; długość zależy od zapytania |
| `clipped` | `ClippedString(n)` | przycięcie do `n` znaków ze znacznikiem `…` | etykiety, nazwy, numery uchwał, nagłówki HTTP z zewnątrz |
| `strict` | `String(n)` | **jawny `DataError`** | skróty, statusy, wersje, klucze tożsamości, zapis wsadowy importerów |

`ClippedString` (`app/models/types.py`) przycina przy wiązaniu parametru przez `clip_text` (`app/shared/text.py`), więc
obejmuje każdą ścieżkę zapisu; schemat bazy jest taki sam jak dla `String(n)` (bez migracji). W logu jest tylko długość
(wartość może zawierać geometrię). **Odstępstwo od dosłownego kryterium „n+1 przycina albo kolumna jest Text”:** dla `strict`
test sprawdza, że `n+1` jest odrzucane. Cicha zmiana skrótu, statusu albo klucza unikalnego (np. `parcels.parcel_identifier`)
byłaby gorsza niż kontrolowany błąd; taki błąd jest błędem programisty, nie danych z zewnątrz.

Identyfikator aktu tworzony z adresu dokumentu (`mpzp-document:<url>`) był kluczem unikalnym `planning_acts.act_identifier`
(`VARCHAR(200)`), czyli tym samym błędem na ścieżce ręcznej strefy MPZP. `bounded_act_identifier`
(`app/shared/act_identifier.py`) zostawia wartość mieszczącą się w limicie bez zmian, a dłuższą skraca do
`<początek>#<skrót SHA-256>` — unikalność zachowana, nic nie jest przycinane.

### 3. `PersistenceError` → 503 `PERSISTENCE_FAILED`

`save_analysis` i zapis wznowienia zamieniają `DataError`/`IntegrityError` na `PersistenceError` po wycofaniu transakcji.
Log `persistence_failed` niesie `request_id`, operację, typ błędu sterownika (np. `StringDataRightTruncation`), SQLSTATE,
komunikat sterownika (`value too long for type character varying(30)`), tabelę z instrukcji i kontekst (identyfikator działki,
status). **Nie** zawiera parametrów instrukcji ani śladu stosu z `str(exc)` (zawierają zapisywane wartości). Inne wyjątki
propagują się bez zmian (po rollbacku).

### 4. ULDK: kod statusu to pierwszy token

`_parse_uldk_response` dzieli pierwszą linię na kod i komunikat (komunikat bywa w drugiej linii w starszej formie).
`-1 brak wyników` ponawia zapytanie **raz** po 300 ms (brak działki jest nieodróżnialny od przejściowego błędu
źródła), a drugie takie `-1` daje `ParcelNotFoundError` (404) z komunikatem „ULDK nie zwróciło działki dla tej lokalizacji
(brak działki albo chwilowy błąd źródła)”. Inne `-1 …` to `UldkServiceUnavailableError` (503), brak kodu/nieznany kod/pusta
odpowiedź to `InvalidUldkResponseError` (502 `UPSTREAM_INVALID_RESPONSE`). Błędy transportu (połączenie, DNS) są
ponawiane jak timeouty i kończą się 503, nie surowym wyjątkiem `httpx`. Liczniki per kod odpowiedzi
(`uldk.response.<kod>`, ponowienia, transport) są w pamięci procesu (`app/core/metrics.py`) i wystawione operatorowi przez
`GET /health/upstream` (klucz `X-Admin-Key`).

### 5. Kontrakt błędów: `ErrorResponse` + `request_id` dla każdego błędu

- `RequestIdMiddleware` (czysty ASGI, najbardziej zewnętrzny z middleware aplikacji) przyjmuje poprawny `X-Request-ID`
  (`^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$`), w przeciwnym razie generuje UUID4; zapisuje go w `scope["state"]` i `ContextVar`
  (kopiowany do wątków przez `asyncio.to_thread`) i dodaje do nagłówka odpowiedzi. Format logu: `[request_id]` w każdym wpisie,
  `request_id=` w zdarzeniach `log_analysis_event`.
- `app/routers/error_handlers.py`: handler `HTTPException` (zachowuje `detail` jako string i nagłówki, np. `Retry-After`; dodaje
  `error` i `request_id`), tabela `DOMAIN_ERRORS` (klasa wyjątku → status i kod) oraz handler `Exception` → `500
  INTERNAL_ERROR` bez stack trace. Routery nie łapią już błędów domenowych `analyze` — jedno źródło mapowania.
- Handler `Exception` działa w `ServerErrorMiddleware`, **poza** `CORSMiddleware`, więc sam dopisuje nagłówki CORS na
  podstawie `settings.backend_cors_origins` i nagłówka `Origin` (te same originy, `allow_credentials`, `expose_headers`).
  Odpowiedzi z pozostałych handlerów przechodzą przez `CORSMiddleware`. `expose_headers` zawiera `X-Request-ID` i
  `Retry-After`, bez czego front nie przeczyta czasu oczekiwania po 429.
- Błędy walidacji starszych endpointów zachowują format FastAPI (lista); identyfikator jest w nagłówku.
- Frontend (`lib/api.ts`): 5xx → „Błąd po stronie serwera. Kod zgłoszenia: …” bez zachęty do ponawiania; 429 → odliczanie z
  `Retry-After`; 502/503 → osobne komunikaty; `PERSISTENCE_FAILED` → błąd zapisu; status 0 wyłącznie dla odrzuconego `fetch`.

## Konsekwencje i ograniczenia

- Pola `AnalyzeResponse` się **nie zmieniły** — `*_SCHEMA_VERSION`, `RESULT_CONTRACT_VERSION` i `field-mapping.md` bez zmian.
  Zmieniła się wartość `terrain.source.source_url` (krótszy adres); zapisy sprzed zmiany zachowują pełny adres.
- Ciała błędów zyskały pola `error` i `request_id`; `detail` bez zmian dla klientów, którzy go czytali.
- Rollback migracji 031 jest stratny (przycina adresy do 1000 znaków) — udokumentowany, z logiem.
- Liczniki ULDK są lokalne dla procesu (po restarcie od zera; przy N procesach każdy ma własny widok).
- `-1 brak wyników` na nieistniejącej lokalizacji kosztuje jedno dodatkowe zapytanie i ~300 ms.
- Obserwacja z danych rzeczywistych: NMT `GetMinMaxByPolygon` odrzuca poligony większe niż 100 000 m² błędem w treści
  ze statusem HTTP 200 (`Powierzchnia poligonu … większa niż 100000 m2`) — adapter traktuje to jako niedostępność sekcji
  terenu (nie jako „płaski teren”); analiza działki 23,7 ha kończy się 200. Zamrożone jako
  `real-025-281603-4-0001-431-66`. Osobne usprawnienie (np. pomiar z rastra WCS dla dużych działek) nie jest częścią tej zmiany.

## Rozważone alternatywy

- *Przycinanie adresów NMT zamiast skrótu* — gubi informację potrzebną do odtworzenia zapytania i psuje dopasowanie rekordów źródeł.
- *Zamiana wszystkich `String(n)` na `ClippedString`* — cicho zmieniałaby skróty, statusy i klucze tożsamości.
- *`BaseHTTPMiddleware` do obsługi 500* — owija strumienie odpowiedzi i wyjątki; wybrany czysty ASGI plus handler `Exception`.
- *Ręczne łapanie wyjątków w każdym routerze* — rozproszone mapowanie; tabela `DOMAIN_ERRORS` + test po klasach wyjątków modułów
  (`uldk`, `initiation`, `persistence`) wykrywa nowy wyjątek bez reguły.
