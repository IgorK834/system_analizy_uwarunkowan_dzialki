# Odbiór BK-106 i BK-107

Data odbioru: 24 września 2026 r.

- **BK-106** — rozdzielenie statusu prawnego aktu POG od pokrycia danymi i
  dostępności źródła. Decyzja: `docs/adr/ADR-002-pog-legal-status-and-coverage.md`.
- **BK-107** — provenance aktu i dokumentów RU (WFS → CSW → dokument + SHA).
  Decyzja: `docs/adr/ADR-003-pog-act-provenance-chain.md`.

## Zakres kontraktu

| Warstwa | BK-106 | BK-107 |
|---|---|---|
| Wspólny kod | `app/shared/planning_status.py` — kanoniczny enum, aliasy, kody urzędowe, tabela decyzyjna, komunikaty | `app/shared/provenance.py` — `is_verified_https_url`, typy łańcucha provenance |
| Import | `binding` tylko z urzędowym kodem (`PogActRecord.validate`), CLI przyjmuje kod urzędowy | parser RU: `gml:identifier`, `poczatekWersjiObiektu`, `obowiazujeOd/Do`, pełny `DokumentFormalny` + SHA C14N; `resolve_act_documents`; CSW przez `OgcClient` (`csw_metadata.py`) |
| PostGIS | migracja `016_pog_status_coverage` | migracja `017_pog_act_provenance` |
| Repozytorium | brak filtra `adopted`; `find_pog_acts_for_parcel`, `last_confirmed_pog_status`; `find_pog_intersections(legal_statuses=("binding",))` | `load_pog_act_provenance` |
| API | `PogResult` 2.1: `legal_status`, `coverage_status`, `data_availability`, `status_confirmed_at`, dowody; walidator aliasów | `PogResult` 2.2: `PogActResult` z publikacją, wersją, CSW, dokumentami; `gml_url` stref |
| Cache | sygnatura `pog-v2.1` | sygnatura `pog-v2.2` |
| UI | `frontend/lib/pogStatus.ts`, `ResultPanel`, `PreviewOverlays` | `PogOfficialSources.tsx`, `lib/safeLink.ts`, kolumna „Źródło” stref |
| PDF | status/zakres/aktualność, podstawa statusu, komunikaty | sekcja „Źródła urzędowe aktu”, dokumenty z SHA i ostrzeżeniami |

Status pusty WFS, timeout i brak geometrii nigdy nie są prezentowane jako brak
planu; dla `project`, `in_progress` i `unknown` UI/PDF nie używają słowa
„obowiązuje” (testy tablicowe w `test_planning_status.py`, `pogStatus.test.ts`,
`ResultPanel.test.tsx` i `test_pog_status_pipeline.py`).

## Scenariusze końcowe

### BK-106 — tabela decyzyjna i połączenie warstw

`backend/tests/test_planning_status.py` zawiera 20 wierszy tabeli, m.in.
projekt+available, binding+unknown, binding+act_without_spatial_data,
superseded, pusty WFS (także z pełną paginacją), urzędowe potwierdzenie braku
aktu, timeout bez i z ostatnią potwierdzoną wartością (`stale` z datą). Test
sprawdza, że tabela pokrywa każdą wartość wszystkich trzech wymiarów.

`backend/tests/test_pog_status_pipeline.py` przechodzi przez PostGIS → zapytania
repozytorium → orkiestrator → `PogResult` → HTML raportu dla: projektu
(dostępne strefy, brak w zapytaniu aktów wiążących, brak słowa „obowiązuje”),
aktu wiążącego bez geometrii, aktu nieaktualnego, pustej odpowiedzi RU i
timeoutu z ostatnią potwierdzoną wartością z innego wydania.

### BK-107 — łańcuch na realnej próbce Sopotu (226401)

