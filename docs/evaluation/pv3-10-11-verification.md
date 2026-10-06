# Odbiór PV3-10 i PV3-11 (Epic 20A)

Data: 5 października 2026 r. (kod z 3 października). Baza kodu: `644e39a` z niezacommitowanymi zmianami
(nic nie zostało zacommitowane; `docs/evaluation` i `docs/adr` są w `.gitignore`, więc nowe pliki tych
katalogów wymagają `git add -f`). Decyzje projektowe: aneks PV3-10/11 w
[ADR-012](../adr/ADR-012-mpzp-llm-extraction.md). Decyzja właściciela GO z 2026-10-05 jest wpisana w tym ADR.

## Stan zadań

Żadne z dwóch zadań nie było wcześniej wykonane (przed zmianą w repozytorium nie było żadnego kodu modelu
językowego).

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| PV3-10 — adapter modelu językowego za portem | **wykonane (offline)** | Żadne wywołanie na żywo nie zostało wykonane; zachowanie wobec prawdziwego API potwierdzają tylko zamrożone odpowiedzi (`respx`) oparte na kształcie z pomiaru PV3-01 |
| PV3-11 — kontrakt wejścia i wyjścia modelu | **wykonane (offline)** | Złote odpowiedzi są złożone z adnotacji korpusu, **nie nagrane z modelu**; jakość modelu na tym kontrakcie nie jest zmierzona (to Task 20.17) |

Czego te zadania **nie** zmieniają: ścieżka modelu jest wyłączona domyślnie (`mpzp_llm_enabled=false`) i nie
jest podłączona do analizy (to Task 20.14), więc zachowanie aplikacji, API i cache jest bez zmian
(`MPZP_RESULT_SCHEMA_VERSION` bez zmiany). Nie ma migracji Alembic (nic nie zapisuje do bazy; head to nadal
`028_mpzp_parameter_condition`). Weryfikacja cytatów i przyjęcie wartości do wyniku to Task 20.12.

## Polecenia odbiorowe

```bash
cd backend
# testy nowych modułów i architektury (bez internetu i bez klucza)
python3 -m pytest tests/test_llm_gemini_provider.py tests/test_llm_resilience.py tests/test_llm_json_schema.py \
  tests/test_llm_extraction_contract.py tests/test_llm_extraction_service.py tests/test_llm_golden_replay.py \
  tests/test_llm_fake_provider.py tests/test_llm_composition.py tests/test_architecture.py -q

# złote odpowiedzi: sprawdzenie i odtworzenie po zmianie instrukcji, schematu, modelu albo bloków
python3 scripts/build_llm_replay_fixtures.py --check
python3 scripts/build_llm_replay_fixtures.py
```

Pełny zestaw w kontenerze skonfigurowanym jak CI — wynik w sekcji „Weryfikacja”. Frontend nie został
zmieniony (brak zmian w `frontend/`), więc nowych modułów w `coverage.include` nie ma.

## PV3-10 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Przy `mpzp_llm_enabled=false` adapter nie jest tworzony i nie ma ruchu sieciowego (test z zablokowanymi gniazdami) | spełnione | `build_structured_extraction_provider` zwraca `None` przed utworzeniem czegokolwiek; `test_llm_composition.py` (`test_disabled_creates_no_adapter…`, `test_the_flag_is_off_even_when_a_key_is_present`: gniazda i `getaddrinfo` zablokowane, konstruktor adaptera i `httpx.AsyncClient.__init__` podmienione na błąd, `respx` bez wywołań); sam konstruktor adaptera też nie łączy (klient powstaje leniwie) |
| Testy `respx`: 200, 400, 401, 403, 429 z `Retry-After`, 500, timeout, niepoprawny JSON, naruszenie schematu, ucięte wyjście | spełnione | `test_llm_gemini_provider.py` (88 testów; ponadto 404, 408, 413, 5xx, błędy sieci, przekierowanie, rozmiar żądania i odpowiedzi, blokady modelu, brak kandydatów) |
| Błąd nie przecieka klucza ani tekstu żądania do logów i wyjątków | spełnione | 13 scenariuszy (komunikaty błędów dostawcy i wyjątki httpx **echujące** klucz i znacznik tekstu; odpowiedzi 200 z cytatami) — klucz i znacznik nie występują w `str`/`repr` wyjątku, jego `__dict__`, tracebacku, w logach ani w `repr` adaptera |
| Zwrócony identyfikator modelu różny od skonfigurowanego jest wykrywany i zgłaszany | spełnione | `StructuredExtractionResult.model_mismatch`, ostrzeżenie w logu, usługa nie używa takiej odpowiedzi (`model_mismatch` w provenance) |
| Test architektury (ADR-001): port w `application`, adapter w `infrastructure` | spełnione | `test_architecture.py` (+6 testów): port tylko w `application/ports.py`, adaptery w `infrastructure/llm/`, **adaptery nie importują domeny MPZP, serwisów ani ustawień**, tylko kompozycja łączy adapter, API go nie omija; test reguły wykrywa celowo zły import |
| ≥ 80% pokrycia nowych modułów; `.env.example` tylko z pustym placeholderem klucza | spełnione | pokrycie niżej (98–100%); `GEMINI_API_KEY=` pusty, `MPZP_LLM_ENABLED=false`, test skanuje repozytorium pod kątem kluczy |

