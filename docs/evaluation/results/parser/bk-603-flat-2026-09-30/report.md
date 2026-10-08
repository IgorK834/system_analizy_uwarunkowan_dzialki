# Ewaluacja parsera MPZP (BK-603)

Plik jest generowany z `parameter_results.json`; nie jest edytowany ręcznie.

## Zamrożony manifest

| Pole | Wartość |
|---|---|
| `commit_sha` | `cbefc0a22ba65bbcdfd205132ebd8070c34120db` |
| `manifest_sha256` | `95e0b2cbca5f03809881a1b13c34ca6b44e63d9ed91f78d0584dc3ea809693f5` |
| `corpus_sha256` | `d32dd895374bdef98e65ba01a9eb890243124f708a686ad9e64db9b460ccd186` |
| `annotations_sha256` | `19f1faf273e4a6d1f063c518cf84f9fde6ef4014975860e73db0c9a3046751ff` |
| `parser_version` | `mpzp-parser/2.0` |
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

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy | Pomyłki stref |
|---|---|---|---|---|---|---:|
| `overall` | 1.000 (62/62) [0.94; 1.00] | 0.248 (62/250) [0.20; 0.31] | 0.806 (50/62) [0.69; 0.89] | 0.200 (50/250) [0.16; 0.25] | 1.000 (37/37) [0.91; 1.00] | 0 |

### Według podziału (rozwojowy / końcowy)

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy | Pomyłki stref |
|---|---|---|---|---|---|---:|
| `development` | 1.000 (30/30) [0.89; 1.00] | 0.337 (30/89) [0.25; 0.44] | 0.767 (23/30) [0.59; 0.88] | 0.258 (23/89) [0.18; 0.36] | 1.000 (19/19) [0.83; 1.00] | 0 |
| `final` | 1.000 (32/32) [0.89; 1.00] | 0.199 (32/161) [0.14; 0.27] | 0.844 (27/32) [0.68; 0.93] | 0.168 (27/161) [0.12; 0.23] | 1.000 (18/18) [0.82; 1.00] | 0 |

