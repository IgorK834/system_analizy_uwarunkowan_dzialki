# ADR-005: Kontrolowany ręczny symbol strefy MPZP i informacyjna ocena relacji MPZP–POG

- Status: Zaakceptowany
- Data: 2026-09-25
- Zadania: BK-204 (Task 3.4) i BK-205 (Task 3.5); zależności BK-201, BK-105,
  BK-106, BK-202, BK-203
- Kontekst szerszy: ADR-001 (granice modułów), ADR-002 (status prawny POG),
  ADR-004 (wektor stref MPZP), `docs/data_sources/mpzp_contracts.md`

## Kontekst

**BK-204.** Przepływ `waiting_for_user_input` → `POST /analyze/resume` istniał,
ale: (1) API nie przekazywało kandydatów, planu ani źródła, więc użytkownik
podawał symbol „w ciemno”; (2) resume ponownie pobierał dokument spod URL, więc
mógł odczytać inną uchwałę niż ta, którą rozpoznano przy wstrzymaniu;
(3) ręczny symbol dostawał fikcyjne 100% pola działki i status dominującej;
(4) parametry z dokumentu nie były oznaczane jako zależne od decyzji
użytkownika; (5) raport nie mówił wprost, że symbol podano ręcznie.

**BK-205.** Relacja MPZP–POG była booleanem `conflict_with_mpzp` liczonym dla
„strefy dominującej” MPZP i dominującej strefy POG, a reguła, uzasadnienie i
pewność były schowane w `raw_attributes.scenario`. UI/PDF pokazywały
„Zgodność potwierdzona wstępnie”, co brzmi jak rozstrzygnięcie prawne.

## Decyzja — tryb ręczny MPZP (BK-204)

1. **Kontrakt bez nowego statusu.** Publiczny status nadal
   `waiting_for_user_input`, w bazie `waiting_for_zone_symbol`; endpoint
   `POST /analyze/resume` bez zmian ścieżki i ciała żądania.
2. **Materiał przed formularzem.** `AnalyzeResponse.manual_zone_context`
   (tylko przy `manual_zone_required=true`): `plan_id`,
   `candidate_zone_symbols`, `document_status`
   (`pinned | unavailable | not_provided`), metadane przypiętego dokumentu
   (SHA-256, rozmiar, data pobrania, `document_version_id`,
   `preview_path`), klucz podglądu rastrowego (`mpzp`), wzorzec i długość
   symbolu (jedno źródło reguł walidacji UI i API) oraz nota ograniczeń.
   UI pokazuje kolejno: obraz źródłowy (mozaika kafli z backendowego proxy
   `/api/v1/map/tiles/mpzp` z obrysem działki), plan i dokument, kandydatów,
   dopiero potem pole symbolu i wymagane potwierdzenie porównania.
3. **Przypięcie artefaktu przy wstrzymaniu.** Orkiestrator pobiera uchwałę w
   chwili wstrzymania i w tej samej transakcji co analiza zapisuje
   `analysis_pending_documents` (bajty, SHA-256, media type, URL żądany i
   końcowy, data pobrania) oraz rejestruje `DocumentVersion` w module
   dokumentów (ten sam klucz co późniejszy audyt parsera). Błąd pobrania nie
   blokuje wstrzymania — ostrzeżenie `MPZP_PENDING_DOCUMENT_UNAVAILABLE`.
   Bajty są w PostgreSQL (`bytea`, limit pobrania 50 MB), bo zapis musi być
   transakcyjny z analizą; magazyn obiektowy pozostaje poza zakresem.
