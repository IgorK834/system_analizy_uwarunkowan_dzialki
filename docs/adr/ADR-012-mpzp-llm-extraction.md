# ADR-012: Ekstrakcja parametrów MPZP modelem językowym (Gemini 3.8 Flash) — spike, budżet i wariant awaryjny

- Status: **Proponowany — pomiar na żywo wykonano 2026-10-02 (wynik mechaniczny: GO); decyzję zapisuje właściciel** (patrz „Stan wykonania”)
- Data: 2026-10-01
- Zakres: PV3-01 (Task 20.1); blokuje Task 20.10 (adapter) i Task 20.11 (prompt, schemat)
- Powiązane: ADR-001 (modularny monolit: port w `application`, adapter w `infrastructure`),
  ADR-004 (parametry MPZP i dowód wartości), `docs/evaluation/bk-601-603-verification.md`
  (recall parsera 0,25), Task 20.2 (zbiór końcowy), Task 20.3 (ewaluator wielosilnikowy),
  Task 20.15 (budżet i degradacja), Task 20.17 (bramka jakości)

## Kontekst

Obecny parser MPZP ma recall 0,25 na korpusie BK-603, a metryka źródła
(`source_consistent`, Task 20.3) pokazuje, że 24 z 75 poprawnych wartości nie da się
potwierdzić w bloku właściwej strefy (9 z innej strony, 8 z bloku innej strefy,
7 niejednoznacznych). Właściciel wybrał wariant C: deterministyczny rdzeń plus ekstrakcja
modelem językowym **z deterministyczną weryfikacją cytatu**. Wynik modelu nie jest źródłem
prawdy: trafia do wyniku wyłącznie po weryfikacji względem tekstu źródłowego i ma status
`ai_candidate` (istniejąca wartość `ReviewStatus` w `planning/domain/rules.py`), nigdy
`verified`.

W repozytorium nie ma integracji z modelem językowym. To ADR rozstrzyga, czy wskazany model
jest dostępny, tani i wystarczająco powtarzalny, zanim powstanie kod produkcyjny.

## Stan wykonania (uczciwie)

| Element Task 20.1 | Stan |
|---|---|
| 1. Lista modeli, identyfikator, metody, limity, wyjście strukturalne | **Wykonane 2026-10-02**: `models.list` zwraca `models/gemini-3.8-flash` (wejście 1 048 576, wyjście 65 536 tokenów; metody `generateContent`, `countTokens`, `createCachedContent`, `batchGenerateContent`); test dymny potwierdził przyjęcie `generateContent` + `responseJsonSchema` + `thinkingLevel` (`results/llm-spike/models.json`) |
| 2. Pomiar 10 bloków (opóźnienie, tokeny, koszt, schemat, powtarzalność) | **Wykonane 2026-10-02**: 30/30 wywołań udanych, schemat 30/30, cytaty 261/261, stabilne wartości w 9 z 10 bloków (niestabilny: B06 Łódź, układ 4), p95 8,3 s, koszt na analizę 0,020 USD (promocja) / 0,041 USD (od 2027). Tabela i progi w sekcji „Pomiar”; surowe dane w `results/llm-spike/measurements.json` |
| 3. Limity RPM/TPM/dzienne, region, SLA, warunki danych | **Częściowo**: warunki danych, ceny i kontekst z dokumentacji; limity per projekt dostawca podaje wyłącznie w AI Studio (nie w dokumentacji); region i SLA — brak deklaracji dla Gemini Developer API |
| 4. Wariant awaryjny i progi budżetowe | Wariant opisany; progi liczbowe są **formułą** wypełnianą z pomiaru (szacunek wstępny niżej) |
| 5. Decyzja, kryteria bramki, ograniczenia | Kryteria i ograniczenia zapisane; wynik mechaniczny progów: **GO**. **Decyzji właściciela nie wpisano** — wpisuje ją właściciel (progi są propozycją do potwierdzenia) |

