# Odbiór AU-001–AU-003 — naprawy P0 z audytu 2026-10-05 (Task 21.1–21.3)

Data: 6 października 2026 r. Baza kodu: `ecfd4b3` (zmiany z tego odbioru zacommitowano w AU-009, 2026-10-08). Decyzje: [ADR-015](../adr/ADR-015-api-errors-request-id-and-text-column-policy.md).
Dokumenty audytu wskazane w zadaniach (`docs/audit/…`) nie istnieją w repozytorium — źródłem były treści zadań.

## Stan zadań: **wykonane**

Wszystkie trzy zadania były otwarte (w repozytorium nie było `clip_text`, `PersistenceError`, `request_id`, migracji 031 ani kodu
`UPSTREAM_INVALID_RESPONSE`) i zostały wykonane od początku do końca: kod, migracja, testy na zamrożonych rzeczywistych danych,
test na żywych usługach, test w przeglądarce, dokumentacja.

## AU-001 — zapis analizy z długim adresem źródła

| Kryterium | Stan | Dowód |
|---|---|---|
| Zamrożone wielokąty `146510_8.0502.1/3` (76 wierzchołków) i `126105_9.0001.580/4` (134) przechodzą cały `run_analysis` do zapisu i odczytu raportu PDF | **spełnione** (i jeszcze 6 działek z audytu) | `tests/test_long_geometry_analysis.py`: 8 działek × (API → DB → odczyt → cache → PDF); geometrie i odpowiedzi NMT to rzeczywiste pliki z 2026-10-06 (`fixtures/parcels/long_geometry`), geometria równa korpusowi |
| Dla każdej kolumny `String(n)` test z wartością `n+1` (zapis przycina albo kolumna jest `Text`) | **spełnione z jawnym odstępstwem** | `tests/test_column_length_contract.py`: 174 kolumny; `text` (18) zapisują całość, `clipped` (40) przycinają do `n` z `…`, `strict` (116, skróty/statusy/klucze) odrzucają `n+1` jawnym `DataError` zamiast cichej zmiany wartości — patrz ADR-015 §2 |
| Live smoke: 8 działek z 500 → 200, w logach brak `StringDataRightTruncation` | **spełnione** | [live-smoke-2026-10-06.json](results/au-001-003/live-smoke-2026-10-06.json): 8/8 HTTP 200 (6–16 s), 0 trafień `StringDataRightTruncation` w logu backendu; adres NMT w odpowiedzi ma 157–158 znaków, wysokości identyczne z zamrożonymi odpowiedziami |
| Dokumentacja: wpis w `docs/current_state.md` + ADR | **spełnione** | sekcja „Audyt 2026-10-05 — naprawy P0”, ADR-015 |
| ≥ 80% pokrycia | **spełnione** | kontener jak w CI (`REPO_ROOT=/repo`, repo tylko do odczytu, kopia bez `.env`): **3784 passed**, 3 deselected (`docker_cli`), pokrycie **94,35%** (próg 80%); zmienione moduły: `uldk.py` 100%, `nmt.py` 99%, `persistence.py` 98%, `error_handlers.py` 98%, `request_id.py` 98%, `types.py`/`text.py`/`act_identifier.py` 100%, `metrics.py` 95% |

Odtworzenie przyczyny: dokładnie 8 z 30 działek korpusu ma adres NMT z wielokątem dłuższy niż 1000 znaków (Warszawa 1, Kraków 2, Legnica 3,
Pisz 2 — rozkład z audytu); test `test_exactly_the_eight_audit_parcels_…`. Zakres wykonany: `clip_text`, `ClippedString`, migracja `031`
(18 kolumn, test upgrade/downgrade z danymi na izolowanej bazie), skrócony adres NMT (`polygon_sha256`, `vertex_count`; pełny adres w `DEBUG`),
`PersistenceError` → 503 `PERSISTENCE_FAILED` (także przy wznowieniu analizy), log z `request_id`, `bounded_act_identifier`.

Dodatkowo znaleziono i naprawiono tę samą klasę błędu na ścieżce ręcznej strefy MPZP: identyfikator aktu `mpzp-document:<url>` (klucz unikalny
`VARCHAR(200)`) przepełniał się dla adresów dokumentu dłuższych niż ~186 znaków (`test_pending_document_keeps_long_urls…`).

## AU-002 — klasyfikacja odpowiedzi ULDK

| Kryterium | Stan | Dowód |
|---|---|---|
| Zamrożone odpowiedzi ULDK (`-1 brak wyników`, `-1` z błędem, pusta, `0` bez danych, poprawna) w teście parametrycznym | **spełnione** | `tests/test_uldk.py::test_frozen_uldk_responses_are_classified` (7 odpowiedzi × `GetParcelById`/`GetParcelByXY`); pliki: `fixtures/source_contracts/uldk/` — rzeczywiste odpowiedzi z 2026-10-06, poza `blad_z_komunikatem.txt` (ręcznie, opisane w README) |
| Test parametryczny po wszystkich klasach wyjątków `uldk`/`initiation`: żadna nie daje HTTP 500 | **spełnione** | `tests/test_error_handling.py::test_no_exception_class_of_the_uldk_initiation_persistence_modules_ends_in_http_500` — klasy wykrywane przez `inspect`, więc nowy wyjątek bez reguły w `DOMAIN_ERRORS` przerywa test; plus przejście przez cały stos HTTP z zamrożonymi odpowiedziami (404/502/503) |
| Dokumentacja: wpis w `current_state.md` + ADR | **spełnione** | jw. |
| ≥ 80% pokrycia | **spełnione** | `app/services/uldk.py` 100% |

