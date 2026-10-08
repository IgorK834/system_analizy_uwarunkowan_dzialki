# Odbiór AU-008 (Task 21.8): lista podpowiedzi adresowych jako popover

- Data: 2026-10-08. Stan: **zaimplementowane i zacommitowane w `main` (AU-009, 2026-10-08)**.
- Audyt: pozycja B5 (dokumenty `docs/audit/…` wskazane w zadaniu nie istnieją w repozytorium; źródłem była treść zadania).
- Artefakty: [`results/au-008/`](results/au-008/) — pomiary `document.elementFromPoint` przed i po zmianie oraz zrzuty ekranu.

## Przyczyna

W zakładce „Adres” lista `ul.suggestions` była elementem przepływu wewnątrz `.search-panel`. Sam panel ma `position: absolute`,
`z-index: 5`, a `.map-controls` (panel POG i podglądy WMS) `z-index: 6` i jest zakotwiczony przy dole — lista rosła w dół i trafiała
pod panel POG. Pomiar w przeglądarce na kodzie sprzed zmiany odtwarza audyt co do piksela: `position: static`, `z-index: auto`,
lista y 339–599 przy 1440×900, `.map-controls` od y 358; środki wszystkich 5 pozycji trafiają w panel POG (0/5 w liście; przy 375×812 —
2/5).

## Zmiana

- `SearchPanel.tsx`: pole adresu i lista są w `div.address-field` (`position: relative`); lista jest popoverem
  (`position: absolute; top: calc(100% + 4px); left/right: 0`), więc **nie zajmuje miejsca w przepływie** i panel POG się nie przesuwa.
- Panel wyszukiwania dostaje klasę `search-panel-suggesting` (`z-index: 8`), **tylko gdy lista jest otwarta** — czyli nad
  `.map-controls` (6) i `.result-stack` (7); w spoczynku zostaje `z-index: 5` jak dotąd (wynik analizy nadal leży nad panelem
  wyszukiwania). `z-index` samej listy nie wystarcza, bo leży w kontekście stosu panelu.
- Przewijanie przy małej wysokości: `max-height: clamp(8rem, calc(100dvh - 20rem), 24rem)`, `overflow-y: auto`,
  `overscroll-behavior: contain`. Pięć pozycji mieści się bez przewijania przy 1440×900 i 375×812 (≈ 281 px).
- Klawiatura i ARIA bez zmian: `role="listbox"`/`option`, `aria-controls`, `aria-activedescendant`, strzałki i Enter. Dodano
  zamykanie listy klawiszem Escape i kliknięciem poza panelem, żeby otwarty popover nie zasłaniał kontrolek mapy, gdy użytkownik
  przestał z niego korzystać.

## Kryteria akceptacji

| Kryterium | Wynik | Dowód |
|---|---|---|
| Dla 5 podpowiedzi środek każdej pozycji jest elementem listy (`document.elementFromPoint`), nie panelem — 1440×900 | **spełnione** (5/5; przed: 0/5) | [01-elementFromPoint-measurements.json](results/au-008/01-elementFromPoint-measurements.json), [03-after-1440x900.jpg](results/au-008/03-after-1440x900.jpg) |
| to samo przy 375×812 | **spełnione** (5/5; przed: 2/5) | tamże, [02-after-375x812.jpg](results/au-008/02-after-375x812.jpg) |
| Panel POG nie przesuwa się przy otwarciu listy | **spełnione** | `pogPanelMovedByPopover: false` (granice `.map-controls` 358–880 przed i po otwarciu listy przy 1440×900; 437–802 przy 375×812) |
| Przy małej wysokości lista się przewija | **spełnione** | 1440×520: `max-height` 200 px, `scrollHeight` 281 > `clientHeight` 198, dolna krawędź 489 < 520, ostatnia pozycja osiągalna przewijaniem i trafiana przez `elementFromPoint` |
| Klikalność | **spełnione** | prawdziwe kliknięcie 5. pozycji (wcześniej zasłoniętej) wysyła `POST /analyze` z `selected_result_id: hash:stub5` i zamyka listę |
| Obsługa klawiatury i `aria-activedescendant` zachowana | **spełnione** | `SearchPanel.test.tsx`: strzałki ustawiają `aria-activedescendant` na `address-suggestion-N`, Enter wybiera, `aria-controls` wskazuje listę |
| Dokumentacja: `docs/current_state.md` (+ ADR, jeśli zmienia się decyzja architektoniczna) | **spełnione** | sekcja AU-008 w `current_state.md`; zmiana jest lokalna (CSS i jeden komponent), więc bez nowego ADR |
| ≥ 80% pokrycia, moduł w `coverage.include` | **spełnione** | `components/SearchPanel.tsx` już w `coverage.include`; frontend ogółem 97,7% (wiersze), próg 80% |

## Testy

- `components/SearchPanel.test.tsx` (6 nowych): lista wewnątrz `.address-field`, klasa `search-panel-suggesting` tylko przy otwartej
  liście, strzałki i `aria-activedescendant`, Escape (także bez listy), zamykanie klikiem poza panelem a nie w panelu.
- `app/searchPanelLayout.test.ts` (4 nowe): kontrakt `globals.css` — popover `absolute` z `top: calc(100% …)`,
  `overflow-y: auto` i `max-height` w `dvh`, `z-index` panelu z listą większy niż `.map-controls` i `.result-stack`, `.map-controls`
  zakotwiczone przy dole. Vitest nie liczy układu (`css: false`), więc położenie pozycji weryfikuje przebieg w przeglądarce.
- Przebieg w przeglądarce (`resize_window` 1440×900, 375×812, 1440×520; produkcyjny build frontendu z `next build`; stub API
  zwracający 5 podpowiedzi) — pomiar funkcją opisaną w [01-elementFromPoint-measurements.json](results/au-008/01-elementFromPoint-measurements.json):
  dla każdej pozycji środek `getBoundingClientRect()` → `document.elementFromPoint()` → czy trafiony element należy do tej pozycji.
  Test przeglądarkowy w pipeline CI to Task 24.10 (poza zakresem).

## Ograniczenia

- Pomiar użył stubu API (5 stałych podpowiedzi), nie żywego indeksu adresowego; układ nie zależy od źródła danych.
- Gdy lista jest otwarta, panel wyszukiwania leży nad panelem wyniku (z-index 8 > 7); po wyborze pozycji, Escape lub kliknięciu
  poza panelem wraca do `z-index: 5`.
