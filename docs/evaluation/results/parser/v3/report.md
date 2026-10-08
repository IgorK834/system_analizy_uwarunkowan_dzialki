# Ewaluacja silnika `v3` parsera MPZP (BK-603, PV3-03)

Plik jest generowany z `parameter_results.json`; nie jest edytowany ręcznie.

## Zamrożony manifest

| Pole | Wartość |
|---|---|
| `engine` / `engine_version` | `v3` / `mpzp-parser/3.0-det+scope.1` |
| `run_mode` | `offline` |
| `commit_sha` | `644e39a249d44beaead0bc30925bc02aac187ce7` |
| `manifest_sha256` | `b1e46622b6f12f9aa0f0609bb7804430ec4135736a9f58cf9ff03d94b5018835` |
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
| `overall` | 1.000 (248/248) [0.98; 1.00] | 0.992 (248/250) [0.97; 1.00] | 0.940 (233/248) [0.90; 0.96] | 0.932 (233/250) [0.89; 0.96] | 1.000 (118/118) [0.97; 1.00] | 0 | 0.975 (355/364) [0.95; 0.99] | 0.896 (224/250) [0.85; 0.93] |

### Według podziału (rozwojowy / końcowy)

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `development` | 1.000 (89/89) [0.96; 1.00] | 1.000 (89/89) [0.96; 1.00] | 0.966 (86/89) [0.91; 0.99] | 0.966 (86/89) [0.91; 0.99] | 1.000 (44/44) [0.92; 1.00] | 0 | 0.973 (110/113) [0.92; 0.99] | 0.933 (83/89) [0.86; 0.97] |
| `final` | 1.000 (159/159) [0.98; 1.00] | 0.988 (159/161) [0.96; 1.00] | 0.925 (147/159) [0.87; 0.96] | 0.913 (147/161) [0.86; 0.95] | 1.000 (74/74) [0.95; 1.00] | 0 | 0.976 (245/251) [0.95; 0.99] | 0.876 (141/161) [0.82; 0.92] |

### Według formatu

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `html` | 1.000 (23/23) [0.86; 1.00] | 1.000 (23/23) [0.86; 1.00] | 0.870 (20/23) [0.68; 0.95] | 0.870 (20/23) [0.68; 0.95] | 1.000 (9/9) [0.70; 1.00] | 0 | 0.925 (37/40) [0.80; 0.97] | 0.739 (17/23) [0.54; 0.87] |
| `ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `ocr_simulated` | 1.000 (21/21) [0.85; 1.00] | 0.913 (21/23) [0.73; 0.98] | 0.429 (9/21) [0.24; 0.63] | 0.391 (9/23) [0.22; 0.59] | 1.000 (12/12) [0.76; 1.00] | 0 | 1.000 (32/32) [0.89; 1.00] | 0.391 (9/23) [0.22; 0.59] |
| `pdf_table` | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | null (no_eligible_cases) | 0 | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] |
| `pdf_text` | 1.000 (192/192) [0.98; 1.00] | 1.000 (192/192) [0.98; 1.00] | 1.000 (192/192) [0.98; 1.00] | 1.000 (192/192) [0.98; 1.00] | 1.000 (97/97) [0.96; 1.00] | 0 | 0.979 (274/280) [0.95; 0.99] | 0.969 (186/192) [0.93; 0.99] |

### Podział × format

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `development/html` | 1.000 (18/18) [0.82; 1.00] | 1.000 (18/18) [0.82; 1.00] | 0.833 (15/18) [0.61; 0.94] | 0.833 (15/18) [0.61; 0.94] | 1.000 (9/9) [0.70; 1.00] | 0 | 0.909 (30/33) [0.76; 0.97] | 0.667 (12/18) [0.44; 0.84] |
| `development/ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `development/pdf_table` | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | null (no_eligible_cases) | 0 | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] |
| `development/pdf_text` | 1.000 (59/59) [0.94; 1.00] | 1.000 (59/59) [0.94; 1.00] | 1.000 (59/59) [0.94; 1.00] | 1.000 (59/59) [0.94; 1.00] | 1.000 (35/35) [0.90; 1.00] | 0 | 1.000 (68/68) [0.95; 1.00] | 1.000 (59/59) [0.94; 1.00] |
| `final/html` | 1.000 (5/5) [0.57; 1.00] | 1.000 (5/5) [0.57; 1.00] | 1.000 (5/5) [0.57; 1.00] | 1.000 (5/5) [0.57; 1.00] | null (no_eligible_cases) | 0 | 1.000 (7/7) [0.65; 1.00] | 1.000 (5/5) [0.57; 1.00] |
| `final/ocr_real` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 | null (no_source_checks) | null (no_eligible_cases) |
| `final/ocr_simulated` | 1.000 (21/21) [0.85; 1.00] | 0.913 (21/23) [0.73; 0.98] | 0.429 (9/21) [0.24; 0.63] | 0.391 (9/23) [0.22; 0.59] | 1.000 (12/12) [0.76; 1.00] | 0 | 1.000 (32/32) [0.89; 1.00] | 0.391 (9/23) [0.22; 0.59] |
| `final/pdf_text` | 1.000 (133/133) [0.97; 1.00] | 1.000 (133/133) [0.97; 1.00] | 1.000 (133/133) [0.97; 1.00] | 1.000 (133/133) [0.97; 1.00] | 1.000 (62/62) [0.94; 1.00] | 0 | 0.972 (206/212) [0.94; 0.99] | 0.955 (127/133) [0.91; 0.98] |

