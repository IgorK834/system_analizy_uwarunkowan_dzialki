# Runbook: ścieżka modelu językowego w parserze MPZP

Dotyczy ekstrakcji parametrów MPZP modelem językowym (wariant C, [ADR-012](../adr/ADR-012-mpzp-llm-extraction.md)).
Dane i zagrożenia: [ADR-014](../adr/ADR-014-llm-data-handling.md) (w zadaniu — „ADR-013 o danych”; numer 013 zajął
wcześniej ADR silnika ilości). Stan: **ścieżka jest wyłączona domyślnie** (`MPZP_LLM_ENABLED=false`,
`MPZP_PARSER_MODE=legacy`); decyzja o włączeniu produkcyjnym nie została podjęta (bramka jakości z Task 20.17:
`NOT_DECIDABLE`, patrz ADR-012). Przełączenie domyślnego trybu i wycofanie: §10 (PV3-21).

Zasada, która obowiązuje przy każdej operacji: **wartość z modelu to kandydat do ręcznej weryfikacji
(`ai_candidate`), nigdy „verified”**, a awaria modelu nigdy nie psuje analizy — wynik pochodzi wtedy z rdzenia
deterministycznego z ostrzeżeniem `MPZP_LLM_UNAVAILABLE` i statusem `partial`.

## 1. Przegląd sterowania

| Co | Gdzie | Efekt |
|---|---|---|
| `MPZP_PARSER_MODE` | środowisko, restart | `legacy` (domyślny) / `v3` — bez modelu; `hybrid_shadow` — odpowiedź = `v3`, model liczony w tle i porównywany; `hybrid` — `v3` + kandydaci modelu po bramkach G1–G8 |
| `MPZP_LLM_ENABLED` | środowisko, restart | `false` — adapter nie powstaje, brak ruchu do dostawcy |
| plik `MPZP_LLM_KILL_SWITCH_FILE` (domyślnie `/var/lib/dzialki/llm-disabled`) | `touch` / `rm` w kontenerze | natychmiastowe wyłączenie bez restartu i wdrożenia |
| `GEMINI_API_KEY` | środowisko / menedżer sekretów, restart | klucz projektu **płatnego**; nigdy w repozytorium, logach ani artefaktach |
| `MPZP_LLM_MODEL`, `MPZP_LLM_PROMPT_VERSION` | środowisko, restart | muszą odpowiadać przypięciu (`model_pin.json`); inaczej `pin_mismatch` i ścieżka wstrzymana |
| limity `MPZP_LLM_*` | środowisko, restart | tabela w §4 |

Stan komponentu: `GET /health` → `components.llm.status` ∈ {`ok`, `degraded`, `disabled`} (**nigdy `failed`**;
nie wpływa na `GET /health/ready` ani `/health/live`). Szczegóły dla operatora: `GET /health/llm` z nagłówkiem
`X-Admin-Key` (powody, odsetki, zużycie, progi, przypięcie, liczniki — bez treści żądań i kluczy).

```bash
curl -s http://localhost:8000/health
curl -s -H "X-Admin-Key: <klucz operatora z ADMIN_API_KEYS>" http://localhost:8000/health/llm | python3 -m json.tool
```

## 2. Włączanie

Warunki wstępne (wszystkie):

1. Decyzja właściciela o włączeniu produkcyjnym wpisana w ADR-012 (do tej pory: GO dla ścieżki z warunkami,
   **bez** decyzji o włączeniu — bramka Task 20.17 `NOT_DECIDABLE`).
2. Klucz projektu rozliczeniowego (warstwa płatna; bezpłatna jest wykluczona — dane służyłyby dostawcy do
   ulepszania produktów) w menedżerze sekretów albo lokalnym, niezacommitowanym `.env`.
3. Przypięcie zgodne i oceniona para (model, prompt):
   ```bash
   python3 backend/scripts/check_llm_pin.py check --require-evaluation   # kod 0 = zgodne i oceniona na zbiorze złotym
   ```
   Dziś kończy się kodem 1 (`evaluation_not_recorded`): nie wykonano biegu `--live` na zbiorze złotym (wymaga
   zgody właściciela na wysyłkę publicznych tekstów aktów i koszt). To świadomie blokuje włączenie produkcyjne.
