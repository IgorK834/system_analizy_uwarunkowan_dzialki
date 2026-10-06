# ADR-012: Ekstrakcja parametrów MPZP modelem językowym (Gemini 3.8 Flash) — spike, budżet i wariant awaryjny

- Status: **Zaakceptowany — decyzja właściciela GO z 2026-10-05** (pomiar na żywo 2026-10-02, wynik mechaniczny GO; patrz „Decyzja właściciela”)
- Data: 2026-10-01
- Zakres: PV3-01 (Task 20.1); blokuje Task 20.10 (adapter) i Task 20.11 (prompt, schemat). Aneksy: PV3-10–11, PV3-12–14,
  PV3-15–17 oraz **PV3-18–20** (oznaczenie w UI/PDF, monitoring i przypięcie wersji, dokumentacja i runbook)
- Powiązane: runbook [`docs/operations/mpzp-llm.md`](../operations/mpzp-llm.md), ADR-014 (dane i zagrożenia; w zadaniu
  nazwany „ADR-013”), ADR-001 (modularny monolit: port w `application`, adapter w `infrastructure`),
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
| 5. Decyzja, kryteria bramki, ograniczenia | Kryteria i ograniczenia zapisane; wynik mechaniczny progów: **GO**. **Decyzja właściciela: GO (2026-10-05)** z progami, warunkami i bramką jakości jak niżej — patrz „Decyzja właściciela” |

Pomiar powstał; zgodnie z zależnością z issue Task 20.10 i 20.11 zaczynać można było po wpisaniu decyzji przez właściciela — wpisano ją 2026-10-05 (sekcja „Decyzja właściciela”). Pomiar to jeden bieg z jednego miejsca i jednej chwili na 10 blokach — nie gwarancja jakości (tę mierzy bramka z Task 20.17).

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

## Decyzja właściciela

**2026-10-05, właściciel projektu (polecenie w rozmowie z asystentem): GO** — z zaproponowanymi progami
decyzji, warunkami wykorzystania i kryteriami bramki jakości, bez zmian. Dokładnie potwierdzono:

1. **GO dla ścieżki z modelem językowym** (wariant C: rdzeń deterministyczny + ekstrakcja modelem z
   deterministyczną weryfikacją cytatu), model `gemini-3.8-flash`, transport REST przez `httpx`
   (decyzje 1–2 poniżej), wyłącznie jako kandydaci do ręcznej weryfikacji.
2. **Progi decyzji** z sekcji „Progi decyzji go/no-go” (schemat ≥ 0,95; cytaty ≥ 0,90; stabilne wartości
   ≥ 0,80; p95 ≤ 30 s; koszt na analizę ≤ 0,05 USD wg ceny od 2027) — zmierzony wynik spełnia wszystkie.
3. **Warunki** z decyzji 3–5 poniżej: wyłącznie warstwa płatna (`GEMINI_API_KEY` tylko ze zmiennej
   środowiskowej), dane wysyłane ograniczone do tekstu publicznego aktu i symboli stref (bez identyfikatora
   działki, analizy, użytkownika, adresu i współrzędnych), podstawa wykorzystania tekstów aktów prawa
   miejscowego oraz zgodność z warunkami dostawcy i RODO (przekazanie poza EOG) — **potwierdzone przez
   właściciela jako jego oświadczenie; to nie jest opinia prawna ani weryfikacja wykonana przez asystenta**.
4. **Bramka jakości dla Task 20.17** z sekcji „Kryteria bramki jakości” — **zamrożona tym wpisem**:
   zmiana progów po obejrzeniu wyników na zbiorze końcowym unieważnia bramkę i wymaga nowego wpisu z datą
   i uzasadnieniem.

Skutki i zastrzeżenia tej decyzji:

- Decyzja **nie zmienia** stanu domyślnego: `mpzp_llm_enabled=false`. Włączenie ścieżki na produkcji wymaga
  jawnej konfiguracji (flaga, klucz) oraz osobnego wdrożenia potoku (Task 20.12–20.14); do czasu spełnienia
  bramki z Task 20.17 wartości z modelu nie trafiają do wyniku jako zweryfikowane.
- Progi budżetowe (Task 20.15) pozostają formułą z pomiaru (max 4000 tokenów wejściowych na żądanie, 6
  żądań i 12 000 tokenów wejściowych na analizę); limity wydatków per projekt ustawia się w AI Studio.
- Wynik pomiaru to jeden bieg z jednego miejsca i jednej chwili na 10 blokach z korpusu, którego
  anotacje nadał asystent AI (przegląd człowieka oczekuje); decyzja go/no-go nie zastępuje bramki
  z Task 20.17 na zbiorze końcowym z Task 20.2 (ten zbiór jeszcze nie istnieje).
- **Kolejność wykonania**: adapter (Task 20.10) i kontrakt (Task 20.11) powstały 2026-10-03 na polecenie
  właściciela, **przed** zapisaniem tej decyzji (ADR przewidywał odwrotną kolejność). Ścieżka była przez
  cały ten czas wyłączona domyślnie i nie wykonano żadnego wywołania modelu; ewentualne odrzucenie
  decyzji oznaczałoby wyłączenie ścieżki (wariant awaryjny 7a), bez utraty działania aplikacji.

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

