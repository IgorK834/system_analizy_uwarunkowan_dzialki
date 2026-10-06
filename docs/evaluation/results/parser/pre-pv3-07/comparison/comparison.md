# Porównanie silników parsera MPZP (PV3-03)

Plik jest generowany z wyników silników; nie jest edytowany ręcznie. Porównanie jest sparowane: każdy silnik oceniono na tych samych parach (próbka, strefa, parametr) tego samego manifestu.

- silnik odniesienia: `legacy`; silniki: `legacy`, `v3`;
- `annotations_sha256`: `1408aee8787834219103448e5ebbc4773951d3f65d252a05d381c91087be7244`; `corpus_sha256`: `ca5a005efeff440fc59aeca9f9909f5ee8313d7f9dd314baab4bf31c15a8fdd0`;
- przedziały: bootstrap po próbkach (klastrach), 2000 losowań, ziarno 603; test McNemara dokładny, dwustronny; przy mniej niż 5 próbkach w wycinku przedział nie jest podawany.

## Metryki każdego silnika

| Silnik | Precision | Recall | Dokładność wartości (znalezione) | Przypisanie do strefy (wg wartości) | `source_consistent` | Dokładność end-to-end ze źródłem |
|---|---|---|---|---|---|---|
| `legacy` | 1.000 (62/62) | 0.248 (62/250) | 0.806 (50/62) | 1.000 (37/37) | 0.680 (51/75) | 0.156 (39/250) |
| `v3` | 1.000 (73/73) | 0.292 (73/250) | 0.849 (62/73) | 1.000 (43/43) | 1.000 (88/88) | 0.248 (62/250) |

### Metryki według formatu

| Wycinek | Silnik | Precision | Recall | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|
| `html` | `legacy` | 1.000 (5/5) | 0.217 (5/23) | 0.800 (4/5) | 0.043 (1/23) |
| `html` | `v3` | 1.000 (6/6) | 0.261 (6/23) | 1.000 (6/6) | 0.130 (3/23) |
| `ocr_real` | `legacy` | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) |
| `ocr_real` | `v3` | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) |
| `ocr_simulated` | `legacy` | 1.000 (2/2) | 0.087 (2/23) | 1.000 (3/3) | 0.043 (1/23) |
| `ocr_simulated` | `v3` | 1.000 (6/6) | 0.261 (6/23) | 1.000 (9/9) | 0.000 (0/23) |
| `pdf_table` | `legacy` | null (no_eligible_cases) | 0.000 (0/10) | null (no_source_checks) | 0.000 (0/10) |
| `pdf_table` | `v3` | null (no_eligible_cases) | 0.000 (0/10) | null (no_source_checks) | 0.000 (0/10) |
| `pdf_text` | `legacy` | 1.000 (53/53) | 0.276 (53/192) | 0.646 (42/65) | 0.182 (35/192) |
| `pdf_text` | `v3` | 1.000 (59/59) | 0.307 (59/192) | 1.000 (71/71) | 0.297 (57/192) |

### Metryki według gminy