4. Skaner sekretów czysty: `python3 backend/scripts/secret_scan.py .`.

Kolejność:

1. **Tryb cienia** — `MPZP_PARSER_MODE=hybrid_shadow`, `MPZP_LLM_ENABLED=true`, `GEMINI_API_KEY=…`;
   `docker compose up -d backend`. Odpowiedzi API się nie zmieniają; różnice (`agree`/`disagree`/`llm_only`/
   `det_only`) trafiają do logu `app.mpzp_llm` i liczników.
2. Sprawdź `GET /health` (`components.llm.status` = `ok`) i `GET /health/llm`: `pin.problems` puste,
   `breaker` = `closed`; ostrzeżenia `evaluation_pending` i `drift_never_checked` oznaczają brak biegu `--live` i
   kontroli dryfu (§6) — to nie awaria.
3. Obserwuj co najmniej dobę ruchu: odsetek odrzuceń per bramka, odsetek degradacji, koszt (progi w §5).
4. Dopiero po decyzji właściciela: `MPZP_PARSER_MODE=hybrid` (kandydaci widoczni w UI i PDF z oznaczeniem
   „odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia”). Zmiana **domyślnego**
   trybu w kodzie (ustawienia, Compose, `.env.example`) ma osobną bramkę — §10.

## 3. Wyłączanie

Od najszybszego (każdy poziom zostawia analizy działające — wynik deterministyczny, `MPZP_LLM_UNAVAILABLE`,
`partial`):

| Poziom | Polecenie | Czas |
|---|---|---|
| 1. kill switch | `docker compose exec backend touch /var/lib/dzialki/llm-disabled` (zdjęcie: `rm`) | natychmiast, bez restartu |
| 2. konfiguracja | `MPZP_LLM_ENABLED=false` albo `MPZP_PARSER_MODE=legacy` (lub `v3`) + `docker compose up -d backend` | restart |
| 3. limity | automatyczny twardy stop przy przekroczeniu doby/miesiąca | natychmiast |

Uwagi: plik kill switcha leży w warstwie zapisywalnej kontenera — nie przeżywa jego odtworzenia (do trwałego
wyłączenia użyj poziomu 2). `/health` pokazuje `degraded` z powodem `kill_switch` (analizy są wtedy `partial`),
a po zmianie trybu na `legacy`/`v3` — `disabled` (stan zamierzony, nie błąd).

## 4. Limity

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `MPZP_LLM_MAX_REQUESTS_PER_ANALYSIS` / `MPZP_LLM_MAX_INPUT_TOKENS_PER_ANALYSIS` | 6 / 12 000 | budżet jednej analizy |
| `MPZP_LLM_MAX_INPUT_TOKENS_PER_DOCUMENT` / `…_PER_REQUEST` | 12 000 / 4 000 | dokument i żądanie |
| `MPZP_LLM_TIME_BUDGET_SECONDS` / `MPZP_LLM_ANALYSIS_DEADLINE_SECONDS` | 30 / 90 | czas ścieżki modelu i termin analizy |
| `MPZP_LLM_DAILY_TOKEN_LIMIT` / `MPZP_LLM_DAILY_COST_LIMIT_USD` | 2 000 000 / 5 | twardy stop doby (UTC), rejestr `mpzp_llm_usage` |
| `MPZP_LLM_MONTHLY_TOKEN_LIMIT` / `MPZP_LLM_MONTHLY_COST_LIMIT_USD` | 40 000 000 / 100 | twardy stop miesiąca (UTC) |
| `MPZP_LLM_MAX_CONCURRENCY` / `MPZP_LLM_MAX_REQUESTS_PER_MINUTE` | 4 / 60 | w jednym procesie (przy N procesach — N × wartość) |

Limity doby i miesiąca zaakceptował właściciel 2026-10-05; **zmiana wymaga nowego wpisu z datą w ADR-012**
(aneks PV3-15–17, pkt 5). Pusta wartość limitu znaczy „bez limitu”, a nie 0. Limity wydatków w AI Studio
(poziom projektu dostawcy) są niezależnym dodatkowym zabezpieczeniem.