### Progi decyzji go/no-go (potwierdzone przez właściciela 2026-10-05)

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

Proponowane w issue Task 20.17; **zamrożone w tym ADR 2026-10-05, przed pierwszym biegiem na zbiorze
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
  identyfikatorem, ceny i limity; przypięcie wersji i monitoring (PV3-19) opisuje aneks PV3-18–20 i runbook.
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

- Task 20.10/20.11 ruszyły po decyzji GO (2026-10-05; kolejność wykonania w sekcji „Decyzja właściciela”); adapter
  ma domyślnie `mpzp_llm_enabled=false`, a `.env.example` tylko pusty placeholder klucza.
- Ewaluator (Task 20.3) ma gotowy kontrakt odtwarzania odpowiedzi (`--llm-replay`) o kluczu
  cache obejmującym dostawcę, model, wersje promptu i schematu, temperaturę oraz SHA-256 wysłanego
  tekstu; produkcyjny cache (Task 20.13) powinien używać tego samego klucza.

## Aneks PV3-10 i PV3-11 (2026-10-05): adapter i kontrakt wyjścia

Wykonane 2026-10-03 (kod, testy offline), zapisane tu po decyzji właściciela. Szczegóły i liczby:
`docs/evaluation/pv3-10-11-verification.md`.

1. **Powierzchnia API: `generateContent`** (otwarty punkt decyzji 2). Wybrana, bo spike ją zmierzył i
   dokumentuje pola użycia tokenów; Interactions API nie jest używane (domyślnie przechowuje interakcje po
   stronie dostawcy). Transport: `httpx` (async), **bez nowej zależności** (brak oficjalnego SDK).
2. **Port i adapter (ADR-001).** Port `StructuredExtractionProvider` w `planning/application/ports.py`
   zna tylko „instrukcja + tekst + schemat JSON → obiekt JSON”. Adaptery w `planning/infrastructure/llm/`
   (`gemini_provider.py`, `fake_provider.py`) nie importują domeny MPZP — pilnuje tego test architektury.
   Host dostawcy jest stałą adaptera (nie ustawieniem), klucz tylko w nagłówku `x-goog-api-key`,
   `follow_redirects=False`, limit rozmiaru odpowiedzi, odpowiedź czytana strumieniowo.
3. **Błędy.** 400/401/403/404/413 nie są ponawiane; 408/429/5xx, timeout i błędy transportu — z
   wykładniczym opóźnieniem i losowaniem, `Retry-After` jako dolna granica (żądanie czekania powyżej 30 s
   kończy bez czekania). Wyłącznik awaryjny liczy tylko awarie dostępności (401/403/404/429/5xx/timeout/
   sieć); błędy treści (zły JSON, naruszenie schematu, ucięcie) i żądania go nie otwierają. Wyjątki i logi
   niosą ustalone kody i skróty, nigdy treść żądania, odpowiedzi ani klucza (komunikat błędu dostawcy jest
   pomijany — mógłby echować dane).
4. **Identyfikator modelu.** Zwrócony `modelVersion` różny od skonfigurowanego ustawia `model_mismatch`,
   jest logowany, a usługa ekstrakcji **nie używa takiej odpowiedzi** (jakość innego modelu nie jest
   zmierzona ani skalibrowana); operator ustawia `mpzp_llm_model` na faktyczny identyfikator po ponownym
   pomiarze. Brak pola w odpowiedzi nie jest niezgodnością.
5. **Kontrakt wyjścia** (`domain/extraction_contract.py`): kandydat ma `zone_symbol`, `parameter` (9
   parametrów katalogu), `operator`, `raw_value`, `value` (opcjonalna, ignorowana przy niezgodności z liczbą
   wyliczoną z `raw_value`), `unit` (jednostki katalogu), `applicability`, `conditions[]` (obiekty
   `{kind, label, quote}` jak w PV3-08), `evidence_quote`, `scope_quote`; jawne `not_found`. Schemat jest
   jedynym kontraktem: niezgodna odpowiedź jest odrzucana w całości z kodem, kandydat łamiący kontrakt
   (symbol spoza listy, operator niedozwolony dla parametru, `raw_value` poza cytatem…) — pojedynczo z kodem.
   Kontrakt nie ma stanu „zweryfikowany”; kandydat to `ai_candidate`.
6. **Instrukcja** jest plikiem `domain/prompts/mpzp_extraction_v1.md` (decyzja „plik, nie stała”: czytelny
   przegląd zmian). `PROMPT_VERSION = mpzp-extraction/1`, `SCHEMA_VERSION = mpzp-extraction-schema/1`;
   skróty SHA-256 instrukcji (z szablonem wiadomości) i schematu są przypięte w testach i wchodzą do
   provenance. Instrukcja zawiera definicje 9 parametrów z jednostkami, semantykę operatorów,
   kontrprzykłady (wysokość parteru ≠ wysokość zabudowy) i listę wskaźników ustawowych jako kontekst
   (tylko parametry katalogu są zwracane).
7. **Klucz cache = klucz odtwarzania ewaluatora** (dostawca, model, wersja i skrót instrukcji, wersja
   schematu, temperatura, skrót wiadomości z danymi), więc zmiana instrukcji, schematu, modelu albo tekstu
   unieważnia zapisane odpowiedzi. Produkcyjny cache (Task 20.13) ma używać tego samego klucza.