### Według gminy

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `Białystok` | 1.000 (14/14) [0.78; 1.00] | 1.000 (14/14) [0.78; 1.00] | 1.000 (14/14) [0.78; 1.00] | 1.000 (14/14) [0.78; 1.00] | 1.000 (8/8) [0.68; 1.00] | 0 | 1.000 (29/29) [0.88; 1.00] | 1.000 (14/14) [0.78; 1.00] |
| `Bielsko-Biała` | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (15/15) [0.80; 1.00] | 1.000 (13/13) [0.77; 1.00] | 0 | 1.000 (18/18) [0.82; 1.00] | 1.000 (15/15) [0.80; 1.00] |
| `Kraków` | 1.000 (44/44) [0.92; 1.00] | 1.000 (44/44) [0.92; 1.00] | 1.000 (44/44) [0.92; 1.00] | 1.000 (44/44) [0.92; 1.00] | 1.000 (22/22) [0.85; 1.00] | 0 | 1.000 (50/50) [0.93; 1.00] | 1.000 (44/44) [0.92; 1.00] |
| `Krzemieniewo` | 1.000 (9/9) [0.70; 1.00] | 1.000 (9/9) [0.70; 1.00] | 1.000 (9/9) [0.70; 1.00] | 1.000 (9/9) [0.70; 1.00] | null (no_eligible_cases) | 0 | 1.000 (34/34) [0.90; 1.00] | 1.000 (9/9) [0.70; 1.00] |
| `Legnica` | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] | null (no_eligible_cases) | 0 | 1.000 (10/10) [0.72; 1.00] | 1.000 (10/10) [0.72; 1.00] |
| `Mogilany` | 1.000 (8/8) [0.68; 1.00] | 0.800 (8/10) [0.49; 0.94] | 1.000 (8/8) [0.68; 1.00] | 0.800 (8/10) [0.49; 0.94] | null (no_eligible_cases) | 0 | 1.000 (11/11) [0.74; 1.00] | 0.800 (8/10) [0.49; 0.94] |
| `Ostrów Wielkopolski` | 1.000 (33/33) [0.90; 1.00] | 1.000 (33/33) [0.90; 1.00] | 1.000 (33/33) [0.90; 1.00] | 1.000 (33/33) [0.90; 1.00] | 1.000 (16/16) [0.81; 1.00] | 0 | 1.000 (39/39) [0.91; 1.00] | 1.000 (33/33) [0.90; 1.00] |
| `Pisz` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 | null (no_source_checks) | null (no_eligible_cases) |
| `Raszków` | 1.000 (11/11) [0.74; 1.00] | 1.000 (11/11) [0.74; 1.00] | 1.000 (11/11) [0.74; 1.00] | 1.000 (11/11) [0.74; 1.00] | 1.000 (8/8) [0.68; 1.00] | 0 | 1.000 (26/26) [0.87; 1.00] | 1.000 (11/11) [0.74; 1.00] |
| `Stare Miasto` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] |
| `Szczytno` | 1.000 (32/32) [0.89; 1.00] | 1.000 (32/32) [0.89; 1.00] | 1.000 (32/32) [0.89; 1.00] | 1.000 (32/32) [0.89; 1.00] | 1.000 (8/8) [0.68; 1.00] | 0 | 0.864 (38/44) [0.73; 0.94] | 0.812 (26/32) [0.65; 0.91] |
| `Warszawa` | 1.000 (47/47) [0.92; 1.00] | 1.000 (47/47) [0.92; 1.00] | 0.745 (35/47) [0.60; 0.85] | 0.745 (35/47) [0.60; 0.85] | 1.000 (34/34) [0.90; 1.00] | 0 | 1.000 (61/61) [0.94; 1.00] | 0.745 (35/47) [0.60; 0.85] |
| `Łódź` | 1.000 (23/23) [0.86; 1.00] | 1.000 (23/23) [0.86; 1.00] | 0.870 (20/23) [0.68; 0.95] | 0.870 (20/23) [0.68; 0.95] | 1.000 (9/9) [0.70; 1.00] | 0 | 0.925 (37/40) [0.80; 0.97] | 0.739 (17/23) [0.54; 0.87] |

