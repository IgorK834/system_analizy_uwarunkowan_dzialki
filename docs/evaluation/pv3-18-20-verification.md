# Odbiór PV3-18, PV3-19 i PV3-20 (Epic 20D)

Data: 5 października 2026 r. Baza kodu: `178f70b` (cała praca PV3-03–17 jest już w historii) z **niezacommitowanymi**
zmianami tych zadań (nic nie zostało zacommitowane; `docs/evaluation` i `docs/adr` są w `.gitignore` — nowe pliki
wymagają `git add -f`). Decyzje: aneks PV3-18–20 w [ADR-012](../adr/ADR-012-mpzp-llm-extraction.md), uzupełnienia w
[ADR-014](../adr/ADR-014-llm-data-handling.md), runbook [`docs/operations/mpzp-llm.md`](../operations/mpzp-llm.md).

## Stan zadań

| Zadanie | Stan | Zastrzeżenie |
|---|---|---|
| PV3-18 — UI i PDF: oznaczenie odczytu automatycznego, cytat, warunki | **wykonane** | ręczny odbiór UI na lokalnym stubie API (nie na danych rzeczywistych); automatyzacja w przeglądarce (axe, rzeczywisty czytnik ekranu) — BK-701 |
| PV3-19 — monitoring i przypięcie wersji modelu | **wykonane (offline)** | brak biegu `--live` na zbiorze złotym i brak ruchu produkcyjnego: para (`gemini-3.8-flash`, `mpzp-extraction/1`) **nie ma zapisu ponownej ewaluacji**, a progi alarmów to propozycja, nie zmierzona norma |
| PV3-20 — dokumentacja decyzji i obsługi | **wykonane** | ADR dane/zagrożenia ma numer **014** (013 zajęty — w zadaniu „ADR-013”); dostawcy nie dopisano do `catalog.yaml` (ADR-014, pkt 5) |

Zależności: PV3-08 (warunki w kontrakcie), PV3-13/14 (provenance, tryb hybrydowy) i PV3-15 (budżet) — wykonane wcześniej;
PV3-17 pozostaje `NOT_DECIDABLE` (patrz `results/parser-v3/README.md`), a ADR-012 nie przedstawia wyników jako
gwarantowanych.

## Polecenia odbiorowe

```bash
cd backend
# kontrola przypięcia modelu i promptu (offline); drugie — bramka wdrożeniowa, dziś kod 1: evaluation_not_recorded
python3 scripts/check_llm_pin.py check
python3 scripts/check_llm_pin.py check --require-evaluation
# próba runbooka (włączenie, wyłączenie, rotacja klucza, limity, zmiana modelu), offline, 14 kroków
python3 scripts/rehearse_llm_runbook.py
# testy PV3-18/19 (pełny zestaw jak w CI: patrz niżej)
python3 -m pytest tests/test_llm_monitoring.py tests/test_llm_pin.py tests/test_llm_drift.py \
  tests/test_llm_runbook_rehearsal.py tests/test_report_model_reading.py tests/test_audit_model_provenance.py \
  tests/test_health_api.py tests/test_audit_export.py tests/test_report_v2.py -q
# frontend (Docker, Node 22, jak w CI)
docker build --build-context shared=./shared --target test -t pv3fe ./frontend
docker run --rm -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pv3fe sh -c 'npm run typecheck && npm run test:coverage && npm run build'
```

Kontrola dryfu (`scripts/check_llm_drift.py --confirm-public-text`) i bieg `--live` ewaluatora wysyłają publiczne teksty aktów
do dostawcy i kosztują — **nie wykonano ich** (wymagają zgody właściciela i klucza); logikę sprawdzono na zniekształconym
odtworzeniu (`tests/test_llm_drift.py`).