`backend/tests/test_pog_provenance_chain.py` importuje zamrożone odpowiedzi RU
(akt, dokument, strefa, OUZ, OZS) i CSW `GetRecords` ISO 19139 (nowy fixture
`csw_getrecords_iso_226401.xml` z wpisem w `manifest.json`) przez
`WfsPogReader` i wspólny `OgcClient` na transporcie mock, bez internetu:

```text
strefa PL.ZIPPZP.10011/226401-POG/1POG-100SU (wersja 20260819T010000, URL GML)
  → akt PL.ZIPPZP.10011/226401-POG/1POG, wersja 20260819T010000,
    publikacja …/AktPlanowaniaPrzestrzennego/…/1POG/20260819T010000,
    początek wersji 2026-08-19T01:00Z, obowiązuje od 2026-08-19
  → CSW 9223a9d0-e8b7-453b-b7bc-000e5e4d4d87 (MD_Identifier …/226401-POG/,
    publikacja 2026-08-12; rekord MPZP z tej samej odpowiedzi nie jest wiązany)
  → dokument …/226401-POG/1 (przystąpienie, SHA-256 rekordu C14N)
    + dokument …/XXIV.300.2026 wskazany przez akt, bez rekordu → „niedostępny”
```

Test odtwarza SHA z zapisanego artefaktu ZIP (WFS + `90-csw-226401.xml`),
zapisuje analizę, zmienia „bieżący katalog” w PostGIS (tytuł i SHA dokumentu,
datę publikacji CSW) i potwierdza, że odczyt starej analizy oraz raport
wskazują pierwotną wersję bez odpytywania usług. Testy
`test_pog_ru_mapping.py` sprawdzają, że dwa dokumenty o tym samym tytule i
różnych wersjach nie są scalane (starsza wersja → `unresolved`), a
`test_pog_provenance_report.py` i `PogOfficialSources.test.tsx` — że linkowane
są wyłącznie zweryfikowane HTTPS, a tytuły są escapowane.

Artefakty scenariusza (wygenerowane w kontenerze, WeasyPrint):

- `bk-106-107/sopot-pog-report.pdf` — raport PDF (7 aktywnych linków),
- `bk-106-107/sopot-pog-result.json` — zapisany snapshot `PogResult` 2.2,
- `bk-106-107/sopot-import-outcome.json` — wynik importu z ostrzeżeniem
  `pog_document_unavailable`.

Odtworzenie zapytania CSW ze skryptu `backend/scripts/fetch_ru_contracts.py`
24 września 2026 r. dało ten sam URL co w manifeście i identyczne rekordy
(identyfikator, `MD_Identifier`, data publikacji, SHA rekordu C14N). SHA całej
odpowiedzi zmienia się przy każdym pobraniu (`SearchStatus timestamp`).

## Migracje

`016_pog_status_coverage` (po `015_pog_v2_release`) i `017_pog_act_provenance`
(po 016). Świeża baza PostGIS przeszła pełny łańcuch 001→017 w entrypoincie
kontenera. `test_migration_016_pog_status.py` zapisuje dane w schemacie 015
(`adopted` z kodem i bez, `not_available`, `outdated`, MPZP `adopted`, snapshoty
v1/v2 z przypiętym wydaniem i bez), wykonuje upgrade → downgrade → upgrade i
porównuje dokładne wartości; `CHECK` odrzuca `adopted`/`complete` w `pog_data`.
`test_migration_017_pog_provenance.py` sprawdza wersjonowaną unikalność
dokumentów, `CHECK` stanu powiązania i rollback (stratny dla dodatkowych wersji
dokumentu — tylko na odizolowanej bazie).

## Polecenia odbiorowe

