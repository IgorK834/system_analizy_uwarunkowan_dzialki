# Badanie poprawności na zamrożonym korpusie (BK-601)

Wszystkie liczby pochodzą z `evaluation.json`; ten plik jest generowany i nie jest edytowany ręcznie.

## Zamrożony manifest biegu

| Pole | Wartość |
|---|---|
| `commit_sha` | `cbefc0a22ba65bbcdfd205132ebd8070c34120db` |
| `manifest_sha256` | `f8d42cb487fce424a11ded34b026538d669e04b8c52efd0b02f64a849becfcd1` |
| `corpus_sha256` | `65f16dc25c4fcde8ba6ee9051b3604f909477be7eebdb205fe8dcb8a1786d106` |
| `corpus_id` | `BK-002-real-parcels-2026-09-23` |
| `substantive_sha256` | `a9dd98d131db79d8b00bbd5f1b5c84050cc216c2b5442efcbaf82bf0faf57495` |
| `source_release_ids` | `{"gdos":"gdos@2026-09-23","isok":"isok@2026-09-23","kimpzp":"kimpzp@2026-09-23","krakow_mpzp":"krakow_mpzp@2026-09-23","nmt":"nmt@2026-09-23","ru_pog":"ru_pog@2026-09-23","uldk":"uldk@2026-09-23"}` |
| `versions` | `{"analysis_result_contract":"pog-v2.4+mpzp-v2.1+terrain-v1.0+risk-v1.0+quality-v1.0","mpzp_parser":"mpzp-parser/2.0","planning_rule_set":"1.0","pog_presentation_style":"2026.09.29-1","quality_policy_schema":"quality-policy/1","report_layout":"report-v2/2026.09.29-1","report_map_config":"report-map/2026.09.29-1","terrain_algorithm":"horn1981-3x3-v1","terrain_slope_classes":"slope-classes-pl-v1"}` |
| `environment` | `{"geos":"3.13.1","implementation":"CPython","platform":"Darwin arm64","proj":"9.5.1","pyproj":"3.7.2","python":"3.13.3","shapely":"2.1.2"}` |
| `worktree.dirty` | `True` (14 ścieżek) |

Pełne parametry ewaluacji, skróty wszystkich artefaktów korpusu i odcisk kodu są w `run_manifest.json`. `manifest_sha256` obejmuje wejście badania, a nie czas ani listę zmienionych plików.

## Liczność i mianowniki

| Wielkość | Wartość |
|---|---:|
| wejścia | 30 |
| ukończone (`complete`) | 0 |
| częściowe (`partial`) | 30 |
| błędne (`failed`) | 0 |
| wejścia = ukończone + częściowe + błędne | tak |

## Metryki BK-004 (nagłówkowe) z mianownikami

| Metryka | Wartość | Jednostka |
|---|---|---|
| `case_count` | 30 | case |
| `failed_cases` | 0 | case |
| `parcel_identification_accuracy` | 1.000000 (30/30) | ratio |
| `zone_class_accuracy` | 1.000000 (10/10) | ratio |
| `zone_share_mae` | 0.000000 | percentage_point |
| `zone_share_max_absolute_error` | 0.000000 | percentage_point |
| `risk_area_mae` | 0.000000 | square_metre |
| `risk_area_max_absolute_error` | 0.000000 | square_metre |
| `risk_share_mae` | 0.000000 | percentage_point |
| `unknown_case_share` | 1.000000 (30/30) | ratio |
| `partial_case_share` | 1.000000 (30/30) | ratio |
| `manual_review_case_share` | 0.500000 (15/30) | ratio |
| `field_completeness` | 0.675926 (365/540) | ratio |
| `cache_hits` | 27 | case |
| `cache_misses` | 3 | case |

### Macierze confusion warunków binarnych

| Warunek | TP | FP | FN | TN | n | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---|---|---|
| `flood_intersection` | 8 | 0 | 0 | 21 | 29 | 1.000000 (8/8) | 1.000000 (8/8) | 1.000000 |
| `nature_intersection` | 2 | 0 | 0 | 26 | 28 | 1.000000 (2/2) | 1.000000 (2/2) | 1.000000 |
| `ouz_presence` | 4 | 0 | 0 | 2 | 6 | 1.000000 (4/4) | 1.000000 (4/4) | 1.000000 |
| `aggregate` | 14 | 0 | 0 | 49 | 63 | 1.000000 (14/14) | 1.000000 (14/14) | 1.000000 |

## Co naprawdę waliduje każda metryka

Metryka nagłówkowa może być wysoka, bo runner BK-004 odczytuje zamrożone obserwacje. Poniższa tabela mówi, co potwierdza dany wynik.

| Ścieżka | Rodzaj pomiaru | Co jest sprawdzane |
|---|---|---|
| `geometry.area` | `production_computation` | calculate_geometry_metrics na zamrożonej geometrii ULDK |
| `geometry.perimeter` | `production_computation` | calculate_geometry_metrics na zamrożonej geometrii ULDK |
| `pog.*` | `observation_passthrough` | udziały i pola stref skopiowane z zamrożonej obserwacji RU; brak przecięcia na geometrii stref (geometrii stref nie zamrożono) |
| `ouz.*` | `observation_passthrough` | pole i udział OUZ skopiowane z obserwacji; relacja z reguł progowych harnessu |
| `mpzp.*` | `observation_passthrough` | symbole i udziały z zamrożonego discovery KIMPZP / pomiaru Krakowa; tryb z reguły harnessu |
| `flood.*` | `production_contract_mapping` | obserwacja ISOK mapowana przez risk_section_from_structured (ten sam kontrakt co API); wartości przecięć z obserwacji |
| `nature.*` | `production_contract_mapping` | obserwacja GDOŚ mapowana przez risk_section_from_structured; wartości przecięć z obserwacji |
| `terrain.*` | `observation_passthrough` | wysokości z obserwacji NMT; klasa z progów harnessu (2 m, 10 m) |