Pomiar powstał; zgodnie z zależnością z issue Task 20.10 i 20.11 można zaczynać dopiero po wpisaniu decyzji przez właściciela. Pomiar to jeden bieg z jednego miejsca i jednej chwili na 10 blokach — nie gwarancja jakości (tę mierzy bramka z Task 20.17).

## Fakty zweryfikowane w dokumentacji dostawcy (2026-10-01)

Źródło: strony dokumentacji pobrane 2026-10-01 narzędziem pobierającym strony i
streszczającym je modelem pomocniczym. Wartości **wymagają potwierdzenia** poleceniem
`llm_spike.py models` (identyfikator, limity, metody) i przeglądu cennika przed budżetem.

| Fakt | Wartość | Źródło (strona z datą aktualizacji) |
|---|---|---|
| Identyfikator | `gemini-3.8-flash`, status Stable (jest też `gemini-3.7-flash`, `gemini-3.6-flash` Stable) | ai.google.dev/gemini-api/docs/models (2026-10-01) |
| Limit wejścia / wyjścia | 1 048 576 / 65 536 tokenów | ai.google.dev/gemini-api/docs/models/gemini-3.8-flash (aktualizacja: wrzesień 2026) |
| Możliwości | wyjście strukturalne (JSON Schema), myślenie `low`/`medium`/`high` (`minimal` zwraca błąd — **myślenia nie da się wyłączyć**), cache kontekstu, Batch API | tamże |
| Cena (warstwa płatna, standard, 1 mln tokenów) | wejście 0,75 USD, wyjście 3,75 USD (wyjście wlicza myślenie) **do 2026-12-31**; od 2027-01-01 wejście 1,50 USD, wyjście 7,50 USD | ai.google.dev/gemini-api/docs/pricing (2026-10-01) |
| Dane w warstwie płatnej | prompty i odpowiedzi nie służą do ulepszania produktów; logi trzymane tymczasowo wyłącznie do wykrywania nadużyć i zgodności prawnej | ai.google.dev/gemini-api/terms (obowiązują od 2026-03-23) |
| Dane w warstwie bezpłatnej | służą do ulepszania produktów, mogą być czytane przez ludzi; zakaz przesyłania danych wrażliwych | tamże |
| EOG/Szwajcaria/Wielka Brytania | warunki warstwy płatnej obowiązują dla wszystkich usług, także bezpłatnych | tamże |
| Lokalizacja przetwarzania | dane mogą być przechowywane lub buforowane przejściowo w dowolnym kraju, w którym dostawca ma obiekty; dla Gemini Developer API **brak deklaracji rezydencji danych** | tamże; strona „Available regions” (2026-04-28) wymienia tylko dostępność, w tym Polskę |
| Limity (RPM/TPM/RPD) | liczone **per projekt**, zależne od poziomu (Free, Tier 1–3 wg wydatków); liczby per model **nie są w dokumentacji** — widoczne w AI Studio (`aistudio.google.com/rate-limit`). Dla Batch API Gemini 3.8 Flash: 3 mln / 400 mln / 1 mld tokenów w kolejce (Tier 1/2/3) | ai.google.dev/gemini-api/docs/rate-limits (2026-09-02) |
| Powierzchnie REST | Interactions API (`POST /v1beta/interactions`, GA od czerwca 2026, „zalecane dla nowych projektów”, parametr `store` domyślnie `true`) oraz starsze `generateContent` („w pełni wspierane”): `generationConfig.responseMimeType`, `responseJsonSchema`/`responseSchema`, `thinkingConfig`, w odpowiedzi `usageMetadata` z `promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount` | ai.google.dev/gemini-api/docs/interactions, /api/generate-content |
| Wyjście strukturalne: zastrzeżenia | obsługiwany podzbiór JSON Schema; zbyt duże lub głęboko zagnieżdżone schematy mogą zostać odrzucone; składniowa poprawność nie gwarantuje poprawności wartości | ai.google.dev/gemini-api/docs/structured-output |
| SLA | dla Gemini Developer API nie znaleziono umowy SLA; dla *Gemini Online Inference na Vertex AI* publikowany jest cel dostępności 99,5% miesięcznie | wyniki wyszukiwania (cloud.google.com/vertex-ai/generative-ai/sla) — **niezweryfikowane dla `gemini-3.8-flash`** |

