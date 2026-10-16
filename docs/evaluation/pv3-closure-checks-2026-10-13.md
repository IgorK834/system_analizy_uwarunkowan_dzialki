# Parser MPZP v3 i testy Docker — weryfikacja issues zamkniętych bez wykonania (#260, #275, #277, #279, #59)

Data: 13 października 2026 r. Baza kodu: `fcf6a9b` z niezacommitowanymi zmianami. Zakres: **sprawdzenie i opis**, bez zmiany kodu ścieżki parsera, bez biegów płatnych
(`--live`), bez zmiany domyślnego trybu parsera i bez zmiany workflow CI. Wszystkie wyniki poniżej pochodzą z poleceń uruchomionych tego dnia (offline, na hoście
albo w kontenerze `backend-test` osobnego projektu Compose). Poprzednie odbiory: [pv3-15-17-verification.md](pv3-15-17-verification.md), [pv3-18-20-verification.md](pv3-18-20-verification.md),
[pv3-21-verification.md](pv3-21-verification.md).

## Podsumowanie

| Issue | Stan faktyczny | Co blokuje | Kto musi działać |
|---|---|---|---|
| #260 (Task 20.2) zbiór końcowy, drugi anotator | **zablokowane** — jest narzędzie i walidator, nie ma zbioru | zatwierdzenie planu źródeł, pobranie ≥ 20 próbek z ≥ 10 nowych gmin, anotacje człowieka, drugi anotator ≥ 20% | właściciel + anotatorzy |
| #275 (Task 20.17) A/B i bramka go/no-go | **zablokowane** — raport `NOT_DECIDABLE` (odtworzony offline) | zbiór z #260, płatny bieg `--live`, 3 biegi zmienności, przegląd ręczny ≥ 100 wartości | właściciel (zgoda i koszt), recenzent |
| #277 (Task 20.19) monitoring i przypięcie modelu | **częściowo** — kod, testy i runbook są; brak biegu `--live` na zbiorze złotym | bieg `--live` (przypięcie ma `evaluation_not_recorded`), nigdy niewykonana kontrola dryfu u dostawcy | właściciel (zgoda i klucz) |
| #279 (Task 20.21) wygaszenie legacy | **zablokowane świadomie** — domyślny tryb pozostaje `legacy` | bramka: `gate_not_go`, `pin_not_ready`, brak okresu cienia, brak decyzji właściciela | właściciel |
| #59 (Task 9.2) E2E w Docker Compose | zob. sekcja „#59” | zob. sekcja „#59” | zob. sekcja „#59” |

## #260 — nowy zbiór końcowy i drugi anotator

**Jest (narzędzia, offline):** protokół anotacji [mpzp_annotation_protocol.md](mpzp_annotation_protocol.md); plan doboru źródeł [mpzp_corpus_v2_source_plan.md](mpzp_corpus_v2_source_plan.md)
(„nic nie zostało pobrane”, lista do zatwierdzenia); `backend/scripts/mpzp_corpus_tools.py` (`skeleton`, `agreement`, `coverage`, `download-log`, `freeze`); `backend/scripts/mpzp_annotation_agreement.py`;
walidator profilu `final-v2` w `backend/scripts/evaluate_mpzp_parser.py --validate-only --profile final-v2`.