## 5. Monitoring i reakcja na alarmy

Metryki (liczniki procesu `app.modules.planning.application.llm_metrics`, zdarzenia `app.mpzp_llm` w logu):
liczba wywołań, opóźnienie (histogram skumulowany, suma, maksimum), tokeny, koszt szacowany (µUSD, cena od 2027),
odrzucenia per bramka G1–G8 i per kod, trafienia/chybienia cache, degradacje i analizy z modelem. Odsetki są liczone
w oknie czasowym (`MPZP_LLM_ALARM_WINDOW_SECONDS`, domyślnie 1 h) i dopiero od minimalnej próby.

Progi alarmów (**propozycja, nieskalibrowana na ruchu produkcyjnym — którego jeszcze nie było**; zmiana: nowy wpis
w ADR-012):

| Powód w `/health` | Warunek (domyślnie) | Reakcja |
|---|---|---|
| `rejection_rate_alarm` | odrzucenia G1–G7 ≥ 30% ocenionych kandydatów, ≥ 20 kandydatów w oknie | sprawdź `/health/llm` → `rates.rejection_by_gate`; G3/G4 — model cytuje inaczej niż tekst (dryf modelu albo zmiana formatu uchwał); G7 — zakres strefy. Uruchom kontrolę dryfu (§6); przy potwierdzeniu — wyłącz ścieżkę (§3) |
| `degradation_rate_alarm` | ≥ 20% analiz z modelem zakończonych degradacją, ≥ 10 analiz w oknie | `rates.degradation`, liczniki `llm.unavailable.<powód>` i log `app.mpzp_llm`; powód dominujący wskazuje przyczynę (limit, timeout, dostawca) |
| `daily_cost_alarm` | koszt doby ≥ 4 USD (80% limitu twardego 5 USD) | przejrzyj `usage.daily_cost_usd`; nietypowy ruch → rozważ kill switch przed twardym stopem |
| `daily_limit_reached` | koszt doby ≥ limit twardy | ścieżka jest już wstrzymana do końca doby UTC; ustal przyczynę, **nie** podnoś limitu bez wpisu w ADR |
| `breaker_open` | 5 kolejnych awarii dostępności; otwarty 60 s | awaria dostawcy/sieci/klucza; sprawdź `unauthorized`/`rate_limited`/`timeout` w logu; klucz → §7 |
| `model_unavailable` | ostatni przebieg w oknie zakończył się degradacją | zwykle ustępuje po udanym przebiegu; jeśli nie — jak `breaker_open` |
| `model_mismatch` | dostawca zwrócił inny `modelVersion` | odpowiedzi odrzucone (jakość innego modelu nie jest zmierzona); ustal, czy dostawca podmienił model; procedura §8 |
| `pin_mismatch` | model/prompt/schemat ≠ `model_pin.json` | ścieżka wstrzymana; przywróć przypięte wartości albo wykonaj §8 |
| `drift_alarm` | ostatnia kontrola dryfu przekroczyła próg 0,20 | §6 |
| `config_error` | brak klucza albo katalogu odtwarzania | uzupełnij konfigurację (§7), restart |
| `kill_switch`, `llm_disabled` | plik kill switcha / flaga wyłączona w trybie z modelem | stan zamierzony; zdejmij kill switch lub ustaw tryb `legacy`/`v3` |
| `health_check_error` | błąd samej oceny zdrowia | log `mpzp_llm health evaluation failed: <typ błędu>`; zgłoś (to błąd aplikacji, nie dostawcy) |

Ostrzeżenia (`warnings`, nie zmieniają statusu): `evaluation_pending` (brak biegu `--live` dla przypiętej pary),
`drift_never_checked` / `drift_check_stale` (brak kontroli dryfu lub starsza niż 14 dni), `daily_cost_unknown`
(rejestr zużycia nieczytelny).

## 6. Cykliczna kontrola dryfu

