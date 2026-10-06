# ADR-013: Silnik wartości liczbowych MPZP, warunki wartości i skalibrowana pewność

- Status: **Zaakceptowany technicznie; progi pewności i wynik rozwojowy czekają na potwierdzenie właściciela** (patrz „Co wymaga decyzji”)
- Data: 2026-10-03
- Zakres: PV3-07 (Task 20.7), PV3-08 (Task 20.8), PV3-09 (Task 20.9) — Epic 20A, parser v3
- Powiązane: ADR-001 (modularny monolit), ADR-004 (parametry MPZP i dowód wartości — pkt 4 i 5 zastępuje
  niniejszy ADR), ADR-011 (macierz jakości i paczka audytowa), ADR-012 (LLM — kandydat, nigdy źródło prawdy),
  `docs/evaluation/pv3-07-09-verification.md` (liczby i polecenia odbiorowe)

## Kontekst

Po PV3-06 parser czytał dobry blok strefy, ale wzorce wartości były wąskie (recall 0,25 na korpusie BK-603,
podział końcowy 0,20). Wzorce liczbowe były rozproszone w czterech miejscach (`mpzp_parser_numeric`,
`mpzp_parser_descriptive`, `rules.py`, ewaluator z własną normalizacją anotacji), więc poprawka w jednym
nie dochodziła do pozostałych, a ewaluator mógł mierzyć coś innego niż aplikacja. Dwie różne wartości tego
samego parametru zawsze oznaczano jako sprzeczność, choć w uchwałach to zwykle warunek (inny dach, inna
podstrefa, inny typ budynku). Pewność była iloczynem stałych mnożników (0,65 / 0,75 / 0,85 / 0,6) bez
pomiaru, więc „0,9” nic nie znaczyło.

## Decyzja

### 1. Jeden silnik wartości liczbowych (PV3-07)

`app/modules/planning/domain/quantity_engine.py` (czysta domena, bez frameworków — ADR-001) czyta tekst
bloku w pięciu krokach: **maskowanie szumu** (stopki stron, odwołania `§/ust./pkt`, identyfikatory; długość
tekstu zachowana, więc znaki wskazują oryginał) → **podział na klauzule** po znacznikach listy z dziedziczeniem
rzeczownika z klauzuli nagłówkowej (`1) … a) …`) → **atomy** (liczba + jednostka, zakres „od X do Y” i
„X – Y”, liczebniki słowne, artefakty stopni OCR, notacja podwójna „0,50 (50%)”) → **ramki** (linia zabudowy,
miejsca parkingowe, powierzchnia biologicznie czynna z wartością przed rzeczownikiem) → **operatory**
(min/max/dokładnie, przymiotnik przed liczbą, „nie większą niż”, „do N”; operatory ostre i „zwiększenie o…”
**odrzucają** wartość, bo nie jest limitem). Dopasowanie niesie zakres znaków, strategię i flagi.

Zasady, które wynikają z projektu, nie z dopasowania do korpusu:

- **Leksykon jest danymi**, nie kodem (`quantity_lexicon.py`, `LEXICON_VERSION`): rzeczowniki parametrów,
  dopuszczalne jednostki, operatory, rzeczowniki neutralizujące (wysokość parteru/elewacji/obiektów,
  szerokość, poziom posadowienia), znaczniki list, wzorce szumu, kary flag. Dodanie brzmienia = wpis w
  leksykonie + test.
- **Niejednoznaczność ma flagę i karę, nie jest zgadywana po cichu**: stosunek jako procent
  (`ratio_to_percent`), stopnie OCR (`degree_ocr`), liczebnik słowny, rzeczownik odziedziczony,
  rzeczownik domyślny (`noun_implied`). Flagi trafiają do API (`normalization_flags`) i do cech pewności.
- **Zakres strefy**: gdy klauzula wymienia symbole (`dla terenów 1MN, 2MN`, zakresy `1MN–3MN`, tolerancja
  OCR), wartość trafia tylko do wymienionych stref; „pozostałych” = dopełnienie. Wartość z klauzuli innej
  strefy jest odrzucana i raportowana w `dropped`, nie przypisywana „na wszelki wypadek”.
- **Jedna normalizacja**: `quantity_normalization.py` (`normalize_annotation_value`, `normalize_quantity`)
  jest używana przez parser **i** przez ewaluator; ewaluator nie ma już własnej kopii reguł. Test
  (`test_evaluate_mpzp_parser`) porównuje oba wejścia na tych samych przypadkach.
