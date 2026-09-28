# ADR-002: Status prawny aktu POG oddzielony od pokrycia i dostępności danych

- Status: Zaakceptowany
- Data: 2026-09-24
- Zadanie: BK-106 (Task 2.6), zależność BK-105
- Kontekst szerszy: `context.md` §14–15, `backlog.md` BK-106, ADR-001

## Kontekst

Do BK-105 jeden enum `adopted | in_progress | not_available | unknown` opisywał
naraz trzy różne fakty: status prawny aktu, obecność danych przestrzennych i
dostępność usługi. Skutki:

- pusta odpowiedź WMS/WFS dawała `not_available`, co w UI i PDF czytało się jak
  „brak planu”;
- obiekt WMS bez atrybutu statusu był domyślnie `adopted`, a tekst strony BIP
  ze słowem „uchwalony” ustawiał status — oba bez urzędowego kodu;
- SQL repozytorium filtrował `legal_status = 'adopted'`, więc projekt znikał z
  wyniku zamiast być pokazany jako niewiążący;
- analizator zapisywał `coverage_status = "complete"`, wartość spoza kontraktu.

Dokumenty planistyczne proponowały przy tym dwie nazwy tego samego stanu:
`superseded` (`context.md`) i `outdated` (`backlog.md`).

## Decyzja

Wprowadzamy trzy niezależne wymiary z jednym, wspólnym mapperem w
`backend/app/shared/planning_status.py` (biblioteka standardowa, dostępny dla
warstw `domain` zgodnie z ADR-001):

| Wymiar | Wartości kanoniczne | Znaczenie |
|---|---|---|
| `legal_status` | `binding`, `project`, `in_progress`, `superseded`, `unknown` | status prawny aktu z urzędowego kodu |
| `coverage_status` | `available`, `partial`, `act_without_spatial_data`, `no_act_confirmed`, `unknown` | dane przestrzenne aktu dla działki |
| `data_availability` | `current`, `stale`, `unavailable` | stan operacyjny źródła w chwili analizy |

**Nazwa kanoniczna to `superseded`.** `outdated` jest wyłącznie aliasem
wejściowym. Te same wartości używają: domena importu, kolumny PostGIS
(`planning_act_versions.legal_status` dla aktów POG, `pog_data`), zapytania
repozytorium, `PogResult` (API, cache, snapshot), typy TypeScript
(`frontend/lib/types.ts`) i prezentacja (`frontend/lib/pogStatus.ts`, raport PDF).

### Reguły

1. **Status prawny tylko z urzędowego kodu.** Rozpoznawane są kody INSPIRE
   `ProcessStepGeneralValue` (`legalForce` → `binding`, `adoption` → `project`,
   `elaboration` → `in_progress`, `obsolete` → `superseded`), etykiety słownika
   RU oraz sufiksy warstw WMS RU (`…PrawnieWiazacyLubRealizowany`,
   `…WTrakciePrzyjmowania`, `…WOpracowaniu`). Brak kodu, słowo „uchwalony”,
   data uchwały, obecność geometrii lub tekst BIP dają `unknown`.
   `PogActRecord.validate()` odrzuca `binding` bez urzędowego kodu, a
   `PogResult` odrzuca `binding` bez `legal_status_evidence.official`.
2. **Pusta odpowiedź nie jest dowodem braku aktu.** Pusty WFS/WMS — także z
   pełną paginacją — daje `coverage_status = unknown` (albo `partial`, gdy
   część danych istnieje). `no_act_confirmed` wymaga `coverage_evidence`
   wskazującego urzędowe potwierdzenie (`official = true` i `reference`).
3. **Awaria źródła.** Bez wcześniejszej wartości: `unknown` + `unknown` +
   `unavailable`. Z wcześniej potwierdzonym statusem (dowolne wydanie PostGIS,
   urzędowy kod): status zachowany, `data_availability = stale`, data
   potwierdzenia w `status_confirmed_at` i reguła wyświetlania daty w UI/PDF.
   Dostępność nigdy nie zmienia statusu prawnego.
