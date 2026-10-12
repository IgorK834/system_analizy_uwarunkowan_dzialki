# AU-102 — przyczyny zerowego wyniku dla stref MPZP (Task 22.2)

Data: 10–11 października 2026 r. Baza kodu: `61f26da` z **niezacommitowanymi** zmianami (AU-101–AU-103 razem). Decyzje: [ADR-021](../adr/ADR-021-mpzp-zone-section-detection.md).
Zbiór: [`backend/tests/fixtures/mpzp_live`](../../backend/tests/fixtures/mpzp_live/README.md). Wyniki surowe:
[`results/au-102/`](results/au-102/README.md). Pomiar: `python -m scripts.evaluate_mpzp_live` (deterministyczny, bez sieci i modelu).

## Wynik w jednym akapicie

Na 41 odrębnych strefach z 9 realnych uchwał (7 gmin) udział stref z co najmniej jednym parametrem **albo jawnym, poprawnym powodem braku**
wzrósł z **56%** do **100%** w obu trybach parsera (próg zadania: 70%). Metryka zadania jest jednak łagodna — nie sprawdza, SKĄD
parametr pochodzi — więc obok niej mierzona jest **metryka surowa** (parametr liczy się tylko z adnotowanej sekcji strefy): z **46%/49%**
(`legacy`/`v3`) do **100%/100%**. `MW/U.4` (Kraków) ma teraz 6 (`legacy`) i 8 (`v3`) parametrów, każdy z numerem strony i cytatem
(wcześniej 0 w obu trybach). Odtwarzalność oczekiwanych parametrów (recall): `v3` 47% → **94%** (103/110), `legacy` 40% → **78%** (86/110).
**Tryb `legacy` nadal zwraca wartości spoza sekcji strefy (224 wartości, wcześniej 43)** — to znany, zmierzony kompromis jego architektury
(sklejanie segmentów); tryb `v3` ma 0 takich wartości. Wynik NIE jest oceną uogólnienia — zob. „Uczciwe ograniczenia”.

| Miara (41 stref, 110 oczekiwanych parametrów) | `legacy` przed | `legacy` po | `v3` przed | `v3` po |
|---|---:|---:|---:|---:|
| strefy z parametrem **albo** jawnym powodem (kryterium zadania, próg 70%) | 23/41 (56%) | **41/41 (100%)** | 23/41 (56%) | **41/41 (100%)** |
| – strony rozwojowe (`development`) | 18/31 (58%) | 31/31 | 19/31 (61%) | 31/31 |
| – strony „wstrzymane” (`holdout`, nieślepe — zob. niżej) | 5/10 (50%) | 10/10 | 4/10 (40%) | 10/10 |
| strefy z ≥ 1 parametrem w sekcji tej strefy (metryka surowa) | 19/41 (46%) | **41/41 (100%)** | 20/41 (49%) | **41/41 (100%)** |
| strefy oczekujące parametrów, które mają ≥ 1 parametr (metryka „czysta” z treści zadania) | 23/34 (68%) | 34/34 (100%) | 23/34 (68%) | 34/34 (100%) |
| oczekiwane parametry odtworzone (nazwa, wartość, strona w sekcji, cytat) | 44/110 (40%) | 86/110 (78%) | 52/110 (47%) | **103/110 (94%)** |
| wartości spoza sekcji strefy | 43 | **224** | 9 | **0** |
| wartości zwrócone ogółem | 162 | 496 | 164 | 335 |

Jawne powody (7 stref): 4 × `ZONE_NOT_IN_DOCUMENT` (symbol nie występuje w czytelnym tekście), 3 × `SCANNED_NO_OCR` (skan Bielska-Białej,
115 stron bez warstwy tekstowej) — przed zmianą wszystkie 7 miało ogólne `ZONE_SECTION_NOT_FOUND` (werdykt `fail_wrong_reason`).

## Przyczyny (kategorie, liczba stref, poprawka)

Każda przyczyna ma test regresji na REALNYM tekście uchwały w [`test_mpzp_live_corpus.py`](../../backend/tests/test_mpzp_live_corpus.py)
(i testy jednostkowe obok kodu). Liczby: strefy z niepowodzeniem w metryce surowej przed → po.

