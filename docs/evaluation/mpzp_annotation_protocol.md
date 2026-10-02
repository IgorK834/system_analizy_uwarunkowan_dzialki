# Protokół anotacji korpusu parsera MPZP (BK-603, PV3-02)

Protokół opisuje, jak powstaje i jak jest zamrażany niezależny zbiór końcowy do oceny
silników ekstrakcji parametrów MPZP. Obowiązuje dla każdej nowej wersji korpusu
(`backend/tests/fixtures/mpzp_evaluation/manifest.json`, nowy `corpus_id`). Walidator
(`python3 backend/scripts/evaluate_mpzp_parser.py --validate-only --profile final-v2`) sprawdza
programowo wszystko, co da się sprawdzić maszynowo; ocena trafności interpretacji pozostaje
po stronie ludzi.

## 1. Zasady, których nie wolno łamić

1. **Anotują ludzie.** Pierwszy i drugi anotator to osoby; asystent AI nie jest anotatorem
   zbioru końcowego. Anotacje BK-603 (`claude-sonnet-5-5`, `independent_human_review: pending`)
   pozostają w korpusie jako **zbiór rozwojowy**.
2. **Żaden silnik nie widzi zbioru końcowego przed zamrożeniem.** Dotyczy parsera `legacy`, `v3`,
   `hybrid` i modelu językowego, także w trybie „na próbę”. Ewaluator odmawia pracy na korpusie bez
   zapisu zamrożenia, a `--validate-only` niczego nie uruchamia. Wyjątek: narzędzie ręczne
   `llm_spike.py` używa wyłącznie bloków **zbioru rozwojowego** (ADR-012).
3. **Anotacje powstają z tekstu źródłowego**, nie z wyników silnika, i nie są poprawiane po
   obejrzeniu wyników. Zmiana po zamrożeniu = nowy `corpus_id`, nowy skrót, ponowna ocena
   wszystkich wyników.
4. **Drugi anotator pracuje niezależnie**: nie widzi anotacji pierwszego, nie konsultuje się z nim
   przed oddaniem, używa tej samej wersji tekstu migawki. Rozmowa jest dopuszczalna dopiero na
   etapie rozstrzygania (pkt 7).
5. **Pobrania tylko z publicznych źródeł** (BIP, dzienniki urzędowe, jawne wypisy), za zgodą
   właściciela na konkretną listę, **bez wyłączania weryfikacji TLS**. Przy błędzie certyfikatu
   dokument się odrzuca i zapisuje przyczynę; nie obchodzi się błędu.
6. **W repozytorium tylko tekst stron** (`pages.json`), metadane (`source.json`), ewentualne
   tabele (`tables.json`); surowych PDF nie przechowuje się (opcja `--keep-pdf` zapisuje je poza
   repozytorium, wyłącznie do ręcznej anotacji skanów).

## 2. Skład zbioru końcowego (minimum)

| Wymaganie | Minimum | Jak sprawdzane |
|---|---|---|
| nowe próbki | 20 | profil `final-v2`: `split: final` |
| gminy niewykorzystane wcześniej | 10 | `previous_corpus.gminas` |
| województwa | 4 | `documents[*].voivodeship` |
| tabela parametrów w PDF (`pdf_table`) | 5 | format próbki |
| prawdziwe skany (`ocr_real`, bez symulacji) | 4 | format próbki; symulacje są odrzucane |
| dokumenty HTML lub wypisy (`html`) | 4 | format próbki |
| strategie zakresu strefy 1–6 | każda co najmniej raz | `zones[*].scope_strategy` |
| drugi anotator | co najmniej 20% próbek | `second_annotation.sample_ids` |

Cel (nie wymóg walidatora): co najmniej dwie próbki na każdą strategię i co najmniej jedna próbka
ujemna (dokument bez parametrów katalogu, z oczekiwanym brakiem wartości).

**Strategie zakresu** (Task 20.6; anotator wpisuje strategię, którą naprawdę widzi w tekście):

| Nr | Układ | Wskazówka |
|---|---|---|
| 1 | osobny § na strefę | jedna strefa, jeden paragraf ustaleń |
| 2 | wspólny § dla listy lub zakresu symboli | „dla terenów oznaczonych symbolami A, B i C” |
| 3 | numerowane podpunkty „N) dla terenu X:” | strefy jako punkty jednego ustępu |
| 4 | wartości przypisane symbolom listą w akapicie | „w terenie X – …; w terenie Y – …” |
| 5 | klauzula ogólna per symbol | wskaźnik podany w klauzuli ogólnej, nie w sekcji strefy |
| 6 | tabela | wiersze tabeli przypisane do strefy |