4. **Język prezentacji.** Dla `project`, `in_progress` i `unknown` żaden tekst
   UI/PDF nie używa słowa „obowiązuje”. Dla braku geometrii zawsze pada zdanie
   „Brak geometrii lub pusta odpowiedź usługi nie oznacza braku planu.”
5. **Zapytania repozytorium** zwracają obiekty aktów w każdym statusie; o
   wiążącym charakterze decyduje mapper. `find_pog_intersections` domyślnie
   filtruje `legal_status = ANY(('binding',))`.

### Tabela decyzyjna (skrót)

| Obserwacja źródła | legal | coverage | availability |
|---|---|---|---|
| kod `legalForce`, strefy pokrywają działkę, pełna paginacja | binding | available | current |
| kod `adoption`, strefy pokrywają działkę | project | available | current |
| kod `legalForce`, akt bez geometrii | binding | act_without_spatial_data | current |
| kod `legalForce`, brak obiektów i brak dowodu zakresu | binding | unknown | current |
| kod `obsolete` | superseded | wg danych | current |
| geometria bez kodu statusu | unknown | wg danych | current |
| pusty WFS (także pełna paginacja) | unknown | unknown | current |
| pusty WFS + urzędowe potwierdzenie braku aktu | unknown | no_act_confirmed | current |
| timeout bez wcześniejszej wartości | unknown | unknown | unavailable |
| timeout, wcześniej potwierdzony `binding` | binding | wg poprzedniej | stale |

Pełna tabela jest testem akceptacyjnym `backend/tests/test_planning_status.py`.

### Migracja i wsteczna zgodność

Migracja `016_pog_status_coverage` (po `015_pog_v2_release`):

- `planning_act_versions` (tylko `kind = 'pog'`): `adopted` → `binding`
  wyłącznie przy zachowanym urzędowym kodzie w `raw_legal_status`, inaczej
  `unknown` (+ `manual_review_required`, `review_status = 'unreviewed'`);
  `not_available` → `unknown`; `outdated` → `superseded`. Oryginał trafia do
  `legacy_legal_status`. Akty MPZP zachowują swoje wartości (`adopted`,
  `raster_only`) — ich rozdzielenie jest poza zakresem BK-106.
- `pog_data`: nowe kolumny `legal_status`, `coverage_status`,
  `data_availability`, `status_confirmed_at` z ograniczeniami `CHECK`,
  `legacy_status` z oryginałem; `status` staje się lustrem `legal_status`.
  `result_v2` jest przepisywany tą samą regułą: potwierdzeniem `adopted` jest
  urzędowy kod w `raw_attributes.app_metadata.raw_legal_status` albo przypięte
  wydanie (`data_release_id` + `artifact_sha256` + wersja aktu). `complete` →
  `available`; snapshot v1 z zapisaną strefą ma `partial`.
- Downgrade przywraca dokładne wartości z kolumn `legacy_*`; JSON `result_v2`
  wraca do kontraktu 015 (`adopted`/`not_available`, `complete`/`unknown`).

Odczyt starych snapshotów (API, cache, raport) przechodzi przez walidator
`PogResult.upgrade_legacy_statuses` z tą samą regułą. Wersja kontraktu wynosi
`2.1`; sygnatura cache (`pog-v2.1`) unieważnia trafienia zapisane przed zmianą
semantyki.

## Konsekwencje

- Projekt POG jest widoczny w wyniku (strefy, parametry), ale zawsze jako
  niewiążący i z `manual_review_required = true`; nie przechodzi oceny
  zgodności z MPZP.
- Wynik `complete` analizy wymaga `binding` + `available` + `current`.
- Import CLI przyjmuje w `--legal-status` wyłącznie urzędowy kod statusu;
  `binding`/`adopted` wpisane ręcznie są odrzucane albo dają `unknown`.
- Frontend i backend utrzymują lustrzane etykiety; zgodność zbioru wartości
  sprawdzają testy obu stron.
