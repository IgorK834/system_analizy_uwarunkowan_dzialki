# Odbiór BK-204 i BK-205

Data odbioru: 26 września 2026 r.

- **BK-204** (Task 3.4) — kontrolowany tryb ręcznego wskazania strefy MPZP dla
  planów bez wektora.
- **BK-205** (Task 3.5) — informacyjna, audytowalna ocena relacji MPZP–POG
  zamiast booleana `conflict_with_mpzp`.

Decyzje: `docs/adr/ADR-005-manual-mpzp-zone-and-mpzp-pog-compatibility.md`.
Kontrakt trybu ręcznego: `docs/data_sources/mpzp_contracts.md` (sekcja „Tryb
ręczny bez wektora”).

## Zakres

| Warstwa | BK-204 | BK-205 |
|---|---|---|
| Migracje | `020_manual_zone_pending_doc`: tabela `analysis_pending_documents`; historyczne 100% stref bez wektora → `NULL` | `021_compatibility_assessment`: kolumna JSONB, boolean → `legacy_evidence`, usunięcie `conflict_with_mpzp` |
| Modele | `AnalysisPendingDocument`, `Analysis.pending_document` | `PogData.compatibility_assessment` |
| Schematy API | `manual_zone_context`, `ManualZoneSelection`, udział strefy nullable (MPZP 2.1) | `CompatibilityAssessment`, `CompatibilityZonePair`, `CompatibilitySource`, `LegacyCompatibilityEvidence` (POG 2.3) |
| Orkiestrator | przypięcie uchwały przy wstrzymaniu, `partial` dla każdej strefy bez wektora | ocena wszystkich par stref, bez strefy dominującej i bez `raw_attributes.scenario` |
| Resume | tylko przypięty artefakt (SHA), `FOR UPDATE` + ponowny test statusu, jeden commit, flagi każdego parametru | ponowna ocena na zachowanym snapshocie POG |
| Router | `GET /analyze/{id}/pending-document`; 503 dla błędu przypiętego dokumentu | — |
| Reguły | walidacja symbolu wspólna UI/API | `rule_id`/`rule_version`/`source` dla każdej reguły tabeli |
| UI | `ManualZonePanel` (obraz → plan i dokument → kandydaci → formularz), `MpzpRasterPreview`, `MpzpZoneCard` (udział nieustalony, decyzja), komunikaty 404/409/503 | `CompatibilityAssessmentCard` jako osobna sekcja |
| PDF | baner „symbol strefy podano ręcznie”, decyzja, SHA, „udział nieustalony” | sekcja „Relacja MPZP–POG — analiza informacyjna” z parami, regułami, datą i źródłami |

## Scenariusze końcowe — `backend/tests/test_manual_zone_compatibility_e2e.py`

### BK-204: `waiting_for_zone_symbol` → podgląd źródła → symbol → `partial` → PDF

1. `POST /analyze` (discovery `raster_only`, obowiązujący POG, NMT) →
   `waiting_for_user_input`; w bazie `waiting_for_zone_symbol`;
   `manual_zone_context` z planem `MPZP/E2E/1`, kandydatami `1MN, 2MN` i
   przypiętym PDF (SHA-256).
2. `GET /analyze/{id}/pending-document` zwraca bajt w bajt przypiętą kopię.
3. Pod tym samym URL „publikowana” jest inna uchwała (1MN: 15 m).
4. `1 MN` → 422, nieznane `analysis_id` → 404; snapshot bez zmian, zero stref.
5. Resume `1MN` z realnym parserem tekstowego PDF (pdfplumber): `partial`,
   `manual_user_input`, udział `null`, `is_dominant=false`, wysokość **9 m** z
   przypiętej wersji (nie 15 m), każdy parametr `manual_review_required=true`,
   SHA dokumentu w evidence i `manual_selection`; POG i źródło NMT zachowane;
   para MPZP–POG nieprzestrzenna → nierozstrzygnięta.
