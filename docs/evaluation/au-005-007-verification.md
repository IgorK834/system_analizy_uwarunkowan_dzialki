# Odbiór AU-005, AU-006, AU-007 (Task 21.5–21.7): token resume, jeden limiter, single-flight

- Data: 2026-10-08. Stan: **zaimplementowane i zacommitowane w `main` (AU-009, 2026-10-08)**.
- Decyzje: [ADR-017](../adr/ADR-017-resume-token-rate-limit-and-single-flight.md). Audyt: pozycje B6 (AU-005), B7/R3/R4
  (AU-006), B8/R3 (AU-007) — dokumenty `docs/audit/…` wskazane w zadaniach nadal nie istnieją w repozytorium, źródłem były treści zadań.
- Artefakty: [`results/au-005-007/`](results/au-005-007/) (pomiary na żywym stosie, log single-flight, zrzut UI).
- Zależność AU-007 od AU-001 (Task 21.1): spełniona (AU-001 wykonane 2026-10-06).

## Kryteria akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| AU-005: brak tokenu → 403 dla istniejącego i nieistniejącego `analysis_id`, identyczna treść | **spełnione** | `test_resume_without_token_is_403_with_identical_body_for_existing_and_missing_analysis`, `test_resume_with_wrong_token_is_403_for_existing_and_missing_analysis[*]`, `test_resume_with_token_of_another_analysis_is_403_and_does_not_resume`, `test_resume_403_is_decided_before_any_database_access` (podmiana `get_db` na funkcję zrywającą test); żywy stos: obie odpowiedzi `403 FORBIDDEN "Brak dostępu do tej analizy."` |
| AU-005: poprawny token + analiza nieczekająca → 409; `404/409` tylko po tokenie | **spełnione** | `test_resume_404_and_409_are_returned_only_after_a_valid_token`; żywy stos: 409 po wznowieniu analizy 2 poprawnym tokenem |
| AU-005: token w body lub nagłówku `X-Analysis-Token` | **spełnione** | `test_resume_accepts_the_token_in_the_x_analysis_token_header`, `test_resume_accepts_a_valid_body_token_even_if_the_header_is_wrong`, `test_resume_non_ascii_header_bytes_are_403_not_500`, OpenAPI (`test_resume_openapi_documents_token_and_403`) |
| AU-005: E2E ręcznego symbolu strefy przechodzi, UI przekazuje token z wyniku | **spełnione** | testy e2e `test_manual_zone_compatibility_e2e` (z tokenem), `app/page.test.tsx` (wywołanie z `access_token: "token-42"`), brak wysyłki bez tokenu; przeglądarka na żywym stosie: formularz → `POST /analyze/resume` z `access_token` z wyniku → 200, formularz znika, widoczna notka o ręcznym symbolu ([04-ui-manual-zone-resume.jpg](results/au-005-007/04-ui-manual-zone-resume.jpg)) |
| AU-006: rotacja `X-Forwarded-For` przy `TRUST=false` nie zmienia licznika; przy `TRUST=true` bez zaufanego peera — również nie | **spełnione** | `test_audit_measurement_trust_off_*`, `test_audit_measurement_trust_on_without_a_trusted_peer_*`, `test_audit_measurement_trust_on_with_a_peer_outside_the_list_*`, `test_audit_measurement_rotating_the_client_supplied_prefix_behind_a_trusted_proxy` (rotacja przodu za zaufanym proxy też nic nie daje), pozytywny `test_distinct_real_clients_behind_a_trusted_proxy_keep_separate_budgets` |
| AU-006: pomiar z audytu (50 żądań, limit 5/min) przepuszcza ≤ 5 | **spełnione** | test jw. na `POST /analyze?force_refresh=true`; żywy stos: **5 × 422 (przeszło limiter), 45 × 429** z `Retry-After: 60` ([01-live-measurements.json](results/au-005-007/01-live-measurements.json)) |
| AU-006: wszystkie endpointy publiczne mają limit i test 429 z `Retry-After` | **spełnione** | `tests/test_rate_limit_coverage.py`: lista operacji z OpenAPI (kontrola, że każda należy do znanego routera), `test_every_public_operation_has_a_rate_limiter[*]` i `test_exceeding_the_limit_returns_429_with_retry_after[*]` dla każdej operacji (kafle WMS/MVT, `/geocode/suggest`, `/api/v1/search/addresses`, `/api/v1/map/coverage/kiut`, raporty, dokumenty, wydania POG, sondy, endpointy administracyjne) |
| AU-006: `.env.example`, Compose, README; test `test_env_example_*` pilnuje wartości domyślnej `false` | **spełnione** | `test_env_example_does_not_trust_forwarded_for_by_default`, `…_values_match_the_settings_defaults`, `test_compose_defaults_to_not_trusting_forwarded_for`; README „Dostęp, limity i status analizy” (kiedy włączać: tylko za proxy, z `RATE_LIMIT_TRUSTED_PROXIES` i `TRUSTED_PROXY_COUNT`); lokalny `.env` poprawiony ręcznie |
| AU-007: 6 równoległych `POST /analyze` dla nowej działki → 1 wiersz w `analyses`, każde źródło 1×, ten sam `analysis_id` (respx) | **spełnione** | `test_six_parallel_requests_for_a_new_parcel_run_one_analysis`: ULDK, KIMPZP i NMT to zamrożone, rzeczywiste odpowiedzi (`respx`, opóźnione 0,2–0,5 s), pozostałe źródła to szwy z licznikami; wywołania każdego źródła = wywołania jednej analizy (mierzone osobno), ULDK dokładnie 1×, 1 wiersz, 6 × 200, jeden `analysis_id` i `access_token`; żywy stos (prawdziwe usługi): 6 × 200 w 24 s, 1 `analysis_id`, 1 wiersz, log: 1 × `leader`, 5 × `wait` ([03-live-singleflight-log.txt](results/au-005-007/03-live-singleflight-log.txt)) |
| AU-007: awaria lidera zwalnia blokadę, czekający nie zawisają (timeout i ponowna próba) | **spełnione** | `test_failed_leader_releases_the_lock_and_waiters_get_a_result` (HTTP: lider 500, trzech 200 z jedną analizą), `test_leader_failure_releases_the_flight_and_one_waiter_takes_over`, `test_cancelled_leader_hands_over_without_failing_the_waiters`, `test_repeated_leader_failures_are_propagated_instead_of_multiplying_work`, `test_waiter_over_the_wait_limit_gets_503_with_retry_after_and_the_leader_still_finishes`, na PostgreSQL: `test_advisory_lock_is_released_after_an_exception_in_the_leader`, `…_when_the_leader_is_cancelled`, `test_advisory_lock_disappears_with_the_session_of_a_dead_worker`, `test_advisory_lock_times_out_instead_of_hanging` |
| AU-007: `force_refresh` objęty blokadą | **spełnione** | `test_six_parallel_force_refresh_requests_share_one_new_analysis` (6 równoległych odświeżeń → 1 nowa analiza, historia zachowana), `test_force_refresh_is_not_served_the_older_cache_entry_by_a_waiting_leader` |
| AU-007: wiele workerów | **spełnione** | `test_two_workers_run_the_work_once_and_the_second_reuses_the_stored_result` (dwa rejestry = dwa procesy, wspólna tylko blokada PostgreSQL), `test_result_saved_by_another_worker_while_waiting_for_the_lock_is_reused` i wariant `force_refresh` na pełnym `POST /analyze` |
| AU-007: metryka `analysis_singleflight_waiters` i log `singleflight=wait\|leader` | **spełnione** | `test_waiters_gauge_counts_the_waiting_requests_and_returns_to_zero`, `test_metrics_are_visible_to_the_operator` (`GET /health/upstream` → `gauges`), log w [03-live-singleflight-log.txt](results/au-005-007/03-live-singleflight-log.txt) |
| Dokumentacja: `docs/current_state.md` i ADR | **spełnione** | sekcja AU-005–007 w `current_state.md`, ADR-017, uzupełnienie ADR-005, README |
| ≥ 80% pokrycia | **spełnione** | patrz „Weryfikacja” |

