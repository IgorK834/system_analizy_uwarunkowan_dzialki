# ADR-022 — Definicja `complete`, kompletność analizy i status nagłówkowy

Status: przyjęta technicznie 2026-10-12 (AU-104, Task 22.4, audyt R5 i §6 Etap 2 pkt 1); **definicja czeka na potwierdzenie właściciela** (sekcja „Do potwierdzenia”).
Numer: w opisie zadania ADR nosi nazwę `ADR-015-definicja-complete.md`, ale numer 015 zajmuje [ADR-015](ADR-015-api-errors-request-id-and-text-column-policy.md) (AU-003);
kolejny wolny numer to 022. Powiązane: [ADR-011](ADR-011-section-quality-matrix-and-audit-package.md) (macierz jakości sekcji),
[ADR-020](ADR-020-mpzp-acts-ru-channel-precedence-and-state.md) (akty i stan MPZP — `no_match`, `assignment_method`),
[ADR-002](ADR-002-pog-legal-status-and-coverage.md) (status prawny POG osobno od pokrycia), [ADR-023](ADR-023-discovery-pog-ru-wfs-budzet-zadan.md).

## Kontekst

`status` odpowiedzi (`complete` / `partial`) był jedną plakietką, której wartość `complete` wymagała jednocześnie: stref MPZP z **lokalnego wektora**
(`assignment_method = vector_intersection`), wiążącego i bieżącego POG z pokryciem `available`, dostępnych ISOK i GDOŚ (oraz KIUT, o ile kontrakt potwierdzony) i braku
ręcznej weryfikacji w jakimkolwiek źródle. Wektor MPZP istnieje tylko po ręcznym imporcie (wiele gmin go nie publikuje), więc w konfiguracji domyślnej status był
**zawsze `partial`**: 0 z 22 udanych analiz korpusu w audycie, 0 z 30 w live smoke z 2026-10-08 i 2026-10-11. Plakietka `partial` nie niosła informacji: nie mówiła, ile sekcji
sprawdzono ani których brakuje.

## Decyzje

1. **Nowa definicja.** `complete` ⇔ **wszystkie sekcje w zakresie analizy są sprawdzone**. Sekcja jest sprawdzona (`checked`), gdy źródło zwróciło wynik zgodny z kontraktem
   (`available` w macierzy jakości z ADR-011) albo — tylko dla MPZP — wszystkie odpytane kanały odpowiedziały poprawnie i nie wskazały obowiązującego planu (`no_match`,
   ADR-020; `no_match` nigdy nie występuje przy błędzie źródła). Brak pokrycia, błąd, wynik częściowy i „nieustalone” to `missing` — nie „brak ograniczenia”.
2. **MPZP bez lokalnego wektora.** MPZP może być ustalone przez **akt i dokument** (`assignment_method = document_candidate`) z jawną podstawą w kontrakcie; lokalny wektor
   (`vector_intersection`) jest **wyższą jakością** (znany udział strefy w działce), nie warunkiem. Nie są ustaleniem systemu: symbol podany ręcznie (`manual_user_input`) ani snapshot
   sprzed wersjonowania (`legacy`). Sekcja MPZP jest też niesprawdzona, gdy stan to `ambiguous`, strefa ma **sprzeczne parametry** bez wyboru parsera (`MPZP_PARAMETER_CONFLICT`), wektor ma
   `MPZP_PARTIAL_COVERAGE` / `MPZP_ZONES_OVERLAP` albo ścieżka modelu językowego jest zdegradowana (`MPZP_LLM_UNAVAILABLE`, `MPZP_LLM_CANDIDATES_REJECTED` — wynik deterministyczny, nie
   pełnowartościowy). `no_match` z jednego kanału (drugi wyłączony konfiguracją, np. `RU_MPZP_ACTS_ENABLED=false`) też jest sprawdzeniem — lista odpytanych kanałów jest w `mpzp_status.channels`.
