# Ewaluacja silnika `legacy` parsera MPZP (BK-603, PV3-03)

Plik jest generowany z `parameter_results.json`; nie jest edytowany ręcznie.

## Zamrożony manifest

| Pole | Wartość |
|---|---|
| `engine` / `engine_version` | `legacy` / `mpzp-parser/3.0-det` |
| `run_mode` | `offline` |
| `commit_sha` | `644e39a249d44beaead0bc30925bc02aac187ce7` |
| `manifest_sha256` | `fb20dd25f907463c6ad86e861890508143e28491feb7f15e848d26b2f1781896` |
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
| `overall` | 1.000 (208/208) [0.98; 1.00] | 0.832 (208/250) [0.78; 0.87] | 0.904 (188/208) [0.86; 0.94] | 0.752 (188/250) [0.69; 0.80] | 1.000 (98/98) [0.96; 1.00] | 0 | 0.768 (209/272) [0.71; 0.81] | 0.548 (137/250) [0.49; 0.61] |

### Według podziału (rozwojowy / końcowy)

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `development` | 1.000 (89/89) [0.96; 1.00] | 1.000 (89/89) [0.96; 1.00] | 0.843 (75/89) [0.75; 0.90] | 0.843 (75/89) [0.75; 0.90] | 1.000 (44/44) [0.92; 1.00] | 0 | 0.652 (73/112) [0.56; 0.73] | 0.494 (44/89) [0.39; 0.60] |
| `final` | 1.000 (119/119) [0.97; 1.00] | 0.739 (119/161) [0.67; 0.80] | 0.950 (113/119) [0.89; 0.98] | 0.702 (113/161) [0.63; 0.77] | 1.000 (54/54) [0.93; 1.00] | 0 | 0.850 (136/160) [0.79; 0.90] | 0.578 (93/161) [0.50; 0.65] |

### Według formatu

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `html` | 1.000 (23/23) [0.86; 1.00] | 1.000 (23/23) [0.86; 1.00] | 0.783 (18/23) [0.58; 0.90] | 0.783 (18/23) [0.58; 0.90] | 1.000 (9/9) [0.70; 1.00] | 0 | 0.925 (37/40) [0.80; 0.97] | 0.652 (15/23) [0.45; 0.81] |
| `ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `ocr_simulated` | 1.000 (9/9) [0.70; 1.00] | 0.391 (9/23) [0.22; 0.59] | 0.778 (7/9) [0.45; 0.94] | 0.304 (7/23) [0.16; 0.51] | 1.000 (4/4) [0.51; 1.00] | 0 | 1.000 (10/10) [0.72; 1.00] | 0.304 (7/23) [0.16; 0.51] |
| `pdf_table` | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | null (no_eligible_cases) | 0 | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] |
| `pdf_text` | 1.000 (164/164) [0.98; 1.00] | 0.854 (164/192) [0.80; 0.90] | 0.921 (151/164) [0.87; 0.95] | 0.786 (151/192) [0.72; 0.84] | 1.000 (85/85) [0.96; 1.00] | 0 | 0.714 (150/210) [0.65; 0.77] | 0.536 (103/192) [0.47; 0.61] |

### Podział × format

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `development/html` | 1.000 (18/18) [0.82; 1.00] | 1.000 (18/18) [0.82; 1.00] | 0.833 (15/18) [0.61; 0.94] | 0.833 (15/18) [0.61; 0.94] | 1.000 (9/9) [0.70; 1.00] | 0 | 0.909 (30/33) [0.76; 0.97] | 0.667 (12/18) [0.44; 0.84] |
| `development/ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `development/pdf_table` | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | null (no_eligible_cases) | 0 | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] |
| `development/pdf_text` | 1.000 (59/59) [0.94; 1.00] | 1.000 (59/59) [0.94; 1.00] | 0.814 (48/59) [0.70; 0.89] | 0.814 (48/59) [0.70; 0.89] | 1.000 (35/35) [0.90; 1.00] | 0 | 0.463 (31/67) [0.35; 0.58] | 0.339 (20/59) [0.23; 0.47] |
| `final/html` | 1.000 (5/5) [0.57; 1.00] | 1.000 (5/5) [0.57; 1.00] | 0.600 (3/5) [0.23; 0.88] | 0.600 (3/5) [0.23; 0.88] | null (no_eligible_cases) | 0 | 1.000 (7/7) [0.65; 1.00] | 0.600 (3/5) [0.23; 0.88] |
| `final/ocr_real` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 | null (no_source_checks) | null (no_eligible_cases) |
| `final/ocr_simulated` | 1.000 (9/9) [0.70; 1.00] | 0.391 (9/23) [0.22; 0.59] | 0.778 (7/9) [0.45; 0.94] | 0.304 (7/23) [0.16; 0.51] | 1.000 (4/4) [0.51; 1.00] | 0 | 1.000 (10/10) [0.72; 1.00] | 0.304 (7/23) [0.16; 0.51] |
| `final/pdf_text` | 1.000 (105/105) [0.96; 1.00] | 0.789 (105/133) [0.71; 0.85] | 0.981 (103/105) [0.93; 0.99] | 0.774 (103/133) [0.70; 0.84] | 1.000 (50/50) [0.93; 1.00] | 0 | 0.832 (119/143) [0.76; 0.88] | 0.624 (83/133) [0.54; 0.70] |