6. Drugie resume → 409, nadal jedna strefa.
7. `GET /report/{id}` (WeasyPrint): tekst „symbol strefy podano ręcznie”,
   „nieustalony”, SHA dokumentu, sekcja relacji MPZP–POG.

### BK-205: API → DB → UI → PDF

Import wektora MPZP (1MN `single_family_housing` 60%, 2MN `production` 40%) +
obowiązujący POG (SJ 60%, SN 40%) → `run_analysis`: dwie pary zidentyfikowane
przestrzennie w EPSG:2180 (600 m² i 400 m², pary bez wspólnej części
pominięte), `1MN×SJ` compatible, `2MN×SN` incompatible, całość
`incompatible` (najsłabsze ogniwo), każda para z `rule_id`, `rule_version`,
`source`, `as_of`. Kolumna i `result_v2` identyczne, odczyt z bazy równy
odpowiedzi, status analizy nadal `partial`, brak wewnętrznego WKT w JSON. HTML i
PDF: MPZP → POG → osobna sekcja relacji, nota „nie przesądza o prawnej
możliwości zabudowy”, brak fraz typu „można zabudować”/„zgodność potwierdzona”.

### Pozostałe testy

- `test_analyze_resume.py` (31): 404/409, 422 dla 5 wariantów symbolu bez
  zmian snapshotu, 503 dla uszkodzonego (SHA) i nieczytelnego dokumentu bez
  zapisu, brak wywołania sieci, 409 dla równoległego resume zakończonego w
  trakcie parsowania, brak dublowania stref, brak przypiętego dokumentu, symbol
  spoza kandydatów, zachowanie POG v2/ryzyk/infrastruktury/NMT, nagłówki
  `pending-document` (PDF inline, HTML jako załącznik z CSP `sandbox`).
- `test_pog_scenarios.py` (35): wszystkie 5 statusów, wielostrefowość bez
  uśredniania, projekt/procedura/akt nieaktualny/potwierdzony brak/brak źródła
  jako odrębne ścieżki, brak reguły i nieznormalizowana funkcja → `unknown`,
  niepełne pokrycie → `uncertain`, strefa ręczna nigdy nie rozstrzygnięta,
  walidatory Pydantic, legacy.
- `test_migration_020_021.py`: upgrade 019→021 na danych (legacy boolean z
  `result_v2` i kolumny, fikcyjne 100% → `NULL`, wektor bez zmian), downgrade
  do 019 i ponowny upgrade; ograniczenia i kaskada tabeli dokumentów; zgodność
  zamrożonych tekstów migracji z kodem.
- `test_report_manual_zone_compatibility.py`: HTML raportu bez WeasyPrint.

## Artefakty — `docs/evaluation/results/bk-204-205/`

Wygenerowane w kontenerze backendu (WeasyPrint) przez scenariusze końcowe:

- `bk-204/01-waiting.json` — odpowiedź wstrzymania z `manual_zone_context`;
- `bk-204/02-resumed.json` — wynik po resume (`partial`, udział `null`,
  parametry z evidence i flagami);
- `bk-204/03-report.pdf` — raport z banerem „symbol strefy podano ręcznie”
  (strona 1) i kartą strefy z decyzją użytkownika i SHA (strona 3);
- `bk-205/result.json` — wynik z `compatibility_assessment` (dwie pary);
- `bk-205/report.pdf` — sekcja „Relacja MPZP–POG — analiza informacyjna”
  (strona 5).

PDF-y sprawdzono wizualnie — tabele mieszczą się w marginesach A4.

## Polecenia odbiorowe

