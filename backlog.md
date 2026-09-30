# Backlog rozwoju — System analizy uwarunkowań przestrzennych działki

> Dokument opisuje **wyłącznie przyszłe prace od aktualnego stanu repozytorium**. Nie jest kopią ani reorganizacją istniejących GitHub Issues i nie należy interpretować jego numeracji jako kontynuacji wcześniejszych zadań.
>
> Bazowy stan repozytorium: branch `main`, commit `64ca3e85ab74ae811280fda01e485e89cdf4f29e` z 2026-09-03.
>
> Dokument przygotowano dla pracy inżynierskiej pt. **„System analizy uwarunkowań przestrzennych działki”**.

---

## 1. Cel pracy i kryterium sukcesu

Celem pracy nie jest odtworzenie komercyjnego serwisu OnGeo 1:1 ani zbudowanie możliwie największej liczby warstw mapowych. Wartość akademicka systemu powinna wynikać z rozwiązania problemu inżynierskiego:

> **W jakim stopniu heterogeniczne publiczne źródła danych przestrzennych i dokumenty planistyczne pozwalają automatycznie, poprawnie, audytowalnie i odtwarzalnie określić uwarunkowania przestrzenne konkretnej działki?**

System powinien udowodnić, że potrafi przejść pełną ścieżkę:

`identyfikacja działki → pozyskanie źródeł → normalizacja → analiza GIS → interpretacja domenowa → ocena kompletności/pewności → wizualizacja → trwały snapshot → raport PDF → walidacja względem danych referencyjnych`.

### 1.1. Kryteria sukcesu pracy inżynierskiej

Praca jest kompletna, gdy system:

1. analizuje działkę na podstawie **kilku klas danych o różnym charakterze**, co najmniej:
   - dane działki i geometrię,
   - planowanie przestrzenne: MPZP oraz POG,
   - ograniczenia środowiskowe: powódź i formy ochrony przyrody,
   - rzeźbę terenu,
   - co najmniej jedną klasę danych infrastrukturalnych lub transportowych;
2. korzysta z kilku typów źródeł/protokołów, np. REST, WFS/GML, WMS, CSW, pliki APP/GML, dokument PDF/OCR oraz raster wysokościowy;
3. wykonuje obliczenia geometryczne w kanonicznym układzie metrycznym `EPSG:2180`;
4. odróżnia:
   - „sprawdzono i nie znaleziono”,
   - „źródło nie zawiera danych dla obszaru”,
   - „źródło jest niedostępne”,
   - „wynik jest niejednoznaczny”,
   - „wynik wymaga ręcznej weryfikacji”;
5. zachowuje pochodzenie danych, datę pobrania, identyfikator wydania i ograniczenia źródła;
6. umożliwia odtworzenie raportu z zapisanego snapshotu bez ponownego wykonywania analizy;
7. jest oceniony na **rzeczywistym zbiorze referencyjnym działek** z użyciem mierzalnych metryk;
8. posiada eksperymenty pokazujące poprawność, odporność na błędy, wydajność i odtwarzalność.

---

## 2. Rola stron referencyjnych

### 2.1. OnGeo — wzorzec organizacji raportu, nie zakres obowiązkowy

Źródło referencyjne:
- https://ongeo.pl/raporty/przykladowy-raport/FB5CE893C584CB976177B832C9924DE4

OnGeo pokazuje docelową klasę produktu: jeden raport agreguje informacje planistyczne, środowiskowe, terenowe, infrastrukturalne, geologiczne, komunikacyjne i rynkowe. Dla pracy inżynierskiej najważniejsze są z niego trzy wzorce:

- **czytelna segmentacja raportu** według grup uwarunkowań;
- mapa + tabela + opis znaczenia wyniku;
- jawne źródła, aktualność danych i możliwość eksportu.

Nie należy próbować implementować wszystkich kategorii OnGeo przed obroną. Warstwy takie jak klimat, ceny nieruchomości, rozbudowane POI, hydrogeologia czy pełna analiza rynku mają niższy priorytet niż poprawność rdzenia GIS i walidacja naukowo-inżynierska.

### 2.2. rejestrplanowogolnych.pl — wzorzec interaktywnej prezentacji POG

Źródło referencyjne:
- https://rejestrplanowogolnych.pl/?teryt=246101_1

Najważniejsze elementy do odwzorowania funkcjonalnie, bez kopiowania wyglądu:

- tematyczne kolorowanie stref według parametrów;
- szybkie przełączanie trybu mapy bez ponownego pobierania danych;
- prezentacja co najmniej:
  - rodzaju strefy,
  - maksymalnej nadziemnej intensywności zabudowy,
  - maksymalnego udziału powierzchni zabudowy,
  - maksymalnej wysokości zabudowy,
  - minimalnego udziału powierzchni biologicznie czynnej;
- OUZ, OZS i standardy dostępności infrastruktury społecznej jako osobne obiekty/warstwy;
- legenda generowana z tych samych reguł, które sterują stylem mapy;
- analiza udziałów powierzchniowych stref, a nie wyłącznie wskazanie jednej strefy dominującej.

### 2.3. Rejestr Urbanistyczny — źródło urzędowe i wzorzec provenance

Źródła referencyjne:
- https://rejestr-urbanistyczny.gov.pl/network-services
- https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe

Rejestr Urbanistyczny powinien być traktowany jako źródło urzędowe dla danych APP, a nie jako wzorzec interfejsu. Repozytorium ma już zamrożone realne kontrakty WMS 1.3.0, WFS 2.0.0 oraz CSW 2.0.2, w tym rzeczywistą odpowiedź dla POG Bielska-Białej. Następne prace powinny przekształcić te kontrakty z materiału testowego w kontrolowany kanał danych runtime/import.

---

## 3. Aktualny stan bazowy — funkcje, których backlog nie implementuje ponownie

Poniższe elementy istnieją w aktualnym `main` i są punktem startowym dalszego rozwoju:

- FastAPI + Next.js + PostgreSQL/PostGIS uruchamiane przez Docker Compose;
- identyfikacja działki przez mapę, adres i identyfikator działki;
- geometria działki w `EPSG:2180`, metryki i GeoJSON dla frontendu;
- techniczne odsunięcie od granic działki;
- cache analiz i trwały zapis wyniku;
- analiza kontekstu uruchamiana równolegle;
- ISOK — rzeczywiste przecięcia powierzchniowe obszarów zagrożenia powodziowego;
- GDOŚ — rzeczywiste przecięcia form ochrony przyrody;
- NMT — minimalna, maksymalna wysokość i deniwelacja działki;
- KIUT/GESUT — kod adaptera i warstwa podglądowa, przy czym kontrakt wektorowy i semantyka błędów wymagają dalszego uporządkowania;
- discovery MPZP przez KIMPZP;
- bezpieczne pobieranie dokumentów MPZP;
- ekstrakcja PDF/HTML/OCR oraz parser parametrów MPZP;
- zapis audytu parsera dokumentów;
- analiza POG/OUZ z obliczaniem rzeczywistych przecięć powierzchniowych;
- domenowy `PogAnalysisResult` zawierający listę wszystkich stref POG;
- uproszczony `PogResult`, który obecnie przekazuje dalej głównie strefę dominującą;
- wersjonowany model źródeł, artefaktów i wydań danych z SHA-256;
- importy danych przestrzennych z mechanizmem aktywnego `data_release`;
- frontend z MapLibre, warstwami GeoJSON wyniku, panelem wyników i przełącznikami;
- raport PDF generowany z zapisanego snapshotu analizy;
- CI budujące frontend/backend i wymagające min. 80% coverage.

