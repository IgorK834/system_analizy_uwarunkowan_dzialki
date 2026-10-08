# Ewaluacja silnika `legacy` parsera MPZP (BK-603, PV3-03)

Plik jest generowany z `parameter_results.json`; nie jest edytowany ręcznie.

## Zamrożony manifest

| Pole | Wartość |
|---|---|
| `engine` / `engine_version` | `legacy` / `mpzp-parser/2.0` |
| `run_mode` | `offline` |
| `commit_sha` | `0d78dbf626e786357c721fc6fddc62836a9c25c3` |
| `manifest_sha256` | `7cd212acb70e59d28a132d6b2ad864e40c98cb910e1eb593b218e4a6f0922ea6` |
| `corpus_sha256` | `d32dd895374bdef98e65ba01a9eb890243124f708a686ad9e64db9b460ccd186` |
| `annotations_sha256` | `19f1faf273e4a6d1f063c518cf84f9fde6ef4014975860e73db0c9a3046751ff` |
| `freeze` | `{"annotations_sha256":"19f1faf273e4a6d1f063c518cf84f9fde6ef4014975860e73db0c9a3046751ff","frozen_at":"2026-09-30","parser_run_on_final_split_before_freeze":false,"parser_version_at_freeze":"mpzp-parser/2.0","statement":"Anotacje zbioru końcowego powstały z lektury tekstu źródłowego, zanim parser został uruchomiony na dokumentach zbioru końcowego. Zbiór rozwojowy składa się z dokumentów użytych wcześniej do rozwoju i regresji parsera. Po zamrożeniu anotacji nie wolno zmieniać bez nowego skrótu i nowego numeru wersji korpusu; parser nie jest stroiony w tym badaniu."}` |

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
| `overall` | 1.000 (62/62) [0.94; 1.00] | 0.248 (62/250) [0.20; 0.31] | 0.806 (50/62) [0.69; 0.89] | 0.200 (50/250) [0.16; 0.25] | 1.000 (37/37) [0.91; 1.00] | 0 | 0.680 (51/75) [0.57; 0.77] | 0.156 (39/250) [0.12; 0.21] |

### Według podziału (rozwojowy / końcowy)

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `development` | 1.000 (30/30) [0.89; 1.00] | 0.337 (30/89) [0.25; 0.44] | 0.767 (23/30) [0.59; 0.88] | 0.258 (23/89) [0.18; 0.36] | 1.000 (19/19) [0.83; 1.00] | 0 | 0.718 (28/39) [0.56; 0.83] | 0.213 (19/89) [0.14; 0.31] |
| `final` | 1.000 (32/32) [0.89; 1.00] | 0.199 (32/161) [0.14; 0.27] | 0.844 (27/32) [0.68; 0.93] | 0.168 (27/161) [0.12; 0.23] | 1.000 (18/18) [0.82; 1.00] | 0 | 0.639 (23/36) [0.48; 0.78] | 0.124 (20/161) [0.08; 0.18] |