```bash
cd backend
pytest tests/test_planning_status.py tests/test_pog_status_pipeline.py \
  tests/test_pog_ru_mapping.py tests/test_pog_csw_metadata.py \
  tests/test_pog_provenance_chain.py tests/test_pog_provenance_report.py \
  tests/test_migration_016_pog_status.py tests/test_migration_017_pog_provenance.py -q

cd ..
docker compose build backend
docker compose run --rm -v "$PWD:/repo:ro" -e REPO_ROOT=/repo backend \
  pytest -m 'not docker_cli' --cov=app --cov-report=term-missing \
  --cov-fail-under=80 -v

docker compose run --rm -v "$PWD/docs/evaluation/results/bk-106-107:/out" backend \
  python scripts/generate_pog_evidence.py

cd frontend
npm ci
npm run typecheck
npm run test:coverage -- --run
npm run build
```

## Wyniki

**Backend** — dokładne polecenie CI w jednym procesie, świeża baza PostGIS
(`dzialki_ci`, migracje 001→017), bieżący kod zamontowany do obrazu
`backend` (wymagania Pythona bez zmian), kopia repozytorium bez `.env`
(CI usuwa `.env` przed testami): **1353 passed, 3 deselected (`docker_cli`),
kod 0, coverage aplikacji 89,85%**; próg 80% bez zmian.

Pokrycie zmienionych modułów: `planning_status` 99%, `provenance` 92%,
`pog_provenance` 100%, `pog_scenarios` 98%, `pog` 95%, `pog_analyzer` 93%,
`analysis_orchestrator` 89%, `persistence` 98%, `report` 95%, `cache` 100%,
`schemas/analyze` 98%, `schemas/source` 100%, `models/pog_data` 100%,
`models/versioned` 100%, `imports/domain/pog` 94%, `pog/reader` 89%,
`pog/csw_metadata` 96%, `imports/repository` 91%, `pog_import` 86%,
`imports/composition` 95%, `imports/api/cli` 99%, `documents/worker` 98%.

**Frontend** (Node 25.6 lokalnie; lock bez zmian): `npm ci`, `typecheck` i
`build` — kod 0; `test:coverage` — 16 plików, 151 testów, 94,91% statements,
86,00% branches, 97,22% functions, 97,07% lines. Nowe moduły
`lib/pogStatus.ts`, `lib/safeLink.ts` i `components/PogOfficialSources.tsx`
dopisano do `coverage.include`.

### Naprawiona przyczyna „braku pamięci” z BK-105

Odbiór BK-105 raportował przekroczenie pamięci przy jednym procesie pytest z
coverage i dzielił przebieg na dwa procesy. Rzeczywistą przyczyną były dwa
testy `test_document_adapters.py`, które wywoływały
`_run_with_resource_limits` w procesie pytest i trwale ustawiały mu
`RLIMIT_AS=1536 MB` oraz `RLIMIT_CPU=90 s`. Pod coverage przebieg przekraczał
90 s CPU (SIGKILL, kod 137), a przy ~1,5 GB pamięci wirtualnej
`asyncio.to_thread` zgłaszał `can't start new thread`. Test wrappera działa
teraz w procesie potomnym, a test wykonawcy inline rejestruje limity zamiast je
ustawiać. Dzięki temu dokładne polecenie CI przechodzi w jednym procesie.

## Odbiór interfejsu przed BK-701

Odbiór UI jest realizowany testami komponentów (`ResultPanel.test.tsx`,
`PogOfficialSources.test.tsx`, `pogStatus.test.ts`) oraz integracyjnym testem
HTML/PDF raportu na danych z PostGIS. Wygenerowany PDF sprawdzono wizualnie:
sekcje statusu, stref ze źródłem GML, źródeł urzędowych aktu, dokumentów z SHA
i ostrzeżeniem o dokumencie niedostępnym mieszczą się w marginesach A4.

## Poza zakresem

Akty MPZP w `planning_act_versions` zachowują wartości `adopted`/`raster_only`
(ADR-002). Wznowienie analizy MPZP aktualizuje kolumny `pog_data`, ale nie
`result_v2` — zgłoszone jako osobne zadanie.
