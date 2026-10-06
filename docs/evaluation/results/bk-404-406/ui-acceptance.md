# Ręczny odbiór UI BK-404–406 (29.09.2026)

Środowisko: produkcyjne obrazy z `docker compose build` (izolowany projekt
Compose `bk404`, PostGIS 16/3.4), osobna baza `dzialki_demo` (migracje
001–024) zasilona prawdziwym `run_pog_import` (`demo-import.json`, wydanie #1).
Backend `uvicorn` na `:8000`, frontend standalone (`node server.js`) na `:3001`,
przeglądarka Chromium (panel podglądu), okno ~720×860 CSS px. Widok mapy
ustawiano `jumpTo` na instancji MapLibre; kliknięcia, klawiatura i przyciski —
zdarzeniami przeglądarki. Liczba żądań: `performance.getEntriesByType('resource')`
i dziennik sieci panelu.

| # | Krok | Oczekiwanie | Wynik |
|---|---|---|---|
| 1 | Start strony, widok całej Polski | stan z metadanych wydania, plakietka projektu | „dane niepełne — Dane wydania w widoku są niepełne” (akty 1POG Sopotu i 2POG mają niepełne agregaty BK-405), „Wydanie pog-33b9f56a288c (#1) z dnia 28.09.2026, styl 2026.09.29-1”, plakietka „projekt / dane niewiążące”; brak przycisku „Ponów” (ponowienie nie naprawi danych importu) ✔ |
| 2 | Widok syntetycznego aktu 60/40 (poza zasięgiem aktów Sopotu) | `available` | „dostępna — Warstwa POG dostępna.” ✔ |
| 3 | Klik w punkt nakładania SU (obowiązuje) i SJ (projekt) w Sopocie | inspektor bez analizy, obie strefy + OUZ/OZS/OSDIS, bez duplikatów | MapLibre zwrócił SJ dwukrotnie (dwa kafle); inspektor: „W punkcie nakłada się 2 stref”, SJ: 0,6 / 0% / „brak wartości w danych” / 50%, plakietka „projekt / dane niewiążące”; SU: 0,9 / 90% / 4 m / 5%, „obowiązuje”; OUZ, OZS, OSDIS; wszystko „#1 (pog-33b9f56a288c)”; profile SU z nazwami (np. „KPT-MPZP-U — teren usług”) z `GET …/features/…`; **0 żądań `/analyze`** ✔ |
| 4 | Focus i `Escape` | focus na nagłówku, `Escape` zamyka i wraca na mapę | po kliknięciu `document.activeElement` = `H2 „Plan ogólny w punkcie 54,45626, 18,53845”` (widoczny obrys focus); `Escape` → panel zamknięty, focus na `canvas.maplibregl-canvas` ✔ |
| 5 | Klik w SU aktu 60/40 → „Struktura stref aktu i gminy” | wykres i tabela z tymi samymi liczbami | „Mianownik: 1,000 km² (granica aktu ze źródła)”, słupki `SW 60,0%`, `SU 40,0%`; tabela: SW 0,600 km² 60,0%, SU 0,400 km² 40,0%, suma 100,0% ✔; parametry SU bez wartości → „brak wartości w danych” (nie 0) ✔ |
| 6 | Edycja klawiaturą: klik „Wszystkie akty”, `↓` | jawny wybór, tylko `setFilter`, ten sam URL kafli | zaznaczone „Tylko akty obowiązujące” (focus na nim), filtr `["==",["get","legal_status"],"binding"]`, źródło nadal `…/releases/1/{z}/{x}/{y}.mvt` ✔ |
| 7 | Awaria kafli: zatrzymanie API, przesunięcie mapy na nowe kafle | `partial` z liczbą błędów, plakietka zostaje | 70 zdarzeń `error` (`AJAXError: Failed to fetch`) dla `pog-mvt-source` i WMS; „dane niepełne — Część kafli POG nie została wczytana (17).”, przycisk „Ponów wczytanie warstwy”, plakietka widoczna; WMS MPZP/POG: „Stan warstwy: dane niepełne” ✔ |
| 8 | „Ponów” przy wyłączonym API | `stale` z datą i wydaniem, dane zostają | „dane nieaktualne — Dane nieaktualne — pokazano ostatnie wczytane wydanie. … mapa nadal pokazuje wydanie #1 potwierdzone 29.09.2026, 16:59”; linia wydania „z dnia 28.09.2026 … ostatnio potwierdzone 29.09.2026, 16:59”; plakietka projektu widoczna; źródło i warstwy POG nadal na mapie ✔ |
| 9 | Start API, „Ponów” | powrót do stanu z metadanych | „dane niepełne — Dane wydania w widoku są niepełne.”, brak przycisku ponowienia, plakietka ✔ |
| 10 | Przy edycji „obowiązujące” klik w punkt nakładania | projekt ukryty filtrem | inspektor pokazuje tylko SU (SJ-projekt jest odfiltrowany), 0 żądań `/analyze` ✔ |
| 11 | „Analizuj działkę w tym punkcie” | dokładnie jedno `POST /analyze` z punktem kliknięcia | dziennik sieci: `OPTIONS /analyze` (preflight CORS) + **jedno** `POST /analyze` 200; w bazie jedna analiza `partial` działki `226401_1.0002.2/66`, której geometria zawiera punkt (18.53845, 54.45626) ✔ |

Żaden z oglądanych komunikatów nie zawierał frazy „brak planu” (tylko
zastrzeżenie „nie oznacza/nie potwierdza braku planu”).

Poprawki wprowadzone w trakcie odbioru (z testami):

1. Przycisk „Ponów” był pokazywany także dla niepełnych danych importu —
   teraz tylko dla przyczyn, które ponowienie może usunąć (awaria metadanych
   lub kafli, stale).
2. Punkt z samymi OUZ/OZS/OSDIS (bez strefy) nie mówił nic o strefie — dodano
   jawny komunikat o luce w danych wydania, zależny od stanu warstwy.
3. Przy wąskim oknie kontrolki mapy (z-index 6) przykrywały inspektor — kolumna
   wyników/inspektora ma z-index 7.
4. Kolumny listy parametrów w inspektorze i neutralny opis źródła danych.