8. **Duże bloki:** limit 6000 znaków na żądanie, podział na granicach punktów listy/akapitów/zdań,
   nakładka kontekstu 400 znaków i linia otwierająca blok w kolejnych częściach, deduplikacja kandydatów po
   (symbol, parametr, operator, surowa wartość, położenie cytatu), najwyżej 6 żądań na blok (blok większy
   jest pomijany z kodem `block_too_large`, bez wywołań).
9. **Złote odpowiedzi** (`tests/fixtures/mpzp_evaluation/llm_replay/`, 13 plików) składa z adnotacji
   korpusu `scripts/build_llm_replay_fixtures.py`; **nie są nagraniami modelu**. Prawdziwe nagrania (ręcznie,
   poza CI) dostaną ten sam klucz.
10. **Czego to nie obejmuje:** weryfikacja cytatów i przyjęcie wartości do wyniku (Task 20.12), cache
    (20.13), potok hybrydowy i rejestracja `register_live_provider` w ewaluatorze (20.14), budżet i
    degradacja (20.15), ochrona danych w żądaniach poza kontraktem tekstu bloku (20.16), bramka jakości
    (20.17). Ścieżka nie jest podłączona do analizy, więc `MPZP_RESULT_SCHEMA_VERSION` nie rośnie.

## Aneks PV3-12, PV3-13 i PV3-14 (2026-10-05): bramki, cache i tryby parsera

Wykonane 2026-10-05 (kod i testy offline, bez wywołań na żywo). Liczby i mapowanie kryteriów:
`docs/evaluation/pv3-12-14-verification.md`.

1. **Jedyna droga wartości z modelu do wyniku** to weryfikator `planning/domain/candidate_verifier.py`
   (domena, bez sieci i ustawień). Bramki w stałej kolejności: G1 katalog, G2 operator, G3 cytat w bloku
   (po normalizacji białych znaków; granice wyrazów; termin parametru w cytacie albo do 400 znaków przed nim;
   cytat bez znamion polecenia/JSON; cytaty warunków w bloku; dla OCR tolerancja edycyjna min(6, 8%) przy
   cytacie ≥ 24 znaki, bez zmiany cyfr, z flagą `quote_ocr_fuzzy` i karą ×0,8), G4 `raw_value` w cytacie i w
   dopasowanym tekście bloku, bez uciętych liczb, G5 przeliczenie tą samą normalizacją co rdzeń
   (`normalize_quantity`) — liczba modelu nie jest wymagana, a różna od wyliczonej odrzuca kandydata;
   `manual` tylko dla zapisów z flagą artefaktu, G6 `validate_planning_rule` (procent 0–100, dodatnie
   wysokości i intensywność, min ≤ max także między kandydatami), G7 zakres (symbol w `scope_quote` leżącym w
   bloku albo blok o `scope_confidence` ≥ 0,6; inaczej `applicability=unresolved` i brak przypisania; cytat
   nazywający tylko inną strefę — odrzucony), G8 deduplikacja. Strona i zakres znaków pochodzą wyłącznie z
   dopasowania w bloku. Każde odrzucenie ma bramkę i kod, liczniki per bramka trafiają do metryk procesu.
2. **Status.** Przyjęty kandydat: `review_status=ai_candidate`, `extraction_method=llm_verified`, zawsze
   ręczna weryfikacja, pewność z modelu PV3-09 z `origin=llm` (poniżej pasma `high`). Gwarancja na trzech
   poziomach: konstruktor `AcceptedCandidate`, `validate_planning_rule` i więzy bazy
   `ck_mpzp_parameters_llm_candidate`. Wartość `ai_candidate` nie wypełnia płaskich pól strefy API.
3. **Cache i provenance (PV3-13).** Tabela `mpzp_llm_extractions` (migracja **029** po faktycznym head `028`;
   issue mówi „027”, ale numery 027/028 zajęły PV3-04/PV3-08): klucz bloku = SHA-256(`document_sha256`,
   `block_sha256`, `prompt_version`, `schema_version`, `model_id`, `params_hash`), `params_hash` obejmuje
   dostawcę, skróty instrukcji i schematu, temperaturę, limity podziału, `thinking_level`, symbole i ścieżkę
   bloku. Zapis zawiera wyłącznie wyjście modelu i skróty wejścia (bez treści żądania i identyfikatorów
   działki, analizy, użytkownika), tokeny, opóźnienie, koszt szacowany wg ceny od 2027 i status
   `ok`/`rejected_schema`/`error`. Tylko `ok` w retencji jest trafieniem; trafienie przechodzi przez ten sam
   kontrakt i te same bramki co odpowiedź świeża. Retencja `MPZP_LLM_CACHE_RETENTION_DAYS` (domyślnie 180
   dni), czyszczenie `python -m app.modules.planning purge-llm-cache`. Evidence parametru niesie `model_id`,
   `prompt_version` i `response_sha256` (skrót zapisu w tabeli). Odczyt zapisanej analizy nie tworzy
   adaptera (snapshot jest źródłem prawdy).
