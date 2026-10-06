# ADR-010: Raport PDF v2 — dziesięć sekcji, pełne tabele stref i deterministyczne mapy ze snapshotu

- Status: Zaakceptowany
- Data: 2026-09-29
- Zakres: BK-501 (Task 6.1), BK-502 (Task 6.2), BK-503 (Task 6.3)
- Powiązane: ADR-001 (modularny monolit), ADR-002 (status prawny ≠ pokrycie),
  ADR-004/005 (evidence MPZP, strefa ręczna, relacja MPZP–POG), ADR-006 (teren),
  ADR-007 (wspólny styl POG), ADR-008 (wydanie = pełny snapshot)

## Kontekst

Raport PDF (`GET /report/{analysis_id}`) był budowany z odtworzonego snapshotu
analizy, ale:

- szablon HTML był długim literałem w `app/services/report.py`, a sekcje nie
  odpowiadały architekturze informacji z backlogu (brak podsumowania, macierzy
  jakości, jawnego podziału na fakt / obliczenie / przybliżenie);
- POG miał tabelę stref, lecz parametry, profile, status aktu i OUZ/OZS/OSDIS
  nie były osobnymi tabelami; MPZP pokazywał strefy jako karty z parametrami
  wyłącznie wtedy, gdy wartość była niepusta (brak wartości znikał zamiast
  „nie określono”), a wartości sprzeczne nie miały listy kandydatów;
- miniatura mapy opcjonalnie pobierała **bieżący** WMS (OSM, KIMPZP, KIUT)
  i rysowała się bieżącym stylem — historyczny PDF mógł zmienić znaczenie po
  zmianie usługi, aktywnego wydania albo konfiguracji (luka odtwarzalności
  opisana w `docs/current_state.md`).

## Decyzje

### 1. Dziesięć sekcji i rodzaje ustaleń (BK-501)

Kolejność sekcji jest stałą domenową (`app/modules/reporting/domain/sections.py`):
1 identyfikacja i geometria, 2 podsumowanie, 3 MPZP, 4 POG + OUZ/OZS/OSDIS,
5 środowisko, 6 teren, 7 infrastruktura/transport, 8 jakość i kompletność,
9 źródła/provenance, 10 ograniczenia interpretacyjne; załącznik A — mapowanie
pól API.

- Każda wartość ma oznaczenie rodzaju: **fakt źródłowy** (atrybut rekordu, zapis
  uchwały, status aktu), **wynik obliczenia** (pole/udział w EPSG:2180,
  deniwelacja, spadek), **przybliżenie** (odsunięcie od granic, bufory sieci),
  **dane ręczne** (symbol podany przez użytkownika).
- Podsumowanie (sekcja 2) i macierz kompletności (sekcja 8) używają **tej samej**
  oceny obszaru (`_domain_assessments`): status `sprawdzono | częściowo |
  wymaga ręcznej weryfikacji | nieustalone | źródło niedostępne | poza zakresem
  potwierdzonego kontraktu`, powód/kod, data pobrania, wydanie, pewność. Nie ma
  syntetycznej oceny punktowej ani „scoringu inwestycyjnego”.
- `null` jest zawsze renderowany jako „nie określono”, `0` liczbowo; każda
  pusta sekcja ma jawny powód (brak danych ≠ brak ograniczenia).
- Transport jest jawnie „poza zakresem potwierdzonego kontraktu” (BK-305);
  KIUT pokazuje wyłącznie pokrycie powiatu, bufory sieci są przybliżeniem
  (BK-306).
- Szablon jest wydzielony do `backend/app/templates/report.html` (Jinja2,
  `autoescape`, `StrictUndefined`). WeasyPrint dostaje `url_fetcher`
  dopuszczający wyłącznie `data:` — PDF nie pobiera zasobów z sieci ani dysku.
- Tabela mapowania (`reporting/domain/field_mapping.py`) przypisuje każdą ścieżkę
  liścia `AnalyzeResponse` do sekcji, elementu i rodzaju albo podaje uzasadnienie
  pominięcia (sekret `access_token`, surowe `raw_attributes`, alias `pog.status`,
  parametry formularza UI). Test przechodzi po modelu Pydantic, więc nowe pole
  API bez decyzji zatrzymuje CI; `docs/report/field-mapping.md` jest generowany
  z tej samej tabeli, a załącznik A PDF pokazuje dla danej analizy liczbę
  wartości obecnych i null.

### 2. Pełne tabele MPZP i POG (BK-502)

- MPZP: Tabela 3.1 — jeden wiersz na strefę (symbol, stabilne ID wydzielenia,
  sposób przypisania, pole m², udział %, akt/wersja/wydanie, źródło); Tabela
  3.2 — jeden wiersz na (strefa, parametr) z jednostką (m, %, kondygnacje,
  wartość bezwymiarowa) i odsyłaczem `[E#]`; Tabela 3.3 — evidence (wartość
  surowa, dokument `[D#]`, strona, segment, jednostka redakcyjna, metoda
  ekstrakcji, wersja parsera, pewność, fragment). Hash dokumentu występuje raz,
  w tabeli dokumentów `[D#]`. Sprzeczne kandydatury → „wymaga weryfikacji —
  kandydaci: 2 kondygn. [E2]; 3 kondygn. [E3]”, bez wyboru.
- POG: Tabela 4.1 status aktu i zakres danych; 4.2 strefy (ID, symbol, rodzaj,
  pole, udział, status aktu z plakietką projektu, źródło obiektu); 4.3 parametry
  z jednostkami i profile — **wiersz na strefę, bez średnich**; 4.4–4.6 OUZ,
  OZS, OSDIS jako osobne grupy (z rozróżnieniem „sprawdzono — brak obszaru” od
  „nie ustalono”); 4.7–4.8 provenance aktu i dokumenty; 4.9 relacja MPZP–POG
  (informacyjna).
