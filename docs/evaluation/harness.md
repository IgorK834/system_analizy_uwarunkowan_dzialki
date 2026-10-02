# Harness ewaluacyjny korpusu referencyjnego (BK-004)

## Jedno polecenie

Z katalogu głównego repozytorium:

```bash
python3 backend/scripts/evaluate_reference_corpus.py \
  --corpus backend/tests/fixtures/reference_corpus/manifest.json \
  --output-dir docs/evaluation/results/reference-corpus \
  --mode offline \
  --fail-on-regression
```

Polecenie nie łączy się z internetem. Najpierw sprawdza strukturę manifestu,
lokalne ścieżki i SHA-256 wszystkich artefaktów. Kod wyjścia `1` oznacza
niepoprawny korpus, błąd co najmniej jednego przypadku albo przekroczenie
zadeklarowanego progu regresji. Błąd pojedynczego przypadku pozostaje w tabeli
z `status=failed` i pełną przyczyną.

## Przepływ danych

`FrozenArtifactAnalysisRunner` realizuje port `AnalysisCaseRunner`. Otrzymuje
wyłącznie identyfikator wejściowy, lokalną geometrię ULDK i obserwacje z
zamrożonych adapterów źródeł. Nie otrzymuje `expected`. Metryki geometrii
przechodzą przez produkcyjną funkcję `calculate_geometry_metrics`; sekcje
źródłowe są składane z utrwalonych odpowiedzi adapterów. Dopiero osobna warstwa
porównująca otwiera `expected`.

To rozdzielenie jest sprawdzane testem z adapterem szpiegującym. Zmiana
wartości `expected` nie zmienia wyniku adaptera. Zwykłe CI nie odświeża danych
źródłowych i nie korzysta z sieci.

## Artefakty

Katalog `docs/evaluation/results/reference-corpus/` zawiera:

- `evaluation.json` — pełne metadane, metryki i wyniki wszystkich przypadków;
- `cases.csv` — jeden wiersz na przypadek, w tym błąd, status, czas i cache;
- `metrics.csv` — metryki zbiorcze oraz macierze TP/FP/FN/TN;
- `report.md` — tabele gotowe do użycia w rozdziale wyników;
- `metrics.svg` — odtwarzalny wykres trzech głównych wskaźników.

JSON, oba CSV i Markdown zawierają ten sam `schema_version`, `commit_sha`,
`corpus_sha256`, `source_release_ids`, `seed` i znaczniki czasu. Rekordy są
sortowane po `case_id`.

## Definicje mianowników

- **Identyfikacja działki:** liczba zgodnych identyfikatorów / wszystkie
  przypadki, także nieudane.
- **Accuracy klasy strefy:** zgodność zbioru symboli POG i MPZP dla sekcji ze
  statusem referencyjnym `available`. `unknown`, ręczny discovery i wskazana
  metryka `ambiguous` nie są wymuszane jako klasa.
- **MAE i maksimum udziału:** bezwzględna różnica udziałów dopasowanych po
  symbolu, w punktach procentowych. Korpus używa skali 0–100.
- **Warunki binarne:** osobne macierze dla przecięcia ISOK, przecięcia GDOŚ i
  obecności OUZ oraz ich suma. Precision = `TP/(TP+FP)`, recall =
  `TP/(TP+FN)`, F1 = średnia harmoniczna.
- **Kompletność pól:** liczba znanych wartości w stałym zestawie 18 pól /
  `18 * liczba wszystkich przypadków`. `null`, `unknown`, błąd i metryka
  `ambiguous` pozostają w mianowniku. Przypadek nie znika z raportu.
- **unknown / partial / manual review:** udział przypadków z co najmniej jedną
  nieznaną sekcją, statusem częściowym albo wymaganą kontrolą ręczną,
  odpowiednio, względem wszystkich przypadków.
- **Czas:** p50 i p95 czasu pojedynczego adaptera, z interpolacją liniową dla
  rangi `(N-1)*q`; nieudane próby również mają koszt.
