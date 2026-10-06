# Odbiór BK-501, BK-502 i BK-503

Data odbioru: 29 września 2026 r. Punkt wyjścia: `main` @ `ed8aee6`.

- **BK-501** (Task 6.1, P0) — raport v2: architektura informacji (10 sekcji).
- **BK-502** (Task 6.2, P0) — pełne tabele MPZP i POG w PDF.
- **BK-503** (Task 6.3, P0) — deterministyczne snapshoty map w raporcie.

Decyzje: `docs/adr/ADR-010-report-v2-and-deterministic-maps.md`.
Mapowanie pól API: `docs/report/field-mapping.md` (generowane).
Artefakty dowodowe: `docs/evaluation/results/bk-501-503/`.

## Zakres zmian

| Warstwa | BK-501 | BK-502 | BK-503 |
|---|---|---|---|
| Domena (`modules/reporting/domain`) | `sections.py` (10 sekcji, rodzaje ustaleń, statusy), `field_mapping.py` (wzorce pól API → sekcja/element/rodzaj albo uzasadnienie pominięcia) | — | `map_snapshot.py` (kanoniczny JSON, hash semantyczny, kadr metryczny, podziałka 1-2-5, klasy tematu, walidacja) |
| Serwisy | `report.py` (kontekst 10 sekcji, wspólna ocena dla podsumowania i macierzy jakości, `url_fetcher` tylko `data:`), `report_fields.py` (introspekcja modelu, obecność wartości/null, eksport Markdown) | `report.py` (wiersz na strefę, parametry z jednostkami, rejestr evidence `[E#]`/`[D#]`, sprzeczności z kandydatami, suma udziałów w pp bez korekty, grupy OUZ/OZS/OSDIS) | `report_map_snapshot.py` (zamrożenie), `report_map.py` (render Pillow ze snapshotu), `report_map_basemap.py` (podkład tylko z artefaktu z SHA-256, bez HTTP) |
| Szablon | `app/templates/report.html` (wydzielony, `autoescape`, `StrictUndefined`, spis treści z numerami stron) | CSS długich tabel: `thead` powtarzany, `tr` bez łamania, `colgroup`, dzielenie wyrazów; brak `nowrap` | podpis mapy: tryb, układ, podziałka, kadr, daty i wydania, styl, konfiguracja, hashe, tło |
| Persystencja / migracja | — | — | `analyses.report_map_snapshot` (JSONB), migracja `025_report_map_snapshot` (bez uzupełniania wstecz); zamrożenie w `save_analysis`, odświeżenie przy wznowieniu MPZP |
| Konfiguracja | — | — | `REPORT_MAP_POG_THEME`, `REPORT_MAP_BASEMAP_ARTIFACT_DIR`, `REPORT_MAP_CONFIG_VERSION`; usunięte `REPORT_MAP_BASEMAP_ENABLED`, `REPORT_MAP_WMS_*`, `REPORT_MAP_KIMPZP_OVERLAY_ENABLED`, `REPORT_MAP_KIUT_OVERLAY_ENABLED` |
| API | `GET /report/{analysis_id}` bez zmian kontraktu (PDF, `Content-Disposition`, 403/404/429/500) | — | — |
| Frontend | `ReportDownloadButton` opisuje zakres raportu (10 sekcji ze snapshotu, bez oceny punktowej) | — | — |
| Dokumentacja | README, ADR-010, `docs/report/field-mapping.md`, `docs/current_state.md`, adnotacje w `backlog.md` | | |

Zmiany obok zakresu: publiczny alias `PARSER_TO_API_PARAMETER_MAP` w
`mpzp_zones.py` (grupowanie evidence po polu API); usunięto nieużywane
`RISK_LAYER_STYLE`, `POG_OVERLAY_ORDER`. Testy istniejące, które sprawdzały stary
układ (nagłówki sekcji, 1 miejsce po przecinku, nakładki WMS), dostosowano do
nowego układu bez osłabiania semantyki (np. kolejność sekcji po `id="sec-*"`).

## Kryteria akceptacji

### BK-501