### Czego nie zweryfikowano

- (zweryfikowano 2026-10-02: `models.list` zwraca `gemini-3.8-flash` z `generateContent`, a żądanie z
  `responseJsonSchema`, `thinkingLevel` i `temperature: 0` jest przyjmowane);
- że temperatura 0 jest dla tego modelu zalecana lub w ogóle obsługiwana (strona modelu: „nie podano”);
- limitów RPM/TPM/dziennych naszego projektu, regionu przetwarzania i SLA dla naszej ścieżki;
- dostępności Vertex AI i regionów europejskich dla tego modelu.

## Decyzje (niezależne od wyniku pomiaru)

1. **Model:** `gemini-3.8-flash` (identyfikator wskazany przez właściciela i obecny w dokumentacji).
   Jeżeli `models.list` go nie zwróci, narzędzie kończy się kodem 3 i **nie wolno zakładać innej
   nazwy**: zapisać faktyczny identyfikator albo no-go z alternatywą.
2. **Transport:** REST przez istniejące `httpx`, **bez nowej zależności** (oficjalny SDK nie jest
   potrzebny: jedno żądanie JSON, adapter ma własną obsługę błędów i limitów). Spike używa
   `generateContent`, bo dokumentuje pola użycia tokenów. Wybór powierzchni dla Task 20.10 jest
   otwarty: Interactions API jest zalecane dla nowych projektów, ale domyślnie przechowuje
   interakcje po stronie dostawcy — jeżeli zostanie wybrane, `store` musi być jawnie `false`, a
   pola użycia tokenów trzeba zweryfikować (strona ich nie opisuje).
3. **Warstwa:** wyłącznie płatna (`Paid Services`). Warstwa bezpłatna jest wykluczona: dane służą
   w niej do ulepszania produktów. Klucz projektu rozliczeniowego jest jedyną dopuszczalną
   konfiguracją produkcyjną i trafia do aplikacji wyłącznie ze zmiennej środowiskowej
   `GEMINI_API_KEY` (`SecretStr`, brak w repozytorium, logach i artefaktach).
4. **Dane wysyłane:** wyłącznie dosłowny tekst bloku publicznego aktu planistycznego, symbole
   stref i stała instrukcja; **bez identyfikatora działki, użytkownika, adresu i współrzędnych**
   (Task 20.16). Akty mogą zawierać nazwiska osób pełniących funkcje publiczne (podpisy,
   nagłówki); bloki stref ich zwykle nie obejmują, a ekstrakcja ma je pomijać.
5. **Podstawa wykorzystania tekstów:** uchwały w sprawie MPZP są aktami prawa miejscowego, a akty
   normatywne nie są przedmiotem prawa autorskiego (art. 4 pkt 1 ustawy o prawie autorskim i
   prawach pokrewnych) i są jawne (BIP, dzienniki urzędowe). To nie jest opinia prawna; zgodność
   z warunkami dostawcy i RODO (przekazanie poza EOG) wymaga potwierdzenia przez właściciela.
6. **Koszt liczony wg ceny od 2027-01-01**, nie promocyjnej: cena do końca 2026 jest o połowę
   niższa i nie może wyznaczać budżetu.
7. **Wariant awaryjny, w kolejności:** (a) **wyłączenie ścieżki LLM** — `mpzp_llm_enabled=false`
   jest stanem domyślnym; wynik pochodzi wtedy z rdzenia deterministycznego (v3, a do czasu jego
   powstania z parsera `legacy`), bez utraty działania aplikacji; (b) **drugi model**:
   `gemini-3.7-flash` (ten sam cennik i status Stable) po ponownym pomiarze tym samym
   narzędziem (`--model`); (c) **Vertex AI** jako droga do umowy SLA i punktów końcowych
   regionalnych — wymaga osobnej weryfikacji dostępności modelu i regionu.
