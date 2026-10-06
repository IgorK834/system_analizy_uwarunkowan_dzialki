# Analiza błędów korpusu referencyjnego (BK-601)

Dokument jest generowany przez `evaluate_reference_corpus.py --study` z `error_ledger.json` (`substantive_sha256` `a9dd98d131db79d8b00bbd5f1b5c84050cc216c2b5442efcbaf82bf0faf57495`, `manifest_sha256` `f8d42cb487fce424a11ded34b026538d669e04b8c52efd0b02f64a849becfcd1`, commit `cbefc0a22ba65bbcdfd205132ebd8070c34120db`). Nie jest edytowany ręcznie.

## Jak czytać wynik

Metryki nagłówkowe BK-004 (accuracy klas stref, MAE udziałów, confusion matrix) zostały policzone na kontrakcie, w którym runner odczytuje zamrożone obserwacje. Dla udziałów stref POG/MPZP/OUZ oraz wartości NMT jest to **odczyt obserwacji**, nie niezależne przecięcie, więc wartość 1,0 lub 0 pp potwierdza wierność odczytu, a nie poprawność obliczeń. Poprawność obliczeń potwierdzają tylko pola `production_computation` (pole i obwód działki) oraz mapowanie kontraktu ryzyk. Zakres każdej metryki jest w `report.md`.

Badanie nie usunęło żadnego trudnego przypadku: wszystkie 30 wejść są w rejestrze i w mianownikach.

## Podsumowanie

- wpisów w rejestrze: 107 (rozbieżności i awarie: 30, ograniczenia: 77);
- zgodność pól: 0.973631 (480/493); zgodność statusów: 0.990476 (208/210);
- fałszywa pewność: 0.050336 (15/298);
- rozbieżności udziału > 0.5 pp: 0 (z 49 porównań udziałów).

## Przyczyny źródłowe

| Przyczyna | Kategoria | Dyspozycja | Wpisów | Przypadków |
|---|---|---|---:|---:|
| `RC-01` Literał NULL z KIMPZP GetFeatureInfo traktowany jak symbol strefy | `parser` | `open_defect` | 25 | 8 |
| `RC-02` Klasy terenu flat/moderate/relief bez definicji w protokole i kodzie produkcyjnym | `presentation` | `definition_gap` | 1 | 1 |
| `RC-03` Niespójna etykieta statusu MPZP w korpusie dla tej samej metody pomiaru | `data` | `ground_truth_inconsistency` | 2 | 1 |
| `RC-04` Cecha ISOK styczna do granicy opisana w ground truth, nieobecna w zamrożonej obserwacji | `data` | `unresolved_hypothesis` | 2 | 1 |
| `RC-10` Brak zweryfikowanej lokalnej geometrii POG dla gminy | `source` | `expected_limitation` | 23 | 23 |
| `RC-11` Brak zweryfikowanej geometrii OUZ | `source` | `expected_limitation` | 23 | 23 |
| `RC-12` KIMPZP nie zwróciło planu miejscowego dla próbki punktów działki | `source` | `expected_limitation` | 12 | 12 |
| `RC-13` MPZP tylko jako discovery lub raster: udziałów stref nie da się ustalić z wektora | `source` | `expected_limitation` | 14 | 14 |
| `RC-14` Przejściowy błąd usługi GDOŚ podczas zbierania obserwacji | `source` | `expected_limitation` | 2 | 2 |
| `RC-15` Limit powierzchni poligonu NMT (100 000 m²) lub brak pokrycia | `source` | `expected_limitation` | 2 | 2 |
| `RC-16` Przecięcie OUZ mniejsze niż tolerancja ręcznego pomiaru | `geometry` | `expected_limitation` | 1 | 1 |

Kategorie: `source` (usługa lub jej brak pokrycia), `data` (zamrożona obserwacja lub etykieta ground truth), `geometry` (tolerancja lub topologia), `parser` (odczyt odpowiedzi/dokumentu), `presentation` (definicja lub reprezentacja wyniku widoczna dla użytkownika). Kategoria wynika z reguły (`basis`) i dowodu; tam, gdzie offline nie da się rozstrzygnąć przyczyny, dyspozycja to `unresolved_hypothesis`.

### RC-01 — Literał NULL z KIMPZP GetFeatureInfo traktowany jak symbol strefy

Kategoria `parser`, dyspozycja `open_defect`.

`discover_mpzp` przyjmuje każdy niepusty napis z atrybutu symbolu (`_first_matching_attribute`) jako symbol strefy. Dla planów rastrowych KIMPZP zwraca napis `NULL`; trafia on do `candidate_zone_symbols`, sekcja dostaje tryb `vector_discovery` i liczbę stref większą o jeden, a ground truth klasyfikuje taki przypadek jako `raster_manual`.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-044` | `real-017-281603-4-0001-496-5` | mpzp | mode | `"raster_manual"` | `"vector_discovery"` |
| `E-045` | `real-017-281603-4-0001-496-5` | mpzp | zone_count | `null` | `1` |
| `E-046` | `real-017-281603-4-0001-496-5` | mpzp | zones.symbols | `null` | `["NULL"]` |
| `E-050` | `real-018-281603-4-0001-358` | mpzp | mode | `"raster_manual"` | `"vector_discovery"` |
| `E-051` | `real-018-281603-4-0001-358` | mpzp | zone_count | `null` | `1` |
| `E-052` | `real-018-281603-4-0001-358` | mpzp | zones.symbols | `null` | `["NULL"]` |
| `E-056` | `real-019-281603-4-0001-740` | mpzp | mode | `"raster_manual"` | `"vector_discovery"` |
| `E-057` | `real-019-281603-4-0001-740` | mpzp | zone_count | `null` | `1` |
| `E-058` | `real-019-281603-4-0001-740` | mpzp | zones.symbols | `null` | `["NULL"]` |
| `E-065` | `real-021-022104-2-0002-352` | mpzp | mode | `"raster_manual"` | `"vector_discovery"` |
| `E-066` | `real-021-022104-2-0002-352` | mpzp | zone_count | `null` | `1` |
| `E-067` | `real-021-022104-2-0002-352` | mpzp | zones.symbols | `null` | `["NULL"]` |
| `E-071` | `real-022-022104-2-0002-288-20` | mpzp | mode | `"raster_manual"` | `"vector_discovery"` |
| `E-072` | `real-022-022104-2-0002-288-20` | mpzp | zone_count | `null` | `1` |
| `E-073` | `real-022-022104-2-0002-288-20` | mpzp | zones.symbols | `null` | `["NULL"]` |
| `E-077` | `real-023-022104-2-0002-776` | mpzp | mode | `"raster_manual"` | `"vector_discovery"` |
| `E-078` | `real-023-022104-2-0002-776` | mpzp | zone_count | `null` | `1` |
| `E-079` | `real-023-022104-2-0002-776` | mpzp | zones.symbols | `null` | `["NULL"]` |
| `E-083` | `real-024-022104-2-0002-230-2` | mpzp | mode | `"raster_manual"` | `"vector_discovery"` |
| `E-084` | `real-024-022104-2-0002-230-2` | mpzp | zone_count | `null` | `1` |
| `E-085` | `real-024-022104-2-0002-230-2` | mpzp | zones.symbols | `null` | `["NULL"]` |
| `E-089` | `real-025-281603-4-0001-431-66` | mpzp | mode | `"raster_manual"` | `"vector_discovery"` |
| `E-090` | `real-025-281603-4-0001-431-66` | mpzp | zone_count | `2` | `3` |
| `E-091` | `real-025-281603-4-0001-431-66` | mpzp | zones.symbols | `["15.U","16.U/US"]` | `["15.U","16.U/US","NULL"]` |
| `E-092` | `real-025-281603-4-0001-431-66` | mpzp | zones[NULL].presence | `null` | `"NULL"` |

### RC-02 — Klasy terenu flat/moderate/relief bez definicji w protokole i kodzie produkcyjnym

Kategoria `presentation`, dyspozycja `definition_gap`.

Kod produkcyjny ma wyłącznie klasy spadku w procentach (`slope-classes-pl-v1`), a protokół BK-003 nie definiuje klas deniwelacji. Harness przyjmuje progi 2 m i 10 m, natomiast etykiety korpusu dzielą przypadki inaczej (9,0 m = `relief`, 7,2 m = `moderate`). Wartości liczbowe są zgodne; różni się etykieta pochodna.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-029` | `real-012-246101-1-0056-110-1` | terrain | class | `"relief"` | `"moderate"` |

