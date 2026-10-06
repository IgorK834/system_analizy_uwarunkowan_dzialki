# ADR-014: Obsługa danych ścieżki modelu językowego — dane wysyłane, prompt injection, sekrety, kill switch

- Status: **Zaakceptowany technicznie (2026-10-05)**; punkty prawne — oświadczenie właściciela z ADR-012
  (2026-10-05), **nie opinia prawna**
- Zakres: PV3-16 (Task 20.16). Issue nazywa ten dokument „ADR-013-llm-data-handling.md”; numer 013 zajął
  wcześniej ADR-013 (silnik ilości, warunki, kalibracja, PV3-07–09), więc dokument ma numer 014.
- Powiązane: ADR-001 (port/adapter), ADR-012 (model, warunki dostawcy, decyzja GO, aneksy PV3-10–20),
  ADR-011 (katalog źródeł i pakiet audytowy), Task 8.2 (bezpieczeństwo źródeł i parserów), runbook
  [`docs/operations/mpzp-llm.md`](../operations/mpzp-llm.md) (włączanie, wyłączanie, rotacja klucza, alarmy — PV3-19/20)

## Kontekst

Ścieżka modelu (Gemini 3.8 Flash przez REST, ADR-012) wysyła tekst do zewnętrznego dostawcy poza EOG. Trzy
ryzyka wymagają rozstrzygnięcia: (1) co dokładnie opuszcza system, (2) czy treść dokumentu może sterować
systemem (prompt injection), (3) czy klucz API może wyciec. Do tego potrzebny jest wyłącznik ścieżki bez
wdrożenia kodu i zapis stanu prawnego.

## Decyzje

### 1. Dane wysyłane do modelu — lista dozwolonych pól

Żądanie `generateContent` ma wyłącznie pola `systemInstruction`, `contents` (jedna wiadomość `user` z
jedną częścią tekstową) i `generationConfig` (`temperature`, `maxOutputTokens`, `responseMimeType`,
`responseJsonSchema`, `thinkingConfig`). Zawartość:

| Pole | Treść | Źródło |
|---|---|---|
| instrukcja systemowa | stała instrukcja z definicjami 9 parametrów katalogu (wersjonowana, skrót w provenance) | `prompts/mpzp_extraction_v1.md` |
| `Zone symbols:` | symbole stref planu (publiczne oznaczenia z rysunku/uchwały) | wektor MPZP / discovery / ręczny wpis |
| `Heading path:` | ścieżka nagłówków aktu (np. „§ 5 ust. 2”) | drzewo struktury dokumentu |
| `Part: N of M` | numer części dużego bloku | podział bloku |
| tekst między znacznikami danych | dosłowny tekst bloku publicznego aktu planistycznego | uchwała z BIP/dziennika urzędowego |
| schemat odpowiedzi | stały schemat JSON (wersjonowany) | `extraction_contract.RESPONSE_SCHEMA` |

**Nigdy nie są wysyłane:** identyfikator działki, numer analizy, adres, współrzędne/geometria, dane
użytkownika (nagłówki HTTP klienta, adres IP, `User-Agent`), tokeny dostępu do raportów, sesje. Kontrola
techniczna: `extraction_contract.validate_user_text` odrzuca (`document_text_unsafe`, żądanie nie
wychodzi) każdą wiadomość z polem nagłówka spoza listy albo z naruszonymi znacznikami; adapter buduje ciało
z ustalonych kluczy; dane klienta HTTP nie są przekazywane do orkiestratora (router przekazuje tylko ciało
żądania analizy). Testy: `test_llm_data_handling.py` (prawdziwe żądanie HTTP przechwycone `respx`, także
w pełnej analizie przez API z identyfikatorem działki, `X-Forwarded-For`, `Authorization`, `User-Agent` i
adresem w nagłówkach — żaden z nich ani token dostępu z odpowiedzi nie trafia do żądania).

Tekst aktu może zawierać nazwiska osób pełniących funkcje publiczne (podpis przewodniczącego rady); bloki
stref ich zwykle nie obejmują. Nie redagujemy treści aktu (zmieniłoby to dowód wartości) — to dane publiczne
z urzędowej publikacji.