Dostawca może zmienić zachowanie modelu pod tym samym identyfikatorem. Kontrola przepuszcza 13 bloków kanarkowych
(złote przypadki korpusu BK-603) przez potok z zamrożonym odtworzeniem i z dostawcą na żywo i porównuje przyjęte
wartości; dryf = udział wartości występujących po jednej stronie. **Poza CI**, ręcznie albo z harmonogramu (np. raz
na tydzień), wysyła do dostawcy publiczne teksty aktów i kosztuje rzędu 0,2 USD (≤ 13 żądań; liczy się do limitów
doby/miesiąca):

```bash
cd backend && python3 scripts/check_llm_drift.py --confirm-public-text          # zapisuje stan czytany przez /health
python3 scripts/check_llm_drift.py --confirm-public-text --cases L1 L3 --max-requests 4   # krótsza kontrola
```

Kody wyjścia: `0` brak dryfu, `3` alarm (rozbieżność ≥ 0,20), `4` nierozstrzygnięte (awaria dostawcy — nie
alarm), `2` błąd użycia (brak zgody/klucza, kill switch, CI). Alarm: zbadaj `cases[*].only_frozen`/`only_live`
w raporcie (`--output`), wyłącz ścieżkę (§3) i wykonaj §8. Zamrożone odtworzenie to złota odpowiedź z adnotacji (nie
nagranie modelu), więc kontrola mierzy też zgodność z adnotacjami na zestawie kanarkowym.

## 7. Rotacja klucza API

Rutynowo co 90 dni, po zmianie osób z dostępem i po podejrzeniu wycieku (wtedy najpierw kill switch):

1. W konsoli projektu rozliczeniowego dostawcy utwórz nowy klucz ograniczony do Generative Language API.
2. Ustaw `GEMINI_API_KEY` w menedżerze sekretów / środowisku wdrożenia (nie w repozytorium).
3. `docker compose up -d backend` (odtworzenie kontenera z nowym środowiskiem; restart procesu zeruje też wyłącznik
   awaryjny).
4. Sprawdź: `GET /health/llm` (`config_error` brak, `breaker` = `closed`); jedna analiza w trybie `hybrid_shadow`
   (log `app.mpzp_llm`, brak `unauthorized`).
5. Unieważnij stary klucz w konsoli; po chwili sprawdź w konsoli, że nie ma ruchu z jego użyciem.
6. Podejrzenie wycieku: kill switch → unieważnij klucz → przejrzyj zużycie (`mpzp_llm_usage`, rozliczenia w
   konsoli) → nowy klucz → zdejmij kill switch. Klucz w historii git wymaga rotacji niezależnie od usunięcia pliku.

Klucz nie pojawia się w logach (filtr `SecretRedactionFilter`, wyjątki bez komunikatu dostawcy), w odpowiedziach
`/health`, w cache ani w rejestrze zużycia; skaner `scripts/secret_scan.py` pilnuje repozytorium (ADR-014).

## 8. Zmiana modelu albo promptu

Nowa wersja modelu lub promptu **nie wchodzi bez ponownej ewaluacji na zbiorze złotym** i wpisu w dzienniku zmian
ADR-012. Kontroluje to `model_pin.json` (model, wersja i skrót promptu, wersja i skrót schematu, `thinking_level`)
w trzech miejscach: w czasie działania (`pin_mismatch` → ścieżka wstrzymana), w CI (`tests/test_llm_pin.py`) i
skryptem `check_llm_pin.py`.

1. Zmień kod/prompt/model (`MPZP_LLM_MODEL`, `MPZP_LLM_PROMPT_VERSION`, `PROMPT_VERSION`, `prompts/…`). Po zmianie
   instrukcji, schematu lub modelu podnieś wersję i odtwórz złote odpowiedzi:
   `python3 backend/scripts/build_llm_replay_fixtures.py` (oraz stałą `MODEL` w tym skrypcie).
2. Wykonaj bieg na zbiorze złotym **na żywo** (ręcznie, poza CI; wymaga zgody właściciela, klucza i kosztu):
   ```bash
   cd backend && python3 scripts/evaluate_mpzp_parser.py --engine v3 hybrid --live --model <model_id> \
     --llm-replay <katalog-odpowiedzi> --output-dir ../docs/evaluation/results/<bieg>
   ```