- Stary parser liczbowy i opisowy **delegują** do silnika (`extract_numeric_matches`, `find_quantities`,
  `find_roof_geometry`); `rules.py` bierze reguły liczbowe z silnika. Pozostały tylko adaptery
  (segmenty, dowód, deduplikacja).
- Wersja parsera: `mpzp-parser/3.0-det` (tryb `legacy`), `mpzp-parser/3.0-det+scope.1` (tryb blokowy).
  Domyślnym trybem zakresu pozostaje `legacy` — włączenie blokowego to Task 20.14.

**Interpretacja „4 ustawowych” parametrów.** Issue wymienia 9 parametrów katalogu BK-603 oraz „4 ustawowe”
bez ich nazw. Dziewięć to nazwy katalogu ewaluacji: `max_building_height_m`, `max_storeys`,
`max_building_coverage_percent`, `min_biologically_active_percent`, `min_intensity`, `max_intensity`,
`roof_angle_min_deg`, `roof_angle_max_deg`, `setback_m`. Cztery ustawowe przyjęto jako parametry spoza
katalogu, które nazywa ustawa o planowaniu i zagospodarowaniu przestrzennym (art. 15 ust. 2 pkt 6 i 8,
art. 10 ust. 3a): `parking_minimum`, `min_building_coverage_percent` (minimalny udział powierzchni zabudowy,
w rodzinie `coverage` z operatorem „min”), `min_plot_area_m2` i `max_retail_sales_area_m2`. Interpretacja jest
zapisana w komentarzu `PARAMETER_FAMILIES` i nie wpływa na zachowanie silnika; jeśli właściciel miał na myśli
inną czwórkę, zmienia się leksykon (jedno miejsce), a nie silnik. Te cztery nie mają anotacji w korpusie
BK-603, więc ich dokładność **nie jest zmierzona** na korpusie — są pokryte tylko testami jednostkowymi i
syntetycznymi brzmieniami (`test_quantity_engine.py`).

### 2. Warunki wartości zamiast zawsze-sprzeczności (PV3-08)

`value_conditions.py` klasyfikuje kwalifikatory znalezione przez silnik (przed liczbą, po liczbie, w
klauzuli, w nagłówku) słownikami: `roof_type` (rdzenie z `ROOF_TYPES`), `building_type`, `subzone`,
`location`, `other`. Warunek ma postać `{kind, label, quote}` (`ValueCondition`); cytat to dosłowny
fragment źródła.

- `value_kind`: `unconditional` | `conditional` | `conflict`. **Konflikt** zachodzi tylko, gdy ta sama
  przesłanka — (parametr, rodzaj zakresu, klucz warunków) — ma więcej niż jedną różną wartość. Dwie różne
  wartości o różnych warunkach to dwie wartości warunkowe.
- **Pole płaskie API** (`max_building_height_m` itd.) wypełnia się wyłącznie, gdy istnieje dokładnie jedna
  wartość bezwarunkowa; w przeciwnym razie pozostaje `null` (null ≠ 0, brak wyboru za użytkownika).
  Wartości warunkowe są w `parameters[]` (evidence) z listą warunków.
- `conflict_group_id` ma sufiks skrótu SHA-256 zestawu warunków, więc grupy różnych warunków się nie mieszają.
- **Pokazywanie**: UI (`MpzpZoneCard`: znacznik „warunkowa”, lista warunków z cytatem, nota), PDF (wiersze
  „warunkowa” w sekcji parametrów, osobna kolumna warunków w evidence, tekst ograniczeń), paczka audytowa
  (`audit-exporter/1.1.0`, sekcja README „Parametry MPZP: wartości warunkowe i sprzeczności”).
- **Persystencja**: warunki to struktura zagnieżdżona i nie są kluczem zapytań, więc wybrano JSONB
  (`mpzp_parameters.conditions`) i `value_kind` z `CHECK` — migracja `028_mpzp_parameter_condition` po head
  `027_zone_symbol_length`, pełny downgrade. **Stare snapshoty i wiersze**: `conditions IS NULL` i
  `value_kind IS NULL` czytane są jako wartość bezwarunkowa; przy odczycie `MpzpParameterEvidence`
  wyprowadza `value_kind` z obecności warunków i grupy konfliktu, więc nie ma przeliczania historii.