### 2. Prompt injection — treść dokumentu jest danymi, nie poleceniem

Kontrole (od najwcześniejszej):

1. **Separacja ról**: instrukcja w osobnym polu `systemInstruction`, dane w `contents` między znacznikami
   `=====BEGIN/END DOCUMENT TEXT=====`; instrukcja każe ignorować polecenia z danych.
2. **Znaczniki w dokumencie**: tekst zawierający znacznik danych nie jest wysyłany (`document_text_unsafe`).
3. **Schemat jako jedyny kontrakt**: dodatkowe pola (np. `review_status: verified`, `page_number`) odrzucają
   całą odpowiedź (`schema_violation`); kontrakt nie ma stanu „zweryfikowany” ani pola strony.
4. **Bramki deterministyczne G1–G8** (ADR-012, aneks PV3-12): cytat musi leżeć w bloku dosłownie, bez
   znamion polecenia/JSON/ogrodzenia markdown (`quote_suspicious`), z terminem parametru; liczba musi stać w
   cytacie i w tekście; homoglify i znaki zerowej szerokości nie przechodzą dopasowania (`quote_not_in_block`,
   `symbol_not_in_block`). Odrzucenia kontraktu są mapowane na bramki, więc każde odrzucenie ma kod.
5. **Status**: wartość z modelu zawsze `ai_candidate`, nigdy nie wypełnia płaskich pól strefy i zawsze
   wymaga ręcznej weryfikacji — nawet udane wstrzyknięcie nie tworzy „faktu”.
6. **Zasoby**: limity rozmiaru bloku i żądania, budżet tokenów i czasu (ADR-012, aneks PV3-15).

Korpus testów: `tests/fixtures/mpzp_prompt_injection/cases.json` (14 przypadków: polecenia PL/EN, sfałszowany
JSON, ogrodzenie markdown, znaczniki danych, token 100 000 znaków, homoglify symbolu, cytatu i nagłówka,
znak zerowej szerokości, dodatkowe pola odpowiedzi, podmiana liczby) — każdy z odpowiedzią modelu, który
**wykonał** wstrzyknięcie; oczekiwanie: wynik deterministyczny bez zmian i kod odrzucenia. Korpus ujawnił
kwadratowy koszt wyszukiwania zakresów symboli dla długich tokenów w rdzeniu deterministycznym (token 100 000
znaków ≈ 2 min) — naprawione w `zone_scope._RANGE_MENTION` (wynik bez zmian, test regresji czasu).

### 3. Klucz API

- Wyłącznie zmienna środowiskowa `GEMINI_API_KEY` (`SecretStr`); w repozytorium tylko pusty placeholder w
  `.env.example`; Compose przekazuje zmienną bez wartości wyłącznie do backendu.
- W ruchu: tylko nagłówek `x-goog-api-key` do przypiętego hosta `https://generativelanguage.googleapis.com`
  (stała adaptera, nie ustawienie), `follow_redirects=False` (przekierowanie = błąd `unexpected_redirect`, bez
  żądania pod `Location`), limit rozmiaru żądania i odpowiedzi (odpowiedź czytana strumieniowo i przerywana).
- W logach: adapter loguje tylko kody i skróty; dodatkowo filtr `SecretRedactionFilter` (`core/logging.py`)
  redaguje w każdym komunikacie i tekście wyjątku postacie kluczy `AIza…`, nagłówki `x-goog-api-key` i
  `Authorization`, tokeny `Bearer`/JWT, parametry `key=`/`token=`/`signature=` w adresach i przypisania
  `GEMINI_API_KEY=`.
- W wyjątkach i zapisach: wyjątki portu niosą ustalony tekst i bezpieczny znacznik (komunikat dostawcy jest
  pomijany — mógłby echować klucz albo tekst); cache (`mpzp_llm_extractions`) i rejestr zużycia
  (`mpzp_llm_usage`) nie mają kolumn na żądanie ani klucz. Test: echo klucza w odpowiedzi 400 dostawcy nie
  trafia do logów, wyniku, cache, rejestru ani `repr`.
