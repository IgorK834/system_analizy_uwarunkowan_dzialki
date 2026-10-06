# Odbiór BK-504 i BK-505

Data odbioru: 29 września 2026 r. Punkt wyjścia: `main` @ `ed8aee6` z niezacommitowanymi
zmianami BK-501–503 (odbiór: `bk-501-503-verification.md`), na których oparto tę pracę.

- **BK-504** (Task 6.4, P0) — macierz kompletności i świeżości danych.
- **BK-505** (Task 6.5, P1) — pakiet audytowy analizy (`GET /report/{id}/audit.zip`).

Decyzje: `docs/adr/ADR-011-section-quality-matrix-and-audit-package.md`.
Artefakty dowodowe: `docs/evaluation/results/bk-504-505/`.

## Zakres zmian

| Warstwa | BK-504 | BK-505 |
|---|---|---|
| Wspólne pojęcia (`app/shared`) | `data_quality.py`: statusy, `FreshnessRule`, `evaluate_freshness` (czas z przyszłości/bez strefy/brak reguły → `unknown`), kody powodów z etykietami PL, hash kanoniczny | te same etykiety i polityka redystrybucji (`allows_derived`, `allows_raw`) |
| Katalog źródeł | `freshness_policy` per źródło (`max_age_days`, `basis`, `rationale`), walidator zakazuje TTL dla `expected_update_interval: unknown`, `quality_policy_version` | `redistribution` (`allowed`/`derived_only`/`forbidden`/`unconfirmed`), `no_redistribution` ⇒ `forbidden` |
| Schematy | `SectionQuality`, `FreshnessAssessment`, `SectionQualityMatrix` (legenda wyliczana), `AnalyzeResponse.section_quality` | — |
| Serwisy | `section_quality.py` (10 sekcji wg kontraktów źródeł), integracja w orkiestratorze, `save_analysis`, wznowieniu MPZP i odczycie (`persistence.py`), `RESULT_CONTRACT_VERSION` `+quality-v1.0`; `source_id` w adapterach ULDK, KIMPZP, KIUT WMS, POG | `audit_package.py` (snapshot → dane pakietu, warstwy GeoJSON per źródło, referencje `SourceArtifact`/wydań) |
| Moduł `reporting` | `domain/sections.py` (`QUALITY_SECTIONS`, etykiety statusów) | `domain/audit_package.py`, `application/audit_export.py`, `infrastructure/archive.py` |
| API | `section_quality` w odpowiedzi `/analyze` i odczycie z cache | `GET /report/{id}/audit.zip` (streaming, token jak raport, `404`/`413`/`500`), nagłówki `X-Audit-Package-SHA256`, `X-Audit-Exporter-Version` (CORS `expose_headers`) |
| Baza | migracja `026_section_quality_matrix` (`analyses.section_quality` JSONB, bez uzupełniania wstecz) | — |
| Raport PDF | podsumowanie i tabela 8.1 z zapisanej macierzy, 8.2 legenda, 8.3 wiek na dzień eksportu, 8.4 ostrzeżenia, suma kontrolna i wersja polityki w sekcji 9, mapowanie pól API (`section_quality.*`) | — |
| Frontend | `lib/quality.ts`, `SectionQualityMatrix.tsx` (karty sekcji + legenda + ostrzeżenie „na dziś”), typy, integracja w `ResultPanel`, `vitest.config.ts` | `getAnalysisAuditPackage` (walidacja ZIP i sumy SHA-256), drugi przycisk w `ReportDownloadButton` |
| Konfiguracja | — | `AUDIT_EXPORT_MAX_FILES`, `AUDIT_EXPORT_MAX_FILE_BYTES`, `AUDIT_EXPORT_MAX_TOTAL_BYTES` |
| Narzędzia | — | `backend/scripts/verify_audit_package.py` (offline, tylko biblioteka standardowa) |
| Dokumentacja | README, ADR-011, `backlog.md`, `docs/current_state.md`, `docs/report/field-mapping.md`, nagłówek katalogu źródeł | |