### Według gminy

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `Białystok` | 1.000 (12/12) [0.76; 1.00] | 0.857 (12/14) [0.60; 0.96] | 1.000 (12/12) [0.76; 1.00] | 0.857 (12/14) [0.60; 0.96] | 1.000 (8/8) [0.68; 1.00] | 0 | 0.846 (22/26) [0.66; 0.94] | 0.643 (9/14) [0.39; 0.84] |
| `Bielsko-Biała` | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (13/13) [0.77; 1.00] | 0 | 1.000 (18/18) [0.82; 1.00] | 1.000 (15/15) [0.80; 1.00] |
| `Kraków` | 1.000 (44/44) [0.92; 1.00] | 1.000 (44/44) [0.92; 1.00] | 0.750 (33/44) [0.61; 0.85] | 0.750 (33/44) [0.61; 0.85] | 1.000 (22/22) [0.85; 1.00] | 0 | 0.265 (13/49) [0.16; 0.40] | 0.114 (5/44) [0.05; 0.24] |
| `Krzemieniewo` | 1.000 (9/9) [0.70; 1.00] | 1.000 (9/9) [0.70; 1.00] | 0.889 (8/9) [0.57; 0.98] | 0.889 (8/9) [0.57; 0.98] | null (no_eligible_cases) | 0 | 1.000 (12/12) [0.76; 1.00] | 0.889 (8/9) [0.57; 0.98] |
| `Legnica` | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | null (no_eligible_cases) | 0 | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] |
| `Mogilany` | 1.000 (6/6) [0.61; 1.00] | 0.600 (6/10) [0.31; 0.83] | 1.000 (6/6) [0.61; 1.00] | 0.600 (6/10) [0.31; 0.83] | null (no_eligible_cases) | 0 | 1.000 (6/6) [0.61; 1.00] | 0.600 (6/10) [0.31; 0.83] |
| `Ostrów Wielkopolski` | 1.000 (23/23) [0.86; 1.00] | 0.697 (23/33) [0.53; 0.83] | 1.000 (23/23) [0.86; 1.00] | 0.697 (23/33) [0.53; 0.83] | 1.000 (12/12) [0.76; 1.00] | 0 | 0.692 (18/26) [0.50; 0.83] | 0.485 (16/33) [0.33; 0.65] |
| `Pisz` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 | null (no_source_checks) | null (no_eligible_cases) |
| `Raszków` | 1.000 (7/7) [0.65; 1.00] | 0.636 (7/11) [0.35; 0.85] | 1.000 (7/7) [0.65; 1.00] | 0.636 (7/11) [0.35; 0.85] | 1.000 (4/4) [0.51; 1.00] | 0 | 1.000 (22/22) [0.85; 1.00] | 0.636 (7/11) [0.35; 0.85] |
| `Stare Miasto` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `Szczytno` | 1.000 (24/24) [0.86; 1.00] | 0.750 (24/32) [0.58; 0.87] | 0.958 (23/24) [0.80; 0.99] | 0.719 (23/32) [0.55; 0.84] | 1.000 (4/4) [0.51; 1.00] | 0 | 1.000 (24/24) [0.86; 1.00] | 0.719 (23/32) [0.55; 0.84] |
| `Warszawa` | 1.000 (33/33) [0.90; 1.00] | 0.702 (33/47) [0.56; 0.81] | 0.939 (31/33) [0.80; 0.98] | 0.660 (31/47) [0.52; 0.78] | 1.000 (26/26) [0.87; 1.00] | 0 | 0.676 (25/37) [0.51; 0.80] | 0.447 (21/47) [0.31; 0.59] |
| `Łódź` | 1.000 (23/23) [0.86; 1.00] | 1.000 (23/23) [0.86; 1.00] | 0.783 (18/23) [0.58; 0.90] | 0.783 (18/23) [0.58; 0.90] | 1.000 (9/9) [0.70; 1.00] | 0 | 0.925 (37/40) [0.80; 0.97] | 0.652 (15/23) [0.45; 0.81] |