- **Cache:** `miss`, jeżeli przypadek jako pierwszy używa danego wydania
  źródła w procesie; późniejsze użycie zamrożonego wydania jest `hit`.

Jeżeli mianownik precision, recall, accuracy albo MAE wynosi zero, wartość jest
`null` z polem `reason`; nie jest zapisywana jako zero.

## Progi regresji

Progi znajdują się w `manifest.json` pod `evaluation_thresholds`. Obecny
kontrakt wymaga braku błędów przypadków, 100% identyfikacji działki, co najmniej
95% accuracy klas stref, co najmniej 60% kompletności oraz MAE udziałów nie
większego niż 0,1 pp. Flaga `--fail-on-regression` zamienia naruszenie na kod
wyjścia `1`.

## Test kontrolny

Test `test_hand_calculated_confusion_mae_and_percentiles` używa czterech
przypadków z ręcznie ustaloną macierzą `TP=1, FP=1, FN=1, TN=1` dla każdego
warunku, błędami udziału `[2, 4, 0, 0]` i czasami `[10, 20, 30, 40]` ms.
Oczekuje precision/recall/F1 `0,5`, MAE `1,5 pp`, maksimum `4 pp`, p50 `25 ms`
i p95 `38,5 ms`.

```bash
cd backend
python3 -m pytest \
  tests/test_evaluate_reference_corpus.py \
  tests/test_reference_corpus.py \
  --cov=scripts.evaluate_reference_corpus \
  --cov=app.modules.analysis.application.ports \
  --cov-report=term-missing \
  --cov-fail-under=80
```

Wynik odbiorowy z 23.09.2026: 37 testów przeszło, a pokrycie zmienionych
modułów wyniosło 95% (port 100%, harness 95%).

## Tryb badania BK-601 (`--study`)

```bash
python3 backend/scripts/evaluate_reference_corpus.py --study --repeat 3
```

Domyślnie zapisuje do `docs/evaluation/results/accuracy/` i generuje
`docs/evaluation/error_analysis.md`. Metryki BK-004 pozostają bez zmian; tryb dodaje:

- `run_manifest.json` — zamrożone wejście: `commit_sha`, odcisk kodu (SHA-256 plików
  harnessu i analizatorów), `corpus_sha256` i skróty wszystkich artefaktów, wydania
  źródeł, wersje parsera, stylu POG i kontraktów, środowisko oraz wszystkie parametry
  (progi, tolerancje, `discrepancy_threshold_pp=0.5`); `manifest_sha256` nie zależy od czasu;
- porównanie na poziomie pól (`field_results.csv`) i statusów sekcji
  (`section_results.csv`) z werdyktami `match`, `within_tolerance`, `mismatch`,
  `missing_actual`, `unexpected_actual` (fałszywa pewność), `both_unknown` i
  `ambiguous_excluded` (wyłącznie wskazana ścieżka); każda metryka ma licznik i mianownik;
- rejestr błędów i ograniczeń (`error_ledger.json|csv`) z kategorią przyczyny
  (`source`, `data`, `geometry`, `parser`, `presentation`), przyczyną źródłową, regułą
  atrybucji i dowodami (wskaźnik JSON w zamrożonej obserwacji z SHA-256, wskaźnik w
  ground truth, odwołanie do kodu, próba uruchomienia kodu produkcyjnego);
- tabelę „co waliduje każda metryka” (`measurement_kinds`): udziały POG/MPZP/OUZ i NMT są
  odczytem zamrożonych obserwacji, a nie niezależnym przecięciem;
- kontrolę determinizmu: `--repeat N` porównuje `substantive_sha256`; czas trafia osobno
  do `timing.json` i nie wchodzi do skrótu. Kod wyjścia `1` także przy mniej niż 24
  przypadkach, przy `wejścia ≠ ukończone + częściowe + błędne` i przy rozbieżności skrótów.

