# Odbiór PV3-21 — hybryda jako domyślna, jeden silnik, regresja (Task 20.21)

Data: 5 października 2026 r. Baza kodu: `178f70b` z **niezacommitowanymi** zmianami (także PV3-18–20 z tej samej daty;
`docs/evaluation` i `docs/adr` są w `.gitignore` — nowe pliki wymagają `git add -f`). Decyzje: aneks PV3-21 w
[ADR-012](../adr/ADR-012-mpzp-llm-extraction.md), runbook [§10](../operations/mpzp-llm.md).

## Stan zadania: **częściowo wykonane — przełączenie zablokowane przez zależność**

Przełączenia domyślnego trybu **nie wykonano** i nie wolno go wykonać: zadanie wymaga decyzji `GO` z Task 20.17 i
okresu cienia bez regresji, a bramka 20.17 ma stan `NOT_DECIDABLE` (brak zbioru końcowego z Task 20.2, biegu hybrydy
na żywo, badania zmienności i przeglądu ręcznego ≥ 100 wartości przez człowieka), ścieżka modelu nigdy nie działała
w `hybrid_shadow` na ruchu, a przypięta para nie ma ewaluacji `--live`. Wszystko, co od tych wejść nie zależy, jest
zrobione, a samo przełączenie jest zablokowane w CI do czasu ich spełnienia.

## Kryteria akceptacji

| Kryterium | Stan | Dowód |
|---|---|---|
| Domyślny tryb przełączony wyłącznie po udokumentowanej decyzji `GO` z Task 20.17 i okresie cienia bez regresji metryk | **niespełnione — zablokowane (świadomie)**; mechanizm wymuszający gotowy | `app/core/mpzp_parser_rollout.json` (zapis przesłanek: decyzja bramki ze skrótem raportu, progi okresu cienia, raport cienia, decyzja właściciela, tryb wycofania); `scripts/check_parser_default_switch.py` (ocena: `READY`/`NOT_READY`/naruszenie); `scripts/mpzp_shadow_report.py` (raport okresu cienia z logów); `tests/test_mpzp_parser_rollout.py` — CI odrzuci zmianę domyślnego trybu bez `READY`. Dziś: `NOT_READY` (`gate_not_go`, `pin_not_ready`, `shadow_requirements_unconfirmed`, `shadow_observation_missing`, `owner_decision_missing`) |
| W repozytorium jeden silnik ekstrakcji; dotychczasowe wejścia działają przez niego | **spełnione** | liczby: `quantity_engine` (PV3-07); zapisy opisowe: nowy `planning/domain/descriptive_engine.py`, używany przez `services/mpzp_parser_descriptive.py` (parser, oba tryby) i `planning/domain/rules.py` (reguły jednostek prawnych). Test na AST (`test_descriptive_engine.py`) pilnuje, że moduły parsera i `rules.py` nie mają wzorców ustaleń; test „ten sam tekst → te same ustalenia w obu wejściach” (`test_planning_rules.py`). Martwy kod usunięty |
| Testy regresji opisują stan faktyczny i nie mają słabszych asercji | **spełnione** | `test_mpzp_parser_regression.py`: każdy dokument w trybach `legacy` i `v3` (20 przypadków zamiast 10); nowa asercja `expected_values` (dokładny zbiór); luki zamknięte przez silnik dopisane jako oczekiwania (Łódź, Poznań); `expected_found: false` — tylko 2 prawdziwe braki w uchwale (lista zamknięta testem); `mode_overrides` tylko dla ręcznej weryfikacji strefy, z przyczyną (testy walidacji nadpisań). Żadnej asercji nie usunięto; test aliasu `_parse_polish_number` przeniesiono do `test_shared_numbers.py`; test dachu w regułach zmieniono na dokładną formę kanoniczną |
| Wycofanie (powrót do trybu deterministycznego) sprawdzone i opisane | **spełnione** | runbook §10.3 (kill switch / `MPZP_PARSER_MODE=legacy` / wydanie) i §10.4 (usunięcie `legacy` po jednym wydaniu); próba `scripts/rehearse_llm_runbook.py` kroki 15–17: po przełączeniu na `hybrid` i powrocie do `legacy` wynik 10/10 dokumentów identyczny z migawką, 0 wartości modelu, 0 żądań, inna sygnatura cache, komponent `disabled` (17/17 kroków, test `test_llm_runbook_rehearsal.py`) |
| ≥ 80% pokrycia, progi CI nieobniżone | **spełnione** | kontener jak w CI (`REPO_ROOT=/repo`, repo tylko do odczytu, bez `.env`): **3291 passed**, pokrycie **94,30%**; `descriptive_engine.py` 98%, `mpzp_parser_descriptive.py` 100%, `rules.py` 99% |