Zmiany kontraktu wartych odnotowania (opisane w ADR-011): status `manual` z raportu został
zastąpiony flagą `manual_review_required` (status opisuje kompletność, nie weryfikację);
`not_covered` → `out_of_scope` (transport) i `no_coverage` (brak pokrycia źródła, np. NMT lub
powiat bez GESUT); KIUT `covered` jest `partial` (podgląd nie jest geometrią sieci, BK-306);
`error` i `unavailable` są odrębne. Istniejące testy raportu dostosowano do nowego słownika
(`test_summary_statuses_are_consistent_with_quality_matrix`), fixtures raportu dostały
identyfikatory źródeł z katalogu (`build_fixtures.py`; mapy referencyjne bez zmian).

## Kryteria akceptacji

### BK-504

| Kryterium | Dowód |
|---|---|
| Każda sekcja, w tym pusta, ma status, źródło albo powód jego braku, czas/release i flagę manual | `test_every_section_has_status_source_or_reason_and_manual_flag[multizone|project|long_tables]`, `test_empty_response_has_every_section_with_explicit_reasons`, `test_reason_is_mandatory_when_source_is_missing_in_every_path`, `test_every_reason_code_the_builder_can_emit_has_a_polish_label`; UI: `SectionQualityMatrix.test.tsx` (10 kart, „brak źródła” + powód); PDF: `test_sections_without_source_state_the_reason_of_the_gap` |
| Status wg kontraktów źródeł; brak pokrycia ≠ błąd ≠ niedostępność | `test_risk_sections_distinguish_unavailable_error_and_unknown`, `test_terrain_no_coverage_is_not_an_error_and_relief_failure_is_partial`, `test_utilities_status_follows_the_kiut_preview_contract`, `test_pog_status_follows_coverage_and_availability_contract[7]`, `test_mpzp_status_variants`, scenariusz końcowy `test_no_coverage_error_unavailable_and_stale_are_distinct_states` (NMT brak pokrycia vs timeout, ISOK niedostępny vs błąd nieoczekiwany, stary pomiar) |
| Test zamrożonego zegara: świeży, stary, przyszły/nieprawidłowy czas, brak polityki; brak globalnego TTL | `test_data_quality.py` (granica dokładnie limitu = `fresh`, +1 s = `stale`, reguły per źródło, przyszłość poza tolerancją, czas bez strefy, brak czasu, tolerancja zegara), `test_fresh_stale_future_invalid_and_no_policy_on_frozen_clock`, `test_no_global_ttl_only_sources_with_rules_can_become_stale` (900 dni: tylko źródło z regułą jest `stale`), `test_real_catalog_has_explicit_per_source_rules_and_never_for_unknown_interval`, `test_catalog_rejects_invented_ttl_for_unknown_interval` |
| DB/cache/PDF zachowują historyczny stan; wiek eksportu nie zmienia hasha | scenariusz końcowy `test_matrix_is_the_same_in_api_db_reread_cache_and_pdf` (API = kolumna JSONB = odczyt = cache = PDF), `test_time_passing_and_late_export_never_rewrite_the_stored_assessment` (eksport +400 dni: ostrzeżenie w tabeli 8.3, zapis w bazie, `matrix_sha256` i hash semantyczny map bez zmian), `test_export_age_warning_is_separate_and_does_not_change_the_stored_assessment`, `test_matrix_hash_is_independent_of_export_time_and_visible_in_pdf`, `test_stored_matrix_does_not_move_when_time_or_policy_changes`, stare wiersze: `test_legacy_row_is_reconstructed_on_read_and_never_written_back`, `test_old_row_keeps_null_and_is_reconstructed_on_read_without_backfill` (migracja 026 w górę/w dół), wznowienie: `test_manual_zone_resume_issues_the_matrix_again` |
| Pokrycie zmienionych modułów ≥ 80%, globalne progi nie obniżone | zob. „Polecenia i wyniki” |
| Polecenia, wynik scenariusza końcowego, artefakty, opis zmian kontraktu/konfiguracji/migracji | ten dokument, ADR-011, README, `bk-504-505/` |

### BK-505

