# Porównanie silników parsera MPZP (PV3-03)

Plik jest generowany z wyników silników; nie jest edytowany ręcznie. Porównanie jest sparowane: każdy silnik oceniono na tych samych parach (próbka, strefa, parametr) tego samego manifestu.

- silnik odniesienia: `legacy`; silniki: `legacy`, `v3`;
- `annotations_sha256`: `1408aee8787834219103448e5ebbc4773951d3f65d252a05d381c91087be7244`; `corpus_sha256`: `ca5a005efeff440fc59aeca9f9909f5ee8313d7f9dd314baab4bf31c15a8fdd0`;
- przedziały: bootstrap po próbkach (klastrach), 2000 losowań, ziarno 603; test McNemara dokładny, dwustronny; przy mniej niż 5 próbkach w wycinku przedział nie jest podawany.

## Metryki każdego silnika

| Silnik | Precision | Recall | Dokładność wartości (znalezione) | Przypisanie do strefy (wg wartości) | `source_consistent` | Dokładność end-to-end ze źródłem |
|---|---|---|---|---|---|---|
| `legacy` | 1.000 (208/208) | 0.832 (208/250) | 0.904 (188/208) | 1.000 (98/98) | 0.768 (209/272) | 0.548 (137/250) |
| `v3` | 1.000 (248/248) | 0.992 (248/250) | 0.940 (233/248) | 1.000 (118/118) | 0.975 (355/364) | 0.896 (224/250) |

### Metryki według formatu

| Wycinek | Silnik | Precision | Recall | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|
| `html` | `legacy` | 1.000 (23/23) | 1.000 (23/23) | 0.925 (37/40) | 0.652 (15/23) |
| `html` | `v3` | 1.000 (23/23) | 1.000 (23/23) | 0.925 (37/40) | 0.739 (17/23) |
| `ocr_real` | `legacy` | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) |
| `ocr_real` | `v3` | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) |
| `ocr_simulated` | `legacy` | 1.000 (9/9) | 0.391 (9/23) | 1.000 (10/10) | 0.304 (7/23) |
| `ocr_simulated` | `v3` | 1.000 (21/21) | 0.913 (21/23) | 1.000 (32/32) | 0.391 (9/23) |
| `pdf_table` | `legacy` | 1.000 (10/10) | 1.000 (10/10) | 1.000 (10/10) | 1.000 (10/10) |
| `pdf_table` | `v3` | 1.000 (10/10) | 1.000 (10/10) | 1.000 (10/10) | 1.000 (10/10) |
| `pdf_text` | `legacy` | 1.000 (164/164) | 0.854 (164/192) | 0.714 (150/210) | 0.536 (103/192) |
| `pdf_text` | `v3` | 1.000 (192/192) | 1.000 (192/192) | 0.979 (274/280) | 0.969 (186/192) |

### Metryki według gminy

