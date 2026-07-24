# Fixtures testu architektonicznego

Kontrolowane, celowo błędne i poprawne drzewa modułów używane przez
`tests/test_architecture.py`. Pliki są wyłącznie parsowane (AST) przez
`app/core/architecture.py` — nigdy nie są importowane ani wykonywane, dlatego
mogą zawierać zabronione importy bez wpływu na aplikację.

- `bad/` — narusza reguły zależności (zabronione frameworki w domain, sięganie
  do infrastruktury z api/application, import prywatnych elementów innego modułu).
- `good/` — poprawny podział warstw (domain importuje tylko `app.shared` i własne
  typy; application definiuje port/protokół).