4. **Tryby (PV3-14).** `MPZP_PARSER_MODE`: `legacy` (domyślny, wynik bajtowo jak przed zmianą — test na pliku
   zamrożonym przed zmianą), `v3` (bloki stref), `hybrid_shadow` (odpowiedź i zapis = `v3`; model dla
   wszystkich par w zadaniu w tle, różnice w logu `app.mpzp_llm` i licznikach), `hybrid` (`v3` + kandydaci
   modelu tylko dla par bez wartości deterministycznej, z konfliktem albo z zakresem < 0,6). Scalanie:
   wartość deterministyczna ma pierwszeństwo, zgodność nie dodaje kopii, rozbieżność zostawia obie z ręczną
   weryfikacją. Niedostępność modelu, timeout, wyczerpany budżet, brak konfiguracji albo błąd potoku:
   wynik deterministyczny, ostrzeżenie `MPZP_LLM_UNAVAILABLE` z kodem przyczyny i status `partial` (także
   analizy). Orkiestrator i wznowienie po ręcznym symbolu używają tego samego potoku (wznowienie — na kopii
   przypiętej, bez pobrania). Budżet: jeden licznik na analizę (6 żądań, 12 000 szacowanych tokenów
   wejścia — progi wejściowe z „Pomiaru”; dopracowanie w Task 20.15). Sygnatura cache analizy zawiera tryb,
   wersję parsera oraz — w trybach z modelem — wersje promptu i schematu, model i stan flagi;
   `MPZP_RESULT_SCHEMA_VERSION` się nie zmienia (jednorazowa zmiana sygnatur unieważnia stare wpisy cache).
5. **Ewaluator.** Silnik `hybrid` jest zarejestrowany: odtwarzanie przez bramę ewaluatora pod tym samym
   kluczem co złote odpowiedzi, `--live` ręcznie przez produkcyjny adapter (rejestrowany tylko dla
   `--engine hybrid --live`); brak zapisanej odpowiedzi przerywa przebieg zamiast mieszać wynik
   deterministyczny.
6. **Czego to nie obejmuje:** pomiaru jakości na żywo i bramki (Task 20.17), budżetu dziennego/miesięcznego
   i wyłącznika między analizami (20.15), dalszej ochrony przed prompt injection poza bramkami G3 (20.16),
   monitoringu i przypięcia wersji modelu (20.19), oznaczenia w UI i PDF (20.18). Domyślny tryb pozostaje
   `legacy` do czasu spełnienia bramki jakości.

## Aneks PV3-15, PV3-16 i PV3-17 (2026-10-05): limity, dane, bramka

Szczegóły i mapowanie kryteriów: `docs/evaluation/pv3-15-17-verification.md`; obsługa danych — ADR-014.

1. **Limity (PV3-15).** W analizie (warstwa application): 6 żądań, 12 000 tokenów wejścia na analizę,
   12 000 na dokument, 4000 na żądanie (próg „max_input_tokens_per_request” z „Pomiaru”), termin od startu
   analizy (90 s) propagowany do każdego żądania i budżet czasu ścieżki modelu 30 s (próg p95 z tego ADR).
   Między analizami (adapter `infrastructure/llm/budget.py`): częstotliwość 60/min i współbieżność 4 w
   procesie, twarde limity doby i miesiąca w UTC — 2 mln tokenów / 5 USD na dobę, 40 mln / 100 USD na miesiąc
   (wyprowadzone z progu 0,05 USD na analizę × 100 analiz dziennie; **zaakceptowane przez właściciela 2026-10-05**, pkt 5) — w
   rejestrze `mpzp_llm_usage` (migracja 030) z rezerwacją najgorszego przypadku pod blokadą doradczą; brak
   rejestru = brak wywołania. Wyłącznik awaryjny jest wspólny dla procesu.
2. **Degradacja.** Każda awaria, przekroczenie limitu lub terminu i błąd potoku: wynik deterministyczny,
   `MPZP_LLM_UNAVAILABLE` z kodem i `partial`; kandydaci odrzuceni przez bramki: `MPZP_LLM_CANDIDATES_REJECTED`
   z licznikami per bramka i `partial`. Metryki: wywołania, odrzucenia per bramka i kod, degradacje, tokeny
   i koszt szacowany (mikro-USD).
3. **Dane i bezpieczeństwo (PV3-16)** — ADR-014: lista dozwolonych pól żądania egzekwowana w kontrakcie,
   redakcja sekretów w logach, skaner sekretów w CI, korpus prompt injection, kill switch plikowy.
4. **Bramka (PV3-17).** Kryteria z sekcji „Kryteria bramki jakości” pozostają **bez zmian** (zamrożone
   2026-10-05); `scripts/mpzp_quality_gate.py` ocenia je z licznikami i daje `GO` / `NO_GO` /
   `NOT_DECIDABLE`. Stan 2026-10-05: **`NOT_DECIDABLE`** — brak zbioru końcowego z Task 20.2, biegu hybrydy
   na żywo, badania zmienności i przeglądu ręcznego ≥ 100 wartości przez człowieka
   (`docs/evaluation/results/parser-v3/gate_report.md`). Decyzji go/no-go dla włączenia ścieżki **nie
   podjęto**; domyślny tryb pozostaje `legacy`.
