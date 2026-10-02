# Odbiór PV3-01, PV3-02 i PV3-03 (Epic 20A)

Data: 1 października 2026 r. Baza kodu: `cbefc0a` z niezacommitowanymi zmianami (zmiany nie są
zacommitowane; `docs/evaluation` i `docs/adr` są w `.gitignore`, więc nowe pliki tych katalogów
wymagają `git add -f`).

## Stan zadań

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| PV3-03 — ewaluator wielosilnikowy, `source_consistent`, A/B, koszt | **wykonane** | `v3` i `hybrid` są zarejestrowane, ale nie istnieją (Taski 20.4–20.14); porównanie sparowane sprawdzono na silniku testowym i `legacy`. Produkcyjnego dostawcy modelu (Task 20.10) nie ma, więc `--live` kończy się odmową, a odtwarzanie sprawdzono na nagraniach z dostawcy testowego; w repozytorium nie ma jeszcze katalogu `llm_replay/` z prawdziwymi nagraniami |
| PV3-02 — nowy zbiór końcowy i drugi anotator | **wykonane częściowo: narzędzia i protokół, brak korpusu** | Korpusu nie zbudowano. Wymaga (1) zatwierdzenia listy źródeł przez właściciela przed jakimkolwiek pobraniem i (2) anotacji oraz drugiej anotacji wykonanych przez **ludzi**; asystent AI nie może być anotatorem tego zbioru |
| PV3-01 — spike modelu, ADR-012 | **wykonane (pomiar 2026-10-02); decyzja go/no-go czeka na właściciela** | Pomiar na żywo wykonano po zgodzie właściciela (10 publicznych bloków, 30 wywołań). Wynik mechaniczny progów: GO; ADR-012 ma status „Proponowany” i **nie zawiera wpisanej decyzji właściciela**. Brak limitów RPM/TPM projektu (tylko w AI Studio), regionu i SLA |

## Polecenia odbiorowe

```bash
# PV3-03: ewaluacja silnika legacy (wyniki w docs/evaluation/results/parser/legacy/)
python3 backend/scripts/evaluate_mpzp_parser.py --mode offline --output-dir docs/evaluation/results/parser --repeat 2

# PV3-03: nieobecne silniki i tryb live odmawiają z kodem 2 (bez zapisu wyników)
python3 backend/scripts/evaluate_mpzp_parser.py --engine v3
python3 backend/scripts/evaluate_mpzp_parser.py --engine hybrid --live

# PV3-02: walidacja korpusu bieżącego i wobec profilu zbioru końcowego (bieżący go nie spełnia)
python3 backend/scripts/evaluate_mpzp_parser.py --validate-only
python3 backend/scripts/evaluate_mpzp_parser.py --validate-only --profile final-v2

# PV3-01: część offline (10 bloków + SHA-256 wejść); reszta wymaga klucza, patrz ADR-012
python3 backend/scripts/llm_spike.py prepare

# Testy zmienionych modułów
cd backend
python3 -m pytest tests/test_evaluate_mpzp_parser.py tests/test_mpzp_eval_engines.py \
  tests/test_mpzp_eval_compare.py tests/test_mpzp_corpus_tools.py tests/test_llm_spike_isolation.py -q \
  --cov=scripts.evaluate_mpzp_parser --cov=scripts.mpzp_eval_engines --cov=scripts.mpzp_eval_compare \
  --cov=scripts.mpzp_annotation_agreement --cov=scripts.mpzp_corpus_tools --cov-report=term-missing
```

Pełny zestaw w kontenerze skonfigurowanym jak CI (wynik w sekcji „Weryfikacja”).

## PV3-03 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| `source_consistent` z licznikiem i mianownikiem; zestaw kontrolny z wartością zgodną przypadkiem daje oczekiwane metryki | spełnione | `control.json` → `source_control` (ręcznie: `source_consistent` 3/5, przypisanie wg wartości 5/6, ze źródłem 3/6, 2 błędy `wrong_source`), `test_source_control_matches_hand_calculation`; na korpusie: 51/75 |
| Ten sam manifest daje identyczne metryki merytoryczne; czas i koszt osobno | spełnione | `determinism.json` (`excluded: ["observations"]`), `observations.json`, test `test_substance_is_identical_across_runs_while_observations_differ` |
| Raport porównuje silniki na tych samych parach, rozdziela formaty, gminy i bramki odrzuceń | spełnione w mechanizmie | `comparison/` (McNemar dokładny, bootstrap po próbkach, per format/gmina/podział, tabela bramek); sprawdzone na `legacy` i silniku testowym, bo `v3`/`hybrid` nie istnieją |
| Odtwarzanie działa offline z fixtures; `--live` niedostępny bez flagi i poza CI | spełnione częściowo | testy z zablokowanymi gniazdami; klucz cache, brak nagrania = błąd, nagrania z sumą kontrolną; live: odmowa bez flagi, bez `GEMINI_API_KEY`, w CI i bez zarejestrowanego dostawcy. Brak prawdziwych nagrań i dostawcy produkcyjnego (Task 20.10/20.17) |
| ≥ 80% pokrycia zmienionego modułu; progi CI nie obniżone; zmiany kontraktu opisane | spełnione | pokrycie modułów: patrz „Weryfikacja”; `--cov-fail-under=80` bez zmian; kontrakt w `backend/tests/fixtures/mpzp_evaluation/README.md` |