| Kryterium | Dowód |
|---|---|
| Raport wielostrefowej działki ma 10 sekcji i jawny powód braku danych w każdej pustej sekcji | `test_report_has_ten_sections_in_order_and_appendix`, `test_every_empty_section_states_an_explicit_reason` (MPZP/POG/środowisko/teren/KIUT/źródła puste — każda sekcja ma powód), `test_full_analysis_pdf_contains_all_sections_and_polish_characters` (PDF z bazy); `multizone.pdf`, `sparse.pdf` |
| Zablokowany HTTP: raport ze snapshotu, bez `run_analysis` i bez pobrań | `test_report_is_generated_from_snapshot_with_network_blocked` (gniazda Pythona zablokowane, `run_analysis` podmienione na błąd, `GET /report` → 200, 0 prób połączeń), `test_release_b_does_not_change_report_of_snapshot_a` (to samo w scenariuszu końcowym), `test_offline_fetcher_refuses_network_and_files`, `test_renderer_modules_have_no_http_client` |
| PyMuPDF i obrazy stron: brak obcięć, nakładania, zgubionych polskich znaków | `test_pages_render_without_clipping_overlaps_or_lost_polish_letters[multizone|project|long_tables]`: linie tekstu w marginesach strony, brak przecięć linii i obrazów (> 2 pt²), każda strona renderuje się do obrazu (niepusta), fonty wyłącznie DejaVu, brak U+FFFD, wszystkie polskie litery z HTML obecne w tekście PDF. Test wykrył dwie realne kolizje (plakietka `nowrap` wychodząca na kolumnę źródła; nagłówek „Pole strefy [m²]”) — poprawione w CSS. Obrazy stron: `pages/*.png` |
| Każde istotne pole API ma odpowiednik lub uzasadnione pominięcie | `test_every_api_leaf_field_has_a_report_counterpart_or_reason` (517 ścieżek liści), `test_every_mapping_pattern_is_used_and_well_formed`, `test_omissions_are_few_and_never_hide_findings` (9 pominięć z uzasadnieniem), `test_mapping_documentation_is_in_sync_with_code`; załącznik A w każdym PDF z liczbą wartości obecnych/null |
| Fakt / obliczenie / przybliżenie, bez scoringu | oznaczenia w tabelach i podsumowaniu, `test_summary_statuses_are_consistent_with_quality_matrix`, sekcja 10 „Raport nie zawiera syntetycznej oceny (scoringu)” |

### BK-502