Skan OCR nie jest strategią, lecz formatem (`ocr_real`); ma własną strategię zakresu jak każdy tekst.

## 3. Lista dokumentów i źródeł (przed pobraniem)

Przed jakimkolwiek pobraniem powstaje lista kandydatów w osobnym pliku, zatwierdzana przez
właściciela. Wpis: gmina, województwo, tytuł aktu, URL, format spodziewany, powód wyboru
(strategia zakresu, format), znana wielkość. Pobranie „przesiewowe” (żeby ocenić układ) też
wymaga zgody, bo jest pobraniem. Limity: tylko dokumenty z listy; każdy dodatkowy dokument
wymaga odrębnej zgody; dokumenty odrzucone po przesiewie są wpisywane do README korpusu z powodem.

Wykluczenia: gminy z poprzedniego korpusu; dokumenty już użyte (URL w `previous_corpus.document_urls`);
projekty uchwał bez uchwalenia; wykazy i obwieszczenia, które nie są aktem; dokumenty, których nie
da się pobrać z prawidłowym TLS.

## 4. Przygotowanie migawki

Z katalogu `backend/`, w obrazie backendu (jest w nim Tesseract):

```bash
python -m scripts.build_mpzp_text_fixture --url <URL> --output-dir <katalog> --note "<opis>" \
  --voivodeship "<województwo>"
# prawdziwy skan: fragment stron i OCR produkcyjnym Tesseractem
python -m scripts.build_mpzp_text_fixture --url <URL> --output-dir <katalog> --ocr --pages 1-4 \
  --voivodeship "<województwo>"
```

`source.json` zapisuje URL, czas pobrania, rozmiar (`content_length`), SHA-256 pobranych bajtów
(`document_sha256`), metodę ekstrakcji, wersję OCR, numery stron źródła i `tls_verification`.
Do manifestu przenosi się: `url`, `fetched_at`, `content_length`, `document_sha256`, `pages_sha256`
(skrót zapisanego `pages.json`), `legal_basis`, `gmina`, `voivodeship`, `tls_verification: "enabled"`.

Dla **prawdziwego skanu** anotator czyta tekst z **obrazu strony**, a nie z wyniku OCR; różnice
między obrazem a OCR zapisuje w `ambiguity` (rodzaj `extraction_artifact`). Surowy PDF skanu
anotator otwiera z kopii poza repozytorium (`--keep-pdf`).

## 5. Schemat anotacji

Próbka: `sample_id`, `document_id`, `split`, `format` (`pdf_text`, `pdf_table`, `html`, `ocr_real`),
`multi_zone` (prawda, gdy zon > 1), `zones`, `annotator` (`{"id", "kind": "human"}`), `annotated_at`,
`notes`. Strefa: `symbol` (dokładnie jak w tekście, np. `1.4U,MN`), `scope_strategy` (1–6),
`annotations`.

Anotacja (jedna wartość jednego parametru w jednej strefie):

| Pole | Znaczenie |
|---|---|
| `parameter` | jeden z 9 parametrów katalogu (niżej) |
| `operator` | `max`, `min`, `range_lower`, `range_upper`, `exact` — dozwolony dla parametru |
| `raw_value` | fragment tekstu z wartością, **dosłownie** |
| `normalized_value` | liczba po jawnej regule normalizacji |
| `unit` | jednostka katalogu (`m`, `ratio`, `percent`, `deg`, `count`) |
| `normalization` | `identity`, `ratio_to_percent`, `range_lower`, `range_upper`, `word_number`, `manual` |
| `evidence` | dosłowny cytat zawierający `raw_value` (białe znaki są normalizowane) |
| `anchor` | dosłowny tekst oznaczający początek sekcji strefy; cytat leży do 5000 znaków za kotwicą |
| `page` | numer strony źródła, na której zaczyna się cytat |
| `status` | `required` (ma być znalezione) albo `acceptable` (dopuszczalne, ale nie błąd) |
| `applicability` | `zone_section` albo `general_clause` (klauzula ogólna nazywająca symbol) |
| `ambiguity` | `{kind, note}`, gdy wartość jest warunkowa, niejednoznaczna lub zniekształcona |

Katalog (9): `max_building_height_m` (m, `max`), `min_intensity` / `max_intensity` (ratio),
`max_building_coverage_percent` (percent, `max`), `min_biologically_active_percent` (percent, `min`),
`roof_angle_min_deg` / `roof_angle_max_deg` (deg), `max_storeys` (count, `max`), `setback_m`
(m, `exact`). Parametry opisowe (przeznaczenie, zakazy, forma dachu, parkowanie) są poza katalogiem.