8. **Brak nowej zależności i brak kodu produkcyjnego** przed zamknięciem pomiaru.

## Pomiar (Task 20.1, pkt 2)

Narzędzie ręczne, poza CI i poza pytest (`backend/scripts/llm_spike.py`; `testpaths` obejmuje
tylko `tests/`, workflow go nie wywołuje, a samo narzędzie odmawia pracy, gdy ustawione jest `CI`
lub `GITHUB_ACTIONS`). Klucz tylko ze zmiennej środowiskowej; przed zapisem każdego artefaktu
sprawdzana jest nieobecność klucza; klucz wysyłany jest wyłącznie do hosta dostawcy (albo
`localhost` dla stubu).

```bash
export GEMINI_API_KEY=...   # klucz projektu z włączonym rozliczaniem; nie zapisywać w plikach
python3 backend/scripts/llm_spike.py prepare                       # offline: 10 bloków + SHA-256 wejść
python3 backend/scripts/llm_spike.py models                        # potwierdzenie identyfikatora, limitów, metod
python3 backend/scripts/llm_spike.py smoke --confirm-public-text   # jedno wywołanie: czy kształt żądania działa
python3 backend/scripts/llm_spike.py measure --confirm-public-text # 10 bloków × 3 wywołania, temperatura 0
python3 backend/scripts/llm_spike.py report --update-adr           # tabela + wynik mechaniczny do tego ADR
```

Bloki (po jednym z układów 1–6 z Task 20.6, dwa dla układu 1 i 2 oraz dwa rzeczywiste skany OCR,
w tym próbka ujemna): `B01`–`B10`, tekst wycinany deterministycznie z zamrożonego korpusu BK-603
(od kotwicy strefy do ostatniego cytatu + 300 znaków), bez udziału żadnego silnika. Wejścia z
SHA-256, liczbą znaków i gminą są w `docs/evaluation/results/llm-spike/inputs.json`:

| Blok | Układ | Gmina | Znaki | SHA-256 wejścia |
|---|---|---|---:|---|
| B01 | 1 osobny § na strefę | Szczytno | 2580 | `56caf1b7841b3155…` |
| B02 | 1 osobny § na strefę | Bielsko-Biała | 1351 | `11d1dd126eabb81c…` |
| B03 | 2 wspólny § dla listy symboli | Szczytno | 1974 | `9b1c681cbb190034…` |
| B04 | 2 wspólny § dla listy symboli | Białystok | 1333 | `e01ee9c2f1d1a215…` |
| B05 | 3 podpunkty „N) dla terenu X:” | Kraków | 559 | `bc75e88039f388ac…` |
| B06 | 4 wartości per symbol w akapicie | Łódź | 3169 | `bae3de54c510e52f…` |
| B07 | 5 klauzula ogólna per symbol | Raszków | 2470 | `4dbbc85b136ca366…` |
| B08 | 6 tabela | Legnica | 652 | `312288a6d128f81b…` |
| B09 | skan OCR (rzeczywisty) | Stare Miasto | 414 | `1bd50bf98bcbbbf9…` |
| B10 | skan OCR (rzeczywisty), próbka ujemna | Pisz | 4891 | `06a2aa49bfd7a00b…` |

Pełne SHA-256, skrót korpusu, skrót promptu (`spike-v0`) i skrót schematu są w `inputs.json`.
Prompt `spike-v0` jest promptem spike’u, a nie produkcyjnym promptem z Task 20.11.

<!-- spike-results:begin -->
_Pomiar z 2026-10-02T12:44:25.760538Z; model zażądany `gemini-3.8-flash`, zwrócony `gemini-3.8-flash`; powierzchnia API `generateContent`; temperatura 0.0, thinkingLevel `low`, 3 wywołania na blok. To pomiar jednego biegu, nie gwarancja._