### Według formatu

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `html` | 1.000 (5/5) [0.57; 1.00] | 0.217 (5/23) [0.10; 0.42] | 0.200 (1/5) [0.04; 0.62] | 0.043 (1/23) [0.01; 0.21] | null (no_eligible_cases) | 0 | 0.800 (4/5) [0.38; 0.96] | 0.043 (1/23) [0.01; 0.21] |
| `ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `ocr_simulated` | 1.000 (2/2) [0.34; 1.00] | 0.087 (2/23) [0.02; 0.27] | 0.500 (1/2) [0.09; 0.91] | 0.043 (1/23) [0.01; 0.21] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (3/3) [0.44; 1.00] | 0.043 (1/23) [0.01; 0.21] |
| `pdf_table` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/10) [0.00; 0.28] |
| `pdf_text` | 1.000 (53/53) [0.93; 1.00] | 0.276 (53/192) [0.22; 0.34] | 0.868 (46/53) [0.75; 0.93] | 0.240 (46/192) [0.18; 0.30] | 1.000 (35/35) [0.90; 1.00] | 0 | 0.646 (42/65) [0.52; 0.75] | 0.182 (35/192) [0.13; 0.24] |

### Podział × format

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `development/html` | 1.000 (3/3) [0.44; 1.00] | 0.167 (3/18) [0.06; 0.39] | 0.000 (0/3) [0.00; 0.56] | 0.000 (0/18) [0.00; 0.18] | null (no_eligible_cases) | 0 | 1.000 (3/3) [0.44; 1.00] | 0.000 (0/18) [0.00; 0.18] |
| `development/ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `development/pdf_table` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/10) [0.00; 0.28] |
| `development/pdf_text` | 1.000 (25/25) [0.87; 1.00] | 0.424 (25/59) [0.31; 0.55] | 0.840 (21/25) [0.65; 0.94] | 0.356 (21/59) [0.25; 0.48] | 1.000 (19/19) [0.83; 1.00] | 0 | 0.676 (23/34) [0.51; 0.81] | 0.288 (17/59) [0.19; 0.41] |
| `final/html` | 1.000 (2/2) [0.34; 1.00] | 0.400 (2/5) [0.12; 0.77] | 0.500 (1/2) [0.09; 0.91] | 0.200 (1/5) [0.04; 0.62] | null (no_eligible_cases) | 0 | 0.500 (1/2) [0.09; 0.91] | 0.200 (1/5) [0.04; 0.62] |
| `final/ocr_real` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 | null (no_source_checks) | null (no_eligible_cases) |
| `final/ocr_simulated` | 1.000 (2/2) [0.34; 1.00] | 0.087 (2/23) [0.02; 0.27] | 0.500 (1/2) [0.09; 0.91] | 0.043 (1/23) [0.01; 0.21] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (3/3) [0.44; 1.00] | 0.043 (1/23) [0.01; 0.21] |
| `final/pdf_text` | 1.000 (28/28) [0.88; 1.00] | 0.211 (28/133) [0.15; 0.29] | 0.893 (25/28) [0.73; 0.96] | 0.188 (25/133) [0.13; 0.26] | 1.000 (16/16) [0.81; 1.00] | 0 | 0.613 (19/31) [0.44; 0.76] | 0.135 (18/133) [0.09; 0.20] |

### Według gminy

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `Białystok` | 1.000 (4/4) [0.51; 1.00] | 0.286 (4/14) [0.12; 0.55] | 0.250 (1/4) [0.05; 0.70] | 0.071 (1/14) [0.01; 0.31] | 1.000 (2/2) [0.34; 1.00] | 0 | 0.000 (0/4) [0.00; 0.49] | 0.000 (0/14) [0.00; 0.22] |
| `Bielsko-Biała` | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (13/13) [0.77; 1.00] | 0 | 1.000 (18/18) [0.82; 1.00] | 1.000 (15/15) [0.80; 1.00] |
| `Kraków` | 1.000 (10/10) [0.72; 1.00] | 0.227 (10/44) [0.13; 0.37] | 0.600 (6/10) [0.31; 0.83] | 0.136 (6/44) [0.06; 0.27] | 1.000 (6/6) [0.61; 1.00] | 0 | 0.312 (5/16) [0.14; 0.56] | 0.045 (2/44) [0.01; 0.15] |
| `Krzemieniewo` | 1.000 (2/2) [0.34; 1.00] | 0.222 (2/9) [0.06; 0.55] | 1.000 (2/2) [0.34; 1.00] | 0.222 (2/9) [0.06; 0.55] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 0.222 (2/9) [0.06; 0.55] |
| `Legnica` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/10) [0.00; 0.28] |
| `Mogilany` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/10) [0.00; 0.28] |
| `Ostrów Wielkopolski` | 1.000 (6/6) [0.61; 1.00] | 0.182 (6/33) [0.09; 0.34] | 1.000 (6/6) [0.61; 1.00] | 0.182 (6/33) [0.09; 0.34] | 1.000 (3/3) [0.44; 1.00] | 0 | 0.833 (5/6) [0.44; 0.97] | 0.152 (5/33) [0.07; 0.31] |
| `Pisz` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 | null (no_source_checks) | null (no_eligible_cases) |
| `Raszków` | null (no_eligible_cases) | 0.000 (0/11) [0.00; 0.26] | null (no_eligible_cases) | 0.000 (0/11) [0.00; 0.26] | null (no_eligible_cases) | 0 | null (no_source_checks) | 0.000 (0/11) [0.00; 0.26] |
| `Stare Miasto` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `Szczytno` | 1.000 (6/6) [0.61; 1.00] | 0.188 (6/32) [0.09; 0.35] | 1.000 (6/6) [0.61; 1.00] | 0.188 (6/32) [0.09; 0.35] | 1.000 (1/1) [0.21; 1.00] | 0 | 1.000 (6/6) [0.61; 1.00] | 0.188 (6/32) [0.09; 0.35] |
| `Warszawa` | 1.000 (12/12) [0.76; 1.00] | 0.255 (12/47) [0.15; 0.40] | 0.917 (11/12) [0.65; 0.99] | 0.234 (11/47) [0.14; 0.37] | 1.000 (12/12) [0.76; 1.00] | 0 | 0.562 (9/16) [0.33; 0.77] | 0.128 (6/47) [0.06; 0.25] |
| `Łódź` | 1.000 (5/5) [0.57; 1.00] | 0.217 (5/23) [0.10; 0.42] | 0.200 (1/5) [0.04; 0.62] | 0.043 (1/23) [0.01; 0.21] | null (no_eligible_cases) | 0 | 0.800 (4/5) [0.38; 0.96] | 0.043 (1/23) [0.01; 0.21] |