### Według formatu

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy | Pomyłki stref |
|---|---|---|---|---|---|---:|
| `html` | 1.000 (5/5) [0.57; 1.00] | 0.217 (5/23) [0.10; 0.42] | 0.200 (1/5) [0.04; 0.62] | 0.043 (1/23) [0.01; 0.21] | null (no_eligible_cases) | 0 |
| `ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 |
| `ocr_simulated` | 1.000 (2/2) [0.34; 1.00] | 0.087 (2/23) [0.02; 0.27] | 0.500 (1/2) [0.09; 0.91] | 0.043 (1/23) [0.01; 0.21] | 1.000 (2/2) [0.34; 1.00] | 0 |
| `pdf_table` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 |
| `pdf_text` | 1.000 (53/53) [0.93; 1.00] | 0.276 (53/192) [0.22; 0.34] | 0.868 (46/53) [0.75; 0.93] | 0.240 (46/192) [0.18; 0.30] | 1.000 (35/35) [0.90; 1.00] | 0 |

### Podział × format

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy | Pomyłki stref |
|---|---|---|---|---|---|---:|
| `development/html` | 1.000 (3/3) [0.44; 1.00] | 0.167 (3/18) [0.06; 0.39] | 0.000 (0/3) [0.00; 0.56] | 0.000 (0/18) [0.00; 0.18] | null (no_eligible_cases) | 0 |
| `development/ocr_real` | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | 1.000 (2/2) [0.34; 1.00] | null (no_eligible_cases) | 0 |
| `development/pdf_table` | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0.000 (0/10) [0.00; 0.28] | null (no_eligible_cases) | 0 |
| `development/pdf_text` | 1.000 (25/25) [0.87; 1.00] | 0.424 (25/59) [0.31; 0.55] | 0.840 (21/25) [0.65; 0.94] | 0.356 (21/59) [0.25; 0.48] | 1.000 (19/19) [0.83; 1.00] | 0 |
| `final/html` | 1.000 (2/2) [0.34; 1.00] | 0.400 (2/5) [0.12; 0.77] | 0.500 (1/2) [0.09; 0.91] | 0.200 (1/5) [0.04; 0.62] | null (no_eligible_cases) | 0 |
| `final/ocr_real` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | null (no_eligible_cases) | 0 |
| `final/ocr_simulated` | 1.000 (2/2) [0.34; 1.00] | 0.087 (2/23) [0.02; 0.27] | 0.500 (1/2) [0.09; 0.91] | 0.043 (1/23) [0.01; 0.21] | 1.000 (2/2) [0.34; 1.00] | 0 |
| `final/pdf_text` | 1.000 (28/28) [0.88; 1.00] | 0.211 (28/133) [0.15; 0.29] | 0.893 (25/28) [0.73; 0.96] | 0.188 (25/133) [0.13; 0.26] | 1.000 (16/16) [0.81; 1.00] | 0 |

### Wielostrefowe vs jednostrefowe

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy | Pomyłki stref |
|---|---|---|---|---|---|---:|
| `true` | 1.000 (55/55) [0.93; 1.00] | 0.252 (55/218) [0.20; 0.31] | 0.800 (44/55) [0.68; 0.88] | 0.202 (44/218) [0.15; 0.26] | 1.000 (37/37) [0.91; 1.00] | 0 |
| `false` | 1.000 (7/7) [0.65; 1.00] | 0.219 (7/32) [0.11; 0.39] | 0.857 (6/7) [0.49; 0.97] | 0.188 (6/32) [0.09; 0.35] | null (no_eligible_cases) | 0 |

### Według parametru

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Dokładność end-to-end | Przypisanie do strefy | Pomyłki stref |
|---|---|---|---|---|---|---:|
| `max_building_height_m` | 1.000 (23/23) [0.86; 1.00] | 0.535 (23/43) [0.39; 0.67] | 0.565 (13/23) [0.37; 0.74] | 0.302 (13/43) [0.19; 0.45] | 1.000 (14/14) [0.78; 1.00] | 0 |
| `min_intensity` | 1.000 (2/2) [0.34; 1.00] | 0.069 (2/29) [0.02; 0.22] | 1.000 (2/2) [0.34; 1.00] | 0.069 (2/29) [0.02; 0.22] | null (no_eligible_cases) | 0 |
| `max_intensity` | 1.000 (2/2) [0.34; 1.00] | 0.051 (2/39) [0.01; 0.17] | 1.000 (2/2) [0.34; 1.00] | 0.051 (2/39) [0.01; 0.17] | 1.000 (2/2) [0.34; 1.00] | 0 |
| `max_building_coverage_percent` | 1.000 (3/3) [0.44; 1.00] | 0.088 (3/34) [0.03; 0.23] | 1.000 (3/3) [0.44; 1.00] | 0.088 (3/34) [0.03; 0.23] | 1.000 (2/2) [0.34; 1.00] | 0 |
| `min_biologically_active_percent` | 1.000 (19/19) [0.83; 1.00] | 0.475 (19/40) [0.33; 0.63] | 0.947 (18/19) [0.75; 0.99] | 0.450 (18/40) [0.31; 0.60] | 1.000 (15/15) [0.80; 1.00] | 0 |
| `roof_angle_min_deg` | 1.000 (3/3) [0.44; 1.00] | 0.143 (3/21) [0.05; 0.35] | 1.000 (3/3) [0.44; 1.00] | 0.143 (3/21) [0.05; 0.35] | 1.000 (2/2) [0.34; 1.00] | 0 |
| `roof_angle_max_deg` | 1.000 (3/3) [0.44; 1.00] | 0.111 (3/27) [0.04; 0.28] | 0.667 (2/3) [0.21; 0.94] | 0.074 (2/27) [0.02; 0.23] | 1.000 (2/2) [0.34; 1.00] | 0 |
| `max_storeys` | 1.000 (4/4) [0.51; 1.00] | 0.444 (4/9) [0.19; 0.73] | 1.000 (4/4) [0.51; 1.00] | 0.444 (4/9) [0.19; 0.73] | null (no_eligible_cases) | 0 |
| `setback_m` | 1.000 (3/3) [0.44; 1.00] | 0.375 (3/8) [0.14; 0.69] | 1.000 (3/3) [0.44; 1.00] | 0.375 (3/8) [0.14; 0.69] | null (no_eligible_cases) | 0 |

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

Wpisów w śladzie błędów: 200 (`errors.json`, `errors.csv`). Każdy ma dokument, SHA-256, stronę anotacji, wynik parsera i wersję parsera.

| Typ | liczba |
|---|---:|
| `detection_fn` | 188 |
| `value_error` | 12 |

Rozdzielenie błędów: **rozpoznanie** (`detection_fn`, `detection_fp`, `zone_not_found`: czy wartość została znaleziona), **normalizacja/wartość** (`value_error`: wartość znaleziona, ale inna niż w adnotacji lub z dodatkową wartością) i **przypisanie** (`zone_assignment`: wartość innej strefy).

| Kategoria | pdf_text | pdf_table | html | ocr_real | ocr_simulated | razem |
|---|---:|---:|---:|---:|---:|---:|
| `recognition` | 139 | 10 | 18 | 0 | 21 | 188 |
| `normalization_or_value` | 7 | 0 | 4 | 0 | 1 | 12 |
| `assignment` | 0 | 0 | 0 | 0 | 0 | 0 |

Wskazówki przyczyn (wyprowadzone z oznaczeń niejednoznaczności w adnotacji; nie są dowodem przyczyny w kodzie parsera):

| Wskazówka | liczba |
|---|---:|
| `wording_not_matched` | 124 |
| `conditional_value` | 22 |
| `implicit_percent` | 12 |
| `ocr_noise_or_wording` | 12 |
| `extraction_artifact` | 8 |
| `shared_section` | 6 |
| `value_from_other_context` | 5 |
| `residual_clause` | 4 |
| `number_word` | 4 |
| `building_line_not_setback_phrase` | 3 |
