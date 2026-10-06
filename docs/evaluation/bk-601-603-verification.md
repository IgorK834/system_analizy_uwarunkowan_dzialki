# Odbiór BK-601, BK-602 i BK-603

Data: 30 września 2026 r. Baza kodu: `cbefc0a` z niezacommitowanymi zmianami tego
odbioru (manifesty zapisują `worktree.dirty` i odcisk kodu).

## Stan zadań

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| BK-601 — badanie poprawności | wykonane | udziały stref POG/MPZP/OUZ i NMT w harnessie BK-004 są odczytem zamrożonych obserwacji, więc ich zgodność nie dowodzi poprawności przecięć; raport to rozdziela |
| BK-602 — centroid vs przecięcie | **wykonane częściowo** | brak geometrii stref rzeczywistych: host Rejestru Urbanistycznego był nieosiągalny; wynik dokładny na realnych strefach nie został zmierzony |
| BK-603 — ewaluacja parsera MPZP | wykonane | anotacje sporządził asystent AI z tekstu źródłowego; przegląd człowieka oczekuje |

## Następstwo: parser MPZP v3

Wyniki BK-603 (recall 0,25 parsera `legacy`) uruchomiły Epic 20 (parser v3: rdzeń deterministyczny + ekstrakcja modelem
językowym z weryfikacją cytatu). Wyniki parsera v3 na tym samym korpusie, z jawnymi ograniczeniami (anotacje AI, wynik
rozwojowy, bramka `NOT_DECIDABLE`), opisują: [PV3-04–06](pv3-04-06-verification.md), [PV3-07–09](pv3-07-09-verification.md),
[PV3-15–17](pv3-15-17-verification.md) i katalog wyników `results/parser-v3/` (`legacy/`, `v3/`, `comparison/`,
`gate_report.*`); oznaczenie w UI i PDF oraz monitoring i runbook: [PV3-18–20](pv3-18-20-verification.md). Liczby BK-603
w tym dokumencie pozostają miarą parsera sprzed v3 i **nie zostały zmienione**.

## Polecenia odbiorowe

```bash
# BK-601 (zapisuje docs/evaluation/results/accuracy/ i docs/evaluation/error_analysis.md)
python3 backend/scripts/evaluate_reference_corpus.py --study --repeat 3

# BK-602 (zapisuje docs/evaluation/results/centroid/)
python3 backend/scripts/compare_centroid_intersection.py --output-dir docs/evaluation/results/centroid --repeat 2

# BK-603 (zapisuje docs/evaluation/results/parser/)
python3 backend/scripts/evaluate_mpzp_parser.py --mode offline --output-dir docs/evaluation/results/parser --repeat 2

# Testy zmienionych modułów
cd backend
python3 -m pytest tests/test_evaluate_reference_corpus.py tests/test_reference_corpus.py \
  tests/test_evaluate_mpzp_parser.py tests/test_centroid_experiment.py -q \
  --cov=scripts.evaluate_reference_corpus --cov=scripts.evaluate_mpzp_parser \
  --cov=scripts.compare_centroid_intersection --cov=scripts.freeze_pog_zone_layers \
  --cov-report=term-missing
```

Pełny zestaw w kontenerze jak w CI (`docker compose run … -e REPO_ROOT=/repo backend pytest -m 'not docker_cli' --cov=app --cov-fail-under=80`): wynik i logi w sekcji „Weryfikacja” poniżej.

## BK-601 — wyniki

Manifest zamrożony w `results/accuracy/run_manifest.json` (`commit_sha`, odcisk kodu,
`corpus_sha256`, skróty 60 artefaktów, wydania źródeł `*@2026-09-23`, wersje parsera
`mpzp-parser/2.0`, stylu POG `2026.09.29-1`, kontraktu wyniku, środowisko, parametry,
tabela pomiarów). Skrót merytoryczny trzech biegów jest identyczny (`determinism.json`), a
dwa osobne procesy CLI dają ten sam skrót (test). Czas jest w `timing.json`.

| Wielkość | Wynik | n |
|---|---:|---:|
| wejścia = ukończone + częściowe + błędne | 30 = 0 + 30 + 0 | 30 |
| identyfikacja działki | 1,000 | 30/30 |
| accuracy klasy strefy (BK-004) | 1,000 | 10/10 |
| MAE udziału stref (BK-004) | 0,000 pp | 14 |
| zgodność pól | 0,974 | 480/493 |
| zgodność statusów sekcji | 0,990 | 208/210 |
| fałszywa pewność (wartość przy `unknown` w ground truth) | 0,050 | 15/298 |
| porównań udziału > 0,5 pp | 0 | 49 |
| MAE / max pola działki | 0,0003 / 0,0005 m² | 30 |
| kompletność pól | 0,676 | 365/540 |
| przypadki z nieznaną sekcją / częściowe / manual review | 1,000 / 1,000 / 0,500 | 30 |

