# Odbiór PV3-15, PV3-16 i PV3-17 (Epic 20B/20C)

Data: 5 października 2026 r. Baza kodu: `644e39a` z niezacommitowanymi zmianami PV3-03–14 i tych zadań
(nic nie zostało zacommitowane; `docs/evaluation` i `docs/adr` są w `.gitignore` — nowe pliki wymagają
`git add -f`). Decyzje: aneks PV3-15–17 w [ADR-012](../adr/ADR-012-mpzp-llm-extraction.md) oraz nowy
[ADR-014](../adr/ADR-014-llm-data-handling.md).

## Stan zadań

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| PV3-15 — budżet, limity, bezpieczna degradacja | **wykonane (offline)** | Progi dobowe/miesięczne wyprowadzone z ADR-012 i **zaakceptowane przez właściciela 2026-10-05**; opóźnienie zmierzone na opóźnieniach z PV3-01, nie na nowym biegu |
| PV3-16 — dane, prompt injection, sekrety | **wykonane** | ADR ma numer **014** (013 zajęty); notatka prawna to stan wiedzy + oświadczenie właściciela, nie opinia prawna |
| PV3-17 — ewaluacja A/B i bramka go/no-go | **nie da się zakończyć — zablokowane** | Brak zbioru końcowego (Task 20.2), brak biegu hybrydy na żywo i 3 biegów zmienności, brak przeglądu ręcznego ≥ 100 wartości przez człowieka. Wykonano narzędzia, bieg `legacy`/`v3` i raport bramki: **`NOT_DECIDABLE`** |

## Polecenia odbiorowe

```bash
cd backend
python3 -m pytest tests/test_failure_injection_mpzp_llm.py tests/test_llm_data_handling.py \
  tests/test_llm_prompt_injection.py tests/test_mpzp_quality_gate.py tests/test_migration_030.py -m "not integration" -q
python3 scripts/secret_scan.py ..                     # skanowanie sekretów (krok CI)
python3 scripts/mpzp_quality_gate.py gate --results ../docs/evaluation/results/parser-v3 \
  --output-dir ../docs/evaluation/results/parser-v3   # kod 3 = NOT_DECIDABLE
docker compose exec backend touch /var/lib/dzialki/llm-disabled   # kill switch (zdjęcie: rm)
```

## PV3-15 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Każdy scenariusz wstrzykiwania → wynik deterministyczny, `partial`, kod ostrzeżenia; bez wyjątku i częściowego zapisu | spełnione | `test_failure_injection_mpzp_llm.py`: 10 scenariuszy z issue (timeout, 429, 5xx, naruszenie schematu, ucięte wyjście, cytat nie znaleziony, tekst udający polecenie, inny identyfikator modelu, pusta odpowiedź, wyłącznik otwarty) przez **prawdziwy adapter Gemini** (`respx`) — parser i **pełna analiza na PostGIS** (snapshot i wiersze parametrów = `v3`, brak wartości `llm_verified`, brak wiszących rezerwacji w rejestrze); dodatkowo awaria adaptera spoza portu (`MemoryError`), współbieżność, częstotliwość, rejestr niedostępny, limity analizy/dokumentu/żądania, termin |
| Limity dobowe i miesięczne twarde (zegar kontrolowany); przekroczenie wyłącza ścieżkę bez wpływu na resztę | spełnione | 4 warianty (koszt/tokeny × doba/miesiąc): przy limicie żadne żądanie nie wychodzi, wynik = `v3`, w następnym okresie (UTC) ścieżka wraca; rejestr SQL: 25 równoległych rezerwacji przy limicie na 10 → dokładnie 10 przyjętych (blokada doradcza PostgreSQL) |
| Dodatkowe opóźnienie p95 w budżecie ADR-012 (bez sieci) | spełnione | 30 zmierzonych w PV3-01 opóźnień (`fixtures/mpzp_llm_latency`) odtworzonych przez prawdziwy potok w skali 1:1000 i przeskalowanych: p95 dodatkowego opóźnienia ≈ p95 modelu (8,3 s) ≤ 30 s; narzut potoku p95 < 0,5 s; wolny model przerywany po budżecie czasu (0,2 s zamiast 5 s) |
| Metryki: wywołania, odrzucenia per bramka, degradacje, koszt szacowany | spełnione | liczniki `llm.calls`, `llm.verifier.rejected.G1…G8`, `llm.verifier.code.*`, `llm.degraded`, `llm.unavailable.<powód>`, `llm.rejection_warnings`, `llm.tokens.input/output`, `llm.cost.estimated_microusd` (testy sprawdzają wartości) |
| ≥ 80% pokrycia nowych modułów | spełnione | `infrastructure/llm/budget.py` 98%, model `mpzp_llm_usage` 100%, `llm_pipeline` 99%, `mpzp_parser_hybrid` 94%, `composition` 89% |