### RC-03 — Niespójna etykieta statusu MPZP w korpusie dla tej samej metody pomiaru

Kategoria `data`, dyspozycja `ground_truth_inconsistency`.

Przypadki Krakowa real-005…008 mają ten sam rodzaj dowodu wektorowego (symbol, pole, udział) i równoważne zastrzeżenie, że interpretacja parametrów tekstowych wymaga aktu BIP. Mimo to real-008 ma w korpusie status `manual_review` i tryb `document_parse`, a real-005…007 — `available` i `vector`. Harness nie odtwarza rozróżnienia, bo dane nie zawierają kryterium, które je uzasadnia.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-022` | `real-008-126105-9-0119-181-10` | mpzp | mode | `"document_parse"` | `"vector"` |
| `E-023` | `real-008-126105-9-0119-181-10` | mpzp | status | `"manual_review"` | `"available"` |

### RC-04 — Cecha ISOK styczna do granicy opisana w ground truth, nieobecna w zamrożonej obserwacji

Kategoria `data`, dyspozycja `unresolved_hypothesis`.

Ground truth real-030 opisuje styk z granicą jednej cechy Q0,2% (`boundary_feature_area=0`, relacja `intersection_and_boundary`). Zamrożona obserwacja ISOK ma cztery cechy o dodatnim polu i żadnej cechy stycznej, więc relacja to `intersection`. Offline nie da się rozstrzygnąć, czy styk pominął adapter przy zbieraniu obserwacji, czy został wykryty wyłącznie osobną sondą ground truth.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-106` | `real-030-026201-1-0009-578-2` | flood | relation | `"intersection_and_boundary"` | `"intersection"` |
| `E-107` | `real-030-026201-1-0009-578-2` | flood | status | `"manual_review"` | `"available"` |

### RC-10 — Brak zweryfikowanej lokalnej geometrii POG dla gminy

Kategoria `source`, dyspozycja `expected_limitation`.

Korpus nie zawiera zweryfikowanej lokalnej geometrii POG dla tej gminy. Z danych offline nie wynika, czy RU nie publikuje aktu, czy geometrii; `unknown` nie jest dowodem braku aktu ani braku ograniczeń.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-001` | `real-001-146510-8-0502-1-3` | pog | status | `"unknown"` | `"unknown"` |
| `E-004` | `real-002-146510-8-0310-82-2` | pog | status | `"unknown"` | `"unknown"` |
| `E-007` | `real-003-146510-8-0504-1` | pog | status | `"unknown"` | `"unknown"` |
| `E-011` | `real-004-146510-8-0309-1-4` | pog | status | `"unknown"` | `"unknown"` |
| `E-014` | `real-005-126105-9-0001-580-4` | pog | status | `"unknown"` | `"unknown"` |
| `E-016` | `real-006-126105-9-0001-49-2` | pog | status | `"unknown"` | `"unknown"` |
| `E-018` | `real-007-126105-9-0001-540-15` | pog | status | `"unknown"` | `"unknown"` |
| `E-020` | `real-008-126105-9-0119-181-10` | pog | status | `"unknown"` | `"unknown"` |
| `E-030` | `real-013-026201-1-0009-1319-4` | pog | status | `"unknown"` | `"unknown"` |
| `E-033` | `real-014-026201-1-0010-410-1` | pog | status | `"unknown"` | `"unknown"` |
| `E-036` | `real-015-026201-1-0015-530` | pog | status | `"unknown"` | `"unknown"` |
| `E-039` | `real-016-026201-1-0009-579-9` | pog | status | `"unknown"` | `"unknown"` |
| `E-042` | `real-017-281603-4-0001-496-5` | pog | status | `"unknown"` | `"unknown"` |
| `E-048` | `real-018-281603-4-0001-358` | pog | status | `"unknown"` | `"unknown"` |
| `E-054` | `real-019-281603-4-0001-740` | pog | status | `"unknown"` | `"unknown"` |
| `E-060` | `real-020-281603-4-0002-130` | pog | status | `"unknown"` | `"unknown"` |
| `E-063` | `real-021-022104-2-0002-352` | pog | status | `"unknown"` | `"unknown"` |
| `E-069` | `real-022-022104-2-0002-288-20` | pog | status | `"unknown"` | `"unknown"` |
| `E-075` | `real-023-022104-2-0002-776` | pog | status | `"unknown"` | `"unknown"` |
| `E-081` | `real-024-022104-2-0002-230-2` | pog | status | `"unknown"` | `"unknown"` |
| `E-087` | `real-025-281603-4-0001-431-66` | pog | status | `"unknown"` | `"unknown"` |
| `E-095` | `real-026-281604-5-0011-107` | pog | status | `"unknown"` | `"unknown"` |
| `E-103` | `real-030-026201-1-0009-578-2` | pog | status | `"unknown"` | `"unknown"` |

### RC-11 — Brak zweryfikowanej geometrii OUZ

Kategoria `source`, dyspozycja `expected_limitation`.

Bez zweryfikowanej geometrii POG/OUZ relacja działki z OUZ pozostaje nieznana.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-002` | `real-001-146510-8-0502-1-3` | ouz | status | `"unknown"` | `"unknown"` |
| `E-005` | `real-002-146510-8-0310-82-2` | ouz | status | `"unknown"` | `"unknown"` |
| `E-008` | `real-003-146510-8-0504-1` | ouz | status | `"unknown"` | `"unknown"` |
| `E-012` | `real-004-146510-8-0309-1-4` | ouz | status | `"unknown"` | `"unknown"` |
| `E-015` | `real-005-126105-9-0001-580-4` | ouz | status | `"unknown"` | `"unknown"` |
| `E-017` | `real-006-126105-9-0001-49-2` | ouz | status | `"unknown"` | `"unknown"` |
| `E-019` | `real-007-126105-9-0001-540-15` | ouz | status | `"unknown"` | `"unknown"` |
| `E-021` | `real-008-126105-9-0119-181-10` | ouz | status | `"unknown"` | `"unknown"` |
| `E-031` | `real-013-026201-1-0009-1319-4` | ouz | status | `"unknown"` | `"unknown"` |
| `E-034` | `real-014-026201-1-0010-410-1` | ouz | status | `"unknown"` | `"unknown"` |
| `E-037` | `real-015-026201-1-0015-530` | ouz | status | `"unknown"` | `"unknown"` |
| `E-040` | `real-016-026201-1-0009-579-9` | ouz | status | `"unknown"` | `"unknown"` |
| `E-043` | `real-017-281603-4-0001-496-5` | ouz | status | `"unknown"` | `"unknown"` |
| `E-049` | `real-018-281603-4-0001-358` | ouz | status | `"unknown"` | `"unknown"` |
| `E-055` | `real-019-281603-4-0001-740` | ouz | status | `"unknown"` | `"unknown"` |
| `E-061` | `real-020-281603-4-0002-130` | ouz | status | `"unknown"` | `"unknown"` |
| `E-064` | `real-021-022104-2-0002-352` | ouz | status | `"unknown"` | `"unknown"` |
| `E-070` | `real-022-022104-2-0002-288-20` | ouz | status | `"unknown"` | `"unknown"` |
| `E-076` | `real-023-022104-2-0002-776` | ouz | status | `"unknown"` | `"unknown"` |
| `E-082` | `real-024-022104-2-0002-230-2` | ouz | status | `"unknown"` | `"unknown"` |
| `E-088` | `real-025-281603-4-0001-431-66` | ouz | status | `"unknown"` | `"unknown"` |
| `E-096` | `real-026-281604-5-0011-107` | ouz | status | `"unknown"` | `"unknown"` |
| `E-104` | `real-030-026201-1-0009-578-2` | ouz | status | `"unknown"` | `"unknown"` |