### Wielostrefowe vs jednostrefowe

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `true` | 1.000 (55/55) [0.93; 1.00] | 0.252 (55/218) [0.20; 0.31] | 0.800 (44/55) [0.68; 0.88] | 0.202 (44/218) [0.15; 0.26] | 1.000 (37/37) [0.91; 1.00] | 0 | 0.662 (45/68) [0.54; 0.76] | 0.151 (33/218) [0.11; 0.20] |
| `false` | 1.000 (7/7) [0.65; 1.00] | 0.219 (7/32) [0.11; 0.39] | 0.857 (6/7) [0.49; 0.97] | 0.188 (6/32) [0.09; 0.35] | null (no_eligible_cases) | 0 | 0.857 (6/7) [0.49; 0.97] | 0.188 (6/32) [0.09; 0.35] |

### Według parametru

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `max_building_height_m` | 1.000 (23/23) [0.86; 1.00] | 0.535 (23/43) [0.39; 0.67] | 0.565 (13/23) [0.37; 0.74] | 0.302 (13/43) [0.19; 0.45] | 1.000 (14/14) [0.78; 1.00] | 0 | 0.500 (17/34) [0.34; 0.66] | 0.163 (7/43) [0.08; 0.30] |
| `min_intensity` | 1.000 (2/2) [0.34; 1.00] | 0.069 (2/29) [0.02; 0.22] | 1.000 (2/2) [0.34; 1.00] | 0.069 (2/29) [0.02; 0.22] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 0.069 (2/29) [0.02; 0.22] |
| `max_intensity` | 1.000 (2/2) [0.34; 1.00] | 0.051 (2/39) [0.01; 0.17] | 1.000 (2/2) [0.34; 1.00] | 0.051 (2/39) [0.01; 0.17] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (2/2) [0.34; 1.00] | 0.051 (2/39) [0.01; 0.17] |
| `max_building_coverage_percent` | 1.000 (3/3) [0.44; 1.00] | 0.088 (3/34) [0.03; 0.23] | 1.000 (3/3) [0.44; 1.00] | 0.088 (3/34) [0.03; 0.23] | 1.000 (2/2) [0.34; 1.00] | 0 | 1.000 (3/3) [0.44; 1.00] | 0.088 (3/34) [0.03; 0.23] |
| `min_biologically_active_percent` | 1.000 (19/19) [0.83; 1.00] | 0.475 (19/40) [0.33; 0.63] | 0.947 (18/19) [0.75; 0.99] | 0.450 (18/40) [0.31; 0.60] | 1.000 (15/15) [0.80; 1.00] | 0 | 0.737 (14/19) [0.51; 0.88] | 0.350 (14/40) [0.22; 0.50] |
| `roof_angle_min_deg` | 1.000 (3/3) [0.44; 1.00] | 0.143 (3/21) [0.05; 0.35] | 1.000 (3/3) [0.44; 1.00] | 0.143 (3/21) [0.05; 0.35] | 1.000 (2/2) [0.34; 1.00] | 0 | 0.750 (3/4) [0.30; 0.95] | 0.095 (2/21) [0.03; 0.29] |
| `roof_angle_max_deg` | 1.000 (3/3) [0.44; 1.00] | 0.111 (3/27) [0.04; 0.28] | 0.667 (2/3) [0.21; 0.94] | 0.074 (2/27) [0.02; 0.23] | 1.000 (2/2) [0.34; 1.00] | 0 | 0.750 (3/4) [0.30; 0.95] | 0.074 (2/27) [0.02; 0.23] |
| `max_storeys` | 1.000 (4/4) [0.51; 1.00] | 0.444 (4/9) [0.19; 0.73] | 1.000 (4/4) [0.51; 1.00] | 0.444 (4/9) [0.19; 0.73] | null (no_eligible_cases) | 0 | 1.000 (4/4) [0.51; 1.00] | 0.444 (4/9) [0.19; 0.73] |
| `setback_m` | 1.000 (3/3) [0.44; 1.00] | 0.375 (3/8) [0.14; 0.69] | 1.000 (3/3) [0.44; 1.00] | 0.375 (3/8) [0.14; 0.69] | null (no_eligible_cases) | 0 | 1.000 (3/3) [0.44; 1.00] | 0.375 (3/8) [0.14; 0.69] |