Confusion (TP/FP/FN/TN): powódź 8/0/0/21, ochrona przyrody 2/0/0/26, OUZ 4/0/0/2, suma 14/0/0/49.

Metryki nagłówkowe są 1,0 lub 0 pp, a porównanie pól ujawnia 28 rozbieżności i 2
niezgodności statusu w czterech przyczynach źródłowych. Najważniejsza: **RC-01**,
literał `NULL` z KIMPZP GetFeatureInfo traktowany jak symbol strefy (8 działek, 25
wpisów; dowód liczony w czasie badania przez produkcyjny parser odpowiedzi). Pozostałe to
klasa terenu bez definicji (RC-02), niespójna etykieta ground truth dla `real-008`
(RC-03) i cecha ISOK styczna do granicy, której nie ma w zamrożonej obserwacji (RC-04,
przyczyna nierozstrzygnięta offline). Do tego 77 ograniczeń (unknown / manual review) z
kategorią i dowodem. Szczegóły, każdy wpis i dowód: `docs/evaluation/error_analysis.md`
(generowany z `error_ledger.json`).

Nowy wpis w korpusie to nie „naprawa”: zadanie nie zmienia kodu produkcyjnego ani
ground truth. Defekty przekazano jako osobne zadania (sekcja „Ustalenia”).

## BK-603 — wyniki

Korpus `backend/tests/fixtures/mpzp_evaluation/`: 21 próbek, 17 dokumentów, 13 gmin, 14
próbek wielostrefowych, 321 anotacji w 47 strefach, anotacje zamrożone skrótem. Formaty:
tekstowy PDF 14, tabela PDF 1, HTML 2, skan z OCR rzeczywisty 2, skan symulowany 2.

| Wycinek | Precision | Recall | Dokładność wartości (znalezione) | Przypisanie do strefy |
|---|---|---|---|---|
| łącznie | 1,000 (62/62) | 0,248 (62/250) | 0,806 (50/62) | 1,000 (37/37) |
| rozwojowy | 1,000 (30/30) | 0,337 (30/89) | 0,767 (23/30) | 1,000 (19/19) |
| **końcowy** | 1,000 (32/32) | **0,199** (32/161) | 0,844 (27/32) | 1,000 (18/18) |
| tekstowy PDF | 1,000 (53/53) | 0,276 | 0,868 (46/53) | 1,000 (35/35) |
| tabela PDF | brak wartości | 0,000 (0/10) | brak | brak |
| HTML | 1,000 (5/5) | 0,217 | 0,200 (1/5) | brak |
| OCR rzeczywisty / symulowany | 1,000 / 1,000 | 1,000 (2/2) / 0,087 (2/23) | 1,000 / 0,500 | brak / 1,000 |

Przedziały Wilsona, liczniki i wycinki według parametru są w `report.md` i `metrics.json`.
Wnioski:

- parser jest zachowawczy: na 423 parach (strefa, parametr) nie zwrócił żadnego
  fałszywego alarmu, ale znalazł 25% wartości; na zbiorze końcowym 20%, czyli korpus
  rozwojowy zawyża wynik;
- błędy rozpoznania (188) dominują nad błędami wartości (12), a błędów przypisania do
  strefy nie zmierzono (0 z 37 par rozróżniających); najczęstsze wskazówki przyczyn:
  inne sformułowanie niż wzorzec (124), wartości zależne od typu obiektu (22), udział
  podany jako ułamek bez `%` (12), szum OCR (12), artefakt ekstrakcji `°`→`0` (8);
- confidence niesie informację, ale nie jest skalibrowane: błędnych 11/34 w paśmie
  < 0,5 (32%), 3/41 w paśmie 0,5–0,8 (7%) i 0/14 w paśmie ≥ 0,8; test Fishera niskie vs
  wysokie p = 0,013. Parser jest niedoszacowany (w paśmie 0,85 trafność 100%), ECE 0,244.
  Flaga `manual_review_required` wychwytuje 14 z 14 błędnych wartości;
- próbki tabelowe są tylko rozwojowe (jedna tabela Legnicy): w pobranych dokumentach
  końcowych nie znaleziono tabeli z parametrami stref, a parser zwrócił dla tabeli 0/10.

Kontrolny zestaw z celowo dodanym FP, FN i pomyłką strefy (`control.json`) daje ręcznie
policzone wartości: precision i recall 8/9, dokładność wartości 5/8, przypisanie 6/7,
Brier 0,15009, ECE 0,235, p Fishera 1/21 (test
`test_control_set_*`).

