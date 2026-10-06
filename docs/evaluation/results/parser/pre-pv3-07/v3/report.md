# Ewaluacja silnika `v3` parsera MPZP (BK-603, PV3-03)

Plik jest generowany z `parameter_results.json`; nie jest edytowany ręcznie.

## Zamrożony manifest

| Pole | Wartość |
|---|---|
| `engine` / `engine_version` | `v3` / `mpzp-parser/3.0-scope.1` |
| `run_mode` | `offline` |
| `commit_sha` | `0d78dbf626e786357c721fc6fddc62836a9c25c3` |
| `manifest_sha256` | `e2bcddd54f245a2c433286a0116772cfef9ad357f70e7a3fcbd7f74c6c415126` |
| `corpus_sha256` | `ca5a005efeff440fc59aeca9f9909f5ee8313d7f9dd314baab4bf31c15a8fdd0` |
| `annotations_sha256` | `1408aee8787834219103448e5ebbc4773951d3f65d252a05d381c91087be7244` |
| `freeze` | `{"annotations_sha256":"1408aee8787834219103448e5ebbc4773951d3f65d252a05d381c91087be7244","frozen_at":"2026-09-30","parser_run_on_final_split_before_freeze":false,"parser_version_at_freeze":"mpzp-parser/2.0","statement":"Anotacje zbioru końcowego powstały z lektury tekstu źródłowego, zanim parser został uruchomiony na dokumentach zbioru końcowego. Zbiór rozwojowy składa się z dokumentów użytych wcześniej do rozwoju i regresji parsera. Po zamrożeniu anotacji nie wolno zmieniać bez nowego skrótu i nowego numeru wersji korpusu; parser nie jest stroiony w tym badaniu."}` |

## Korpus

- próbek: 21 (rozwojowe: 7, końcowe: 14);
- dokumentów: 17 z 13 gmin;
- formaty próbek: `{"html":2,"ocr_real":2,"ocr_simulated":2,"pdf_table":1,"pdf_text":14}`;
- próbek wielostrefowych: 14;
- zonów z adnotacją: 47; adnotacji parametrów: 321.

Jednostką detekcji jest para (strefa, parametr katalogu). `fp` to wartość zwrócona dla pary, której dokument nie zawiera; wartość akceptowalna (`acceptable`) nie jest błędem. Ustalenia z klauzul ogólnych (`general_clause`) raportowane są osobno i nie wchodzą do recall, bo parser przypisuje wartości z sekcji strefy.

## Wyniki łączne

### Łącznie

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `overall` | 1.000 (73/73) [0.95; 1.00] | 0.292 (73/250) [0.24; 0.35] | 0.849 (62/73) [0.75; 0.91] | 0.248 (62/250) [0.20; 0.31] | 1.000 (43/43) [0.92; 1.00] | 0 | 1.000 (88/88) [0.96; 1.00] | 0.248 (62/250) [0.20; 0.31] |

### Według podziału (rozwojowy / końcowy)

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `development` | 1.000 (32/32) [0.89; 1.00] | 0.360 (32/89) [0.27; 0.46] | 0.906 (29/32) [0.76; 0.97] | 0.326 (29/89) [0.24; 0.43] | 1.000 (21/21) [0.85; 1.00] | 0 | 1.000 (41/41) [0.91; 1.00] | 0.326 (29/89) [0.24; 0.43] |
| `final` | 1.000 (41/41) [0.91; 1.00] | 0.255 (41/161) [0.19; 0.33] | 0.805 (33/41) [0.66; 0.90] | 0.205 (33/161) [0.15; 0.27] | 1.000 (22/22) [0.85; 1.00] | 0 | 1.000 (47/47) [0.92; 1.00] | 0.205 (33/161) [0.15; 0.27] |

