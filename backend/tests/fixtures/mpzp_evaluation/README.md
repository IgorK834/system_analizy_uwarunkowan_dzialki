# Korpus ewaluacji parsera MPZP (BK-603)

`manifest.json` jest jedynym punktem wejścia: rejestr dokumentów, próbki z ręcznymi
anotacjami i zapis zamrożenia (`freeze.annotations_sha256`). Kontrolny zestaw z
celowo dodanym FP, FN i pomyłką strefy jest w `control.json` z ręcznie policzonymi
metrykami.

## Zawartość

- 21 próbek (7 rozwojowych, 14 końcowych), 17 dokumentów z 13 gmin;
- formaty: tekstowy PDF (14), tabela PDF (1), HTML (2), skan z OCR (2 rzeczywiste)
  i 2 skany symulowane; 14 próbek wielostrefowych;
- anotacje: strefa, parametr katalogu, operator, `raw_value`, `normalized_value`,
  jednostka, reguła normalizacji, cytat (`evidence`), kotwica strefy, strona źródła,
  niejednoznaczność (`ambiguity`), status (`required`/`acceptable`) i zakres
  (`zone_section`/`general_clause`).

Katalog parametrów (9): `max_building_height_m`, `min_intensity`, `max_intensity`,
`max_building_coverage_percent`, `min_biologically_active_percent`,
`roof_angle_min_deg`, `roof_angle_max_deg`, `max_storeys`, `setback_m`. Parametry
opisowe parsera (przeznaczenie, zakazy, geometria dachu, parkowanie) są poza
katalogiem, bo nie mają jednoznacznej wartości liczbowej do porównania.

## Podział rozwojowy / końcowy

- `development`: dokumenty, na których parser był rozwijany i regresowany
  (`../mpzp/`, `../mpzp_documents/`). Dla Krakowa anotowano strefy inne niż `MN.1`
  (nieobjęte asercjami regresji). Strefy Bielska-Białej, Legnicy, `6.8.MW/U` z Łodzi i
  `146 MN` ze Starego Miasta są także w asercjach regresji, a anotator znał część
  oczekiwanych wartości lub przyczyn luk przed anotacją; próbki mają notę o tym
  ograniczeniu niezależności.
- `final`: dokumenty pobrane dla tego badania (`documents/`), nieużywane wcześniej.
  Anotacje powstały z lektury tekstu **przed pierwszym uruchomieniem parsera na
  tym podziale**; parser nie jest stroiony w badaniu.
- Dwa dokumenty końcowe (`*_sim_ocr`) są **symulowanym skanem** tekstowego PDF
  (rasteryzacja 110 DPI, rozmycie 0,8, obrót 0,6°, JPEG 55, produkcyjny Tesseract
  5.5.0). Nie są prawdziwymi skanami i w raportach stanowią osobny format.

## Niezależność anotacji — ograniczenie

Anotacje sporządził asystent AI (`claude-sonnet-5-5`) z tekstu źródłowego; to jedna
osoba/agent, bez drugiego anotatora i bez zgodności międzyanotatorskiej.
`independent_human_review` ma wartość `pending`. Każdy cytat jest sprawdzany
programowo względem tekstu źródłowego (w kotwicy strefy), ale trafność interpretacji
semantycznej (np. które wartości są warunkowe) wymaga przeglądu człowieka.

## Źródła i wycofane pobrania

Dokumenty końcowe to publiczne akty prawa miejscowego i ich projekty; URL, rozmiar i
SHA-256 pobranych bajtów są w `documents[*].url|content_length|document_sha256`, a
SHA-256 zapisanego tekstu w `pages_sha256`. W repozytorium leży tylko tekst stron
(`pages.json`), metadane (`source.json`) i ewentualne tabele (`tables.json`), bez
surowych PDF.

Przy wyborze dokumentów pobrano także cztery, które nie weszły do korpusu:
projekt uchwały MPZP Puław (brak uchwalenia), wykaz planów Legnicy (nie jest aktem),
plany wsi Włosań i Konary (Mogilany; tekst, nie skan). Próby pobrania dzienników
Zakopanego i Szczecina zakończyły się błędem certyfikatu TLS; weryfikacja TLS nie
została wyłączona.