### Wielostrefowe vs jednostrefowe

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `true` | 1.000 (218/218) [0.98; 1.00] | 1.000 (218/218) [0.98; 1.00] | 0.931 (203/218) [0.89; 0.96] | 0.931 (203/218) [0.89; 0.96] | 1.000 (118/118) [0.97; 1.00] | 0 | 0.970 (292/301) [0.94; 0.98] | 0.890 (194/218) [0.84; 0.92] |
| `false` | 1.000 (30/30) [0.89; 1.00] | 0.938 (30/32) [0.80; 0.98] | 1.000 (30/30) [0.89; 1.00] | 0.938 (30/32) [0.80; 0.98] | null (no_eligible_cases) | 0 | 1.000 (63/63) [0.94; 1.00] | 0.938 (30/32) [0.80; 0.98] |

### Według parametru

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy (wg wartości) | Pomyłki stref | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|---:|---|---|
| `max_building_height_m` | 1.000 (43/43) [0.92; 1.00] | 1.000 (43/43) [0.92; 1.00] | 0.930 (40/43) [0.81; 0.98] | 0.930 (40/43) [0.81; 0.98] | 1.000 (24/24) [0.86; 1.00] | 0 | 0.949 (94/99) [0.89; 0.98] | 0.814 (35/43) [0.67; 0.90] |
| `min_intensity` | 1.000 (29/29) [0.88; 1.00] | 1.000 (29/29) [0.88; 1.00] | 1.000 (29/29) [0.88; 1.00] | 1.000 (29/29) [0.88; 1.00] | 1.000 (6/6) [0.61; 1.00] | 0 | 1.000 (34/34) [0.90; 1.00] | 1.000 (29/29) [0.88; 1.00] |
| `max_intensity` | 1.000 (38/38) [0.91; 1.00] | 0.974 (38/39) [0.87; 1.00] | 0.921 (35/38) [0.79; 0.97] | 0.897 (35/39) [0.76; 0.96] | 1.000 (29/29) [0.88; 1.00] | 0 | 1.000 (45/45) [0.92; 1.00] | 0.897 (35/39) [0.76; 0.96] |
| `max_building_coverage_percent` | 1.000 (34/34) [0.90; 1.00] | 1.000 (34/34) [0.90; 1.00] | 0.912 (31/34) [0.77; 0.97] | 0.912 (31/34) [0.77; 0.97] | 1.000 (20/20) [0.84; 1.00] | 0 | 1.000 (39/39) [0.91; 1.00] | 0.912 (31/34) [0.77; 0.97] |
| `min_biologically_active_percent` | 1.000 (40/40) [0.91; 1.00] | 1.000 (40/40) [0.91; 1.00] | 0.850 (34/40) [0.71; 0.93] | 0.850 (34/40) [0.71; 0.93] | 1.000 (26/26) [0.87; 1.00] | 0 | 1.000 (44/44) [0.92; 1.00] | 0.850 (34/40) [0.71; 0.93] |
| `roof_angle_min_deg` | 1.000 (21/21) [0.85; 1.00] | 1.000 (21/21) [0.85; 1.00] | 1.000 (21/21) [0.85; 1.00] | 1.000 (21/21) [0.85; 1.00] | 1.000 (4/4) [0.51; 1.00] | 0 | 0.943 (33/35) [0.81; 0.98] | 0.905 (19/21) [0.71; 0.97] |
| `roof_angle_max_deg` | 1.000 (27/27) [0.88; 1.00] | 1.000 (27/27) [0.88; 1.00] | 1.000 (27/27) [0.88; 1.00] | 1.000 (27/27) [0.88; 1.00] | 1.000 (9/9) [0.70; 1.00] | 0 | 0.958 (46/48) [0.86; 0.99] | 0.926 (25/27) [0.77; 0.98] |
| `max_storeys` | 1.000 (9/9) [0.70; 1.00] | 1.000 (9/9) [0.70; 1.00] | 1.000 (9/9) [0.70; 1.00] | 1.000 (9/9) [0.70; 1.00] | null (no_eligible_cases) | 0 | 1.000 (13/13) [0.77; 1.00] | 1.000 (9/9) [0.70; 1.00] |
| `setback_m` | 1.000 (7/7) [0.65; 1.00] | 0.875 (7/8) [0.53; 0.98] | 1.000 (7/7) [0.65; 1.00] | 0.875 (7/8) [0.53; 0.98] | null (no_eligible_cases) | 0 | 1.000 (7/7) [0.65; 1.00] | 0.875 (7/8) [0.53; 0.98] |