## Zgodność na poziomie pól i statusów

- zgodność pól: 0.973631 (480/493) (licznik: `match` + `within_tolerance`; mianownik: pola o znanej wartości oczekiwanej, bez `ambiguous` i bez `both_unknown`);
- zgodność statusów sekcji: 0.990476 (208/210);
- fałszywa pewność (wartość podana, gdy ground truth mówi `unknown`): 0.050336 (15/298);
- werdykty pól: `{"ambiguous_excluded":2,"both_unknown":283,"match":420,"mismatch":13,"unexpected_actual":15,"within_tolerance":60}`.

| Sekcja | Zgodność pól | Zgodność statusu | Statusy wyniku |
|---|---|---|---|
| `geometry` | 1.000000 (60/60) | 1.000000 (30/30) | `{"available":30}` |
| `pog` | 1.000000 (39/39) | 1.000000 (30/30) | `{"available":7,"unknown":23}` |
| `ouz` | 1.000000 (20/20) | 1.000000 (30/30) | `{"available":6,"manual_review":1,"unknown":23}` |
| `mpzp` | 0.822581 (51/62) | 0.966667 (29/30) | `{"available":4,"manual_review":14,"unknown":12}` |
| `flood` | 0.992424 (131/132) | 0.966667 (29/30) | `{"available":30}` |
| `nature` | 1.000000 (68/68) | 1.000000 (30/30) | `{"available":28,"unknown":2}` |
| `terrain` | 0.991071 (111/112) | 1.000000 (30/30) | `{"available":28,"unknown":2}` |

## Błędy pól i udziałów

| Pole | Jednostka | n | MAE | max | ponad tolerancję |
|---|---|---:|---:|---:|---:|
| `flood.features.area` | square_metre | 24 | 0.000000 | 0.000000 | 0 |
| `flood.features.share` | percentage_point | 24 | 0.000000 | 0.000000 | 0 |
| `geometry.area` | square_metre | 30 | 0.000306 | 0.000497 | 0 |
| `geometry.perimeter` | metre | 30 | 0.000278 | 0.000495 | 0 |
| `mpzp.zones.area` | square_metre | 6 | 0.000000 | 0.000000 | 0 |
| `mpzp.zones.share` | percentage_point | 6 | 0.000000 | 0.000000 | 0 |
| `nature.features.area` | square_metre | 3 | 0.000000 | 0.000000 | 0 |
| `nature.features.share` | percentage_point | 3 | 0.000000 | 0.000000 | 0 |
| `ouz.intersection_area` | square_metre | 7 | 0.000000 | 0.000000 | 0 |
| `ouz.share` | percentage_point | 7 | 0.000000 | 0.000000 | 0 |
| `pog.zones.area` | square_metre | 9 | 0.000000 | 0.000000 | 0 |
| `pog.zones.share` | percentage_point | 9 | 0.000000 | 0.000000 | 0 |
| `terrain.maximum` | metre | 28 | 0.000000 | 0.000000 | 0 |
| `terrain.minimum` | metre | 28 | 0.000000 | 0.000000 | 0 |
| `terrain.relief` | metre | 28 | 0.000000 | 0.000000 | 0 |

Porównań udziałów: 49; MAE 0.000000 pp; maksimum 0.000000 pp; powyżej 0.5 pp: 0.000000 (0/49).

Żadne porównanie udziału nie przekracza 0.5 pp; uwaga: udziały stref POG/MPZP/OUZ są w tym runnerze odczytem obserwacji, więc wynik 0 nie dowodzi poprawności przecięcia (patrz tabela pomiarów).

## Kompletność i manual review

- kompletność pól (stały zestaw 18 pól): 0.675926 (365/540);
- przypadki z nieznaną sekcją: 1.000000 (30/30);
- przypadki częściowe: 1.000000 (30/30);
- przypadki wymagające manual review: 0.500000 (15/30).

## Rejestr błędów i ograniczeń

Wpisów: 107. Podział według rodzaju i kategorii przyczyny:

| Rodzaj | source | data | geometry | parser | presentation | razem |
|---|---:|---:|---:|---:|---:|---:|
| `case_failure` | 0 | 0 | 0 | 0 | 0 | 0 |
| `field_discrepancy` | 0 | 2 | 0 | 25 | 1 | 28 |
| `status_disagreement` | 0 | 2 | 0 | 0 | 0 | 2 |
| `limitation` | 76 | 0 | 1 | 0 | 0 | 77 |

Szczegóły każdego wpisu, dowody i wyjaśnienia: [`error_analysis.md`](../../error_analysis.md) oraz `error_ledger.json`.

## Determinizm i czas

Powtórzeń: 3; `substantive_sha256` identyczne: **tak**.

| Bieg | `substantive_sha256` |
|---:|---|
| 1 | `a9dd98d131db79d8b00bbd5f1b5c84050cc216c2b5442efcbaf82bf0faf57495` |
| 2 | `a9dd98d131db79d8b00bbd5f1b5c84050cc216c2b5442efcbaf82bf0faf57495` |
| 3 | `a9dd98d131db79d8b00bbd5f1b5c84050cc216c2b5442efcbaf82bf0faf57495` |

Czas (osobna obserwacja, `timing.json`): p50 0.0372 ms, p95 0.1306 ms. Czas różni się między biegami i nie wchodzi do skrótu merytorycznego.
