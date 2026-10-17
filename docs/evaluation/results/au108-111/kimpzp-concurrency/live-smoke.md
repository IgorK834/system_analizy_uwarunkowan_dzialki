# Live smoke korpusu referencyjnego — 2026-10-17

Przebieg korpusu referencyjnego (30 działek) na żywych usługach (`POST /analyze`), bez zamrożonych obserwacji — różnica względem ewaluacji offline (`evaluate_reference_corpus.py`, tryb `offline`) jest wynikiem tej pracy. W raporcie nie ma tokenów dostępu ani pełnych odpowiedzi.

**Wynik: PASS** — próg: 0 odpowiedzi 5xx i 0 braków odpowiedzi. 5xx: **0**, brak odpowiedzi: **0**, `complete`: **0** z 30 udanych analiz (HTTP 2xx).

## Metadane

| Pole | Wartość |
|---|---|
| Adres API | `http://localhost:8000` |
| Korpus | `BK-002-real-parcels-2026-09-23` (`sha256:65f16dc25c4fcde8…`) |
| Commit | `unknown` |
| Start / koniec (UTC) | 2026-10-17T19:59:08Z / 2026-10-17T20:00:29Z (81.0 s) |
| Równoległość / limit czasu żądania | 3 / 180 s |
| `force_refresh` | false |
| Ponowienia po HTTP 429 (`Retry-After`) | 0 |

## Podsumowanie

### Statusy HTTP

| HTTP | Przypadki |
|---|---:|
| `200` | 30 |

### Status analizy (`status`, tylko HTTP 2xx)

| status | Przypadki |
|---|---:|
| `partial` | 30 |

### Kody błędów (`ErrorResponse.error`)

_brak danych_

### Czasy odpowiedzi

| Miara | ms |
|---|---:|
| p50 | 3 823 |
| p95 | 26 844 |
| maksimum | 47 407 |

### Strefy planistyczne

| Sekcja | Działki z ≥ 1 strefą | Suma stref | HTTP 2xx bez sekcji (`null`) |
|---|---:|---:|---:|
| MPZP (`mpzp_zones`) | 4 | 5 | 0 |
| POG (`pog.zones`) | 0 | 0 | 0 |

Brak sekcji (`null`) nie jest zerem stref, a brak stref nie dowodzi braku ograniczenia — zob. statusy źródeł w odpowiedzi API.

### Kompletność analizy (`completeness`, HTTP 2xx; AU-104)

Liczba sprawdzonych sekcji w zakresie analizy (status `complete` ⇔ wszystkie):

| sprawdzono | Przypadki |
|---|---:|
| `4 z 10` | 15 |
| `3 z 10` | 13 |
| `2 z 10` | 2 |

Sekcje, których brakowało (liczba analiz):

| sekcja | Przypadki |
|---|---:|
| `buildings` | 30 |
| `mpzp_pog_relation` | 30 |
| `pog` | 30 |
| `pog_overlays` | 30 |
| `transport` | 30 |
| `mpzp` | 26 |
| `flood` | 18 |
| `nature` | 3 |

Podstawa ustalenia MPZP (analizy ze sprawdzoną sekcją MPZP):

| podstawa | Przypadki |
|---|---:|
| `document_candidate` | 3 |
| `no_match` | 1 |

### Droga i zabudowa sąsiednia (`road_context`, `neighbour_buildings`; BDOT10k, HTTP 2xx)

Statusy rozłączne: `available`, `no_match` (pusty ograniczony zasięg — nie dowód braku drogi), `no_coverage` (powiat bez zaimportowanego wydania — nie brak drogi ani budynków) i `unavailable` (awaria zapytania).

Droga (`road_context.status`):

| status | Przypadki |
|---|---:|
| `no_coverage (NO_ACTIVE_RELEASE)` | 30 |

Zabudowa sąsiednia (`neighbour_buildings.status`):

| status | Przypadki |
|---|---:|
| `no_coverage (NO_ACTIVE_RELEASE)` | 30 |

### Ostrzeżenia (`warnings[].code`, HTTP 2xx)