### Liczniki werdyktów

| Wycinek | `tp_exact` | `tp_partial` | `tp_wrong` | `fn` | `fp` | `tn` | `acceptable_only` | `general_found` | `general_missed` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `overall` | 233 | 15 | 0 | 2 | 0 | 161 | 0 | 12 | 0 |
| `development` | 86 | 3 | 0 | 0 | 0 | 82 | 0 | 0 | 0 |
| `final` | 147 | 12 | 0 | 2 | 0 | 79 | 0 | 12 | 0 |
| `html` | 20 | 3 | 0 | 0 | 0 | 13 | 0 | 0 | 0 |
| `ocr_real` | 2 | 0 | 0 | 0 | 0 | 16 | 0 | 0 | 0 |
| `ocr_simulated` | 9 | 12 | 0 | 2 | 0 | 13 | 0 | 0 | 0 |
| `pdf_table` | 10 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 |
| `pdf_text` | 192 | 0 | 0 | 0 | 0 | 111 | 0 | 12 | 0 |

Odkrywanie symboli bez podpowiedzi (recall symboli z anotacji): 0.234 (11/47) [0.14; 0.37].

## Źródło wartości (`source_consistent`)

Wartość zwrócona przez silnik jest *poprawna co do wartości*, gdy równa się wartości z adnotacji tej strefy. Jest *spójna ze źródłem*, gdy jej strona jest stroną cytatu adnotacji, a miejsce dopasowania leży w bloku strefy (od kotwicy do ostatniego cytatu tej kotwicy plus 300 znaków, nie dalej niż następna kotwica). Poprawna wartość z innej strony lub z bloku innej strefy jest **błędem przypisania, nie trafieniem**. Wartości, których źródła nie da się potwierdzić (`indeterminate`: identyczny tekst także poza blokiem; `unlocatable`: brak strony lub tekstu), nie są liczone jako spójne.

`source_consistent` = 0.975 (355/364) [0.95; 0.99]; przypisanie do strefy z uwzględnieniem źródła = 1.000 (118/118) [0.97; 1.00] (wg samej wartości: 1.000 (118/118) [0.97; 1.00]); dokładność end-to-end ze źródłem = 0.896 (224/250) [0.85; 0.93] (wg samej wartości: 0.932 (233/250) [0.89; 0.96]); pary z poprawną wartością z niewłaściwego źródła: 9.

| Status źródła | wszystkie poprawne wartości | w tym równe wartości wymaganej |
|---|---:|---:|
| `consistent` | 355 | 325 |
| `wrong_page` | 0 | 0 |
| `wrong_block` | 9 | 9 |
| `indeterminate` | 0 | 0 |
| `unlocatable` | 0 | 0 |
| `not_checked` | 0 | 0 |
| razem | 364 | 334 |