### Według formatu

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `html` | 1.000 (6/6) [0.61; 1.00] | 0.261 (6/23) [0.13; 0.46] | 0.500 (3/6) [0.19; 0.81] | 0.130 (3/23) [0.05; 0.32] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (6/6) [0.61; 1.00] | 0.130 (3/23) [0.05; 0.32] |
| `ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `ocr_simulated` | 1.000 (6/6) [0.61; 1.00] | 0.261 (6/23) [0.13; 0.46] | 0.000 (0/6) [0.00; 0.39] | 0.000 (0/23) [0.00; 0.14] | 1.000 (6/6) [0.61; 1.00] | 0 | 1.000 (9/9) [0.70; 1.00] | 0.000 (0/23) [0.00; 0.14] |
| `pdf_table` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/10) [0.00; 0.28] |
| `pdf_text` | 1.000 (59/59) [0.94; 1.00] | 0.307 (59/192) [0.25; 0.38] | 0.966 (57/59) [0.88; 0.99] | 0.297 (57/192) [0.24; 0.36] | 1.000 (35/35) [0.90; 1.00] | 0 | 1.000 (71/71) [0.95; 1.00] | 0.297 (57/192) [0.24; 0.36] |

### Podział × format

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `development/html` | 1.000 (5/5) [0.57; 1.00] | 0.278 (5/18) [0.12; 0.51] | 0.400 (2/5) [0.12; 0.77] | 0.111 (2/18) [0.03; 0.33] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (5/5) [0.57; 1.00] | 0.111 (2/18) [0.03; 0.33] |
| `development/ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `development/pdf_table` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/10) [0.00; 0.28] |
| `development/pdf_text` | 1.000 (25/25) [0.87; 1.00] | 0.424 (25/59) [0.31; 0.55] | 1.000 (25/25) [0.87; 1.00] | 0.424 (25/59) [0.31; 0.55] | 1.000 (19/19) [0.83; 1.00] | 0 | 1.000 (34/34) [0.90; 1.00] | 0.424 (25/59) [0.31; 0.55] |
| `final/html` | 1.000 (1/1) [0.21; 1.00] | 0.200 (1/5) [0.04; 0.62] | 1.000 (1/1) [0.21; 1.00] | 0.200 (1/5) [0.04; 0.62] | null (no_eligible_cases) | 0 | 1.000 (1/1) [0.21; 1.00] | 0.200 (1/5) [0.04; 0.62] |
| `final/ocr_real` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 | null (no_source_checks) | null (no_eligible_cases) |
| `final/ocr_simulated` | 1.000 (6/6) [0.61; 1.00] | 0.261 (6/23) [0.13; 0.46] | 0.000 (0/6) [0.00; 0.39] | 0.000 (0/23) [0.00; 0.14] | 1.000 (6/6) [0.61; 1.00] | 0 | 1.000 (9/9) [0.70; 1.00] | 0.000 (0/23) [0.00; 0.14] |
| `final/pdf_text` | 1.000 (34/34) [0.90; 1.00] | 0.256 (34/133) [0.19; 0.34] | 0.941 (32/34) [0.81; 0.98] | 0.241 (32/133) [0.18; 0.32] | 1.000 (16/16) [0.81; 1.00] | 0 | 1.000 (37/37) [0.91; 1.00] | 0.241 (32/133) [0.18; 0.32] |

