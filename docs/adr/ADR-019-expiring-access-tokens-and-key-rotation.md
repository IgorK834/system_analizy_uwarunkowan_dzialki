# ADR-019: Wygasające tokeny dostępu v2, rotacja kluczy `kid` i ochrona tokenu w logach

- Status: **Zaakceptowany technicznie (2026-10-08)**
- Zakres: AU-012 (Task 21.12, audyt pozycje R4 i B14).
- Powiązane: ADR-017 (token dla `POST /analyze/resume`, limiter), ADR-015 (kontrakt błędów, `request_id`), ADR-001 (granice modułów).

## Kontekst

Token dostępu do raportu, pakietu audytowego i dokumentu uchwały był HMAC-SHA256 samego `analysis_id` (`analysis-access:v1:<id>`):

1. **Bez wygasania.** Raz wydany link działał wiecznie; jedyny sposób unieważnienia to zmiana `ACCESS_TOKEN_SECRET`.
2. **Zmiana sekretu unieważniała wszystkie linki naraz.** Rotacja sekretu (np. po wycieku) kosztowała każdego użytkownika
   z zapisanym linkiem.
3. **Token w query-stringu.** `GET /report/1?access_token=…` trafiał do dziennika dostępu uvicorna (`uvicorn.access`, własny
   handler z `propagate=False`, więc filtr redakcji sekretów z handlera aplikacji go nie obejmował) i do nagłówka `Referer`.

## Decyzje

### 1. Format `v2.<exp>.<kid>.<sig>`

- `exp` — koniec ważności, sekundy UNIX (cyfry, najwyżej 12); `kid` — identyfikator klucza (`[A-Za-z0-9_-]{1,32}`);
  `sig` — `base64url(HMAC-SHA256(klucz[kid], "analysis-access:v2:" + analysis_id + "|" + exp))` bez dopełnienia (43 znaki).
  Termin jest w podpisie, więc nie da się go przedłużyć; `analysis_id` też, więc token nie działa dla sąsiednich identyfikatorów.
- Weryfikacja (`app/core/access_control.py`, `check_analysis_token`): kształt → klucz po `kid` → podpis (porównanie na bajtach,
  stały czas) → okres przejściowy klucza → `exp > teraz`. Powód odrzucenia (`malformed`, `unknown_kid`, `bad_signature`,
  `key_retired`, `expired`) trafia tylko do logu (INFO), **nigdy do odpowiedzi**: brak tokenu, token błędny, cudzy i wygasły dają
  ten sam `403 Brak dostępu do tej analizy.` — niezależnie od tego, czy analiza istnieje (ADR-017 §1 bez zmian).
- Tokeny v1 (bez `exp`) **nie są już akceptowane**: ich zachowanie (wieczna ważność) jest właśnie defektem. Skutek: linki wydane
  przed wdrożeniem przestają działać; interfejs wydaje nowy token przy każdej odpowiedzi analizy (także z cache), więc wystarczy
  ponowić analizę.
- Pole `AnalyzeResponse.access_token` jest polem obliczanym przy serializacji, więc **nie zmieniają się pola kontraktu** —
  `*_SCHEMA_VERSION`, `RESULT_CONTRACT_VERSION` i `docs/report/field-mapping.md` bez zmian (`access_token` jest tam wykluczony).
  Zmienił się tylko opis pola i jego wartość (nowy format).

### 2. Okresy ważności

| Token | Skąd | Domyślnie | Zmienna |
|---|---|---|---|
| `access_token` w odpowiedzi analizy, link „Udostępnij” (`purpose=share`) | `POST /analyze`, `POST /analyze/{id}/links` | 30 dni | `ACCESS_TOKEN_TTL_SECONDS` |
| bezpośrednie pobranie (`purpose=download`), `preview_path` dokumentu uchwały | `POST /analyze/{id}/links`, `manual_zone_context` | 15 min | `ACCESS_TOKEN_DOWNLOAD_TTL_SECONDS` |

Token z odpowiedzi analizy ma 30 dni, bo interfejs trzyma go w pamięci karty i używa do pobrań oraz wznowienia analizy;
15 minut zepsułoby pobranie po przerwie. Krótki token jest tam, gdzie adres musi go nieść w query-stringu (`href` w atrybucie,
który nie może dołączyć nagłówka).

### 3. `POST /analyze/{id}/links`

- Wymaga ważnego tokenu (nagłówek `X-Analysis-Token` albo `access_token`); 403 przed jakimkolwiek odczytem bazy, `404` tylko po
  poprawnym tokenie. Ciało opcjonalne: `{"purpose": "download" | "share"}`.
- **Brak eskalacji:** `exp` wydanego tokenu to `min(teraz + TTL, exp tokenu użytego do wydania)`. Krótki token nie wyda
  30-dniowego, a ostatni token w łańcuchu wygasa razem z pierwszym. Wydawanie kolejnych 15-minutowych linków z ważnego tokenu
  jest dozwolone — kto ma ważny token, ma już dostęp.
