# Kontrakty usług Rejestru Urbanistycznego

## Zakres i źródło prawdy

Fixtury w `backend/tests/fixtures/ru/` zamrażają zweryfikowany 23 września
2026 r. kontrakt oficjalnych usług Rejestru Urbanistycznego:

- WMS POG 1.3.0 do podglądu i discovery;
- WFS POG 2.0.0 / GML 3.2 do importu wektorowego;
- CSW 2.0.2 do discovery metadanych;
- małą odpowiedź WFS GetFeature dla aktu Bielska-Białej (`246101`);
- wynik `DescribeFeatureType`, importowany schemat APP 3.0 i po jednej pełnej,
  nieprzyciętej próbce każdego z sześciu typów POG.

Stroną wejściową jest
`https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe`. Dokładny URL żądania,
czas pobrania UTC, SHA-256, wersja usługi, `Fees`, `AccessConstraints` i podstawa
dostępu są zapisane osobno dla każdego pliku w `manifest.json`. Runtime i CLI
nie przechowują kopii endpointów w ustawieniach: pobierają je z wpisu
`pog_app` w `docs/data_sources/catalog.yaml`.

## Kontrolowane odświeżenie poza CI

Najpierw należy pobrać kandydat do katalogu tymczasowego i przejrzeć różnicę:

```bash
python backend/scripts/fetch_ru_contracts.py \
  --output-dir /tmp/ru-contract-review --timeout 60
diff -ru backend/tests/fixtures/ru /tmp/ru-contract-review
```

Po zaakceptowaniu zmiany kontraktu to samo polecenie bez `--output-dir`
atomowo zastępuje komplet fixtur i manifest:

```bash
python backend/scripts/fetch_ru_contracts.py --timeout 60
```

Skrypt wymaga HTTP 200, XML-owego MIME, niepustej treści i przejścia parserów
kontraktowych. Najpierw waliduje cały zestaw w pamięci, a dopiero później
wykonuje `os.replace`. Błąd nie pozostawia częściowo odświeżonego korpusu.
Zwykłe testy i CI wyłącznie czytają fixtury; nie importują ani nie uruchamiają
skryptu sieciowego.

## Wynik ponownej weryfikacji 23.09.2026

Pierwsza próba odświeżenia zatrzymała się na asercji historycznego
`CountDefault=100`. Ręczny odczyt trzech GetCapabilities potwierdził zmianę
wartości domyślnej WFS na 10. Druga kontrolowana próba wykazała zmianę
przestrzeni nazw APP z `.../app/2.0` na `.../app/3.0`; stary filtr GetFeature
zwrócił HTTP 400 `Unknown namespace [app-pog]`. Po aktualizacji przestrzeni
nazw cały zestaw, w tym próbka Bielska-Białej, przeszedł walidację i został
zamrożony. Jest to jawnie przejrzana ewolucja kontraktu, a nie automatyczna
aktualizacja fixtur.

### WMS POG

- wersja `1.3.0`;
- 51 nazwanych warstw `APP.POG.*`;
- formaty GetMap zapisane w katalogu dokładnie jak w XML;
- CRS: `EPSG:2180`, `EPSG:4326`, `CRS:84`;
- `EPSG:2180` pochodzi także z dziedziczenia po warstwie nadrzędnej;
- brak deklaracji `EPSG:3857`.

Klient ma jawną tabelę osi WMS 1.3.0: `EPSG:4326` i `EPSG:2180` używają kolejności
północ,wschód (Y,X w zapisie x = wschód, y = północ), a `CRS:84` kolejności X,Y. Pierwotny wpis
`EPSG:2180 → X,Y` był błędny (zob. „Ustalenia z żywej usługi 2026-10-12” na końcu dokumentu). WMS pozostaje kanałem prezentacji i
discovery. Nie jest źródłem obliczeń powierzchni ani odległości.

### WFS POG

- wersja `2.0.0`;
- typy: `AktPlanowaniaPrzestrzennego`, `DokumentFormalny`,
  `ObszarStandardowDostepnosciInfrastrukturySpolecznej`,
  `ObszarUzupelnieniaZabudowy`, `ObszarZabudowySrodmiejskiej`,
  `StrefaPlanistyczna`;
- domyślny CRS `EPSG:2180`;
- wspierane CRS: `EPSG:2176`, `2177`, `2178`, `2179`, `2180`, `4258`, `4326`;
- format `application/gml+xml; version=3.2` i aliasy wykazane w XML;
- `CountDefault=10` w snapshotcie z 23.09.2026;
- przestrzeń nazw APP `.../schemas/app/3.0`.

