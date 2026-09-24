# Wynik ewaluacji korpusu referencyjnego

## Metadane eksperymentu

| Pole | Wartość |
|---|---|
| `schema_version` | `1.0.0` |
| `commit_sha` | `8418bddc73bc76f590e021af122262f0a44bcaa4` |
| `corpus_sha256` | `65f16dc25c4fcde8ba6ee9051b3604f909477be7eebdb205fe8dcb8a1786d106` |
| `seed` | `20260923` |
| `source_release_ids` | `{"gdos": "gdos@2026-09-23", "isok": "isok@2026-09-23", "kimpzp": "kimpzp@2026-09-23", "krakow_mpzp": "krakow_mpzp@2026-09-23", "nmt": "nmt@2026-09-23", "ru_pog": "ru_pog@2026-09-23", "uldk": "uldk@2026-09-23"}` |
| `timestamps` | `{"finished_at": "2026-09-23T18:52:29.035187Z", "started_at": "2026-09-23T18:52:29.023456Z"}` |

## Metryki

| Metryka | Wartość | Jednostka | Mianownik |
|---|---:|---|---:|
| `case_count` | 30 | case |  |
| `failed_cases` | 0 | case |  |
| `parcel_identification_accuracy` | 1.000000 | ratio | 30 |
| `zone_class_accuracy` | 1.000000 | ratio | 10 |
| `zone_share_mae` | 0.000000 | percentage_point | 14 |
| `zone_share_max_absolute_error` | 0.000000 | percentage_point | 14 |
| `unknown_case_share` | 1.000000 | ratio | 30 |
| `partial_case_share` | 1.000000 | ratio | 30 |
| `manual_review_case_share` | 0.500000 | ratio | 30 |
| `field_completeness` | 0.675926 | ratio | 540 |
| `duration_p50` | 0.037583 | millisecond |  |
| `duration_p95` | 0.113121 | millisecond |  |
| `cache_hits` | 27 | case |  |
| `cache_misses` | 3 | case |  |

### Warunki binarne

| Warunek | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `flood_intersection` | 8 | 0 | 0 | 21 | 1.000000 | 1.000000 | 1.000000 |
| `nature_intersection` | 2 | 0 | 0 | 26 | 1.000000 | 1.000000 | 1.000000 |
| `ouz_presence` | 4 | 0 | 0 | 2 | 1.000000 | 1.000000 | 1.000000 |
| `aggregate` | 14 | 0 | 0 | 49 | 1.000000 | 1.000000 | 1.000000 |

## Przypadki

| case_id | status | cache | czas [ms] | unknown | manual review | błąd |
|---|---|---|---:|---|---|---|
| `real-001-146510-8-0502-1-3` | partial | miss | 2.112 | true | false |  |
| `real-002-146510-8-0310-82-2` | partial | hit | 0.059 | true | false |  |
| `real-003-146510-8-0504-1` | partial | hit | 0.045 | true | false |  |
| `real-004-146510-8-0309-1-4` | partial | hit | 0.038 | true | false |  |
| `real-005-126105-9-0001-580-4` | partial | miss | 0.118 | true | false |  |
| `real-006-126105-9-0001-49-2` | partial | hit | 0.033 | true | false |  |
| `real-007-126105-9-0001-540-15` | partial | hit | 0.108 | true | false |  |
| `real-008-126105-9-0119-181-10` | partial | hit | 0.037 | true | false |  |
| `real-009-246101-1-0056-155-3` | partial | miss | 0.037 | true | false |  |
| `real-010-246101-1-0055-68-13` | partial | hit | 0.041 | true | false |  |
| `real-011-246101-1-0081-7` | partial | hit | 0.032 | true | false |  |
| `real-012-246101-1-0056-110-1` | partial | hit | 0.032 | true | false |  |
| `real-013-026201-1-0009-1319-4` | partial | hit | 0.039 | true | true |  |
| `real-014-026201-1-0010-410-1` | partial | hit | 0.081 | true | true |  |
| `real-015-026201-1-0015-530` | partial | hit | 0.075 | true | true |  |
| `real-016-026201-1-0009-579-9` | partial | hit | 0.047 | true | true |  |
| `real-017-281603-4-0001-496-5` | partial | hit | 0.066 | true | true |  |
| `real-018-281603-4-0001-358` | partial | hit | 0.034 | true | true |  |
| `real-019-281603-4-0001-740` | partial | hit | 0.068 | true | true |  |
| `real-020-281603-4-0002-130` | partial | hit | 0.038 | true | true |  |
| `real-021-022104-2-0002-352` | partial | hit | 0.034 | true | true |  |
| `real-022-022104-2-0002-288-20` | partial | hit | 0.037 | true | true |  |
| `real-023-022104-2-0002-776` | partial | hit | 0.032 | true | true |  |
| `real-024-022104-2-0002-230-2` | partial | hit | 0.032 | true | true |  |
| `real-025-281603-4-0001-431-66` | partial | hit | 0.069 | true | true |  |
| `real-026-281604-5-0011-107` | partial | hit | 0.033 | true | false |  |
| `real-027-246101-1-0001-1043` | partial | hit | 0.036 | true | true |  |
| `real-028-246101-1-0004-737-26` | partial | hit | 0.034 | true | false |  |
| `real-029-246101-1-0004-741-132` | partial | hit | 0.037 | true | false |  |
| `real-030-026201-1-0009-578-2` | partial | hit | 0.040 | true | true |  |

## Próg regresji

Wszystkie zadeklarowane progi zostały spełnione.

`ambiguous` usuwa wyłącznie wskazaną metrykę z jej accuracy/MAE. 
Przypadek nadal występuje w tabeli i mianowniku kompletności.