### Wynik metryki źródła na korpusie BK-603 (silnik `legacy`, `mpzp-parser/2.0`)

| Wielkość | Wynik |
|---|---|
| `source_consistent` (poprawne wartości ze spójnym źródłem) | 0,680 (51/75), 95% CI [0,57; 0,77] |
| statusy źródła poprawnych wartości | `consistent` 51, `wrong_page` 9, `wrong_block` 8, `indeterminate` 7, `unlocatable` 0 |
| wśród 72 wartości równych wymaganej (wiersze `tp_exact`/`tp_partial`) | `consistent` 48, **`wrong_page` 9**, `wrong_block` 8, `indeterminate` 7 |
| przypisanie do strefy wg samej wartości / ze źródłem | 37/37 → 26/37 |
| dokładność end-to-end wg wartości / ze źródłem | 50/250 → 39/250 |
| pary z poprawną wartością z niewłaściwego źródła | 13 |

**Odtworzenie liczby „9 z 72” z issue:** wśród 72 wartości zgodnych z wymaganą dokładnie 9 pochodzi z
innej strony niż cytat adnotacji (`wrong_page`), co zgadza się z analizą ad hoc. Metryka bloku wykrywa
dodatkowo 8 wartości z bloku innej strefy na właściwej stronie (np. Kraków MN.2 pobiera 11 m i 9,5 m z
końca sekcji MN.1, zbieżnych z własnymi wartościami MN.2) oraz 7 niejednoznacznych (identyczny tekst w
blokach dwóch stref, np. Falenica A1MN/A2MN), czego analiza ad hoc nie mierzyła. Przykład z issue:
Kraków MN.11, 11 m ze strony 18 zamiast 19 jest `wrong_page` (test `test_krakow_mn11_height_from_page_18_is_a_wrong_source`).

Wszystkie dotychczasowe metryki `legacy` (423 wiersze, werdykty, precision 62/62, recall 62/250,
kalibracja) są **identyczne** z wynikami BK-603 sprzed zmiany; sprawdzono wiersz po wierszu na zachowanej
kopii (`results/parser/bk-603-flat-2026-09-30/`).

Ograniczenia metryki: blok strefy jest wyprowadzony z anotacji (kotwica do ostatniego cytatu + 300 znaków),
a nie z resolvera v3, który jeszcze nie istnieje; `legacy` zwraca tylko fragment kontekstu, więc przy
identycznym tekście w kilku strefach status jest `indeterminate`, nie zgadywany; dla skanu symulowanego
porównuje się tylko stronę.

## PV3-02 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Co jest, czego brakuje |
|---|---|---|
| ≥ 20 nowych próbek z ≥ 10 nowych gmin; formaty i układy w raporcie pokrycia | **niespełnione** | Jest profil `final-v2` (progi 20/10/4 województwa/5 tabel/4 skany/4 HTML/strategie 1–6), raport pokrycia (`mpzp_corpus_tools.py coverage`) i plan źródeł. Nie ma próbek |
| Każda anotacja ma cytat sprawdzony programowo; walidator przechodzi | mechanizm gotowy | walidator BK-603 + profil; na nowym korpusie nie ma czego uruchomić |
| Drugi anotator ≥ 20% próbek; zgodność i rozstrzygnięcia udokumentowane | mechanizm gotowy, danych brak | `mpzp_annotation_agreement.py` (obecność parametru z kappą, dokładna wartość, wszystkie wartości, typy rozbieżności), dziennik rozstrzygnięć, walidator przelicza raport z dwóch niezależnych plików i sprawdza, że rozstrzygnięcia `a`/`b` są w zamrożonych anotacjach |
| Zamrożenie skrótem; zmiana anotacji po zamrożeniu = błąd; żaden silnik przed zamrożeniem | mechanizm gotowy | `freeze` odmawia przy niespełnionym profilu; ewaluator odmawia pracy na korpusie niezamrożonym (test); deklaracja `engines_run_before_freeze: []`. Żaden silnik **nie** uruchomiono na nowym zbiorze, bo go nie ma |
| Log pobrań i podstawa prawna; podział rozwojowy/końcowy | narzędzie i opis gotowe | `download-log`, `legal_basis`, `tls_verification`, `voivodeship` w `source.json`; protokół pkt 6 i 9. Brak pobrań, więc brak wpisów |

Test łańcucha na korpusie syntetycznym (`test_mpzp_corpus_tools.py`): szkielet v2 z obecnego korpusu
(14 próbek końcowych → rozwojowy 2) + 24 próbki końcowe z 12 nowych gmin → niezależna podwójna anotacja →
raport zgodności → dziennik rozstrzygnięć → zamrożenie → walidacja profilu → ewaluacja silnikiem `legacy`;
27 mutacji łamiących reguły profilu jest wykrywanych. To dowód działania narzędzi, **nie** zbiór końcowy.