3. Zapisz wynik w przypięciu (sprawdza, że to był bieg `hybrid` na żywo dla DOKŁADNIE tej pary):
   `python3 backend/scripts/check_llm_pin.py record-evaluation --run-manifest docs/evaluation/results/<bieg>/hybrid/run_manifest.json`
4. Zaktualizuj `model_pin.json` (model/prompt/skróty), ustawienia domyślne, `docker-compose.yml`, `.env.example` oraz
   dodaj wiersz do „Dziennika zmian przypięcia modelu i promptu” w ADR-012 z wynikiem oceny i decyzją właściciela.
5. `python3 backend/scripts/check_llm_pin.py check --require-evaluation` musi dać kod 0; dopiero wtedy wdrożenie —
   najpierw `hybrid_shadow` (§2).

Wynik `--live` nie zastępuje bramki jakości z Task 20.17: ocenia, czy nowa para nie pogorszyła zmierzonych wskaźników.

## 9. Wynik próby runbooka (2026-10-05)

Próba obejmuje włączenie, wyłączenie i rotację klucza, a także limity i zmianę modelu. Wykonano ją na dwóch
poziomach; **żaden nie używał prawdziwego klucza ani nie łączył się z dostawcą modelu**.

**Poziom 1 — prawdziwy kontener** (backend z obrazu Compose + PostGIS 16, osobny projekt `pv3rb`, kod i migracje
`001`–`030` z drzewa roboczego, dostawca `fake` z odtwarzaniem złotych odpowiedzi albo `gemini` z losowym
„kluczem”; ścieżka restartu to `docker compose up -d backend` ze zmienioną zmienną środowiskową, odczyt stanu —
`GET /health`, `GET /health/llm` i `GET /health/ready` w kontenerze):

| Krok | Działanie | Zaobserwowano | Wynik |
|---|---|---|---|
| R1 | domyślna konfiguracja (`legacy`, flaga wyłączona) | `/health`: `llm` = `disabled`, `status` = `ok`; `/health/ready` 200 | zgodne |
| R2 | `MPZP_PARSER_MODE=hybrid_shadow`, `MPZP_LLM_ENABLED=true`, `MPZP_LLM_PROVIDER=fake` + katalog odtwarzania, `up -d` | `llm` = `ok`; `/health/llm`: brak powodów, ostrzeżenie `drift_never_checked` | zgodne |
| R3 | `docker compose exec backend touch /var/lib/dzialki/llm-disabled` (bez restartu) | `llm` = `degraded` (`kill_switch`) od razu, `/health/ready` nadal 200; po `rm` — `ok` | zgodne |
| R4 | `MPZP_LLM_ENABLED=false` w trybie `hybrid_shadow`; potem `MPZP_PARSER_MODE=legacy` | `degraded` (`llm_disabled`); potem `disabled` | zgodne |
| R5 | dostawca `gemini`, tryb `hybrid_shadow`, **brak klucza** | `degraded` (`config_error`, `config_error: api_key`); odczyt zużycia dobowego z PostGIS (`mpzp_llm_usage`) = 0,0 USD | zgodne |
| R5b | losowy „stary” klucz w środowisku, `up -d` | `ok`, `breaker` = `closed`, `pin.problems` = [], ostrzeżenia `evaluation_pending`, `drift_never_checked` | zgodne |
| R5c | losowy „nowy” klucz, `up -d` (rotacja) | `ok`, jak wyżej | zgodne |
| R5d | logi kontenera ze starym i z nowym kluczem (10 i 9 wierszy) oraz odpowiedzi `/health/llm` | 0 wystąpień starego i nowego klucza | zgodne |
| R6 | gotowość i stan kontenera w każdym z powyższych stanów | `/health/ready` 200, kontener `healthy` | zgodne |

Ustalenia z próby kontenerowej: (1) `docker-compose.yml` nie przekazuje `MPZP_LLM_REPLAY_DIR` do kontenera, więc
dostawca `fake` w samym Compose kończy jako `config_error: replay_dir` — katalog odtwarzania trzeba dodać
w pliku nadpisań (to tryb testowy, nie produkcyjny); (2) `/health` nie wywołuje dostawcy, więc `ok` z losowym
kluczem **nie dowodzi, że klucz jest ważny** — dowodzi tego dopiero jedna analiza w trybie `hybrid_shadow`
(§7 pkt 4); (3) `up -d` odtwarza kontener, więc jego poprzednie logi znikają — przegląd logów starego klucza
wymaga zbieracza logów albo zapisu przed odtworzeniem (tak zrobiono w próbie).

