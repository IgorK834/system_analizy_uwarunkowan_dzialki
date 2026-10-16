# Live smoke korpusu referencyjnego — 2026-10-13

Przebieg korpusu referencyjnego (6 działek) na żywych usługach (`POST /analyze`), bez zamrożonych obserwacji — różnica względem ewaluacji offline (`evaluate_reference_corpus.py`, tryb `offline`) jest wynikiem tej pracy. W raporcie nie ma tokenów dostępu ani pełnych odpowiedzi.

**Wynik: PASS** — próg: 0 odpowiedzi 5xx i 0 braków odpowiedzi. 5xx: **0**, brak odpowiedzi: **0**, `complete`: **0** z 6 udanych analiz (HTTP 2xx).

## Metadane

| Pole | Wartość |
|---|---|
| Adres API | `http://127.0.0.1:18000` |
| Korpus | `BK-002-real-parcels-2026-09-23` (`sha256:65f16dc25c4fcde8…`) |
| Commit | `fcf6a9b52f134124a2c4feb7aa8e12493abfe22c` + niezacommitowane zmiany |
| Start / koniec (UTC) | 2026-10-13T10:07:36Z / 2026-10-13T10:08:27Z (50.8 s) |
| Równoległość / limit czasu żądania | 3 / 180 s |
| `force_refresh` | false |
| Ponowienia po HTTP 429 (`Retry-After`) | 0 |

## Podsumowanie

### Statusy HTTP

| HTTP | Przypadki |
|---|---:|
| `200` | 6 |

### Status analizy (`status`, tylko HTTP 2xx)

| status | Przypadki |
|---|---:|
| `partial` | 6 |

### Kody błędów (`ErrorResponse.error`)

_brak danych_

### Czasy odpowiedzi

| Miara | ms |
|---|---:|
| p50 | 21 408 |
| p95 | 27 528 |
| maksimum | 29 445 |

### Strefy planistyczne

| Sekcja | Działki z ≥ 1 strefą | Suma stref | HTTP 2xx bez sekcji (`null`) |
|---|---:|---:|---:|
| MPZP (`mpzp_zones`) | 2 | 2 | 0 |
| POG (`pog.zones`) | 0 | 0 | 0 |

Brak sekcji (`null`) nie jest zerem stref, a brak stref nie dowodzi braku ograniczenia — zob. statusy źródeł w odpowiedzi API.

### Kompletność analizy (`completeness`, HTTP 2xx; AU-104)

Liczba sprawdzonych sekcji w zakresie analizy (status `complete` ⇔ wszystkie):

| sprawdzono | Przypadki |
|---|---:|
| `3 z 10` | 4 |
| `4 z 10` | 2 |

Sekcje, których brakowało (liczba analiz):

| sekcja | Przypadki |
|---|---:|
| `buildings` | 6 |
| `mpzp_pog_relation` | 6 |
| `pog` | 6 |
| `pog_overlays` | 6 |
| `transport` | 6 |
| `mpzp` | 4 |
| `terrain` | 4 |
| `flood` | 2 |

Podstawa ustalenia MPZP (analizy ze sprawdzoną sekcją MPZP):

| podstawa | Przypadki |
|---|---:|
| `document_candidate` | 2 |

### Droga i zabudowa sąsiednia (`road_context`, `neighbour_buildings`; BDOT10k, HTTP 2xx)

Statusy rozłączne: `available`, `no_match` (pusty ograniczony zasięg — nie dowód braku drogi), `no_coverage` (powiat bez zaimportowanego wydania — nie brak drogi ani budynków) i `unavailable` (awaria zapytania).

Droga (`road_context.status`):

| status | Przypadki |
|---|---:|
| `no_coverage (NO_ACTIVE_RELEASE)` | 6 |

Zabudowa sąsiednia (`neighbour_buildings.status`):

| status | Przypadki |
|---|---:|
| `no_coverage (NO_ACTIVE_RELEASE)` | 6 |

### Ostrzeżenia (`warnings[].code`, HTTP 2xx)

| kod | Przypadki |
|---|---:|
| `KIUT_WARNING` | 6 |
| `MPZP_WARNING` | 6 |
| `NEIGHBOUR_BUILDINGS_NO_ACTIVE_RELEASE` | 6 |
| `OUZ_DATA_UNAVAILABLE` | 6 |
| `POG_DISCOVERY_ONLY` | 6 |
| `POG_EMPTY_RESPONSE_NOT_ABSENCE` | 6 |
| `POG_NOT_FOUND_IN_SOURCE` | 6 |
| `POG_RU_CATALOG_SOURCE` | 6 |
| `POG_SCENARIO_UNKNOWN` | 6 |
| `ROAD_CONTEXT_NO_ACTIVE_RELEASE` | 6 |
| `MPZP_DISCOVERY_UNAVAILABLE` | 4 |
| `NMT_RELIEF_WARNING` | 4 |
| `ISOK_WARNING` | 2 |
| `MPZP_PARAMETERS_NOT_IN_FLAT_CONTRACT` | 2 |
| `MPZP_VECTOR_UNAVAILABLE` | 2 |
| `ZONE_SECTION_AMBIGUOUS` | 1 |

## Zmiany względem poprzedniego przebiegu

Brak poprzedniego przebiegu do porównania — ten raport jest punktem odniesienia.

## Wyniki per działka

| # | case_id | Identyfikator działki | HTTP | status | kod błędu | MPZP | POG | czas [ms] | próby | request_id |
|---:|---|---|---:|---|---|---:|---:|---:|---:|---|
| 1 | `real-001-146510-8-0502-1-3` | `146510_8.0502.1/3` | 200 | partial | — | 0 | 0 | 11 145 | 1 | `live-smoke-20261013100736-001` |
| 2 | `real-002-146510-8-0310-82-2` | `146510_8.0310.82/2` | 200 | partial | — | 0 | 0 | 21 354 | 1 | `live-smoke-20261013100736-002` |
| 3 | `real-003-146510-8-0504-1` | `146510_8.0504.1` | 200 | partial | — | 0 | 0 | 21 368 | 1 | `live-smoke-20261013100736-003` |
| 4 | `real-004-146510-8-0309-1-4` | `146510_8.0309.1/4` | 200 | partial | — | 0 | 0 | 21 774 | 1 | `live-smoke-20261013100736-004` |
| 5 | `real-005-126105-9-0001-580-4` | `126105_9.0001.580/4` | 200 | partial | — | 1 | 0 | 29 445 | 1 | `live-smoke-20261013100736-005` |
| 6 | `real-006-126105-9-0001-49-2` | `126105_9.0001.49/2` | 200 | partial | — | 1 | 0 | 21 448 | 1 | `live-smoke-20261013100736-006` |

`MPZP`/`POG` — liczba stref w odpowiedzi; `—` oznacza brak sekcji albo błąd (nie zero). Czas dotyczy ostatniej próby, bez oczekiwania na `Retry-After`.