- Dokładność prezentacji: pole i udział 0,01. Suma udziałów wierszy jest liczona
  z wartości źródłowych i pokazywana z odchyleniem w **pp**; zaokrąglenia nie
  są korygowane do 100%.
- Projekt/akt w trakcie nigdy nie jest opisany językiem „obowiązuje”
  (etykieta daty z APP zależy od statusu).
- Długie tabele: `thead` jako `table-header-group` (powtarzany na każdej
  stronie), `tr { break-inside: avoid }`, `table-layout: fixed` z `colgroup`,
  `overflow-wrap: anywhere` i dzielenie wyrazów `lang="pl"`; znaczniki bez
  `nowrap` (test wykrył wychodzenie plakietki poza komórkę).

### 3. Deterministyczne mapy ze snapshotu (BK-503)

- Przy `save_analysis` (i przy wznowieniu MPZP) powstaje
  `analyses.report_map_snapshot` (migracja `025_report_map_snapshot`, JSONB,
  schemat `report-map-snapshot/1`), zawierający:
  - obrys działki i geometrie analizowanych stref MPZP/POG, OUZ/OZS/OSDIS,
    obiektów ISOK/GDOŚ i przybliżeń technicznych w **EPSG:2180**, przycięte do
    kadru i zaokrąglone do 1 cm;
  - kadr metryczny (`fit_frame`: działka + 10% z każdej strony dopełnione do
    proporcji 3:2, `meters_per_pixel`) i podziałkę z szeregu 1-2-5;
  - konfigurację renderowania z wersją `REPORT_MAP_CONFIG_VERSION`: wymiary,
    kolejność warstw, style warstw, paletę MPZP, font (rodzina, plik, rozmiar),
    styl POG zapisany w analizie (BK-403) wraz z progami tematu i stylem statusu
    aktu, tryb tematyczny POG (`REPORT_MAP_POG_THEME`, jeden z pięciu tematów);
  - `data_release_id` i daty pobrania danych każdej mapy;
  - `semantic_sha256` — SHA-256 kanonicznego JSON danych i konfiguracji (bez
    chwili zamrożenia).
- Renderer (`app/services/report_map.py`, Pillow) rysuje wyłącznie ze snapshotu:
  wypełnienia, wzory (OUZ/OZS/OSDIS, projekt, brak wartości — niezależnie od
  barwy), obrysy (także przerywane), podpisy symboli, podziałkę i strzałkę
  północy. Legenda, tryb, skala, układ, wydania, daty, wersje i hashe są w
  podpisie mapy (tekst PDF). Moduły mapy nie importują klienta HTTP.
- Podkład: wyłącznie zapisany, dozwolony artefakt PNG w EPSG:2180 z
  metadanymi i SHA-256 (`REPORT_MAP_BASEMAP_ARTIFACT_DIR`); brak pliku, inny
  SHA albo brak zgody → neutralne tło i adnotacja, nigdy błąd raportu ani
  pobranie WMS. Usunięto ustawienia `REPORT_MAP_BASEMAP_ENABLED`,
  `REPORT_MAP_WMS_*`, `REPORT_MAP_KIMPZP_OVERLAY_ENABLED`,
  `REPORT_MAP_KIUT_OVERLAY_ENABLED`.
- Hash semantyczny jest porównywalny między środowiskami; bajtowy SHA-256 PNG
  jest podawany z opisem środowiska (wersja Pillow, SHA-256 fontu) i porównywany
  tylko w identycznym środowisku. Niezgodność zapisanego hasha z treścią daje
  ostrzeżenie w PDF.
- Analizy sprzed migracji `025` nie są uzupełniane wstecz (zamrożenie bieżącą
  konfiguracją udawałoby stan z chwili analizy); raport odtwarza ich mapy z
  danych snapshotu analizy i oznacza to w podpisie mapy, sekcji 9 i 10.

## Alternatywy odrzucone

- **Zapis gotowego PNG w bazie** — bajty zależą od wersji Pillow/FreeType, więc
  nie spełniają kryterium „semantycznie identyczny obraz” między środowiskami;
  ponadto utrudniają zmianę układu raportu bez zmiany znaczenia mapy.
- **Pin do nieusuwanego wydania zamiast kopii geometrii** — geometrie
  wynikowe (przecięcia z działką) już są w snapshocie analizy (`result_v2`,
  `result_snapshot`, `risk_records`), a obiekty ryzyk nie pochodzą z wydań
  lokalnych; kopia przyciętej geometrii daje jeden, samowystarczalny zapis.
- **Pozostawienie WMS jako wyjątku UX** — łamie odtwarzalność i zasadę „WMS
  wyłącznie podgląd”; podkład jest dopuszczalny wyłącznie jako artefakt z SHA.
- **Średnia/„dominująca” wartość parametrów stref** — niedopuszczalna wykładnia;
  każda strefa jest osobnym wierszem.

## Konsekwencje

- Raport jest samowystarczalny i odtwarzalny: ten sam snapshot → te same
  tabele, mapa (hash semantyczny) i PDF bez sieci.
- Nowe pola `AnalyzeResponse` wymagają decyzji w tabeli mapowania (test).
- Zmiana stylu/konfiguracji mapy wymaga podbicia `REPORT_MAP_CONFIG_VERSION`
  (dotyczy tylko nowych analiz) i odświeżenia map referencyjnych
  (`python -m tests.fixtures.reports.build_fixtures --maps`).
- Rozmiar wiersza `analyses` rośnie o przycięte geometrie (kilka–kilkadziesiąt
  kB); pakiet audytowy (BK-505) może eksportować snapshot mapy bez zmian.