### Liczniki werdyktów

| Wycinek | `tp_exact` | `tp_partial` | `tp_wrong` | `fn` | `fp` | `tn` | `acceptable_only` | `general_found` | `general_missed` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `overall` | 50 | 9 | 3 | 188 | 0 | 161 | 0 | 0 | 12 |
| `development` | 23 | 4 | 3 | 59 | 0 | 82 | 0 | 0 | 0 |
| `final` | 27 | 5 | 0 | 129 | 0 | 79 | 0 | 0 | 12 |
| `html` | 1 | 1 | 3 | 18 | 0 | 13 | 0 | 0 | 0 |
| `ocr_real` | 2 | 0 | 0 | 0 | 0 | 16 | 0 | 0 | 0 |
| `ocr_simulated` | 1 | 1 | 0 | 21 | 0 | 13 | 0 | 0 | 0 |
| `pdf_table` | 0 | 0 | 0 | 10 | 0 | 8 | 0 | 0 | 0 |
| `pdf_text` | 46 | 7 | 0 | 139 | 0 | 111 | 0 | 0 | 12 |

Odkrywanie symboli bez podpowiedzi (recall symboli z anotacji): 0.234 (11/47) [0.14; 0.37].

## Źródło wartości (`source_consistent`)

Wartość zwrócona przez silnik jest *poprawna co do wartości*, gdy równa się wartości z adnotacji tej strefy. Jest *spójna ze źródłem*, gdy jej strona jest stroną cytatu adnotacji, a miejsce dopasowania leży w bloku strefy (od kotwicy do ostatniego cytatu tej kotwicy plus 300 znaków, nie dalej niż następna kotwica). Poprawna wartość z innej strony lub z bloku innej strefy jest **błędem przypisania, nie trafieniem**. Wartości, których źródła nie da się potwierdzić (`indeterminate`: identyczny tekst także poza blokiem; `unlocatable`: brak strony lub tekstu), nie są liczone jako spójne.

`source_consistent` = 0.680 (51/75) [0.57; 0.77]; przypisanie do strefy z uwzględnieniem źródła = 0.703 (26/37) [0.54; 0.83] (wg samej wartości: 1.000 (37/37) [0.91; 1.00]); dokładność end-to-end ze źródłem = 0.156 (39/250) [0.12; 0.21] (wg samej wartości: 0.200 (50/250) [0.16; 0.25]); pary z poprawną wartością z niewłaściwego źródła: 13.

| Status źródła | wszystkie poprawne wartości | w tym równe wartości wymaganej |
|---|---:|---:|
| `consistent` | 51 | 48 |
| `wrong_page` | 9 | 9 |
| `wrong_block` | 8 | 8 |
| `indeterminate` | 7 | 7 |
| `unlocatable` | 0 | 0 |
| `not_checked` | 0 | 0 |
| razem | 75 | 72 |

Podstawa porównania: `{"block":69,"evidence":3,"page":3}` (`block` = kotwica strefy, `evidence` = tylko cytat adnotacji bez kotwicy, `page` = tylko strona, gdy tekst anotowany nie jest tekstem odczytanym przez silnik, np. skan symulowany).

## Kalibracja confidence