| Wycinek | Silnik | Precision | Recall | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|
| `Białystok` | `legacy` | 1.000 (4/4) | 0.286 (4/14) | 0.000 (0/4) | 0.000 (0/14) |
| `Białystok` | `v3` | 1.000 (4/4) | 0.286 (4/14) | 1.000 (4/4) | 0.143 (2/14) |
| `Bielsko-Biała` | `legacy` | 1.000 (15/15) | 1.000 (15/15) | 1.000 (18/18) | 1.000 (15/15) |
| `Bielsko-Biała` | `v3` | 1.000 (15/15) | 1.000 (15/15) | 1.000 (18/18) | 1.000 (15/15) |
| `Kraków` | `legacy` | 1.000 (10/10) | 0.227 (10/44) | 0.312 (5/16) | 0.045 (2/44) |
| `Kraków` | `v3` | 1.000 (10/10) | 0.227 (10/44) | 1.000 (16/16) | 0.227 (10/44) |
| `Krzemieniewo` | `legacy` | 1.000 (2/2) | 0.222 (2/9) | 1.000 (2/2) | 0.222 (2/9) |
| `Krzemieniewo` | `v3` | 1.000 (2/2) | 0.222 (2/9) | 1.000 (2/2) | 0.222 (2/9) |
| `Legnica` | `legacy` | null (no_eligible_cases) | 0.000 (0/10) | null (no_source_checks) | 0.000 (0/10) |
| `Legnica` | `v3` | null (no_eligible_cases) | 0.000 (0/10) | null (no_source_checks) | 0.000 (0/10) |
| `Mogilany` | `legacy` | null (no_eligible_cases) | 0.000 (0/10) | null (no_source_checks) | 0.000 (0/10) |
| `Mogilany` | `v3` | null (no_eligible_cases) | 0.000 (0/10) | null (no_source_checks) | 0.000 (0/10) |
| `Ostrów Wielkopolski` | `legacy` | 1.000 (6/6) | 0.182 (6/33) | 0.833 (5/6) | 0.152 (5/33) |
| `Ostrów Wielkopolski` | `v3` | 1.000 (9/9) | 0.273 (9/33) | 1.000 (9/9) | 0.273 (9/33) |
| `Pisz` | `legacy` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_source_checks) | null (no_eligible_cases) |
| `Pisz` | `v3` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_source_checks) | null (no_eligible_cases) |
| `Raszków` | `legacy` | null (no_eligible_cases) | 0.000 (0/11) | null (no_source_checks) | 0.000 (0/11) |
| `Raszków` | `v3` | null (no_eligible_cases) | 0.000 (0/11) | null (no_source_checks) | 0.000 (0/11) |
| `Stare Miasto` | `legacy` | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) |
| `Stare Miasto` | `v3` | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) |
| `Szczytno` | `legacy` | 1.000 (6/6) | 0.188 (6/32) | 1.000 (6/6) | 0.188 (6/32) |
| `Szczytno` | `v3` | 1.000 (8/8) | 0.250 (8/32) | 1.000 (8/8) | 0.250 (8/32) |
| `Warszawa` | `legacy` | 1.000 (12/12) | 0.255 (12/47) | 0.562 (9/16) | 0.128 (6/47) |
| `Warszawa` | `v3` | 1.000 (17/17) | 0.362 (17/47) | 1.000 (23/23) | 0.234 (11/47) |
| `Łódź` | `legacy` | 1.000 (5/5) | 0.217 (5/23) | 0.800 (4/5) | 0.043 (1/23) |
| `Łódź` | `v3` | 1.000 (6/6) | 0.261 (6/23) | 1.000 (6/6) | 0.130 (3/23) |

## Porównanie sparowane: `legacy` → `v3`

Par łącznie: 423. Różnica = odsetek `v3` − odsetek `legacy`; „tylko odniesienie / tylko kandydat” to pary niezgodne (b, c).

### wartość dokładna (tp_exact) wśród par z wartością w adnotacji