4. **Bezpieczny podgląd źródła.** `GET /analyze/{id}/pending-document` serwuje
   przypiętą kopię: PDF `inline`, HTML wyłącznie jako
   `application/octet-stream` + `attachment` + CSP `sandbox`; zawsze
   `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
   `X-Document-SHA256`. Adres zewnętrzny jest klikalny tylko jako
   zweryfikowany HTTPS.
5. **Resume bez sieci i bez częściowego zapisu.** Kolejność: 404/409 → walidacja
   formatu symbolu (422) → weryfikacja SHA i parsowanie przypiętego dokumentu
   (błąd → 503, snapshot nietknięty) → `SELECT … FOR UPDATE` wiersza analizy i
   ponowne sprawdzenie statusu (równoległe resume → 409) → jeden commit. Drugie
   resume dostaje 409, więc strefy nie są dublowane. Brak przypiętego
   dokumentu (starsze wstrzymania, błąd przy wstrzymaniu) nie wywołuje
   ponownego pobrania: symbol jest zapisywany bez parametrów, z
   `MPZP_PINNED_DOCUMENT_MISSING`.
6. **Brak fikcyjnego udziału.** `MpzpZoneResult.intersection_area_sqm` i
   `intersection_pct` są nullable; strefa bez wektora (`manual_user_input` i
   `document_candidate`) ma je `null`, `is_dominant=false`. Usunięto nieużywany
   helper „cała działka = jedna strefa”. Migracja 020 zamienia historyczne
   100% takich stref na `NULL` (downgrade odtwarza dawną konwencję).
7. **Flagi zależności od decyzji.** `cap_fallback_zone` ustawia
   `manual_review_required=true` strefie, źródłu i **każdemu** parametrowi;
   `manual_selection` zapisuje wpisany symbol, plan, kandydatów, czy symbol był
   wśród kandydatów (`MPZP_MANUAL_SYMBOL_NOT_IN_CANDIDATES`), SHA i wersję
   dokumentu. Ocena MPZP–POG z udziałem strefy ręcznej nie jest
   rozstrzygana (pkt 12).
8. **Nigdy `complete`.** Resume zawsze zapisuje `partial`; `_result_status`
   jawnie zwraca `partial` dla każdej strefy bez `vector_intersection`.
9. **Zachowanie reszty snapshotu.** Resume usuwa i odtwarza wyłącznie strefy
   MPZP; POG (z `result_v2`), ryzyka, infrastruktura, rekordy źródeł (w tym NMT)
   i ostrzeżenia innych sekcji zostają. Ponownie liczone są tylko ostrzeżenia
   oceny MPZP–POG.
10. **Raport.** PDF ma baner „UWAGA: symbol strefy podano ręcznie …”, w karcie
    strefy decyzję użytkownika, dokument (SHA) i „udział nieustalony”, a w
    ograniczeniach — brak ustalenia udziału.

## Decyzja — relacja MPZP–POG (BK-205)

11. **`PogResult.compatibility_assessment`** zastępuje `conflict_with_mpzp`
    (API, kolumna JSONB `pog_data.compatibility_assessment`, snapshot
    `result_v2`, TS, UI, PDF). Pola: `status`, `reason_code`, `as_of` (data
    stanu prawnego: potwierdzenie statusu POG, inaczej chwila analizy),
    `rule_id`/`rule_version` zestawu reguł, `aggregation`, `sources[]`
    (tabela reguł, akt POG, akt/dokument MPZP z wersją), `rationale`,
    `manual_review_required`, `zone_pairs[]`, `informational_notice`,
    `legacy_evidence`.
12. **Pary stref zidentyfikowane przestrzennie.** Para powstaje dla każdej
    strefy MPZP (bez styczności) i strefy POG, których przecięcia z działką w
    EPSG:2180 mają wspólną część > 1e-6 m²; para ma `overlap_area_sqm` i
    `overlap_pct`. Geometria przecięć jest przenoszona wewnętrznym polem
    `intersection_wkt` (EPSG:2180, `exclude=True` — nie trafia do API ani
    snapshotu). Gdy którakolwiek strona nie ma geometrii (symbol ręczny,
    discovery, snapshot bez geometrii), para jest tworzona dla każdej
    kombinacji, ale jako `spatially_identified=false` i nigdy nie jest
    rozstrzygnięta: `uncertain` z `rule_result` z tabeli albo `unknown`.
13. **Reguły.** Każdy wpis tabeli `planning_compatibility` ma stabilne
    `rule_id` = `mpzp-pog-function-table:<funkcja>:<typ>`, wersję zestawu
    (`1.0`) i źródło (opis tabeli, bez przypisywania jej mocy wykładni prawa).
    Funkcja MPZP musi być dokładną wartością katalogu (bez heurystyk nazw);
    brak funkcji albo brak reguły → para `unknown`. Walidator Pydantic odrzuca
    parę `compatible/incompatible` bez `rule_id`, `rule_version`, `source`,
    `as_of` albo bez identyfikacji przestrzennej.
14. **Agregacja bez uśredniania — „najsłabsze ogniwo”:** `incompatible`, jeśli
    którakolwiek para jest rozbieżna; inaczej `unknown`, jeśli którakolwiek
    para nie ma reguły lub danych; następnie `uncertain`; `compatible` tylko
    gdy wszystkie pary są `compatible`, przestrzenne i razem pokrywają działkę
    (tolerancja 0,1%) przy `coverage_status=available` — w przeciwnym razie
    `uncertain` (`PAIRS_PARTIAL_COVERAGE`). Parametry różnych stref nie są
    łączone.
15. **Ścieżki bez oceny par** (odrębne `reason_code`): projekt POG
    (`POG_PROJECT_NOT_BINDING`) i procedura (`POG_PROCEDURE_IN_PROGRESS`) →
    `not_applicable` (projekt nie ustanawia obowiązku); akt nieaktualny
    (`POG_SUPERSEDED`) → `not_applicable` z odesłaniem do aktu zastępującego;
    potwierdzony brak aktu (`POG_NO_ACT_CONFIRMED`) → `not_applicable`; brak
    wyniku POG (`POG_SOURCE_MISSING`) i niepotwierdzony status
    (`POG_STATUS_UNKNOWN`) → `unknown`; brak stref MPZP/POG lub brak wspólnych
    części → `unknown`.
16. **Legacy.** Migracja 021 przenosi `conflict_with_mpzp` (i ewentualne
    `raw_attributes.scenario.compatibility`) do `legacy_evidence` ze statusem
    `unknown` i `reason_code=LEGACY_BOOLEAN_ONLY`; walidator nie pozwala, by
    zapis legacy miał inny status. Ten sam mechanizm działa przy odczycie starych
    snapshotów JSON. Downgrade odtwarza boolean.
17. **Prezentacja.** MPZP i POG pozostają osobnymi sekcjami; ocena to osobna
    sekcja „Relacja MPZP–POG — analiza informacyjna” (UI i PDF) z notą, że nie
    jest opinią prawną i nie przesądza o prawnej możliwości zabudowy. Etykiety
    statusów nie używają słów „zgodne/dopuszczalne” (`compatible` →
    „brak wskazanej rozbieżności w tabeli reguł”). Ocena nie podnosi statusu
    analizy.

## Konsekwencje

- Kontrakt: `MPZP_RESULT_SCHEMA_VERSION` 2.0 → 2.1, `POG_RESULT_SCHEMA_VERSION`
  2.2 → 2.3; sygnatura cache zmienia się, więc starsze snapshoty nie są
  serwowane jako trafienie cache (odczyt historyczny działa bez zmian).
- Klienci czytający `pog.conflict_with_mpzp` muszą przejść na
  `pog.compatibility_assessment.status`; stare snapshoty wracają z
  `legacy_evidence`.
- W produkcji większość stref MPZP pochodzi z fallbacku (ADR-004), więc ocena
  zwykle kończy się `uncertain`/`unknown` z parami nieprzestrzennymi — to
  zamierzone: brak geometrii nie może dać pozornego rozstrzygnięcia.
- Przypięte dokumenty zwiększają rozmiar bazy (jeden na wstrzymaną analizę);
  retencja jest do ustalenia razem z polityką magazynu artefaktów.
