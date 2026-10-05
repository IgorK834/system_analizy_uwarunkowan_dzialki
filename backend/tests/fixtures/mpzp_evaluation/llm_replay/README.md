# Złote odpowiedzi do testów odtwarzania ekstrakcji modelem językowym (PV3-11)

Pliki `<klucz>.json` mają format `ReplayStore` z `scripts/mpzp_eval_engines.py`. Klucz to SHA-256 z
(dostawca, model, wersja i skrót instrukcji, wersja schematu, temperatura, skrót wiadomości z danymi).

**To nie są nagrania odpowiedzi modelu.** Składa je `scripts/build_llm_replay_fixtures.py` z adnotacji
korpusu BK-603 (pole `origin: "golden_fixture"`): tak odpowiedziałby model idealny. Pokrywają układy
zakresu 1–6 (13 bloków, 7 przypadków) oraz skan bez wartości katalogu (jawne `not_found`).

Zmiana `prompts/mpzp_extraction_v1.md`, szablonu wiadomości, schematu, modelu albo wyznaczania bloków
zmienia klucze, więc test `test_the_stored_responses_are_up_to_date…` zawodzi, dopóki plików się nie
odtworzy:

```bash
cd backend
python3 scripts/build_llm_replay_fixtures.py          # zapis
python3 scripts/build_llm_replay_fixtures.py --check  # tylko sprawdzenie (kod 1, gdy nieaktualne)
```

Prawdziwe nagrania powstają ręcznie, poza CI (`--live` ewaluatora), i dostają ten sam klucz; nie
nadpisują się złotych plików (`ReplayStore.put` odmawia nadpisania).