| kod | Przypadki |
|---|---:|
| `KIUT_WARNING` | 30 |
| `MPZP_WARNING` | 30 |
| `NEIGHBOUR_BUILDINGS_NO_ACTIVE_RELEASE` | 30 |
| `NO_LOCAL_RELEASE` | 30 |
| `OUZ_DATA_UNAVAILABLE` | 30 |
| `POG_DISCOVERY_ONLY` | 30 |
| `POG_RU_CATALOG_SOURCE` | 30 |
| `ROAD_CONTEXT_NO_ACTIVE_RELEASE` | 30 |
| `ISOK_WARNING` | 18 |
| `POG_EMPTY_RESPONSE_NOT_ABSENCE` | 18 |
| `POG_NOT_FOUND_IN_SOURCE` | 18 |
| `POG_SCENARIO_UNKNOWN` | 18 |
| `MPZP_DOCUMENT_OR_SYMBOL_MISSING` | 14 |
| `MPZP_POG_INPUT_INCOMPLETE` | 12 |
| `POG_COVERAGE_PARTIAL` | 12 |
| `POG_VECTOR_LINK_MISSING` | 12 |
| `MPZP_DISCOVERY_UNAVAILABLE` | 11 |
| `POG_IMPORT_PENDING` | 10 |
| `NMT_CROSSCHECK_DIFF` | 7 |
| `MPZP_VECTOR_UNAVAILABLE` | 4 |
| `GDOS_WARNING` | 3 |
| `MPZP_PARAMETERS_NOT_IN_FLAT_CONTRACT` | 3 |
| `ZONE_SECTION_AMBIGUOUS` | 3 |
| `NMT_WARNING` | 2 |
| `POG_IMPORT_QUEUED` | 2 |
| `MPZP_NOT_FOUND` | 1 |
| `PARAMETER_CONFLICT` | 1 |

## Zmiany względem poprzedniego przebiegu

Poprzedni przebieg: 2026-10-17T16:39:59.678650Z (commit `unknown`). Bez zmian: 30 przypadków.

| Wskaźnik | Poprzednio | Teraz |
|---|---:|---:|
| Odpowiedzi 5xx | 0 | 0 |
| Brak odpowiedzi | 0 | 0 |
| Udane analizy (2xx) | 30 | 30 |
| Analizy `complete` | 0 | 0 |
| Działki z ≥ 1 strefą MPZP | 4 | 4 |
| Działki z ≥ 1 strefą POG | 0 | 0 |
| Czas p50 [ms] | 49266.35 | 3823.4 |
| Czas p95 [ms] | 64703.82999999997 | 26844.01999999999 |

**Kompletność: liczba sprawdzonych sekcji (z liczby sekcji w zakresie)**

| wartość | Poprzednio | Teraz |
|---|---:|---:|
| `2 z 10` | 2 | 2 |
| `3 z 10` | 19 | 13 |
| `4 z 10` | 9 | 15 |

**Kompletność: sekcje, których brakowało**

| wartość | Poprzednio | Teraz |
|---|---:|---:|
| `buildings` | 30 | 30 |
| `flood` | 24 | 18 |
| `mpzp` | 26 | 26 |
| `mpzp_pog_relation` | 30 | 30 |
| `nature` | 3 | 3 |
| `pog` | 30 | 30 |
| `pog_overlays` | 30 | 30 |
| `transport` | 30 | 30 |

**Droga (`road_context.status`)**

| wartość | Poprzednio | Teraz |
|---|---:|---:|
| `no_coverage (NO_ACTIVE_RELEASE)` | 30 | 30 |

**Zabudowa sąsiednia (`neighbour_buildings.status`)**

| wartość | Poprzednio | Teraz |
|---|---:|---:|
| `no_coverage (NO_ACTIVE_RELEASE)` | 30 | 30 |

### Nowe regresje (0)

_brak_

### Naprawy (0)

_brak_

## Wyniki per działka