### Wielostrefowe vs jednostrefowe

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `true` | 1.000 (182/182) [0.98; 1.00] | 0.835 (182/218) [0.78; 0.88] | 0.907 (165/182) [0.86; 0.94] | 0.757 (165/218) [0.70; 0.81] | 1.000 (98/98) [0.96; 1.00] | 0 | 0.739 (178/241) [0.68; 0.79] | 0.523 (114/218) [0.46; 0.59] |
| `false` | 1.000 (26/26) [0.87; 1.00] | 0.812 (26/32) [0.65; 0.91] | 0.885 (23/26) [0.71; 0.96] | 0.719 (23/32) [0.55; 0.84] | null (no_eligible_cases) | 0 | 1.000 (31/31) [0.89; 1.00] | 0.719 (23/32) [0.55; 0.84] |

### Według parametru

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `max_building_height_m` | 1.000 (36/36) [0.90; 1.00] | 0.837 (36/43) [0.70; 0.92] | 0.806 (29/36) [0.65; 0.90] | 0.674 (29/43) [0.53; 0.80] | 1.000 (20/20) [0.84; 1.00] | 0 | 0.760 (57/75) [0.65; 0.84] | 0.419 (18/43) [0.28; 0.57] |
| `min_intensity` | 1.000 (27/27) [0.88; 1.00] | 0.931 (27/29) [0.78; 0.98] | 0.963 (26/27) [0.82; 0.99] | 0.897 (26/29) [0.74; 0.96] | 1.000 (5/5) [0.57; 1.00] | 0 | 0.750 (24/32) [0.58; 0.87] | 0.621 (18/29) [0.44; 0.77] |
| `max_intensity` | 1.000 (33/33) [0.90; 1.00] | 0.846 (33/39) [0.70; 0.93] | 0.909 (30/33) [0.76; 0.97] | 0.769 (30/39) [0.62; 0.87] | 1.000 (25/25) [0.87; 1.00] | 0 | 0.737 (28/38) [0.58; 0.85] | 0.513 (20/39) [0.36; 0.66] |
| `max_building_coverage_percent` | 1.000 (30/30) [0.89; 1.00] | 0.882 (30/34) [0.73; 0.95] | 0.967 (29/30) [0.83; 0.99] | 0.853 (29/34) [0.70; 0.94] | 1.000 (16/16) [0.81; 1.00] | 0 | 0.800 (28/35) [0.64; 0.90] | 0.647 (22/34) [0.48; 0.79] |
| `min_biologically_active_percent` | 1.000 (36/36) [0.90; 1.00] | 0.900 (36/40) [0.77; 0.96] | 0.806 (29/36) [0.65; 0.90] | 0.725 (29/40) [0.57; 0.84] | 1.000 (22/22) [0.85; 1.00] | 0 | 0.675 (27/40) [0.52; 0.80] | 0.500 (20/40) [0.35; 0.65] |
| `roof_angle_min_deg` | 1.000 (15/15) [0.80; 1.00] | 0.714 (15/21) [0.50; 0.86] | 1.000 (15/15) [0.80; 1.00] | 0.714 (15/21) [0.50; 0.86] | 1.000 (3/3) [0.44; 1.00] | 0 | 0.824 (14/17) [0.59; 0.94] | 0.571 (12/21) [0.37; 0.76] |
| `roof_angle_max_deg` | 1.000 (19/19) [0.83; 1.00] | 0.704 (19/27) [0.52; 0.84] | 1.000 (19/19) [0.83; 1.00] | 0.704 (19/27) [0.52; 0.84] | 1.000 (7/7) [0.65; 1.00] | 0 | 0.826 (19/23) [0.63; 0.93] | 0.593 (16/27) [0.41; 0.75] |
| `max_storeys` | 1.000 (8/8) [0.68; 1.00] | 0.889 (8/9) [0.57; 0.98] | 0.875 (7/8) [0.53; 0.98] | 0.778 (7/9) [0.45; 0.94] | null (no_eligible_cases) | 0 | 1.000 (8/8) [0.68; 1.00] | 0.778 (7/9) [0.45; 0.94] |
| `setback_m` | 1.000 (4/4) [0.51; 1.00] | 0.500 (4/8) [0.22; 0.78] | 1.000 (4/4) [0.51; 1.00] | 0.500 (4/8) [0.22; 0.78] | null (no_eligible_cases) | 0 | 1.000 (4/4) [0.51; 1.00] | 0.500 (4/8) [0.22; 0.78] |