### Najważniejsze istniejące ograniczenia, które wyznaczają początek backlogu

1. Bogaty wynik POG jest redukowany do strefy dominującej przed API, bazą, UI i PDF.
2. Rejestr Urbanistyczny ma już potwierdzone fixtury kontraktów, ale część konfiguracji nadal opisuje go jako źródło eksperymentalne.
3. MPZP jest nadal w dużej mierze ścieżką `discovery → symbol kandydujący → dokument`, zamiast deterministycznego przecięcia działki z lokalną geometrią stref, gdy wektor jest dostępny.
4. NMT działa w backendzie, ale wynik nie jest jeszcze pierwszoklasową sekcją `AnalyzeResponse`/UI/PDF.
5. Testowy zestaw działek jest w dużej mierze syntetyczny i zawiera nieaktualne komentarze `TODO`; brakuje formalnego realnego zbioru referencyjnego do oceny jakości systemu.
6. KIUT może zamienić błąd transportu na pustą listę, co nie daje wystarczającej podstawy do twierdzenia, że sieci nie występują.
7. Brakuje ilościowej ewaluacji całego systemu względem ręcznie zweryfikowanego ground truth.

---

## 4. Pytania badawczo-inżynierskie

W implementacji i rozdziale ewaluacyjnym pracy należy zebrać wyniki pozwalające odpowiedzieć na następujące pytania:

### RQ1 — poprawność przestrzenna
Jak dokładnie system odtwarza ręcznie zweryfikowane przecięcia działki z obiektami planistycznymi i ograniczeniami przestrzennymi?

### RQ2 — znaczenie metody geometrycznej
Jak często przypisanie na podstawie centroidu prowadzi do innego wyniku niż analiza pełnego przecięcia powierzchniowego działki?

### RQ3 — odporność na niepełne źródła
Czy awaria, brak pokrycia lub zmiana kontraktu zewnętrznej usługi prowadzi do poprawnego statusu `unknown/partial/unavailable`, zamiast do fałszywego „brak ograniczenia”?

### RQ4 — ekstrakcja dokumentów planistycznych
Jaka jest trafność automatycznej ekstrakcji parametrów MPZP z dokumentów tekstowych, tabelarycznych i skanowanych?

### RQ5 — odtwarzalność
Czy analizę można jednoznacznie odtworzyć na podstawie zapisanego snapshotu, wersji źródeł i identyfikatorów wydań danych?

### RQ6 — koszt wydajnościowy
Jaka jest różnica czasu odpowiedzi między analizą opartą na usługach zewnętrznych, lokalnych danych PostGIS i cache?

---

## 5. Priorytety

- **P0 — obowiązkowe do obrony:** bez tego zakres pracy lub jej ewaluacja jest niepełna.
- **P1 — istotne rozszerzenie:** znacząco podnosi jakość i wartość demonstracyjną, ale może zostać ograniczone przy braku czasu.
- **P2 — po pracy / stretch:** wartość produktowa, nie warunek mocnej pracy inżynierskiej.

### 5.1. Definition of Done dla każdej nowej analizy przestrzennej

Moduł analityczny można uznać za zakończony dopiero, gdy:

- źródło jest opisane w `docs/data_sources/catalog.yaml` wraz z właścicielem, protokołem, CRS, zasadami dostępu, datą weryfikacji i atrybucją;
- niepotwierdzone źródło nie jest uruchamiane jako produkcyjne;
- dane obliczeniowe są przeliczane do `EPSG:2180`;
- obliczenie używa realnej geometrii, a nie rastra WMS, jeśli raportuje pole, odległość lub przecięcie;
- istnieją rozłączne stany co najmniej `available`, `no_match`, `unavailable/unknown`;
- wynik zawiera provenance i datę danych;
- błędy zewnętrzne nie są mapowane na „brak ograniczenia”;
- są testy jednostkowe, kontraktowe i co najmniej jeden przypadek w realnym korpusie ewaluacyjnym;
- pole jest widoczne w API, UI i PDF, jeśli ma znaczenie dla użytkownika;
- znane ograniczenia są opisane w raporcie i dokumentacji.

---

# 6. Backlog wykonawczy

## ETAP A — Fundament ewaluacji akademickiej

### BK-001 — Zamrożenie i udokumentowanie aktualnego baseline

**Priorytet:** P0  
**Cel:** stworzyć jednoznaczny punkt odniesienia dla dalszych zmian i rozdziału implementacyjnego pracy.

**Zakres:**
- dodać `docs/current_state.md` z architekturą systemu na commicie bazowym;
- udokumentować przepływ `/analyze` od identyfikacji działki do persistence i PDF;
- wypisać istniejące źródła, ich status produkcyjny/badawczy i kanał danych;
- zaktualizować stare komentarze w fixtures, które twierdzą, że istniejące już serwisy nie zostały zaimplementowane;
- zapisać wersje kluczowych zależności i sposób reprodukcji testów;
- wykonać pełny CI lokalnie/Docker i zachować raport coverage jako baseline.

**Kryteria akceptacji:**
- dokument nie opisuje funkcji planowanych jako istniejące ani istniejących jako planowane;
- wszystkie nieaktualne `TODO` w korpusie testowym są poprawione lub oznaczone datą i rzeczywistym ograniczeniem;
- podana jest dokładna wersja `main` stanowiąca bazę porównania.

**Wartość akademicka:** umożliwia rzetelne rozdzielenie „stan początkowy → wykonane rozszerzenia → wyniki eksperymentów”.

---

### BK-002 — Rzeczywisty korpus referencyjny działek

**Priorytet:** P0  
**Cel:** zastąpić ocenę „na oko” zbiorem referencyjnym, na którym można policzyć jakość systemu.

**Zakres minimalny:**
- co najmniej **24 rzeczywiste działki**, rekomendowane 30;
- co najmniej 5 gmin i 3 województwa;
- przypadki miejskie i wiejskie;
- różne rozmiary i kształty działek;
- reprezentacja:
  - jedna strefa POG,
  - co najmniej dwie działki wielostrefowe POG,
  - OUZ: wewnątrz / poza / styczność z granicą / częściowe przecięcie,
  - POG prawnie wiążący oraz projekt/in-progress/unknown, jeśli są dostępne,
  - MPZP z wiarygodnym wektorem,
  - MPZP wymagający parsowania dokumentu,
  - przypadek rastrowy/manualny,
  - powódź: brak / przecięcie / granica,
  - forma ochrony przyrody: brak / przecięcie,
  - teren płaski oraz działka o wyraźnej deniwelacji.

**Dane w fixture:**
- `parcel_identifier`;
- TERYT;
- data ręcznej weryfikacji;
- źródła referencyjne;
- oczekiwane klasy/strefy;
- oczekiwane pola i udziały z tolerancją;
- oczekiwany stan `available/unknown/manual_review`;
- notatka o niejednoznacznościach.

Nie przechowywać danych osobowych EGiB ani materiałów bez prawa redystrybucji.

**Kryteria akceptacji:**
- każdy przypadek ma oczekiwany wynik domenowy, a nie tylko dane wejściowe;
- każdy wynik referencyjny ma opis sposobu ręcznego ustalenia;
- korpus może być używany offline w testach regresyjnych.