- Jakość sekcji: parametr warunkowy **nie** obniża jakości sekcji do „partial” (to nie brak, tylko wartość
  z warunkiem); prawdziwy konflikt nadal tak.

### 3. Skalibrowana pewność (PV3-09)

`evidence_confidence.py` zastępuje stałe mnożniki prawdopodobieństwem z regresji logistycznej na 22 cechach
dowodu: tryb ekstrakcji (OCR, szum OCR, HTML), pewność i rodzaj zakresu (zapas, klauzula ogólna,
niepewność, tryb legacy), weryfikacja cytatu, strategia silnika (zakres, wartość bez rzeczownika, ramka),
flagi normalizacji, liczba kandydatów, konflikt, warunkowość, niejednoznaczność strefy, symbol
wywnioskowany. **Samoocena modelu językowego nie jest cechą** (ADR-012): wartość z modelu dostaje pewność
ograniczoną od góry i zawsze wymaga weryfikacji ręcznej.

- **Artefakt**: `backend/app/core/mpzp_confidence_calibration.json` — wersja, skrót SHA-256 danych
  kalibracyjnych, korpus i podział, wagi, progi pasm, pomiary (krzywa niezawodności, Brier, ECE, błąd na
  pasmo), polityka tolerancji, wersje silnika/leksykonu/warunków. Ładowanie waliduje artefakt (schemat, zgodność liczby wag z `FEATURE_NAMES`, progi 0–1, skrót danych);
brak lub uszkodzenie pliku kończy się `CalibrationError` — bez cichego powrotu do starych mnożników.
- **Podział**: kalibracja na `development`; ocena niezawodności na `final` (nie używano go do doboru wag
  ani progów). Regularyzacja `l2` dobrana walidacją krzyżową „bez jednej próbki” na danych kalibracyjnych.
  Wagi mają priory i ograniczenia znaku (cechy „złe” nie mogą dostać dodatniej wagi), bo na 241 wartościach
  z 16 błędami swobodna regresja dawała sprzeczne z sensem znaki.
- **Pasma** low/medium/high i **próg ręcznej weryfikacji** wynikają z pomiaru: najniższa pewność, od której
  odsetek błędów wygładzony regresją izotoniczną nie przekracza tolerancji (ε_high = 0,05, ε_review =
  0,10, minimum 20 wartości). Tolerancje to **polityka** (do potwierdzenia przez właściciela), progi — wynik.
- **Numeryczne** wartości dostają `score(...)`: prawdopodobieństwo (`confidence`), pasmo
  (`confidence_band`) i identyfikator artefaktu (`confidence_calibration`) trafiają do API; **cechy dowodu**
  (`confidence_features`) są w wyniku parsera i w ewaluatorze (raport niezawodności), ale nie w odpowiedzi
  API — to dane diagnostyczne, nie kontrakt dla klienta. **Opisowe** wartości (listy ustaleń) —
  `min(pewność parsera, p, limit)` bez pasma, bo nie mają etykiet kalibracyjnych.
- **Rekalibracja**: `python3 backend/scripts/calibrate_mpzp_confidence.py` (z `backend/`; `--check` kończy
  się błędem, jeśli artefakt różni się od wyniku z bieżącego kodu i danych — pilnuje tego
  `test_mpzp_confidence_calibration`). Obowiązkowa po zmianie: leksykonu/silnika, kodu cech, korpusu lub
  anotacji, wersji parsera/trybu zakresu, a także po wejściu ścieżki modelu językowego (osobna kalibracja,
  bez samooceny modelu).

### 4. Kontrakt i cache

`MPZP_RESULT_SCHEMA_VERSION` 2.2 → 2.3 (nowa postać wartości z silnika) → 2.4 (warunki, `value_kind`,
pole płaskie tylko dla jednej wartości bezwarunkowej) → 2.5 (skalibrowana pewność, pasma, cechy). Sygnatura cache
(`RESULT_CONTRACT_VERSION` = `pog-v…+mpzp-v{MPZP_RESULT_SCHEMA_VERSION}+terrain-v…+risk-v…+quality-v…`)
zawiera wersję kontraktu MPZP, więc snapshoty z `mpzp-v2.2` i starszych nie są serwowane jako trafienie
cache — analiza liczy się od nowa; odczyt historyczny działa bez zmian (stare wartości, bez warunków i
pasm). Sama wersja parsera (`MPZP_PARSER_VERSION`) **nie** wchodzi do sygnatury; trafia do wiersza
`mpzp_parameters.parser_version` i do dowodu wartości, a odświeżenie cache po zmianie parsera zapewnia
podniesienie `MPZP_RESULT_SCHEMA_VERSION` (komentarz w `services/cache.py`). Konsekwencja: każda
przyszła zmiana wartości zwracanych przez silnik **musi** podnieść wersję kontraktu (zrobiono 3 razy w
tym zadaniu). Jednorazowy koszt: pierwsza analiza każdej działki po wdrożeniu jest liczona ponownie.

