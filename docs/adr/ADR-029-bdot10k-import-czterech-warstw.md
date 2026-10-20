# ADR-029: pełna podstawa importu BDOT10k i kolejka powiatów (AU-201)

Data: 2026-10-17. Status: przyjęty w implementacji.
Uzupełnia ADR-025 (drogi/budynki), ADR-026 (kolejka) i ADR-028 (pokrycie terenu).

## Kontekst i decyzja

Task 23.1 miał już import osi jezdni, budynków i granicy powiatu, ale nie linii
energetycznych ani powiatowych zleceń w kolejce. Import pokrycia terenu z AU-204
jest zachowany. Podstawą czterech grup są pełne paczki powiatowe schematu 2021:
`OT_SKJZ_L`, `OT_BUBD_A`, 12 klas `OT_PT*_A`, `OT_SULN_L`; `OT_ADJA_A`
wyznacza zasięg. Nie pobieramy geometrii z WMS. Porty pozostają w application,
czytnik GDAL i zapis PostGIS w infrastructure (ADR-001).

`OT_SULN_L` potwierdzono na trzech rzeczywistych paczkach. Atrybut `rodzaj`
podaje średnie/wysokie/najwyższe napięcie; nie podaje napięcia w kV. Import
nie wyprowadza pasów prawnych ani parametrów technicznych. Nieznany rodzaj,
niepoprawny identyfikator lub nieużyteczna geometria zatrzymują publikację.
To warstwa źródłowa dla późniejszej analizy sieci, a nie taka analiza.

Geometrie naprawia Shapely, zapis to `MULTILINESTRING(2180)` z GiST i FK do
`data_releases` (`040_bdot_powerlines`, po `039_land_cover`). Każdy powiat ma
własne aktywne wydanie. `bdot10k-import/3` / etykieta `-v3` pozwala ponownie
zaimportować tę samą paczkę, gdy stare wydanie pomijało klasy. Nie modyfikujemy
starych snapshotów. Ten sam hash i wersja importera dają te same liczności.

Historyczny wycinek testowy bez SULN nie deklaruje kompletności: liczność
klasy w `/api/v1/coverage` to `null`; obecna, potwierdzona pusta klasa to 0.
UI rozróżnia te stany. Odczyt pokrycia z awarią bazy daje 503, nie pustą listę.

Zlecenie `bdot_county` używa istniejącego `import_jobs` (deduplikacja zakresu
`powiat:XXXX`, dzierżawa, backoff i limit trzech prób). Endpoint i formularz
pokrycia przyjmują kod powiatu; worker wywołuje dokładnie kompozycję CLI.
Timeout można ponowić; wadę kontraktu trzeba zweryfikować (zlecenie `failed`).
`pog-sync --no-schedule` przetwarza tylko kolejkę, bez kosztownego odświeżenia
krajowego POG. Rollback 040 zachowuje historię: obecność zleceń `bdot_county`
zatrzyma przywrócenie starego CHECK, zamiast skasować je po cichu.

## Pomiar pełnych paczek (17.10.2026)

Osobna baza PostGIS 16/3.4, Python 3.13, Docker Desktop na arm64 (PostGIS amd64).
Czasy importu obejmują czytnik, walidację, zapis i indeksy, bez pobierania.

| Powiat | ZIP B | Drogi | Budynki | Pokrycie terenu | Linie | Import s | Powtórzenie s |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1261 Kraków | 35 198 806 | 33 585 | 90 563 | 22 731 | 607 | 23,70 | 9,51 |
| 0262 Legnica | 5 383 751 | 5 230 | 12 302 | 2 256 | 154 | 2,39 | 1,22 |
| 2461 Bielsko-Biała | 19 169 873 | 13 323 | 37 591 | 5 063 | 291 | 6,97 | 3,53 |

Pobranie: odpowiednio 4,37 / 0,74 / 2,40 s. Trzy powiaty łącznie:
`pg_total_relation_size` (tabela i indeksy): drogi 25 837 568 B, budynki
81 223 680 B, pokrycie terenu 23 437 312 B, linie 557 056 B.
Wszystkie importy `succeeded`; powtórzenie zwróciło to samo `data_release_id`
i `created=0`. Liczności i hashe: `docs/evaluation/results/au112-113/bdot-full-imports.json`.
To pomiar tej maszyny, nie gwarancja czasu dla całego kraju.

## Weryfikacja i konsekwencje

Zamrożone pełne klasy SULN z trzech powiatów są w
`backend/tests/fixtures/source_contracts/bdot10k_base/`. Test porównuje ich
liczność i długości policzone bez GDAL z `gml:posList` ze stanem PostGIS.
Test kolejki przechodzi endpoint → zlecenie → worker → rzeczywisty import →
widoczne pokrycie; wstrzyknięcie timeoutu i zmiany schematu zachowuje jawne błędy.
Mini-paczki testowe nie służą do publikacji pokrycia produkcyjnego.

Nie zmieniamy formatu `AnalyzeResponse` dla samego importu SULN. Drogi,
budynki i pokrycie terenu mają własne sekcje API/UI/PDF/audytu i wersje kontraktu.
Redystrybucja pozostaje `derived_only`, a atrybucja GUGiK jest w katalogu.
Paczki odświeża operator albo jawne zlecenie; GUGiK nie deklaruje stałego cyklu.