## Odtwarzanie migawek

Polecenia uruchamia się w obrazie backendu (jest w nim Tesseract), z katalogu `backend/`:

```bash
python -m scripts.build_mpzp_text_fixture --url <URL> --output-dir <katalog> --note "<opis>"
# fragment stron i OCR produkcyjnym Tesseractem (prawdziwy skan):
python -m scripts.build_mpzp_text_fixture --url <URL> --output-dir <katalog> --ocr --pages 1-4
# symulowany skan tekstowego PDF (jawnie oznaczony w source.json):
python -m scripts.build_mpzp_text_fixture --url <URL> --output-dir <katalog> --pages 3-4 --simulate-scan
```

`source.json` zawiera `document_sha256`, `content_length`, `extraction_method`,
`ocr_used`, `ocr_engine_version`, `source_page_numbers` i — dla skanów — `ocr_origin`.

## Uruchamianie ewaluacji

```bash
python3 backend/scripts/evaluate_mpzp_parser.py --mode offline \
  --output-dir docs/evaluation/results/parser --repeat 2          # silnik legacy (domyślny)
python3 backend/scripts/evaluate_mpzp_parser.py --engine legacy v3 \
  --output-dir docs/evaluation/results/parser --repeat 2          # porównanie sparowane legacy → v3
python3 backend/scripts/evaluate_mpzp_parser.py --engine legacy v3 hybrid \
  --llm-replay backend/tests/fixtures/mpzp_evaluation/llm_replay   # patrz „Silnik hybrid”: złote odpowiedzi nie pokrywają całego korpusu
python3 backend/scripts/evaluate_mpzp_parser.py --validate-only [--profile final-v2]   # sama walidacja korpusu
```

Polecenie nie łączy się z siecią. Przerywa pracę, gdy zmieniono anotację po
zamrożeniu (`annotations_sha256`), gdy cytat nie występuje w tekście źródłowym, gdy
strona, jednostka, operator lub normalizacja nie zgadzają się z anotacją, albo gdy
dwa kolejne biegi tego samego silnika dają różne wyniki merytoryczne.

### Kontrakt wyników (schema 2.0.0, PV3-03)

- **Katalog na silnik:** `<output-dir>/<silnik>/` (`legacy/`, `v3/`, `hybrid/`) i, przy co najmniej dwóch
  silnikach, `<output-dir>/comparison/`. Wcześniejszy układ płaski (`<output-dir>/report.md` itd.)
  został zastąpiony; wyniki BK-603 w starym układzie leżą w `parser/bk-603-flat-2026-09-30/` i mają
  te same metryki merytoryczne co `parser/legacy/` (sprawdzone wiersz po wierszu).
- **Rejestr silników:** `legacy` (produkcyjny parser w trybie domyślnym) i `v3` (ten sam parser w trybie
  blokowym: drzewo struktury dokumentu → resolver zakresu strefy → wartości z własnego dopasowania; PV3-05/06)
  działają; `hybrid` (PV3-14) jest dostępny z odtwarzania (`--llm-replay`) albo na żywo (`--live`) — bez jednego z
  nich kończy się kodem 2. Silnik zwraca neutralny `EngineResult` (wartość, strona, cytat, opcjonalnie zakres
  znaków, `review_status`, odrzucenia z nazwą bramki, bloki stref `ScopeBlock`, użycie tokenów, opóźnienia i
  kosztu).
- **Nowe metryki w `metrics.json` → `detection`:** `source_consistent` (licznik/mianownik: poprawne
  wartości, których strona i miejsce dopasowania leżą w bloku strefy z anotacji), `source_status_counts`,
  `zone_assignment_source_aware`, `strict_end_to_end`, `wrong_source_errors`; nowy typ błędu
  `wrong_source` (kategoria `assignment`) w `errors.json`; nowe wycinki `by_gmina` i `rejections`.
  Dotychczasowe pola (precision, recall, `zone_assignment_accuracy` wg wartości itd.) są bez zmian.