3. **Zakres analizy** (reguła `completeness/1.0`, dziesięć sekcji macierzy jakości): w zakresie są geometria działki, MPZP, strefy POG, OUZ/OZS/OSDIS, zagrożenie powodziowe (ISOK), ochrona
   przyrody (GDOŚ), teren (NMT) i relacja MPZP–POG. **Poza zakresem** (`out_of_scope`): transport (brak kontraktu źródła), uzbrojenie terenu (tylko podgląd pokrycia KIUT, bez analizy
   odległości — kontrakt wektorowy KIUT/GESUT niepotwierdzony, jak w `context.py`) oraz relacja MPZP–POG, gdy POG jest sprawdzone, a MPZP jest `no_match` (nie ma strefy do porównania)
   albo pochodzi z aktu i dokumentu (strefa bez geometrii — pary MPZP×POG są liczone na przecięciu geometrii w EPSG:2180 i bez niej nierozstrzygalne z założenia,
   `MPZP_POG_PAIRS_NOT_SPATIAL`). Przy lokalnym wektorze MPZP nierozstrzygnięta relacja jest brakiem; relacja jest też brakiem, gdy POG samo nie jest sprawdzone. Sekcje poza
   zakresem **nie wchodzą do mianownika, ale zawsze są wymienione** w komunikacie i na liście sekcji — żadna nie jest po cichu pomijana. Wejście sekcji do zakresu to nowa wersja reguły.
4. **Co NIE obniża kompletności**, lecz jest osobnym faktem (zgodnie z ADR-002 i ADR-011): status prawny POG (`binding` / `project` / …), flaga ręcznej weryfikacji i świeżość danych. Trafiają do
   `completeness.notes` i do komunikatu („Do weryfikacji ręcznej: …”), np. „POG: status prawny `project` — plan ogólny nie jest (jeszcze) aktem wiążącym”. `complete` oznacza kompletność
   sprawdzeń, nie obowiązywanie planów ani brak ograniczeń.
5. **Kontrakt.** `AnalyzeResponse.completeness {schema_version, rule_version, checked, total, complete, headline, notes, sections[], origin}` — sekcja: `section`, `label`, `short_label`,
   `state` (`checked|missing|out_of_scope`), `quality_status` (status z macierzy, rozłączny od stanu), `basis` (MPZP: `vector_intersection` | `document_candidate` | `no_match`),
   `manual_review_required`, `reason_codes` — oraz `headline` (np. „6 z 8 sekcji sprawdzono; brak: MPZP, strefy POG. Poza zakresem: uzbrojenie terenu, droga i dostęp.”). **`status` zostaje**
   dla zgodności wstecznej i jest wyprowadzany z kompletności (`complete` ⇔ `completeness.complete`, inaczej `partial`; analiza wstrzymana zachowuje własny status). Reguła jest w jednym miejscu
   (`services/completeness.py`); UI, PDF i pakiet audytowy czytają zapisany wynik.
6. **Zapis i wersje.** Migracja `035_completeness` (`analyses.completeness` JSONB, nullable, bez uzupełniania wstecz): ocena z chwili analizy nie zmienia się, gdy reguła zostanie zmieniona
   (`rule_version`). Odczyt zapisu sprzed AU-104 odtwarza kompletność z macierzy jakości (`origin=reconstructed`) i **nie zmienia zapisanego `status`** — jego semantyka pochodzi ze starej definicji.
   `COMPLETENESS_RESULT_SCHEMA_VERSION` 1.0 wchodzi do `RESULT_CONTRACT_VERSION` (`…+completeness-v1.0`), więc cache nie miesza starej semantyki. `docs/report/field-mapping.md` odświeżony (693 ścieżki liści),
   raport PDF: układ `report-v2/2026.10.12-1`, pakiet audytowy `audit-exporter/1.4.0`. Wznowienie ręcznego symbolu przelicza kompletność w tej samej transakcji (zawsze `partial`: symbol ręczny nie jest ustaleniem systemu).
7. **Prezentacja.** UI: pasek kompletności (`role="progressbar"`, jeden segment na sekcję w zakresie, znak i tekst obok koloru) zamiast sztywnej plakietki `partial`; szczegóły w `<details>`; zapis sprzed AU-104
   zachowuje plakietkę z wyjaśnieniem. PDF, strona 1: ten sam komunikat, tabela sekcji (stan, podstawa, status źródła, znacznik weryfikacji) i zastrzeżenia.

## Wynik na korpusie referencyjnym i od końca do końca

**Live smoke 2026-10-12** (30 działek, żywe usługi, kod z drzewa roboczego; [raport](../evaluation/results/live-smoke/2026-10-12.md)): 30 × HTTP 200, 0 × 5xx, **`complete` = 0/30** (`partial` 30). Rozkład sprawdzonych
sekcji: 2 z 8 — 1 analiza, 3 z 8 — 17, 4 z 8 — 10, 5 z 8 — 2. Sekcje, których brakowało (liczba analiz): **strefy POG 30, OUZ/OZS/OSDIS 30, relacja MPZP–POG 30**, MPZP 26, teren (NMT) 20, ochrona przyrody 1.
Podstawa ustalenia MPZP tam, gdzie sekcja była sprawdzona: akt i dokument — 3 analizy (Kraków), `no_match` — 1 (gmina 281604); lokalnego wektora na korpusie nie ma (0).