### RC-12 — KIMPZP nie zwróciło planu miejscowego dla próbki punktów działki

Kategoria `source`, dyspozycja `expected_limitation`.

Discovery na próbce punktów nie znalazło planu. To nie dowodzi braku ograniczeń, tylko braku danych z tego źródła.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-003` | `real-001-146510-8-0502-1-3` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-006` | `real-002-146510-8-0310-82-2` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-009` | `real-003-146510-8-0504-1` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-013` | `real-004-146510-8-0309-1-4` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-025` | `real-009-246101-1-0056-155-3` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-026` | `real-010-246101-1-0055-68-13` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-027` | `real-011-246101-1-0081-7` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-028` | `real-012-246101-1-0056-110-1` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-097` | `real-026-281604-5-0011-107` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-100` | `real-027-246101-1-0001-1043` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-101` | `real-028-246101-1-0004-737-26` | mpzp | status | `"unknown"` | `"unknown"` |
| `E-102` | `real-029-246101-1-0004-741-132` | mpzp | status | `"unknown"` | `"unknown"` |

### RC-13 — MPZP tylko jako discovery lub raster: udziałów stref nie da się ustalić z wektora

Kategoria `source`, dyspozycja `expected_limitation`.

Źródło nie udostępnia wektora stref dla działki; symbole są kandydatami z GetFeatureInfo, a udział stref pozostaje `null` do ręcznej kontroli dokumentu i rysunku.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-032` | `real-013-026201-1-0009-1319-4` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-035` | `real-014-026201-1-0010-410-1` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-038` | `real-015-026201-1-0015-530` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-041` | `real-016-026201-1-0009-579-9` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-047` | `real-017-281603-4-0001-496-5` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-053` | `real-018-281603-4-0001-358` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-059` | `real-019-281603-4-0001-740` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-062` | `real-020-281603-4-0002-130` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-068` | `real-021-022104-2-0002-352` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-074` | `real-022-022104-2-0002-288-20` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-080` | `real-023-022104-2-0002-776` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-086` | `real-024-022104-2-0002-230-2` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-093` | `real-025-281603-4-0001-431-66` | mpzp | status | `"manual_review"` | `"manual_review"` |
| `E-105` | `real-030-026201-1-0009-578-2` | mpzp | status | `"manual_review"` | `"manual_review"` |

### RC-14 — Przejściowy błąd usługi GDOŚ podczas zbierania obserwacji

Kategoria `source`, dyspozycja `expected_limitation`.

Usługa zgłosiła błąd; wynik zachowano jako `unknown`, nie jako brak obszaru chronionego.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-010` | `real-003-146510-8-0504-1` | nature | status | `"unknown"` | `"unknown"` |
| `E-024` | `real-008-126105-9-0119-181-10` | nature | status | `"unknown"` | `"unknown"` |

### RC-15 — Limit powierzchni poligonu NMT (100 000 m²) lub brak pokrycia

Kategoria `source`, dyspozycja `expected_limitation`.

Usługa NMT GetMinMaxByPolygon odrzuca poligony większe niż 100 000 m²; wynik zachowano jako `unknown`.

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-094` | `real-025-281603-4-0001-431-66` | terrain | status | `"unknown"` | `"unknown"` |
| `E-098` | `real-026-281604-5-0011-107` | terrain | status | `"unknown"` | `"unknown"` |

### RC-16 — Przecięcie OUZ mniejsze niż tolerancja ręcznego pomiaru

Kategoria `geometry`, dyspozycja `expected_limitation`.

Ślad 0,158 m² jest mniejszy od tolerancji 1 m²; relacja OUZ jest świadomie niejednoznaczna (`ambiguous` tylko dla `ouz.relation`).

| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |
|---|---|---|---|---|---|
| `E-099` | `real-027-246101-1-0001-1043` | ouz | status | `"manual_review"` | `"manual_review"` |

## Wpisy szczegółowe i dowody

### E-001 — real-001-146510-8-0502-1-3 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/0/expected/pog/status` = `"unknown"`

### E-002 — real-001-146510-8-0502-1-3 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/0/expected/ouz/status` = `"unknown"`

