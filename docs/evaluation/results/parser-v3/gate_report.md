# Bramka jakości parsera MPZP v3 (PV3-17)

Decyzja: **NOT_DECIDABLE** · kryteria zamrożone 2026-10-05 (ADR-012) · mpzp-quality-gate/1 · 2026-10-05T09:36:51.971643Z

> Wynik jest rekomendacją opartą na pomiarze z jawnymi ograniczeniami (liczba gmin, formaty, zmienność modelu, anotacje), a nie gwarancją jakości.

## Braki uniemożliwiające decyzję

- zbiór nie jest niezależnym zbiorem końcowym (profil final-v2): 89 problemów, np. 14 final samples < 20
- brak wyników silnika hybrid (bieg --live albo odtworzenie zapisanych odpowiedzi)
- brak przeglądu ręcznego (arkusz review-sheet wypełniony przez człowieka)
- brak badania zmienności (3 biegi na żywo)

## Kryteria

| Kryterium | Próg | Wynik | Liczniki | Spełnione |
|---|---|---|---|---|
| precision | >= 0.95 | — | — | nie zmierzono |
| recall | >= 0.8 | — | — | nie zmierzono |
| source_consistent (Task 20.3) | >= 0.98 | — | — | nie zmierzono |
| przyjęte wartości bez zweryfikowanego cytatu | == 0 | — | — | nie zmierzono |
| błędy przypisania do strefy / znalezione | <= 0.02 | — | — | nie zmierzono |
| ECE (kalibracja pewności) | <= 0.1 | — | — | nie zmierzono |
| koszt na analizę, cena od 2027 [USD] | <= 0.05 | — | — | nie zmierzono |
| dodatkowe opóźnienie p95 względem v3 [s] | <= 30.0 | — | — | nie zmierzono |
| ocenione ręcznie wartości ai_candidate | >= 100 | — | — | nie zmierzono |

## Silniki (całość)

| Silnik | Precision | Recall | source_consistent | ECE |
|---|---|---|---|---|
| legacy | 1 [0.982; 1.000] | 0.832 [0.781; 0.873] | 0.7684 | 0.04902 |
| v3 | 1 [0.985; 1.000] | 0.992 [0.971; 0.998] | 0.9753 | 0.02468 |
| hybrid | — | — | — | — |