## Zmiany względem zakresu zadania i decyzje do potwierdzenia przez właściciela

1. **`pg_advisory_lock` sesyjny zamiast `pg_advisory_xact_lock`.** Zakres wspomina oba warianty; wybrano sesyjny na osobnym połączeniu
   (ADR-017 §3), bo analiza trwa kilkanaście sekund i obejmuje wiele transakcji. Wymaga to bezpośredniego połączenia z PostgreSQL.
2. **Timeout oczekiwania = `503 ANALYSIS_IN_PROGRESS` + `Retry-After`, a nie „licz sam”.** Wariant „licz sam po timeoucie” odtwarza
   wzmacnianie ruchu do usług rządowych. Domyślny limit 90 s (`ANALYSIS_SINGLEFLIGHT_WAIT_SECONDS`) to decyzja do potwierdzenia.
3. **„Unikalność logiczna” bez indeksu.** Zrealizowana kontrolą po blokadzie (`reuse`: cache lub, dla `force_refresh`, wynik nie starszy niż
   żądanie); indeks częściowy odrzucałby prawidłowe zapisy (`force_refresh`, zmiana sygnatury).
4. **Dodatkowe zmiany nad zakres:** współdzielenie identyfikacji ULDK przez identyczne żądania (warunek „każde źródło 1×”),
   savepoint w `get_or_create_parcel` (wyścig pierwszego zapisu tej samej działki dawał 503 przy wyłączonym single-flight —
   odkryty testem), limity także na sondach zdrowia i endpointach z kluczem administracyjnym (ochrona przed zgadywaniem klucza),
   klucz IPv6 po prefiksie /64.