### E-003 — real-001-146510-8-0502-1-3 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-001-146510-8-0502-1-3` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-001-146510-8-0502-1-3.json`, sha256 `937808640ff1…`) = `"no_mpzp"`
  - ground truth `/cases/0/expected/mpzp/status` = `"unknown"`

### E-004 — real-002-146510-8-0310-82-2 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/1/expected/pog/status` = `"unknown"`

### E-005 — real-002-146510-8-0310-82-2 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/1/expected/ouz/status` = `"unknown"`

### E-006 — real-002-146510-8-0310-82-2 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-002-146510-8-0310-82-2` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-002-146510-8-0310-82-2.json`, sha256 `4982a059986f…`) = `"no_mpzp"`
  - ground truth `/cases/1/expected/mpzp/status` = `"unknown"`

### E-007 — real-003-146510-8-0504-1 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/2/expected/pog/status` = `"unknown"`

### E-008 — real-003-146510-8-0504-1 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/2/expected/ouz/status` = `"unknown"`

### E-009 — real-003-146510-8-0504-1 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-003-146510-8-0504-1` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-003-146510-8-0504-1.json`, sha256 `7a46e132f8ae…`) = `"no_mpzp"`
  - ground truth `/cases/2/expected/mpzp/status` = `"unknown"`

### E-010 — real-003-146510-8-0504-1 / nature / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-14`; reguła: `rule:gdos_error`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja GDOŚ `unknown`: Usługa GDOŚ zwróciła błąd: Server error '500 Internal Server Error' for url 'https://sdi.gdos.gov.pl/wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=GDOS%3ARezerwaty&bbox=637536.997121127%2C486371.225874029%2C637790.261896401%2C486472.231730355%2CEPSG%3A2180'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500
- dowody:
  - obserwacja `evidence:real-003-146510-8-0504-1` `/observations/gdos` (`artifacts/evidence/real-003-146510-8-0504-1.json`, sha256 `7a46e132f8ae…`) = `{"error":"Usługa GDOŚ zwróciła błąd: Server error '500 Internal Server Error' for url 'https://sdi.gdos.gov.pl/wfs?service=WFS&version=2.0.0&request=GetFeature&`
  - ground truth `/cases/2/expected/nature/status` = `"unknown"`

### E-011 — real-004-146510-8-0309-1-4 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/3/expected/pog/status` = `"unknown"`

### E-012 — real-004-146510-8-0309-1-4 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/3/expected/ouz/status` = `"unknown"`

### E-013 — real-004-146510-8-0309-1-4 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-004-146510-8-0309-1-4` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-004-146510-8-0309-1-4.json`, sha256 `7b740032617e…`) = `"no_mpzp"`
  - ground truth `/cases/3/expected/mpzp/status` = `"unknown"`

### E-014 — real-005-126105-9-0001-580-4 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/4/expected/pog/status` = `"unknown"`

### E-015 — real-005-126105-9-0001-580-4 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/4/expected/ouz/status` = `"unknown"`

### E-016 — real-006-126105-9-0001-49-2 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/5/expected/pog/status` = `"unknown"`

### E-017 — real-006-126105-9-0001-49-2 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/5/expected/ouz/status` = `"unknown"`

### E-018 — real-007-126105-9-0001-540-15 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/6/expected/pog/status` = `"unknown"`

### E-019 — real-007-126105-9-0001-540-15 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/6/expected/ouz/status` = `"unknown"`

### E-020 — real-008-126105-9-0119-181-10 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/7/expected/pog/status` = `"unknown"`

### E-021 — real-008-126105-9-0119-181-10 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/7/expected/ouz/status` = `"unknown"`

### E-022 — real-008-126105-9-0119-181-10 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `data`; przyczyna: `RC-03`; reguła: `rule:mpzp_status_label_inconsistency;verdict=mismatch`;
- oczekiwano: `"document_parse"`; otrzymano: `"vector"`;
- wyjaśnienie: `mode`: korpus 'document_parse', harness 'vector'; te same dowody wektorowe co w real-005…007, które mają inną etykietę.
- dowody:
  - obserwacja `evidence:real-008-126105-9-0119-181-10` `/observations/krakow_mpzp_derived/layers/0/intersections` (`artifacts/evidence/real-008-126105-9-0119-181-10.json`, sha256 `c49ce8bdaf74…`) = `[{"area_sqm":307.503,"attributes":{"Data_DUWM":"2021-02-24T00:00:00","Data_obowiązywania":"2021-03-11T00:00:00","Data_uchwalenia":"2021-02-18T00:00:00","Data_za`
  - ground truth `/cases/7/expected/mpzp/values/mode` = `"document_parse"`

### E-023 — real-008-126105-9-0119-181-10 / mpzp / status

- rodzaj: `status_disagreement`; kategoria: `data`; przyczyna: `RC-03`; reguła: `rule:mpzp_status_label_inconsistency;status`;
- oczekiwano: `"manual_review"`; otrzymano: `"available"`;
- wyjaśnienie: `status`: korpus 'manual_review', harness 'available'; te same dowody wektorowe co w real-005…007, które mają inną etykietę.
- dowody:
  - obserwacja `evidence:real-008-126105-9-0119-181-10` `/observations/krakow_mpzp_derived/layers/0/intersections` (`artifacts/evidence/real-008-126105-9-0119-181-10.json`, sha256 `c49ce8bdaf74…`) = `[{"area_sqm":307.503,"attributes":{"Data_DUWM":"2021-02-24T00:00:00","Data_obowiązywania":"2021-03-11T00:00:00","Data_uchwalenia":"2021-02-18T00:00:00","Data_za`
  - ground truth `/cases/7/expected/mpzp/status` = `"manual_review"`

### E-024 — real-008-126105-9-0119-181-10 / nature / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-14`; reguła: `rule:gdos_error`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja GDOŚ `unknown`: Nie udało się sparsować odpowiedzi GDOŚ (warstwa GDOS:UzytkiEkologiczne): Usługa zwróciła ows:ExceptionReport: NoApplicableCode; java.util.ConcurrentModificationException
null
- dowody:
  - obserwacja `evidence:real-008-126105-9-0119-181-10` `/observations/gdos` (`artifacts/evidence/real-008-126105-9-0119-181-10.json`, sha256 `c49ce8bdaf74…`) = `{"error":"Nie udało się sparsować odpowiedzi GDOŚ (warstwa GDOS:UzytkiEkologiczne): Usługa zwróciła ows:ExceptionReport: NoApplicableCode; java.util.ConcurrentM`
  - ground truth `/cases/7/expected/nature/status` = `"unknown"`

### E-025 — real-009-246101-1-0056-155-3 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-009-246101-1-0056-155-3` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-009-246101-1-0056-155-3.json`, sha256 `56e18c81bc4e…`) = `"no_mpzp"`
  - ground truth `/cases/8/expected/mpzp/status` = `"unknown"`