**Poziom 2 — proces z prawdziwymi ustawieniami, kompozycją i adapterem Gemini; dostawca zastąpiony zamrożonymi
odpowiedziami `respx`** (`backend/scripts/rehearse_llm_runbook.py`, powtarzalny offline, testowany w
`tests/test_llm_runbook_rehearsal.py`; „klucze” to losowe teksty, dostawca przyjmuje tylko „nowy” i odpowiada 401 na
„stary”):

| Krok | Działanie | Oczekiwanie | Zaobserwowano | Wynik |
|---|---|---|---|---|
| 1. A. Włączenie | domyślna konfiguracja (tryb legacy, flaga wyłączona) | komponent `disabled`, brak żądań | disabled, żądań do dostawcy: 0 | zgodne |
| 2. A. Włączenie | `MPZP_PARSER_MODE=hybrid_shadow`, `MPZP_LLM_ENABLED=true`, brak klucza | `degraded`: `config_error` (api_key) | degraded: config_error (api_key) | zgodne |
| 3. A. Włączenie | dodanie klucza, restart (nowe `Settings`), pierwszy przebieg | `ok`; wartości po bramkach; ostrzeżenia o braku ewaluacji i dryfu | ok ['evaluation_pending', 'drift_never_checked']; pierwszy przebieg: ok, wartości: 9, żądań: 1 | zgodne |
| 4. B. Wyłączanie | kill switch: `touch` pliku (bez restartu) | `degraded`: `kill_switch`; brak żądań; powód `kill_switch` w opcjach parsera | degraded: kill_switch; przebieg: kill_switch; opcje parsera: kill_switch; żądań: 0 | zgodne |
| 5. B. Wyłączanie | kill switch: `rm` pliku | `ok`; żądania wracają | ok; przebieg: ok (9); żądań: 1 | zgodne |
| 6. B. Wyłączanie | `MPZP_LLM_ENABLED=false` w trybie hybrid (restart) | `degraded`: `llm_disabled`; brak żądań | degraded: llm_disabled; przebieg: llm_disabled; żądań: 0 | zgodne |
| 7. B. Wyłączanie | powrót do `MPZP_PARSER_MODE=legacy` | komponent `disabled` | disabled | zgodne |
| 8. C. Rotacja klucza | dostawca zna tylko nowy klucz, aplikacja ma stary | przebieg `unauthorized`, `degraded`: `model_unavailable` | przebieg: unauthorized; zdrowie: degraded model_unavailable; żądań: 1 | zgodne |
| 9. C. Rotacja klucza | nowy klucz w środowisku, restart, kontrolny przebieg | `ok`; w nagłówku nowy klucz | przebieg: ok (9); zdrowie: ok; żądań: 1, klucz w nagłówku: ['AIza…'] | zgodne |
| 10. C. Rotacja klucza | stary klucz po unieważnieniu | odrzucony przez dostawcę (`unauthorized`) | stary klucz po unieważnieniu: unauthorized | zgodne |
| 11. C. Rotacja klucza | przegląd logów całej próby | żaden klucz (stary ani nowy) nie występuje w logach | wierszy logu: 27; klucz stary/nowy w logach: False | zgodne |
| 12. D. Limity i alarmy | koszt doby 4,2 USD (próg alarmu 4) i 5,0 USD (limit twardy) | `daily_cost_alarm`, potem `daily_limit_reached` | po udanym przebiegu (ok): ok; 4,2 USD: daily_cost_alarm; 5,0 USD: daily_limit_reached | zgodne |
| 13. E. Zmiana modelu | `MPZP_LLM_MODEL` zmieniony bez ponownej ewaluacji | `degraded`: `pin_mismatch`; ścieżka wstrzymana, brak żądań | degraded: pin_mismatch; przebieg: pin_mismatch; żądań: 0 | zgodne |
| 14. E. Zmiana modelu | bramka wdrożeniowa przy obecnym przypięciu | jedyna rozbieżność: brak zapisu ewaluacji `--live` | `check_llm_pin.py check --require-evaluation`: ['evaluation_not_recorded'] | zgodne |