| # | case_id | Identyfikator działki | HTTP | status | kod błędu | MPZP | POG | czas [ms] | próby | request_id |
|---:|---|---|---:|---|---|---:|---:|---:|---:|---|
| 1 | `real-001-146510-8-0502-1-3` | `146510_8.0502.1/3` | 200 | partial | — | 0 | 0 | 3 159 | 1 | `live-smoke-20261017195908-001` |
| 2 | `real-002-146510-8-0310-82-2` | `146510_8.0310.82/2` | 200 | partial | — | 0 | 0 | 2 972 | 1 | `live-smoke-20261017195908-002` |
| 3 | `real-003-146510-8-0504-1` | `146510_8.0504.1` | 200 | partial | — | 0 | 0 | 2 980 | 1 | `live-smoke-20261017195908-003` |
| 4 | `real-004-146510-8-0309-1-4` | `146510_8.0309.1/4` | 200 | partial | — | 0 | 0 | 2 737 | 1 | `live-smoke-20261017195908-004` |
| 5 | `real-005-126105-9-0001-580-4` | `126105_9.0001.580/4` | 200 | partial | — | 1 | 0 | 24 440 | 1 | `live-smoke-20261017195908-005` |
| 6 | `real-006-126105-9-0001-49-2` | `126105_9.0001.49/2` | 200 | partial | — | 1 | 0 | 17 305 | 1 | `live-smoke-20261017195908-006` |
| 7 | `real-007-126105-9-0001-540-15` | `126105_9.0001.540/15` | 200 | partial | — | 2 | 0 | 28 811 | 1 | `live-smoke-20261017195908-007` |
| 8 | `real-008-126105-9-0119-181-10` | `126105_9.0119.181/10` | 200 | partial | — | 1 | 0 | 47 407 | 1 | `live-smoke-20261017195908-008` |
| 9 | `real-009-246101-1-0056-155-3` | `246101_1.0056.155/3` | 200 | partial | — | 0 | 0 | 8 645 | 1 | `live-smoke-20261017195908-009` |
| 10 | `real-010-246101-1-0055-68-13` | `246101_1.0055.68/13` | 200 | partial | — | 0 | 0 | 2 056 | 1 | `live-smoke-20261017195908-010` |
| 11 | `real-011-246101-1-0081-7` | `246101_1.0081.7` | 200 | partial | — | 0 | 0 | 3 058 | 1 | `live-smoke-20261017195908-011` |
| 12 | `real-012-246101-1-0056-110-1` | `246101_1.0056.110/1` | 200 | partial | — | 0 | 0 | 3 042 | 1 | `live-smoke-20261017195908-012` |
| 13 | `real-013-026201-1-0009-1319-4` | `026201_1.0009.1319/4` | 200 | partial | — | 0 | 0 | 3 946 | 1 | `live-smoke-20261017195908-013` |
| 14 | `real-014-026201-1-0010-410-1` | `026201_1.0010.410/1` | 200 | partial | — | 0 | 0 | 3 700 | 1 | `live-smoke-20261017195908-014` |
| 15 | `real-015-026201-1-0015-530` | `026201_1.0015.530` | 200 | partial | — | 0 | 0 | 5 330 | 1 | `live-smoke-20261017195908-015` |
| 16 | `real-016-026201-1-0009-579-9` | `026201_1.0009.579/9` | 200 | partial | — | 0 | 0 | 5 595 | 1 | `live-smoke-20261017195908-016` |
| 17 | `real-017-281603-4-0001-496-5` | `281603_4.0001.496/5` | 200 | partial | — | 0 | 0 | 7 445 | 1 | `live-smoke-20261017195908-017` |
| 18 | `real-018-281603-4-0001-358` | `281603_4.0001.358` | 200 | partial | — | 0 | 0 | 2 675 | 1 | `live-smoke-20261017195908-018` |
| 19 | `real-019-281603-4-0001-740` | `281603_4.0001.740` | 200 | partial | — | 0 | 0 | 4 851 | 1 | `live-smoke-20261017195908-019` |
| 20 | `real-020-281603-4-0002-130` | `281603_4.0002.130` | 200 | partial | — | 0 | 0 | 11 264 | 1 | `live-smoke-20261017195908-020` |
| 21 | `real-021-022104-2-0002-352` | `022104_2.0002.352` | 200 | partial | — | 0 | 0 | 10 670 | 1 | `live-smoke-20261017195908-021` |
| 22 | `real-022-022104-2-0002-288-20` | `022104_2.0002.288/20` | 200 | partial | — | 0 | 0 | 6 934 | 1 | `live-smoke-20261017195908-022` |
| 23 | `real-023-022104-2-0002-776` | `022104_2.0002.776` | 200 | partial | — | 0 | 0 | 3 462 | 1 | `live-smoke-20261017195908-023` |
| 24 | `real-024-022104-2-0002-230-2` | `022104_2.0002.230/2` | 200 | partial | — | 0 | 0 | 6 438 | 1 | `live-smoke-20261017195908-024` |
| 25 | `real-025-281603-4-0001-431-66` | `281603_4.0001.431/66` | 200 | partial | — | 0 | 0 | 5 189 | 1 | `live-smoke-20261017195908-025` |
| 26 | `real-026-281604-5-0011-107` | `281604_5.0011.107` | 200 | partial | — | 0 | 0 | 3 117 | 1 | `live-smoke-20261017195908-026` |
| 27 | `real-027-246101-1-0001-1043` | `246101_1.0001.1043` | 200 | partial | — | 0 | 0 | 3 163 | 1 | `live-smoke-20261017195908-027` |
| 28 | `real-028-246101-1-0004-737-26` | `246101_1.0004.737/26` | 200 | partial | — | 0 | 0 | 2 715 | 1 | `live-smoke-20261017195908-028` |
| 29 | `real-029-246101-1-0004-741-132` | `246101_1.0004.741/132` | 200 | partial | — | 0 | 0 | 2 017 | 1 | `live-smoke-20261017195908-029` |
| 30 | `real-030-026201-1-0009-578-2` | `026201_1.0009.578/2` | 200 | partial | — | 0 | 0 | 3 466 | 1 | `live-smoke-20261017195908-030` |

`MPZP`/`POG` — liczba stref w odpowiedzi; `—` oznacza brak sekcji albo błąd (nie zero). Czas dotyczy ostatniej próby, bez oczekiwania na `Retry-After`.