| Wycinek | pary | próbki | odniesienie | kandydat | różnica | 95% CI | b / c | p McNemar |
|---|---:|---:|---:|---:|---:|---|---|---:|
| `overall` | 250 | 20 | 50/250 | 62/250 | +0.048 | [+0.016; +0.079] | 1 / 13 | 0.0018 |
| `format=html` | 23 | 2 | 1/23 | 3/23 | +0.087 | n/a | 0 / 2 | 0.5000 |
| `format=ocr_real` | 2 | 1 | 2/2 | 2/2 | +0.000 | n/a | 0 / 0 | n/a |
| `format=ocr_simulated` | 23 | 2 | 1/23 | 0/23 | -0.043 | n/a | 1 / 0 | 1.0000 |
| `format=pdf_table` | 10 | 1 | 0/10 | 0/10 | +0.000 | n/a | 0 / 0 | n/a |
| `format=pdf_text` | 192 | 14 | 46/192 | 57/192 | +0.057 | [+0.027; +0.089] | 0 / 11 | 0.0010 |
| `gmina=Białystok` | 14 | 1 | 1/14 | 2/14 | +0.071 | n/a | 0 / 1 | 1.0000 |
| `gmina=Bielsko-Biała` | 15 | 1 | 15/15 | 15/15 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Kraków` | 44 | 3 | 6/44 | 10/44 | +0.091 | n/a | 0 / 4 | 0.1250 |
| `gmina=Krzemieniewo` | 9 | 1 | 2/9 | 2/9 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Legnica` | 10 | 1 | 0/10 | 0/10 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Mogilany` | 10 | 2 | 0/10 | 0/10 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Ostrów Wielkopolski` | 33 | 2 | 6/33 | 9/33 | +0.091 | n/a | 0 / 3 | 0.2500 |
| `gmina=Raszków` | 11 | 1 | 0/11 | 0/11 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Stare Miasto` | 2 | 1 | 2/2 | 2/2 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Szczytno` | 32 | 2 | 6/32 | 8/32 | +0.062 | n/a | 0 / 2 | 0.5000 |
| `gmina=Warszawa` | 47 | 3 | 11/47 | 11/47 | +0.000 | n/a | 1 / 1 | 1.0000 |
| `gmina=Łódź` | 23 | 2 | 1/23 | 3/23 | +0.087 | n/a | 0 / 2 | 0.5000 |
| `split=development` | 89 | 7 | 23/89 | 29/89 | +0.067 | [+0.023; +0.109] | 0 / 6 | 0.0312 |
| `split=final` | 161 | 13 | 27/161 | 33/161 | +0.037 | [+0.000; +0.075] | 1 / 7 | 0.0703 |

### wartość dokładna i z właściwego źródła (source-aware) wśród tych samych par

| Wycinek | pary | próbki | odniesienie | kandydat | różnica | 95% CI | b / c | p McNemar |
|---|---:|---:|---:|---:|---:|---|---|---:|
| `overall` | 250 | 20 | 44/250 | 62/250 | +0.072 | [+0.032; +0.111] | 1 / 19 | 0.0000 |
| `format=html` | 23 | 2 | 1/23 | 3/23 | +0.087 | n/a | 0 / 2 | 0.5000 |
| `format=ocr_real` | 2 | 1 | 2/2 | 2/2 | +0.000 | n/a | 0 / 0 | n/a |
| `format=ocr_simulated` | 23 | 2 | 1/23 | 0/23 | -0.043 | n/a | 1 / 0 | 1.0000 |
| `format=pdf_table` | 10 | 1 | 0/10 | 0/10 | +0.000 | n/a | 0 / 0 | n/a |
| `format=pdf_text` | 192 | 14 | 40/192 | 57/192 | +0.089 | [+0.050; +0.132] | 0 / 17 | 0.0000 |
| `gmina=Białystok` | 14 | 1 | 0/14 | 2/14 | +0.143 | n/a | 0 / 2 | 0.5000 |
| `gmina=Bielsko-Biała` | 15 | 1 | 15/15 | 15/15 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Kraków` | 44 | 3 | 2/44 | 10/44 | +0.182 | n/a | 0 / 8 | 0.0078 |
| `gmina=Krzemieniewo` | 9 | 1 | 2/9 | 2/9 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Legnica` | 10 | 1 | 0/10 | 0/10 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Mogilany` | 10 | 2 | 0/10 | 0/10 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Ostrów Wielkopolski` | 33 | 2 | 6/33 | 9/33 | +0.091 | n/a | 0 / 3 | 0.2500 |
| `gmina=Raszków` | 11 | 1 | 0/11 | 0/11 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Stare Miasto` | 2 | 1 | 2/2 | 2/2 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Szczytno` | 32 | 2 | 6/32 | 8/32 | +0.062 | n/a | 0 / 2 | 0.5000 |
| `gmina=Warszawa` | 47 | 3 | 10/47 | 11/47 | +0.021 | n/a | 1 / 2 | 1.0000 |
| `gmina=Łódź` | 23 | 2 | 1/23 | 3/23 | +0.087 | n/a | 0 / 2 | 0.5000 |
| `split=development` | 89 | 7 | 19/89 | 29/89 | +0.112 | [+0.043; +0.173] | 0 / 10 | 0.0020 |
| `split=final` | 161 | 13 | 25/161 | 33/161 | +0.050 | [+0.009; +0.088] | 1 / 9 | 0.0215 |