| 15. F. Przełączenie i wycofanie | `MPZP_PARSER_MODE=hybrid` jako domyślny (restart), przebieg kontrolny | `ok`; kandydaci modelu po bramkach; sygnatura cache trybu `hybrid` | przebieg: ok (9); żądań: 1; sygnatura: hybrid, gemini-3.8-flash | zgodne |
| 16. F. Przełączenie i wycofanie | wycofanie: `MPZP_PARSER_MODE=legacy` (restart), 10 dokumentów regresji | brak adaptera i żądań; wynik identyczny z migawką `legacy`; 0 wartości modelu; inna sygnatura cache | adapter: False; dokumenty identyczne z migawką legacy: 10/10; wartości modelu: 0; żądań: 0; sygnatura cache inna niż hybrid: True | zgodne |
| 17. F. Przełączenie i wycofanie | stan po wycofaniu i bramka przełączenia | komponent `disabled`; zapis wskazuje `legacy` jako tryb wycofania; bramka spójna (bez naruszeń) | zdrowie: disabled; tryb wycofania w zapisie: legacy; bramka przełączenia: NOT_READY (kod 3) | zgodne |

Kroki 15–17 dodano 2026-10-05 (PV3-21). Odtworzenie: `cd backend && python3 scripts/rehearse_llm_runbook.py` (kod 0 =
17/17 kroków zgodnych).

**Czego próba nie wykazała:** unieważnienia klucza w konsoli dostawcy i braku ruchu na starym kluczu po stronie
dostawcy (kroki 1 i 5 z §7 to czynności w konsoli); zachowania przy prawdziwym ruchu, opóźnieniu i kosztach;
progów alarmów na rzeczywistym ruchu (brak ruchu produkcyjnego — progi to propozycja); kontroli dryfu z
prawdziwym dostawcą (skrypt przetestowano na zniekształconym odtworzeniu).

## 10. Przełączenie domyślnego trybu i wycofanie (PV3-21)

Domyślny tryb parsera to dziś `legacy` i **nie wolno** go zmienić, dopóki nie są spełnione wszystkie przesłanki.
Zapis przesłanek: `backend/app/core/mpzp_parser_rollout.json` (śledzony w repozytorium); ocenia go
`backend/scripts/check_parser_default_switch.py`, a test `tests/test_mpzp_parser_rollout.py` nie przepuści w CI
zmiany domyślnego trybu bez oceny `READY`.

```bash
cd backend && python3 scripts/check_parser_default_switch.py check --verify-files
# kod 0 = READY; 3 = NOT_READY (zostaje legacy); 1 = naruszenie (zapis niespójny albo tryb zmieniony bez przesłanek)
```

Stan 2026-10-05: `NOT_READY` — `gate_not_go` (bramka 20.17 `NOT_DECIDABLE`), `pin_not_ready` (brak ewaluacji
`--live`), `shadow_requirements_unconfirmed`, `shadow_observation_missing`, `owner_decision_missing`.

### 10.1. Przesłanki (kolejno)

1. **Bramka jakości `GO`** (Task 20.17, `scripts/mpzp_quality_gate.py gate`), zapisana w rekordzie razem ze skrótem
   raportu: `python3 scripts/check_parser_default_switch.py record-gate --report ../docs/evaluation/results/parser-v3/gate_report.json`.
2. **Przypięcie z ewaluacją `--live`** (§8): `check_llm_pin.py check --require-evaluation` = 0.
3. **Potwierdzone progi okresu cienia.** Propozycja w rekordzie (`shadow_requirements`, status `proposed`): ≥ 14 dni,
   ≥ 200 porównań w tle, rozbieżność model–rdzeń ≤ 5% par z wartością w obu (odpowiednik progu precision 0,95),
   błędy trybu cienia ≤ 5%, degradacja ≤ 20% i odrzucenia ≤ 30% (progi alarmów §5), kontrola dryfu `ok` w oknie
   obserwacji dla przypiętej pary. Właściciel potwierdza je wpisem z datą w ADR-012 i ustawia
   `"status": "confirmed"`, `"confirmed_by_owner_on": "<data>"` — dopiero wtedy liczą się do oceny.