**Wartość akademicka:** podstawowy zbiór badawczy pracy.

---

### BK-003 — Protokół ręcznego ground truth

**Priorytet:** P0  
**Zależność:** BK-002

**Cel:** zdefiniować procedurę, według której „poprawny wynik” jest ustalany niezależnie od implementacji systemu.

**Zakres:**
- dokument `docs/evaluation/ground_truth_protocol.md`;
- dla geometrii: QGIS/PostGIS i ten sam CRS `EPSG:2180`;
- dla POG: oficjalny Rejestr Urbanistyczny/GML/WFS;
- dla MPZP: oficjalna geometria, uchwała i dokument źródłowy;
- dla ISOK/GDOŚ/NMT: oficjalna usługa + data pobrania;
- opisać tolerancję numeryczną pól, procentów i odległości;
- 20% przypadków zweryfikować ponownie w odrębnej sesji; jeżeli jest możliwość — przez drugą osobę;
- zdefiniować procedurę oznaczania przypadku jako `ambiguous` zamiast wymuszania odpowiedzi.

**Kryteria akceptacji:** inna osoba, dysponując dokumentem i źródłami, może odtworzyć wartość referencyjną.

---

### BK-004 — Harness automatycznej ewaluacji

**Priorytet:** P0  
**Zależność:** BK-002, BK-003

**Cel:** zamienić korpus w mierzalny eksperyment.

**Implementacja:**
- `backend/scripts/evaluate_reference_corpus.py`;
- wejście: zamrożony korpus i snapshot źródeł;
- wyjście: JSON + CSV + Markdown;
- metryki:
  - skuteczność identyfikacji działki,
  - accuracy klasy strefy,
  - MAE i maksymalny błąd bezwzględny udziału powierzchniowego,
  - precision/recall/F1 dla warunków binarnych,
  - udział wyników `unknown`, `partial`, `manual_review_required`,
  - kompletność pól,
  - p50/p95 czasu analizy,
  - cache hit/miss;
- wyniki zapisywać w `docs/evaluation/results/`.

**Kryteria akceptacji:** jedno polecenie generuje komplet tabel użytecznych bezpośrednio w rozdziale wyników pracy.

---

## ETAP B — Rejestr Urbanistyczny jako główne źródło POG

### BK-101 — Synchronizacja katalogu źródeł z potwierdzonym kontraktem RU

**Priorytet:** P0

**Problem:** repo ma realne, zamrożone fixtury WMS/WFS/CSW RU z 2026-09-03, natomiast część konfiguracji nadal traktuje RU jako niepotwierdzone źródło eksperymentalne.

**Zakres:**
- zweryfikować ponownie oficjalną stronę usług sieciowych;
- zaktualizować `docs/data_sources/catalog.yaml` o:
  - endpoint WMS POG 1.3.0,
  - endpoint WFS POG 2.0.0,
  - endpoint CSW 2.0.2,
  - dostępne CRS,
  - limity `CountDefault`,
  - warunki publicznego dostępu,
  - datę weryfikacji;
- dopiero po potwierdzeniu dostępu/licencji ustawić źródło jako `production_ready`;
- usunąć nieaktualny komentarz w `settings.py`, który twierdzi, że publiczny kontrakt RU nie jest potwierdzony;
- test katalogu ma porównywać deklarację z zamrożonymi capabilities.

**Kryteria akceptacji:** runtime/import nie opiera się na ręcznie wpisanym, niezweryfikowanym URL; kontrakt katalogu i fixtury są zgodne.

**Wartość akademicka:** pokazuje kontrolowaną integrację systemu z urzędowym, ewoluującym API OGC.

---

### BK-102 — Bezpieczny wspólny klient OGC dla RU

**Priorytet:** P0  
**Zależność:** BK-101

**Zakres:**
- wydzielić klienta WFS/WMS/CSW w warstwie `infrastructure` modularnego monolitu;
- WFS 2.0:
  - jawne `typeNames`, `srsName`, `count`, `startIndex`,
  - paginacja respektująca `CountDefault=100`,
  - maksymalna liczba stron/cech,
  - wykrywanie `ExceptionReport` także przy HTTP 200;
- WMS 1.3:
  - poprawna obsługa osi i CRS,
  - wyłącznie do prezentacji/discovery, nie do obliczeń pól;
- CSW 2.0.2:
  - jawne negocjowanie wersji,
  - discovery metadanych/dokumentów;
- bezpieczeństwo:
  - allowlista hostów,
  - timeouty,
  - limity odpowiedzi,
  - brak automatycznego follow redirect do obcego hosta,
  - parser XML bez encji zewnętrznych;
- każde wywołanie tworzy `SourceMetadata`/provenance.

**Kryteria akceptacji:** wszystkie testy kontraktowe działają offline, a testy integracyjne na mocku pokrywają paginację, timeout, błąd HTTP, `ExceptionReport` i niepoprawny XML.

---

### BK-103 — Pełne mapowanie domenowe obiektów POG z WFS RU

**Priorytet:** P0  
**Zależność:** BK-102

**Obiekty:**
- AktPlanowaniaPrzestrzennego,
- StrefaPlanistyczna,
- ObszarUzupelnieniaZabudowy,
- ObszarZabudowySrodmiejskiej,
- ObszarStandardowDostepnosciInfrastrukturySpolecznej,
- DokumentFormalny.

**Zakres:**
- stabilne identyfikatory i wersje obiektów;
- status aktu bez lokalnego zgadywania z dat;
- geometria transformowana do `EPSG:2180`;
- cztery parametry stref jako wartości liczbowe z jednostkami:
  - maksymalna nadziemna intensywność zabudowy,
  - maksymalna wysokość zabudowy,
  - maksymalny udział powierzchni zabudowy,
  - minimalny udział powierzchni biologicznie czynnej;
- `NULL` pozostaje `NULL`, nigdy `0`;
- profile funkcjonalne przechowywać jako kod + etykieta + źródło słownika;
- projekty i akty w toku muszą zachować niewiążący status.

**Kryteria akceptacji:** realna cecha Bielska-Białej przechodzi parser bez specjalnego warunku zakodowanego dla jednej gminy.

---

### BK-104 — Wersjonowany import POG z RU do PostGIS

**Priorytet:** P0  
**Zależność:** BK-103

**Zakres:**
- wykorzystać istniejące `DataSource`, `SourceArtifact`, `DataRelease`, `ImportRun`;
- pobrany artefakt zachować z SHA-256;
- importować dane do nieaktywnego wydania (każde wydanie jest kompletnym snapshotem
  wszystkich aktów paczki, także niezmienionych — `docs/adr/ADR-008-pog-release-complete-snapshot.md`);
- walidacja przed publikacją:
  - geometria poprawna i niepusta,
  - SRID,
  - relacja stref do aktu,
  - duplikaty identyfikatorów,
  - wartości procentowe 0–100,
  - wartości nieujemne,
  - statystyka liczby cech względem poprzedniego wydania;
- aktywacja wydania atomowa;
- wcześniejsze wydanie pozostaje dostępne dla audytu;
- MVP może importować wybrane gminy z korpusu; pełen import krajowy nie jest wymagany do obrony.