5. **Decyzja właściciela 2026-10-05 (polecenie w rozmowie z asystentem):** limity dobowe i miesięczne
   ścieżki modelu są **zaakceptowane** w proponowanej postaci: doba 2 000 000 tokenów (wejście + wyjście) i
   5 USD, miesiąc 40 000 000 tokenów i 100 USD (koszt szacowany wg ceny od 2027-01-01, granice doby i
   miesiąca w UTC, twardy stop). Zmiana tych wartości wymaga nowego wpisu z datą. Limity wydatków w AI Studio
   (poziom projektu dostawcy) są niezależnym, dodatkowym zabezpieczeniem i nie zastępują tych limitów.

## Aneks PV3-18, PV3-19 i PV3-20 (2026-10-05): oznaczenie w UI i PDF, monitoring, przypięcie, dokumentacja

Wykonane 2026-10-05 (kod, testy offline, próba runbooka, ręczny odbiór UI na lokalnym stubie). **Nic nie zmierzono
na żywo**: nie wykonano biegu `--live` na zbiorze złotym, więc para (`gemini-3.8-flash`, `mpzp-extraction/1`) nie ma
zapisu ponownej ewaluacji. Mapowanie kryteriów i polecenia: `docs/evaluation/pv3-18-20-verification.md`.

1. **Oznaczenie odczytu automatycznego (PV3-18).** Wartość jest odczytem modelu, gdy ma status `ai_candidate` albo
   metodę `llm_verified` (jedno rozpoznanie dla API, PDF i pakietu: `app/shared/model_reading.py`; w UI
   `frontend/lib/mpzpProvenance.ts`). Wartość deterministyczna **nigdy** nie jest tak oznaczana — nie decyduje o tym
   pewność, flaga weryfikacji ani metoda OCR. Marker: „odczyt automatyczny (model językowy), zweryfikowany z cytatem —
   wymaga potwierdzenia”; nota: „Odczyt automatyczny nie jest interpretacją prawną…”. Test backendu pilnuje, żeby
   tekst w `mpzpProvenance.ts` i w raporcie był ten sam.
   - **UI** (`MpzpZoneCard`): marker przy parametrze (z tekstem dla czytnika ekranu), cytat, strona, warunki wartości,
     linia provenance (model, wersja instrukcji, skrót odpowiedzi), nota na poziomie strefy, filtr „Tylko do ręcznej
     weryfikacji (N)” (`aria-pressed`, licznik jako komunikat `role="status"`), tabela jako nazwany, przewijalny region
     dostępny z klawiatury. **Brak danych (`null`) to „brak danych”**, różny od `0` i od braku ograniczenia.
   - **PDF**: nowy rodzaj ustaleń „odczyt automatyczny” (`model_reading`) w legendzie; tabela 3.2 pokazuje kandydata modelu
     w osobnej, oznaczonej linii (obok wartości deterministycznej, nigdy zamiast niej; bez wartości deterministycznej
     wiersz mówi „nie ustalono deterministycznie”); tabela evidence 3.3 ma marker, `model: <id>; wersja instrukcji: <wersja>`
     i pełny SHA-256 odpowiedzi; ograniczenia i flagi weryfikacji zawierają zastrzeżenie o braku interpretacji prawnej.
   - **Pakiet audytowy** (`audit-exporter/1.2.0`): blok `model_provenance` w `analysis.json` (model, wersja instrukcji, skrót
     odpowiedzi, strona, skrót dokumentu, ostrzeżenia `MPZP_LLM_*` także dla niepełnego wyniku). **Z odpowiedzi modelu
     pakiet niesie tylko skrót i zweryfikowany cytat**; treść odpowiedzi nie jest dołączana (`model_responses_included:
     false`). Cytat jest dosłownym fragmentem uchwały, więc podlega polu `redistribution` źródła strefy: trafia do pakietu
     tylko przy `allowed`; przy `derived_only`, `forbidden`, `unconfirmed` i źródle spoza katalogu `quote`,
     `evidence_text`, `raw_value` i cytaty warunków są `null`, a zostaje skrót, powód i wpis w `redactions`.
2. **Monitoring (PV3-19).** Liczniki procesu (`llm_metrics`): wywołania, opóźnienie (histogram skumulowany, suma,
   maksimum), tokeny, koszt szacowany, odrzucenia per bramka G1–G8 i kod, trafienia/chybienia cache, analizy z modelem i
   degradacje; sumy w oknie czasowym dają odsetki (odrzuceń G1–G7, degradacji, trafień cache) dopiero od minimalnej próby.
   `GET /health` zawiera `components.llm` ∈ {`ok`, `degraded`, `disabled`} — **nigdy `failed`** — i nie zmienia `status`
   usługi ani gotowości (`/health/ready` sprawdza tylko bazę); `GET /health/llm` (klucz administracyjny) podaje
   szczegóły. Ocena nie wywołuje dostawcy i nie zgłasza wyjątków. Logi i odpowiedzi nie zawierają treści żądań ani klucza.
   **Progi alarmów — propozycja, nieskalibrowana na ruchu (którego nie było):** odrzucenia ≥ 30% (≥ 20 kandydatów
   w oknie 1 h), degradacja ≥ 20% (≥ 10 analiz), koszt doby ≥ 4 USD (80% limitu twardego 5 USD), dryf ≥ 0,20; zmiana
   wymaga nowego wpisu z datą. Alarmy są **wskaźnikami w `/health`**, nie systemem powiadomień (projekt go nie ma).
