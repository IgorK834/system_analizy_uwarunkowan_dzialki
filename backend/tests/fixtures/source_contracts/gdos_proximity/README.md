# GDOŚ — kontrakt bliskości (AU-206)

Zamrożono 20.10.2026 z publicznej usługi `https://sdi.gdos.gov.pl/wfs`.
Pliki `monument-point.xml` i `wind-turbine.xml` są minimalnymi wycinkami
rzeczywistych odpowiedzi WFS 2.0, zachowującymi rekordy domenowe (odpowiednio z warstw
`GDOS:PomnikiPrzyrodyPunktowe` i `GDOS:ElektrownieWiatrowe`). Pełne odpowiedzi,
z których wybrano rekordy, miały SHA-256:

- pomniki: `33e63f278e4222ff375fca2da7467db311ca3b36e253e7781e397979de858c27`,
- elektrownie: `8ad829bbb332e93297340c9c56e605b17ff69a7368ae7a35fb97d35d0b70980c`.

Pełne, nieprzetworzone odpowiedzi identyfikuje powyższy SHA-256. GetCapabilities z tego samego dnia miał SHA-256
`3fc2a3b52d3a08d2ded4bffcd6f33296058772daa79f9a30442330cbb83002d0`.
Kontrakt potwierdza geometrię punktową, `gml:id`, atrybut `nazwa` pomnika,
`nazwa_przedsiewziecia` turbiny oraz formalną kolejność osi URN EPSG:2180
(`northing,easting`; parser sprowadza ją do `easting,northing`).

Dla działki odbiorowej `141801_4.0701.23/8` ponowiono rzeczywiste zapytania
21.10.2026. Dwie rzeczywiste, puste odpowiedzi pomników (zmieniono wyłącznie
białe znaki między tagami dla czytelności) są zapisane jako
`gora-kalwaria-monuments-*-empty.xml`; potwierdzają `numberMatched=0` w promieniu
200 m. Plik `gora-kalwaria-verification.json` zapisuje ich skróty oraz wynik
pomiaru pełnej odpowiedzi OChK: „Warszawski”, przecięcie 100%. Pełna geometria
OChK ma 4,1 MB, dlatego jej nie duplikuje; identyfikuje ją SHA-256 w JSON.

Dla działki referencyjnej `281604_5.0011.107` w gminie Ruciane-Nida zapisano
21.10.2026 dwie pełne, nieprzetworzone odpowiedzi WFS dla rzeczywistego BBOX
działki. Stanowią regresję przecięcia z obszarami Natura 2000:

- `ruciane-nida-birds.xml` — `GDOS:ObszarySpecjalnejOchrony`, 101 951 B,
  `numberMatched=1`, „Puszcza Piska”, SHA-256
  `f0bc9dd6c9fc74379e40b339a45f1a65934d07dad1bbc67de6422c8334e03911`;
- `ruciane-nida-habitats.xml` — `GDOS:SpecjalneObszaryOchrony`, 384 332 B,
  `numberMatched=1`, „Ostoja Piska”, SHA-256
  `6314e89cd5e938000e37bdacdca13307ba5a257887dcd4f7e03437fcd2189a33`.

Oczekiwane pola przecięcia są niezależnie zapisane w korpusie referencyjnym
(`reference_corpus/artifacts/evidence/real-026-281604-5-0011-107.json`):
100% działki dla „Puszczy Piskiej” oraz 2 766,727 m² (1,041285%) dla „Ostoi
Piskiej”.

