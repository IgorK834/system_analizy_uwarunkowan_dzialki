# Zbiór `mpzp_live` — realne uchwały MPZP do pomiaru wykrywania sekcji stref (AU-102)

Zamrożony zbiór 9 publicznych uchwał rad gmin (aktów prawa miejscowego) z 7 gmin, pobranych z ich Biuletynów
Informacji Publicznej i dzienników urzędowych **z włączoną weryfikacją TLS**. W repozytorium **nie ma PDF-ów** (do
43 MB) — tylko tekst stron wyciągnięty produkcyjnym ekstraktorem (`pages.json`), metadane i skróty SHA-256
(`source.json`) oraz adnotacje (`manifest.json`). Zbiór jest odrębny od 21 próbek BK-603 (`mpzp_evaluation`); test
pilnuje, że żaden dokument się nie powtarza.

| Dokument | Gmina | Uchwała | Układ | Stron | Strefy | Oczekiwane parametry |
|---|---|---|---|---:|---:|---:|
| `krakow_stare_miasto_xii_131_11` | Kraków | XII/131/11 | tekst, karty opisowe | 99 | 6 | 11 |
| `krakow_kleparz_liii_1464_21` | Kraków | LIII/1464/21 | tekst | 95 | 4 | 12 |
| `krakow_bienczyce_lxx_1007_13` | Kraków | LXX/1007/13 | tekst | 18 | 5 | 15 |
| `gora_kalwaria_iv_30_2024` | Góra Kalwaria | IV/30/2024 | tekst | 16 | 5 | 15 |
| `pisz_xxi_232_20` | Pisz | XXI/232/20 | tekst, zakresy symboli | 38 | 8 | 25 |
| `bielsko_biala_liii_749_1998` | Bielsko-Biała | LIII/749/98 | **skan bez warstwy tekstowej** | 115 | 3 | 0 (powód: `SCANNED_NO_OCR`) |
| `legnica_xxiv_220_16` | Legnica | XXIV/220/16 | tekst | 12 | 4 | 18 |
| `czarny_bor_lxxxi_420_2024` | Czarny Bór | LXXXI/420/2024 | tekst | 13 | 2 | 5 |
| `ruciane_nida_lxxiv_686_2023` | Ruciane-Nida | LXXIV/686/2023 | tekst | 15 | 4 | 9 |

Razem 41 odrębnych symboli stref (w tym 7 oczekiwanych jako brak: 4× `ZONE_NOT_IN_DOCUMENT`, 3× `SCANNED_NO_OCR`) i
110 oczekiwanych parametrów z cytatem i stroną. Strony (`development` / `holdout`) są **historyczną etykietą**, nie
ślepą próbą — zob. „Ograniczenia” poniżej.

## Co jest w katalogu dokumentu

- `pages.json` — tekst stron (PDF → `pdfplumber`, jak w produkcji; bez OCR — skan zostaje skanem).
- `source.json` — adres, czas pobrania, SHA-256 bajtów PDF, rozmiar, metoda ekstrakcji, `needs_ocr`, podstawa prawna.
- `tables.json` — tabele wykryte w PDF (tylko gdy są).
- `manifest.json` (jeden na zbiór) — adnotacje: dla każdej strefy układ (`layout`), oczekiwany wynik
  (`parameters` / `ZONE_NOT_IN_DOCUMENT` / `SCANNED_NO_OCR`), strony i cytat początku sekcji (`section`) oraz oczekiwane
  parametry (`name`, `normalized_value`, `unit`, `page`, `quote`). Ta sama strefa może mieć kilka wpisów (kilka sekcji tego samego symbolu).

## Odtworzenie i pomiar

Z katalogu `backend` (obraz backendu ma zaufany łańcuch TLS):

```bash
python -m scripts.build_mpzp_live_corpus --only SLUG [--force]   # pobranie, ekstrakcja, skróty do manifestu
python -m scripts.evaluate_mpzp_live --verbose [--mode legacy v3] [--out wynik.json]
python -m pytest tests/test_mpzp_live_corpus.py                  # spójność adnotacji + progi + regresje per przyczyna
```

`tests/test_mpzp_live_corpus.py` sprawdza, że każdy cytat adnotacji istnieje na wskazanej stronie zamrożonego tekstu,
skróty pasują, symbole oczekiwane jako brak naprawdę nie występują, skany naprawdę nie mają tekstu, a metryki
(≥ 70% stref z parametrem albo jawnym powodem; metryka surowa; brak wartości spoza sekcji w trybie `v3`) nie spadają.

## Ograniczenia (uczciwie)

1. **Adnotacje przygotował asystent AI** na podstawie odczytu tekstu uchwał; **nie przejrzał ich niezależnie człowiek**
   (`independent_human_review: pending` w manifeście). Wynik pomiaru jest tak wiarygodny, jak adnotacje — przegląd
   człowieka (próbka ≥ 20% stref, wszystkie parametry sporne) jest otwartą decyzją właściciela.
2. **Zbiór nie jest ślepy.** Wszystkie dokumenty zostały przeczytane przy diagnozie; część poprawek wyprowadzono z dokumentów
   oznaczonych `holdout` (wprowadzenie „Ustalenia szczegółowe dla terenu…”, etykiety „przeznaczenie podstawowe terenu:”,
   zdanie „Wyznacza się teren … symbolem X.” jako wprowadzenie). Zbiór mierzy więc regresję i pokrycie przyczyn, a nie
   uogólnienie na nieznane uchwały; uogólnienie sprawdza (tylko w zakresie braku regresji) 21 próbek BK-603 niezależnych od tych poprawek.
3. **Adnotacja uzupełniona po pierwszym przebiegu:** dopisano drugie sekcje tego samego symbolu w Legnicy (`1MN` § 6,
   `1E` § 7 — przeznaczenie osobno od wskaźników), bo pierwotny wpis wskazywał tylko sekcję ze wskaźnikami i metryka
   „parametr z sekcji strefy” błędnie liczyła przeznaczenie jako spoza sekcji. Zmiana dotyczy poprawności adnotacji, nie oczekiwań.
4. **Brak Warszawy.** Zadanie wymienia 10 uchwał; dziennik `edziennik.mazowieckie.pl` nie odpowiada (`ReadTimeout` po 150 s,
   próby 2026-10-10 i 2026-10-11; TLS nie był wyłączany), więc zbiór ma 9 dokumentów. Dla gmin bez dostępnego planu
   z korpusu użyto innej uchwały tej samej gminy (zgodnie z decyzją właściciela) — kolumna `corpus_parcel` w manifeście
   mówi, której działki z korpusu referencyjnego dotyczy dana strefa (`null` = uchwała spoza korpusu).
5. Parametry, których brak wynika ze słownika wielkości (np. „wysokość elewacji lub attyki budynku” w Legnicy,
   „wysokość … liczona do kalenicy” w Czarnym Borze), pozostają w oczekiwaniach — to osobna luka leksykonu, poza zakresem AU-102.