3. **Przypięcie wersji (PV3-19).** `model_pin.json` (`infrastructure/llm`) zapisuje model, wersję i skrót promptu, wersję i
   skrót schematu oraz `thinking_level`; kontrola: w czasie działania (`MPZP_LLM_ENFORCE_PIN`, domyślnie włączone:
   rozbieżność → `pin_mismatch`, adapter nie powstaje, analiza zostaje deterministyczna z ostrzeżeniem), w CI
   (`tests/test_llm_pin.py` porównuje przypięcie z ustawieniami, kodem, Compose, `.env.example`, złotymi odpowiedziami
   i dziennikiem poniżej) oraz skryptem `backend/scripts/check_llm_pin.py`. **Zmiana modelu, promptu albo schematu bez
   zapisu ponownej ewaluacji jest wykrywana.** Ponowna ewaluacja = bieg `evaluate_mpzp_parser.py --engine hybrid --live
   --model <id>` (ewaluator zapisuje w manifeście model, wersję i skrót promptu i schematu), potem
   `check_llm_pin.py record-evaluation` i wiersz w dzienniku zmian niżej. Stan: ewaluacja **oczekuje**
   (`check_llm_pin.py check --require-evaluation` kończy się kodem 1) — świadoma blokada włączenia produkcyjnego.
4. **Kontrola dryfu (PV3-19).** `backend/scripts/check_llm_drift.py` przepuszcza 13 bloków kanarkowych przez ten sam potok z
   zamrożonym odtworzeniem i z dostawcą na żywo i porównuje przyjęte wartości; alarm przy rozbieżności ≥ 0,20;
   awaria dostawcy to „nierozstrzygnięte”, nie alarm. Ręcznie albo z harmonogramu, poza CI; stan trafia do `/health`
   (`drift_alarm`). Zamrożone odtworzenie to złota odpowiedź z adnotacji (nie nagranie modelu).
5. **Dokumentacja (PV3-20).** Runbook `docs/operations/mpzp-llm.md` (włączanie, wyłączanie, limity, alarmy, dryf, rotacja
   klucza, zmiana modelu) z wynikiem próby: kontener (PostGIS, `docker compose up -d`) oraz proces z prawdziwą kompozycją
   i adapterem (`scripts/rehearse_llm_runbook.py`, 14/14 kroków). **Numeracja:** zadanie nazywa dokument o danych
   „ADR-013-llm-data-handling”, ale numer 013 zajął ADR silnika ilości, więc dokument to ADR-014 (nie przenumerowano
   — odesłania w kodzie i dokumentach). Dostawcy nie dopisano do `catalog.yaml` (ADR-014, pkt 5: przetwarzający, nie źródło).
6. **Ograniczenia.** Brak biegu `--live` na zbiorze złotym i brak ruchu produkcyjnego — progi alarmów i dryfu są
   propozycją, a nie zmierzoną normą. Zbiór ewaluacyjny BK-603 ma anotacje asystenta AI (bez drugiego anotatora i
   niezależnego przeglądu człowieka), podział rozwojowy/końcowy jest rozwojowy dla silnika, zbiór końcowy z Task 20.2 nie
   istnieje; bramka z Task 20.17: `NOT_DECIDABLE`. Ręczny odbiór UI wykonano na lokalnym stubie API (nie na danych
   rzeczywistych); automatyzacja w przeglądarce (axe, rzeczywisty czytnik ekranu) należy do BK-701.

## Aneks PV3-21 (2026-10-05): jeden silnik, regresja, bramka przełączenia i wycofanie

Mapowanie kryteriów i polecenia: `docs/evaluation/pv3-21-verification.md`.

1. **Domyślny tryb NIE został przełączony.** Przesłanka zadania — decyzja `GO` z Task 20.17 i okres cienia bez
   regresji — nie jest spełniona (bramka `NOT_DECIDABLE`, brak biegu `--live`, brak ruchu w `hybrid_shadow`). Zamiast
   przełączenia wprowadzono **bramkę przełączenia**: zapis przesłanek `backend/app/core/mpzp_parser_rollout.json`
   (decyzja bramki ze skrótem raportu, progi okresu cienia, raport cienia, decyzja właściciela, tryb wycofania,
   zasada utrzymania `legacy`) oceniany przez `scripts/check_parser_default_switch.py`; test CI wymaga zgodności
   rekordu z domyślnym trybem w ustawieniach i oceny `READY` dla każdego odejścia od `legacy`. Raport okresu cienia
   liczy `scripts/mpzp_shadow_report.py` z logu `app.mpzp_llm` (liczniki procesu zerują się przy restarcie).
2. **Progi okresu cienia — PROPOZYCJA do potwierdzenia przez właściciela** (status `proposed` w rekordzie; dopóki nie
   ma potwierdzenia z datą, ocena jest `NOT_READY`): ≥ 14 dni, ≥ 200 porównań, rozbieżność model–rdzeń ≤ 0,05 (para
   z wartością w obu silnikach; odpowiednik progu precision 0,95), błędy trybu cienia ≤ 0,05, degradacja ≤ 0,20,
   odrzucenia ≤ 0,30 (progi alarmów z aneksu PV3-18–20), kontrola dryfu `ok` w oknie dla przypiętej pary, decyzja
   właściciela po końcu okna. Wartości nie są skalibrowane na ruchu (którego nie było).