Korpus `BK-603-mpzp-parser-evaluation-2026-09-30` (`corpus_sha256` `ca5a005efeff440f…`), prompt `spike-v0` (`797d16b38a0a7b6b…`), schemat `eb9ef8ffbd2266b5…`. Ceny: https://ai.google.dev/gemini-api/docs/pricing, pobrane 2026-10-01.

| Blok | Układ | Gmina | Znaki | SHA-256 wejścia | tokeny we/wy (śr.) | opóźnienie p50 [s] | schemat | cytaty | identyczny JSON | stabilne wartości |
|---|---|---|---:|---|---|---:|---|---|---|---|
| B01 | 1 osobny § na strefę | Szczytno | 2580 | `56caf1b7841b3155…` | 1315 / 1788 | 5.77 | 3/3 | 33/33 | nie | tak |
| B02 | 1 osobny § na strefę | Bielsko-Biała | 1351 | `11d1dd126eabb81c…` | 969 / 1464 | 5.28 | 3/3 | 27/27 | nie | tak |
| B03 | 2 wspólny § dla listy symboli | Szczytno | 1974 | `9b1c681cbb190034…` | 1119 / 2516 | 7.94 | 3/3 | 48/48 | nie | tak |
| B04 | 2 wspólny § dla listy symboli | Białystok | 1333 | `e01ee9c2f1d1a215…` | 954 / 2137 | 5.78 | 3/3 | 30/30 | nie | tak |
| B05 | 3 podpunkty „N) dla terenu X:” | Kraków | 559 | `bc75e88039f388ac…` | 703 / 946 | 4.51 | 3/3 | 18/18 | nie | tak |
| B06 | 4 wartości per symbol w akapicie | Łódź | 3169 | `bae3de54c510e52f…` | 1616 / 2088 | 6.71 | 3/3 | 30/30 | nie | nie |
| B07 | 5 klauzula ogólna per symbol | Raszków | 2470 | `4dbbc85b136ca366…` | 1282 / 2311 | 6.66 | 3/3 | 39/39 | nie | tak |
| B08 | 6 tabela | Legnica | 652 | `312288a6d128f81b…` | 697 / 1695 | 5.07 | 3/3 | 30/30 | nie | tak |
| B09 | skan OCR (rzeczywisty) | Stare Miasto | 414 | `1bd50bf98bcbbbf9…` | 624 / 595 | 2.54 | 3/3 | 6/6 | nie | tak |
| B10 | skan OCR (rzeczywisty), próbka ujemna | Pisz | 4891 | `06a2aa49bfd7a00b…` | 2212 / 286 | 1.75 | 3/3 | 0/0 | tak | tak |

| Wielkość | Wynik |
|---|---|
| wywołania udane / wszystkie | 30 / 30 (statusy HTTP: {"200": 30}) |
| zgodność ze schematem | 30 / 30 (1.000) |
| cytaty znalezione w tekście wejścia | 261 / 261 (1.000) |
| powtarzalność: bloki z identycznym JSON / identycznym tekstem / stabilnymi zweryfikowanymi wartościami | 1 / 1 / 9 z 10 kompletnych |
| opóźnienie p50 / p95 / max [s] | 5.42 / 8.30 / 8.38 |
| tokeny wejściowe: suma / max / średnia | 34473 / 2212 / 1149 |
| tokeny wyjściowe (w tym myślenie): suma / max / średnia | 47477 / 2767 / 1583 (myślenie łącznie: 0) |
| powody zakończenia | {"STOP": 30} |
| koszt na wywołanie, cena promocyjna (do 2026-12-31) | śr. 0.00680 USD, max 0.01109 USD |
| koszt na wywołanie, cena od 2027 | śr. 0.01359 USD, max 0.02218 USD |
| koszt na analizę (3 bloki, założenie): promocyjna / od 2027 | 0.02039 / 0.04078 USD |
| koszt na 1000 analiz: promocyjna / od 2027 | 20.39 / 40.78 USD |