Klient nie polega na wartości domyślnej. Każda strona ma jawne `count=100` i
`startIndex=0,100,...`. Import CLI dodaje filtr FES po przestrzeni nazw
identyfikatora JPT, więc nie pobiera całego zasobu krajowego. Próbka
GetFeature ma `numberMatched=1`, `numberReturned=1` i identyfikator zawierający
`246101`. Przycięcie dotyczy wyłącznie tej starszej próbki. Sześć fixtur
`wfs_pog_getfeature_{act,document,osdis,ouz,ozs,zone}.xml` zachowuje pełną
odpowiedź, w tym geometrię i relacje `xlink`. Razem ze schematami XSD służą
one do offline'owego testu mapowania wszystkich obiektów domenowych.

### CSW

Endpoint negocjuje jawnie `version=2.0.2`. Snapshot potwierdza operacje
`GetCapabilities`, `GetRecords`, `GetRecordById`, `DescribeRecord`, `GetDomain`
i `GetRepositoryItem`. Klient kontynuuje strony wyłącznie według
`SearchResults@nextRecord`.

## Warunki dostępu i status produkcyjny

Wszystkie trzy GetCapabilities deklarują:

- `Fees`: „Brak ograniczeń w publicznym dostępie”;
- `AccessConstraints`: „Brak warunków dostępu i użytkowania”.

Te pola są udokumentowaną podstawą bezpośredniego używania publicznych usług i
dlatego kanały RU w katalogu mają potwierdzony kontrakt oraz mogą przejść guard
`ensure_source_runnable`. Nie są interpretowane jako osobna licencja na
publikowanie kopii surowego zbioru. Aplikacja zachowuje atrybucję i provenance.

## Ograniczenia klienta i semantyka błędów

`OgcClient` stosuje HTTPS i allowlistę hostów, sprawdza DNS/IP przy każdym
żądaniu i przekierowaniu, nie śledzi automatycznie redirectów, ma osobne
timeouty połączenia, odczytu i całej operacji, ograniczone retry z backoff,
limity bajtów, stron, cech, głębokości XML i liczby węzłów. DTD i deklaracje
encji są odrzucane. `ExceptionReport` przy HTTP 200 jest błędem kontraktu.

Powtórzona strona, powtórzony zestaw identyfikatorów albo pusty rekord przed
zadeklarowanym końcem zwraca `complete=false`. Przekroczenie limitu zgłasza
`OgcLimitError` z niekompletnym wynikiem. Importer nie publikuje takiego
artefaktu. Każdy sukces i błąd niesie `Provenance` z identyfikatorem źródła,
czasem, URL-em, operacją, kompletnością i kodem błędu.

## Polecenia odbiorowe

```bash
cd backend
pytest tests/test_ru_contracts.py tests/test_data_sources.py tests/test_ogc_client.py -q
pytest tests/test_ogc_client.py --cov=app.modules.imports.infrastructure.ogc_client \
  --cov-report=term-missing --cov-fail-under=80
```

Scenariusz importu z URL pobranym z katalogu (wymaga sieci i bazy operatora):

```bash
cd backend
python -m app.modules.imports pog --source pog_app --dry-run \
  --act-id 246101-POG --teryt 246101
```

Dla obiektów APP 3.0 z RU status prawny pochodzi z pola `status` aktu
(kod INSPIRE, np. `legalForce`). Parametr `--legal-status` dotyczy wyłącznie
warstw lokalnych bez tego pola i przyjmuje tylko urzędowy kod statusu
(`legalForce`, `adoption`, `elaboration`, `obsolete`); inne wartości dają
`unknown` (ADR-002).

Brak potwierdzenia w dowolnym innym wpisie katalogu nadal blokuje publikację;
`--dry-run` pozostaje jawnym trybem weryfikacji takiego źródła.

## Provenance aktu i dokumentów (BK-107)

Import `pog --source pog_app` (bez `--resource`) po złożeniu aktów z WFS
pobiera metadane CSW zasobu `ru_csw` wspólnym klientem OGC i wiąże je z aktem
wyłącznie po `MD_Identifier` równym URI przestrzeni nazw aktu. Dokumenty
`DokumentFormalny` są wiązane z wersją aktu po idIIP i wersji
(`resolved | unresolved | unavailable`), a każdy rekord ma SHA-256 postaci
kanonicznej C14N. Odpowiedź CSW trafia do artefaktu ZIP importu
(`90-csw-{TERYT}.xml`). Awaria CSW daje ostrzeżenie `csw_unavailable:{TERYT}`
w wyniku importu, bez blokowania publikacji. Zasady opisuje
`docs/adr/ADR-003-pog-act-provenance-chain.md`.