### fałszywy alarm (fp) wśród par bez wartości w adnotacji

| Wycinek | pary | próbki | odniesienie | kandydat | różnica | 95% CI | b / c | p McNemar |
|---|---:|---:|---:|---:|---:|---|---|---:|
| `overall` | 173 | 20 | 0/173 | 0/173 | +0.000 | [+0.000; +0.000] | 0 / 0 | n/a |
| `format=html` | 13 | 2 | 0/13 | 0/13 | +0.000 | n/a | 0 / 0 | n/a |
| `format=ocr_real` | 16 | 2 | 0/16 | 0/16 | +0.000 | n/a | 0 / 0 | n/a |
| `format=ocr_simulated` | 13 | 2 | 0/13 | 0/13 | +0.000 | n/a | 0 / 0 | n/a |
| `format=pdf_table` | 8 | 1 | 0/8 | 0/8 | +0.000 | n/a | 0 / 0 | n/a |
| `format=pdf_text` | 123 | 13 | 0/123 | 0/123 | +0.000 | [+0.000; +0.000] | 0 / 0 | n/a |
| `gmina=Białystok` | 4 | 1 | 0/4 | 0/4 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Bielsko-Biała` | 12 | 1 | 0/12 | 0/12 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Kraków` | 46 | 3 | 0/46 | 0/46 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Legnica` | 8 | 1 | 0/8 | 0/8 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Mogilany` | 8 | 2 | 0/8 | 0/8 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Ostrów Wielkopolski` | 12 | 2 | 0/12 | 0/12 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Pisz` | 9 | 1 | 0/9 | 0/9 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Raszków` | 16 | 1 | 0/16 | 0/16 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Stare Miasto` | 7 | 1 | 0/7 | 0/7 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Szczytno` | 4 | 2 | 0/4 | 0/4 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Warszawa` | 34 | 3 | 0/34 | 0/34 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Łódź` | 13 | 2 | 0/13 | 0/13 | +0.000 | n/a | 0 / 0 | n/a |
| `split=development` | 82 | 7 | 0/82 | 0/82 | +0.000 | [+0.000; +0.000] | 0 / 0 | n/a |
| `split=final` | 91 | 13 | 0/91 | 0/91 | +0.000 | [+0.000; +0.000] | 0 / 0 | n/a |

## Bramki odrzuceń kandydatów

- `legacy`: silnik nie zgłosił odrzuceń (brak bramek albo żaden kandydat nie został odrzucony).
- `v3`: silnik nie zgłosił odrzuceń (brak bramek albo żaden kandydat nie został odrzucony).

„Wartość była poprawna” oznacza odrzucenie wartości zgodnej z adnotacją (koszt bramki dla recall); „błędna” — słuszne odrzucenie.

## Koszt i opóźnienie (obserwacje, nie wynik merytoryczny)

Wartości zależą od środowiska i biegu; w trybie odtwarzania tokeny, opóźnienie modelu i koszt pochodzą z nagrania odpowiedzi, a nie z bieżącego wywołania. `—` oznacza „silnik nie raportuje”, nie zero.

| Silnik | wywołania | tokeny wejściowe | tokeny wyjściowe | czas ścienny p50 / p95 [ms] | opóźnienie modelu p50 / p95 [ms] | koszt łącznie [USD] | na dokument | na strefę |
|---|---:|---:|---:|---|---|---:|---:|---:|
| `legacy` | 0 | — | — | 8.8 / 21.3 | — / — | — | — | — |
| `v3` | 0 | — | — | 69.3 / 113.2 | — / — | — | — | — |