- **Skanowanie sekretów**: krok CI `python3 backend/scripts/secret_scan.py .` przed utworzeniem `.env`
  (biblioteka standardowa, bez zewnętrznej akcji — mniejsza powierzchnia łańcucha dostaw), wzorce: klucz Google
  (pełna długość), klucze PEM, AWS, GitHub, Slack, przypisanie klucza Gemini oraz pliki `.env`; ten sam skaner
  w pytest (`test_the_repository_has_no_secrets`) obok istniejących `test_no_env_files_in_repository` i
  `test_no_api_key_is_present_in_the_repository_text_files`.

**Runbook — rotacja i wyciek klucza** (kanoniczna, przećwiczona wersja: `docs/operations/mpzp-llm.md` §7 i §9):

1. Rutynowo co 90 dni i po każdej zmianie osób z dostępem: w Google AI Studio / konsoli projektu
   rozliczeniowego utworzyć nowy klucz ograniczony do Generative Language API.
2. Ustawić nowy klucz w menedżerze sekretów/środowisku wdrożenia (`GEMINI_API_KEY`), zrestartować backend
   (`docker compose up -d backend`), sprawdzić jedną analizą w trybie `hybrid_shadow` (log `app.mpzp_llm`,
   brak `unauthorized`).
3. Unieważnić stary klucz w konsoli; sprawdzić w AI Studio, że nie ma ruchu z jego użyciem.
4. **Podejrzenie wycieku**: natychmiast kill switch (pkt 4), unieważnić klucz, przejrzeć zużycie (tabela
   `mpzp_llm_usage` i rozliczenia w konsoli), utworzyć nowy klucz, zdjąć kill switch. Jeżeli klucz trafił do
   historii git — rotacja jest obowiązkowa niezależnie od usunięcia pliku (historia jest kopiowana).

### 4. Kill switch — wyłączenie ścieżki bez wdrożenia kodu

Trzy poziomy, od najszybszego:

| Mechanizm | Działanie | Czas zadziałania |
|---|---|---|
| plik `MPZP_LLM_KILL_SWITCH_FILE` (domyślnie `/var/lib/dzialki/llm-disabled`) | `docker compose exec backend touch /var/lib/dzialki/llm-disabled` — każda kolejna analiza ma powód `kill_switch`; zdjęcie: `rm` | natychmiast, bez restartu (plik w kontenerze nie przeżywa jego odtworzenia — do trwałego wyłączenia użyć poziomu 2) |
| `MPZP_LLM_ENABLED=false` albo `MPZP_PARSER_MODE=legacy`/`v3` | zmiana konfiguracji i restart backendu; adapter nie powstaje | restart |
| twarde limity dobowe/miesięczne (`MPZP_LLM_DAILY_*`, `MPZP_LLM_MONTHLY_*`) | automatyczny stop przy przekroczeniu budżetu (ADR-012, aneks PV3-15) | natychmiast |

W każdym przypadku analiza działa dalej: wynik deterministyczny, ostrzeżenie `MPZP_LLM_UNAVAILABLE` z
powodem, status `partial` (w trybie `hybrid`). Stan jest widoczny w `GET /health` (`components.llm`: `degraded` z
powodem `kill_switch` / `llm_disabled`, albo `disabled` dla trybów `legacy`/`v3`) — komponent nigdy nie jest „failed”
i nie zmienia gotowości usługi (PV3-19). Próba w kontenerze (`touch` i `rm` pliku, zmiana flagi, restart):
runbook §9.

### 5. Dostawca w katalogu źródeł — przetwarzający, nie źródło danych

Dostawca modelu **nie** jest wpisywany do `docs/data_sources/catalog.yaml`: katalog opisuje źródła danych
(licencja, świeżość, redystrybucja), a model niczego nie dostarcza jako fakt — przetwarza publiczny tekst
aktu na kandydatów weryfikowanych deterministycznie. Rola dostawcy jako **przetwarzającego** jest zapisana w
tym ADR i w README; provenance każdej wartości z modelu (model, wersja promptu, skrót odpowiedzi) jest w
evidence parametru i w `mpzp_llm_extractions`.