## BK-602 — wyniki

Reguła centroidu: `shapely` `parcel.centroid` w EPSG:2180, zachowany tam, gdzie wypada;
granica przez `covers`; remis (kilka stref) i brak strefy liczą się jako bez
rozstrzygnięcia; `representative_point` nie jest używany. Przecięcia liczą produkcyjne
`analyze_pog_vectors` i `calculate_mpzp_zone_intersections` (na kontrolach dają te same
pola).

1. **Kontrole** (prostokąt 60/40, wklęsły L, dziura, centroid na granicy) zgadzają się z
   ręcznymi oczekiwaniami; wklęsły L ma centroid poza działką w strefie-widmie (100%
   pominiętego obszaru), a dziura i granica odpowiednio `in_hole` i remis.
2. **Dane rzeczywiste, granice** (bez geometrii stref): 11 przypadków korpusu ma
   liczbowe udziały stref; w 3 z 11 (`real-007` Kraków: 97,63/2,33/0,04%, `real-028`:
   80,34/19,66%, `real-029`: 50,07/49,93%) jedno przypisanie **musi** pominąć co najmniej
   `k−1` stref i co najmniej 2,37 / 19,66 / 49,93% działki.
3. **Symulacja** na rzeczywistych geometriach 30 działek (18 cięć na działkę, 540
   scenariuszy, nie jest to zagospodarowanie gminy): pominięty obszar ≥ 10% w 384/540
   (71%), ≥ 25% w 230/540 (43%); strefa centroidu ≠ dominująca w 5/540 (0,9%, wszystkie w
   działkach o wypukłości < 0,80); centroid poza działką dla 2 działek i w dziurze dla 1.
   Wniosek: dla cięć prostoliniowych centroid zwykle trafia w strefę dominującą, ale gubi
   dużą część powierzchni — pominięcie *strefy* jest dla dwóch stref pewne z definicji.
4. **Dokładne wyniki na realnych strefach: nie zmierzone.** Zadanie wymaga geometrii
   stref POG z publicznego WFS RU; za zgodą użytkownika próbowano je pobrać, ale
   `rejestr-urbanistyczny.gov.pl` przekraczał czas połączenia (curl i przeglądarka
   aplikacji, kilka prób w czasie sesji; inne hosty GUGiK odpowiadały). `freeze_pog_zone_layers.py`
   jest gotowy i przetestowany na prawdziwej odpowiedzi RU z fixtures; po odzyskaniu
   dostępu wystarczy:

   ```bash
   python3 backend/scripts/freeze_pog_zone_layers.py --output-dir backend/tests/fixtures/centroid_zone_layers
   python3 backend/scripts/compare_centroid_intersection.py --zone-layers backend/tests/fixtures/centroid_zone_layers
   ```

   Geometrii MPZP Krakowa nie wolno redystrybuować (`contract_required`), więc dla
   `real-005…008` zostają granice z zamrożonych udziałów.

Kryterium „eksperyment używa całego właściwego podzbioru korpusu” jest spełnione
częściowo: granice obejmują wszystkie 11 przypadków z udziałami, symulacja wszystkie 30
działek, natomiast wynik dokładny dotyczy 0 z 11.

## Ustalenia wymagające decyzji (poza zakresem tych zadań)

1. `app/services/mpzp.py::_first_matching_attribute` przyjmuje `NULL` jako symbol strefy
   (RC-01). Zadanie utworzone osobno.
2. `app/services/pog_fetch.py::_canonical_crs` odrzuca legacy `srsName`
   `http://www.opengis.net/gml/srs/epsg.xml#2180`, którym odpowiada prawdziwe WFS RU
   (fixture `wfs_pog_getfeature_zone.xml` się nie parsuje). Runtime `fetch_pog_vector_data`
   zgłosiłby `POG_VECTOR_UNAVAILABLE`. Zadanie utworzone osobno; narzędzie zamrażające
   stosuje obejście tylko u siebie.
3. Etykiety ground truth: `real-008` ma `manual_review`/`document_parse` mimo identycznych
   dowodów co `real-005…007` (RC-03); klasy `flat/moderate/relief` nie mają definicji
   (RC-02). Wymagają decyzji właściciela korpusu.
4. Wyniki BK-004 w `results/reference-corpus/` z 23.09.2026 nie zawierają metryk ryzyk
   dodanych później do harnessu; badanie BK-601 uruchomiło bieżący kod.

## Zmiany kontraktu i konfiguracji

- brak migracji Alembic i brak zmian API;
- `scripts/build_mpzp_text_fixture.py`: opcjonalne `--pages`, `--ocr`, `--simulate-scan`,
  `--keep-pdf`; `source.json` ma dodatkowe pola (`document_sha256`, `content_length`,
  `extraction_method`, `ocr_used`, `ocr_engine_version`, `source_page_numbers`,
  `ocr_origin`); istniejące fixtures bez zmian;
