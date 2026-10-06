# Parser MPZP v3 — ewaluacja A/B i bramka jakości (PV3-17)

Stan na 2026-10-05: **decyzja NIEROZSTRZYGALNA (`NOT_DECIDABLE`)** — nie dlatego, że kryterium zawiodło,
tylko dlatego, że brakuje wejść, których nie da się wytworzyć bez ludzi i bez decyzji właściciela.
Raport: [`gate_report.md`](gate_report.md) (dane: `gate_report.json`).

## Co jest w katalogu

| Ścieżka | Zawartość |
|---|---|
| `legacy/`, `v3/` | bieg ewaluatora offline na korpusie **BK-603** (21 próbek, 9 gmin, anotacje AI, część rozwojowa i „final” BK-603), 2 powtórzenia — `determinism.json` potwierdza identyczne metryki merytoryczne |
| `comparison/` | porównanie sparowane `v3` vs `legacy` (bootstrap, te same pary) |
| `gate_report.*` | ocena wobec kryteriów zamrożonych w ADR-012 (2026-10-05) z licznikami i listą braków |

`hybrid/` **nie ma**: złote odpowiedzi w `backend/tests/fixtures/mpzp_evaluation/llm_replay` pokrywają tylko
13 bloków (są złożone z adnotacji, nie nagrane z modelu), a ewaluator celowo przerywa bieg przy braku
odpowiedzi zamiast mieszać wynik deterministyczny.

## Dlaczego nie ma decyzji

1. **Zbiór końcowy z Task 20.2 nie istnieje.** BK-603 nie spełnia profilu `final-v2` (89 problemów, m.in.
   14 próbek „final” < 20, brak drugiego anotatora); rdzeń v3 był rozwijany na tych samych próbkach, więc
   jego liczby są rozwojowe. Kryteria bramki wolno oceniać tylko na zbiorze niezależnym.
2. **Brak biegu hybrydy na żywo** (koszt i wysyłka publicznego tekstu do dostawcy — wymaga zgody właściciela
   na ten bieg) i **brak badania zmienności** (3 biegi do osobnych katalogów odpowiedzi).
3. **Brak przeglądu ręcznego ≥ 100 wartości `ai_candidate`** — musi go wykonać człowiek; asystent AI nie
   może być recenzentem.

Obserwacja informacyjna (nie decyzja): na BK-603 rdzeń `v3` ma precision 1,000 (248/248), recall 0,992
(248/250), ale `source_consistent` **0,975** (355/364) — poniżej progu 0,98, który obowiązuje hybrydę.
Ścieżka hybrydowa dodaje wartości tylko dla par bez wartości deterministycznej, więc tego wskaźnika sama nie
poprawi; to sygnał do analizy błędów źródła rdzenia przed biegiem końcowym.

## Procedura dokończenia (gdy wejścia będą dostępne)

```bash
cd backend
CORPUS=tests/fixtures/mpzp_evaluation_v2/manifest.json        # zbiór końcowy z Task 20.2 (profil final-v2)
python3 scripts/evaluate_mpzp_parser.py --corpus $CORPUS --validate-only --profile final-v2
# bieg na żywo (ręcznie, poza CI, klucz tylko w środowisku) — zapisuje odpowiedzi z hashami
GEMINI_API_KEY=... python3 scripts/evaluate_mpzp_parser.py --corpus $CORPUS --engine legacy v3 hybrid \
  --live --llm-replay tests/fixtures/mpzp_evaluation_v2/llm_replay --output-dir ../docs/evaluation/results/parser-v3
# powtórka offline z zapisanych odpowiedzi — identyczne metryki merytoryczne (koszt/czas osobno)
python3 scripts/evaluate_mpzp_parser.py --corpus $CORPUS --engine legacy v3 hybrid \
  --llm-replay tests/fixtures/mpzp_evaluation_v2/llm_replay --output-dir /tmp/parser-v3-replay
# zmienność: dwa kolejne biegi na żywo do OSOBNYCH katalogów odpowiedzi, potem porównanie trzech
python3 scripts/mpzp_quality_gate.py variance --runs ../docs/evaluation/results/parser-v3/hybrid RUN2/hybrid RUN3/hybrid \
  --output ../docs/evaluation/results/parser-v3/variance.json
# przegląd ręczny: arkusz → człowiek wpisuje verdict/reviewer/reviewed_at → ocena
python3 scripts/mpzp_quality_gate.py review-sheet --results ../docs/evaluation/results/parser-v3/hybrid \
  --output ../docs/evaluation/results/parser-v3/review_sheet.csv --n 120
python3 scripts/mpzp_quality_gate.py review-score --sheet ../docs/evaluation/results/parser-v3/review_sheet.csv \
  --output ../docs/evaluation/results/parser-v3/review.json
# decyzja (kod wyjścia: 0 GO, 2 NO_GO, 3 NOT_DECIDABLE)
python3 scripts/mpzp_quality_gate.py gate --corpus $CORPUS --results ../docs/evaluation/results/parser-v3 \
  --review ../docs/evaluation/results/parser-v3/review.json --variance ../docs/evaluation/results/parser-v3/variance.json \
  --output-dir ../docs/evaluation/results/parser-v3
```

Decyzję go/no-go zapisuje właściciel w ADR-012 z uzasadnieniem; raport bramki jest rekomendacją opartą na
pomiarze (liczba gmin, formaty, zmienność modelu, anotacje), nie gwarancją jakości.
