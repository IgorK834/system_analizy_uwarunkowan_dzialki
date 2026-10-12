# ADR-021 — Wykrywanie sekcji strefy MPZP: zakresy symboli, wprowadzenia, powody braku

Status: przyjęta 2026-10-11 (AU-102, Task 22.2). Powiązane: [ADR-012](ADR-012-mpzp-llm-extraction.md) (parser v3, bloki stref),
[ADR-020](ADR-020-mpzp-acts-ru-channel-precedence-and-state.md) (akty i stan MPZP, AU-101/AU-103),
analiza przyczyn: [au-102-zone-section-causes.md](../evaluation/au-102-zone-section-causes.md).

## Kontekst

Dla działki w Krakowie (strefa `MW/U.4`) parser zwracał 0 parametrów w trybach `legacy` i `v3`, a użytkownik widział ogólne
`ZONE_SECTION_NOT_FOUND`. Badanie na 9 realnych uchwałach (41 stref) pokazało pięć niezależnych przyczyn (C1–C5 w raporcie): symbol nazwany
tylko zakresem (`1KDZ-11KDZ`), nierozpoznane wprowadzenie sekcji, zapisy opisowe poza słownikiem silnika, oraz dwa różne stany pod jednym kodem
błędu — symbol nieobecny w czytelnym tekście i skan bez warstwy tekstowej. Dodatkowo dowód opisowy wskazywał stronę cudzej sekcji.
Ograniczenie z zadania: poprawić rdzeń deterministyczny **bez dostrajania do 21 próbek BK-603** — próbki służą wyłącznie do wykrycia regresji.

## Decyzje

1. **Jeden rozwijacz zakresów symboli** (`app/shared/zone_symbol.py`: `find_zone_symbol_range_mentions`, `split_symbol_range_token`) używany przez oba
   tryby parsera i silnik ilości. Reguły: ten sam przedrostek i przyrostek, rosnące numery, końce włącznie, skrót „1-13MWU”, myślnik z odstępami i bez,
   łamanie wiersza po myślniku, limit 200 numerów, brak liter w przedrostku i przyrostku = nie zakres symboli (`10-12`). Wielkość liter nie rozstrzyga,
   chyba że jest czym: gdy jakiś zakres pasuje co do wielkości liter, zakresy różniące się tylko nią są pomijane (Pisz: rodziny `MNu` i `MNU`).
   Zakres w `legacy` jest kandydatem o niższej pewności (0,55, jak „sekcja wspólna”), nie literalnym dopasowaniem.
2. **Wprowadzenia sekcji** (`zone_scope.py`): „Ustalenia szczegółowe dla terenu(ów) …”; dla listy symboli liczy się OSTATNIE słowo kluczowe przed symbolem
   („oznaczone **symbolami** …”); zdanie wyznaczające kończące ustęp („Wyznacza się/Określa się … symbolem X.”) jest wprowadzeniem, a odwołanie („Ustala się … zgodnie
   z ustaleniami określonymi dla terenów 1Up i 2Up.”) — nie; punkt-liść wewnątrz wspólnego § („e) dla terenu 2Up dopuszcza się dachy płaskie;”) jest wyjątkiem w tej
   samej sekcji, a własną sekcją strefy (przez co wspólny § staje się klauzulą ogólną) jest dopiero punkt z podpunktami.
3. **Silnik opisowy `descriptive/2`:** etykiety i zwroty przeznaczenia (`przeznaczenie podstawowe terenu:`, `1) przeznaczenie: …`, `z podstawowym przeznaczeniem na …`),
   uprawnienia `możliwość lokalizacji …` i `dopuszcza się …`, tolerancja skrótów („ust.”, „pkt”) i wierszy przełamanych; wprowadzenie listy (`…:`) nie jest ustaleniem.
   Zwrot opisujący przeznaczenie nie trafia do strefy, gdy zdanie przed nim nazywa symbol INNEJ strefy (wykazy rodzajów terenów w przepisach ogólnych).
4. **Jawne powody braku sekcji** (`mpzp_parser_reasons.py`), zamiast jednego `ZONE_SECTION_NOT_FOUND`:
   `SCANNED_NO_OCR` (dokument wymaga OCR, a OCR nie został użyty albo nie dał tekstu — symbolu nie da się wyszukać; ma pierwszeństwo, bo brak symbolu w tekście
   skanu niczego nie dowodzi), `ZONE_NOT_IN_DOCUMENT` (czytelny tekst, symbol nie występuje wprost, w zakresie ani z pomyłką OCR). `ZONE_SECTION_NOT_FOUND` zostaje wyłącznie
   dla symbolu, który w tekście jest, a sekcji nie wyznaczono. Brak sekcji nigdy nie oznacza „brak ograniczeń” (komunikat mówi to wprost).