- `scripts/evaluate_reference_corpus.py`: nowy tryb `--study` (`--study-dir`,
  `--error-analysis`, `--repeat`); tryb domyślny BK-004 bez zmian;
- nowe: `evaluate_mpzp_parser.py`, `compare_centroid_intersection.py`,
  `freeze_pog_zone_layers.py`, korpus `mpzp_evaluation/`;
- `frontend`: bez zmian (nie uruchamiano testów frontendu).

## Log pobrań (zgoda użytkownika: „do ok. 12 dokumentów”)

Pobrano 14 różnych dokumentów (więcej niż przybliżone 12, bo cztery posłużyły tylko do
przeglądu) produkcyjnym fetcherem w kontenerze; zapisano wyłącznie tekst stron. Użyte w
korpusie (10): Warszawa Falenica A2 (305 KB), Białystok LXXXII/1136/24 (4,6 MB), Szczytno
XXXVIII/262/2021 (1,6 MB), Szczytno LXXVI/555/2023 (1,7 MB), Ostrów Wlkp. XI/105/2025
(2,0 MB), Raszków XXXVI/242/2021 (2,1 MB), Krzemieniewo X/87/2025 (3,2 MB), Libertów
XXV/213/05 (73 KB), Łódź VI/214/19 (HTML, 167 KB), Pisz XXV/254/98 (skan, 456 KB). Tylko
przegląd (4): projekt MPZP Puław (156 KB), wykaz planów Legnicy (85 KB), Włosań (90 KB),
Konary (82 KB). URL i SHA-256: `mpzp_evaluation/manifest.json`. Pobranie geometrii POG z RU
nie powiodło się (brak połączenia). Nie wyłączano weryfikacji TLS; dwa dzienniki z błędem
certyfikatu pominięto.

## Ograniczenia

- BK-601: pokrycie produkcyjne ogranicza się do pól działki i mapowania kontraktu ryzyk;
  zamrożone obserwacje nie pozwalają zmierzyć przecięć stref, ISOK, GDOŚ ani NMT;
- BK-602: patrz wyżej (brak realnych geometrii stref);
- BK-603: jeden anotator (AI), bez zgodności międzyanotatorskiej; próbki tabelowe i OCR
  rzeczywiste są nieliczne (1 i 2), więc przedziały są szerokie; dwie próbki OCR są
  symulacją skanu; dla czterech próbek rozwojowych anotator znał część oczekiwań
  regresji; dwie uwagi w metadanych próbek zmieniono po pierwszym uruchomieniu parsera
  (wartości anotacji niezmienione, potwierdzone porównaniem), co zmieniło skrót zamrożenia;
- wyniki są pomiarem w danym dniu i na danych, nie gwarancją jakości dla innych gmin.

## Weryfikacja

Środowisko: obraz backendu (`python:3.13`, Tesseract 5.5), PostGIS 16-3.4, osobny projekt
Compose, kopia repozytorium bez `.env` jako `/repo` tylko do odczytu (jak w CI).
Logi: `results/bk-601-603/`.

- pełny zestaw: `pytest -m 'not docker_cli' --cov=app --cov-report=term-missing
  --cov-fail-under=80` — **2050 przeszło**, 3 odznaczone, pokrycie aplikacji **92,80%**
  (próg 80% nie został obniżony); `backend-full-ci-like.txt`;
- testy zmienionych modułów z pokryciem (132 testy): `compare_centroid_intersection.py`
  96%, `evaluate_mpzp_parser.py` 96%, `evaluate_reference_corpus.py` 95%,
  `freeze_pog_zone_layers.py` 87%; `backend-targeted-coverage.txt`;
- pierwszy bieg w kontenerze wykazał dwa błędy własnych testów (ścieżki kodu liczone od
  `REPO_ROOT` oraz zapis do katalogu tylko do odczytu); poprawiono je i powtórzono cały zestaw;
- testy nie łączą się z siecią (gniazda zablokowane w testach determinizmu BK-601, BK-602
  i BK-603); narzędzie zamrażające geometrie testowano z mockiem `respx` na prawdziwej
  odpowiedzi RU z fixtures;
- statyczne: `ruff check` bez uwag dla zmienionych plików;
- frontend: bez zmian, testów nie uruchamiano.

Powtarzalność: skrót merytoryczny BK-601 jest identyczny w trzech biegach w procesie i w
dwóch osobnych procesach CLI; BK-602 i BK-603 porównują dwa biegi i kończą kodem 1 przy różnicy.