## Model zagrożeń

| # | Zagrożenie | Wektor | Kontrola | Dowód (test) |
|---|---|---|---|---|
| T1 | Wysłanie danych osobowych/identyfikatorów | błąd kodu dopisujący pole do żądania | lista dozwolonych pól (`validate_user_text`), stałe klucze ciała, brak przekazywania danych klienta HTTP | `test_llm_data_handling` (żądanie HTTP, pełna analiza przez API) |
| T2 | Prompt injection zmieniająca wynik | polecenie/JSON/markdown w tekście uchwały | separacja ról, znaczniki, schemat, bramki G1–G8, status `ai_candidate` | `test_llm_prompt_injection` (14 przypadków), `test_llm_candidate_verifier` (przeciwnicy, własność) |
| T3 | Wyjście poza dane (sterowanie systemem) | odpowiedź z polami `verified`, stroną, zakresem | `extra="forbid"`, brak pól w schemacie, strona z dopasowania | jw. (`role_override_field`, `model_supplied_page`) |
| T4 | Wyciek klucza | logi, wyjątki, zapisy, repozytorium, przekierowanie | `SecretStr`, redakcja logów, bezpieczne wyjątki, brak kolumn, skaner w CI, przypięty host bez przekierowań | `test_llm_data_handling`, `test_llm_gemini_provider`, `test_llm_composition` |
| T5 | Wyczerpanie zasobów/kosztu | długie bloki, pętla wywołań, awaria dostawcy | limity żądania/dokumentu/analizy, doba/miesiąc, współbieżność, częstotliwość, termin, wyłącznik | `test_failure_injection_mpzp_llm` |
| T6 | Odmowa usługi rdzenia deterministycznego | bardzo długi token w dokumencie | liniowe wyszukiwanie zakresów symboli | `test_a_very_long_token_is_parsed_in_linear_time` |
| T7 | Podmiana modelu po stronie dostawcy | inny `modelVersion` | odrzucenie odpowiedzi (`model_mismatch`), alarm `model_mismatch` w `/health`, przypięcie wersji (T10) | `test_failure_injection_mpzp_llm` (`other_model_id`), `test_llm_monitoring` |
| T8 | Użycie danych przez dostawcę | warunki usługi | wyłącznie warstwa płatna (pkt 6) | — (decyzja organizacyjna) |
| T9 | Dryf zachowania modelu pod tym samym identyfikatorem | zmiana po stronie dostawcy | cykliczna kontrola dryfu: bieżące wyjścia kontra zamrożone odtworzenie, próg 0,20; alarm `drift_alarm`; odsetek odrzuceń bramek jako sygnał ciągły | `test_llm_drift`, `test_llm_monitoring` |
| T10 | Zmiana modelu, promptu lub schematu bez ponownej oceny | edycja konfiguracji lub instrukcji | `model_pin.json`: `pin_mismatch` wstrzymuje ścieżkę w czasie działania; skrypt i test CI wykrywają zmianę bez zapisu ewaluacji `--live` i bez wiersza w dzienniku ADR-012 | `test_llm_pin`, `test_llm_monitoring` |
| T11 | Odczyt modelu wzięty za ustalenie prawne | UI, PDF, pakiet audytowy | stałe oznaczenie „odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia”, nota o braku interpretacji prawnej, status `ai_candidate`; cytat z modelu w pakiecie podlega polu `redistribution` | `test_report_model_reading`, `test_audit_model_provenance`, `MpzpZoneCard*.test.tsx` |

Ryzyka rezydualne: model może zwrócić wartość dosłownie obecną w dokumencie, ale błędnie przypisaną
semantycznie (np. operator `max` zamiast `min`) — chroni ją wyłącznie status `ai_candidate` i przegląd
ręczny; heurystyka G3 (termin parametru, znamiona polecenia) może odrzucić poprawną wartość (bezpieczne:
zostaje wynik deterministyczny).

