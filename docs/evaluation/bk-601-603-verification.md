# Odbiór BK-601, BK-602 i BK-603

Data: 30 września 2026 r. Baza kodu: `cbefc0a` z niezacommitowanymi zmianami tego
odbioru (manifesty zapisują `worktree.dirty` i odcisk kodu).

## Stan zadań

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| BK-601 — badanie poprawności | wykonane | udziały stref POG/MPZP/OUZ i NMT w harnessie BK-004 są odczytem zamrożonych obserwacji, więc ich zgodność nie dowodzi poprawności przecięć; raport to rozdziela |
| BK-602 — centroid vs przecięcie | **wykonane (AU-112, 2026-10-17)** | wynik dokładny: 13 działek, 3 gminy; surowe odpowiedzi RU i geometrie z SHA-256; dwa identyczne przebiegi offline |
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
4. **Dokładne wyniki na realnych strefach — wykonane 17.10.2026 (AU-112).**
   RU jest osiągalny. Żądanie używa URN w `bbox` z osiami północ,wschód oraz
   `srsName=EPSG:2180` dla geometrii w kolejności wschód,północ. Warstwy
   zamrożono dla **13 rzeczywistych działek w 3 gminach**: Bielsko-Biała (7),
   Legnica (5), Sopot (1). Surowe strony (w tym druga strona Sopotu) i GeoJSON
   mają SHA-256 w `backend/tests/fixtures/centroid_zone_layers/manifest.json`.
   Wszystkie 30 działek pierwotnego korpusu odpytano; 18 pustych odpowiedzi
   zachowano jako `no_match` zapytania WFS, bez wnioskowania o braku planu.
   Sopot (`226401_1.0002.2/66`) jest **osobnym rozszerzeniem** korpusu,
   z geometrią ULDK i zachowaną odpowiedzią. Pierwotna prawda korpusu jest bez zmian.
   Dla 7 dawnych przypadków POG udziały zgadzają się z korpusem (≤ 0,1 p.p.);
   5 przypadków Legnicy i Sopot zweryfikowano niezależnym przecięciem Shapely.

   | Miara dokładna | Wynik |
   |---|---:|
   | Działki / gminy | **13 / 3** |
   | Działki z ≥ 2 przecinającymi strefami | 4 |
   | Centroid pomija co najmniej jedną strefę | **5/13 (38,46%)** |
   | Strefa centroidu inna niż dominująca | **1/13 (7,69%)** |
   | Centroid poza działką / wskazuje strefę bez powierzchni wspólnej | 1 / 1 |
   | Pominięta powierzchnia ≥ 10% / ≥ 25% | 3/13 / 2/13 |
   | Dwa przebiegi offline | identyczne |

   W `real-028` pominięcie wynosi 19,65934%, w `real-029` 49,92921%.
   W `real-013` (Legnica) centroid leży poza działką i wskazuje inną strefę:
   pomija 100% powierzchni. Dla Sopotu pomija 1,80936%.
   To pomiar przestrzenny wydania RU, nie ocena mocy prawnej POG. Parametry
   urbanistyczne nie są mierzone przez ten eksperyment (zamrażamy geometrię i symbol).

   ```bash
   python backend/scripts/freeze_pog_zone_layers.py --corpus backend/tests/fixtures/centroid_corpus/manifest.json --all-parcels --output-dir backend/tests/fixtures/centroid_zone_layers
   # Po przejściowym timeout: to samo polecenie z --resume (ponawia tylko błędy).
   python backend/scripts/compare_centroid_intersection.py --corpus backend/tests/fixtures/centroid_corpus/manifest.json --zone-layers backend/tests/fixtures/centroid_zone_layers --repeat 2 --output-dir docs/evaluation/results/au112-113/centroid
   ```

   Wynik: [raport dokładny](results/au112-113/centroid/report.md),
   [wyniki JSON](results/au112-113/centroid/summary.json),
   [determinizm](results/au112-113/centroid/determinism.json).
   Geometrii MPZP Krakowa nadal nie redystrybuujemy (`contract_required`);
   dla `real-005…008` pozostają granice z zamrożonych udziałów.

Część dokładna BK-602 jest zamknięta dla POG (wszystkie 7 dawnych przypadków
z udziałami + 6 dodatkowych). Pierwotna symulacja 30 działek powyżej pozostaje
wynikiem historycznym; przebieg z rozszerzeniem Sopotu ma 31 działek i 558 symulacji.

## Ustalenia wymagające decyzji (poza zakresem tych zadań)

1. **Zamknięte (AU-112):** literał `NULL` nie jest symbolem strefy. Funkcję starego
   `mpzp.py::_first_matching_attribute` zastąpił wcześniej adapter KIMPZP z
   `clean_value`; nowy jawny test `test_real_pisz_null_is_never_a_zone_symbol`
   potwierdza zachowanie na niezmienionej rzeczywistej odpowiedzi z Pisza.
2. **Zamknięte (AU-112):** produkcyjne `_canonical_crs` rozpoznaje legacy
   `http://www.opengis.net/gml/srs/epsg.xml#2180` (i HTTPS), bez odczytu URL.
   Regresja parsuje oryginalne `wfs_pog_getfeature_zone.xml` bez przepisania XML;
   obejście w narzędziu zamrażającym usunięto.
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
Konary (82 KB). URL i SHA-256: `mpzp_evaluation/manifest.json`. W pierwotnej próbie
pobranie geometrii POG z RU nie powiodło się. AU-112 z 17.10.2026 usunął tę blokadę:
surowe odpowiedzi i geometrie z trzech gmin są zamrożone, a wynik dokładny jest powyżej.
Nie wyłączano weryfikacji TLS; dwa dzienniki z błędem certyfikatu pominięto.

## Ograniczenia

- BK-601: pokrycie produkcyjne ogranicza się do pól działki i mapowania kontraktu ryzyk;
  zamrożone obserwacje nie pozwalają zmierzyć przecięć stref, ISOK, GDOŚ ani NMT;
- BK-602: zamknięte dla POG (AU-112: 13 działek / 3 gminy); MPZP nadal bez redystrybucji geometrii;
- BK-603: jeden anotator (AI), bez zgodności międzyanotatorskiej; próbki tabelowe i OCR
  rzeczywiste są nieliczne (1 i 2), więc przedziały są szerokie; dwie próbki OCR są
  symulacją skanu; dla czterech próbek rozwojowych anotator znał część oczekiwań
  regresji; dwie uwagi w metadanych próbek zmieniono po pierwszym uruchomieniu parsera
  (wartości anotacji niezmienione, potwierdzone porównaniem), co zmieniło skrót zamrożenia;
- wyniki są pomiarem w danym dniu i na danych, nie gwarancją jakości dla innych gmin.

## Weryfikacja pierwotna BK-601–603

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

Ponowna weryfikacja AU-112 (19.10.2026): pełny backend **5351 passed**, pokrycie
**95,33%**, `pog_fetch` 84%. Regresje używają niezmienionych rzeczywistych odpowiedzi
RU i KIMPZP. Zbiorcze logi oraz zakres ostatnich zmian:
[odbiór AU-112, AU-113 i AU-201](au112-113-au201-verification.md).