3. **Jeden silnik ekstrakcji.** Wartości liczbowe pochodziły już z `quantity_engine` (PV3-07); zapisy opisowe miały
   dwa zestawy wzorców (`services/mpzp_parser_descriptive.py` i `planning/domain/rules.py`). Teraz oba wejścia
   wołają domenowy `planning/domain/descriptive_engine.py`; w modułach parsera i w `rules.py` nie ma wzorców ustaleń
   (test `test_descriptive_engine.py` pilnuje tego na AST). Ujednolicenie zmieniło wynik — każdą różnicę sprawdzono
   z tekstem uchwał: przeznaczenie z etykiety z listą podpunktów (Łódź: 2 podstawowe, 3 uzupełniające; wcześniej parser
   nic, reguły „a)”), definicje ze słowniczka nie są już przeznaczeniem w regułach, zakaz przełamany w wierszu PDF
   i liczba dziesiętna w zakazie („4,0 m” zamiast „4”), rodzaj dachu w regułach w formie kanonicznej słownika
   (`dwuspadowy_lub_płaski` zamiast pierwszego słowa). Kompromis zachowany z parsera: przecinek kończy zakaz, więc
   wyliczenie po przecinku daje krótszy zakaz. Wartości liczbowe — bez zmian (migawka trybów porównana).
4. **Wersje i cache.** `mpzp-parser/3.1-det` (blokowy `3.1-det+scope.1`), `MPZP_RESULT_SCHEMA_VERSION` 2.5 → 2.6,
   reguły `mpzp-rules/1.0` → `2.0`; sygnatura cache zawiera kontrakt i wersję parsera, więc zapisy sprzed zmiany nie
   są trafieniem. Kalibrację pewności odtworzono (`calibrate_mpzp_confidence.py`; dane i progi bez zmian). Migawkę
   trybów zamrożono ponownie (`freeze_mpzp_parser_modes.py`, opis zmian w skrypcie). Usunięto martwy kod
   (nieużywane stałe i metody silnika, alias `_parse_polish_number`, nieużyte wywołanie `classify_document`).
5. **Regresja opisuje stan faktyczny.** `test_mpzp_parser_regression.py` sprawdza każdy dokument w trybach `legacy`
   i `v3`; luki zamknięte przez silnik dopisano jako oczekiwania (`expected_values` — dokładny zbiór), a
   `expected_found: false` zostały tylko dwa prawdziwe braki wartości w uchwale (lista zamknięta testem). Różnice
   między trybami są dozwolone wyłącznie dla ręcznej weryfikacji strefy (`mode_overrides` z przyczyną: w `legacy`
   wieloznaczna sekcja, w `v3` blok rozstrzygnięty) — wartości parametrów obowiązują w obu trybach.
6. **Wycofanie** opisane w runbooku (§10) i sprawdzone próbą (kroki 15–17): po powrocie do `legacy` wynik 10/10
   dokumentów identyczny z migawką, 0 wartości modelu i 0 żądań, inna sygnatura cache. `legacy` zostaje przez jedno
   wydanie po przełączeniu; potem usuwa się go (zakres w runbooku §10.4), a trybem wycofania staje się `v3`.
7. **Poza zakresem (zgłoszone osobno):** `extract_planning_rules` kończy się wyjątkiem dla całej jednostki prawnej,
   gdy uchwała podaje minimalną intensywność 0 (Raszków, Białystok) — błąd sprzed PV3-21; polityka „> 0” jest wspólna
   z parserem i bramką G6, więc jej zmiana wymaga decyzji właściciela.

## Artefakty pomiarowe i ich skróty (SHA-256)

Skróty policzone 2026-10-05 poleceniem `shasum -a 256 <plik>` (ścieżki względem korzenia repozytorium). Zmiana pliku
zmienia skrót; wyniki starsze od zmiany nie są wtedy dowodem. Katalogi `docs/evaluation` i `docs/adr` są w `.gitignore`
— nowe pliki wymagają `git add -f`.