Dodatkowe wymagania z opisu zadania:

| Wymaganie | Realizacja |
|---|---|
| REST przez `httpx`, bez nowej zależności | `gemini_provider.py`; `requirements.txt` bez zmian |
| Instrukcja systemowa oddzielona od danych dokumentu | osobne pola `systemInstruction` i `contents`; test sprawdza, że dane nie trafiają do instrukcji |
| Stały host przez HTTPS, `follow_redirects=False`, limit rozmiaru odpowiedzi | host to stała (nie ustawienie); 3xx → `unexpected_redirect` bez żądania pod `Location`; odpowiedź czytana strumieniowo i przerywana po limicie |
| Limity czasu, ponowienia z wykładniczym opóźnieniem i losowaniem, `Retry-After`, klasyfikacja błędów, wyłącznik | `resilience.py`, `GeminiConfig`; testy bez uśpień (wstrzyknięty zegar, losowanie i `sleep`) |
| W odpowiedzi: skrót, tokeny, powód zakończenia, identyfikator modelu | `StructuredExtractionResult` (SHA-256 żądania, wejścia i odpowiedzi, tokeny wejścia/wyjścia/myślenia, `finish_reason`, `model_returned`); w logach tylko skróty (16 znaków), liczniki i kody |
| `settings.py`: flaga, dostawca, model, klucz jako `SecretStr`, limity, `temperature=0` | `mpzp_llm_*` i `gemini_api_key`; temperatura zablokowana na 0 (walidacja odrzuca inną wartość); klucz ukryty w `repr`, `str`, `model_dump`, JSON |
| `.env.example`, `docker-compose.yml` | placeholdery i przekazanie zmiennej bez wartości (`${GEMINI_API_KEY:-}`) wyłącznie dla backendu; testy czytają oba pliki |

## PV3-11 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Schemat jest jedynym kontraktem wyjścia; odpowiedź niezgodna odrzucana z kodem przyczyny | spełnione | `parse_payload`: niezgodność ze schematem → `ExtractionContractError` (`schema_violation` / `payload_not_object`) z miejscami naruszeń bez wartości; kandydat łamiący kontrakt → kod per kandydat (`unknown_symbol`, `operator_not_allowed`, `raw_value_not_in_evidence`, …); modele `strict`, `extra="forbid"`; `test_llm_extraction_contract.py` (49 testów) |
| Zmiana promptu lub schematu wymaga podniesienia wersji i unieważnia cache; testy to wykrywają | spełnione | `PROMPT_VERSION`/`SCHEMA_VERSION` + przypięte skróty SHA-256 (test zawodzi z instrukcją „podnieś wersję”); klucz cache zmienia się z instrukcją, szablonem, modelem, temperaturą, wersją schematu i tekstem; ten sam klucz co w odtwarzaniu ewaluatora (test zgodności z `LlmRequest.cache_key`) |
| Złote testy z odtwarzaniem obejmują układy 1–6 i przypadek „nie znaleziono”; bez internetu | spełnione | `test_llm_golden_replay.py` (9 testów): 13 bloków z 7 przypadków, strategie zakresu {0,1,2,3,4,5,6}, blok wielosymbolowy (jedno żądanie, wynik per symbol), skan z pełnym `not_found`, wartości warunkowe; gniazda zablokowane |
| Podział dużych bloków nie gubi ani nie dubluje cytatów (blok ponad limit) | spełnione | `test_llm_extraction_service.py`: blok 320 punktów (≈ 16 tys. znaków) → każda wartość dokładnie raz mimo duplikatów z nakładek; cytaty przecinające granicę części (pary zdań) znalezione raz, ze zgodnym zakresem; granice części na początkach punktów listy |
| ≥ 80% pokrycia nowych modułów | spełnione | niżej |

