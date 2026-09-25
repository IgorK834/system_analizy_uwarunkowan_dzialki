# Odbiór BK-202 i BK-203

Data odbioru: 25 września 2026 r.

- **BK-202** — lokalny, wersjonowany wektor MPZP i przecięcia pełnego obrysu
  działki.
- **BK-203** — powiązanie strefy z wektora z cytowalnym parametrem uchwały.

Decyzje: `docs/adr/ADR-004-mpzp-vector-zones-and-parameter-evidence.md`.
Kontrakt źródeł: `docs/data_sources/mpzp_contracts.md` (sekcja „Wektor stref
w analizie działki”).

## Zakres

| Warstwa | BK-202 | BK-203 |
|---|---|---|
| Import | stabilne `zone_identifier`, opcjonalne `document_url`, odrzucenie duplikatu ID | — |
| PostGIS | migracja `018_mpzp_vector_zones` | migracja `019_mpzp_parameter_evidence` |
| Repozytorium | `find_mpzp_zone_intersections(parcel, as_of, data_release_id)`, `find_plan_intersections(as_of)` | zapis audytu dokumentu powiązany z aktem wektora |
| Serwis | `assess_vector_zones`, `cap_fallback_zone` (`mpzp_zones.py`) | `apply_parser_zone`, `DocumentEvidenceContext` |
| Parser | — | dokładny token symbolu, segment/strona dowodu, SHA dokumentu, `mpzp-parser/2.0`, grupy konfliktu, jawna kara OCR |
| Orkiestrator | wektor przed discovery, fallback z obniżoną pewnością | parser per akt/wersja z symbolami z geometrii |
| API/cache | `MpzpZoneResult` + `zone_id`, akt/wersja, wydanie, styczność, metoda, GeoJSON; sygnatura `pog-v2.2+mpzp-v2.0` | `parameters[]` z `MpzpParameterEvidence` |
| Snapshot | `mpzp_zones.result_snapshot` i kolumny provenance | wiersze evidence w `mpzp_parameters` |
| UI | `MpzpZoneCard` — pełna lista stref | tabela parametrów ze źródłem w uchwale |
| PDF | strefy z metodą, ID, planem i wersją | tabela evidence: strona, segment, jednostka, fragment, SHA, metoda |

## Scenariusze końcowe

### BK-202 — `backend/tests/test_mpzp_vector_zones.py` (PostGIS)

Każdy przypadek przechodzi przez `run_mpzp_import` → PostGIS →
`find_mpzp_zone_intersections` → `assess_vector_zones`:

- działka 1000 m² w kształcie L, której centroid (x = 62) leży w mniejszej
  strefie: **1MN 600 m²/60% i 2U 400 m²/40%**, dwa stabilne ID niezmienione
  po ponownym imporcie jako nowe wydanie;
- styczność granicy — osobny wpis `touches_boundary`, pole 0, bez dominacji;
- wydzielenie `MultiPolygon` — jedna pozycja z sumą obu części;
- wydzielenie z otworem — pole bez otworu; działka w otworze nie ma przecięć;
- geometria naprawiona przy imporcie (kokarda) i niepoprawna działka
  (`MPZP_PARCEL_GEOMETRY_REPAIRED`);
- nakładanie dwóch planów — udziały 100% + 50% bez normalizacji,
  `MPZP_ZONES_OVERLAP` i `MPZP_MULTIPLE_ACTS`;
- obszar poza pokryciem — pokrycie 50%, `MPZP_PARTIAL_COVERAGE`;
- `as_of`/`data_release_id` — po imporcie nowej wersji przypięty `as_of` zwraca
  poprzednie ID i udziały.

### BK-203 i połączenie warstw — `backend/tests/test_mpzp_parameter_evidence.py`

- Tekstowy PDF (pdfplumber) z dwiema strefami tej samej uchwały: 1MN — 9 m,
  2MN — 12 m, strona 2, różne segmenty, SHA-256 dokumentu, wersja parsera.
- Dwie sprzeczne wysokości tej samej strefy: obie zachowane ze wspólnym
  `conflict_group_id`, płaskie pole `null`, `manual_review_required`, wynik
  `partial`.
- Ten sam dokument jako skan + OCR: te same wartości, `extraction_method=ocr`,
  confidence dokładnie ×0,85.
- Symbole `MN`, `MN.1` i `U` (przy tekście `MW/U`) nie są scalane przez podciąg.
- **`run_analysis` end-to-end**: import aktu z `document_url` → przecięcie L-kształtnej
  działki → parser dostaje `["1MN", "2MN"]` → wysokości przypisane do stref,
  evidence z `legal_unit_id` i `document_version_id` → zapis → odczyt z bazy
  identyczny ze snapshotem → HTML raportu ze stroną, fragmentem, jednostką i SHA
  → import nowej wersji aktu nie zmienia starego snapshotu, a nowa analiza (nowe
  wydanie ⇒ brak trafienia cache) daje 80/20.