| Kryterium | Dowód |
|---|---|
| Pakiet rozpakowany offline przechodzi walidację każdego SHA; zmiana jednego bajtu wykryta | `test_every_sha_verifies_and_a_single_flipped_byte_is_detected` (każdy plik osobno: dokładnie jeden problem z nazwą pliku), `test_offline_verifier_accepts_unpacked_and_zipped_package_and_detects_changes` (ZIP i katalog, zmiana bajtu, obcięcie, brak i nadmiarowy plik, brak manifestu, zła suma całej paczki), `test_audit_package_end_to_end_from_persisted_snapshot`, `test_offline_verifier_cli_accepts_downloaded_package_and_rejects_a_modified_one`; ręcznie: `verify_audit_package.py` na Pythonie hosta (3.13.3) → `OK: 7 plików zgodnych z manifest.json` |
| Źródło z zakazem redystrybucji nie jest kopiowane; manifest opisuje pominięcie bez utraty referencji | `test_forbidden_source_is_not_copied_but_reference_and_hash_are_kept`, `test_omitted_layer_hash_matches_the_canonical_content_that_was_left_out`, `test_marker_of_forbidden_layer_and_raw_attributes_never_reaches_the_archive`, `test_derived_only_keeps_derived_layers_but_drops_raw_attributes`, `test_unconfirmed_and_unknown_sources_are_treated_as_forbidden`, `test_layer_with_mixed_sources_needs_every_source_to_allow`, `test_forbidden_parcel_source_keeps_only_reference_and_hash_for_the_geometry`, e2e: `test_forbidden_sources_are_only_referenced_never_copied` (współrzędne pominiętej warstwy nie występują w żadnym pliku), `test_without_the_source_catalog_nothing_is_copied_but_every_reference_stays`; `test_catalog_redistribution_rules` |
| Dwa eksporty tego samego snapshotu w tej samej wersji eksportera są deterministyczne | `test_two_builds_of_the_same_snapshot_are_byte_identical`, `test_zip_layout_is_sorted_with_fixed_timestamps_and_attributes` (posortowane nazwy, 1980-01-01, stała kompresja i atrybuty; kolejność wejścia bez znaczenia), `test_export_time_never_enters_the_package`, `test_export_modules_do_not_read_the_clock_or_the_network`, e2e `test_two_exports_of_the_same_snapshot_are_identical_even_later` (drugi eksport „za 400 dni”: identyczne bajty i nagłówek) |
| Brak analizy → 404; dostęp jak raport; limity | `test_access_control_and_error_statuses_match_the_report` (403 bez/złego tokenu, 404 z prawidłowym tokenem nieistniejącej analizy, 422 dla id ≤ 0), `test_route_uses_the_same_guards_as_the_pdf_report` (te same zależności: token i limit zapytań), `test_package_over_the_limit_is_413_not_a_truncated_archive`, `test_limits_reject_too_many_files_too_large_files_and_too_large_package`, `test_unexpected_failure_is_a_generic_500_without_internals` |
| CRS, README, bezpieczne nazwy, streaming | `test_crs_are_described_separately_and_geojson_is_wgs84`, `test_readme_describes_crs_analysis_date_statuses_and_verification`, `test_safe_entry_names_are_accepted`/`test_unsafe_entry_names_are_rejected` (Zip Slip, ścieżki absolutne, `..`, `\`, znaki spoza ASCII), `test_archive_passes_the_project_safe_extraction_helper` (`app.shared.safe_archive`), `test_streaming_yields_all_bytes_in_chunks_and_always_closes_the_file`, `test_large_archive_spills_to_disk_but_hash_matches` |
| Polecenia, wynik scenariusza końcowego, artefakty, opis zmian | ten dokument, ADR-011, README, `bk-504-505/` |

## Polecenia i wyniki

Backend — jak `.github/workflows/ci.yml`: izolowany projekt Compose (osobna baza PostGIS, porty
nie są publikowane, by nie dotykać stosu deweloperskiego), repozytorium bez `.env` pod `/repo`
(CI wykonuje `rm -f .env`), kod z drzewa roboczego zamontowany do obrazu backendu:

```bash
docker compose -p bk504 --env-file /dev/null -f docker-compose.yml -f override-bez-portow.yml run --rm \
  -v "$PWD/backend/app:/app/app" -v "$PWD/backend/tests:/app/tests" \
  -v "$PWD/backend/alembic:/app/alembic" -v "$PWD/backend/scripts:/app/scripts" \
  -v "<kopia-repo-bez-.env>:/repo:ro" -e REPO_ROOT=/repo \
  backend pytest -m 'not docker_cli' --cov=app --cov-report=term-missing --cov-fail-under=80
```

Wynik: ****1966 passed, 3 deselected (`docker_cli`), 0 failed; pokrycie 92,74%** (próg 80% spełniony i niezmieniony; kod wyjścia 0, w tym `test_no_env_files_in_repository` na kopii repozytorium bez `.env`)**. Zmienione i nowe moduły
(`bk-504-505/backend-pytest-summary.txt`): `shared/data_quality.py` 100%, `services/section_quality.py` 100%, `services/audit_package.py` 100%, `reporting/domain/audit_package.py` 100%, `reporting/application/audit_export.py` 98%, `reporting/infrastructure/archive.py` 94%, `schemas/source.py` 100%, `routers/report.py` 100%, `services/persistence.py` 97%, `services/report.py` 98%, `core/data_sources.py` 97%, `services/cache.py` 100%, `services/analysis_resume.py` 99%, `services/analysis_orchestrator.py` 88%, `services/kiut_coverage.py` 86%, `services/pog_fetch.py` 84%, `services/pog.py` 95%, `services/uldk.py` 99%, `services/mpzp.py` 98%.
Punkt odniesienia przed zmianami: 1787 passed (baseline BK-501–503, 92,25%).

Frontend (Node 22, obraz `--target test` zbudowany jak w CI, `npm ci` w obrazie):

```bash
docker build --build-context shared=./shared --target test -t dzialki-frontend-test ./frontend
docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 dzialki-frontend-test \
  sh -c "npm run typecheck && npm run test:coverage && npm run build"
```

Wynik: typecheck OK, **386 passed (39 plików)**, pokrycie 97,21% instrukcji / 91,02% gałęzi / 98,58% linii (`quality.ts` 100%, `SectionQualityMatrix.tsx` 100%, `api.ts` 99%, `ReportDownloadButton.tsx` 90,6% instrukcji), `next build` bez błędów (Node 22.23.3, npm 10.9.9) (`bk-504-505/frontend-summary.txt`). Nowe moduły
`lib/quality.ts` i `components/SectionQualityMatrix.tsx` są w `coverage.include`; progi
Vitest (80%) nie zostały zmienione.

Odświeżenie artefaktów: `EVIDENCE_OUT=/evidence REPORT_EVIDENCE_DIR=/evidence/bk-504-505/report`
przy uruchomieniu pełnego zestawu (JSON-y i PDF-y scenariusza końcowego, obrazy stron z tabelami
sekcji 8), `python backend/scripts/export_report_field_mapping.py` (dokument mapowania).

## Scenariusz końcowy (rzeczywiste połączenie warstw)

`tests/test_quality_e2e.py` i `tests/test_audit_e2e.py`: `POST /analyze` → orkiestrator (realny
adapter NMT na zamrożonym fixture, sekcje kontekstu ISOK/GDOŚ z provenance) → `save_analysis` w
PostGIS → odczyt historyczny → trafienie cache → `GET /report/{id}` (WeasyPrint) →
`GET /report/{id}/audit.zip` → rozpakowanie i weryfikacja offline. Artefakty:

| Plik | Zawartość |
|---|---|
| `bk-504-505/00-api-response.json`, `01-api-section-quality.json` | odpowiedź API i macierz (10 sekcji, legenda, `matrix_sha256`) |
| `bk-504-505/02-report.pdf`, `03-report-late-export.pdf` | raport z dnia analizy i eksport „za 400 dni” (ten sam snapshot, ta sama tabela 8.1, inna tabela 8.3) |
| `bk-504-505/04-distinct-states.json` | brak pokrycia, timeout, ISOK niedostępny, błąd nieoczekiwany, stary pomiar |
| `bk-504-505/05-resume.json` | wznowienie MPZP wystawia macierz od nowa |
| `bk-504-505/06-audit-package.zip`, `07-manifest.json`, `08-README.md`, `09-sources.json`, `10-package-sha256.txt` | pakiet audytowy, jego manifest, README, rejestr źródeł i hash paczki (poza archiwum) |
| `bk-504-505/report/quality-*.pdf`, `*-pNN.png` | sekcja 8 raportu: eksport w dniu analizy, po 400 dniach oraz rzadka analiza z lukami |

## Odbiór UI (przed BK-701)

Ręczny odbiór na realnych odpowiedziach: Next.js `dev` (Node 25 lokalnie) z atrapą API
serwującą artefakty z `bk-504-505/` (`00-api-response.json` jako odpowiedź `/analyze`,
`06-audit-package.zip` jako `audit.zip`); przeglądarka wbudowana, widok 1440×1000.

- Panel wyniku pokazuje sekcję „Kompletność i świeżość danych”: 10 kart (nagłówek: sekcja,
  status z tekstem i znakiem oraz plakietka „wymaga weryfikacji”; pola: źródło z `source_id`
  albo „brak źródła”, pobrano, wydanie, świeżość z wiekiem i regułą, powód). Transport:
  „poza zakresem” + „brak potwierdzonego kontraktu źródła danych (BK-305)”; relacja MPZP–POG:
  „nieustalone” + kod ścieżki decyzji; POG bez `source_id`: powód „źródło bez identyfikatora z
  katalogu”. Tabela w wąskim panelu (440 px) wymagała przewijania poziomego i ukrywała
  kolumny świeżości i powodu, więc układ zmieniono na karty — po zmianie wszystkie pola są
  widoczne bez przewijania w poziomie.
- Przycisk „Pobierz pakiet audytowy (ZIP)” pobrał paczkę, a pod przyciskiem pojawiła się suma
  `a0180eaa…d006`; równa `shasum -a 256` pliku (klient porównuje ją z sumą pobranych bajtów).
- Uwaga środowiskowa: serwer `next dev` uruchomiony przed edycją CSS zgłosił błąd manifestu RSC
  (problem serwera deweloperskiego przy zmianie plików w trakcie pracy); po restarcie widok
  działał, a `next build` w obrazie Node 22 przechodzi.

Automatyzację tego odbioru w przeglądarce przejmie BK-701.

## Znane ograniczenia i zadania następcze

- **Reguły świeżości** mają tylko cztery źródła usług na żywo (`isok`, `gdos`, `nmt`, `nmt_wcs`):
  7 dni, `project_decision`. To decyzja projektowa, nie deklaracja właściciela
  danych; pozostałe źródła mają świeżość `unknown` do czasu opublikowania częstotliwości
  aktualizacji albo jawnej decyzji z uzasadnieniem (wpis w katalogu podbija
  `quality_policy_version`). **Do potwierdzenia przez właściciela produktu.**
- **`redistribution`** w katalogu to ostrożna interpretacja pola `license` (m.in. `pog_app` =
  `derived_only`, więc surowe atrybuty POG nie trafiają do pakietu); wymaga potwierdzenia
  właścicieli danych/prawnika. Domyślnie `unconfirmed` = zakaz.
- Pakiet zawiera wyniki, warstwy pochodne i referencje (URI, SHA-256, rozmiar artefaktu);
  **nie zawiera surowych plików źródeł** ani zamrożonej specyfikacji map raportu
  (`report_map_snapshot`) — ewentualne dołączenie to osobne zadanie.
- Sekcja MPZP bierze źródło pierwszej strefy (strefy jednej analizy pochodzą z jednego
  przypiętego wydania). Adaptery bez `source_id` z katalogu (np. BIP gminy, `POG_GMINA_BIP`)
  dają `SOURCE_ID_UNRESOLVED` i ich warstwy są pomijane w pakiecie.
- Bajty archiwum ZIP są deterministyczne w tym samym środowisku (ta sama wersja zlib);
  sumy SHA-256 plików w manifeście nie zależą od środowiska.
- Przeglądarkowy E2E pobrania pakietu i podglądu macierzy — BK-701.
