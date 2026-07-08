# Dataset referencyjny działek testowych

## Cel katalogu

Ten katalog zawiera **stały dataset referencyjny działek testowych**, używany
przez testy jednostkowe, integracyjne i E2E w miarę powstawania kolejnych
serwisów domenowych (parser MPZP, POG/OUZ, ryzyka środowiskowe). Zamiast
dobierać dane przypadkowo przy każdej nowej funkcji, kolejne zadania
weryfikują swoją logikę na tych samych, znanych przypadkach z `cases.json`.

Zgodnie z sekcją 15 i 16 `context.md` (patrz "Odniesienie do context.md"
poniżej), dataset ma pokrywać minimum 10 działek z różnych (fikcyjnych) gmin
i obejmować wszystkie kluczowe scenariusze E2E wymagane dla tego projektu.

**Ważne:** to zadanie tworzy dane testowe (fixtures) i minimalny walidator ich
struktury. Nie implementuje logiki parsera MPZP/POG/ryzyk — te serwisy jeszcze
nie istnieją w `backend/app/services/` (obecnie tylko `geometry.py`,
`geocoding.py`, `geojson.py`, `uldk.py`, `initiation.py`). `cases.json` opisuje
**oczekiwane wyniki domenowe jako specyfikację/kontrakt na przyszłość**, a nie
jako dane do bieżącej asercji względem nieistniejącego kodu.

## Struktura `cases.json`

Plik `cases.json` to lista (JSON array) obiektów, każdy reprezentuje jeden
przypadek testowy działki. Wymagane pola każdego przypadku:

| Pole | Typ | Opis |
|---|---|---|
| `case_id` | string | Unikalny slug w `snake_case`, np. `mpzp_vector_single_zone`. |
| `name` | string | Czytelna nazwa po polsku. |
| `gmina` | string | Nazwa gminy — fikcyjna/syntetyczna, żeby uniknąć problemów z danymi wrażliwymi. |
| `parcel_identifier` | string | Identyfikator działki w formacie ULDK, np. `146101_1.0001.123/4`. |
| `scenario_type` | string (enum) | Jeden z: `mpzp_vector`, `mpzp_raster`, `mpzp_multi_zone`, `ouz_inside`, `ouz_outside`, `ouz_touches_boundary`, `flood_risk`, `nature_protection`, `pog_missing`, `mpzp_pog_conflict`, `mpzp_problematic_pdf`. |
| `description` | string | 2-4 zdania po polsku: co przypadek testuje i dlaczego jest ważny domenowo. |
| `input` | object | `{"method": "parcel_id", "parcel_identifier": "..."}` — wszystkie przypadki identyfikują działkę wprost, żeby test był deterministyczny i niezależny od geokodowania. |
| `expected_result` | object | Częściowy, uproszczony podzbiór pól z `app/schemas/analyze.py` (`MpzpZoneResult`, `PogResult`, `RiskResult`) istotny dla danego scenariusza — patrz niżej. |
| `expected_warnings` | list[string] | Oczekiwane kody ostrzeżeń zgodne z `WarningMessage.code` (np. `MPZP_MULTI_ZONE_WARNING`). Pusta lista jest dopuszczalna. |
| `fixture_files` | object | Ścieżki względne do `responses/` z nagranymi odpowiedziami usług zewnętrznych. `null`, gdy serwis jeszcze nie istnieje (patrz "Ograniczenia" poniżej). |
| `notes` | string \| null | Dodatkowe uwagi, ograniczenia znanych danych, TODO dla przyszłych zadań. |

### Pola `expected_result`

- `mpzp_zones_count` (int) — oczekiwana liczba stref MPZP (0 dla braku MPZP).
- `dominant_zone_symbol` (string \| null) — symbol strefy dominującej.
- `pog_status` (string \| null) — jeden z `adopted` / `not_available` /
  `in_progress` / `unknown`, albo `null` gdy nie dotyczy scenariusza.
- `touches_ouz_boundary` (bool) — czy działka jedynie styka się z granicą OUZ.
- `ouz_intersection_expected` (bool) — czy oczekiwane jest realne przecięcie
  powierzchniowe z OUZ.
- `risk_types_expected` (list[string]) — oczekiwane typy ryzyk (np.
  `flood_zone`, `natura_2000`), pusta lista gdy brak.
- `requires_manual_review` (bool) — czy oczekiwany wynik ma
  `manual_review_required=True` w którejkolwiek sekcji.

### Pola `fixture_files`

- `uldk_response` (string \| null) — ścieżka do nagranej odpowiedzi ULDK.
- `mpzp_response` (string \| null) — ścieżka do nagranej odpowiedzi MPZP.
- `pog_response` (string \| null) — ścieżka do nagranej odpowiedzi POG.
- `additional` (list[string]) — inne pliki fixture, opcjonalne.