## Notatka prawna (stan wiedzy, nie opinia prawna)

- **Charakter danych:** uchwały w sprawie MPZP są aktami prawa miejscowego, publikowanymi w dziennikach
  urzędowych i BIP; akty normatywne nie są przedmiotem prawa autorskiego (art. 4 pkt 1 ustawy o prawie
  autorskim i prawach pokrewnych). Do modelu trafia wyłącznie ten tekst i symbole stref (pkt 1).
- **Warunki dostawcy** (ADR-012, „Fakty zweryfikowane w dokumentacji dostawcy”, stan 2026-10-01): w warstwie
  płatnej prompty i odpowiedzi **nie służą do ulepszania produktów** (trenowania); logi są przechowywane
  tymczasowo wyłącznie do wykrywania nadużyć i zgodności prawnej; w warstwie bezpłatnej dane mogą być używane
  i czytane przez ludzi — dlatego wyłącznie warstwa płatna. Dla EOG/Szwajcarii/Wielkiej Brytanii warunki
  warstwy płatnej obowiązują dla wszystkich usług.
- **Region:** dla Gemini Developer API dostawca nie deklaruje rezydencji danych (przetwarzanie może odbywać się
  w dowolnym kraju, w którym ma obiekty) — przekazanie poza EOG. Gwarancje regionalne i SLA wymagałyby Vertex AI
  (wariant 7c ADR-012, niezweryfikowany).
- **Potwierdzenie:** właściciel projektu 2026-10-05 potwierdził podstawę wykorzystania tekstów aktów oraz
  zgodność z warunkami dostawcy i RODO jako **swoje oświadczenie** (ADR-012, „Decyzja właściciela”). Asystent
  nie wykonał weryfikacji prawnej; zmiana warunków dostawcy wymaga ponownego przeglądu tego punktu.

## Monitoring bez treści (PV3-19)

Metryki i logi ścieżki modelu zawierają wyłącznie liczby, kody i skróty: `llm_metrics.log_event` odrzuca pola o innej
wartości niż liczba, wartość logiczna albo krótki jednowierszowy napis; test `test_llm_monitoring` sprawdza, że logi
pełnego przebiegu (także z odpowiedzią dostawcy echującą klucz) nie zawierają treści żądania, cytatów ani klucza.
`GET /health` nie ujawnia kosztu, limitów ani wersji; szczegóły (`GET /health/llm`) wymagają klucza administracyjnego i
też nie zawierają treści żądań ani kluczy. Progi alarmów i ich uzasadnienie: ADR-012, aneks PV3-18–20.

## Artefakty

| Artefakt | SHA-256 |
|---|---|
| `backend/tests/fixtures/mpzp_prompt_injection/cases.json` (14 przypadków prompt injection) | `532dcd9421d7e4dc37517f2946e13aed1b28a5f344734d5ed5466cbba2903a8c` |
| `backend/app/modules/planning/domain/prompts/mpzp_extraction_v1.md` (instrukcja systemowa, lista pól żądania) | `9d49b86fd1e522cc2425d0c67788f2adb65196ed0b0e97de321060498561cc0f` |
| `docs/evaluation/results/llm-spike/measurements.json` (pomiar PV3-01: warunki dostawcy, opóźnienia, koszt) | `17857777cc41917e18b07c506aa6e6cb9975e34c7f227c0f37814da3f35b7dc0` |

Pozostałe artefakty pomiarowe ścieżki modelu (wraz z poleceniem odtwarzającym tabelę skrótów): ADR-012, sekcja
„Artefakty pomiarowe i ich skróty (SHA-256)”.

## Konsekwencje

- Każda zmiana pól żądania wymaga zmiany `ALLOWED_HEADER_LINES` i tego ADR (test zawodzi inaczej).
- Zmiana instrukcji/szablonu nadal wymaga podniesienia `PROMPT_VERSION` (ADR-012, aneks PV3-11).
- Kill switch i limity działają bez wdrożenia kodu; ich stan jest widoczny w ostrzeżeniach analizy i w
  licznikach `llm.unavailable.*` (`app.mpzp_llm`).