| # | Przyczyna | `legacy` | `v3` | Przykład (realny tekst) | Poprawka |
|---|---|---:|---:|---|---|
| C1 | **Symbol nazwany tylko zakresem**: „Dla terenów oznaczonych symbolami 1KDZ-11KDZ ustala się:” — symbolu `2KDZ` nie ma w tekście wprost; zakres z myślnikiem bez odstępów był jednym tokenem | 6 → 0 | 5 → 0 | Pisz § 25 (`2KDZ`), § 23 (`2PU`), § 35 (`2ZP`), § 31 (`3E`), § 18 (`12MNU`), § 17 (`5MNu`) | wspólny rozwijacz zakresów `find_zone_symbol_range_mentions` (`app/shared/zone_symbol.py`: końce włącznie, przedrostek/przyrostek jak w symbolu, skrót „1-13MWU”, myślnik z odstępami i bez, łamanie wiersza, limit 200 numerów, rodziny `MNu`/`MNU` rozróżniane, gdy jest czym); używany przez lokalizator `legacy` (`find_zone_sections`, kandydat „zakres symboli”) i `v3` (`zone_scope.range_mentions`); `split_symbol_range_token` w silniku ilości (zakres „1MNU-20MNU” w nagłówku nie był zakresem stref, więc wartości sekcji odrzucano jako cudze) |
| C2 | **Wprowadzenie sekcji nierozpoznane**: (a) „Ustalenia **szczegółowe** dla terenu oznaczonego symbolem ZC:”; (b) lista symboli po „oznaczone **symbolami** 1ZL oraz 2ZL” (pierwsze słowo kluczowe zjadało listę, więc punkty 7/9/10 uchodziły za osobne sekcje); (c) ustęp o jednym zdaniu „Wyznacza się teren … symbolem 1MN.” | 1 → 0 | 3 → 0 | Ruciane-Nida § 17 (`ZC`), § 18 (`2ZL`), § 19 (`KDD`); Czarny Bór § 14 (`1MN`, wcześniej zakres „cały paragraf”) | `zone_scope.py`: `_INTRO_START` (opcjonalne „szczegółowe”), ostatnie słowo kluczowe przed symbolem, `intro_end` dla zdań wyznaczających („Wyznacza się/Określa się”; samo „Ustala się … dla terenów 1Up i 2Up.” zostaje odwołaniem), punkt-liść wewnątrz wspólnego § jest wyjątkiem, a nie własną sekcją strefy |
| C3 | **Zapis opisowy poza słownikiem silnika**: strefy bez liczb („karty terenu”) — przeznaczenie w zwrocie „z podstawowym przeznaczeniem na …”, „1) przeznaczenie: …”, „przeznaczenie podstawowe terenu: …”, uprawnienia „możliwość lokalizacji …”, „dopuszcza się …” w wierszach przełamanych | 8 → 0 | 6 → 0 | Kraków Stare Miasto § 58 (`MW/U.4`), § 105 (`ZP.4`), § 22 (`U.9`), § 95 (`KP.1`); Góra Kalwaria § 28 (`2KR`); Legnica § 8 (`1KDL1/2`) | `descriptive_engine.py` (`descriptive/2`): etykiety i zwroty przeznaczenia, `możliwość …`/`dopuszcza się …`, skróty „ust./pkt.” nie kończą ustalenia, wiersz przełamany należy do tego samego ustalenia, wprowadzenie listy („…:”) nie jest ustaleniem, zwrot opisujący INNĄ strefę nie trafia do żądanej (wykaz rodzajów terenów w przepisach ogólnych) |
| C4 | **Symbol nieobecny w dokumencie** zgłaszany ogólnym `ZONE_SECTION_NOT_FOUND` | 4 → 0 | 4 → 0 | `MW/U.99`, `MW.99` (Kraków), `2MN` (Czarny Bór), `MN.1` (Ruciane-Nida) | jawne `ZONE_NOT_IN_DOCUMENT` (`mpzp_parser_reasons.py`): czytelny tekst, symbol nie występuje wprost, w zakresie ani z pomyłką OCR |
| C5 | **Skan bez warstwy tekstowej** zgłaszany jak brak symbolu | 3 → 0 | 3 → 0 | Bielsko-Biała LIII/749/98, 115 stron, strefy `A1`, `A6`, `B1` | jawne `SCANNED_NO_OCR` (dokument wymaga OCR, którego nie użyto albo nie dał tekstu); „nie znaleziono” nigdy nie mówi, że strefy nie ma |
| — | **Strona dowodu z cudzej sekcji** (przekrojowo, wpływa na metrykę surowa i na dowód) | 8 stref (43 wartości) → 224 wartości w 21 strefach, zob. niżej | 6 stref (9 wartości) → 0 | `MW/U.4`: zakaz „lokalizacji lokali mieszkalnych” wskazywał str. 39 zamiast 43; `v3` wskazywał też strony spoza sekcji dla `ZP.4`, `MW/U.28.1`, `1U`, `5MNu`, `7U` | dowód opisowy w `v3` niesie stronę i zakres znaków z BLOKU (`mpzp_parser_blocks._descriptive`); w `legacy` fragment jest szukany najpierw wśród segmentów-kandydatów tej strefy (`_attach_evidence_metadata`) |