```bash
cd backend
DATABASE_URL=postgresql+psycopg2://app:app@localhost:5432/dzialki \
python3 -m pytest tests/test_analyze_resume.py tests/test_pog_scenarios.py \
  tests/test_planning_compatibility.py tests/test_migration_020_021.py \
  tests/test_report_manual_zone_compatibility.py tests/test_mpzp_zones.py -q

cd ..
docker compose build backend
docker compose run --rm -v "$PWD:/repo:ro" -e REPO_ROOT=/repo backend \
  pytest -m 'not docker_cli' --cov=app --cov-report=term-missing \
  --cov-fail-under=80 -v

docker compose run --rm -v "$PWD/docs/evaluation/results/bk-204-205:/out" \
  -e EVIDENCE_OUT=/out backend \
  pytest tests/test_manual_zone_compatibility_e2e.py -v

cd frontend
npm ci
npm run typecheck
npm run test:coverage
npm run build
```

## Wyniki

**Backend** — dokładne polecenie CI w kontenerze `backend`, baza PostGIS z
migracjami do `021_compatibility_assessment`, kopia repozytorium bez `.env`
zamontowana jako `/repo`: **1433 passed, 3 deselected (`docker_cli`), kod 0,
coverage 90,59%**; próg 80% bez zmian. Scenariusze końcowe z `EVIDENCE_OUT`:
2 passed.

Pokrycie zmienionych modułów: `pog_scenarios` 100%, `analysis_resume` 99%,
`persistence` 99%, `schemas/analyze` 99%, `planning_compatibility` 97%,
`report` 97%, `mpzp_zones` 96%, `pog_analyzer` 94%, `analysis_orchestrator`
88%, `routers/analyze` 100%, `models/analysis_pending_document` 100%,
`models/pog_data` 100%, `models/analysis` 100%, `documents/composition` 100%,
`shared/planning_compatibility_text` 100%.

Lokalnie (macOS, Python 3.13) te same testy przechodzą poza tymi, które
generują PDF (brak bibliotek systemowych WeasyPrint) i `test_no_env_files`
(plik `.env` w katalogu roboczym) — stąd pełny przebieg w kontenerze, jak w CI.

**Frontend** (Node 25.6 lokalnie; `package.json`/lock bez zmian): `npm ci`,
`typecheck`, `build` — kod 0; `test:coverage` — 22 pliki, 209 testów,
95,67% statements, 88,8% branches, 97,89% functions, 97,6% lines. Do
`coverage.include` dopisano `ManualZonePanel.tsx`, `MpzpRasterPreview.tsx`,
`CompatibilityAssessmentCard.tsx`, `lib/zoneSymbol.ts`, `lib/compatibility.ts`
(każdy 100% linii).

## Odbiór interfejsu przed BK-701

Odbiór UI realizują testy komponentów i strony (bez przeglądarki):
`ManualZonePanel.test.tsx` (kolejność: obraz źródłowy → plan i dokument →
kandydaci → pole symbolu; link do przypiętej kopii przez API; niezweryfikowany
URL nieklikalny; walidacja jak w API; wymagane potwierdzenie porównania; brak
dokumentu; niedostępny podgląd), `MpzpRasterPreview.test.tsx` (9 kafli z proxy
`/api/v1/map/tiles/mpzp`, obrys działki w granicach mozaiki),
`CompatibilityAssessmentCard.test.tsx` (5 statusów bez stwierdzeń o możliwości
zabudowy, legacy, osobna sekcja po MPZP i POG) oraz `app/page.test.tsx`
(przepływ: wynik wstrzymany → podgląd → wybór kandydata → resume → `partial`
z notą o ręcznym symbolu, formularz znika). Klikalny przebieg w przeglądarce
automatyzuje BK-701.

## Ograniczenia

- Obraz źródłowy to bieżący podgląd KIMPZP z proxy (cache do 24 h), a nie
  zamrożony raster z chwili wstrzymania; zamrożony jest dokument uchwały.
- Przypięte dokumenty są w PostgreSQL (`bytea`); retencja do ustalenia z
  polityką magazynu artefaktów.
- Katalog funkcji MPZP obejmuje sześć wartości; opisowe `primary_use` z uchwał
  nie są klasyfikowane słownikiem, więc takie pary kończą się `unknown`.