## PV3-18 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Wartość z modelu jest zawsze oznaczona i pokazuje cytat, stronę i warunki | spełnione | UI: `MpzpZoneCard.test.tsx` („oznacza wartość z modelu…”, „pokazuje warunki wartości z modelu…”, licznik w nocie), PDF: `test_report_model_reading.py` (evidence: marker, model, wersja instrukcji, pełny skrót odpowiedzi, cytat, strona, warunki; wiersz parametru: oddzielna linia kandydata), pakiet: `test_audit_model_provenance.py`. Marker: „odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia” |
| Wartość deterministyczna nie jest błędnie oznaczona jako z modelu | spełnione | rozpoznanie wyłącznie po statusie `ai_candidate` / metodzie `llm_verified` (`app/shared/model_reading.py`, `lib/mpzpProvenance.ts`); testy: niska pewność, flaga weryfikacji i OCR **nie** oznaczają wartości jako odczytu modelu; w PDF i w UI wiersz deterministyczny obok wiersza modelu nie ma znacznika ani provenance; PDF bez modelu nie zawiera markera ani noty |
| PDF i pakiet audytowy zawierają provenance modelu; testy snapshotowe PDF przechodzą | spełnione | PDF: tabela evidence (model, wersja instrukcji, SHA-256 odpowiedzi) — sprawdzone w tekście wyrenderowanego PDF (PyMuPDF); układ stron (brak obcięć i nakładania, polskie znaki, tylko DejaVu) dla dokumentu z elementami PV3-18 przez wspólny helper `assert_clean_pages`; **istniejące testy PDF/mapy/raportu przechodzą** (`test_report_v2/pdf/e2e/quality/map/terrain/conditions/field_mapping`, 128 testów; w `test_report_v2` wydzielono tylko helper układu stron, a asercje zostały). Pakiet: `audit-exporter/1.2.0`, `model_provenance`, **z odpowiedzi modelu tylko skrót i zweryfikowany cytat** (`model_responses_included: false`), cytat zgodnie z polem `redistribution` (`allowed` → cytat; `derived_only`/`forbidden`/`unconfirmed` → skrót + powód + wpis w `redactions`; testy sprawdzają, że tekst cytatu nie występuje w archiwum), provenance także dla niepełnego wyniku (`MPZP_LLM_UNAVAILABLE`/`CANDIDATES_REJECTED`), determinizm bajtowy i weryfikator offline |
| Testy dostępności (czytnik ekranu, kontrast, klawiatura) dla nowych elementów | spełnione w zakresie testów jednostkowych | `MpzpZoneCard.a11y.test.tsx` (13 testów): role i nazwy (`rowheader` z pełnym markerem i tekstem dla czytnika, `columnheader` ze `scope`, nazwany przewijany region z `tabindex=0`, `role="status"` `aria-live="polite"`, `aria-pressed`, `aria-describedby`), tekst dla czytnika przy braku danych; klawiatura (kolejność Tab, Enter i Spacja przełączają filtr, fokus zostaje, nic nie pułapkuje); kontrast liczony z rzeczywistego `globals.css` (`test/contrast.ts`): oznaczenie i nota **10,9 : 1**, obramowanie **8,0 : 1**, filtr **15,0 : 1**, filtr włączony **9,3 : 1**, „brak danych” **5,4 : 1**, obrys fokusu **7,5 : 1** (AA: 4,5 i 3). Bez nowej zależności (axe w BK-701) |
| ≥ 80% pokrycia nowych modułów (frontend) i zmienionych (backend); nowe moduły w `coverage.include` | spełnione | frontend (pełny przebieg w obrazie Node 22): `lib/mpzpProvenance.ts` w `coverage.include`, `MpzpZoneCard.tsx` 100% linii / 97% gałęzi, całość 97,3%; backend (pełny zestaw 94,24%): `app/shared/model_reading.py` 100%, `reporting/application/audit_export.py` 99%, `services/report.py` 98%, `reporting/domain/sections.py` 100% |
| Ręczny odbiór UI jest udokumentowany | spełnione | sekcja „Ręczny odbiór UI” niżej |