Strefy z wiele-przyczynowym niepowodzeniem policzono do jednej, głównej kategorii (przypisanie: `docs/evaluation/results/au-102/*.json`, pole `zones[]`).

## Co pozostaje (świadomie poza zakresem AU-102)

`v3` nie odtwarza 7 z 110 oczekiwanych parametrów; `legacy` — 24. Wszystkie braki `v3` to luki słownika wielkości i ustaleń, a nie wykrywania sekcji:

| Strefa | Brak | Powód |
|---|---|---|
| Legnica `1MW`, `1MN`, `1E` | `max_building_height_m` (13,9 / 7,5 / 4,5 m) | leksykon nie zna rzeczownika „wysokość elewacji lub attyki budynku” |
| Czarny Bór `1MN` | `max_building_height_m` (9,0 m) | „wysokość zabudowy, liczona do wysokości kalenicy … – 9,00 m” poza wzorcami leksykonu |
| Kraków Kleparz `ZPb.9`, Bieńczyce `ZPp.3` | `prohibition` | zakaz zapisany jako lista z odwołaniem do kilku stref / po wprowadzeniu listy (celowo nie jest ustaleniem) |
| Góra Kalwaria `2ZN-ZP` | `primary_use` | „przeznaczenie:” z listą podpunktów w osobnych wierszach |

Wymagają osobnej zmiany leksykonu (`LEXICON_VERSION`) i ponownej kalibracji; nie dopisywano ich tutaj, żeby nie dostrajać słownika do dziewięciu uchwał.
`legacy`: ustalenia, których sekcja zaczyna się na stronie bez symbolu (§ ciągnący się na następną stronę) nie są kandydatami, a wartości z wielu
segmentów są sklejane — stąd 224 wartości spoza sekcji (oznaczane `ZONE_SECTION_AMBIGUOUS` i ręczną weryfikacją). Naprawa tej klasy to przełączenie na `v3`,
które blokuje bramka z PV3-17 (`NOT_DECIDABLE`; `app/core/mpzp_parser_rollout.json`); AU-102 jej nie dotyka.

## Regresja na próbkach niezależnych od poprawek (BK-603, 21 próbek)

Migawka `frozen_parse_results.json` porównana przed ponownym zamrożeniem (`scripts/freeze_mpzp_parser_modes.py`): **same dodatki, żadnej
zmiany ani ubytku wcześniej zwróconej wartości** (liczbowej i opisowej). Dodane: ustalenia `permission` (Legnica Szpital `1UZ`/`2UZ`, Bielsko
`230_U`/`230_UMW`), przeznaczenie i dopuszczenia Krakowa Morelowej (`MN.1`, tryb `legacy`: 3 przeznaczenia/dopuszczenia), korekta strony dowodu
(Bielsko, `230_U`, `roof_geometry`: 5 → 6), `SCANNED_NO_OCR` zamiast `ZONE_SECTION_NOT_FOUND` w skanie Pisza-Wiartla. Kalibracja pewności
została **powtórzona** (`scripts/calibrate_mpzp_confidence.py`): dane i progi bez zmian (n = 241, błędów 16, próg 0,9431), zmieniły się wersje parsera
w artefakcie i dwa wiersze zbioru oceny (ECE 0,0586 → 0,0594; Brier 0,03430 → 0,03438; cel ≤ 0,10 spełniony). Złote odpowiedzi modelu (`llm_replay`)
odtworzono skryptem i są identyczne z poprzednimi (13 rekordów).