| Wycinek | Silnik | Precision | Recall | `source_consistent` | End-to-end ze źródłem |
|---|---|---|---|---|---|
| `Białystok` | `legacy` | 1.000 (12/12) | 0.857 (12/14) | 0.846 (22/26) | 0.643 (9/14) |
| `Białystok` | `v3` | 1.000 (14/14) | 1.000 (14/14) | 1.000 (29/29) | 1.000 (14/14) |
| `Bielsko-Biała` | `legacy` | 1.000 (15/15) | 1.000 (15/15) | 1.000 (18/18) | 1.000 (15/15) |
| `Bielsko-Biała` | `v3` | 1.000 (15/15) | 1.000 (15/15) | 1.000 (18/18) | 1.000 (15/15) |
| `Kraków` | `legacy` | 1.000 (44/44) | 1.000 (44/44) | 0.265 (13/49) | 0.114 (5/44) |
| `Kraków` | `v3` | 1.000 (44/44) | 1.000 (44/44) | 1.000 (50/50) | 1.000 (44/44) |
| `Krzemieniewo` | `legacy` | 1.000 (9/9) | 1.000 (9/9) | 1.000 (12/12) | 0.889 (8/9) |
| `Krzemieniewo` | `v3` | 1.000 (9/9) | 1.000 (9/9) | 1.000 (34/34) | 1.000 (9/9) |
| `Legnica` | `legacy` | 1.000 (10/10) | 1.000 (10/10) | 1.000 (10/10) | 1.000 (10/10) |
| `Legnica` | `v3` | 1.000 (10/10) | 1.000 (10/10) | 1.000 (10/10) | 1.000 (10/10) |
| `Mogilany` | `legacy` | 1.000 (6/6) | 0.600 (6/10) | 1.000 (6/6) | 0.600 (6/10) |
| `Mogilany` | `v3` | 1.000 (8/8) | 0.800 (8/10) | 1.000 (11/11) | 0.800 (8/10) |
| `Ostrów Wielkopolski` | `legacy` | 1.000 (23/23) | 0.697 (23/33) | 0.692 (18/26) | 0.485 (16/33) |
| `Ostrów Wielkopolski` | `v3` | 1.000 (33/33) | 1.000 (33/33) | 1.000 (39/39) | 1.000 (33/33) |
| `Pisz` | `legacy` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_source_checks) | null (no_eligible_cases) |
| `Pisz` | `v3` | null (no_eligible_cases) | null (no_eligible_cases) | null (no_source_checks) | null (no_eligible_cases) |
| `Raszków` | `legacy` | 1.000 (7/7) | 0.636 (7/11) | 1.000 (22/22) | 0.636 (7/11) |
| `Raszków` | `v3` | 1.000 (11/11) | 1.000 (11/11) | 1.000 (26/26) | 1.000 (11/11) |
| `Stare Miasto` | `legacy` | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) |
| `Stare Miasto` | `v3` | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) | 1.000 (2/2) |
| `Szczytno` | `legacy` | 1.000 (24/24) | 0.750 (24/32) | 1.000 (24/24) | 0.719 (23/32) |
| `Szczytno` | `v3` | 1.000 (32/32) | 1.000 (32/32) | 0.864 (38/44) | 0.812 (26/32) |
| `Warszawa` | `legacy` | 1.000 (33/33) | 0.702 (33/47) | 0.676 (25/37) | 0.447 (21/47) |
| `Warszawa` | `v3` | 1.000 (47/47) | 1.000 (47/47) | 1.000 (61/61) | 0.745 (35/47) |
| `Łódź` | `legacy` | 1.000 (23/23) | 1.000 (23/23) | 0.925 (37/40) | 0.652 (15/23) |
| `Łódź` | `v3` | 1.000 (23/23) | 1.000 (23/23) | 0.925 (37/40) | 0.739 (17/23) |

## Porównanie sparowane: `legacy` → `v3`

Par łącznie: 423. Różnica = odsetek `v3` − odsetek `legacy`; „tylko odniesienie / tylko kandydat” to pary niezgodne (b, c).

### wartość dokładna (tp_exact) wśród par z wartością w adnotacji