- **Definicja `source_consistent`:** wartość zwrócona dla strefy jest *poprawna co do wartości*, gdy
  równa się wartości z jej anotacji; jest *spójna ze źródłem*, gdy strona to strona cytatu anotacji, a
  miejsce dopasowania (`source_text` lub zakres znaków silnika) leży w bloku strefy: od kotwicy do
  ostatniego cytatu tej kotwicy plus 300 znaków, nie dalej niż następna kotwica. Statusy:
  `consistent`, `wrong_page`, `wrong_block`, `indeterminate` (identyczny tekst także poza blokiem),
  `unlocatable`, `not_applicable`, `not_checked`. Do licznika wchodzi tylko `consistent`. Dla skanu
  symulowanego (tekst anotowany ≠ tekst odczytany) porównuje się tylko stronę (`basis: page`).
- **Czas i koszt to obserwacje:** `observations.json` (czas ścienny, opóźnienie modelu, tokeny, koszt;
  `null` = silnik nie raportuje, nie zero) jest poza skrótem determinizmu (`determinism.json`).
- **Porównanie sparowane** (`comparison/`): te same pary (próbka, strefa, parametr); wyniki: wartość
  dokładna, wartość dokładna i z właściwego źródła, fałszywy alarm; test McNemara (dokładny),
  przedział bootstrap po próbkach (ziarno i liczba losowań w pliku), osobno ogółem, per format,
  per gmina i per podział; bramki odrzuceń z podziałem na „wartość była poprawna / błędna”.
- **Odtwarzanie modelu:** `--llm-replay DIR` czyta zapisane odpowiedzi pod kluczem cache (dostawca,
  model, wersja i skrót promptu, wersja schematu, temperatura, SHA-256 wysłanego tekstu); brak klucza w
  katalogu to błąd, nie połączenie sieciowe. `--live` wymaga flagi, zmiennej `GEMINI_API_KEY` i
  zarejestrowanego dostawcy (Task 20.10), nie działa w CI i zapisuje odpowiedzi do `--llm-replay DIR`.

### Zakres strefy (PV3-06, silnik `v3`)

Silnik, który zwraca bloki stref (`EngineResult.blocks`), jest oceniany także pod kątem tego, **gdzie** w
dokumencie leży tekst przypisany strefie; wyniki są w `metrics.json` → `scope`, w `scope_results.json` i w
sekcji „Zakres strefy” raportu. `legacy` bloków nie zwraca, więc ma `scope.reported = false`.

- **Zasięg zakresu** (`coverage`): odsetek anotowanych par, których cytat anotacji (strona i pozycja) leży w
  bloku wybranym dla strefy; osobno per układ strefy (strategie 1–6 i 0), per format i per zakres
  (`zone_section` / `general_clause`).
- **Zanieczyszczenie** (`contamination`): odsetek bloków `zone_section`, w których leży cytat innej strefy z
  tego samego dokumentu (klauzule ogólne i resztowe nie mogą trafić do `zone_section`).
- **Układy stref** (`scope_strategies.json`): etykiety 1–6 (osobny § na strefę; wspólny § dla listy/zakresu
  symboli; podpunkty „N) dla terenu X:”; wartości listą w akapicie; klauzula ogólna per symbol; tabela) oraz 0
  (skan bez struktury, zapas). To metadane opisowe do rozbicia wyników, **nie ground truth** i poza
  zamrożonymi anotacjami (`annotations_sha256`); etykiety nadał asystent AI, przegląd człowieka oczekuje.
  Korpus nowej wersji niesie je w polu `scope_strategy` strefy.
- **Ograniczenie:** resolver był rozwijany na tych samych 21 próbkach, więc wynik na korpusie BK-603 jest
  wynikiem rozwojowym, nie oceną uogólnienia; ocena niezależna wymaga zbioru końcowego z PV3-02.

### Zestawy kontrolne

`control.json` zawiera dwa zestawy z ręcznie policzonymi oczekiwaniami: BK-603 (FP, FN i pomyłka strefy;
metryki wartości i kalibracji) oraz `source_control` (PV3-03: ta sama wartość w dwóch strefach pobrana z
niewłaściwej strony lub z bloku sąsiedniej strefy; `source_consistent` 3/5, przypisanie wg wartości 5/6,
ze źródłem 3/6).

## Nowa wersja korpusu (PV3-02)