**Brakuje (kryteria #260, wszystkie niespełnione):**

1. ≥ 20 nowych próbek z ≥ 10 nowych gmin — korpus ma 21 próbek z 13 gmin (14 „końcowych”, wszystkie z wcześniej użytych gmin; walidator: „14 final samples < 20”, łącznie 89 problemów profilu `final-v2`).
2. Zatwierdzenie przez właściciela listy dokumentów i źródeł **przed** pobraniem (wymóg zadania; plan czeka na zatwierdzenie).
3. Anotacja przez człowieka — obecne anotacje korpusu nie mają przeglądu człowieka (`independent_human_review: pending`); zbiór końcowy wymaga anotatorów-ludzi.
4. Drugi anotator dla ≥ 20% próbek, raport zgodności i dziennik rozstrzygnięć (`mpzp_corpus_tools.py agreement` jest gotowy).
5. Zamrożenie skrótem przed uruchomieniem jakiegokolwiek silnika (`mpzp_corpus_tools.py freeze`) oraz log pobrań z podstawą prawną.

Nic nie zostało pobrane w tej weryfikacji.

## #275 — ewaluacja A/B i bramka jakości

**Stan:** raport bramki odtworzony offline poleceniem

```bash
cd backend && python3 scripts/mpzp_quality_gate.py gate --results ../docs/evaluation/results/parser-v3 --output-dir <katalog-tymczasowy>
```

→ kod wyjścia 3, decyzja **`NOT_DECIDABLE`**, treść identyczna (poza znacznikiem czasu) z zapisaną w `docs/evaluation/results/parser-v3/gate_report.md`. Cztery braki: zbiór nie spełnia profilu `final-v2`; brak wyników silnika `hybrid`;
brak przeglądu ręcznego; brak badania zmienności (3 biegi). Zamrożone kryteria bramki (ADR-012, 2026-10-05): precision ≥ 0,95; recall ≥ 0,80; `source_consistent` ≥ 0,98; 0 przyjętych wartości bez zweryfikowanego cytatu;
błędy przypisania ≤ 2%; ECE ≤ 0,10; koszt ≤ 0,05 USD na analizę; dodatkowe opóźnienie p95 ≤ 30 s; ≥ 100 ocenionych ręcznie wartości `ai_candidate`.

Silniki deterministyczne na obecnym (rozwojowo-końcowym) zbiorze BK-603 — tylko informacyjnie, nie jest to wynik bramki:

| Silnik | Precision | Recall | `source_consistent` | ECE |
|---|---|---|---|---|
| `legacy` | 1,000 [0,982; 1,000] | 0,832 [0,781; 0,873] | 0,7684 | 0,0490 |
| `v3` | 1,000 [0,985; 1,000] | 0,992 [0,971; 0,998] | 0,9753 | 0,0247 |
| `hybrid` | — | — | — | — |

**Polecenia do wykonania po spełnieniu warunków** (wymagają zamrożonego zbioru z #260, klucza `GEMINI_API_KEY` w środowisku **bez wypisywania go**, zgody właściciela na wysłanie publicznych tekstów oraz kosztu poniżej;
bieg zapisuje odpowiedzi do katalogu odtwarzania z hashami, dalsze biegi są offline):

```bash
cd backend
# 1. Ewaluacja na żywo: silniki legacy, v3, hybrid na zamrożonym zbiorze końcowym (zapis odpowiedzi do fixtures)
python3 scripts/evaluate_mpzp_parser.py --corpus <manifest-final-v2.json> --engine legacy v3 hybrid --baseline legacy \
  --live --llm-replay tests/fixtures/mpzp_evaluation/llm_replay_final --output-dir ../docs/evaluation/results/parser-v3-final
# 2. Powtórzenie offline z zapisanych odpowiedzi: metryki merytoryczne muszą być identyczne
python3 scripts/evaluate_mpzp_parser.py --corpus <manifest-final-v2.json> --engine legacy v3 hybrid --baseline legacy \
  --llm-replay tests/fixtures/mpzp_evaluation/llm_replay_final --output-dir <katalog-tymczasowy>
# 3. Badanie zmienności: trzy biegi hybrid na żywo (każdy do osobnego katalogu odpowiedzi i wyników), potem zestawienie
python3 scripts/mpzp_quality_gate.py variance --runs <bieg1>/hybrid <bieg2>/hybrid <bieg3>/hybrid --output <zmiennosc.json>
# 4. Przegląd ręczny: arkusz ≥ 100 przyjętych wartości ai_candidate (wypełnia człowiek), potem ocena i bramka
python3 scripts/mpzp_quality_gate.py review-sheet --results ../docs/evaluation/results/parser-v3-final --output <arkusz.json> --n 100
python3 scripts/mpzp_quality_gate.py review-score --sheet <arkusz-wypelniony.json> --output <ocena.json>
python3 scripts/mpzp_quality_gate.py gate --results ../docs/evaluation/results/parser-v3-final --corpus <manifest-final-v2.json> \
  --review <ocena.json> --variance <zmiennosc.json> --output-dir ../docs/evaluation/results/parser-v3-final
```

**Szacunek kosztu** (z pomiaru ADR-012, 2026-10-02; **nie zmierzony na zbiorze końcowym**): ok. 0,014 USD na żądanie przy cenie od 2027 r. (ok. połowa przy cenie promocyjnej do 2026-12-31), jedno żądanie na blok strefy.
Obecny korpus ma 47 stref (19 rozwojowych, 28 końcowych), więc zbiór końcowy o podobnej gęstości (≈ 2,2 strefy na próbkę, ≥ 20 próbek) to ok. **45–60 żądań na bieg**:

| Bieg | Żądania | Koszt (cena promocyjna / od 2027) |
|---|---:|---:|
| ewaluacja `hybrid` na żywo (1 bieg) | 45–60 | 0,3–0,4 / 0,6–0,85 USD |
| badanie zmienności (3 biegi) | 135–180 | 0,9–1,3 / 1,9–2,5 USD |
| razem z ewaluacją (4 biegi) | 180–240 | 1,2–1,7 / 2,5–3,4 USD |

Górna granica tokenów (ok. 18 tys. na analizę wg ADR-012, pkt 5 aneksu PV3-15–17): 4 biegi ≈ 3–4 mln tokenów, a **dobowy limit w `llm_usage` to 2 mln tokenów i 5 USD** — cztery biegi tego samego dnia mogą zatrzymać twardy limit;
plan: rozłożyć na dwie doby UTC albo świadomie podnieść limit (decyzja właściciela, nowy wpis w ADR-012). Przegląd ręczny ≥ 100 wartości wymaga, żeby bieg `hybrid` dał ≥ 100 przyjętych `ai_candidate` — liczba nie jest znana przed biegiem.

## #277 — monitoring i przypięcie wersji modelu

**Sprawdzone offline w tej weryfikacji:**

| Polecenie | Wynik | Znaczenie |
|---|---|---|
| `python3 backend/scripts/check_llm_pin.py check` | kod 0; „przypięcie gemini-3.8-flash + mpzp-extraction/1: ROZBIEŻNE; ewaluacja `--live`: oczekuje” | spójność kodu, promptu i schematu z `model_pin.json`; ewaluacja nie zapisana |
| `python3 backend/scripts/check_llm_pin.py check --require-evaluation` | kod **1** (`evaluation_not_recorded`) — zgodnie z projektem | bramka wdrożeniowa jest zamknięta do czasu biegu `--live` |
| `python3 backend/scripts/build_llm_replay_fixtures.py --check` | kod 0; „13 rekordów; zgodne” | złote odpowiedzi odpowiadają kontraktowi (syntetyczne, nie nagrania modelu) |
| `python3 backend/scripts/check_parser_default_switch.py check` | kod 3, `NOT_READY` | patrz #279 |

`scripts/check_llm_drift.py` **nie został uruchomiony**: wysyła publiczne teksty do dostawcy (płatne, wymaga `--confirm-public-text` i klucza); nigdy nie był uruchomiony na prawdziwym dostawcy.
Jeden pełny przebieg: najwyżej 13 żądań (≈ 0,2 USD wg ceny od 2027); polecenie: `cd backend && python3 scripts/check_llm_drift.py --confirm-public-text`.

**Czego brakuje do zamknięcia #277:** (1) bieg `--live` na zbiorze złotym i zapis `check_llm_pin.py record-evaluation --run-manifest …/hybrid/run_manifest.json` (runbook `docs/operations/mpzp-llm.md` §8, krok 2–3) — dopiero wtedy
`check --require-evaluation` daje 0; (2) pierwsza kontrola dryfu u dostawcy; (3) potwierdzenie progów alarmów (30% odrzuceń, 20% degradacji, 4 USD/dobę, dryf 0,20) — to propozycje, niezmierzone. Testy monitoringu, `/health` (`llm` = `degraded`,
nigdy `failed`) i redakcja logów są w kodzie i w pełnym przebiegu testów (patrz niżej). Kryterium #277 „zmiana `model_id`/`prompt_version` bez zapisu ewaluacji jest wykrywana” jest spełnione (test `tests/test_llm_pin.py` i skrypt).

## #279 — wygaszenie legacy i domyślny tryb parsera

**Stan:** domyślny tryb pozostaje **`legacy`**; nie zmieniono go. Bramka `backend/app/core/mpzp_parser_rollout.json` + `scripts/check_parser_default_switch.py` + test `tests/test_mpzp_parser_rollout.py` blokują przełączenie
(CI zawodzi, jeśli domyślny tryb ≠ `legacy` bez oceny `READY`). Aktualny wynik: `NOT_READY` — braki: `gate_not_go`, `pin_not_ready`, `shadow_requirements_unconfirmed`, `shadow_observation_missing`, `owner_decision_missing`.

**Zmierzone różnice `legacy` vs `v3`** (tylko offline, na zbiorach, których anotacje nie mają przeglądu człowieka, i bez ślepej próby — zob. ograniczenia):

| Miara | `legacy` | `v3` | Źródło |
|---|---:|---:|---|
| wartości spoza sekcji strefy (41 stref, 9 uchwał) | **224** | **0** | [au-102-zone-section-causes.md](au-102-zone-section-causes.md) |
| oczekiwane parametry odtworzone (110) | 86 (78%) | 103 (94%) | tamże |
| recall (BK-603, całość) | 0,832 | 0,992 | `results/parser-v3/gate_report.md` |
| `source_consistent` (BK-603) | 0,7684 | 0,9753 (< 0,98) | tamże |

**Wariant do decyzji właściciela (propozycja, niewdrożona): „deterministyczne `v3` jako domyślne, niezależnie od ścieżki modelu”.**
Obecna bramka wiąże **każde** odejście od `legacy` (także na `v3`, zob. komentarz w `scripts/check_parser_default_switch.py`) z przesłankami ścieżki LLM (`gate_not_go`, `pin_not_ready`, okres cienia modelu), więc takie przełączenie jest dziś niemożliwe,
choć `v3` nie wywołuje modelu, nie ma kosztu ani zależności od dostawcy. Wariant wymagałby: (1) osobnej, jawnej ścieżki w `mpzp_parser_rollout.json` (`target_mode: v3`) z przesłankami niezależnymi od modelu: decyzja właściciela z datą,
wynik porównania `legacy`/`v3` na zbiorze z #260 (ślepym), okres obserwacji różnic `legacy` vs `v3` na ruchu (trzeba dopisać tryb „cienia” dla silników deterministycznych — dziś `hybrid_shadow` obejmuje model); (2) zmiany reguły w skrypcie i teście
(zmiana wymaga decyzji, nie jest w tym zadaniu); (3) planu wycofania (`legacy` zostaje na jedno wydanie). **Ryzyka:** wszystkie liczby `v3` pochodzą z korpusu, na którym silnik był tworzony (rozwojowy), a anotacje nie mają przeglądu człowieka;
`source_consistent` 0,9753 jest poniżej progu 0,98 przyjętego dla ścieżki hybrydowej; luki `v3` dotyczą 7 z 110 parametrów (m.in. „wysokość elewacji lub attyki”, zakazy zapisane listą). **Korzyść:** usunięcie 224 wartości spoza sekcji
(klasa błędu „cudza strefa”) i wyższy recall przy zerowym koszcie. Rekomendacja: nie przełączać przed wynikiem na ślepym zbiorze z #260 — bez niego przewaga `v3` jest dowodem na zbiorze rozwojowym.

## #59 — testy `docker_cli` (E2E w Compose)

**Wynik uruchomienia: 3 z 3 przechodzą, w obu trybach.** Testy z `backend/tests/test_docker_integration.py` (znacznik `docker_cli`, wykluczony w CI przez `-m 'not docker_cli'`):
`test_postgis_version_is_34x`, `test_spatial_ref_sys_has_srid_2180`, `test_backend_imports_gis_and_pdf_libraries`. Każdy ma dwie gałęzie: *w kontenerze* (gdy `/app/app` istnieje, a `docker` nie) i *na hoście* (przez `docker compose exec/run`).

```bash
# a) w kontenerze testowym — gałąź „bezpośrednia”, osobny projekt Compose, bez dotykania działającego stosu
docker compose -p <proj> --env-file /dev/null --profile test run --rm backend-test pytest -m docker_cli tests/test_docker_integration.py
docker compose -p <proj> --env-file /dev/null --profile test down -v

# b) na hoście — gałąź „docker compose exec”: potrzebne pytest i sqlalchemy; projekt i plik Compose podawane zmiennymi,
#    żeby testy nie trafiły w stos deweloperski (override z `ports: !reset []`)
COMPOSE_PROJECT_NAME=<proj> COMPOSE_FILE=docker-compose.yml:<override-bez-portow.yml> COMPOSE_ENV_FILES=/dev/null docker compose up -d --wait db
cd backend && COMPOSE_PROJECT_NAME=<proj> COMPOSE_FILE=… COMPOSE_ENV_FILES=/dev/null python3 -m pytest -m docker_cli tests/test_docker_integration.py
COMPOSE_PROJECT_NAME=<proj> COMPOSE_FILE=… COMPOSE_ENV_FILES=/dev/null docker compose down -v
```

Wyniki 2026-10-13: a) `3 passed` (PostGIS 3.4, `spatial_ref_sys` z SRID 2180, importy GIS/PDF); b) `3 passed in 3.13s` (host Python 3.13.3, pytest 8.4.2, osobny projekt z czystą bazą, usunięty `down -v`).