### Według gminy

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `Białystok` | 1.000 (4/4) [0.51; 1.00] | 0.286 (4/14) [0.12; 0.55] | 0.500 (2/4) [0.15; 0.85] | 0.143 (2/14) [0.04; 0.40] | null (no_eligible_cases) | 0 | 1.000 (4/4) [0.51; 1.00] | 0.143 (2/14) [0.04; 0.40] |
| `Bielsko-Biała` | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (13/13) [0.77; 1.00] | 0 | 1.000 (18/18) [0.82; 1.00] | 1.000 (15/15) [0.80; 1.00] |
| `Kraków` | 1.000 (10/10) [0.72; 1.00] | 0.227 (10/44) [0.13; 0.37] | 1.000 (10/10) [0.72; 1.00] | 0.227 (10/44) [0.13; 0.37] | 1.000 (6/6) [0.61; 1.00] | 0 | 1.000 (16/16) [0.81; 1.00] | 0.227 (10/44) [0.13; 0.37] |
| `Krzemieniewo` | 1.000 (2/2) [0.34; 1.00] | 0.222 (2/9) [0.06; 0.55] | 1.000 (2/2) [0.34; 1.00] | 0.222 (2/9) [0.06; 0.55] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 0.222 (2/9) [0.06; 0.55] |
| `Legnica` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/10) [0.00; 0.28] |
| `Mogilany` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/10) [0.00; 0.28] |
| `Ostrów Wielkopolski` | 1.000 (9/9) [0.70; 1.00] | 0.273 (9/33) [0.15; 0.44] | 1.000 (9/9) [0.70; 1.00] | 0.273 (9/33) [0.15; 0.44] | 1.000 (4/4) [0.51; 1.00] | 0 | 1.000 (9/9) [0.70; 1.00] | 0.273 (9/33) [0.15; 0.44] |
| `Pisz` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 | null (no_source_checks) | null (no_eligible_cases) |
| `Raszków` | null (no_eligible_cases) | 0.000 (0/11) [0.00; 0.26] | null (no_eligible_cases) | 0.000 (0/11) [0.00; 0.26] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/11) [0.00; 0.26] |
| `Stare Miasto` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `Szczytno` | 1.000 (8/8) [0.68; 1.00] | 0.250 (8/32) [0.13; 0.42] | 1.000 (8/8) [0.68; 1.00] | 0.250 (8/32) [0.13; 0.42] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (8/8) [0.68; 1.00] | 0.250 (8/32) [0.13; 0.42] |
| `Warszawa` | 1.000 (17/17) [0.82; 1.00] | 0.362 (17/47) [0.24; 0.50] | 0.647 (11/17) [0.41; 0.83] | 0.234 (11/47) [0.14; 0.37] | 1.000 (16/16) [0.81; 1.00] | 0 | 1.000 (23/23) [0.86; 1.00] | 0.234 (11/47) [0.14; 0.37] |
| `Łódź` | 1.000 (6/6) [0.61; 1.00] | 0.261 (6/23) [0.13; 0.46] | 0.500 (3/6) [0.19; 0.81] | 0.130 (3/23) [0.05; 0.32] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (6/6) [0.61; 1.00] | 0.130 (3/23) [0.05; 0.32] |

### Wielostrefowe vs jednostrefowe

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `true` | 1.000 (66/66) [0.94; 1.00] | 0.303 (66/218) [0.25; 0.37] | 0.833 (55/66) [0.73; 0.90] | 0.252 (55/218) [0.20; 0.31] | 1.000 (43/43) [0.92; 1.00] | 0 | 1.000 (81/81) [0.95; 1.00] | 0.252 (55/218) [0.20; 0.31] |
| `false` | 1.000 (7/7) [0.65; 1.00] | 0.219 (7/32) [0.11; 0.39] | 1.000 (7/7) [0.65; 1.00] | 0.219 (7/32) [0.11; 0.39] | null (no_eligible_cases) | 0 | 1.000 (7/7) [0.65; 1.00] | 0.219 (7/32) [0.11; 0.39] |