**Kryteria akceptacji:** nieudane QA nie zmienia aktywnego wydania; ponowny import identycznego artefaktu jest idempotentny.

---

### BK-105 — Kontrakt POG v2: wszystkie strefy jako dane pierwszoklasowe

**Priorytet:** P0  
**Zależność:** BK-103

**Problem:** `PogAnalysisResult` już liczy wszystkie strefy, ale obecny `PogResult` redukuje wynik do dominującej strefy.

**Docelowy kontrakt:**

```text
PogResult
  legal_status
  coverage_status
  act
  zones[]
    symbol / type / label
    area_sqm
    area_pct
    max_overground_floor_area_ratio
    max_building_height_m
    max_building_coverage_pct
    min_biologically_active_pct
    primary_profile
    additional_profiles[]
    source
  dominant_zone_id?   # wartość pochodna, opcjonalna
  ouz[]
  downtown_areas[]
  social_infrastructure_standard_areas[]
  source
```

**Zakres implementacji:**
- Pydantic;
- ORM i migracja;
- persistence;
- cache;
- odczyt starej analizy, jeśli jest wymagany;
- typy TypeScript;
- frontend;
- raport PDF.

**Kryteria akceptacji:** round-trip `analyze → DB → cache → API → PDF` dla działki trzy-strefowej zachowuje wszystkie strefy i wszystkie wartości bez agregowania.

---

### BK-106 — Rozdzielenie statusu prawnego od kompletności danych

**Priorytet:** P0  
**Zależność:** BK-105

**Cel:** zapobiec błędnemu wnioskowi „brak geometrii = brak POG”.

**Model przykładowy:**
- `legal_status`: `binding | project | in_progress | superseded | unknown`
  (`outdated` jest wyłącznie aliasem `superseded` — rozstrzygnięcie w
  `docs/adr/ADR-002-pog-legal-status-and-coverage.md`);
- `coverage_status`: `available | partial | act_without_spatial_data | no_act_confirmed | unknown`.

**Reguły:**
- status prawny pochodzi tylko z właściwego źródła urzędowego;
- pusta odpowiedź WFS nie może sama zmienić statusu na `no_act_confirmed`;
- awaria upstream → `unknown` lub zachowanie ostatniej potwierdzonej wartości z datą;
- projekt nigdy nie może być prezentowany językiem „obowiązuje”.

**Kryteria akceptacji:** test tablicowy pokrywa wszystkie istotne kombinacje status/coverage.

---

### BK-107 — Provenance aktu i dokumentów RU

**Priorytet:** P1  
**Zależność:** BK-102, BK-103

**Zakres:**
- powiązać WFS, CSW i dokumenty formalne jednym identyfikatorem aktu/wersji;
- dla wyniku przechowywać oficjalne linki do karty aktu/GML/dokumentów;
- zapisywać identyfikator publikacji, początek wersji, czas pobrania i release;
- udostępnić źródła w panelu i PDF.

**Kryteria akceptacji:** użytkownik może z wyniku przejść do źródła urzędowego, a zapisany raport wskazuje dokładną wersję danych.

---

## ETAP C — MPZP: od discovery do deterministycznego przypisania przestrzennego

### BK-201 — Audyt i katalog wektorowych źródeł MPZP

**Priorytet:** P0

**Cel:** nie zakładać istnienia jednego krajowego wektora stref MPZP bez potwierdzenia.

**Zakres:**
- zweryfikować aktualne kanały RU/KIMPZP/gmin dla minimum gmin z korpusu;
- dla każdego kanału zapisać capabilities, CRS, typy obiektów i licencję;
- sklasyfikować gminy:
  - wektor stref dostępny,
  - tylko granica aktu + dokument,
  - raster,
  - brak wiarygodnego źródła;
- dodać fixtury kontraktowe tak samo jak dla RU POG.

**Kryteria akceptacji:** żadna ścieżka produkcyjna MPZP nie używa wymyślonego `typeName` ani nie traktuje WMS jako geometrii obliczeniowej.

---

### BK-202 — Lokalny wektor MPZP i przecięcia powierzchniowe

**Priorytet:** P0  
**Zależność:** BK-201

**Zakres:**
- import dostępnych stref/granic MPZP do modelu wersjonowanego;
- selekcja kandydatów przez GiST/BBOX;
- `ST_Intersection` oraz `ST_Area` w `EPSG:2180`;
- zwracać wszystkie strefy przecinające działkę;
- styczność granicy oznaczać osobno;
- strefa dominująca jest wyłącznie polem pomocniczym;
- gdy brak wektora — zachować istniejącą ścieżkę dokument/raster/manual, ale jawnie obniżyć confidence.

**Kryteria akceptacji:** działka przecięta przez dwie strefy zwraca obie z udziałem, a wynik nie zależy od położenia centroidu.

---

### BK-203 — Powiązanie strefy wektorowej z parserem uchwały

**Priorytet:** P0  
**Zależność:** BK-202

**Cel:** rozdzielić dwa problemy: „gdzie leży działka?” oraz „jakie parametry ma ta strefa?”.

**Zakres:**
- symbol strefy z geometrii jest wejściem parsera dokumentu;
- każdy wyekstrahowany parametr zachowuje:
  - wartość znormalizowaną,
  - wartość surową,
  - jednostkę,
  - fragment dowodowy,
  - stronę/segment,
  - hash dokumentu,
  - confidence;
- sprzeczne wartości → conflict + manual review; bez automatycznego wyboru;
- OCR zawsze obniża confidence względem równoważnego tekstowego PDF.

**Kryteria akceptacji:** panel i PDF pozwalają wskazać, z którego miejsca uchwały pochodzi wartość kluczowego parametru.

---

### BK-204 — Uporządkowany fallback MPZP rastrowy/manualny

**Priorytet:** P0  
**Zależność:** BK-201

**Zakres:**
- zachować istniejący flow `waiting_for_user_input/resume` (w bazie status
  `waiting_for_zone_symbol`; bez konkurencyjnego statusu — rozstrzygnięcie w
  `docs/adr/ADR-005-manual-mpzp-zone-and-mpzp-pog-compatibility.md`);
- użytkownik musi widzieć obraz źródłowy, plan i kandydatów, zanim poda symbol;
- wszystkie parametry zależne od ręcznego symbolu otrzymują `manual_review_required=true`;
- analiza nie może mieć statusu `complete`, jeśli identyfikacja strefy była ręczna;
- PDF pokazuje wyraźną informację „symbol strefy podano ręcznie”.

---

### BK-205 — Bezpieczny model relacji MPZP–POG

**Priorytet:** P0  
**Zależność:** BK-105, BK-202

**Cel:** nie tworzyć w interfejsie pozornej „opinii prawnej”.

**Zakres:**
- zastąpić uproszczony boolean `conflict_with_mpzp` bogatszym `compatibility_assessment`
  (historyczny boolean pozostaje wyłącznie jako `legacy_evidence` ze statusem
  `unknown` — ADR-005);
- wynik: `compatible | incompatible | uncertain | not_applicable | unknown`;
- wynik ma zawierać datę stanu prawnego, jawne reguły, źródło i uzasadnienie;
- nigdy nie agregować parametrów różnych stref do jednej średniej;
- UI/PDF ma prezentować MPZP i POG jako osobne ustalenia, a ocenę zgodności jako analizę informacyjną.

---

## ETAP D — Uwarunkowania środowiskowe, terenowe i infrastrukturalne