Status: **narzędzia, protokół i walidator gotowe; korpus nie został zbudowany.** Zbiór wymaga listy
dokumentów zatwierdzonej przez właściciela (`docs/evaluation/mpzp_corpus_v2_source_plan.md`) oraz
anotacji i drugiej anotacji wykonanych przez ludzi (`docs/evaluation/mpzp_annotation_protocol.md`).
Obecny podział „końcowy” jest rozwojowy 2 w nowej wersji. Bieżący manifest nie spełnia profilu
`final-v2` — `--validate-only --profile final-v2` wypisuje braki.

Zmiana anotacji wymaga nowej wersji korpusu (`corpus_id`), nowego skrótu w
`freeze` i ponownej oceny wszystkich wyników; nie wolno jej robić po obejrzeniu
wyników parsera.

### Silnik `hybrid`, bieg `--live` i przypięcie (PV3-14–19)

`hybrid` uruchamia produkcyjny tryb `hybrid` parsera: rdzeń `v3` + kandydaci modelu po bramkach G1–G8 (zawsze
`ai_candidate`). Brak zapisanej odpowiedzi przerywa bieg zamiast po cichu zwrócić wynik deterministyczny.

```bash
cd backend
# odtwarzanie (bez sieci): złote odpowiedzi to 13 bloków z 7 przypadków (NIE nagrania modelu; składa je
# build_llm_replay_fixtures.py), więc bieg hybrydy na CAŁYM korpusie przerywa się komunikatem „N model response(s) missing
# in the replay store” (kod 1) — pełny korpus wymaga nagrań z biegu --live; testy używają złotych odpowiedzi na tych 7 przypadkach
python3 scripts/evaluate_mpzp_parser.py --engine legacy v3 hybrid --llm-replay tests/fixtures/mpzp_evaluation/llm_replay
# bieg na żywo (RĘCZNIE, poza CI; wymaga zgody właściciela, GEMINI_API_KEY w środowisku, wysyła publiczny tekst, kosztuje)
python3 scripts/evaluate_mpzp_parser.py --engine v3 hybrid --live --model gemini-3.8-flash \
  --llm-replay <nowy katalog odpowiedzi> --output-dir ../docs/evaluation/results/<bieg>
python3 scripts/check_llm_pin.py record-evaluation --run-manifest ../docs/evaluation/results/<bieg>/hybrid/run_manifest.json
```

`run_manifest.json` biegu hybrydy zapisuje w `llm` tryb (`replay`/`live`), liczbę odtworzonych i nagranych odpowiedzi oraz
**model, wersję i skrót promptu i schematu** — na tej podstawie `check_llm_pin.py record-evaluation` zapisuje ewaluację
w `model_pin.json` (tylko dla DOKŁADNIE przypiętej pary). Zmiana promptu, schematu, modelu lub wyznaczania bloków zmienia
klucze odpowiedzi i unieważnia złote odpowiedzi (`build_llm_replay_fixtures.py --check`). Procedura zmiany modelu:
`docs/operations/mpzp-llm.md` §8.

### Ograniczenia zbioru ewaluacyjnego (nie traktować wyników jako gwarancji)

- Anotacje sporządził asystent AI; **brak drugiego anotatora i przeglądu człowieka** (`independent_human_review: pending`).
- Silnik `v3` (resolver zakresu, leksykon ilości) był rozwijany na tych samych 21 próbkach, więc jego liczby są **rozwojowe**;
  podział „final” (14 próbek) nie jest niezależnym zbiorem końcowym z Task 20.2 (ten nie istnieje; profil `final-v2` zgłasza 89
  problemów).
- 21 próbek z 9 gmin: różnorodność formatów i gmin jest ograniczona; skany: 2 rzeczywiste i 2 symulowane.
- Złote odpowiedzi modelu (`llm_replay/`) to „model idealny” złożony z anotacji — testują potok, nie jakość modelu.
  Jakość modelu na tym zbiorze **nie została zmierzona na żywo** (jedyny pomiar to spike PV3-01 na 10 blokach z promptem
  `spike-v0`: ADR-012).
- Bramka z Task 20.17: `NOT_DECIDABLE` (`docs/evaluation/results/parser-v3/gate_report.md`).