4. **Okres cienia bez regresji.** `MPZP_PARSER_MODE=hybrid_shadow` (§2) przez uzgodniony czas; codziennie zbierany
   log backendu (`docker compose logs --no-color backend >> shadow.log` albo zbieracz logów — `up -d` kasuje logi
   kontenera) i co najmniej jedna kontrola dryfu (§6) w oknie. Raport i zapis w rekordzie:
   ```bash
   python3 scripts/mpzp_shadow_report.py --log shadow.log --drift-state /var/lib/dzialki/llm-drift.json \
     --output ../docs/evaluation/results/parser-v3/shadow_observation.json
   python3 scripts/check_parser_default_switch.py record-shadow --report ../docs/evaluation/results/parser-v3/shadow_observation.json
   ```
   Raport zawiera wyłącznie liczby (zdarzenia `shadow_compare` i `pipeline` z logu `app.mpzp_llm`) i skróty plików.
5. **Decyzja właściciela** po końcu okna cienia: wpis z datą w ADR-012 i w rekordzie
   `"owner_decision": {"decision": "GO", "date": "<data>", "reference": "ADR-012, …"}`.

### 10.2. Przełączenie (jedno wydanie)

Gdy `check` daje 0: w jednym wydaniu zmień domyślny tryb na `hybrid` w `Settings.mpzp_parser_mode`,
`docker-compose.yml`, `.env.example`, README i rekordzie (`default_mode`, `legacy_retention.switched_in_release`,
wpis w `history`); zaktualizuj test `test_the_default_parser_mode_is_legacy…` w `test_mpzp_parser_modes.py`. Analizy
w cache policzone w `legacy` przestają być trafieniem (sygnatura zawiera tryb) — pierwszy dzień po wdrożeniu
oznacza więcej żądań do dostawcy (limity §4 obowiązują).

### 10.3. Wycofanie (powrót do trybu deterministycznego)

| Pilność | Działanie | Efekt |
|---|---|---|
| natychmiast, bez restartu | kill switch (§3, poziom 1) | wynik deterministyczny (`v3`) z `MPZP_LLM_UNAVAILABLE`, `partial` |
| restart | `MPZP_PARSER_MODE=legacy` w środowisku + `docker compose up -d backend` | wynik **identyczny** z trybem sprzed przełączenia (migawka `tests/fixtures/mpzp_parser_modes`), brak adaptera i ruchu do dostawcy, komponent `disabled` |
| wydanie | przywróć `default_mode: legacy` w kodzie i rekordzie (wpis w `history` z powodem) | trwałe wycofanie |

Sprawdzone 2026-10-05 próbą (§9, kroki 15–17): po powrocie do `legacy` 10/10 dokumentów regresji daje wynik
identyczny z migawką, 0 wartości modelu, 0 żądań, inną sygnaturę cache niż `hybrid` (analizy policzone z modelem nie
są serwowane jako trafienie po wycofaniu) i komponent `disabled`. Zapisane w bazie analizy z kandydatami modelu
zostają (snapshot jest źródłem prawdy, oznaczenie „odczyt automatyczny” w UI/PDF zostaje).

### 10.4. Usunięcie trybu `legacy`

`legacy` zostaje przez **jedno wydanie** po przełączeniu jako tryb wycofania. W następnym wydaniu — jeśli wycofania nie
użyto — usuwa się: tryb `legacy` z `ParserMode`/ustawień, sklejanie segmentów (`find_zone_sections`/`extract_parameters`
w `mpzp_parser.py`, wzorce struktury w `mpzp_parser_segment.py`), wpisy `:legacy` migawki i `mode_overrides` w
`tests/fixtures/mpzp/*/expected.json`; trybem wycofania (`rollback_mode`) staje się wtedy `v3`. Silnik ekstrakcji jest
już jeden (`quantity_engine` + `descriptive_engine`), więc usunięcie `legacy` nie zmienia wartości, tylko zakres strefy.
