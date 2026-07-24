"""Moduły domenowe modularnego monolitu.

Każdy moduł ma spójny podział na warstwy: ``domain`` (czysta logika i typy),
``application`` (porty/protokoły i przypadki użycia), ``infrastructure``
(adaptery do baz, usług i bibliotek zewnętrznych) oraz ``api`` (warstwa
transportowa). Reguły zależności między warstwami i modułami opisuje
docs/adr/ADR-001-modular-monolith.md i wymusza test architektoniczny
(app/core/architecture.py, tests/test_architecture.py).

Szkielet jest addytywny: nie przenosi istniejącej logiki z app/services ani nie
zmienia istniejących endpointów. Migracja logiki do modułów jest osobnym,
stopniowym krokiem opisanym w ADR.
"""