### E-026 — real-010-246101-1-0055-68-13 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-010-246101-1-0055-68-13` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-010-246101-1-0055-68-13.json`, sha256 `26db3424ae45…`) = `"no_mpzp"`
  - ground truth `/cases/9/expected/mpzp/status` = `"unknown"`

### E-027 — real-011-246101-1-0081-7 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-011-246101-1-0081-7` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-011-246101-1-0081-7.json`, sha256 `486b74ef740b…`) = `"no_mpzp"`
  - ground truth `/cases/10/expected/mpzp/status` = `"unknown"`

### E-028 — real-012-246101-1-0056-110-1 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-012-246101-1-0056-110-1` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-012-246101-1-0056-110-1.json`, sha256 `1008a06a32b1…`) = `"no_mpzp"`
  - ground truth `/cases/11/expected/mpzp/status` = `"unknown"`

### E-029 — real-012-246101-1-0056-110-1 / terrain / class

- rodzaj: `field_discrepancy`; kategoria: `presentation`; przyczyna: `RC-02`; reguła: `rule:terrain_class_definition;verdict=mismatch`;
- oczekiwano: `"relief"`; otrzymano: `"moderate"`;
- wyjaśnienie: Deniwelacja 9.0 m: korpus `relief`, harness `moderate` (progi harnessu 2 m / 10 m nie są zdefiniowane w protokole).
- dowody:
  - obserwacja `evidence:real-012-246101-1-0056-110-1` `/observations/nmt/relief_m` (`artifacts/evidence/real-012-246101-1-0056-110-1.json`, sha256 `1008a06a32b1…`) = `9.0`
  - ground truth `/cases/11/expected/terrain/values/class` = `"relief"`
  - kod `backend/scripts/evaluate_reference_corpus.py::_terrain_section` — progi klas 2 m i 10 m przyjęte przez harness

### E-030 — real-013-026201-1-0009-1319-4 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/12/expected/pog/status` = `"unknown"`

### E-031 — real-013-026201-1-0009-1319-4 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/12/expected/ouz/status` = `"unknown"`

### E-032 — real-013-026201-1-0009-1319-4 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-013-026201-1-0009-1319-4` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-013-026201-1-0009-1319-4.json`, sha256 `54af1ee6e5c3…`) = `["7 UC,U,M","22 KD G1/2(Z1/4)"]`
  - ground truth `/cases/12/expected/mpzp/status` = `"manual_review"`

### E-033 — real-014-026201-1-0010-410-1 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/13/expected/pog/status` = `"unknown"`

### E-034 — real-014-026201-1-0010-410-1 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/13/expected/ouz/status` = `"unknown"`

### E-035 — real-014-026201-1-0010-410-1 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-014-026201-1-0010-410-1` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-014-026201-1-0010-410-1.json`, sha256 `491a6e7d2d5b…`) = `["MU9.9"]`
  - ground truth `/cases/13/expected/mpzp/status` = `"manual_review"`

### E-036 — real-015-026201-1-0015-530 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/14/expected/pog/status` = `"unknown"`

### E-037 — real-015-026201-1-0015-530 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/14/expected/ouz/status` = `"unknown"`

### E-038 — real-015-026201-1-0015-530 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-015-026201-1-0015-530` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-015-026201-1-0015-530.json`, sha256 `8bf57e78ae88…`) = `["KL 1/2"]`
  - ground truth `/cases/14/expected/mpzp/status` = `"manual_review"`

### E-039 — real-016-026201-1-0009-579-9 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/15/expected/pog/status` = `"unknown"`

### E-040 — real-016-026201-1-0009-579-9 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/15/expected/ouz/status` = `"unknown"`

### E-041 — real-016-026201-1-0009-579-9 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-016-026201-1-0009-579-9` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-016-026201-1-0009-579-9.json`, sha256 `724b87429151…`) = `["13MWU"]`
  - ground truth `/cases/15/expected/mpzp/status` = `"manual_review"`

### E-042 — real-017-281603-4-0001-496-5 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/16/expected/pog/status` = `"unknown"`

### E-043 — real-017-281603-4-0001-496-5 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/16/expected/ouz/status` = `"unknown"`

### E-044 — real-017-281603-4-0001-496-5 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `"raster_manual"`; otrzymano: `"vector_discovery"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `mode`: oczekiwano 'raster_manual', harness/adapter daje 'vector_discovery'.
- dowody:
  - obserwacja `evidence:real-017-281603-4-0001-496-5` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-017-281603-4-0001-496-5.json`, sha256 `47552c5cbf33…`) = `["NULL"]`
  - ground truth `/cases/16/expected/mpzp/values/mode` = `"raster_manual"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-045 — real-017-281603-4-0001-496-5 / mpzp / zone_count

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `1`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zone_count`: oczekiwano None, harness/adapter daje 1.
- dowody:
  - obserwacja `evidence:real-017-281603-4-0001-496-5` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-017-281603-4-0001-496-5.json`, sha256 `47552c5cbf33…`) = `["NULL"]`
  - ground truth `/cases/16/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-046 — real-017-281603-4-0001-496-5 / mpzp / zones.symbols

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `["NULL"]`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zones.symbols`: oczekiwano None, harness/adapter daje ['NULL'].
- dowody:
  - obserwacja `evidence:real-017-281603-4-0001-496-5` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-017-281603-4-0001-496-5.json`, sha256 `47552c5cbf33…`) = `["NULL"]`
  - ground truth `/cases/16/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-047 — real-017-281603-4-0001-496-5 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-017-281603-4-0001-496-5` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-017-281603-4-0001-496-5.json`, sha256 `47552c5cbf33…`) = `["NULL"]`
  - ground truth `/cases/16/expected/mpzp/status` = `"manual_review"`

### E-048 — real-018-281603-4-0001-358 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/17/expected/pog/status` = `"unknown"`

### E-049 — real-018-281603-4-0001-358 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/17/expected/ouz/status` = `"unknown"`

### E-050 — real-018-281603-4-0001-358 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `"raster_manual"`; otrzymano: `"vector_discovery"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `mode`: oczekiwano 'raster_manual', harness/adapter daje 'vector_discovery'.
- dowody:
  - obserwacja `evidence:real-018-281603-4-0001-358` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-018-281603-4-0001-358.json`, sha256 `08befffa9a6d…`) = `["NULL"]`
  - ground truth `/cases/17/expected/mpzp/values/mode` = `"raster_manual"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-051 — real-018-281603-4-0001-358 / mpzp / zone_count

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `1`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zone_count`: oczekiwano None, harness/adapter daje 1.
- dowody:
  - obserwacja `evidence:real-018-281603-4-0001-358` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-018-281603-4-0001-358.json`, sha256 `08befffa9a6d…`) = `["NULL"]`
  - ground truth `/cases/17/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-052 — real-018-281603-4-0001-358 / mpzp / zones.symbols

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `["NULL"]`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zones.symbols`: oczekiwano None, harness/adapter daje ['NULL'].
- dowody:
  - obserwacja `evidence:real-018-281603-4-0001-358` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-018-281603-4-0001-358.json`, sha256 `08befffa9a6d…`) = `["NULL"]`
  - ground truth `/cases/17/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-053 — real-018-281603-4-0001-358 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-018-281603-4-0001-358` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-018-281603-4-0001-358.json`, sha256 `08befffa9a6d…`) = `["NULL"]`
  - ground truth `/cases/17/expected/mpzp/status` = `"manual_review"`