### BK-301 — NMT jako pierwszoklasowy wynik analizy

**Priorytet:** P0

**Problem:** backend pobiera Hmin/Hmax i deniwelację, ale wynik nie jest eksponowany jako sekcja końcowa analizy.

**Zakres:**
- dodać `TerrainResult` do Pydantic/ORM/persistence/cache/TypeScript;
- pola:
  - `min_height_m`,
  - `max_height_m`,
  - `height_difference_m`,
  - rozdzielczość/grid size,
  - liczba próbek,
  - status pokrycia,
  - source;
- panel UI i PDF;
- brak pokrycia nie oznacza „teren płaski”.

**Kryteria akceptacji:** wynik NMT zapisany w analizie jest identyczny po ponownym odczycie z bazy i widoczny w PDF.

---

### BK-302 — Analiza rastra NMT: spadek, ekspozycja i profil terenu

**Priorytet:** P1  
**Zależność:** BK-301

**Wartość:** silny moduł GIS pokazujący coś więcej niż agregację gotowych atrybutów.

**Zakres:**
- potwierdzić oficjalny WCS NMT i zapisać jego kontrakt;
- pobierać GeoTIFF wycięty do działki + mały bufor;
- walidować CRS, pixel size, NoData, rozmiar pliku;
- obliczać:
  - średni spadek,
  - medianę,
  - P90,
  - maksymalny spadek,
  - udział powierzchni w jawnych klasach nachylenia,
  - dominujący kierunek ekspozycji tam, gdzie ma sens,
  - deterministyczny profil wysokościowy;
- wynik powinien przechowywać rozdzielczość danych źródłowych.

**Kryteria akceptacji:** dla kontrolnego rastra syntetycznego wartości zgadzają się z rozwiązaniem analitycznym; dla przypadków realnych są porównane z QGIS.

---

### BK-303 — Uporządkowanie wyników ISOK i GDOŚ

**Priorytet:** P0

**Zakres:**
- rozszerzyć `RiskResult`, aby nie tracił strukturalnych pól domenowych;
- powódź: klasa prawdopodobieństwa, okres powrotu jeśli dostępny, severity, `intersection_area_sqm`, `intersection_pct`, `touches_boundary`;
- ochrona: typ, nazwa, `intersection_area_sqm`, `intersection_pct`;
- tekst `description` pozostaje prezentacją, nie jedynym nośnikiem danych;
- UI oraz PDF korzystają z pól strukturalnych;
- `unavailable` nie może zostać pokazane jako pusta lista „brak ryzyka”.

**Kryteria akceptacji:** evaluator może policzyć precision/recall i błąd powierzchni bez parsowania tekstu `description`.

---

### BK-304 — Kontekst zabudowy sąsiedniej z oficjalnego EGiB WFS

**Priorytet:** P1

**Zakres:**
- zweryfikować aktualny krajowy/agregowany publiczny kanał działek i budynków EGiB;
- przechowywać wyłącznie dane nieosobowe potrzebne do analizy;
- dla buforów np. 50/100/200 m obliczyć:
  - liczbę budynków,
  - sumę footprintów,
  - udział zabudowy,
  - minimalną odległość do budynku,
  - podstawowe statystyki wielkości footprintów;
- wynik nazwać „kontekst istniejącej zabudowy”, nie „możliwość zabudowy”.

**Kryteria akceptacji:** wyniki dla próbki są ręcznie zweryfikowane w QGIS.

---

### BK-305 — Kontekst drogi/transportu z BDOT10k lub równoważnego oficjalnego źródła

**Priorytet:** P1

**Zakres:**
- potwierdzić kontrakt WFS i typy dróg przed implementacją;
- policzyć geometryczną odległość do najbliższej drogi i sąsiedztwo z obiektem drogowym;
- zachować klasę/kategorię tylko jeśli występuje wiarygodnie w źródle;
- wyraźny komunikat:
  **„bliskość/sąsiedztwo geometryczne nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej”**.

**Kryteria akceptacji:** system nigdy nie zwraca pola `legal_public_road_access=true` na podstawie samego przecięcia geometrii.

---

### BK-306 — Strategia KIUT/GESUT bez fałszywej pewności

**Priorytet:** P0

**Problem:** obecny klient KIUT może zamienić timeout/błąd HTTP na `[]`, co jest nieodróżnialne od sprawdzonego braku cech.

**Zakres:**
- potwierdzić, czy istnieje publiczny i dozwolony kontrakt wektorowy właściwy dla potrzeb pracy;
- jeśli tak:
  - zdefiniować `KiutServiceUnavailableError`,
  - odróżnić `available_empty` od `unavailable`,
  - zapisać capabilities/fixtures;
- jeśli nie:
  - wyłączyć geometrię KIUT z wniosków obliczeniowych,
  - pozostawić wyłącznie podgląd WMS/pokrycie,
  - nie raportować odległości ani liczby sieci;
- techniczne bufory sieci mogą być prezentowane tylko jako przybliżenie, z podstawą reguły i confidence;
- przed obroną ręcznie zweryfikować wszystkie wartości z `network_rules.json`; reguła bez wiarygodnej podstawy nie może pomniejszać „powierzchni zabudowalnej” bez wyraźnego oznaczenia symulacyjnego.

**Kryteria akceptacji:** awaria KIUT nigdy nie daje komunikatu „brak sieci”.

---

## ETAP E — Mapa analityczna POG

### BK-401 — Własne warstwy wektorowe/MVT POG z aktywnego wydania PostGIS

**Priorytet:** P0  
**Zależność:** BK-104, BK-105

**Cel:** przejść od rastrowego podglądu do interaktywnej warstwy analitycznej.

**Zakres:**
- endpoint kafli wektorowych oparty o aktywny `data_release`
  (URL przypięty do `release_id`, limity obiektów/bajtów → `413` —
  rozstrzygnięcie w `docs/adr/ADR-007-pog-vector-tiles-and-shared-presentation.md`);
- logiczne warstwy:
  - `zones`,
  - `ouz`,
  - `downtown`,
  - `social_infrastructure_standard`,
  - `act_boundary`;
- atrybuty: symbol, etykieta, cztery parametry, status aktu, TERYT, `data_release_id`;
- projekt i akt wiążący jako osobny stan/edition;
- poprawna walidacja z/x/y;
- pusty kafel = `200`;
- cache + ETag;
- nie publikować pól zbędnych ani dużych surowych atrybutów.

**Kryteria akceptacji:** kliknięta cecha na mapie zawiera te same wartości parametrów co wynik analizy tej samej geometrii.

---

### BK-402 — Pięć trybów tematycznych POG

**Priorytet:** P0  
**Zależność:** BK-401

**Tryby:**
1. strefy planistyczne;
2. maksymalna nadziemna intensywność zabudowy;
3. maksymalny udział powierzchni zabudowy;
4. maksymalna wysokość zabudowy;
5. minimalny udział powierzchni biologicznie czynnej.

**Wymagania:**
- przełączenie przez `setPaintProperty`/wyrażenia MapLibre;
- brak nowego requestu sieciowego podczas samej zmiany trybu;
- `NULL` ma własny styl „brak wartości”, nie klasę zero;
- jednostka i kierunek skali opisane jawnie.

**Kryteria akceptacji:** test komponentu potwierdza, że zmiana wszystkich pięciu trybów nie modyfikuje URL źródła kafli i nie wykonuje fetch.

