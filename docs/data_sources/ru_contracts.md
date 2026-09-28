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

Klient ma jawną tabelę osi WMS 1.3.0: `EPSG:4326` używa kolejności Y,X, a
`CRS:84` i `EPSG:2180` kolejności X,Y. WMS pozostaje kanałem prezentacji i
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