Zachowanie: kod statusu = pierwszy token pierwszej linii; `-1 brak wyników` ponawiane raz po 300 ms → 404 `PARCEL_NOT_FOUND` z komunikatem z zadania;
inne `-1 …` → 503; pusta odpowiedź/brak kodu → 502 `UPSTREAM_INVALID_RESPONSE`; błędy transportu → 503; liczniki per kod: `GET /health/upstream`.
Obserwacja z odpowiedzi rzeczywistych: ULDK zwraca `-1 brak wyników` także dla niepoprawnych współrzędnych, a zapytanie z nieznaną metodą zwraca tekst bez
kodu statusu — to drugie jest teraz 502, nie wyjątkiem nieobsłużonym.

## AU-003 — błędy z `request_id` i CORS dla 5xx

| Kryterium | Stan | Dowód |
|---|---|---|
| `curl -i -H 'Origin: http://localhost:3000'` na wymuszony błąd → JSON z `request_id` i `access-control-allow-origin` | **spełnione** | żywy backend (uvicorn), wyłączona baza: `HTTP/1.1 500`, `content-type: application/json`, `access-control-allow-origin: http://localhost:3000`, `x-request-id: smoke-au003-0001`, ciało `{"error":"INTERNAL_ERROR",…,"request_id":"smoke-au003-0001"}`; ten sam identyfikator w logu serwera ze śladem stosu |
| Vitest dla każdego statusu: 422, 429, 500, 502, 503, 0 | **spełnione** | `frontend/lib/api.test.ts` (blok „błędy HTTP z request_id”), `hooks/useAnalyzeParcel.test.tsx` (odliczanie po 429) |
| Wstrzyknięty wyjątek w `run_analysis` → JSON, `request_id` w logu serwera | **spełnione** | `test_unhandled_exception_returns_json_with_request_id_and_cors_headers` |
| Test architektury: każdy router zwraca `ErrorResponse` dla błędów domenowych | **spełnione** | testy: wszystkie błędy OpenAPI ≥ 400 mają schemat `ErrorResponse`, handler `HTTPException` dla dowolnego routera, wszystkie handlery zarejestrowane w `app`, `analyze` nie łapie błędów lokalnie |
| Dokumentacja + ≥ 80% pokrycia | **spełnione** | jw.; Vitest: 42 plików, **513 testów**, pokrycie 97,39% instrukcji / 91,44% gałęzi (`lib/api.ts` 99,36%, `hooks/useAnalyzeParcel.ts` 97,95%); `typecheck` i `next build` bez błędów |

Test w przeglądarce (wbudowana przeglądarka aplikacji, frontend zbudowany z kodu z drzewa roboczego, żywy backend z innym originem niż backend):
„brak działki” → „Nie znaleziono działki lub adresu. ULDK nie zwróciło działki dla tej lokalizacji (brak działki albo chwilowy błąd źródła)”;
429 → „Zbyt wiele żądań. Spróbuj ponownie za 44 s.” odliczane na ekranie (37 s → 34 s); 5xx (baza wyłączona) → „Błąd po stronie serwera. Kod zgłoszenia:
5b491498-c8c4-4173-8f5c-1c7225cb3637.” — **nie** „Nie udało się połączyć z usługą”, a ten sam kod jest w logu serwera.

## Wykonane polecenia odbiorowe

```bash
# backend — kontener skonfigurowany jak CI (izolowany projekt compose, repo tylko do odczytu, bez .env)
pytest -m 'not docker_cli' --cov=app --cov-report=term-missing --cov-fail-under=80
# frontend
docker build --build-context shared=./shared --target test -t dzialki-frontend-test ./frontend
docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 dzialki-frontend-test \
  sh -c 'npm run typecheck && npm run test:coverage && npm run build'
```

Test na żywych usługach: backend z kodu z drzewa roboczego (PostGIS 16-3.4, migracja 031 zastosowana przez entrypoint), `POST /analyze` dla
8 działek (ULDK, NMT, KIMPZP, ISOK, GDOŚ i pozostałe źródła na żywo), potem `curl -i -H 'Origin: http://localhost:3000'` na wymuszony błąd i
test w przeglądarce. Stos został usunięty po teście (`docker compose down -v`).

## Ograniczenia i decyzje do potwierdzenia

- `strict` zamiast przycinania dla 116 kolumn (ADR-015 §2) — świadome odstępstwo od dosłownego brzmienia kryterium; do potwierdzenia przez właściciela.
- Liczniki ULDK są lokalne dla procesu; `GET /health/upstream` jest endpointem administracyjnym (`X-Admin-Key`).
- NMT odrzuca poligony > 100 000 m² (rzeczywista odpowiedź, status HTTP 200) — sekcja terenu takiej działki jest `unavailable`; wymaga osobnej decyzji (np. WCS).
- Rollback migracji 031 jest stratny (adresy > 1000 znaków przycinane ze znacznikiem `…`, liczba przyciętych wierszy w logu).
- Test na żywych usługach wykonano raz (2026-10-06); pierwszy bieg użył `force_refresh=true` (limit 5/min) i trzy ostatnie żądania dostały `429 RATE_LIMITED`
  z `ErrorResponse` — powtórzono je bez `force_refresh` (wszystkie 200). Zapis wyniku w pliku JSON.
- Nie wykonano commita ani PR-a. Nowe pliki w `docs/adr` i `docs/evaluation` są nieśledzone.