### E-054 — real-019-281603-4-0001-740 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/18/expected/pog/status` = `"unknown"`

### E-055 — real-019-281603-4-0001-740 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/18/expected/ouz/status` = `"unknown"`

### E-056 — real-019-281603-4-0001-740 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `"raster_manual"`; otrzymano: `"vector_discovery"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `mode`: oczekiwano 'raster_manual', harness/adapter daje 'vector_discovery'.
- dowody:
  - obserwacja `evidence:real-019-281603-4-0001-740` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-019-281603-4-0001-740.json`, sha256 `4e1228380651…`) = `["NULL"]`
  - ground truth `/cases/18/expected/mpzp/values/mode` = `"raster_manual"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-057 — real-019-281603-4-0001-740 / mpzp / zone_count

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `1`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zone_count`: oczekiwano None, harness/adapter daje 1.
- dowody:
  - obserwacja `evidence:real-019-281603-4-0001-740` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-019-281603-4-0001-740.json`, sha256 `4e1228380651…`) = `["NULL"]`
  - ground truth `/cases/18/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-058 — real-019-281603-4-0001-740 / mpzp / zones.symbols

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `["NULL"]`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zones.symbols`: oczekiwano None, harness/adapter daje ['NULL'].
- dowody:
  - obserwacja `evidence:real-019-281603-4-0001-740` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-019-281603-4-0001-740.json`, sha256 `4e1228380651…`) = `["NULL"]`
  - ground truth `/cases/18/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-059 — real-019-281603-4-0001-740 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-019-281603-4-0001-740` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-019-281603-4-0001-740.json`, sha256 `4e1228380651…`) = `["NULL"]`
  - ground truth `/cases/18/expected/mpzp/status` = `"manual_review"`

### E-060 — real-020-281603-4-0002-130 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/19/expected/pog/status` = `"unknown"`

### E-061 — real-020-281603-4-0002-130 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/19/expected/ouz/status` = `"unknown"`

### E-062 — real-020-281603-4-0002-130 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-020-281603-4-0002-130` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-020-281603-4-0002-130.json`, sha256 `dfc0c20667b2…`) = `["B-18UT"]`
  - ground truth `/cases/19/expected/mpzp/status` = `"manual_review"`

### E-063 — real-021-022104-2-0002-352 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/20/expected/pog/status` = `"unknown"`

### E-064 — real-021-022104-2-0002-352 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/20/expected/ouz/status` = `"unknown"`

### E-065 — real-021-022104-2-0002-352 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `"raster_manual"`; otrzymano: `"vector_discovery"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `mode`: oczekiwano 'raster_manual', harness/adapter daje 'vector_discovery'.
- dowody:
  - obserwacja `evidence:real-021-022104-2-0002-352` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-021-022104-2-0002-352.json`, sha256 `75cb2d5e07e6…`) = `["NULL"]`
  - ground truth `/cases/20/expected/mpzp/values/mode` = `"raster_manual"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-066 — real-021-022104-2-0002-352 / mpzp / zone_count

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `1`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zone_count`: oczekiwano None, harness/adapter daje 1.
- dowody:
  - obserwacja `evidence:real-021-022104-2-0002-352` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-021-022104-2-0002-352.json`, sha256 `75cb2d5e07e6…`) = `["NULL"]`
  - ground truth `/cases/20/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-067 — real-021-022104-2-0002-352 / mpzp / zones.symbols

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `["NULL"]`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zones.symbols`: oczekiwano None, harness/adapter daje ['NULL'].
- dowody:
  - obserwacja `evidence:real-021-022104-2-0002-352` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-021-022104-2-0002-352.json`, sha256 `75cb2d5e07e6…`) = `["NULL"]`
  - ground truth `/cases/20/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-068 — real-021-022104-2-0002-352 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-021-022104-2-0002-352` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-021-022104-2-0002-352.json`, sha256 `75cb2d5e07e6…`) = `["NULL"]`
  - ground truth `/cases/20/expected/mpzp/status` = `"manual_review"`

### E-069 — real-022-022104-2-0002-288-20 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/21/expected/pog/status` = `"unknown"`

### E-070 — real-022-022104-2-0002-288-20 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/21/expected/ouz/status` = `"unknown"`

### E-071 — real-022-022104-2-0002-288-20 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `"raster_manual"`; otrzymano: `"vector_discovery"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `mode`: oczekiwano 'raster_manual', harness/adapter daje 'vector_discovery'.
- dowody:
  - obserwacja `evidence:real-022-022104-2-0002-288-20` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-022-022104-2-0002-288-20.json`, sha256 `016b7d9ad091…`) = `["NULL"]`
  - ground truth `/cases/21/expected/mpzp/values/mode` = `"raster_manual"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-072 — real-022-022104-2-0002-288-20 / mpzp / zone_count

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `1`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zone_count`: oczekiwano None, harness/adapter daje 1.
- dowody:
  - obserwacja `evidence:real-022-022104-2-0002-288-20` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-022-022104-2-0002-288-20.json`, sha256 `016b7d9ad091…`) = `["NULL"]`
  - ground truth `/cases/21/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-073 — real-022-022104-2-0002-288-20 / mpzp / zones.symbols

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `["NULL"]`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zones.symbols`: oczekiwano None, harness/adapter daje ['NULL'].
- dowody:
  - obserwacja `evidence:real-022-022104-2-0002-288-20` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-022-022104-2-0002-288-20.json`, sha256 `016b7d9ad091…`) = `["NULL"]`
  - ground truth `/cases/21/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-074 — real-022-022104-2-0002-288-20 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-022-022104-2-0002-288-20` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-022-022104-2-0002-288-20.json`, sha256 `016b7d9ad091…`) = `["NULL"]`
  - ground truth `/cases/21/expected/mpzp/status` = `"manual_review"`

### E-075 — real-023-022104-2-0002-776 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/22/expected/pog/status` = `"unknown"`

### E-076 — real-023-022104-2-0002-776 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/22/expected/ouz/status` = `"unknown"`