**Co musi zrobić człowiek (kolejność):** zatwierdzić `docs/evaluation/mpzp_corpus_v2_source_plan.md`
(kwoty, limity pobrań, adresy) → pobranie przesiewowe → wybór próbek → anotacja pierwsza → anotacja druga
(≥ 20%, niezależnie) → rozstrzygnięcia → `freeze` → dopiero wtedy silniki.

## PV3-01 — mapowanie kryteriów akceptacji (stan po pomiarze 2026-10-02)

| Kryterium | Wynik | Dowód |
|---|---|---|
| ADR-012: decyzja go/no-go, potwierdzony identyfikator, źródła i daty cen i limitów | **częściowo** | Identyfikator potwierdzony przez `models.list` (`gemini-3.8-flash`, wejście 1 048 576 / wyjście 65 536, `generateContent`), ceny i warunki danych ze źródłem i datą 2026-10-01. Wynik mechaniczny: GO. **Decyzji właściciela nie wpisano**; brak limitów RPM/TPM projektu (AI Studio), regionu i SLA |
| Tabela 10 bloków z SHA-256 wejść; koszt na analizę i na 1000 analiz; opóźnienie p50/p95 | **spełnione** | tabela w ADR-012 i `results/llm-spike/measurements.md`; koszt na analizę 0,020 USD (promocja do 2026-12-31) / 0,041 USD (od 2027), na 1000 analiz 20,39 / 40,78 USD; opóźnienie p50 5,42 s, p95 8,30 s, max 8,38 s |
| Zgodność ze schematem i powtarzalność zmierzone | **spełnione** | schemat 30/30; cytaty w tekście 261/261; identyczny JSON tylko w 1 z 10 bloków (blok ujemny), ale **stabilny zbiór zweryfikowanych wartości w 9 z 10** (niestabilny: B06 Łódź, układ 4 — wartości per symbol w akapicie) |
| Region, użycie danych, limity, wariant awaryjny | **częściowo** | użycie danych (warstwa płatna nie służy do ulepszania produktów), brak deklaracji rezydencji danych, wariant awaryjny opisany; limity projektu — do odczytu w AI Studio |
| Klucz nie występuje w repozytorium, logach, artefaktach; skrypt wyłącznie ręczny | spełnione | skan `docs/`, `backend/scripts`, `backend/tests`, `README.md`, `shared/` na obecność wartości klucza: 0 trafień; klucz czytany ze zmiennej środowiskowej; do wywołań wysłano wyłącznie publiczny tekst bloków |

Zastrzeżenia do pomiaru: jeden bieg, jedno miejsce i chwila; 10 bloków (z ocenianej już próbki
rozwojowej i końcowej); prompt `spike-v0` nie jest promptem produkcyjnym (Task 20.11); „3 bloki na analizę”
to założenie, nie obserwacja; zgodność z adnotacją jest informacyjna. API nie zwróciło `thoughtsTokenCount` (wartość `null`, raport pokazuje „myślenie łącznie: 0”), ale w próbie
`totalTokenCount` = wejście + kandydaci (1315 + 1778 = 3093), więc koszt z `usageMetadata` nie pomija ukrytych
tokenów. Proponowane progi budżetowe dla Task 20.15: 4000 tokenów wejścia na żądanie, 6 żądań i
12 000 tokenów wejścia na analizę.

Nowa wiedza z dokumentacji, istotna dla Task 20.10: (1) myślenia nie da się wyłączyć (`minimal` zwraca
błąd), więc koszt zdominują tokeny wyjściowe — pomiar to potwierdza (wyjście ~1,4× większe od wejścia);
(2) cena od 2027 jest dwukrotnie wyższa; (3) dokumentacja promuje Interactions API (domyślnie `store=true`,
czyli przechowywanie po stronie dostawcy), a `generateContent` nazywa „w pełni wspieranym” — spike użył
`generateContent`; (4) warstwa bezpłatna jest wykluczona ze względu na użycie danych.

**Co zostaje:** właściciel wpisuje decyzję go/no-go w ADR-012 (i potwierdza lub zmienia progi); odczytać
limity projektu w AI Studio; Task 20.10 dopiero po decyzji.

## Weryfikacja

Pełny zestaw w kontenerze jak w CI (obraz backendu, repozytorium jako `/repo:ro` bez `.env`,
`pytest -m 'not docker_cli' --cov=app --cov-fail-under=80`): **2137 testów przeszło, 3 odrzucone znacznikiem `docker_cli`, 0 niepowodzeń, pokrycie `app` 92,80%** (próg 80% bez zmian; 1 października 2026 r., 3 min 51 s).
Lokalnie (macOS) 5 plików z poleceń wyżej: 137 testów przeszło; w nich pokrycie modułów:
`evaluate_mpzp_parser` 97%, `mpzp_eval_engines` 100%, `mpzp_eval_compare` 99%,
`mpzp_annotation_agreement` 98%, `mpzp_corpus_tools` 99%. `llm_spike.py` nie ma testów tekstu ani nie jest
zbierany przez pytest; sprawdzono go lokalnym stubem HTTP (poza repozytorium) i testami izolacji.
Frontend nie był zmieniany.