Zasady:

- **Jedna wartość = jedna anotacja.** Wartości warunkowe (inna dla dachu płaskiego i stromego)
  są osobnymi anotacjami z `ambiguity.kind = conditional_value`.
- **Nie liczymy i nie domyślamy się.** Ułamek bez `%` zapisuje się regułą `ratio_to_percent`
  (`0,35` → 35) z `ambiguity.kind = implicit_percent`; liczba słowna regułą `word_number`;
  artefakt ekstrakcji (np. „300” w miejscu „30°”) wartością z `manual` i notą.
- **Wartość spoza katalogu nie jest anotowana** (np. wysokość urządzeń sportowych w strefie
  z zakazem zabudowy: `ambiguity.kind = contradiction_in_source`, jeśli dotyczy katalogu).
- **Klauzula ogólna** (`applicability: general_clause`) nie liczy się do recall silnika, który
  czyta sekcję strefy; jest raportowana osobno. **Klauzula resztowa** („w pozostałych terenach”)
  to `ambiguity.kind = residual_clause`.
- **Strefa bez wartości katalogu** (próbka ujemna) ma pustą listę `annotations`; oczekiwany brak
  jest wynikiem anotacji, nie jej pominięciem.
- Rodzaje niejednoznaczności używane dotąd: `conditional_value`, `implicit_percent`,
  `general_clause`, `extraction_artifact`, `shared_section`, `applicability_unresolved`,
  `residual_clause`, `number_word`, `building_line_not_setback_phrase`, `degenerate_limit`,
  `contradiction_in_source`, `symbol_typo_in_source`. Nowy rodzaj dopisuje się tutaj.

Walidator sprawdza programowo: istnienie cytatu w tekście źródłowym (po kotwicy), zgodność strony,
`raw_value` ⊂ `evidence`, jednostki i operatora z katalogiem, reguły normalizacji, brak duplikatów,
zgodność skrótu `pages.json`.

## 6. Podział rozwojowy i końcowy

- **Rozwojowy 1:** dokumenty używane do rozwoju i regresji parsera (BK-603).
- **Rozwojowy 2:** poprzedni zbiór końcowy BK-603 (14 próbek). Został użyty do analizy błędów,
  więc nie jest już niezależny; `development_round: 2`.
- **Końcowy (nowy):** dokumenty z pkt 3, anotowane wg tego protokołu, zamrożone przed
  uruchomieniem jakiegokolwiek silnika. Strojenie i analiza błędów wolno prowadzić wyłącznie na
  zbiorach rozwojowych.

Szkielet nowej wersji powstaje poleceniem (nie modyfikuje istniejącego manifestu):

```bash
python3 backend/scripts/mpzp_corpus_tools.py skeleton --corpus-id PV3-02-mpzp-parser-evaluation-<data> \
  --output backend/tests/fixtures/mpzp_evaluation/manifest.v2-draft.json
```

## 7. Drugi anotator, zgodność i rozstrzyganie

1. Losowanie próbek do podwójnej anotacji: co najmniej 20% próbek końcowych (zaokrąglone w górę),
   losowane przed anotacją, ziarno zapisane w README korpusu; przynajmniej po jednej próbce z formatu
   `pdf_table`, `ocr_real` i `html`, jeśli to możliwe.
2. Pierwszy anotator zapisuje swoje anotacje tych próbek w `first_annotation.json`
   (`{"annotator": {...}, "samples": [...]}`) **przed** otrzymaniem anotacji drugiego; drugi — w
   `second_annotation.json`. Oba pliki mają schemat próbek z pkt 5.
3. Raport zgodności:

   ```bash
   python3 backend/scripts/mpzp_corpus_tools.py agreement --first first_annotation.json \
     --second second_annotation.json --output-dir <katalog korpusu>/second
   ```

   Jednostką jest para (próbka, strefa, parametr katalogu). Raport podaje (z licznikami):
   **obecność parametru** (zgodność obserwowana i kappa Cohena), **dokładną wartość** (zbiory wartości
   wymaganych równe, wśród par, w których obie osoby podały wartość) i **wszystkie wartości**. Typy
   rozbieżności: `zone_only_one`, `presence`, `value`, `status_or_scope`.
4. **Dziennik rozstrzygnięć** (`adjudication_log.csv`): jeden wiersz na rozbieżność, z `resolution`
   (`a`, `b`, `both_acceptable`, `other`), `resolved_by`, `rationale`, `resolved_at`. Rozstrzyga
   osoba trzecia albo obaj anotatorzy wspólnie, patrząc w **tekst źródłowy**, nie na wynik silnika.
   Rozstrzygnięcie `a` lub `b` musi być widoczne w zamrożonych anotacjach manifestu (walidator to
   sprawdza); `both_acceptable` i `other` wymagają uzasadnienia.