### E-077 — real-023-022104-2-0002-776 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `"raster_manual"`; otrzymano: `"vector_discovery"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `mode`: oczekiwano 'raster_manual', harness/adapter daje 'vector_discovery'.
- dowody:
  - obserwacja `evidence:real-023-022104-2-0002-776` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-023-022104-2-0002-776.json`, sha256 `a78a33f80cc7…`) = `["NULL"]`
  - ground truth `/cases/22/expected/mpzp/values/mode` = `"raster_manual"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-078 — real-023-022104-2-0002-776 / mpzp / zone_count

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `1`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zone_count`: oczekiwano None, harness/adapter daje 1.
- dowody:
  - obserwacja `evidence:real-023-022104-2-0002-776` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-023-022104-2-0002-776.json`, sha256 `a78a33f80cc7…`) = `["NULL"]`
  - ground truth `/cases/22/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-079 — real-023-022104-2-0002-776 / mpzp / zones.symbols

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `["NULL"]`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zones.symbols`: oczekiwano None, harness/adapter daje ['NULL'].
- dowody:
  - obserwacja `evidence:real-023-022104-2-0002-776` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-023-022104-2-0002-776.json`, sha256 `a78a33f80cc7…`) = `["NULL"]`
  - ground truth `/cases/22/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-080 — real-023-022104-2-0002-776 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-023-022104-2-0002-776` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-023-022104-2-0002-776.json`, sha256 `a78a33f80cc7…`) = `["NULL"]`
  - ground truth `/cases/22/expected/mpzp/status` = `"manual_review"`

### E-081 — real-024-022104-2-0002-230-2 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/23/expected/pog/status` = `"unknown"`

### E-082 — real-024-022104-2-0002-230-2 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/23/expected/ouz/status` = `"unknown"`

### E-083 — real-024-022104-2-0002-230-2 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `"raster_manual"`; otrzymano: `"vector_discovery"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `mode`: oczekiwano 'raster_manual', harness/adapter daje 'vector_discovery'.
- dowody:
  - obserwacja `evidence:real-024-022104-2-0002-230-2` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-024-022104-2-0002-230-2.json`, sha256 `54ee72a8dbf9…`) = `["NULL"]`
  - ground truth `/cases/23/expected/mpzp/values/mode` = `"raster_manual"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-084 — real-024-022104-2-0002-230-2 / mpzp / zone_count

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `1`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zone_count`: oczekiwano None, harness/adapter daje 1.
- dowody:
  - obserwacja `evidence:real-024-022104-2-0002-230-2` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-024-022104-2-0002-230-2.json`, sha256 `54ee72a8dbf9…`) = `["NULL"]`
  - ground truth `/cases/23/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-085 — real-024-022104-2-0002-230-2 / mpzp / zones.symbols

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `["NULL"]`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['NULL']; `zones.symbols`: oczekiwano None, harness/adapter daje ['NULL'].
- dowody:
  - obserwacja `evidence:real-024-022104-2-0002-230-2` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-024-022104-2-0002-230-2.json`, sha256 `54ee72a8dbf9…`) = `["NULL"]`
  - ground truth `/cases/23/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-086 — real-024-022104-2-0002-230-2 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-024-022104-2-0002-230-2` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-024-022104-2-0002-230-2.json`, sha256 `54ee72a8dbf9…`) = `["NULL"]`
  - ground truth `/cases/23/expected/mpzp/status` = `"manual_review"`

### E-087 — real-025-281603-4-0001-431-66 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/24/expected/pog/status` = `"unknown"`

### E-088 — real-025-281603-4-0001-431-66 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/24/expected/ouz/status` = `"unknown"`

### E-089 — real-025-281603-4-0001-431-66 / mpzp / mode

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `"raster_manual"`; otrzymano: `"vector_discovery"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['16.U/US', 'NULL', '15.U']; `mode`: oczekiwano 'raster_manual', harness/adapter daje 'vector_discovery'.
- dowody:
  - obserwacja `evidence:real-025-281603-4-0001-431-66` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-025-281603-4-0001-431-66.json`, sha256 `85d39b8af271…`) = `["16.U/US","NULL","15.U"]`
  - ground truth `/cases/24/expected/mpzp/values/mode` = `"raster_manual"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-090 — real-025-281603-4-0001-431-66 / mpzp / zone_count

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `2`; otrzymano: `3`; różnica: 1.0 count;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['16.U/US', 'NULL', '15.U']; `zone_count`: oczekiwano 2, harness/adapter daje 3.
- dowody:
  - obserwacja `evidence:real-025-281603-4-0001-431-66` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-025-281603-4-0001-431-66.json`, sha256 `85d39b8af271…`) = `["16.U/US","NULL","15.U"]`
  - ground truth `/cases/24/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-091 — real-025-281603-4-0001-431-66 / mpzp / zones.symbols

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=mismatch`;
- oczekiwano: `["15.U","16.U/US"]`; otrzymano: `["15.U","16.U/US","NULL"]`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['16.U/US', 'NULL', '15.U']; `zones.symbols`: oczekiwano ['15.U', '16.U/US'], harness/adapter daje ['15.U', '16.U/US', 'NULL'].
- dowody:
  - obserwacja `evidence:real-025-281603-4-0001-431-66` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-025-281603-4-0001-431-66.json`, sha256 `85d39b8af271…`) = `["16.U/US","NULL","15.U"]`
  - ground truth `/cases/24/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-092 — real-025-281603-4-0001-431-66 / mpzp / zones[NULL].presence

- rodzaj: `field_discrepancy`; kategoria: `parser`; przyczyna: `RC-01`; reguła: `rule:mpzp_null_symbol;verdict=unexpected_actual`;
- oczekiwano: `null`; otrzymano: `"NULL"`;
- wyjaśnienie: Obserwacja discovery zawiera `NULL` wśród symboli ['16.U/US', 'NULL', '15.U']; `zones[NULL].presence`: oczekiwano None, harness/adapter daje 'NULL'.
- dowody:
  - obserwacja `evidence:real-025-281603-4-0001-431-66` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-025-281603-4-0001-431-66.json`, sha256 `85d39b8af271…`) = `["16.U/US","NULL","15.U"]`
  - ground truth `/cases/24/expected/mpzp/status` = `"manual_review"`
  - kod `backend/app/services/mpzp.py::_first_matching_attribute` — przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy
  - próba kodu `app.services.mpzp._parse_get_feature_info_response(symbol=NULL)` → `{"zone_symbol_from_payload_NULL":{"html":"NULL","json":"NULL"}}`

### E-093 — real-025-281603-4-0001-431-66 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-025-281603-4-0001-431-66` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-025-281603-4-0001-431-66.json`, sha256 `85d39b8af271…`) = `["16.U/US","NULL","15.U"]`
  - ground truth `/cases/24/expected/mpzp/status` = `"manual_review"`

### E-094 — real-025-281603-4-0001-431-66 / terrain / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-15`; reguła: `rule:nmt_limit`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: NMT `unknown`: Usługa NMT zgłosiła błąd: Powierzchnia poligonu (237428.842506199) większa niż 100000 m2
- dowody:
  - obserwacja `evidence:real-025-281603-4-0001-431-66` `/observations/nmt` (`artifacts/evidence/real-025-281603-4-0001-431-66.json`, sha256 `85d39b8af271…`) = `{"error":"Usługa NMT zgłosiła błąd: Powierzchnia poligonu (237428.842506199) większa niż 100000 m2","error_type":"NmtServiceUnavailableError","status":"unknown"`
  - ground truth `/cases/24/expected/terrain/status` = `"unknown"`

