# Legnica — MPZP Wojewódzkiego Szpitala Specjalistycznego

- Dokument: Uchwała Nr XIII/161/25 Rady Miejskiej Legnicy z 24 lutego
  2025 r.
- Źródło: <https://www.inforlex.pl/download/akty_pdf,U70,2025,65,1124.pdf>.
- Data pobrania: 15 lipca 2026 r. (`2026-07-15T18:12:58.229901+00:00`).
- Format: prawdziwy PDF z warstwą tekstową i natywnymi tabelami
  `pdfplumber`; fixture nie jest syntetyczny.

Tabela na stronie 4 zawiera dla `1UZ` i `2UZ`: intensywność 0,15–2,50,
maksymalny udział powierzchni zabudowy 0,60, minimalny udział powierzchni
biologicznie czynnej 0,15 oraz maksymalną wysokość budynku usługowego 16 m.
`tables.json` przechowuje rzeczywiste wiersze zwrócone przez produkcyjną
ekstrakcję.

## Znane ograniczenia

Obecne ekstraktory nie zwracają tych pięciu parametrów. Intensywność jest
zapisana jako zwarty zakres bez etykiet „minimalna/maksymalna”, udziały jako
ułamki bez `%`, a wysokość używa anchoru „wysokość budynku funkcji usługowej”
zamiast „wysokość zabudowy”. Dodatkowo symbole występują w kilku paragrafach,
więc segmentacja oznacza obie strefy jako wieloznaczne. `expected.json`
utrwala te braki jawnie jako negatywne oczekiwania.

Fixture sprawdza przejście prawdziwych `ExtractedTable` przez
`TextExtractionResult` i segmentację. Nie dowodzi jeszcze poprawnego wiązania
wiersza tabeli bez symbolu z poprzedzającą sekcją strefy i nie pokrywa OCR.

Odtworzenie z katalogu `backend/`:

```bash
python -m scripts.build_mpzp_text_fixture \
  --url https://www.inforlex.pl/download/akty_pdf,U70,2025,65,1124.pdf \
  --output-dir tests/fixtures/mpzp/legnica_szpital \
  --note "Uchwała Nr XIII/161/25, MPZP Wojewódzkiego Szpitala Specjalistycznego przy ul. Wrocławskiej, Legnica"
```