**Źródło, które blokuje kryterium „≥ 1 działka korpusu ma `complete`”: POG.** Po AU-105 12 działek ma `binding` (Bielsko-Biała 7, Legnica 5), ale pokrycie to `partial` (`POG_COVERAGE_PARTIAL`, `POG_VECTOR_LINK_MISSING`):
discovery RU wskazuje akt, strefy i obszar zabudowy śródmiejskiej na działce, lecz bez odnośnika do APP/GML nie liczy udziałów powierzchniowych, więc sekcje „strefy POG” i „OUZ, OZS, OSDIS” nie są sprawdzone, a relacja MPZP–POG
nie ma wejścia. Na pozostałych 18 działkach RU nie zwraca obiektów POG (`POG_NOT_FOUND_IN_SOURCE` — to nie jest dowód braku planu). Kolejne blokady są zewnętrzne i zmienne: błąd usługi KIMPZP (MPZP `unavailable`)
oraz „Server disconnected” w WCS NMT (pochodne rastra, 20 z 30 analiz w ostatnim przebiegu; w poprzednim, godzinę wcześniej, 12 z 30 — usługa jest zmienna, a rozkład „n z 8” razem z nią).

**Od końca do końca (orkiestrator → PostGIS → API → cache → PDF) `complete` jest osiągalny bez lokalnego wektora MPZP** — `tests/test_completeness_e2e.py`: strefa z aktu i dokumentu (`document_candidate`), POG z wektorami, ISOK,
GDOŚ i NMT dostępne → `status = complete`, `headline` „Sprawdzono wszystkie 7 sekcji w zakresie analizy…”, ten sam komunikat po odczycie z bazy, z cache i na stronie 1 PDF; brak pochodnych rastra NMT obniża status do `partial` i nazywa
sekcję („brak: teren (NMT)”). `tests/test_terrain_e2e.py` potwierdza to samo dla `no_match`. Czyli kryterium jest spełnione przez definicję i dowód integracyjny, nie na korpusie, który nie ma jeszcze danych POG o pokryciu `available`.

## Konsekwencje

- `complete` jest osiągalny w konfiguracji domyślnej, ale na korpusie referencyjnym jeszcze go nie ma: blokuje POG bez analizy powierzchniowej (zob. wyżej). Najkrótsza droga: analiza powierzchniowa POG na geometrii stref pobranej
  z WFS (ADR-023, „Do potwierdzenia” pkt 4) albo import lokalnego wydania gminy (po imporcie POG ma pokrycie `available`).
- Zmiana semantyki `complete` wymaga zmiany oczekiwań w testach i dokumentacji (README „Status analizy”, `docs/current_state.md`); cache jest unieważniony jednorazowo.
- Statusy `complete` zapisane wcześniej (rzadkie, wymagały lokalnego wektora) pozostają, jakie były; ich kompletność w odczycie jest odtworzona i oznaczona.
- Wykryte przy okazji: `RESULT_CONTRACT_VERSION` po dopisaniu składnika miał 71 znaków, a kolumna `analyses.result_contract_version` — 64; bez poszerzenia (do 128, migracja 035) każda analiza kończyła się 503
  `PERSISTENCE_FAILED`. Testy jednostkowe tego nie wykryły; wykrył live smoke. Dodano test pilnujący zapasu długości.

## Do potwierdzenia przez właściciela

1. Definicja zakresu: uzbrojenie terenu i transport jako `out_of_scope` w wersji 1.0 (zamiast „brak”); relacja MPZP–POG poza zakresem przy `no_match`.
2. `no_match` jako „sprawdzono” w sekcji MPZP — RU jest niepełny i nie dowodzi braku planu (komunikat to mówi); alternatywa to traktowanie `no_match` jako `missing`.
3. Że status prawny POG (projekt, w opracowaniu) i ręczna weryfikacja nie obniżają `complete` (są w komunikacie i `notes`) — alternatywa to dodatkowy wymóg `binding`, jak w poprzedniej definicji.
4. Teren (NMT) w zakresie: poprzednio NMT był pominięty „świadomie” w statusie; teraz niesprawdzony teren (w tym niedostępne pochodne rastra) uniemożliwia `complete`.