Podstawa porównania: `{"block":299,"evidence":33,"page":32}` (`block` = kotwica strefy, `evidence` = tylko cytat adnotacji bez kotwicy, `page` = tylko strona, gdy tekst anotowany nie jest tekstem odczytanym przez silnik, np. skan symulowany).

## Kalibracja confidence

Wartości zwróconych parametrów w strefach z adnotacją: n = 386; Brier = 0.037; ECE = 0.025.

| Przedział confidence | n | błędne | odsetek błędów | 95% CI | średnie confidence |
|---|---:|---:|---:|---|---:|
| `low` | 6 | 3 | 0.500 | [0.19; 0.81] | 0.32 |
| `medium` | 46 | 16 | 0.348 | [0.23; 0.49] | 0.67 |
| `high` | 334 | 3 | 0.009 | [0.00; 0.03] | 0.97 |

| Wartość confidence | n | błędne | odsetek błędów |
|---:|---:|---:|---:|
| 0.32 | 6 | 3 | 0.500 |
| 0.60 | 6 | 3 | 0.500 |
| 0.62 | 9 | 6 | 0.667 |
| 0.67 | 15 | 7 | 0.467 |
| 0.68 | 1 | 0 | 0.000 |
| 0.71 | 12 | 0 | 0.000 |
| 0.73 | 2 | 0 | 0.000 |
| 0.79 | 1 | 0 | 0.000 |
| 0.81 | 1 | 0 | 0.000 |
| 0.88 | 9 | 0 | 0.000 |
| 0.90 | 2 | 0 | 0.000 |
| 0.91 | 6 | 0 | 0.000 |
| 0.93 | 6 | 3 | 0.500 |
| 0.94 | 5 | 0 | 0.000 |
| 0.95 | 26 | 0 | 0.000 |
| 0.96 | 41 | 0 | 0.000 |
| 0.97 | 48 | 0 | 0.000 |
| 0.98 | 186 | 0 | 0.000 |
| 0.99 | 4 | 0 | 0.000 |

Niskie vs wysokie confidence: odsetek błędów 0.500 vs 0.009; niskie częściej błędne: **tak**; test Fishera (jednostronny) p = 0.0001.

## Niezawodność pewności (PV3-09)

### Wszystkie wartości

Artefakt kalibracji `mpzp-confidence/1.0+24d198357bc5`; progi pasm: medium ≥ 0.943, high ≥ 0.943; próg `manual_review_required` 0.943. n = 386; Brier = 0.037; **ECE = 0.046** (10 równych przedziałów).

| Przedział pewności | n | błędne | średnia pewność | trafność |
|---|---:|---:|---:|---:|
| [0.3; 0.4) | 6 | 3 | 0.317 | 0.500 |
| [0.6; 0.7) | 31 | 16 | 0.642 | 0.484 |
| [0.7; 0.8) | 15 | 0 | 0.720 | 1.000 |
| [0.8; 0.9) | 12 | 0 | 0.880 | 1.000 |
| [0.9; 1.0) | 322 | 3 | 0.971 | 0.991 |

| Pasmo | n | błędne | odsetek błędów |
|---|---:|---:|---:|
| `low` | 80 | 22 | 0.275 |
| `medium` | 0 | 0 | n/a |
| `high` | 306 | 0 | 0.000 |

Pasma monotoniczne (odsetek błędów nie rośnie od `low` do `high`): **tak**. Bez ręcznej weryfikacji: 280 wartości, odsetek błędów 0.000; z flagą ręcznej weryfikacji: 106 wartości, odsetek błędów 0.208; błędy wychwycone flagą: 1.000 (22/22) [0.85; 1.00].

### Podział `development`

Artefakt kalibracji `mpzp-confidence/1.0+24d198357bc5`; progi pasm: medium ≥ 0.943, high ≥ 0.943; próg `manual_review_required` 0.943. n = 116; Brier = 0.025; **ECE = 0.013** (10 równych przedziałów).

| Przedział pewności | n | błędne | średnia pewność | trafność |
|---|---:|---:|---:|---:|
| [0.7; 0.8) | 1 | 0 | 0.790 | 1.000 |
| [0.8; 0.9) | 10 | 0 | 0.876 | 1.000 |
| [0.9; 1.0) | 105 | 3 | 0.972 | 0.971 |