Komunikaty: **brak danych jest różny od braku ograniczenia, a `null` od 0** — UI: „brak danych” z tekstem dla czytnika
(`data-value-state="null"`) kontra „0 m” (`zero`), nota pod tabelą; PDF: „nie określono” kontra „0”, nota pod tabelą 3.2
(istniejący test `test_zero_is_numeric_and_null_is_not_specified` przechodzi); pakiet: README. Kontrakt API bez zmian
(pola provenance istniały od PV3-13/14); frontend zyskał `lib/mpzpProvenance.ts`, a `lib/types.ts` już zawierał pola.

### Ręczny odbiór UI (2026-10-05)

Środowisko: `next dev` (Node 25.6, Chromium w panelu przeglądarki) z `NEXT_PUBLIC_API_BASE_URL` wskazującym lokalny stub, który
na `POST /analyze` zwraca zamrożoną odpowiedź fixture `multizone` z strefą `MN.1` zawierającą 2 wartości deterministyczne
(wysokość 12 m, udział zabudowy **0%**), 1 parametr **bez wartości** (`min_biologically_active_percent`: `null`) i 2 odczyty
modelu (`max_storeys` = 3 z cytatem ze str. 13; `roof_angle_min_deg` = 30° z warunkiem „dach stromy” ze str. 14). Dane to
**fixture, nie wynik prawdziwej analizy**; brak połączenia z backendem i dostawcą.

| Sprawdzenie | Wynik |
|---|---|
| Strefa pokazuje notę „2 wartości to odczyt automatyczny (model językowy)” z zastrzeżeniem, że to nie jest interpretacja prawna | tak |
| Dwie wartości z modelu mają fioletowe oznaczenie z pełnym tekstem „odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia”; wartości deterministyczne — bez oznaczenia | tak (2 znaczniki w 5 wierszach strefy) |
| Wartość z modelu: cytat w cudzysłowie, strona (str. 13 / 14), warunek „rodzaj dachu: dach stromy”, linia „Model: gemini-3.8-flash · instrukcja: mpzp-extraction/1 · SHA-256 odpowiedzi: c0ffeedddddd…” | tak |
| Filtr „Tylko do ręcznej weryfikacji (3)”: z klawiatury (fokus → Enter) `aria-pressed` = true, komunikat „Widoczne parametry: 3 z 5.”, tabela zawęża się do 3 wierszy; fokus pozostaje na przycisku | tak |
| Brak danych pokazany jako „brak danych”, zero jako „0 percent” (jednostka tak, jak w danych) | tak |
| Układ na telefonie (375 × 812): strona nie przewija się poziomo; przycisk filtra 230 × 36 px; tekst znacznika 11,8 px — **podniesiony po pomiarze do 0,78 rem (≈ 12,5 px)**, nieobejrzany ponownie w przeglądarce | tak (zmiana rozmiaru niesprawdzona wizualnie) |

Zauważono i **nie zmieniono** (poza zakresem): jednostka procentowa jest pokazywana jako „percent” (tak jak w danych parsera,
istniejące zachowanie sprzed PV3-18). Szersze zachowanie z prawdziwymi danymi i czytnikiem ekranu — BK-701.