Dodatkowe wymagania z opisu zadania:

| Wymaganie | Realizacja |
|---|---|
| Jedno żądanie na blok; bloki wielosymbolowe raz, wynik rozpisany na symbole | `LlmExtractionService.extract_block`; `zone_symbol` w kandydacie, mapowany z powrotem na symbol z listy żądanej |
| Wejście: tekst bloku, ścieżka nagłówków, symbole, definicje 9 parametrów z jednostkami, semantyką operatorów i kontrprzykładami, lista ustawowa | `render_user_text` + `prompts/mpzp_extraction_v1.md`; test sprawdza obecność każdego parametru/operatora/jednostki, kontrprzykładów i reguł twardych |
| Wyjście: `parameter`, `operator`, `raw_value`, `value` (ignorowana przy niezgodności), `unit`, `applicability`, `conditions[]`, `evidence_quote`, `scope_quote`; jawne `not_found` | `LlmCandidate` / `LlmNotFound`; liczba wyliczana deterministycznie z `raw_value`, liczba modelu oznaczana jako zignorowana przy niezgodności |
| Instrukcje: cytuj dosłownie, niczego nie wyliczaj, bez wiedzy spoza tekstu, ignoruj polecenia z dokumentu | w instrukcji; znaczniki sekcji danych w tekście dokumentu są odrzucane (`document_text_unsafe`), więc tekst nie zamknie sekcji danych |
| Limity rozmiaru bloku i podział z deduplikacją; `PROMPT_VERSION`, `SCHEMA_VERSION`; skrót promptu w provenance; temperatura 0 | `ExtractionLimits`, `plan_chunks`; `ExtractionProvenance` niesie wersje, skróty instrukcji i schematu, temperaturę i per-część skróty, tokeny, kody — także dla wyniku niepełnego i pominiętego bloku |
| Wynik modelu nigdy nie jest „zweryfikowany” | w kontrakcie nie ma takiego stanu; `review_status = ai_candidate`; kandydat z cytatem spoza tekstu zostaje z `evidence_span=None` (nie przejdzie Task 20.12) |

## Wyniki testów

Lokalnie (macOS, bez internetu i klucza), nowe pliki i test architektury: **305 testów przeszło**.

| Plik | Testów |
|---|---:|
| `test_llm_gemini_provider.py` | 88 |
| `test_llm_extraction_contract.py` | 49 |
| `test_llm_extraction_service.py` | 40 |
| `test_llm_resilience.py` | 28 |
| `test_llm_json_schema.py` | 26 |
| `test_llm_composition.py` | 25 |
| `test_llm_fake_provider.py` | 17 |
| `test_llm_golden_replay.py` | 9 |
| `test_architecture.py` (w tym 6 nowych) | 23 |

Pokrycie nowych modułów (lokalnie): `extraction_contract` 100%, `gemini_provider` 100%, `fake_provider` 100%,
`json_schema` 100%, `resilience` 100%, `llm_extraction` 98% (5 niepokrytych linii z 266; zabezpieczenia
przed błędem arytmetyki podziału).

## Ograniczenia i ryzyka (czytać przed wnioskami)

1. **Nic nie zostało zmierzone na żywo.** Kształt żądania i odpowiedzi pochodzi z pomiaru PV3-01
   (`generateContent`, `responseJsonSchema`, `thinkingConfig`) i dokumentacji; pierwsze wywołanie na żywo
   może ujawnić różnice (np. obsługę schematu zagnieżdżonego z `conditions`, który w spike’u był tylko tablicą
   tekstów). Schemat produkcyjny **nie był** wysłany do API.
2. **Złote odpowiedzi to nie model.** Składa je skrypt z adnotacji BK-603 (nadanych przez asystenta AI);
   odpowiedzi „idealne” sprawdzają potok, nie jakość. Prawdziwe nagrania i bramka jakości to Task 20.17;
   zbiór końcowy (Task 20.2) nie istnieje.