Kryteria decyzji (progi proponowane, do potwierdzenia przez właściciela):

| Kryterium | Próg | Wynik | Spełnione |
|---|---|---|---|
| zgodność ze schematem | ≥ 0.95 | 1.000 | tak |
| cytaty znalezione w tekście | ≥ 0.9 | 1.000 | tak |
| bloki ze stabilnymi wartościami | ≥ 0.8 | 0.900 | tak |
| opóźnienie p95 [s] | ≤ 30.0 | 8.30 | tak |
| koszt na analizę, cena od 2027 [USD] | ≤ 0.05 | 0.04078 | tak |

Wynik mechaniczny: **GO**. Decyzję zapisuje właściciel w ADR-012.

Proponowane progi budżetowe wejściowe dla Task 20.15 (z pomiaru): max_input_tokens_per_request = 4000; max_requests_per_analysis = 6; max_input_tokens_per_analysis = 12000 (1.5 x the largest measured input, rounded up to 1000; requests cap = 2 x assumed blocks per analysis).

Zgodność z adnotacją jest informacyjna (10 bloków) i nie zastępuje bramki jakości z Task 20.17.
<!-- spike-results:end -->

### Co mierzy narzędzie i jak liczy

- **Opóźnienie:** czas ścienny jednego nieprzesyłanego strumieniowo `POST` (z siecią); p50/p95 metodą
  najbliższej rangi z 30 wywołań. To pomiar z jednego miejsca i jednej chwili.
- **Tokeny i koszt:** z `usageMetadata`; wyjście = tokeny kandydatów + tokeny myślenia. Koszt według
  cennika z tabeli wyżej, osobno cena promocyjna i od 2027. **Koszt na analizę** zakłada 3 bloki
  na analizę (założenie, nie obserwacja — liczba stref na działkę zależy od danych).
- **Zgodność ze schematem:** odpowiedź musi być poprawnym JSON zgodnym ze schematem narzędzia
  (walidacja `pydantic`, `extra="forbid"`), a nie tylko przyjęta przez API.
- **Cytaty:** `evidence_quote` musi występować w tekście wejścia (po normalizacji białych znaków) i
  zawierać `raw_value`; to ta sama bramka, którą wdroży Task 20.12.
- **Powtarzalność:** 3 wywołania na blok, temperatura 0; raportowane osobno: identyczny tekst,
  identyczny JSON i **stabilny zbiór zweryfikowanych wartości** (ten ostatni jest istotny dla jakości).
  Brak identyczności bajtowej przy stabilnych wartościach nie dyskwalifikuje modelu.
- Zgodność z adnotacją na 10 blokach jest **informacyjna** i nie zastępuje bramki z Task 20.17.

### Progi decyzji go/no-go (proponowane, do potwierdzenia przez właściciela)

| Kryterium | Próg |
|---|---|
| wywołania zgodne ze schematem | ≥ 0,95 |
| kandydaci z cytatem znalezionym w tekście | ≥ 0,90 |
| bloki ze stabilnym zbiorem zweryfikowanych wartości w 3 wywołaniach | ≥ 0,80 |
| opóźnienie p95 pojedynczego wywołania | ≤ 30 s |
| koszt na analizę (3 bloki, cena od 2027) | ≤ 0,05 USD (≤ 50 USD na 1000 analiz) |

Próg kosztu wynika ze **szacunku przed pomiarem**: dla około 2 tys. tokenów wejściowych (instrukcja
plus blok) i 1–3 tys. tokenów wyjściowych (JSON plus myślenie, którego nie da się wyłączyć) żądanie
kosztuje po 2027 roku rzędu 0,01–0,03 USD, więc analiza z trzema blokami 0,03–0,08 USD. Koszt jest
zdominowany przez tokeny wyjściowe (cena 5× wyższa). To **szacunek, nie pomiar**, i właściciel może
chcieć inny próg.