Kontrakt: limity **w analizie** (warstwa application, `BudgetTracker`): 6 żądań i 12 000 tokenów wejścia na
analizę, 12 000 na dokument, 4000 na żądanie; termin propagowany od startu analizy (90 s) i budżet czasu
ścieżki modelu (30 s od pierwszego żądania) — trwające żądanie jest przerywane (`deadline_exceeded`).
Limity **między analizami** (adapter `infrastructure/llm/budget.py`, dekorator portu): częstotliwość
(60/min) i współbieżność (4) w procesie, doba (2 mln tokenów, 5 USD) i miesiąc (40 mln, 100 USD) w UTC w
rejestrze `mpzp_llm_usage` (migracja **030**): rezerwacja najgorszego przypadku przed wysłaniem, rozliczenie
po odpowiedzi, zwolnienie żądania niewysłanego; brak rejestru = brak wywołania. Wyłącznik awaryjny jest
wspólny dla procesu (przeżywa analizę). Kandydaci odrzuceni przez bramki dają ostrzeżenie
`MPZP_LLM_CANDIDATES_REJECTED` (liczniki per bramka) i `partial`, niedostępność — `MPZP_LLM_UNAVAILABLE`.

Wyprowadzenie progów dobowych/miesięcznych: koszt ≤ 0,05 USD na analizę (ADR-012) × 100 analiz/dobę = 5 USD;
miesiąc 100 USD ≈ 2000 analiz; tokeny ≈ 18 tys. na analizę (12 tys. wejścia + ~6 tys. wyjścia). To
**zaakceptowane przez właściciela 2026-10-05** (ADR-012, aneks PV3-15–17, pkt 5); limity wydatków projektu w AI Studio są dodatkowym, niezależnym zabezpieczeniem.

## PV3-16 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Żądanie zawiera wyłącznie dozwolone pola; żadnego identyfikatora działki ani użytkownika | spełnione | `test_llm_data_handling.py`: prawdziwe żądanie HTTP (klucze ciała, `generationConfig`, nagłówki, nagłówek wiadomości z danymi wg listy dozwolonej) oraz pełna analiza przez `POST /analyze` z identyfikatorem działki, `X-Forwarded-For`, `Authorization`, `User-Agent` i adresem — żaden ani token dostępu z odpowiedzi nie trafia do żądania; `validate_user_text` w kontrakcie blokuje każde pole spoza listy |
| Prompt injection: tekst dokumentu nie zmienia zachowania ani nie omija bramek; wynik z kodem odrzucenia | spełnione | korpus 14 przypadków `fixtures/mpzp_prompt_injection/cases.json` (model **wykonujący** wstrzyknięcie): wynik = `v3`, brak wartości z modelu, kod (`quote_suspicious`, `quote_not_in_block`, `symbol_not_in_block`, `raw_value_not_in_quote`, `document_text_unsafe`, `block_too_large`, `schema_violation`) w ostrzeżeniu i licznikach; instrukcja systemowa stała, dane tylko między znacznikami. Odrzucenia kontraktu (przed bramkami) są teraz mapowane na bramki |
| Klucz nie występuje w logach, wyjątkach, artefaktach ani repozytorium; `test_no_env_files_in_repository` przechodzi | spełnione | echo klucza w odpowiedzi 400 dostawcy nie trafia do logów, wyniku, cache, rejestru zużycia ani `repr`; filtr redakcji w `core/logging.py` (klucze, `Authorization`, `Bearer`/JWT, `key=`/`token=`); skaner `scripts/secret_scan.py` w CI (przed utworzeniem `.env`) i w pytest; `test_no_env_files_in_repository` przechodzi w kontenerze jak w CI |
| ADR zawiera model zagrożeń, decyzje o danych, kill switch i warunki dostawcy | spełnione | ADR-014 (T1–T8, lista pól, runbook rotacji klucza, kill switch 3-poziomowy, notatka prawna, dostawca jako przetwarzający poza katalogiem źródeł) |
| ≥ 80% pokrycia zmienionych modułów | spełnione | `core/logging.py` 92%, `extraction_contract` 99%, `gemini_provider` 100%, `zone_scope` 97%, `mpzp_parser_options` 100% |