- Brak wektora: fallback z discovery działa, `document_candidate`, confidence
  ≤ 0,5, `MPZP_VECTOR_UNAVAILABLE`, wynik `partial`.
- Akt z wektora bez dokumentu: parametry `null`, `MPZP_ACT_DOCUMENT_MISSING`.

### Migracje — `backend/tests/test_migration_018_019_mpzp.py`

Upgrade 017→019 na danych w schemacie 017: ID wydzieleń uzupełnione formułą
zgodną z importem (duplikat dostaje sufiks, nie znika), stare strefy analiz
czytane jako `legacy`, `CHECK` odrzuca nieznaną metodę przypisania; downgrade
do 017 i ponowny upgrade. Test porównuje też formułę migracji z ID nadanym
przez import dla tej samej geometrii.

## Artefakty

`docs/evaluation/results/bk-202-203/` (wygenerowane w kontenerze, WeasyPrint):

- `mpzp-two-zones-report.pdf` — raport: dwie strefy z metodą przypisania, ID,
  planem, wersją, wydaniem i tabelą evidence;
- `mpzp-two-zones-result.json` — wynik: 60/40, wysokości 9/12 m, evidence
  każdej wartości.

## Polecenia odbiorowe

```bash
cd backend
DATABASE_URL=postgresql+psycopg2://app:app@localhost:5432/dzialki \
python3 -m pytest tests/test_mpzp_vector_zones.py \
  tests/test_mpzp_parameter_evidence.py tests/test_migration_018_019_mpzp.py \
  tests/test_imports_mpzp.py tests/test_mpzp_zones.py tests/test_mpzp_parser*.py -q

cd ..
docker compose build backend
docker compose run --rm -v "$PWD:/repo:ro" -e REPO_ROOT=/repo backend \
  pytest -m 'not docker_cli' --cov=app --cov-report=term-missing \
  --cov-fail-under=80 -v

docker compose run --rm -v "$PWD/docs/evaluation/results/bk-202-203:/out" backend \
  python scripts/generate_mpzp_evidence.py

cd frontend
npm ci
npm run typecheck
npm run test:coverage -- --run
npm run build
```

## Wyniki

**Backend** — dokładne polecenie CI w jednym procesie, świeża baza PostGIS
(migracje 001→019), bieżący kod zamontowany do obrazu `backend` (wymagania
Pythona bez zmian), kopia repozytorium bez `.env`: **1383 passed,
3 deselected (`docker_cli`), kod 0, coverage 90,17%**; próg 80% bez zmian.

Pokrycie zmienionych modułów: `mpzp_zones` 96%, `mpzp_parser` 95%,
`mpzp_parser_segment` 93%, `mpzp_parser_validate` 100%, `analysis_orchestrator`
87%, `analysis_resume` 90%, `persistence` 98%, `report` 95%, `cache` 100%,
`schemas/analyze` 99%, `schemas/mpzp` 100%, `models/mpzp_zone` 100%,
`models/mpzp_parameter` 100%, `imports/domain/mpzp` 95%,
`imports/application/mpzp_import` 98%, `imports/infrastructure/repository` 92%,
`imports/infrastructure/mpzp/reader` 83%.

**Frontend** (Node 25.6 lokalnie; lock bez zmian): `npm ci`, `typecheck`,
`build` — kod 0; `test:coverage` — 17 plików, 155 testów, 95,01% statements,
86,88% branches, 97,33% functions, 97,13% lines. `MpzpZoneCard.tsx` dodano do
`coverage.include` (100% linii).

## Odbiór interfejsu przed BK-701

Odbiór UI realizują testy komponentu `MpzpZoneCard` (dwie strefy 60/40 ze
stabilnymi ID w panelu, źródło wartości, konflikt, styczność, fallback,
snapshot legacy) oraz integracyjny HTML/PDF raportu z danych PostGIS.
Wygenerowany PDF sprawdzono wizualnie — tabele mieszczą się w marginesach A4.

## Ograniczenia

- Dokument uchwały dla aktu z wektora pochodzi z `document_url` źródła albo —
  przy jednym akcie — z discovery (z ostrzeżeniem). Żadne źródło produkcyjne z
  macierzy BK-201 nie ma jeszcze prawa publikacji wektora stref (Kraków MSIP
  wymaga zgody), więc ścieżka wektorowa działa na zaimportowanych danych i
  fixture'ach; produkcyjnie nadal dominuje fallback z obniżoną pewnością.
- Parametry opisowe (listy ustaleń) nie są traktowane jako konflikt; konflikt
  dotyczy wartości liczbowych jednej strefy.