## Lista scenariuszy

Dataset pokrywa minimum następujące typy scenariuszy (`scenario_type`):

1. **`mpzp_vector`** — MPZP wektorowy, jedna strefa, wysoka pewność danych.
2. **`mpzp_raster`** — MPZP dostępny tylko jako WMS/raster, symbol strefy podany ręcznie przez użytkownika.
3. **`mpzp_multi_zone`** — działka przecina dwie lub więcej stref MPZP z jasną strefą dominującą.
4. **`ouz_inside`** — działka w całości wewnątrz Obszaru Uzupełnienia Zabudowy.
5. **`ouz_outside`** — działka całkowicie poza OUZ, brak przecięcia.
6. **`ouz_touches_boundary`** — działka styka się jedynie z granicą OUZ, bez realnego przecięcia powierzchniowego.
7. **`flood_risk`** — działka częściowo w obszarze zagrożenia powodziowego (ISOK).
8. **`nature_protection`** — działka w pobliżu lub wewnątrz obszaru Natura 2000 (GDOŚ).
9. **`pog_missing`** — gmina nie ma jeszcze uchwalonego Planu Ogólnego Gminy.
10. **`mpzp_pog_conflict`** — potencjalna niezgodność funkcji MPZP z ustaleniami POG.
11. **`mpzp_problematic_pdf`** — uchwała MPZP jako skan PDF bez warstwy tekstowej, wymaga OCR.

## Źródła danych

**Wszystkie dane w tym datasecie są SYNTETYCZNE.** Nazwy gmin, identyfikatory
działek, symbole stref i współrzędne geometrii są fikcyjne i zostały
wymyślone specjalnie do celów testowych. Żadny przypadek nie jest oparty na
rzeczywistej nieruchomości, rzeczywistej osobie ani rzeczywistej gminie.
Identyfikatory działek mają poprawny **format** ULDK (`TERYT_obreb.numer/podzial`),
ale nie odpowiadają istniejącym działkom ewidencyjnym. Nie umieszczaj tu
żadnych rzeczywistych danych osobowych ani wrażliwych informacji o
konkretnych nieruchomościach.

## Ograniczenia

- Pole `fixture_files` dla większości przypadków ma wartości `null`, ponieważ
  odpowiadające serwisy (`app/services/mpzp.py`, `mpzp_fetch.py`,
  `mpzp_parser.py`, `pog.py`, `isok.py`, `gdos.py`) **nie zostały jeszcze
  zaimplementowane**. To jest jawne **TODO dla przyszłych zadań** — gdy dany
  serwis powstanie, należy nagrać odpowiadający plik odpowiedzi w
  `responses/<case_id>/` i zaktualizować `fixture_files` w `cases.json`.
- Jedynym w pełni nagranym przykładem w tym zadaniu jest odpowiedź ULDK dla
  przypadku `mpzp_vector_single_zone` (`responses/mpzp_vector_single_zone/uldk.txt`),
  demonstrująca wzorzec formatu do rozbudowy: `status\n{id}|{wkt}|{teryt}\n`.
- Przypadek `mpzp_scanned_pdf_no_text_layer` docelowo powinien mieć też osobny
  fixture w `backend/tests/fixtures/mpzp/<gmina>/` (struktura opisana w
  sekcji 16 `context.md`), gdy powstanie `mpzp_parser.py` i będzie można
  dołączyć syntetyczny skan PDF.

## Zasada dodawania nowych przypadków

Każdy nowy przypadek dodany do `cases.json` musi przejść walidację
`backend/tests/test_parcel_fixtures.py` (uruchamianą przez
`pytest tests/test_parcel_fixtures.py`). W szczególności musi mieć wszystkie
wymagane pola, unikalny `case_id`, poprawny `scenario_type` oraz
`parcel_identifier` zgodny z formatem ULDK.

## Odniesienie do context.md

Struktura i wymagania tego datasetu wynikają z:

- **Sekcja 15 (Testy)** — wymóg testów E2E na minimum 10 działkach z różnych
  gmin, obejmujących wszystkie kluczowe scenariusze planistyczne i środowiskowe.
- **Sekcja 16 (Parser MPZP — testy regresyjne)** — wymóg osobnego katalogu
  fixtures z opisem gminy, typu dokumentu, warstwy tekstowej/OCR i oczekiwanych
  parametrów.
- **Sekcja 22 (Rzeczy, których nie robić)** — dataset uwzględnia, że nie każda
  działka ma jeden MPZP i jedną strefę, nie każdy PDF ma warstwę tekstową, a
  brak danych (np. brak POG) nie oznacza braku ograniczeń planistycznych.