5. **Zmiany zachowania widoczne dla klienta:** odpowiedzi 429 wyszukiwarki adresów nie zawierają już pola `section: "search"`
   (wspólny format `ErrorResponse`); `GET /health/upstream` ma nowe pole `gauges`; kafle i endpointy dotąd bez limitu mają teraz progi
   (kafle 1200/min, pozostałe odczyty 300/min) — wartości domyślne do potwierdzenia.
6. **Nie zmieniono:** `docker-compose.yml` nadal publikuje port 8000 na `0.0.0.0` (po wyłączeniu zaufania do `X-Forwarded-For` nie jest
   to już wektor obejścia limitu, ale zawężenie do `127.0.0.1` to decyzja właściciela); limiter pozostaje w procesie (N workerów = N × limit).
7. **`backend/scripts/live_smoke_corpus.py` (Task 21.11) nadal nie istnieje**, więc pomiar „bez odpowiedzi 5xx na korpusie” nie został
   wykonany tym skryptem. Zamiast tego: 6 równoległych analiz i pomiar limitera na żywym stosie (powyżej) oraz pełny zestaw testów.
   Na żywym stosie discovery MPZP było podmienione (żadna działka korpusu nie wchodzi w tryb ręcznego symbolu).

## Weryfikacja

- **Backend** — kontener jak w `.github/workflows/ci.yml` (kopia repozytorium bez `.env` zamontowana tylko do odczytu jako
  `REPO_ROOT=/repo`, osobny projekt compose z PostGIS):
  `pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-fail-under=80` → **4057 passed**, 3 deselected, pokrycie **94,59%**.
  Pokrycie zmienionych modułów: `core/access_control.py` 100%, `core/rate_limit.py` 100%, `core/settings.py` 100%, `db/session.py` 100%, `routers/analyze.py` 100%, `routers/geocode.py` 100%,
  `routers/health.py` 100%, `services/singleflight.py` 94%, `services/cache.py` 100%.
- **Frontend** — obraz `--target test` (Node 22): `typecheck`, `test:coverage` (**524 testy**, pokrycie **97,49%**) i `next build` zielone.
- **Na żywym stosie** (osobny projekt compose, żywe usługi, frontend z obrazu produkcyjnego): wyniki w
  [`results/au-005-007/`](results/au-005-007/). Stos usunięty po pomiarach (`down -v`).
- Pierwszy przebieg testów e2e wykazał wyścig w `get_or_create_parcel` przy wyłączonym single-flight (patrz pkt 4).