### E-095 — real-026-281604-5-0011-107 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/25/expected/pog/status` = `"unknown"`

### E-096 — real-026-281604-5-0011-107 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/25/expected/ouz/status` = `"unknown"`

### E-097 — real-026-281604-5-0011-107 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-026-281604-5-0011-107` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-026-281604-5-0011-107.json`, sha256 `ae5cebe5ca42…`) = `"no_mpzp"`
  - ground truth `/cases/25/expected/mpzp/status` = `"unknown"`

### E-098 — real-026-281604-5-0011-107 / terrain / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-15`; reguła: `rule:nmt_limit`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: NMT `unknown`: Usługa NMT zgłosiła błąd: Powierzchnia poligonu (265703.13863403) większa niż 100000 m2
- dowody:
  - obserwacja `evidence:real-026-281604-5-0011-107` `/observations/nmt` (`artifacts/evidence/real-026-281604-5-0011-107.json`, sha256 `ae5cebe5ca42…`) = `{"error":"Usługa NMT zgłosiła błąd: Powierzchnia poligonu (265703.13863403) większa niż 100000 m2","error_type":"NmtServiceUnavailableError","status":"unknown"}`
  - ground truth `/cases/25/expected/terrain/status` = `"unknown"`

### E-099 — real-027-246101-1-0001-1043 / ouz / status

- rodzaj: `limitation`; kategoria: `geometry`; przyczyna: `RC-16`; reguła: `rule:ouz_boundary_sliver`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Przecięcie OUZ poniżej tolerancji 1 m²; status `manual_review`, relacja niejednoznaczna.
- dowody:
  - obserwacja `evidence:real-027-246101-1-0001-1043` `/observations/pog_ouz/ouz_area_sqm` (`artifacts/evidence/real-027-246101-1-0001-1043.json`, sha256 `fa99addcadcb…`) = `0.158`
  - ground truth `/cases/26/expected/ouz/values/intersection_area` = `0.158`

### E-100 — real-027-246101-1-0001-1043 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-027-246101-1-0001-1043` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-027-246101-1-0001-1043.json`, sha256 `fa99addcadcb…`) = `"no_mpzp"`
  - ground truth `/cases/26/expected/mpzp/status` = `"unknown"`

### E-101 — real-028-246101-1-0004-737-26 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-028-246101-1-0004-737-26` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-028-246101-1-0004-737-26.json`, sha256 `1871266f25b5…`) = `"no_mpzp"`
  - ground truth `/cases/27/expected/mpzp/status` = `"unknown"`

### E-102 — real-029-246101-1-0004-741-132 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-12`; reguła: `rule:mpzp_no_plan`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.
- dowody:
  - obserwacja `evidence:real-029-246101-1-0004-741-132` `/observations/mpzp_discovery/status` (`artifacts/evidence/real-029-246101-1-0004-741-132.json`, sha256 `d182ba46cb21…`) = `"no_mpzp"`
  - ground truth `/cases/28/expected/mpzp/status` = `"unknown"`

### E-103 — real-030-026201-1-0009-578-2 / pog / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-10`; reguła: `rule:pog_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.
- dowody:
  - ground truth `/cases/29/expected/pog/status` = `"unknown"`

### E-104 — real-030-026201-1-0009-578-2 / ouz / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-11`; reguła: `rule:ouz_unknown`;
- oczekiwano: `"unknown"`; otrzymano: `"unknown"`;
- wyjaśnienie: Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.
- dowody:
  - ground truth `/cases/29/expected/ouz/status` = `"unknown"`

### E-105 — real-030-026201-1-0009-578-2 / mpzp / status

- rodzaj: `limitation`; kategoria: `source`; przyczyna: `RC-13`; reguła: `rule:mpzp_discovery_only`;
- oczekiwano: `"manual_review"`; otrzymano: `"manual_review"`;
- wyjaśnienie: Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.
- dowody:
  - obserwacja `evidence:real-030-026201-1-0009-578-2` `/observations/mpzp_discovery/candidate_zone_symbols` (`artifacts/evidence/real-030-026201-1-0009-578-2.json`, sha256 `2e9bd71843d6…`) = `["6KD L"]`
  - ground truth `/cases/29/expected/mpzp/status` = `"manual_review"`

### E-106 — real-030-026201-1-0009-578-2 / flood / relation

- rodzaj: `field_discrepancy`; kategoria: `data`; przyczyna: `RC-04`; reguła: `rule:isok_boundary_feature_absent;verdict=mismatch`;
- oczekiwano: `"intersection_and_boundary"`; otrzymano: `"intersection"`;
- wyjaśnienie: `relation`: korpus 'intersection_and_boundary', harness 'intersection'; obserwacja ISOK ma 4 cech, wszystkie z dodatnim polem.
- dowody:
  - obserwacja `evidence:real-030-026201-1-0009-578-2` `/observations/isok/features` (`artifacts/evidence/real-030-026201-1-0009-578-2.json`, sha256 `2e9bd71843d6…`) = `[{"area_ratio":0.78337731,"intersection_area_sqm":4728.919,"probability_class":"scenariusz Q 0,2% (raz na 500 lat)","severity":"low","type":"flood"},{"area_rati`
  - ground truth `/cases/29/expected/flood/values/boundary_feature_area` = `0.0`

### E-107 — real-030-026201-1-0009-578-2 / flood / status

- rodzaj: `status_disagreement`; kategoria: `data`; przyczyna: `RC-04`; reguła: `rule:isok_boundary_feature_absent;status`;
- oczekiwano: `"manual_review"`; otrzymano: `"available"`;
- wyjaśnienie: `status`: korpus 'manual_review', harness 'available'; obserwacja ISOK ma 4 cech, wszystkie z dodatnim polem.
- dowody:
  - obserwacja `evidence:real-030-026201-1-0009-578-2` `/observations/isok/features` (`artifacts/evidence/real-030-026201-1-0009-578-2.json`, sha256 `2e9bd71843d6…`) = `[{"area_ratio":0.78337731,"intersection_area_sqm":4728.919,"probability_class":"scenariusz Q 0,2% (raz na 500 lat)","severity":"low","type":"flood"},{"area_rati`
  - ground truth `/cases/29/expected/flood/values/boundary_feature_area` = `0.0`