### Według parametru

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `max_building_height_m` | 1.000 (24/24) [0.86; 1.00] | 0.558 (24/43) [0.41; 0.70] | 0.750 (18/24) [0.55; 0.88] | 0.419 (18/43) [0.28; 0.57] | 1.000 (15/15) [0.80; 1.00] | 0 | 1.000 (37/37) [0.91; 1.00] | 0.419 (18/43) [0.28; 0.57] |
| `min_intensity` | 1.000 (2/2) [0.34; 1.00] | 0.069 (2/29) [0.02; 0.22] | 1.000 (2/2) [0.34; 1.00] | 0.069 (2/29) [0.02; 0.22] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 0.069 (2/29) [0.02; 0.22] |
| `max_intensity` | 1.000 (2/2) [0.34; 1.00] | 0.051 (2/39) [0.01; 0.17] | 1.000 (2/2) [0.34; 1.00] | 0.051 (2/39) [0.01; 0.17] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (2/2) [0.34; 1.00] | 0.051 (2/39) [0.01; 0.17] |
| `max_building_coverage_percent` | 1.000 (3/3) [0.44; 1.00] | 0.088 (3/34) [0.03; 0.23] | 1.000 (3/3) [0.44; 1.00] | 0.088 (3/34) [0.03; 0.23] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (3/3) [0.44; 1.00] | 0.088 (3/34) [0.03; 0.23] |
| `min_biologically_active_percent` | 1.000 (24/24) [0.86; 1.00] | 0.600 (24/40) [0.45; 0.74] | 0.875 (21/24) [0.69; 0.96] | 0.525 (21/40) [0.37; 0.67] | 1.000 (20/20) [0.84; 1.00] | 0 | 1.000 (24/24) [0.86; 1.00] | 0.525 (21/40) [0.37; 0.67] |
| `roof_angle_min_deg` | 1.000 (4/4) [0.51; 1.00] | 0.190 (4/21) [0.08; 0.40] | 1.000 (4/4) [0.51; 1.00] | 0.190 (4/21) [0.08; 0.40] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (5/5) [0.57; 1.00] | 0.190 (4/21) [0.08; 0.40] |
| `roof_angle_max_deg` | 1.000 (4/4) [0.51; 1.00] | 0.148 (4/27) [0.06; 0.32] | 0.500 (2/4) [0.15; 0.85] | 0.074 (2/27) [0.02; 0.23] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (5/5) [0.57; 1.00] | 0.074 (2/27) [0.02; 0.23] |
| `max_storeys` | 1.000 (5/5) [0.57; 1.00] | 0.556 (5/9) [0.27; 0.81] | 1.000 (5/5) [0.57; 1.00] | 0.556 (5/9) [0.27; 0.81] | null (no_eligible_cases) | 0 | 1.000 (5/5) [0.57; 1.00] | 0.556 (5/9) [0.27; 0.81] |
| `setback_m` | 1.000 (5/5) [0.57; 1.00] | 0.625 (5/8) [0.31; 0.86] | 1.000 (5/5) [0.57; 1.00] | 0.625 (5/8) [0.31; 0.86] | null (no_eligible_cases) | 0 | 1.000 (5/5) [0.57; 1.00] | 0.625 (5/8) [0.31; 0.86] |

### Liczniki werdyktów

| Wycinek | `tp_exact` | `tp_partial` | `tp_wrong` | `fn` | `fp` | `tn` | `acceptable_only` | `general_found` | `general_missed` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `overall` | 62 | 8 | 3 | 177 | 0 | 161 | 0 | 0 | 12 |
| `development` | 29 | 0 | 3 | 57 | 0 | 82 | 0 | 0 | 0 |
| `final` | 33 | 8 | 0 | 120 | 0 | 79 | 0 | 0 | 12 |
| `html` | 3 | 0 | 3 | 17 | 0 | 13 | 0 | 0 | 0 |
| `ocr_real` | 2 | 0 | 0 | 0 | 0 | 16 | 0 | 0 | 0 |
| `ocr_simulated` | 0 | 6 | 0 | 17 | 0 | 13 | 0 | 0 | 0 |
| `pdf_table` | 0 | 0 | 0 | 10 | 0 | 8 | 0 | 0 | 0 |
| `pdf_text` | 57 | 2 | 0 | 133 | 0 | 111 | 0 | 0 | 12 |

Odkrywanie symboli bez podpowiedzi (recall symboli z anotacji): 0.234 (11/47) [0.14; 0.37].

## Źródło wartości (`source_consistent`)