5. **Dowód ze swojego miejsca:** w `v3` ustalenia opisowe niosą stronę i zakres znaków z bloku strefy (jak wartości liczbowe); w `legacy` fragment jest lokalizowany najpierw wśród
   segmentów-kandydatów tej strefy, a dopiero potem w całym dokumencie.
6. **Pozyskiwanie dokumentu:** `mpzp_fetch` idzie za linkami stron BIP (strona dokumentu → karta załącznika → plik) przez najwyżej `MPZP_MAX_HTML_HOPS = 2` skoki, tylko w obrębie
   tej samej witryny, z pełną walidacją SSRF na każdym skoku; wybór linku „Tekst planu/uchwały” z pominięciem rysunku, prognozy, GML i innych załączników, na karcie — jedyny plik.
7. **Pomiar:** zamrożony zbiór `tests/fixtures/mpzp_live` (tekst stron, skróty SHA-256, adnotacje z cytatami i stronami; bez PDF-ów), ewaluator `scripts/evaluate_mpzp_live.py` z
   metryką zadania i metryką surową (parametr tylko z sekcji strefy), test spójności adnotacji i progów `tests/test_mpzp_live_corpus.py`.

## Rozważone alternatywy

- **Rozwijanie zakresów w każdym module osobno** — odrzucone: trzy kopie (lokalizator `legacy`, resolver `v3`, silnik ilości) już się rozjeżdżały (zakres z myślnikiem bez odstępów
  był jednym tokenem tylko w silniku ilości).
- **Fuzzy matching symboli** — odrzucone: ryzyko przypisania wartości sąsiedniej strefie; pomyłki OCR zostają tylko w istniejącej, oznaczonej ścieżce tolerancji.
- **Model językowy do wskazywania sekcji** — poza zakresem (rdzeń deterministyczny); ścieżka modelu i jej bramka są w ADR-012.
- **Pomijanie `ZONE_SECTION_NOT_FOUND` w UI** — odrzucone: użytkownik potrzebuje powodu (skan vs inny plan), bo wymagają innej reakcji.
- **Podwyższenie `ENGINE_VERSION` silnika ilości** — nie zrobiono: zmiana dotyczy rozpoznania zakresu stref w nagłówku (zasięg klauzuli), nie cech dowodu ani wartości; kalibrację
  powtórzono i artefakt odtwarza się z danych. Jeśli właściciel woli ścisłą politykę „każda zmiana silnika = nowa wersja”, to osobna, mała zmiana.

## Konsekwencje

- Wersje: parser `mpzp-parser/3.2-det` (blokowy `3.2-det+scope.2`), `descriptive/2`; `MPZP_RESULT_SCHEMA_VERSION` 3.0 (wspólny kontrakt AU-101–AU-103; w historii wersji jako 2.9 — zapis z
  `ZONE_SECTION_NOT_FOUND` nie jest trafieniem cache). Migawka BK-603 ponownie zamrożona po przeglądzie (same dodatki), kalibracja pewności powtórzona, złote odpowiedzi modelu odtworzone (identyczne).
- Nowe kody ostrzeżeń parsera: `ZONE_NOT_IN_DOCUMENT`, `SCANNED_NO_OCR` (pole `code` jest otwartym napisem, bez zmiany schematu).
- `legacy` zwraca więcej wartości (większe pokrycie), w tym wartości spoza sekcji (224 na 41 stref wg metryki surowej; oznaczane jako niejednoznaczne). Naprawa tej klasy to przełączenie na `v3`,
  które nadal blokuje bramka PV3-17/21 (`NOT_DECIDABLE`); ten ADR nie zmienia domyślnego trybu.
- Wynik pomiaru (100% / metryka surowa 100% w obu trybach) opisuje pokrycie przyczyn na znanym zbiorze, nie uogólnienie: zbiór nie jest ślepy, adnotacje przygotował asystent AI bez przeglądu człowieka.

## Następne kroki (decyzje właściciela)

1. Niezależny przegląd adnotacji `mpzp_live` przez człowieka (≥ 20% stref + parametry sporne) i zmiana `independent_human_review`.
2. Ślepy zestaw: kilka nowych uchwał (m.in. Warszawa — dziennik nie odpowiada) ocenionych raz bez zmiany kodu.
3. Luka leksykonu wielkości (np. „wysokość elewacji lub attyki budynku”, „… liczona do wysokości kalenicy”) jako osobne zadanie z ponowną kalibracją.
4. Decyzja o `ENGINE_VERSION` (j.w.).