### Liczniki werdyktów

| Wycinek | `tp_exact` | `tp_partial` | `tp_wrong` | `fn` | `fp` | `tn` | `acceptable_only` | `general_found` | `general_missed` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `overall` | 188 | 20 | 0 | 42 | 0 | 161 | 0 | 12 | 0 |
| `development` | 75 | 14 | 0 | 0 | 0 | 82 | 0 | 0 | 0 |
| `final` | 113 | 6 | 0 | 42 | 0 | 79 | 0 | 12 | 0 |
| `html` | 18 | 5 | 0 | 0 | 0 | 13 | 0 | 0 | 0 |
| `ocr_real` | 2 | 0 | 0 | 0 | 0 | 16 | 0 | 0 | 0 |
| `ocr_simulated` | 7 | 2 | 0 | 14 | 0 | 13 | 0 | 0 | 0 |
| `pdf_table` | 10 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 |
| `pdf_text` | 151 | 13 | 0 | 28 | 0 | 111 | 0 | 12 | 0 |

Odkrywanie symboli bez podpowiedzi (recall symboli z anotacji): 0.234 (11/47) [0.14; 0.37].

## Źródło wartości (`source_consistent`)

Wartość zwrócona przez silnik jest *poprawna co do wartości*, gdy równa się wartości z adnotacji tej strefy. Jest *spójna ze źródłem*, gdy jej strona jest stroną cytatu adnotacji, a miejsce dopasowania leży w bloku strefy (od kotwicy do ostatniego cytatu tej kotwicy plus 300 znaków, nie dalej niż następna kotwica). Poprawna wartość z innej strony lub z bloku innej strefy jest **błędem przypisania, nie trafieniem**. Wartości, których źródła nie da się potwierdzić (`indeterminate`: identyczny tekst także poza blokiem; `unlocatable`: brak strony lub tekstu), nie są liczone jako spójne.

`source_consistent` = 0.768 (209/272) [0.71; 0.81]; przypisanie do strefy z uwzględnieniem źródła = 0.704 (69/98) [0.61; 0.79] (wg samej wartości: 1.000 (98/98) [0.96; 1.00]); dokładność end-to-end ze źródłem = 0.548 (137/250) [0.49; 0.61] (wg samej wartości: 0.752 (188/250) [0.69; 0.80]); pary z poprawną wartością z niewłaściwego źródła: 30.

| Status źródła | wszystkie poprawne wartości | w tym równe wartości wymaganej |
|---|---:|---:|
| `consistent` | 209 | 184 |
| `wrong_page` | 29 | 29 |
| `wrong_block` | 4 | 4 |
| `indeterminate` | 30 | 30 |
| `unlocatable` | 0 | 0 |
| `not_checked` | 0 | 0 |
| razem | 272 | 247 |