Wartości zwróconych parametrów w strefach z adnotacją: n = 89; Brier = 0.172; ECE = 0.244.

| Przedział confidence | n | błędne | odsetek błędów | 95% CI | średnie confidence |
|---|---:|---:|---:|---|---:|
| `low` | 34 | 11 | 0.324 | [0.19; 0.49] | 0.43 |
| `medium` | 41 | 3 | 0.073 | [0.03; 0.19] | 0.65 |
| `high` | 14 | 0 | 0.000 | [0.00; 0.22] | 0.85 |

| Wartość confidence | n | błędne | odsetek błędów |
|---:|---:|---:|---:|
| 0.43 | 34 | 11 | 0.324 |
| 0.51 | 14 | 3 | 0.214 |
| 0.72 | 27 | 0 | 0.000 |
| 0.85 | 14 | 0 | 0.000 |

Niskie vs wysokie confidence: odsetek błędów 0.324 vs 0.000; niskie częściej błędne: **tak**; test Fishera (jednostronny) p = 0.0127.

Flaga `manual_review_required`: oznaczono 48 wartości; błędnych wśród oznaczonych: 0.292 (14/48) [0.18; 0.43]; odsetek błędów wychwyconych flagą: 1.000 (14/14) [0.78; 1.00].

Kalibracje dla wycinków (split, format) są w `metrics.json`; małe n oznacza szerokie przedziały.

## Błędy

Wpisów w śladzie błędów: 206 (`errors.json`, `errors.csv`). Każdy ma dokument, SHA-256, stronę anotacji, wynik parsera i wersję parsera.

| Typ | liczba |
|---|---:|
| `detection_fn` | 188 |
| `value_error` | 5 |
| `wrong_source` | 13 |

Rozdzielenie błędów: **rozpoznanie** (`detection_fn`, `detection_fp`, `zone_not_found`: czy wartość została znaleziona), **normalizacja/wartość** (`value_error`: wartość znaleziona, ale inna niż w adnotacji lub z dodatkową wartością) i **przypisanie** (`zone_assignment`: wartość innej strefy).

| Kategoria | pdf_text | pdf_table | html | ocr_real | ocr_simulated | razem |
|---|---:|---:|---:|---:|---:|---:|
| `recognition` | 139 | 10 | 18 | 0 | 21 | 188 |
| `normalization_or_value` | 1 | 0 | 3 | 0 | 1 | 5 |
| `assignment` | 12 | 0 | 1 | 0 | 0 | 13 |

Wskazówki przyczyn (wyprowadzone z oznaczeń niejednoznaczności w adnotacji; nie są dowodem przyczyny w kodzie parsera):

| Wskazówka | liczba |
|---|---:|
| `wording_not_matched` | 124 |
| `conditional_value` | 16 |
| `value_from_other_source` | 13 |
| `implicit_percent` | 12 |
| `ocr_noise_or_wording` | 12 |
| `extraction_artifact` | 8 |
| `shared_section` | 6 |
| `value_from_other_context` | 4 |
| `residual_clause` | 4 |
| `number_word` | 4 |
| `building_line_not_setback_phrase` | 3 |

## Zakres strefy (PV3-06)

Silnik nie raportuje bloków zakresu strefy (zakres = cały paragraf kandydackich segmentów), więc zasięg i zanieczyszczenie nie są mierzone.

## Bramki odrzuceń kandydatów

Silnik nie zgłosił odrzuceń (nie ma bramek albo żaden kandydat nie został odrzucony).

## Koszt i opóźnienie (obserwacja, nie wynik merytoryczny)

Czas ścienny, opóźnienie modelu, tokeny i koszt zależą od środowiska i biegu i są poza skrótem determinizmu. `—` oznacza, że silnik tego nie raportuje (nie zero). W trybie odtwarzania wartości modelu pochodzą z nagrania.

| Wielkość | Wartość |
|---|---:|
| próbek / dokumentów / stref | 21 / 17 / 47 |
| wywołania modelu | 0 |
| tokeny wejściowe / wyjściowe | — / — |
| czas ścienny na próbkę p50 / p95 [ms] | 8.8 / 21.3 |
| opóźnienie modelu p50 / p95 [ms] | — / — |
| koszt łącznie [USD] | — |
| koszt na dokument / strefę [USD] | — / — |