| Wycinek | pary | próbki | odniesienie | kandydat | różnica | 95% CI | b / c | p McNemar |
|---|---:|---:|---:|---:|---:|---|---|---:|
| `overall` | 250 | 20 | 188/250 | 233/250 | +0.180 | [+0.095; +0.265] | 2 / 47 | 0.0000 |
| `format=html` | 23 | 2 | 18/23 | 20/23 | +0.087 | n/a | 0 / 2 | 0.5000 |
| `format=ocr_real` | 2 | 1 | 2/2 | 2/2 | +0.000 | n/a | 0 / 0 | n/a |
| `format=ocr_simulated` | 23 | 2 | 7/23 | 9/23 | +0.087 | n/a | 2 / 4 | 0.6875 |
| `format=pdf_table` | 10 | 1 | 10/10 | 10/10 | +0.000 | n/a | 0 / 0 | n/a |
| `format=pdf_text` | 192 | 14 | 151/192 | 192/192 | +0.214 | [+0.109; +0.319] | 0 / 41 | 0.0000 |
| `gmina=Białystok` | 14 | 1 | 12/14 | 14/14 | +0.143 | n/a | 0 / 2 | 0.5000 |
| `gmina=Bielsko-Biała` | 15 | 1 | 15/15 | 15/15 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Kraków` | 44 | 3 | 33/44 | 44/44 | +0.250 | n/a | 0 / 11 | 0.0010 |
| `gmina=Krzemieniewo` | 9 | 1 | 8/9 | 9/9 | +0.111 | n/a | 0 / 1 | 1.0000 |
| `gmina=Legnica` | 10 | 1 | 10/10 | 10/10 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Mogilany` | 10 | 2 | 6/10 | 8/10 | +0.200 | n/a | 0 / 2 | 0.5000 |
| `gmina=Ostrów Wielkopolski` | 33 | 2 | 23/33 | 33/33 | +0.303 | n/a | 0 / 10 | 0.0020 |
| `gmina=Raszków` | 11 | 1 | 7/11 | 11/11 | +0.364 | n/a | 0 / 4 | 0.1250 |
| `gmina=Stare Miasto` | 2 | 1 | 2/2 | 2/2 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Szczytno` | 32 | 2 | 23/32 | 32/32 | +0.281 | n/a | 0 / 9 | 0.0039 |
| `gmina=Warszawa` | 47 | 3 | 31/47 | 35/47 | +0.085 | n/a | 2 / 6 | 0.2891 |
| `gmina=Łódź` | 23 | 2 | 18/23 | 20/23 | +0.087 | n/a | 0 / 2 | 0.5000 |
| `split=development` | 89 | 7 | 75/89 | 86/89 | +0.124 | [+0.000; +0.250] | 0 / 11 | 0.0010 |
| `split=final` | 161 | 13 | 113/161 | 147/161 | +0.211 | [+0.111; +0.331] | 2 / 36 | 0.0000 |

### wartość dokładna i z właściwego źródła (source-aware) wśród tych samych par

| Wycinek | pary | próbki | odniesienie | kandydat | różnica | 95% CI | b / c | p McNemar |
|---|---:|---:|---:|---:|---:|---|---|---:|
| `overall` | 250 | 20 | 161/250 | 224/250 | +0.252 | [+0.146; +0.365] | 4 / 67 | 0.0000 |
| `format=html` | 23 | 2 | 15/23 | 17/23 | +0.087 | n/a | 0 / 2 | 0.5000 |
| `format=ocr_real` | 2 | 1 | 2/2 | 2/2 | +0.000 | n/a | 0 / 0 | n/a |
| `format=ocr_simulated` | 23 | 2 | 7/23 | 9/23 | +0.087 | n/a | 2 / 4 | 0.6875 |
| `format=pdf_table` | 10 | 1 | 10/10 | 10/10 | +0.000 | n/a | 0 / 0 | n/a |
| `format=pdf_text` | 192 | 14 | 127/192 | 186/192 | +0.307 | [+0.184; +0.435] | 2 / 61 | 0.0000 |
| `gmina=Białystok` | 14 | 1 | 9/14 | 14/14 | +0.357 | n/a | 0 / 5 | 0.0625 |
| `gmina=Bielsko-Biała` | 15 | 1 | 15/15 | 15/15 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Kraków` | 44 | 3 | 17/44 | 44/44 | +0.614 | n/a | 0 / 27 | 0.0000 |
| `gmina=Krzemieniewo` | 9 | 1 | 8/9 | 9/9 | +0.111 | n/a | 0 / 1 | 1.0000 |
| `gmina=Legnica` | 10 | 1 | 10/10 | 10/10 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Mogilany` | 10 | 2 | 6/10 | 8/10 | +0.200 | n/a | 0 / 2 | 0.5000 |
| `gmina=Ostrów Wielkopolski` | 33 | 2 | 20/33 | 33/33 | +0.394 | n/a | 0 / 13 | 0.0002 |
| `gmina=Raszków` | 11 | 1 | 7/11 | 11/11 | +0.364 | n/a | 0 / 4 | 0.1250 |
| `gmina=Stare Miasto` | 2 | 1 | 2/2 | 2/2 | +0.000 | n/a | 0 / 0 | n/a |
| `gmina=Szczytno` | 32 | 2 | 23/32 | 26/32 | +0.094 | n/a | 2 / 5 | 0.4531 |
| `gmina=Warszawa` | 47 | 3 | 29/47 | 35/47 | +0.128 | n/a | 2 / 8 | 0.1094 |
| `gmina=Łódź` | 23 | 2 | 15/23 | 17/23 | +0.087 | n/a | 0 / 2 | 0.5000 |
| `split=development` | 89 | 7 | 56/89 | 83/89 | +0.303 | [+0.062; +0.541] | 0 / 27 | 0.0000 |
| `split=final` | 161 | 13 | 105/161 | 141/161 | +0.224 | [+0.134; +0.323] | 4 / 40 | 0.0000 |

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
| `legacy` | 0 | — | — | 18.5 / 53.7 | — / — | — | — | — |
| `v3` | 0 | — | — | 40.1 / 94.1 | — / — | — | — | — |