## PV3-19 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Metryki: liczba wywołań, opóźnienie, tokeny, koszt szacowany, odsetek odrzuceń per bramka, trafienia cache, odsetek degradacji — emitowane i testowane | spełnione | punkt integracji „`infrastructure/llm` (metryki)”: adapter dostarcza `latency_ms` i zużycie tokenów w `StructuredExtractionResult`, a liczniki są publikowane w warstwie application (adapter nie importuje rejestru metryk — granica ADR-001, test architektury); `llm_metrics` (histogram opóźnień z sumą, liczbą i maksimum; okna czasowe; odsetki z minimalną próbą, „brak danych” zamiast 0 %), emisja z potoku i trybu hybrydowego; `test_llm_monitoring.py` (wartości liczników na przebiegu przez potok i przez prawdziwy adapter, cache: 1 trafienie / 1 chybienie, degradacja, brak pomiaru opóźnienia dostawcy skryptowego) |
| Logi nie zawierają treści żądań ani klucza | spełnione | `test_llm_monitoring.py`: logi całego przebiegu (poziom DEBUG, wszystkie loggery, także `httpx`) bez fragmentów dokumentu, znaczników danych i klucza; odpowiedź dostawcy echująca klucz — brak klucza i komunikatu dostawcy w logach; `log_event` odrzuca długie i wielowierszowe napisy. Próba kontenerowa: 0 wystąpień klucza w logach (runbook §9) |
| `/health`: komponent LLM „degraded”, nigdy „failed”, bez blokowania gotowości | spełnione | `components.llm.status` ∈ {`ok`, `degraded`, `disabled`} (test słownika stanów i schematu OpenAPI); awaria modelu (prawdziwy adapter, 503) → `degraded` (`model_unavailable`), `/health.status` = `ok`, `/health/ready` = 200; powrót po udanym przebiegu; awaria samej oceny → `degraded` (`health_check_error`), nigdy wyjątek; ocena nie wywołuje dostawcy ani bazy w trybie bez modelu. W kontenerze z PostGIS: ten sam wynik (runbook §9, R1–R6) |
| Zmiana `model_id` lub `prompt_version` bez zapisu ponownej ewaluacji jest wykrywana | spełnione | `tests/test_llm_pin.py` (zmiana modelu, wersji i skrótu promptu, wersji i skrótu schematu, `thinking_level`; model/wersja promptu w Compose i `.env.example` bez zmiany przypięcia; brak wiersza w dzienniku ADR; zapis ewaluacji tylko z biegu `hybrid` + `live` dla DOKŁADNIE przypiętej pary, integralność manifestu), `check_llm_pin.py check --require-evaluation` (dziś kod 1: ewaluacja oczekuje), w czasie działania `pin_mismatch` wstrzymuje ścieżkę (adapter nie powstaje, brak żądania) |
| Runbook opisuje progi alarmów, wyłączenie ścieżki i rotację klucza | spełnione | `docs/operations/mpzp-llm.md` §3 (wyłączanie), §5 (progi i reakcje), §6 (dryf), §7 (rotacja klucza), §8 (zmiana modelu) |
| ≥ 80% pokrycia zmienionych modułów | spełnione | `llm_metrics` 100%, `llm_monitoring` 99%, `llm_drift` 98%, `infrastructure/llm/pin.py` 100%, `routers/health.py` 100%, `composition.py` 94%, `core/settings.py` 100%, `llm_pipeline` 99% (pełny zestaw 94,24%) |

Kontrola dryfu (cykliczna) — `check_llm_drift.py`: porównanie przyjętych wartości z dostawcy na żywo z zamrożonym odtworzeniem
na 13 blokach kanarkowych, próg 0,20, kody wyjścia 0/3/4/2, stan czytany przez `/health` (`drift_alarm`); testy: brak dryfu,
utrata parametru, brak wartości, wartości odrzucone przez bramki, awaria dostawcy = nierozstrzygnięte (nie alarm), limit
żądań, odmowa bez zgody/w CI/przy kill switchu. **Bieg z prawdziwym dostawcą nie został wykonany.**

## PV3-20 — mapowanie kryteriów akceptacji