**Błąd znaleziony przy okazji:** korpus wykazał kwadratowy koszt `zone_scope.range_mentions` dla długiego
tokenu bez odstępu (100 000 znaków ≈ 2 min parsowania rdzenia deterministycznego — odmowa usługi przez treść
dokumentu). Poprawka: dopasowanie zakresu symboli zaczyna się tylko na granicy słowa; wynik regresji bez
zmian (`freeze_mpzp_parser_modes.py --check`), 300 000 znaków ≈ 0,3 s (test regresji czasu).

Poprawka porządkowa: moduły `test_migration_029` i `test_migration_030` dopisano do listy izolowanych baz w
`tests/conftest.py` (BK-306) — wcześniej 029 cofał migracje na wspólnej bazie testowej. Moduł
`test_migration_028` (PV3-08) nadal działa na wspólnej bazie — do decyzji drugiej sesji.

## PV3-17 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód / brak |
|---|---|---|
| Kryteria zamrożone w ADR-012 przed biegiem końcowym; raport podaje wynik wobec każdego z licznikami | narzędzie gotowe, **bez wyniku hybrydy** | kryteria zamrożone 2026-10-05 (ADR-012); `mpzp_quality_gate.py` liczy 9 kryteriów z licznikami, niezależnie weryfikuje cytaty względem tekstu korpusu; `gate_report.md` — wszystkie kryteria „nie zmierzono” |
| Powtórny bieg na tym samym manifeście i odpowiedziach → identyczne metryki merytoryczne | spełnione dla `legacy`/`v3` | `determinism.json` (`identical: true`, 2 powtórzenia); dla `hybrid` zapewnia to odtwarzanie (`--llm-replay`), ale brak nagranych odpowiedzi |
| Wynik przeglądu ręcznego ≥ 100 wartości `ai_candidate` i jawne ograniczenia | **niespełnione** | arkusz i ocena gotowe (`review-sheet`, `review-score`), ale nie ma wartości `ai_candidate` z modelu ani recenzenta-człowieka |
| Decyzja go/no-go z uzasadnieniem, bez przedstawiania rekomendacji jako gwarancji | **NOT_DECIDABLE** | `gate_report.md` (braki: zbiór końcowy, hybryda, zmienność, przegląd); decyzję zapisuje właściciel w ADR-012 |
| Polecenia, wynik scenariusza końcowego i artefakty | dołączone | `docs/evaluation/results/parser-v3/` (README z procedurą, `legacy/`, `v3/`, `comparison/`, `gate_report.*`) |

Obserwacja na BK-603 (informacyjna, rozwojowa): `v3` precision 1,000 (248/248), recall 0,992 (248/250),
`source_consistent` 0,975 (355/364) — **poniżej progu 0,98**; `legacy` recall 0,832, `source_consistent`
0,768. Ewaluator: silnik `hybrid` raportuje odrzucenia per bramka i zużycie per próbkę (poprawiony błąd
PV3-14: tokeny i koszt były sumowane narastająco).

## Wyniki testów

Backend — kontener jak w CI (PostGIS w osobnym projekcie compose, kopia repozytorium bez `.env` jako
`REPO_ROOT` tylko do odczytu), 2026-10-05:

```text
pytest -m "not docker_cli" --cov=app --cov-report=term-missing --cov-fail-under=80
3082 passed, 3 deselected, 2 warnings in 335.94s
Required test coverage of 80% reached. Total coverage: 94.10%
```

Nowe pliki testów: `test_failure_injection_mpzp_llm.py` (42), `test_llm_data_handling.py` (10),
`test_llm_prompt_injection.py` (17), `test_mpzp_quality_gate.py` (9), `test_migration_030.py` (2).
Frontend bez zmian w tej iteracji.

## Ograniczenia i ryzyka

1. Nic nie zmierzono na żywo w tych zadaniach; scenariusze awarii używają zamrożonych odpowiedzi HTTP.
2. Limity częstotliwości i współbieżności są per proces (N procesów → N × limit); twarde są doba/miesiąc.
3. Rezerwacja zakłada najgorszy przypadek wyjścia (`max_output_tokens`), więc blisko limitu ostatnie analizy
   mogą zostać zatrzymane wcześniej niż faktyczne zużycie by wymagało (świadomie zachowawczo).
4. Kill switch plikowy nie przeżywa odtworzenia kontenera — do trwałego wyłączenia `MPZP_LLM_ENABLED=false`.
5. Heurystyki G3 mogą odrzucić poprawną wartość (bezpieczne — zostaje wynik deterministyczny).
6. PV3-17 wymaga ludzi i decyzji właściciela; narzędzia tego nie zastąpią.