Wartość zwrócona przez silnik jest *poprawna co do wartości*, gdy równa się wartości z adnotacji tej strefy. Jest *spójna ze źródłem*, gdy jej strona jest stroną cytatu adnotacji, a miejsce dopasowania leży w bloku strefy (od kotwicy do ostatniego cytatu tej kotwicy plus 300 znaków, nie dalej niż następna kotwica). Poprawna wartość z innej strony lub z bloku innej strefy jest **błędem przypisania, nie trafieniem**. Wartości, których źródła nie da się potwierdzić (`indeterminate`: identyczny tekst także poza blokiem; `unlocatable`: brak strony lub tekstu), nie są liczone jako spójne.

`source_consistent` = 1.000 (88/88) [0.96; 1.00]; przypisanie do strefy z uwzględnieniem źródła = 1.000 (43/43) [0.92; 1.00] (wg samej wartości: 1.000 (43/43) [0.92; 1.00]); dokładność end-to-end ze źródłem = 0.248 (62/250) [0.20; 0.31] (wg samej wartości: 0.248 (62/250) [0.20; 0.31]); pary z poprawną wartością z niewłaściwego źródła: 0.

| Status źródła | wszystkie poprawne wartości | w tym równe wartości wymaganej |
|---|---:|---:|
| `consistent` | 88 | 85 |
| `wrong_page` | 0 | 0 |
| `wrong_block` | 0 | 0 |
| `indeterminate` | 0 | 0 |
| `unlocatable` | 0 | 0 |
| `not_checked` | 0 | 0 |
| razem | 88 | 85 |

Podstawa porównania: `{"block":74,"evidence":5,"page":9}` (`block` = kotwica strefy, `evidence` = tylko cytat adnotacji bez kotwicy, `page` = tylko strona, gdy tekst anotowany nie jest tekstem odczytanym przez silnik, np. skan symulowany).

## Kalibracja confidence

Wartości zwróconych parametrów w strefach z adnotacją: n = 106; Brier = 0.173; ECE = 0.287.

| Przedział confidence | n | błędne | odsetek błędów | 95% CI | średnie confidence |
|---|---:|---:|---:|---|---:|
| `low` | 34 | 18 | 0.529 | [0.37; 0.69] | 0.17 |
| `medium` | 37 | 0 | 0.000 | [0.00; 0.09] | 0.59 |
| `high` | 35 | 0 | 0.000 | [0.00; 0.10] | 0.85 |

| Wartość confidence | n | błędne | odsetek błędów |
|---:|---:|---:|---:|
| 0.09 | 24 | 15 | 0.625 |
| 0.20 | 2 | 0 | 0.000 |
| 0.39 | 6 | 3 | 0.500 |
| 0.48 | 2 | 0 | 0.000 |
| 0.51 | 24 | 0 | 0.000 |
| 0.61 | 1 | 0 | 0.000 |
| 0.76 | 12 | 0 | 0.000 |
| 0.85 | 35 | 0 | 0.000 |

Niskie vs wysokie confidence: odsetek błędów 0.529 vs 0.000; niskie częściej błędne: **tak**; test Fishera (jednostronny) p = 0.0000.

Flaga `manual_review_required`: oznaczono 59 wartości; błędnych wśród oznaczonych: 0.305 (18/59) [0.20; 0.43]; odsetek błędów wychwyconych flagą: 1.000 (18/18) [0.82; 1.00].

Kalibracje dla wycinków (split, format) są w `metrics.json`; małe n oznacza szerokie przedziały.

## Błędy

Wpisów w śladzie błędów: 188 (`errors.json`, `errors.csv`). Każdy ma dokument, SHA-256, stronę anotacji, wynik parsera i wersję parsera.

| Typ | liczba |
|---|---:|
| `detection_fn` | 177 |
| `value_error` | 11 |

Rozdzielenie błędów: **rozpoznanie** (`detection_fn`, `detection_fp`, `zone_not_found`: czy wartość została znaleziona), **normalizacja/wartość** (`value_error`: wartość znaleziona, ale inna niż w adnotacji lub z dodatkową wartością) i **przypisanie** (`zone_assignment`: wartość innej strefy).