| Kryterium | Wynik | Dowód / uwaga |
|---|---|---|
| ADR-012 i ADR-013 kompletne i odsyłają do artefaktów pomiarowych z hashami | spełnione, z uwagą o numerze | ADR-012: aneks PV3-18–20 (architektura, model i przypięcie, koszty, kryteria bramki i wynik — `NOT_DECIDABLE`), tabela „Artefakty pomiarowe i ich skróty (SHA-256)” z poleceniem odtwarzającym (**wszystkie 14 skrótów odtworzone poleceniem `shasum`, 0 niezgodnych**) i dziennik zmian przypięcia. Dokument danych/zagrożeń/kill switch to **ADR-014** (numer 013 zajęty przez ADR silnika ilości): lista pól, model zagrożeń T1–T11, kill switch, monitoring bez treści, tabela artefaktów ze skrótami |
| Runbook przećwiczony w całości (włączenie, wyłączenie, rotacja klucza), opisany wynik próby | spełnione w opisanym zakresie | runbook §9: (1) kontener — projekt Compose z PostGIS, restart `docker compose up -d`, `touch`/`rm` kill switcha, zmiana flagi i trybu, rotacja losowego klucza, `/health*` przez HTTP; (2) proces z prawdziwymi ustawieniami, kompozycją i adapterem, dostawca = zamrożone odpowiedzi `respx` przyjmujące tylko „nowy” klucz — 14/14 kroków (`scripts/rehearse_llm_runbook.py`, test `test_llm_runbook_rehearsal.py`). **Nie obejmuje**: unieważnienia klucza w konsoli dostawcy, prawdziwego ruchu, kontroli dryfu z dostawcą |
| Każda istotna zmiana kontraktu, konfiguracji lub migracji opisana; polecenia z dokumentów odtwarzają wyniki | spełnione | zmiany niżej; polecenia odbiorowe wyżej wykonano (wyniki w „Weryfikacji”); nowa migracja **nie była potrzebna** (head `030_mpzp_llm_usage`; zadanie wspominało head `026`, który jest nieaktualny — 027–030 powstały w PV3-04/08/13/15) |
| Stan projektu nie przedstawia wyników jako gwarantowanych i wskazuje ograniczenia zbioru ewaluacyjnego | spełnione | `docs/current_state.md` (sekcja PV3-18–20 i skorygowany zakres migracji), README (stan i ograniczenia), `backend/tests/fixtures/mpzp_evaluation/README.md` (polecenia ewaluacji, ograniczenia zbioru), `docs/evaluation/bk-601-603-verification.md` (odesłanie do wyników parsera v3) |
| Utrzymane ≥ 80% pokrycia aplikacji | spełnione | pełny zestaw w kontenerze jak w CI: 94,24% |

### Zmiany kontraktu, konfiguracji i migracji

- **Kontrakt API:** bez zmian pól. Nowe: `GET /health` → `components.llm` (`ok`/`degraded`/`disabled`, powody, tryb);
  `GET /health/llm` (klucz administracyjny) — szczegóły. `GET /health/ready` i `/health/live` bez zmian.
- **Pakiet audytowy:** `audit-exporter/1.1.0` → **1.2.0** — `analysis.json` ma blok `model_provenance`; cytat z modelu podlega
  `redistribution`; README opisuje odczyt automatyczny. Pakiety 1.1.0 nie mają tego bloku.
- **Raport PDF:** nowy rodzaj ustaleń `model_reading` („odczyt automatyczny”) w legendzie, linia kandydata modelu w tabeli 3.2,
  marker i provenance w tabeli 3.3, notatki; Załącznik A (mapowanie pól): 4 nowe wiersze dla pól provenance modelu
  (`review_status`, `model_id`, `prompt_version`, `response_sha256`) i rodzaj `model_reading`, `docs/report/field-mapping.md`
  odtworzony (`test_report_field_mapping` przechodzi); kontrakt `MPZP_RESULT_SCHEMA_VERSION` bez zmiany.
- **Konfiguracja:** `MPZP_LLM_PROMPT_VERSION`, `MPZP_LLM_ENFORCE_PIN`, `MPZP_LLM_ALARM_*` (okno, minimalne próby, progi
  odrzuceń, degradacji i kosztu dobowego), `MPZP_LLM_DRIFT_*` (próg, plik stanu, wiek), `MPZP_LLM_HEALTH_LEDGER_TTL_SECONDS`
  (`core/settings.py`, `.env.example`, `docker-compose.yml`). Ewaluator: `--model`; manifest biegu zapisuje model, wersję i
  skrót promptu i schematu.
- **Cache i migracje:** cache `mpzp_llm_extractions` (migracja **029**) i rejestr `mpzp_llm_usage` (**030**) bez zmian; PV3-18–20
  **nie dodały migracji**. Klucz cache nie zmienił się.