Podstawa porównania: `{"block":229,"evidence":33,"page":10}` (`block` = kotwica strefy, `evidence` = tylko cytat adnotacji bez kotwicy, `page` = tylko strona, gdy tekst anotowany nie jest tekstem odczytanym przez silnik, np. skan symulowany).

## Kalibracja confidence

Wartości zwróconych parametrów w strefach z adnotacją: n = 289; Brier = 0.041; ECE = 0.049.

| Przedział confidence | n | błędne | odsetek błędów | 95% CI | średnie confidence |
|---|---:|---:|---:|---|---:|
| `low` | 0 | 0 | n/a | n/a | n/a |
| `medium` | 25 | 12 | 0.480 | [0.30; 0.67] | 0.67 |
| `high` | 264 | 5 | 0.019 | [0.01; 0.04] | 0.94 |

| Wartość confidence | n | błędne | odsetek błędów |
|---:|---:|---:|---:|
| 0.62 | 2 | 1 | 0.500 |
| 0.64 | 2 | 1 | 0.500 |
| 0.67 | 16 | 8 | 0.500 |
| 0.71 | 4 | 2 | 0.500 |
| 0.76 | 1 | 0 | 0.000 |
| 0.85 | 7 | 0 | 0.000 |
| 0.86 | 2 | 1 | 0.500 |
| 0.87 | 1 | 0 | 0.000 |
| 0.88 | 3 | 1 | 0.333 |
| 0.89 | 3 | 0 | 0.000 |
| 0.90 | 9 | 0 | 0.000 |
| 0.91 | 7 | 0 | 0.000 |
| 0.92 | 19 | 0 | 0.000 |
| 0.93 | 15 | 0 | 0.000 |
| 0.94 | 91 | 3 | 0.033 |
| 0.95 | 38 | 0 | 0.000 |
| 0.96 | 24 | 0 | 0.000 |
| 0.97 | 31 | 0 | 0.000 |
| 0.98 | 14 | 0 | 0.000 |

Niskie vs wysokie confidence: co najmniej jeden przedział jest pusty, porównanie niemożliwe.

## Niezawodność pewności (PV3-09)

### Wszystkie wartości

Artefakt kalibracji `mpzp-confidence/1.0+24d198357bc5`; progi pasm: medium ≥ 0.943, high ≥ 0.943; próg `manual_review_required` 0.943. n = 289; Brier = 0.041; **ECE = 0.049** (10 równych przedziałów).

| Przedział pewności | n | błędne | średnia pewność | trafność |
|---|---:|---:|---:|---:|
| [0.6; 0.7) | 20 | 10 | 0.661 | 0.500 |
| [0.7; 0.8) | 5 | 2 | 0.724 | 0.600 |
| [0.8; 0.9) | 25 | 2 | 0.878 | 0.920 |
| [0.9; 1.0) | 239 | 3 | 0.949 | 0.987 |

| Pasmo | n | błędne | odsetek błędów |
|---|---:|---:|---:|
| `low` | 121 | 17 | 0.140 |
| `medium` | 0 | 0 | n/a |
| `high` | 168 | 0 | 0.000 |

Pasma monotoniczne (odsetek błędów nie rośnie od `low` do `high`): **tak**. Bez ręcznej weryfikacji: 162 wartości, odsetek błędów 0.000; z flagą ręcznej weryfikacji: 127 wartości, odsetek błędów 0.134; błędy wychwycone flagą: 1.000 (17/17) [0.82; 1.00].

### Podział `development`

Artefakt kalibracji `mpzp-confidence/1.0+24d198357bc5`; progi pasm: medium ≥ 0.943, high ≥ 0.943; próg `manual_review_required` 0.943. n = 125; Brier = 0.069; **ECE = 0.045** (10 równych przedziałów).

| Przedział pewności | n | błędne | średnia pewność | trafność |
|---|---:|---:|---:|---:|
| [0.6; 0.7) | 16 | 8 | 0.669 | 0.500 |
| [0.7; 0.8) | 4 | 2 | 0.713 | 0.500 |
| [0.8; 0.9) | 5 | 0 | 0.898 | 1.000 |
| [0.9; 1.0) | 100 | 3 | 0.954 | 0.970 |

