_Pomiar z 2026-10-02T12:44:25.760538Z; model zażądany `gemini-3.8-flash`, zwrócony `gemini-3.8-flash`; powierzchnia API `generateContent`; temperatura 0.0, thinkingLevel `low`, 3 wywołania na blok. To pomiar jednego biegu, nie gwarancja._

Korpus `BK-603-mpzp-parser-evaluation-2026-09-30` (`corpus_sha256` `ca5a005efeff440f…`), prompt `spike-v0` (`797d16b38a0a7b6b…`), schemat `eb9ef8ffbd2266b5…`. Ceny: https://ai.google.dev/gemini-api/docs/pricing, pobrane 2026-10-01.

| Blok | Układ | Gmina | Znaki | SHA-256 wejścia | tokeny we/wy (śr.) | opóźnienie p50 [s] | schemat | cytaty | identyczny JSON | stabilne wartości |
|---|---|---|---:|---|---|---:|---|---|---|---|
| B01 | 1 osobny § na strefę | Szczytno | 2580 | `56caf1b7841b3155…` | 1315 / 1788 | 5.77 | 3/3 | 33/33 | nie | tak |
| B02 | 1 osobny § na strefę | Bielsko-Biała | 1351 | `11d1dd126eabb81c…` | 969 / 1464 | 5.28 | 3/3 | 27/27 | nie | tak |
| B03 | 2 wspólny § dla listy symboli | Szczytno | 1974 | `9b1c681cbb190034…` | 1119 / 2516 | 7.94 | 3/3 | 48/48 | nie | tak |
| B04 | 2 wspólny § dla listy symboli | Białystok | 1333 | `e01ee9c2f1d1a215…` | 954 / 2137 | 5.78 | 3/3 | 30/30 | nie | tak |
| B05 | 3 podpunkty „N) dla terenu X:” | Kraków | 559 | `bc75e88039f388ac…` | 703 / 946 | 4.51 | 3/3 | 18/18 | nie | tak |
| B06 | 4 wartości per symbol w akapicie | Łódź | 3169 | `bae3de54c510e52f…` | 1616 / 2088 | 6.71 | 3/3 | 30/30 | nie | nie |
| B07 | 5 klauzula ogólna per symbol | Raszków | 2470 | `4dbbc85b136ca366…` | 1282 / 2311 | 6.66 | 3/3 | 39/39 | nie | tak |
| B08 | 6 tabela | Legnica | 652 | `312288a6d128f81b…` | 697 / 1695 | 5.07 | 3/3 | 30/30 | nie | tak |
| B09 | skan OCR (rzeczywisty) | Stare Miasto | 414 | `1bd50bf98bcbbbf9…` | 624 / 595 | 2.54 | 3/3 | 6/6 | nie | tak |
| B10 | skan OCR (rzeczywisty), próbka ujemna | Pisz | 4891 | `06a2aa49bfd7a00b…` | 2212 / 286 | 1.75 | 3/3 | 0/0 | tak | tak |

| Wielkość | Wynik |
|---|---|
| wywołania udane / wszystkie | 30 / 30 (statusy HTTP: {"200": 30}) |
| zgodność ze schematem | 30 / 30 (1.000) |
| cytaty znalezione w tekście wejścia | 261 / 261 (1.000) |
| powtarzalność: bloki z identycznym JSON / identycznym tekstem / stabilnymi zweryfikowanymi wartościami | 1 / 1 / 9 z 10 kompletnych |
| opóźnienie p50 / p95 / max [s] | 5.42 / 8.30 / 8.38 |
| tokeny wejściowe: suma / max / średnia | 34473 / 2212 / 1149 |
| tokeny wyjściowe (w tym myślenie): suma / max / średnia | 47477 / 2767 / 1583 (myślenie łącznie: 0) |
| powody zakończenia | {"STOP": 30} |
| koszt na wywołanie, cena promocyjna (do 2026-12-31) | śr. 0.00680 USD, max 0.01109 USD |
| koszt na wywołanie, cena od 2027 | śr. 0.01359 USD, max 0.02218 USD |
| koszt na analizę (3 bloki, założenie): promocyjna / od 2027 | 0.02039 / 0.04078 USD |
| koszt na 1000 analiz: promocyjna / od 2027 | 20.39 / 40.78 USD |

Kryteria decyzji (progi proponowane, do potwierdzenia przez właściciela):

| Kryterium | Próg | Wynik | Spełnione |
|---|---|---|---|
| zgodność ze schematem | ≥ 0.95 | 1.000 | tak |
| cytaty znalezione w tekście | ≥ 0.9 | 1.000 | tak |
| bloki ze stabilnymi wartościami | ≥ 0.8 | 0.900 | tak |
| opóźnienie p95 [s] | ≤ 30.0 | 8.30 | tak |
| koszt na analizę, cena od 2027 [USD] | ≤ 0.05 | 0.04078 | tak |

Wynik mechaniczny: **GO**. Decyzję zapisuje właściciel w ADR-012.

Proponowane progi budżetowe wejściowe dla Task 20.15 (z pomiaru): max_input_tokens_per_request = 4000; max_requests_per_analysis = 6; max_input_tokens_per_analysis = 12000 (1.5 x the largest measured input, rounded up to 1000; requests cap = 2 x assumed blocks per analysis).

Zgodność z adnotacją jest informacyjna (10 bloków) i nie zastępuje bramki jakości z Task 20.17.