| Kategoria | pdf_text | pdf_table | html | ocr_real | ocr_simulated | razem |
|---|---:|---:|---:|---:|---:|---:|
| `recognition` | 133 | 10 | 17 | 0 | 17 | 177 |
| `normalization_or_value` | 2 | 0 | 3 | 0 | 6 | 11 |
| `assignment` | 0 | 0 | 0 | 0 | 0 | 0 |

Wskazówki przyczyn (wyprowadzone z oznaczeń niejednoznaczności w adnotacji; nie są dowodem przyczyny w kodzie parsera):

| Wskazówka | liczba |
|---|---:|
| `wording_not_matched` | 119 |
| `conditional_value` | 18 |
| `implicit_percent` | 12 |
| `ocr_noise_or_wording` | 10 |
| `extraction_artifact` | 8 |
| `shared_section` | 6 |
| `value_from_other_context` | 6 |
| `number_word` | 4 |
| `building_line_not_setback_phrase` | 3 |
| `residual_clause` | 2 |

## Zakres strefy (PV3-06)

**Zasięg zakresu**: cytat anotacji leży w bloku zwróconym dla strefy (dowolny rodzaj bloku). **Zanieczyszczenie**: cytat anotacji INNEJ strefy (w innym miejscu niż cytaty tej strefy) leży w bloku `zone_section` tej strefy — oczekiwane 0. Układ (strategia 1–6) pochodzi z etykiet poza zamrożonymi anotacjami (`scope_strategies.json`) albo z pola `scope_strategy` strefy; klauzule ogólne liczone są jako układ 5.

Zasięg: 1.000 (294/294) [0.99; 1.00]; w blokach `zone_section`: 0.888 (261/294) [0.85; 0.92]; zanieczyszczenie: 0.000 (0/428) [0.00; 0.01].

| Układ | Zasięg zakresu | Zanieczyszczenie |
|---|---|---|
| `0` | 1.000 (2/2) [0.34; 1.00] | brak par |
| `1` | 1.000 (139/139) [0.97; 1.00] | 0.000 (0/228) [0.00; 0.02] |
| `2` | 1.000 (53/53) [0.93; 1.00] | 0.000 (0/64) [0.00; 0.06] |
| `3` | 1.000 (48/48) [0.93; 1.00] | 0.000 (0/114) [0.00; 0.03] |
| `4` | 1.000 (30/30) [0.89; 1.00] | 0.000 (0/22) [0.00; 0.15] |
| `5` | 1.000 (12/12) [0.76; 1.00] | brak par |
| `6` | 1.000 (10/10) [0.72; 1.00] | brak par |

| Zastosowalność | Zasięg zakresu |
|---|---|
| `general_clause` | 1.000 (12/12) [0.76; 1.00] |
| `zone_section` | 1.000 (282/282) [0.99; 1.00] |

| Format | Zasięg zakresu |
|---|---|
| `html` | 1.000 (37/37) [0.91; 1.00] |
| `ocr_real` | 1.000 (2/2) [0.34; 1.00] |
| `pdf_table` | 1.000 (10/10) [0.72; 1.00] |
| `pdf_text` | 1.000 (245/245) [0.98; 1.00] |

## Bramki odrzuceń kandydatów

Silnik nie zgłosił odrzuceń (nie ma bramek albo żaden kandydat nie został odrzucony).

## Koszt i opóźnienie (obserwacja, nie wynik merytoryczny)

Czas ścienny, opóźnienie modelu, tokeny i koszt zależą od środowiska i biegu i są poza skrótem determinizmu. `—` oznacza, że silnik tego nie raportuje (nie zero). W trybie odtwarzania wartości modelu pochodzą z nagrania.

| Wielkość | Wartość |
|---|---:|
| próbek / dokumentów / stref | 21 / 17 / 47 |
| wywołania modelu | 0 |
| tokeny wejściowe / wyjściowe | — / — |
| czas ścienny na próbkę p50 / p95 [ms] | 69.3 / 113.2 |
| opóźnienie modelu p50 / p95 [ms] | — / — |
| koszt łącznie [USD] | — |
| koszt na dokument / strefę [USD] | — / — |