- **Nowe pliki kodu:** `application/llm_monitoring.py`, `application/llm_drift.py`, `infrastructure/llm/pin.py` +
  `model_pin.json`, `shared/model_reading.py`, skrypty `check_llm_pin.py`, `check_llm_drift.py`, `rehearse_llm_runbook.py`;
  frontend: `lib/mpzpProvenance.ts`, `test/contrast.ts`.

## Weryfikacja

- Backend, pełny zestaw jak w CI (obraz backendu Compose, repo `:ro` bez `.env`, `pytest -m "not docker_cli" --cov=app
  --cov-fail-under=80`, PostGIS): **3224 testy zaliczone, 0 niezaliczonych** (3 odznaczone `docker_cli`), pokrycie **94,25%**
  (czas 6 min 26 s). Pierwszy pełny przebieg (3215 zaliczonych) miał 1 niezaliczony test — błędna asercja w nowym teście
  (brzmienie „interpretacja prawna”) — poprawiona; następnie dopisano testy zdrowia dla dostawcy odtwarzającego, spójności
  skrótu cytatu, manifestu biegu ewaluatora i mapowania pól, a przebieg powtórzono w całości.
- Frontend (obraz Node 22, `npm ci`, `npm run typecheck`, `npm run test:coverage`, `npm run build`): **42 pliki, 481 testów
  zaliczonych**, pokrycie 97,31% (progi 80% zachowane), `MpzpZoneCard.tsx` 100% linii; typy i build bez błędów.
- Linter (`ruff`, reguły F/E9) na nowych i zmienionych plikach Pythona: bez uwag.
- Skaner sekretów, `test_no_env_files_in_repository` i testy `.env.example`/Compose: zaliczone w pełnym zestawie.
- Polecenia z dokumentów odtworzono: `check_llm_pin.py check` (kod 0) i `--require-evaluation` (kod 1:
  `evaluation_not_recorded`), `rehearse_llm_runbook.py` (14/14, także poza kontenerem), `check_llm_drift.py` bez zgody (kod 2),
  tabela skrótów SHA-256 (14/14 zgodnych), generator mapowania pól (dokument zgodny z kodem).
- Stan repozytorium: **nic nie zacommitowano**; nowe pliki w `docs/evaluation` wymagają `git add -f`. Plik
  `backend/_imp.py` (nieśledzony, sprzed tych zadań) nie jest częścią tej pracy.

## Ograniczenia (jawnie)

1. **Brak biegu `--live`** na zbiorze złotym i brak kontroli dryfu z prawdziwym dostawcą — wymagają zgody właściciela (wysyłka
   publicznych tekstów, koszt) i klucza w środowisku; asystent nie ma klucza do użycia w tej sesji. Skutek: para (model, prompt)
   jest **przypięta, ale nieoceniona**, a `check_llm_pin.py check --require-evaluation` kończy się kodem 1.
2. **Progi alarmów** (odrzucenia 30%, degradacja 20%, koszt 4 USD/dobę, dryf 0,20) to propozycja bez kalibracji na ruchu.
3. **Alarmy to wskaźniki w `/health`**, nie system powiadomień (projekt nie ma systemu metryk ani alertowania); liczniki są
   lokalne dla procesu (restart zeruje, N procesów = N widoków), twarde limity kosztu żyją w bazie.
4. **Zbiór ewaluacyjny BK-603**: 21 próbek z 9 gmin, anotacje asystenta AI (bez drugiego anotatora i przeglądu człowieka),
   silnik v3 rozwijany na tych samych próbkach (wyniki rozwojowe), złote odpowiedzi to nie nagrania modelu, zbiór końcowy z
   Task 20.2 nie istnieje; bramka z Task 20.17 → `NOT_DECIDABLE`.
5. **Ręczny odbiór UI** wykonano na stubie API z fixture, bez czytnika ekranu i bez przeglądarkowej automatyzacji (BK-701).
6. Decyzja o włączeniu produkcyjnym **nie została podjęta**; domyślny stan to `legacy` i `mpzp_llm_enabled=false`.