**Ocena względem kryteriów #59 — issue nie jest spełnione:**

| Kryterium | Stan |
|---|---|
| ≥ 10 scenariuszy E2E w Docker Compose (katalog `backend/tests/e2e` albo `tests/e2e`) | **niespełnione** — katalogu nie ma; `docker_cli` to trzy testy dymne infrastruktury, nie ścieżki użytkownika. Scenariusze „od końca do końca” istnieją w procesie (ASGI + PostGIS + atrapy usług zewnętrznych): `test_*_e2e.py` (30 testów: audyt 10, jakość 5, kontekst BDOT 4, kompletność 3, teren 3, ryzyko 2, ręczna strefa 2, raport 1), ale nie pokrywają wszystkich scenariuszy z issue (np. rastrowy MPZP z wznowieniem, konflikt MPZP–POG jako osobne scenariusze Compose) |
| uruchamianie komendą z README | **niespełnione** — README opisuje tylko `-m 'not docker_cli'`; testów `docker_cli` nie wspomina |
| brak zapytań do usług publicznych | spełnione dla `docker_cli` (wyłącznie baza i obraz) |
| sens domenowy wyniku, nie tylko status 200 | częściowo — testy dymne sprawdzają wersję PostGIS i SRID, nie wynik analizy |
| ≥ 80% pokrycia modułu | nie dotyczy testów dymnych; pokrycie aplikacji w pełnym przebiegu: zob. odbiór BDOT |

**Propozycja (bez zmian w CI):** (1) dopisać do README polecenie a) jako „test dymny stosu”; (2) w CI osobny job `docker-cli-smoke` z poleceniem a) (nie wymaga Pythona na runnerze); (3) prawdziwe E2E Compose (`up -d` backend + frontend + baza, scenariusze HTTP na atrapach źródeł) to osobne zadanie — przebieg
„żywego stosu” z tego odbioru (osobny projekt, 30 działek, przeglądarka) jest jego prototypem, ale używa żywych usług publicznych, więc nie spełnia kryterium „bez zapytań do usług publicznych”.