Oficjalny URL GML dokładnej wersji obiektu to `GetFeature` usługi, z której
obiekt pobrano, z filtrem FES po `przestrzenNazw`, `lokalnyId` i `wersjaId`
(zweryfikowane 24 września 2026 r. dla aktu Sopotu). Identyfikator URI
`https://www.gov.pl/zagospodarowanieprzestrzenne/app/...` nie jest
rozwiązywalną kartą aktu (przekierowuje poza RU), dlatego jako „kartę”
prezentujemy rekord CSW `GetRecordById` w schemacie ISO 19139.

```bash
cd backend
pytest tests/test_pog_ru_mapping.py tests/test_pog_csw_metadata.py \
  tests/test_pog_provenance_chain.py tests/test_pog_provenance_report.py -q
```

## Ustalenia z żywej usługi 2026-10-12 (AU-105, AU-106)

Zmierzone na `rejestr-urbanistyczny.gov.pl`; decyzje: [ADR-023](../adr/ADR-023-discovery-pog-ru-wfs-budzet-zadan.md) (discovery) i
[ADR-024](../adr/ADR-024-import-pog-rownolegly-wznawianie-postep.md) (import).

- **Kolejność osi dla `EPSG:2180` to północ, wschód** — w WMS 1.3.0 (`bbox`), w WFS (`bbox=N_min,E_min,N_max,E_max,urn:ogc:def:crs:EPSG::2180`)
  i w `gml:posList` odpowiedzi. Wcześniejsze zdanie powyżej („`EPSG:2180` kolejności X,Y”) dotyczy tabeli osi klienta i okazało się błędne dla RU:
  zapytanie WMS `GetFeatureInfo` w kolejności `x,y` trafia w lustrzany punkt (dla Bielska-Białej zwróciło POG gminy Rzepin). Tabela osi w `OgcClient` (`_WMS_AXIS_ORDER`) została
  poprawiona (`EPSG:2180 → yx`); discovery POG w RU nie używa już WMS.
- **WFS zwraca jeden typ na żądanie.** `typeNames=a,b,c` daje wyłącznie pierwszy typ. **Strona ma 10 obiektów** niezależnie od `count` (sprawdzone dla 5, 10, 50, 100 i 1000), więc
  „większy `count`” nie przyspiesza importu; klient liczy `startIndex` z faktycznej liczby obiektów, a równoległość stron jest jedyną dźwignią czasu.
- `PROPERTYNAME` na `AktPlanowaniaPrzestrzennego` kończy się `InvalidParameterValue` — nie da się pominąć geometrii granicy aktu (Bielsko-Biała: 303 kB).
- `resourceId` wymaga pełnego `gml:id` **z wersją** (`PL.ZIPPZP.9995_246101-POG_1POG_20260901T095300`); bez wersji zwraca 0 obiektów. Lista kilku identyfikatorów
  potrafiła zerwać połączenie.
- RU filtruje `bbox` po **obwiedni** obiektu — wielokąt zwróconego obiektu może nie dotykać `bbox`; przynależność do działki trzeba rozstrzygać lokalnie w EPSG:2180.
- Czas strony stref (10 obiektów): ok. 2,8 s sekwencyjnie; przy 4 równoległych żądaniach ok. 1,0–1,4 s efektywnie na stronę. Zwiększenie do 12 równoległych żądań nie pomogło
  (usługa kolejkuje), więc `IMPORT_WFS_CONCURRENCY` ma limit 8 i domyślnie 4.

## Publiczne API RU jako kanał importu krajowego (ADR-026, 2026-10-13)

Kanał podstawowy importu planów ogólnych dla całego kraju to publiczne API frontendu Rejestru Urbanistycznego (zasób `ru_published` źródła `pog_app` w
[`catalog.yaml`](catalog.yaml)). To **nie jest usługa OGC** (brak GetCapabilities), więc kontrakt jest przypięty zamrożonymi odpowiedziami, nagranymi skryptem
[`backend/scripts/capture_ru_pog_national.py`](../../backend/scripts/capture_ru_pog_national.py) i zapisanymi w
[`backend/tests/fixtures/ru_national/`](../../backend/tests/fixtures/ru_national/) (`manifest.json`: adres, czas pobrania UTC, SHA-256, rozmiar i nagłówki każdego pliku).
Testy i CI czytają te pliki i nie wykonują żądań sieciowych.