| Artefakt | Zawartość | SHA-256 |
|---|---|---|
| `docs/evaluation/results/llm-spike/measurements.json` | surowe dane pomiaru PV3-01 (30 wywołań, 10 bloków) | `17857777cc41917e18b07c506aa6e6cb9975e34c7f227c0f37814da3f35b7dc0` |
| `docs/evaluation/results/llm-spike/measurements.md` | tabela i kryteria decyzji spike’u | `517b6654ee30c238fbc724aaa8bd450602fabd8b8615b6dbca9f552e13398a7e` |
| `docs/evaluation/results/llm-spike/inputs.json` | wejścia spike’u (bloki, skróty, prompt `spike-v0`) | `baba06d1948882fc419e510e4e1e8be14393e542311d311a3f83c5b35507c840` |
| `docs/evaluation/results/llm-spike/models.json` | odpowiedź `models.list` (identyfikator, limity, metody) | `3b95669b9ecaed878a570380aa12e7a90492361477dd17b922ec1426d9bd11fa` |
| `docs/evaluation/results/llm-spike/summary.json` | podsumowanie spike’u | `7b2a40bfd06bb8c60a1d0db3e9298efd1d2911f3496351466649e05bb18bd599` |
| `backend/tests/fixtures/mpzp_evaluation/manifest.json` | korpus BK-603 (21 próbek; `corpus_sha256` w manifestach biegów); anotacje: `annotations_sha256` `1408aee8787834219103448e5ebbc4773951d3f65d252a05d381c91087be7244` | `ca5a005efeff440fc59aeca9f9909f5ee8313d7f9dd314baab4bf31c15a8fdd0` |
| `docs/evaluation/results/parser-v3/gate_report.json` | raport bramki 20.17 (`NOT_DECIDABLE`) | `aa9881fe1e80e7a4b4c791d05b1d691c1243de4fe0df7dbf563ac37c218f8b1f` |
| `docs/evaluation/results/parser-v3/gate_report.md` | jw., wersja czytelna | `8a27800a145d42cb898e1b4b0f86fe441cf05e4f5a875b340313ec76cfab348c` |
| `docs/evaluation/results/parser-v3/{legacy,v3}/run_manifest.json` | manifesty biegów offline (`manifest_sha256` w pliku) | `legacy` `3b944b3709c6f418d2d5ab57bc51d4dea1f93a5e6614a85e568715600e692755`, `v3` `498756aa2ab7a1834ec6b49ec10ade0a99f428fdcfa99b16f0fa2926ef33c2d9` (pole `manifest_sha256`) |
| `backend/app/modules/planning/domain/prompts/mpzp_extraction_v1.md` | instrukcja `mpzp-extraction/1` (pełny skrót instrukcji z szablonem wiadomości: `model_pin.json`) | `9d49b86fd1e522cc2425d0c67788f2adb65196ed0b0e97de321060498561cc0f` |
| `backend/tests/fixtures/mpzp_llm_latency/spike_latencies.json` | 30 opóźnień z PV3-01 użytych w teście p95 | `51689d45f49c0cdb2deb640649498447a15c19a59838dd46e63f0c4cf95701a2` |
| `backend/app/core/mpzp_confidence_calibration.json` | artefakt kalibracji pewności (PV3-09; odtworzony w PV3-21 — zmieniła się tylko lista wersji parsera, dane i progi bez zmian) | `6928bbae3b66374d8ed6d9faf879d20bb38e72f9f37f17d7df818c43d47f1333` (wcześniej `101e59b627c4c0baf918f3c8d455a2e05501e6270a0e90ba047d66d4241236c1`) |
| `backend/tests/fixtures/mpzp_prompt_injection/cases.json` | korpus 14 przypadków prompt injection (ADR-014) | `532dcd9421d7e4dc37517f2946e13aed1b28a5f344734d5ed5466cbba2903a8c` |

Skróty z `model_pin.json` (stałe dla pary model + prompt): skrót instrukcji
`126480c2f14155d5fd11324764ec4bc684d5e8d96f993ae7c95255ee31f84ceb`, skrót schematu
`5286e624523a2d52c76e989ccf9629bb4a2f6317340bb24d56906c3e72bd7ca4`. Złote odpowiedzi
(`backend/tests/fixtures/mpzp_evaluation/llm_replay/`, 13 plików) mają nazwę równą kluczowi odpowiedzi; `build_llm_replay_fixtures.py
--check` potwierdza ich aktualność względem promptu, schematu i korpusu. Odtworzenie tabeli:

```bash
shasum -a 256 docs/evaluation/results/llm-spike/*.json docs/evaluation/results/llm-spike/measurements.md \
  docs/evaluation/results/parser-v3/gate_report.* backend/tests/fixtures/mpzp_evaluation/manifest.json \
  backend/app/modules/planning/domain/prompts/mpzp_extraction_v1.md backend/tests/fixtures/mpzp_llm_latency/spike_latencies.json \
  backend/app/core/mpzp_confidence_calibration.json backend/tests/fixtures/mpzp_prompt_injection/cases.json
python3 -c "import json;[print(e, json.load(open(f'docs/evaluation/results/parser-v3/{e}/run_manifest.json'))['manifest_sha256']) for e in ('legacy','v3')]"
```

## Dziennik zmian przypięcia modelu i promptu

Tabela jest kontrolowana skryptem `backend/scripts/check_llm_pin.py` (patrz aneks PV3-18–20): każda para
(`model_id`, `prompt_version`) z `model_pin.json` musi mieć tu wiersz ze skrótem promptu (12 znaków).
Zmiana modelu albo promptu bez nowego wiersza i bez zapisu ponownej ewaluacji `--live` jest wykrywana.

| Data | `model_id` | `prompt_version` | Skrót promptu (SHA-256, 12) | Ocena `--live` na zbiorze złotym | Uwagi |
|---|---|---|---|---|---|
| 2026-10-05 | `gemini-3.8-flash` | `mpzp-extraction/1` | `126480c2f141` | brak — oczekuje (wymaga zgody właściciela na wysyłkę tekstów publicznych i koszt) | pierwsze przypięcie; model potwierdzony `models.list` 2026-10-02, pomiar spike dotyczył promptu `spike-v0`, nie tego promptu |