Kontrakt końcowy z zadania: wersje parsera i sygnatura cache zaktualizowane (`mpzp-parser/3.1-det`, blokowy
`3.1-det+scope.1`, `MPZP_RESULT_SCHEMA_VERSION` 2.6, `mpzp-rules/2.0`; sygnatura cache zawiera kontrakt i wersję parsera);
tryb `legacy` zostaje — po przełączeniu jedno wydanie jako wycofanie (zapis `legacy_retention`), usunięcie opisane w §10.4.
Nowej zależności, migracji, endpointu ani zmian frontendu nie było (head migracji: `030`).

## Zmiany wyniku po ujednoliceniu silnika (sprawdzone z tekstem uchwał)

| Wejście | Zmiana | Ocena |
|---|---|---|
| parser, Łódź 6.8.MW/U (oba tryby) | +2 przeznaczenia podstawowe, +3 uzupełniające (lista po etykiecie „przeznaczenie podstawowe:”) | luka zamknięta |
| parser, Łódź (oba tryby) | zakaz „…minimum 4” → „…minimum 4,0 m” | poprawka (przecinek dziesiętny) |
| parser, Poznań 2U (oba tryby) | +„zieleń urządzona” (etykieta obok podpunktu sekcji) | luka zamknięta |
| parser, Kraków MN.1 (`legacy`) | +3 zakazy przełamane w wierszu PDF | luka zamknięta |
| reguły (korpusy regresji i BK-603) | brak „przeznaczeń” z definicji słowniczka i wartości „a)”; dachy w formie kanonicznej bez duplikatów; zakazy zawinięte w wierszu i z liczbą dziesiętną; przeznaczenie z sekcji a)/b) | poprawka / luka zamknięta |
| reguły | zakaz kończy przecinek (semantyka parsera): wyliczenie „zakaz lokalizacji obiektów, urządzeń…” daje krótszy zakaz | znany kompromis, zachowany z parsera |
| wartości liczbowe | bez zmian (migawka trybów porównana; kalibracja odtworzona — dane i progi identyczne) | — |

## Polecenia odbiorowe

```bash
cd backend
python3 scripts/check_parser_default_switch.py check --verify-files   # dziś: NOT_READY, kod 3
python3 scripts/rehearse_llm_runbook.py                              # 17/17 (kroki 15–17: przełączenie i wycofanie)
python3 scripts/freeze_mpzp_parser_modes.py --check                  # migawka trybów aktualna
python3 scripts/calibrate_mpzp_confidence.py --check                 # kalibracja odtwarza się z danych
python3 -m pytest tests/test_mpzp_parser_regression.py tests/test_descriptive_engine.py tests/test_planning_rules.py \
  tests/test_mpzp_parser_rollout.py tests/test_mpzp_parser_modes.py tests/test_llm_runbook_rehearsal.py -q
```

## Czego brakuje do przełączenia (kolejno, runbook §10.1)

1. Zbiór końcowy z Task 20.2, bieg hybrydy `--live`, badanie zmienności i przegląd ręczny ≥ 100 wartości przez człowieka
   → bramka 20.17 `GO` → `check_parser_default_switch.py record-gate`.
2. Ewaluacja `--live` przypiętej pary (`check_llm_pin.py record-evaluation`).
3. Potwierdzenie przez właściciela progów okresu cienia (dziś **propozycja**: ≥ 14 dni, ≥ 200 porównań, rozbieżność
   ≤ 0,05, błędy ≤ 0,05, degradacja ≤ 0,20, odrzucenia ≤ 0,30, kontrola dryfu `ok` w oknie).
4. Okres w `hybrid_shadow` z codziennym zbieraniem logu i kontrolą dryfu → `mpzp_shadow_report.py` → `record-shadow`.
5. Decyzja właściciela (ADR-012 + rekord), potem przełączenie w jednym wydaniu (§10.2).

## Ograniczenia

- Okres cienia i jego progi nie były nigdy mierzone; raport cienia przetestowano na syntetycznych wierszach logu w
  formacie aplikacji, nie na prawdziwym ruchu.
- Zmiany wyniku sprawdzono z tekstem 10 dokumentów regresji i dokumentów korpusu BK-603 (anotacje AI); ewaluator BK-603 nie
  ocenia parametrów opisowych, więc dla nich nie ma metryki ilościowej.
- Poza zakresem (zgłoszone osobno): `extract_planning_rules` kończy się wyjątkiem dla całej jednostki prawnej przy
  minimalnej intensywności 0 (Raszków, Białystok) — błąd sprzed zmiany; polityka „> 0” jest wspólna z parserem
  (ostrzeżenie) i bramką G6 (odrzucenie), więc jej zmiana wymaga decyzji właściciela.