| Pasmo | n | błędne | odsetek błędów |
|---|---:|---:|---:|
| `low` | 44 | 13 | 0.295 |
| `medium` | 0 | 0 | n/a |
| `high` | 81 | 0 | 0.000 |

Pasma monotoniczne (odsetek błędów nie rośnie od `low` do `high`): **tak**. Bez ręcznej weryfikacji: 81 wartości, odsetek błędów 0.000; z flagą ręcznej weryfikacji: 44 wartości, odsetek błędów 0.295; błędy wychwycone flagą: 1.000 (13/13) [0.77; 1.00].

### Podział `final`

Artefakt kalibracji `mpzp-confidence/1.0+24d198357bc5`; progi pasm: medium ≥ 0.943, high ≥ 0.943; próg `manual_review_required` 0.943. n = 164; Brier = 0.021; **ECE = 0.055** (10 równych przedziałów).

| Przedział pewności | n | błędne | średnia pewność | trafność |
|---|---:|---:|---:|---:|
| [0.6; 0.7) | 4 | 2 | 0.631 | 0.500 |
| [0.7; 0.8) | 1 | 0 | 0.765 | 1.000 |
| [0.8; 0.9) | 20 | 2 | 0.873 | 0.900 |
| [0.9; 1.0) | 139 | 0 | 0.945 | 1.000 |

| Pasmo | n | błędne | odsetek błędów |
|---|---:|---:|---:|
| `low` | 77 | 4 | 0.052 |
| `medium` | 0 | 0 | n/a |
| `high` | 87 | 0 | 0.000 |

Pasma monotoniczne (odsetek błędów nie rośnie od `low` do `high`): **tak**. Bez ręcznej weryfikacji: 81 wartości, odsetek błędów 0.000; z flagą ręcznej weryfikacji: 83 wartości, odsetek błędów 0.048; błędy wychwycone flagą: 1.000 (4/4) [0.51; 1.00].


Flaga `manual_review_required`: oznaczono 127 wartości; błędnych wśród oznaczonych: 0.134 (17/127) [0.09; 0.20]; odsetek błędów wychwyconych flagą: 1.000 (17/17) [0.82; 1.00].

Kalibracje dla wycinków (split, format) są w `metrics.json`; małe n oznacza szerokie przedziały.

## Błędy

Wpisów w śladzie błędów: 89 (`errors.json`, `errors.csv`). Każdy ma dokument, SHA-256, stronę anotacji, wynik parsera i wersję parsera.

| Typ | liczba |
|---|---:|
| `detection_fn` | 42 |
| `value_error` | 17 |
| `wrong_source` | 30 |

Rozdzielenie błędów: **rozpoznanie** (`detection_fn`, `detection_fp`, `zone_not_found`: czy wartość została znaleziona), **normalizacja/wartość** (`value_error`: wartość znaleziona, ale inna niż w adnotacji lub z dodatkową wartością) i **przypisanie** (`zone_assignment`: wartość innej strefy).

| Kategoria | pdf_text | pdf_table | html | ocr_real | ocr_simulated | razem |
|---|---:|---:|---:|---:|---:|---:|
| `recognition` | 28 | 0 | 0 | 0 | 14 | 42 |
| `normalization_or_value` | 10 | 0 | 5 | 0 | 2 | 17 |
| `assignment` | 27 | 0 | 3 | 0 | 0 | 30 |

Wskazówki przyczyn (wyprowadzone z oznaczeń niejednoznaczności w adnotacji; nie są dowodem przyczyny w kodzie parsera):

| Wskazówka | liczba |
|---|---:|
| `value_from_other_source` | 30 |
| `wording_not_matched` | 20 |
| `value_from_other_context` | 10 |
| `conditional_value` | 10 |
| `ocr_noise_or_wording` | 9 |
| `implicit_percent` | 3 |
| `residual_clause` | 2 |
| `extraction_artifact` | 2 |
| `building_line_not_setback_phrase` | 2 |
| `number_word` | 1 |

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
| czas ścienny na próbkę p50 / p95 [ms] | 17.1 / 52.2 |
| opóźnienie modelu p50 / p95 [ms] | — / — |
| koszt łącznie [USD] | — |
| koszt na dokument / strefę [USD] | — / — |