---

### BK-403 — Jedno źródło prawdy dla stylu i legendy

**Priorytet:** P0  
**Zależność:** BK-402

**Zakres:**
- `frontend/lib/pogZones.ts` — kompletna lista ustawowych stref, etykiety, kolejność, paleta
  (adapter wspólnego `shared/pog-presentation.json`, czytanego też przez backend — ADR-007);
- `frontend/lib/pogThemes.ts` — progi, jednostki, opis skali;
- te same configi zasilają:
  - mapę,
  - legendę,
  - wykres udziałów,
  - snapshot mapy raportu;
- OUZ/OZS/OSDIS rozróżnione wzorem, nie wyłącznie kolorem;
- informacja nie może zależeć wyłącznie od percepcji barwy.

**Kryteria akceptacji:** test kontraktowy wykrywa rozjazd progów/kolorów między legendą i warstwą.

---

### BK-404 — Inspector obiektu bez uruchamiania pełnej analizy

**Priorytet:** P0  
**Zależność:** BK-401

**Zakres:**
- `queryRenderedFeatures` na warstwach POG;
- panel pokazuje natychmiast:
  - strefę,
  - cztery parametry,
  - profile,
  - akt/status,
  - wersję/release,
  - OUZ/OZS/OSDIS obecne w punkcie;
- dopiero osobny przycisk uruchamia pełną analizę działki;
- kliknięcie poza pokryciem rozróżnia „brak obiektu” od „warstwa niedostępna”
  (szczegóły spoza kafla: `GET /api/v1/map/pog/releases/{release_id}/features/{feature_id}` —
  rozstrzygnięcie w `docs/adr/ADR-009-pog-inspector-area-summaries-layer-state.md`).

---

### BK-405 — Udziały stref dla aktu/gminy

**Priorytet:** P1  
**Zależność:** BK-104

**Zakres:**
- liczyć przy imporcie, nie przy każdym HTTP;
- `area_sqkm`, `share_pct`, `zone_count`, `is_complete`;
- wizualizacja jako wykres + równoważna tekstowa tabela;
- suma udziałów kontrolowana tolerancją;
- niepełny zbiór ma flagę `is_complete=false`
  (migracja `024_pog_area_summaries`, `GET /api/v1/map/pog/releases/{release_id}/summary` — ADR-009).

---

### BK-406 — Jawne rozróżnienie projektu, aktu obowiązującego i braku pokrycia

**Priorytet:** P0

**Zakres:**
- inne style projektu i danych wiążących: kolor + wzór + tekst;
- stała plakietka „projekt / dane niewiążące”;
- stan warstwy co najmniej:
  - loading,
  - available,
  - partial,
  - no_coverage,
  - error,
  - stale;
- komunikat `no_coverage` nie może używać tekstu „brak planu”, jeśli nie ma takiego potwierdzenia w źródle urzędowym.
- stan warstwy wyznaczany z metadanych wydania (`coverage_areas`) i zdarzeń źródła,
  nie z pustego kafla — rozstrzygnięcie w ADR-009.

---

## ETAP F — Raport PDF klasy inżynierskiej

### BK-501 — Raport v2: architektura informacji inspirowana OnGeo

**Priorytet:** P0  
**Zależność:** BK-105, BK-301, BK-303

**Docelowe sekcje:**
1. identyfikacja i geometria działki;
2. podsumowanie wykrytych uwarunkowań;
3. MPZP;
4. POG + OUZ/OZS/OSDIS;
5. środowisko: powódź i ochrona przyrody;
6. teren;
7. infrastruktura/transport — tylko dane o potwierdzonym kontrakcie;
8. jakość i kompletność analizy;
9. źródła/provenance;
10. ograniczenia interpretacyjne.

**Zasady:**
- bez syntetycznego „scoringu inwestycyjnego”;
- rozróżniać fakt źródłowy, wynik obliczenia i przybliżenie;
- brak danych ma być widoczny;
- raport generowany wyłącznie z persisted snapshotu.

**Kryteria akceptacji:** wszystkie istotne pola z API mają jednoznaczny odpowiednik w raporcie albo jawne uzasadnienie pominięcia
(tabela `backend/app/modules/reporting/domain/field_mapping.py`, `docs/report/field-mapping.md`,
szablon `backend/app/templates/report.html` — rozstrzygnięcie w `docs/adr/ADR-010-report-v2-and-deterministic-maps.md`).

---

### BK-502 — Pełne tabele MPZP i POG w PDF

**Priorytet:** P0  
**Zależność:** BK-105, BK-202, BK-203

**Zakres:**
- każda przecinająca strefa jako osobny wiersz;
- udział m² i %;
- parametry z jednostkami;
- `nie określono` różne od `0`;
- wskazanie evidence MPZP;
- status POG/projektu;
- OUZ/OZS/OSDIS osobno;
- brak średniej parametrów pomiędzy strefami
  (evidence przez odsyłacze `[E#]`/`[D#]`, suma udziałów bez korekty zaokrągleń — ADR-010).

---

### BK-503 — Deterministyczne snapshoty map w raporcie

**Priorytet:** P0  
**Zależność:** BK-401, BK-403

**Zakres:**
- render mapy z danych zapisanych/lokalnych, a nie zależność od losowego aktualnego stanu zewnętrznego WMS;
- obrys działki;
- wybrana warstwa planistyczna/ryzyka;
- legenda;
- skala;
- tryb tematyczny;
- `data_release_id` i data danych;
- identyczny snapshot analizy + konfiguracja → semantycznie identyczny obraz.

**Kryteria akceptacji:** powtórne generowanie raportu dla tego samego snapshotu nie zmienia znaczenia mapy mimo aktualizacji upstream
(migracja `025_report_map_snapshot`, `analyses.report_map_snapshot` z hashem semantycznym, render bez WMS — ADR-010).

---

### BK-504 — Macierz kompletności i świeżości danych

**Priorytet:** P0

**Zakres:**
- dla każdej sekcji raportu:
  - status,
  - źródło,
  - `fetched_at`,
  - `data_release_id` jeśli dotyczy,
  - manual review;
- jawne ostrzeżenie dla starego snapshotu lub źródła;
- nie określać arbitralnie jednej daty ważności dla wszystkich źródeł — reguła może zależeć od źródła.

**Kryteria akceptacji:** każda sekcja (także pusta) ma status, źródło albo powód jego braku, czas/wydanie i flagę manual;
reguła świeżości jest per źródło z jawnym punktem odniesienia (`analyzed_at`), brak reguły = `unknown`, brak globalnego TTL;
ocena jest zapisana z analizą (`analyses.section_quality`, migracja `026`, `matrix_sha256`) i tylko czytana przez
cache/API/UI/PDF, a wiek na dzień eksportu jest osobnym ostrzeżeniem
(`SectionQuality`, `freshness_policy` w katalogu — ADR-011).

---

### BK-505 — Pakiet audytowy analizy

**Priorytet:** P1  
**Zależność:** BK-501

**Eksport ZIP:**
- `analysis.json`;
- `sources.json`;
- geometria działki i dozwolone warstwy pochodne GeoJSON;
- `manifest.json` z SHA-256;
- README opisujący CRS, datę analizy i znaczenie statusów.

Do pakietu nie wolno wkładać surowych danych z zakazem redystrybucji.