| Kryterium | Dowód |
|---|---|
| 3 strefy POG i 2 MPZP mają dokładnie odpowiadające wiersze, wartości i evidence | `test_three_pog_zones_and_two_mpzp_zones_have_exact_rows` (dokładnie 3/3/2 wiersze, ID, pole 800,00 m², udział 33,33%, parametry z jednostkami, MPZP 1 500,00 m²/62,50% i 900,00 m²/37,50%; te same wartości w tekście PDF), `test_mpzp_evidence_refs_pages_hashes_and_conflict_candidates` (E1–E9, strona/segment/jednostka, dokumenty D1/D2 z SHA-256, sprzeczność „kandydaci: 2 kondygn. [E2]; 3 kondygn. [E3]”) |
| null/0, długi polski tekst, ≥ 30 wierszy — bez utraty treści, render wszystkich stron | `test_zero_is_numeric_and_null_is_not_specified`, `test_long_tables_keep_every_row_and_long_polish_text` (32 strefy POG, 45 wpisów evidence z długim tekstem, 39 stron; nagłówki tabel powtórzone na ≥ 2 stronach), parametr `long_tables` testu układu; `long_tables.pdf`, `pages/long_tables-p0{7,8}.png` |
| Brak średniej parametrów; projekt nie jest przedstawiany jako akt obowiązujący | `test_no_parameter_average_across_zones`, `test_project_is_never_presented_as_binding_act` (brak „akt obowiązujący”, „obowiązuje (potwierdzone…”, „Początek obowiązywania”; plakietka „projekt / dane niewiążące” w każdym wierszu strefy); `project.pdf`, `pages/project-p08.png` |

### BK-503

| Kryterium | Dowód |
|---|---|
| Snapshot A: te same granice, wartości, legenda i skala przed i po publikacji B z inną geometrią | **Scenariusz końcowy** `test_release_b_does_not_change_report_of_snapshot_a` (PostGIS): import realnych rekordów APP Sopotu do wydania #87 → analiza (`1POG-100SU`, 573,42 m², 4 m) → `save_analysis` (zamrożenie) → `GET /report`; publikacja wydania #88 z przesuniętą strefą i wysokością 33 m → nowa analiza widzi 400,10 m²/33 m (inny hash semantyczny) → ponowny `GET /report` analizy A: identyczny snapshot, PNG, legenda, kadr i podziałka, hash `4a326cc3…` w tekście, brak „33 m” i „#88”, 0 prób połączeń (`e2e/summary.json`, `e2e/report-a.pdf`, `e2e/report-a-after-release-b.pdf`). Jednostkowo: `test_snapshot_a_is_unchanged_after_release_b_and_config_change` (zmiana palety POG, wersji konfiguracji i stylu warstwy), `test_regenerated_report_keeps_frozen_map_after_config_change` (piksele obrazów w PDF identyczne) |
| Render blokuje HTTP; brak podkładu nie blokuje raportu | fixture `no_network` we wszystkich testach renderu; `test_stored_basemap_artifact_is_used_only_with_matching_sha` (artefakt z SHA → użyty; podmieniony/brak → neutralne tło + adnotacja), `test_basemap_selection_requires_permission_and_full_frame_cover`, `test_report_without_maps_adds_explicit_notice_not_error`, `test_report_survives_map_renderer_failure` |
| Mapy projektu, wielu stref, ryzyka i null — porównania referencyjne; data/style/release utrwalone | `test_reference_maps_match[parcel|multizone_mpzp|multizone_pog|project_pog|risk_environment|null_height_pog]`: hash semantyczny zawsze równy referencji, różnica pikseli ≤ 1%, bajtowy SHA-256 PNG gdy środowisko (Pillow 12.3.0 + SHA-256 DejaVuSans) identyczne; `maps/*.png`, `maps/reference.json`; snapshot zawiera `style_version`, `data_release_ids`, `data_dates`, `config_version`, font (`test_snapshot_freezes_geometry_frame_style_mode_and_releases`) |
| Stare analizy | `test_old_analysis_keeps_null_snapshot_and_report_rebuilds_maps` (migracja 025: downgrade/upgrade, brak uzupełniania wstecz, PDF z adnotacją „Mapa odtworzona…”), `test_legacy_analysis_without_map_snapshot_rebuilds_maps_with_notice`, `test_resume_refreshes_frozen_map_snapshot` |

## Polecenia i wyniki

Backend — jak `.github/workflows/ci.yml`: izolowany projekt Compose z czystą bazą
PostGIS i bez `.env` (`--env-file /dev/null`), repozytorium pod `/repo`:

```bash
docker compose -p bk501 --env-file /dev/null build backend
docker compose -p bk501 --env-file /dev/null run --rm -v "$PWD:/repo:ro" -e REPO_ROOT=/repo \
  backend pytest -m 'not docker_cli' --cov=app --cov-report=term-missing --cov-fail-under=80
```

Wynik: **1787 passed, 1 failed, 3 deselected; pokrycie 92,25%** (próg 80% spełniony).
Jedyny błąd, `test_no_env_files_in_repository`, wynika z lokalnego pliku `.env`
w zamontowanym drzewie roboczym (CI wykonuje `rm -f .env`); na kopii drzewa bez
`.env` `tests/test_repo_structure.py` daje 6 passed. Zmienione moduły: `report.py`
98%, `report_map.py` 92%, `report_map_snapshot.py` 97%, `report_map_basemap.py`
98%, `report_fields.py` 99%, `reporting/domain/*` 99–100%, `report_config.py`
95%, `routers/report.py` 100%, `persistence.py` 97%, `analysis_resume.py` 99%
(`bk-501-503/backend-pytest-summary.txt`).

Frontend (Node 22, obraz `--target test`):

```bash
docker build --build-context shared=./shared --target test -t dzialki-frontend-test ./frontend
docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 dzialki-frontend-test \
  sh -c "npm run typecheck && npm run test:coverage && npm run build"
```

Wynik: typecheck OK, **354 passed (37 plików)**, pokrycie 98,49% linii / 90,69%
gałęzi, `next build` bez błędów (`bk-501-503/frontend-summary.txt`). Nie dodano
nowych modułów frontendu (`ReportDownloadButton.tsx` już jest w `coverage.include`).

**Odstępstwo środowiskowe (jawnie):** podczas odbioru pobieranie metadanych
obrazów bazowych (`python:3.13-slim`, `node:22-slim`) zawieszało się na pomocniku
poświadczeń Docker Desktop, więc `build` nie zakończył się lokalnie. Testy
uruchomiono w obrazach zbudowanych wcześniej z tych samych `Dockerfile`,
`requirements.txt` i `package-lock.json` (backend: 29.09.2026 16:46, po ostatniej
zmianie zależności; frontend: lock niezmieniony od 20.07.2026), z aktualnym
kodem zamontowanym (`app`, `tests`, `alembic`, `scripts`; frontend: `app`,
`components`, `lib`, `hooks`, `test`, configi, `shared`). Środowisko: Python
3.13, WeasyPrint 70.0, Pillow 12.3.0, PyMuPDF 1.28.2, PostGIS 16-3.4.

Odświeżenie artefaktów: `python -m tests.fixtures.reports.build_fixtures --maps`
(fixtures i mapy referencyjne), `python backend/scripts/export_report_field_mapping.py`
(dokument mapowania), `REPORT_EVIDENCE_DIR=/evidence pytest tests/test_report_v2.py
tests/test_report_e2e.py` (PDF i obrazy stron).

## Odbiór UI (przed BK-701)

Automatycznie: `ReportDownloadButton.test.tsx` (5 testów: blokada bez analizy,
pobranie PDF z tokenem i bezpieczną nazwą, opis zakresu raportu v2, błąd i
ponowienie, anulowanie). Ręcznie przeglądano obrazy stron raportów
(`pages/*.png`, `e2e/report-a-*-p*.png`): spis treści z numerami stron, żywa
pagina z numerem i tytułem sekcji, mapy z legendą i podziałką, powtarzane
nagłówki długich tabel, brak kolizji tekstu.

## Znane ograniczenia i zadania następcze

- Podkład mapy jest domyślnie neutralny; mechanizm zapisanych artefaktów z SHA-256
  jest gotowy, ale import licencjonowanego podkładu (np. ortofoto) to osobne zadanie.
- Załącznik A dodaje ~5 stron do każdego raportu; BK-505 (pakiet audytowy) może
  przenieść pełną tabelę do `analysis.json`/`manifest.json`, zostawiając w PDF skrót.
- Hash bajtowy PNG jest porównywalny tylko w identycznym środowisku; dokładny lock
  zależności (BK-705) ustabilizuje go także między budowami obrazu.
- Przeglądarkowy E2E pobrania raportu — BK-701.