5. Raport zgodności i dziennik są plikami korpusu z zapisanymi SHA-256 (`second_annotation` w manifeście);
   walidator **przelicza** zgodność z dwóch niezależnych plików i odrzuca raport, który się nie zgadza.
   Kappa niższa niż 0,6 lub zgodność dokładnej wartości niższa niż 0,8 nie blokuje zamrożenia, ale
   wymaga opisu w README korpusu (co jest niejednoznaczne w protokole) i, jeśli to możliwe,
   uszczegółowienia tego protokołu przed anotacją kolejnych próbek.

## 8. Zamrożenie

Kolejność jest obowiązkowa:

1. migawki i anotacje pierwszego anotatora kompletne; drugi anotator oddał swoje;
2. raport zgodności, dziennik rozstrzygnięć i zastosowanie rozstrzygnięć w anotacjach;
3. `python3 backend/scripts/evaluate_mpzp_parser.py --corpus <manifest> --validate-only --profile final-v2`
   (poza brakiem zapisu zamrożenia musi nie zgłaszać problemów);
4. `python3 backend/scripts/mpzp_corpus_tools.py freeze --corpus <manifest>` — zapisuje
   `freeze.annotations_sha256`, `frozen_at`, `engines_run_before_freeze: []` i oświadczenie; odmawia
   zamrożenia, gdy cokolwiek z profilu nie jest spełnione;
5. dopiero teraz wolno uruchomić silnik na zbiorze (`evaluate_mpzp_parser.py --engine …`).

Po zamrożeniu zmiana jakiejkolwiek anotacji, strefy, dokumentu lub normalizacji zmienia
`annotations_sha256` i kończy się błędem ewaluatora (`annotations changed after freeze`).
Oświadczenie `engines_run_before_freeze: []` jest zapisem deklaracji osoby zamrażającej; technicznym
zabezpieczeniem jest odmowa pracy ewaluatora na korpusie niezamrożonym.

## 9. Log pobrań i podstawa prawna

Log pobrań: plik, źródło (URL), rozmiar, SHA-256, czas pobrania, weryfikacja TLS, podstawa prawna —
generowany z manifestu:

```bash
python3 backend/scripts/mpzp_corpus_tools.py download-log --corpus <manifest> --only-final \
  --output backend/tests/fixtures/mpzp_evaluation/download_log.csv
```

Podstawa wykorzystania: uchwały w sprawie MPZP są aktami prawa miejscowego, jawnymi (BIP, dzienniki
urzędowe). Akty normatywne nie są przedmiotem prawa autorskiego (art. 4 pkt 1 ustawy o prawie
autorskim i prawach pokrewnych), a dostęp do nich wynika także z ustawy o dostępie do informacji
publicznej. Zapisuje się to w `legal_basis` każdego dokumentu. To opis przyjętej podstawy, nie opinia
prawna; wątpliwości (np. dokument opublikowany z załącznikami objętymi odrębną ochroną, jak mapy
podkładowe) zgłasza się właścicielowi przed dodaniem dokumentu. Geometria stref MPZP objęta umową
(`contract_required`) nie jest redystrybuowana.

## 10. Raport pokrycia

Po zamrożeniu:

```bash
python3 backend/scripts/mpzp_corpus_tools.py coverage --corpus <manifest>
```

Wynik (formaty, gminy nowe, województwa, strategie zakresu, drugi anotator) wkleja się do README
korpusu jako raport pokrycia, razem z opisem podziału rozwojowego i końcowego oraz z listą
dokumentów odrzuconych po przesiewie.

## 11. Ograniczenia

- Jakość anotacji zależy od ludzi; zgodność dwóch anotatorów na ≥ 20% próbek szacuje, nie dowodzi
  poprawności pozostałych próbek.
- Zbiór jest mały (≥ 20 próbek, ≥ 10 gmin); przedziały ufności metryk pozostają szerokie, a wyniki
  per gmina i per format są ilustracją, nie oceną statystyczną.
- Teksty ze skanów zależą od wersji OCR; anotacja z obrazu odnosi się do treści dokumentu, a wynik
  OCR jest oceniany jako błąd potoku.
- Dokumenty mogą zostać zmienione lub usunięte ze źródła po pobraniu; wiarygodność zapewnia
  SHA-256 pobranych bajtów i zapisany tekst stron, nie dostępność URL.