**Kryteria akceptacji:** `GET /report/{analysis_id}/audit.zip` (dostęp jak raport, `404`, `413`, streaming); manifest z SHA-256
każdego pliku, hash paczki poza archiwum (`X-Audit-Package-SHA256`), weryfikacja offline
(`backend/scripts/verify_audit_package.py`); `redistribution` w katalogu steruje dołączaniem, zabroniony artefakt = referencja,
hash i powód w manifeście; deterministyczny ZIP dla tego samego snapshotu i wersji eksportera (ADR-011).

---

## ETAP G — Ewaluacja ilościowa pracy

### BK-601 — Główne badanie poprawności na korpusie referencyjnym

**Priorytet:** P0  
**Zależność:** BK-004 oraz zakończony rdzeń P0

**Zakres:**
- zamrozić wersję kodu i wersje danych;
- uruchomić wszystkie przypadki;
- wygenerować:
  - accuracy klasyfikacji,
  - confusion matrix dla warunków binarnych,
  - błędy pól/udziałów,
  - kompletność sekcji,
  - liczbę manual review;
- przeanalizować każdy błąd, nie tylko podać średnią;
- nie usuwać „trudnych” przypadków po zobaczeniu wyniku.

**Rekomendowany cel techniczny:** dla przecięć liczonych na tej samej referencyjnej geometrii bezwzględny błąd udziału powinien być na poziomie tolerancji numerycznej/transformacji; różnice większe niż ok. 0,5 punktu procentowego wymagają wyjaśnienia źródła różnicy.

---

### BK-602 — Eksperyment centroid vs pełne przecięcie działki

**Priorytet:** P0  
**Zależność:** BK-002, BK-105, BK-202

**Hipoteza:** uproszczone przypisanie na podstawie centroidu traci informację dla działek wielostrefowych i przy granicach.

**Metryki:**
- procent działek, dla których centroid pomija co najmniej jedną strefę;
- liczba stref pominiętych;
- różnica w parametrach, które zobaczyłby użytkownik;
- udział przypadków, gdzie strefa centroidu nie jest strefą o największym polu.

**Wartość akademicka:** czytelny eksperyment porównujący prostą metodę z właściwą analizą przestrzenną.

---

### BK-603 — Ewaluacja parsera MPZP

**Priorytet:** P0  
**Zależność:** BK-203

**Korpus:** minimum 20 dokumentów/fragmentów z kilku gmin i różnych formatów:
- tekstowy PDF;
- tabela;
- skan/OCR;
- kilka stref w jednym dokumencie.

**Metryki:**
- precision/recall znalezienia parametrów;
- exact-value accuracy po normalizacji;
- accuracy powiązania parametru ze strefą;
- wyniki osobno dla PDF text / tabel / OCR;
- kalibracja confidence: czy przypadki z niskim confidence rzeczywiście częściej są błędne.

---

### BK-604 — Failure injection: dowód bezpiecznej degradacji

**Priorytet:** P0

**Scenariusze:**
- timeout;
- HTTP 5xx;
- pusta odpowiedź;
- malformed XML/GML;
- `ServiceException/ExceptionReport` przy HTTP 200;
- zły CRS;
- odpowiedź za duża;
- brak warstwy/typeName;
- uszkodzony ZIP;
- źródło niedozwolone;
- stale cache.

**Najważniejsze kryterium:** 100% kontrolowanych awarii źródła krytycznego ma zakończyć się `unknown/unavailable/partial`, a nie komunikatem „brak ograniczenia”.

---

### BK-605 — Badanie wydajności

**Priorytet:** P0

**Porównać:**
- cold live upstream;
- lokalny PostGIS;
- warm cache;
- PDF generation;
- MVT cache hit/miss, jeśli BK-401 jest gotowe.

**Metryki:** p50, p95, max, liczba requestów upstream, rozmiar odpowiedzi, rozmiar kafli.

**Wynik:** skrypt tworzy CSV/JSON oraz wykresy używane w pracy.

---

### BK-606 — Eksperyment odtwarzalności i wersjonowania

**Priorytet:** P0

**Zakres:**
- zapisać hash kanonicznego JSON wyniku;
- ten sam snapshot + ten sam release → ten sam wynik merytoryczny;
- po publikacji nowego wydania nowa analiza może się zmienić, ale stara nadal wskazuje poprzednie wydanie;
- raport starej analizy nie pobiera „dzisiejszych” parametrów planistycznych;
- wykazać rollback/przełączenie aktywnego release bez usuwania historii.

---

### BK-607 — Małe badanie użyteczności

**Priorytet:** P1

**Próba:** 5–10 osób, jeśli pozwala czas.

**Zadania:**
- znajdź działkę;
- wskaż strefę POG i jej wysokość;
- sprawdź, czy działka leży w OUZ;
- odczytaj ryzyko powodziowe;
- znajdź źródło informacji;
- pobierz raport.

**Metryki:** czas, liczba błędów interpretacyjnych, subiektywna trudność; opcjonalnie SUS.

---

## ETAP H — Stabilizacja i materiał do obrony

### BK-701 — Deterministyczne E2E bez internetu

**Priorytet:** P0

**Zakres:**
- Docker Compose + zamrożone dane testowe;
- scenariusz pełny: wyszukanie → analiza → mapa → wynik → PDF;
- przypadek wielostrefowy;
- przypadek częściowego wyniku;
- przypadek źródła niedostępnego;
- żadna usługa zewnętrzna nie jest potrzebna podczas testu E2E.

---

### BK-702 — Testy bezpieczeństwa źródeł i parserów

**Priorytet:** P0

**Zakres:**
- SSRF: loopback/private IP/host spoza allowlisty;
- redirect poza allowlistę;
- XXE;
- billion laughs / nadmierne zagnieżdżenie XML;
- zip slip i zip bomb;
- limit rozmiaru odpowiedzi i rozpakowania;
- niedozwolony content type;
- limity paginacji WFS;
- nieprawidłowe jednostki/wartości parametrów planu.

**Kryteria akceptacji:** kontrolowany błąd domenowy, brak niekontrolowanego requestu lub zapisu.

---

### BK-703 — Monitoring zmian kontraktów źródeł

**Priorytet:** P1

**Zakres:**
- ręcznie/schedulowane pobranie GetCapabilities;
- zapis SHA i semantycznego diffu: wersje, CRS, typy, URL operacji;
- zmiana kontraktu tworzy raport do przeglądu;
- brak automatycznego promowania nowego kontraktu do produkcji.

---

### BK-704 — ADR i dokumentacja architektury do pracy

**Priorytet:** P0

**Dodać ADR-y co najmniej dla:**
- hierarchii oficjalnych źródeł i fallbacków;
- kanonicznego CRS i zasad transformacji;
- modelu `available/no_match/unknown`;
- wersjonowania `DataRelease` i provenance;
- POG multi-zone;
- MPZP vector-first + document evidence;
- odtwarzalnego PDF;
- granicy pomiędzy faktem, heurystyką i przybliżeniem technicznym.

**Wynik:** diagramy C4/komponentów i diagram sekwencji pełnej analizy gotowe do wykorzystania w pracy.

---

### BK-705 — Release candidate pracy inżynierskiej

**Priorytet:** P0