### Progi budżetowe wejściowe dla Task 20.15 (formuła, wartości z pomiaru)

- maksymalna liczba tokenów wejściowych na żądanie = 1,5 × największe zmierzone wejście, zaokrąglone
  w górę do 1000 (szacunek wstępny z `inputs.json`: największy blok ≈ 1,4 tys. tokenów + instrukcja
  ≈ 0,7 tys. → rząd 4 tys.);
- maksymalna liczba żądań na analizę = 2 × założona liczba bloków (6);
- maksymalna liczba tokenów wejściowych na analizę = limit na żądanie × założona liczba bloków;
- dzienny i miesięczny limit wydatków oraz limit żądań: do ustalenia z poziomu projektu w AI Studio
  (dokumentacja nie podaje liczb) i z progu kosztu wyżej.

## Kryteria bramki jakości do potwierdzenia w Task 20.17

Proponowane w issue Task 20.17; **do zamrożenia w tym ADR przed pierwszym biegiem na zbiorze
końcowym z Task 20.2** (zmiana po obejrzeniu wyników unieważnia bramkę):

- precision ≥ 0,95; recall ≥ 0,80;
- `source_consistent` ≥ 0,98 (metryka z Task 20.3);
- zero przyjętych wartości bez zweryfikowanego cytatu;
- błędy przypisania do strefy ≤ 2% znalezionych;
- ECE ≤ 0,10;
- koszt i dodatkowe opóźnienie p95 w progach powyżej;
- przegląd ręczny próbki ≥ 100 przyjętych wartości `ai_candidate` względem tekstu źródłowego.

Bramka mierzy wartości przyjęte po weryfikacji, nie surowe odpowiedzi modelu.

## Jawne ograniczenia

- Pomiar spike’u to 30 wywołań na 10 blokach z 9 gmin, jedna chwila i jedna sieć; nie jest
  gwarancją jakości, dostępności ani ceny. Dostawca może zmienić model pod tym samym
  identyfikatorem, ceny i limity; Task 20.19 przypina wersję i monitoruje zmiany.
- Bloki spike’u pochodzą z korpusu BK-603, którego anotacje sporządził asystent AI (przegląd
  człowieka oczekuje), a część dokumentów stała się rozwojowa. Spike nie ocenia jakości
  końcowej; to zadanie Task 20.17 na zbiorze z Task 20.2.
- Tekst z OCR i symbole przekręcone przez OCR (np. `AIMN` zamiast `A1MN`) obniżają cytowalność;
  próbki skanu w spike’u są dwie.
- Myślenie nie da się wyłączyć (`minimal` zwraca błąd), więc koszt i opóźnienie mają składnik
  zmienny; poziom `low` jest wartością domyślną spike’u.
- Region przetwarzania, rezydencja danych i SLA dla Gemini Developer API nie są deklarowane.
  Jeżeli aplikacja ma gwarancje kontraktowe lub dane w UE, droga to Vertex AI (do weryfikacji).
- Model może pominąć wartość (recall) lub zacytować niepoprawnie; dlatego wynik zawsze przechodzi
  weryfikację deterministyczną i ręczną, a brak odpowiedzi modelu nie zmienia działania aplikacji.

## Konsekwencje

- Task 20.10/20.11 mogą ruszyć dopiero po wpisaniu wyniku pomiaru i decyzji go/no-go; adapter
  ma domyślnie `mpzp_llm_enabled=false`, a `.env.example` tylko pusty placeholder klucza.
- Ewaluator (Task 20.3) ma gotowy kontrakt odtwarzania odpowiedzi (`--llm-replay`) o kluczu
  cache obejmującym dostawcę, model, wersje promptu i schematu, temperaturę oraz SHA-256 wysłanego
  tekstu; produkcyjny cache (Task 20.13) powinien używać tego samego klucza.