## Uczciwe ograniczenia

1. **Adnotacje przygotował asystent AI** (odczyt tekstu uchwał); **brak niezależnego przeglądu człowieka** (`independent_human_review: pending`).
   Pomiar jest tak dobry, jak adnotacje; przegląd człowieka (≥ 20% stref i wszystkie parametry sporne) jest decyzją właściciela.
2. **Zbiór nie jest ślepy.** Wszystkie 9 dokumentów przeczytano przy diagnozie, a część poprawek wyprowadzono z dokumentów oznaczonych `holdout`
   (C2: Ruciane-Nida i Czarny Bór; część etykiet C3: Legnica). Wynik 100% jest więc **pokryciem przyczyn na znanym zbiorze**, nie oszacowaniem
   skuteczności na nowych uchwałach; jedyną niezależną kontrolą jest brak regresji na 21 próbkach BK-603 (j.w.). Ślepą próbą byłby nowy zestaw
   uchwał, ocenionych raz — wymaga nowych pobrań i adnotacji (zob. ADR-021, „Następne kroki”).
3. **Adnotację uzupełniono po pierwszym przebiegu** (Legnica `1MN` § 6 i `1E` § 7: osobne sekcje z przeznaczeniem tego samego symbolu) — poprawka
   kompletności adnotacji, nie oczekiwań; bez niej metryka surowa błędnie liczyła przeznaczenie jako „spoza sekcji”.
4. **Brak Warszawy:** `edziennik.mazowieckie.pl` nie odpowiedział (`ReadTimeout` 150 s w próbach 10 i 11 października, TLS nie wyłączano), więc
   zbiór ma 9 z 10 wskazanych uchwał. Tam, gdzie plan z działki korpusu był niedostępny, użyto innej uchwały tej samej gminy (decyzja właściciela) — pole
   `corpus_parcel` w manifeście; `null` = uchwała spoza korpusu referencyjnego.
5. **Strefa `MW/U.4` ma 6 parametrów w `legacy`, ale w `legacy` nie ma zakazu ze str. 43** — sekcja przechodzi na kolejną stronę, której segment nie wymienia
   symbolu (patrz „Co pozostaje”); `v3` zwraca zakaz i dopuszczenia ze str. 43 z zakresem znaków.
6. Śledzenie stron BIP w `mpzp_fetch` (landing page → karta → plik; poniżej) zweryfikowano na żywo dla trzech planów krakowskich (SHA-256 pliku zgodny z korpusem);
   inne układy BIP mogą wymagać własnych reguł — fetcher wtedy zwraca stronę HTML z ręczną weryfikacją, jak dotąd.

## Pozyskiwanie dokumentu: strony BIP zamiast pliku

Adresy z KIMPZP/Rejestru bywają stronami BIP, nie plikami. Dla Krakowa (`?dok_id=…`) strona wylicza załączniki jako karty (`…/n/298025/karta`), a plik
(`/plik.php?zid=…`) linkuje dopiero karta — dotychczasowy fetcher szedł najwyżej jeden skok do linku `.pdf` i kończył na HTML (parser dostawał stronę zamiast uchwały).
Teraz `mpzp_fetch` idzie najwyżej `MPZP_MAX_HTML_HOPS = 2` skoki, tylko w obrębie tej samej witryny, wybiera link „Tekst planu/uchwały” (pomija rysunek, prognozę,
GML i inne załączniki), a na karcie jedyny plik do pobrania; każdy skok przechodzi pełną walidację SSRF. Na żywo 2026-10-10: trzy adresy `landing_url` z korpusu
dały PDF-y o skrótach SHA-256 identycznych z zamrożonymi dokumentami. Test na zamrożonych, realnych linkach stron BIP: `backend/tests/fixtures/mpzp_fetch/krakow_bip`.

## Odtworzenie

```bash
cd backend
python -m scripts.evaluate_mpzp_live --verbose --out wynik.json     # pomiar obu trybów
python -m pytest tests/test_mpzp_live_corpus.py tests/test_zone_symbol.py tests/test_descriptive_engine.py tests/test_mpzp_zone_scope.py
python -m scripts.freeze_mpzp_parser_modes --check                   # migawka BK-603 aktualna
python scripts/calibrate_mpzp_confidence.py --check                  # kalibracja odtwarza się z danych
```