| Operacja | Żądanie | Odpowiedź |
|---|---|---|
| Wykaz bieżących wersji aktów POG | `POST /api/public/published/query` z ciałem `{"page": {"number": N, "size": 100, "sort": {...}}, "criteria": {"publishedActVersions": "CURRENT", "planTypeList": ["MUNICIPAL_GENERAL_PLAN"], "plansGroup": "MUNICIPAL_GENERAL_PLAN", ...}}` | JSON: `publishedActs[]` (`publishedActId`, `tercCode`, `commune`, `district`, `voivodeship`, `title`, `spatialPlanningActStatus`, `publishDate`, `validFrom`, `validTo`, `planType`) i `pageInfo` (`totalElements`, `totalPages`). Strony po **100** wierszy; 2026-10-13: **465 aktów w 462 gminach** (327 „prawnie wiążący lub realizowany”, 113 „w opracowaniu”, 25 „w trakcie przyjmowania”), 5 stron. |
| Plik GML całego aktu | `GET /api/public/published/{publishedActId}/download` (`HEAD` zwraca `content-length` bez pobierania) | Cały akt (granica, strefy, OUZ, OZS, OSDIS, dokumenty formalne) w jednym pliku GML, `content-type: application/octet-stream`, `content-disposition: attachment; filename="<identyfikator aktu>_<znacznik wersji>.gml"`. |

Ustalenia (zmierzone na żywej usłudze 2026-10-13):

- **`publishedActId` to identyfikator wersji aktu w katalogu RU i zmienia się przy każdej nowej wersji** — import przyrostowy używa go jako tokenu: akt z tym samym tokenem w rejestrze jest pomijany bez pobierania,
  nowy token z tą samą treścią pliku (`content_hash`) tylko aktualizuje token w miejscu, a nowa treść tworzy nową wersję i nowy numer stanu krajowego.
- **Układ współrzędnych pliku to PL-2000 strefy gminy** (EPSG:2176, 2177, 2178 lub 2179; Jejkowice → 2177, Sejny → 2179, Barczewo → 2178), nie EPSG:2180. Kolejność osi w `gml:posList` jest **północ, wschód**
  (jak w usługach OGC RU). Czytnik przelicza do EPSG:2180 z kolejnością osi odczytaną z rejestru EPSG (pyproj `axis_info`) i dopuszcza wyłącznie `{2176–2180, 4258, 4326, 3857}`; nieznany układ odrzuca akt.
- **Przestrzeń nazw APP bywa 2.0 albo 3.0** zależnie od aktu (pliki z 2026-10-13: 1152, 1735, 1894, 295 używają 2.0); czytnik przyjmuje obie. Numer uchwały bywa odwołaniem do Dziennika Urzędowego, nie samym numerem.
- **Część plików zaczyna się od BOM UTF-8** (Jejkowice, `publishedActId` 1152, 565 596 B). Sprawdzenie „czy to XML” musi je ignorować — pierwszy przebieg próbny odrzucił ten akt kodem `not_xml`, poprawione i objęte testem.
- **Limit żądań:** RU odpowiada HTTP 429 powyżej kilku żądań na sekundę (przy pętli stron wykazu bez przerw). Klient rozstawia żądania (`RU_PUBLISHED_MIN_INTERVAL_SECONDS` = 1,2 s, wspólny rozstawiacz dla wszystkich wątków),
  ponawia 429 z narastającym odczekaniem i po wyczerpaniu zgłasza `rate_limited`. Przebieg próbny 28 gmin (30 aktów) wykonał 35 żądań w 154 s bez ani jednego 429.
- **Tylko HTTPS i host `rejestr-urbanistyczny.gov.pl`** (przekierowania nie są śledzone), limit rozmiaru pliku `RU_PUBLISHED_MAX_GML_BYTES` (160 MB) i węzłów XML (`POG_NATIONAL_XML_MAX_NODES`); pobrany plik jest niezaufany —
  parser odrzuca DTD i encje zewnętrzne (XXE) i nie wykonuje żadnego kodu z pliku.
- **Wykaz nie zawiera wszystkich gmin:** Warszawa (146501) i Kraków (126101) nie mają bieżącego aktu POG w wykazie RU z 2026-10-13 — to „brak aktu w wykazie RU”, nie dowód braku planu w gminie
  (stan `no_act_in_ru` w `GET /api/v1/coverage/pog/commune/{terc}`).
- **Wycofanie aktu** wynika wyłącznie z nieobecności tokenu w *kompletnym* wykazie (`totalElements` zgodne z liczbą wierszy, wszystkie strony, brak powtórzonych identyfikatorów). Niekompletny wykaz nie wycofuje niczego,
  a wycofanie więcej aktów niż większa z wartości 5 i 10% aktów z tokenem RU wymaga `--allow-mass-withdrawal` (przy 465 aktach: ponad 46 naraz).

WFS (`ru_wfs`) zostaje zapasem: strony po 10 obiektów, jeden typ na żądanie (zob. wyżej) — import największej gminy tą drogą trwałby godziny i bywa blokowany limitem 5 000 obiektów typu.