| Pasmo | n | błędne | odsetek błędów |
|---|---:|---:|---:|
| `low` | 20 | 3 | 0.150 |
| `medium` | 0 | 0 | n/a |
| `high` | 96 | 0 | 0.000 |

Pasma monotoniczne (odsetek błędów nie rośnie od `low` do `high`): **tak**. Bez ręcznej weryfikacji: 87 wartości, odsetek błędów 0.000; z flagą ręcznej weryfikacji: 29 wartości, odsetek błędów 0.103; błędy wychwycone flagą: 1.000 (3/3) [0.44; 1.00].

### Podział `final`

Artefakt kalibracji `mpzp-confidence/1.0+24d198357bc5`; progi pasm: medium ≥ 0.943, high ≥ 0.943; próg `manual_review_required` 0.943. n = 270; Brier = 0.043; **ECE = 0.061** (10 równych przedziałów).

| Przedział pewności | n | błędne | średnia pewność | trafność |
|---|---:|---:|---:|---:|
| [0.3; 0.4) | 6 | 3 | 0.317 | 0.500 |
| [0.6; 0.7) | 31 | 16 | 0.642 | 0.484 |
| [0.7; 0.8) | 14 | 0 | 0.716 | 1.000 |
| [0.8; 0.9) | 2 | 0 | 0.897 | 1.000 |
| [0.9; 1.0) | 217 | 0 | 0.971 | 1.000 |

| Pasmo | n | błędne | odsetek błędów |
|---|---:|---:|---:|
| `low` | 60 | 19 | 0.317 |
| `medium` | 0 | 0 | n/a |
| `high` | 210 | 0 | 0.000 |

Pasma monotoniczne (odsetek błędów nie rośnie od `low` do `high`): **tak**. Bez ręcznej weryfikacji: 193 wartości, odsetek błędów 0.000; z flagą ręcznej weryfikacji: 77 wartości, odsetek błędów 0.247; błędy wychwycone flagą: 1.000 (19/19) [0.83; 1.00].


Flaga `manual_review_required`: oznaczono 106 wartości; błędnych wśród oznaczonych: 0.208 (22/106) [0.14; 0.29]; odsetek błędów wychwyconych flagą: 1.000 (22/22) [0.85; 1.00].

Kalibracje dla wycinków (split, format) są w `metrics.json`; małe n oznacza szerokie przedziały.

## Błędy

Wpisów w śladzie błędów: 26 (`errors.json`, `errors.csv`). Każdy ma dokument, SHA-256, stronę anotacji, wynik parsera i wersję parsera.

| Typ | liczba |
|---|---:|
| `detection_fn` | 2 |
| `value_error` | 15 |
| `wrong_source` | 9 |

Rozdzielenie błędów: **rozpoznanie** (`detection_fn`, `detection_fp`, `zone_not_found`: czy wartość została znaleziona), **normalizacja/wartość** (`value_error`: wartość znaleziona, ale inna niż w adnotacji lub z dodatkową wartością) i **przypisanie** (`zone_assignment`: wartość innej strefy).

| Kategoria | pdf_text | pdf_table | html | ocr_real | ocr_simulated | razem |
|---|---:|---:|---:|---:|---:|---:|
| `recognition` | 0 | 0 | 0 | 0 | 2 | 2 |
| `normalization_or_value` | 0 | 0 | 3 | 0 | 12 | 15 |
| `assignment` | 6 | 0 | 3 | 0 | 0 | 9 |

Wskazówki przyczyn (wyprowadzone z oznaczeń niejednoznaczności w adnotacji; nie są dowodem przyczyny w kodzie parsera):

| Wskazówka | liczba |
|---|---:|
| `value_from_other_source` | 9 |
| `value_from_other_context` | 7 |
| `implicit_percent` | 3 |
| `conditional_value` | 3 |
| `residual_clause` | 2 |
| `ocr_noise_or_wording` | 1 |
| `building_line_not_setback_phrase` | 1 |

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
| czas ścienny na próbkę p50 / p95 [ms] | 71.9 / 117.6 |
| opóźnienie modelu p50 / p95 [ms] | — / — |
| koszt łącznie [USD] | — |
| koszt na dokument / strefę [USD] | — / — |
