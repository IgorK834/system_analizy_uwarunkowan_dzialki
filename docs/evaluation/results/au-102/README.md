# Wyniki AU-102 — pomiar wykrywania sekcji stref na zbiorze `mpzp_live`

- `before-head.json` — kod z `HEAD` (przed AU-101–103), te same adnotacje i ten sam ewaluator (`backend/scripts/evaluate_mpzp_live.py`);
  uzyskany przez `git archive HEAD backend/app` zamontowane w kontenerze `backend-test`, z adnotacjami i zamrożonym tekstem z drzewa roboczego.
- `after.json` — kod po zmianach AU-102 (parser `mpzp-parser/3.2-det`, blokowy `3.2-det+scope.2`).

Struktura: `{legacy|v3: {overall, strict, zones_with_parameter, by_split, by_layout, by_municipality, expected_recall, parameters_outside_section, verdicts, zones[]}}`.
`overall` = metryka zadania (≥ 1 parametr albo jawny, poprawny powód braku); `strict` = parametr musi leżeć w adnotowanej sekcji strefy.
Analiza: [`../../au-102-zone-section-causes.md`](../../au-102-zone-section-causes.md).

Odtworzenie (z `backend/`): `python -m scripts.evaluate_mpzp_live --out wynik.json` — wynik jest deterministyczny (bez sieci i modelu).