**Zakres:**
- tag wersji pracy;
- przypięte zależności;
- pełny CI;
- `docker compose up --build` jako podstawowa ścieżka;
- jedno polecenie do uruchomienia benchmarku ewaluacyjnego;
- jedno polecenie do wygenerowania raportu wyników;
- README „Reprodukcja wyników pracy”.

---

### BK-706 — Evidence pack do rozdziału wyników i obrony

**Priorytet:** P0  
**Zależność:** BK-601–BK-606

**Katalog `docs/thesis/`:**
- opis korpusu referencyjnego;
- macierz benchmarków funkcjonalnych;
- metryki CSV/JSON;
- wykresy;
- confusion matrices;
- porównanie centroid/intersection;
- wynik parsera MPZP;
- benchmark czasu;
- przykładowe PDF;
- zrzuty map tematycznych;
- tabela źródeł i dat weryfikacji;
- znane ograniczenia.

**Kryteria akceptacji:** każda liczba podana w rozdziale ewaluacyjnym ma plik/skrypt, z którego została wygenerowana.

---

# 7. Zakres P2 — dopiero po ukończeniu wszystkich P0

### BK-801 — Widok 3D stref i NMT

Ekstruzja stref wg maksymalnej wysokości + terrain z NMT. Funkcja demonstracyjna, nie rdzeń ewaluacji.

### BK-802 — RCN i kontekst rynku nieruchomości

Możliwy po potwierdzeniu aktualnego publicznego kanału. Nie jest konieczny, ponieważ ceny nie są uwarunkowaniem przestrzennym w tym samym sensie co plan, powódź czy rzeźba terenu.

### BK-803 — POI i izochrony

Dostępność szkół, usług, komunikacji i czasy dojścia/dojazdu. Wartość produktowa podobna do OnGeo, ale poza rdzeniem pracy.

### BK-804 — Pozwolenia na budowę

Realizować tylko po potwierdzeniu aktualnego publicznego i legalnego API. Nie uzależniać obrony od źródła o ograniczonym dostępie.

### BK-805 — Dodatkowe uwarunkowania OnGeo

Hałas, jakość powietrza, geologia, hydrogeologia, gleby, lasy, nasłonecznienie, osuwiska i inne. Każda z tych warstw musi przejść ten sam Definition of Done; nie dodawać ich jako samych kolorowych WMS-ów bez modelu jakości danych.

### BK-806 — Krajowy ciągły import i monitoring operacyjny

Pełny scheduler, dashboard pokrycia, alerty, rollout/rollback dla krajowych wydań. Przydatne produktowo, ale wykracza poza potrzebę udowodnienia architektury na reprezentatywnej próbce.

---

# 8. Rekomendowana kolejność realizacji

Kolejność jest ważniejsza niż równoległa liczba funkcji:

1. **BK-001 → BK-004** — zdefiniować, jak będzie mierzony sukces.
2. **BK-101 → BK-106** — zrobić POG urzędowe, wersjonowane i wielostrefowe.
3. **BK-201 → BK-205** — doprowadzić MPZP do przestrzennego, audytowalnego modelu.
4. **BK-301 + BK-303 + BK-306** — domknąć istniejące analizy kontekstowe i ich semantykę błędów.
5. **BK-401 → BK-404 + BK-406** — mapa tematyczna na poziomie funkcjonalnym zbliżonym do rejestrplanowogolnych.pl.
6. **BK-501 → BK-504** — raport o strukturze jakościowej zbliżonej do profesjonalnych raportów, ale oparty na własnym modelu i danych.
7. **BK-601 → BK-606** — właściwa ewaluacja akademicka.
8. **BK-701, BK-702, BK-704, BK-705, BK-706** — release do obrony.
9. P1 wykonywać równolegle tylko wtedy, gdy nie opóźnia P0.
10. P2 rozpocząć dopiero po uzyskaniu pierwszych wyników BK-601.

---

# 9. Minimalny zakres do obrony

Jeżeli czas będzie ograniczony, praca nadal może być mocna bez pełnego parytetu z OnGeo. Minimalny zakres powinien zawierać:

- realny korpus i evaluator;
- oficjalny RU POG z pełną listą stref i OUZ/OZS/OSDIS;
- powierzchniowe MPZP tam, gdzie dostępny jest wektor, plus audytowalny parser dokumentu;
- ISOK i GDOŚ jako poprawne sekcje z jawnie rozróżnioną niedostępnością;
- NMT w wyniku końcowym;
- kontrolowaną semantykę KIUT;
- mapę POG z pięcioma trybami tematycznymi;
- raport PDF z pełną listą źródeł i kompletnością;
- eksperyment centroid vs intersection;
- ewaluację parsera MPZP;
- failure injection;
- benchmark wydajności;
- eksperyment odtwarzalności.

To daje większą wartość akademicką niż dodanie kilkunastu niezweryfikowanych warstw tematycznych.

---

# 10. Świadome wyłączenia z zakresu pracy

Przed obroną system **nie musi**:

- dorównywać OnGeo liczbą sekcji;
- importować wszystkich gmin w Polsce, o ile architektura importu jest skalowalna i przebadana na reprezentatywnej próbce;
- generować „oceny inwestycyjnej 0–100”;
- wydawać opinii prawnej, czy działka „nadaje się do zabudowy”;
- korzystać z danych osobowych EGiB;
- kopiować danych, stylu lub treści z prywatnych serwisów referencyjnych;
- utrzymywać źródła, których licencji/kontraktu nie potwierdzono;
- automatycznie traktować brak odpowiedzi źródła jako brak ograniczeń.

---

# 11. Ostateczne kryteria jakości release do pracy

Release przeznaczony do obrony powinien spełnić łącznie:

- wszystkie zadania P0 zakończone lub formalnie opisane jako ograniczenie pracy;
- minimum 80% coverage w istniejącym CI;
- zielony deterministic E2E;
- minimum 24 realne działki referencyjne;
- raport ewaluacyjny generowany automatycznie;
- brak znanego przypadku, w którym awaria krytycznego źródła jest prezentowana jako „brak zagrożenia/ograniczenia”;
- pełne provenance dla sekcji kluczowych;
- każda wartość liczbowa ma jednostkę i sposób wyliczenia;
- każda heurystyka/przybliżenie jest opisana jako heurystyka/przybliżenie;
- raport PDF może zostać odtworzony ze snapshotu analizy;
- wersja źródeł wykorzystanych w eksperymentach jest zamrożona lub jednoznacznie identyfikowalna;
- README pozwala uruchomić system i eksperymenty od zera w Dockerze.

---

## 12. Źródła referencyjne do okresowej ponownej weryfikacji

- Repozytorium projektu: https://github.com/IgorK834/system_analizy_uwarunkowan_dzialki
- Rejestr Urbanistyczny — usługi sieciowe: https://rejestr-urbanistyczny.gov.pl/network-services
- Rejestr Urbanistyczny — polska ścieżka usług: https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe
- Rejestr Planów Ogólnych: https://rejestrplanowogolnych.pl/
- Przykładowy raport OnGeo: https://ongeo.pl/raporty/przykladowy-raport/FB5CE893C584CB976177B832C9924DE4
- Geoportal / usługi danych GUGiK: https://www.geoportal.gov.pl/

Przed implementacją nowego adaptera należy ponownie potwierdzić kontrakt usługi, ponieważ endpoint, typy cech, CRS oraz warunki dostępu mogą się zmieniać niezależnie od kodu aplikacji.