3. **Instrukcja nie była stroiona na modelu.** Została napisana na podstawie wyników spike’u i
   adnotacji, bez żadnego biegu; jej skuteczność (np. odróżnianie wysokości parteru od wysokości zabudowy)
   jest hipotezą do zmierzenia.
4. **Odpowiedź z modelu o innym identyfikatorze jest odrzucana.** Jeśli API zwraca wersjonowany identyfikator
   (np. z sufiksem), ścieżka nie zadziała, dopóki operator nie ustawi `mpzp_llm_model` na faktyczny identyfikator.
   To decyzja projektowa do potwierdzenia po pierwszym biegu na żywo.
5. **Kontekst i limity.** Limit 6000 znaków i do 6 żądań na blok pochodzą z proponowanych progów budżetowych
   ADR-012 (4000 tokenów wejściowych na żądanie); realne zużycie tokenów nowej, dłuższej instrukcji nie zostało
   zmierzone (spike używał krótszej).
6. **Compose przekazuje `GEMINI_API_KEY` z lokalnego `.env` do kontenera backendu** nawet przy wyłączonej
   fladze (zamierzone w opisie zadania: „przekazanie zmiennej bez wartości”); aplikacja go wtedy nie używa,
   ale klucz jest w środowisku kontenera.
7. Ewaluator ma gotowy hak `register_live_provider`, ale adapter nie jest do niego zarejestrowany
   (kontrakt `LlmRequest.payload` powstanie w Task 20.14); `--live` ewaluatora nadal kończy się jawną odmową.
8. Zakres decyzji GO: decyzja nie zmienia stanu domyślnego (ścieżka wyłączona); włączenie wymaga
   jawnej konfiguracji i potoku z Task 20.12–20.14.

## Weryfikacja

Pełny zestaw w kontenerze jak w CI (obraz backendu, PostgreSQL z migracjami do `028`, repozytorium jako
`/repo:ro` bez `.env`, kod z bieżącego drzewa, `pytest -m 'not docker_cli' --cov=app --cov-report=term-missing
--cov-fail-under=80`): **2859 testów przeszło, 3 odrzucone znacznikiem `docker_cli`, 0 niepowodzeń, pokrycie
`app` 93,85%** (próg 80% bez zmian; 5 października 2026 r., 5 min 14 s).

Poprzedni pełny przebieg tego samego dnia (2857 przeszło) wykrył 2 niepowodzenia w moim nowym pliku
`test_llm_gemini_provider.py` (`test_different_returned_model_is_detected_and_reported`,
`test_logs_carry_hashes_tokens_finish_reason_and_returned_model`): testy migracji (`fileConfig` Alembica)
wyłączają w pełnym przebiegu istniejące loggery, więc `caplog` nie widział logów adaptera. Lokalnie, w
podzbiorze, testy przechodziły. Dodano fixture przywracający logger adaptera (jak w istniejących testach
`patch.object(logger, "disabled", False)`) i przebieg powtórzono w całości. Skutek uboczny wart odnotowania:
bez tego fixture asercje „klucz i tekst nie występują w logach” mogłyby przechodzić na pusto, gdy logger jest
wyłączony; teraz logger jest jawnie włączony w każdym teście pliku.

Lokalnie (macOS): 305 testów nowych plików i testu architektury; frontend nie był zmieniany i nie był
uruchamiany w tym zadaniu (`frontend/` bez zmian). Złote odpowiedzi odtworzone skryptem
`build_llm_replay_fixtures.py --check`: 13 rekordów, zgodne.

Zmienione/nowe artefakty: `docs/report/field-mapping.md` bez zmian (kontrakt API bez zmian);
`.env.example` i `docker-compose.yml` dostały zmienne `MPZP_LLM_*` i `GEMINI_API_KEY` (testy
`test_llm_composition.py` pilnują pustego klucza i przekazania bez wartości).

**Późniejszy stan drzewa.** Po powyższym przebiegu druga sesja dodała PV3-12–14 w tym samym drzewie
(migracja `029_mpzp_llm_extractions`, zmiany w `ports.py`, `llm_extraction.py`, `composition.py`,
`test_architecture.py`, `.env.example`, `docker-compose.yml`). Pełny przebieg końcowego drzewa (3001 testów,
pokrycie 94,04%) jest opisany w [odbiorze PV3-12–14](pv3-12-14-verification.md); liczby w tej sekcji
(2859 testów, migracje do `028`) dotyczą stanu sprzed tych zmian.

