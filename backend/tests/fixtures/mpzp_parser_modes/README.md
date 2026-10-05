# Zamrożony wynik trybów deterministycznych parsera MPZP (PV3-14)

`frozen_parse_results.json` to wynik `parse_mpzp_document` na 10 dokumentach regresji parsera
(`tests/fixtures/mpzp/*`) w trybie zakresu `legacy` i `blocks`: kontrakt parsera (`MpzpParseResult`) oraz
snapshot strefy API (`map_parser_zone_to_analyze_response`, stałe źródło). Plik zamrożono 2026-10-05 **na kodzie
sprzed wprowadzenia `MPZP_PARSER_MODE`**; `tests/test_mpzp_parser_modes.py` wymaga, żeby tryb `legacy`
(domyślny) dawał wynik identyczny z kluczami `:legacy`, a `v3` i `hybrid_shadow` — z kluczami `:blocks`.

Ponowne zamrożenie wolno wykonać tylko przy świadomej zmianie wyniku parsera (z podniesieniem
`MPZP_RESULT_SCHEMA_VERSION`):

```bash
cd backend
python3 scripts/freeze_mpzp_parser_modes.py --check   # porównanie (kod 1 przy różnicy)
python3 scripts/freeze_mpzp_parser_modes.py           # zapis
```