- Odpowiedź: `access_token`, `expires_at`, `expires_in_seconds`, `report_url`, `audit_package_url` (ścieżki względem API).
  Limit zapytań jak dla raportu (`rate_limit_report_per_minute`).

### 4. Token w nagłówku zamiast w adresie

`GET /report/{id}`, `GET /report/{id}/audit.zip` i `GET /analyze/{id}/pending-document` przyjmują token także w nagłówku
`X-Analysis-Token` (jak `POST /analyze/resume`). Frontend (`getAnalysisReport`, `getAnalysisAuditPackage`) wysyła go nagłówkiem,
więc pobrania z interfejsu nie zostawiają tokenu w żadnym adresie, dzienniku ani `Referer`. Parametr `access_token` zostaje dla
linków do pobrania i udostępniania (`links`).

### 5. Maskowanie w dzienniku dostępu i nagłówki odpowiedzi

- `AccessTokenMaskFilter` na loggerze `uvicorn.access` (dodawany w `configure_logging()`, idempotentnie, **bez dotykania
  handlerów uvicorna** — `dictConfig` z wpisem tego loggera usunąłby je i zgasił dziennik dostępu) zamienia wartość parametru
  `access_token` w argumentach rekordu na `***`: `GET /report/1?access_token=***`. Nazwę parametru rozkodowujemy
  (`access%5Ftoken` uwierzytelnia tak samo i też jest maskowane). Redakcja sekretów handlera aplikacji (`<redacted>`) działa
  dalej i obejmuje teraz także całą listę `ACCESS_TOKEN_SECRETS=…` (przecinek należy do wartości).
- `SensitiveResponseHeadersMiddleware` (czysty ASGI) dopisuje `Referrer-Policy: no-referrer` i `Cache-Control: private, no-store`
  do **wszystkich** odpowiedzi (także błędów) ścieżek `/report/…`, `/analyze` i `/analyze/…` — czyli raportu, pakietu, dokumentu
  uchwały, `links`, `resume` i samego `POST /analyze` (jego odpowiedź niesie `access_token`). Routery ustawiają te same nagłówki
  jawnie (`SENSITIVE_RESPONSE_HEADERS`); `pending-document` miał `private, max-age=300` — zmienione na `no-store`.

### 6. Rotacja kluczy bez unieważniania linków

- `ACCESS_TOKEN_SECRETS=kid1:sekret1,kid2:sekret2|RRRR-MM-DD`: **pierwszy wpis podpisuje**, pozostałe tylko weryfikują. Opcjonalny
  przyrostek `|data` kończy okres przejściowy klucza (sama data = do końca tego dnia UTC; pełny znacznik ISO 8601 jest brany
  dosłownie). Bez daty klucz działa, dopóki operator nie usunie wpisu. Klucz aktywny nie może mieć daty.
- Walidacja przy starcie (`Settings`): zły format, powtórzony `kid`, pusty sekret, zła data → start zatrzymany; komunikaty nie
  powtarzają sekretów. Pojedynczy `ACCESS_TOKEN_SECRET` działa dalej jako klucz `default`, gdy lista jest pusta; bez żadnego
  sekretu proces używa losowego klucza `ephemeral` (ostrzeżenie w logu; tokeny nie przeżywają restartu ani nie działają
  między workerami).
- Procedura: (1) dopisz nowy klucz **na początku** listy, stary zostaw z datą końca (np. `|` + data = termin 30-dniowego linku
  + zapas); (2) wdróż — nowe tokeny mają nowy `kid`, stare działają; (3) po dacie stare tokeny dają 403 (`key_retired`), a wpis
  można usunąć. Wyciek klucza: usuń go od razu z listy (`unknown_kid`) — to jedyny sposób odwołania (ograniczenie niżej).

## Konsekwencje i ograniczenia

- **Brak odwołania pojedynczego tokenu.** Tokeny są bezstanowe: nie ma listy unieważnień ani powiązania z użytkownikiem.
  Odwołanie to usunięcie klucza (wszystkie tokeny tego `kid`) albo upływ `exp`. Lista unieważnień wymagałaby tabeli i osobnej decyzji.
- **Link „Udostępnij” 30 dni nadal niesie token w adresie.** Zmniejszono skutki (maskowanie w logach, `no-referrer`, `no-store`,
  wygasanie), nie usunięto ryzyka kopiowania adresu. Domyślnym kanałem interfejsu jest nagłówek.
- **Odnośnik do dokumentu uchwały (`preview_path`) ma 15 minut.** Panel ręcznej strefy odświeża go z każdą odpowiedzią/odczytem
  analizy; po dłuższym otwarciu karty kliknięcie daje 403, a ponowne wczytanie analizy (cache) wydaje nowy odnośnik.
- **Zegar.** `exp` porównujemy z zegarem serwera; skos między workerami o sekundy nie ma znaczenia przy tych okresach.
- Wszyscy workery muszą mieć tę samą listę `ACCESS_TOKEN_SECRETS` (jak dotąd `ACCESS_TOKEN_SECRET`).
- Niezmienione (osobne decyzje): limiter w procesie, port 8000 publikowany na wszystkich interfejsach (ADR-017).