## Rozważone i odrzucone

- **Pojedynczy duży regex na parametr** — to był stan wyjściowy; nie skaluje się na brzmienia i nie niesie
  zakresu ani strategii.
- **Warunki tylko z LLM** — warunek ma być weryfikowalnym cytatem; słowniki dają go deterministycznie, LLM
  może go później dopowiedzieć jako kandydata (ADR-012).
- **Kolumny `condition_*` zamiast JSONB** — warunków jest zmienna liczba, nie są kluczem filtrów; JSONB bez
  utraty informacji i bez kolejnej migracji przy nowym rodzaju warunku (rodzaj walidowany w schemacie).
- **Regresja logistyczna bez priorów** — nieprzydatna na 16 błędach (patrz wyżej).
- **Wyprowadzanie pasm ze stałych 0,6/0,8** — to właśnie niesprawdzalne progi, które zadanie ma usunąć.

## Konsekwencje i ograniczenia

- **Wynik rozwojowy.** Leksykon i reguły rozwijano na 21 próbkach BK-603, na których mierzy się wynik;
  podział `final` zamrożono 2026-09-30 przed pierwszym biegiem parsera, ale po nim silnik był poprawiany
  na jego błędach (korpus 21 próbek nie ma drugiego, niezależnego podziału). Liczby „final” są więc
  *obserwacją* z korpusu, nie estymatą uogólnienia. Niezależna ocena wymaga zbioru z PV3-02.
- **Anotacje korpusu** nadał asystent AI i czekają na przegląd człowieka (BK-603); wynik „precision/recall”
  jest względem tych anotacji.
- **Pasmo „medium” jest puste**: próg średni i wysoki pokrywają się (0,943), bo między „błąd ≤ 10%” a „błąd ≤ 5%”
  nie ma na danych kalibracyjnych przedziału o ≥ 20 wartościach. Pasma pozostają monotoniczne (low 25% błędów,
  high 0%), ale rozróżniają dwie, nie trzy klasy.
- **Pewność nie jest prawdopodobieństwem uogólnienia**: ECE 0,059 na podziale `final` mierzy zgodność
  na tym samym korpusie; poza nim należy ją traktować jako uporządkowanie, nie gwarancję.
- **Zaostrzone zachowanie**: wartość z klauzuli innej strefy jest odrzucana, a wartość warunkowa nie
  trafia do pola płaskiego — API zwraca teraz `null` tam, gdzie wcześniej `conflict`. Konsumenci muszą
  czytać `parameters[]`; frontend i PDF zaktualizowano.
- Ewaluator: dla wartości o dokładnym zakresie znaków (`span`) pominięto starą weryfikację strony
  (`check_source`), bo silnik potrafi wskazać dokładny cytat; wariant strony zostaje dla wyników bez zakresu.
  Próg testu zakresu strefy (`test_mpzp_zone_scope`) złagodzono do ≥ 0,95 i `wrong_block ≤ 9` (zamiast
  twardszych wartości z PV3-06), bo silnik zwraca teraz więcej poprawnych wartości, w tym powtarzających
  się w kilku blokach.
- Ewaluator mierzy **wykrycie** pary (strefa, parametr), a poprawność wartości osobno (`value_accuracy`);
  szczegółowe liczby i lista pozostałych błędów w raporcie odbioru.

## Co wymaga decyzji właściciela

1. Tolerancje błędu pasm (ε_high 5%, ε_review 10%) i minimum 20 wartości na próg.
2. Czy „4 ustawowe” to cztery parametry wskaźnikowe jak wyżej.
3. Moment włączenia trybu blokowego jako domyślnego (Task 20.14) — zwiększy to zasięg silnika
   v3 na produkcji i wymaga kolejnego podniesienia wersji kontraktu.
4. Przegląd anotacji AI korpusu BK-603 i budowa niezależnego zbioru końcowego (PV3-02) przed
   zaufaniem liczbom z podziału `final`.
