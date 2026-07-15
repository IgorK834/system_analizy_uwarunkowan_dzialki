# Bielsko-Biała — Uchwała Nr VIII/187/2024

Ten katalog nie duplikuje zamrożonej ekstrakcji. `pages.json` i `source.json`
są współdzielone z
`tests/fixtures/mpzp_documents/bielsko_biala_uchwala_viii_187_2024/`.
Tutaj znajduje się tylko kontrakt regresyjny `expected.json` i ten opis.

- Źródło: <https://bip.um.bielsko.pl/api/files/322981>.
- Data pobrania: 14 lipca 2026 r. (`2026-07-14T15:32:37.190167+00:00`).
- Format: PDF z warstwą tekstową, 13 stron, bez tabel `pdfplumber`.
- Status: prawdziwy publiczny akt prawa miejscowego; fixture nie jest
  syntetyczny.

Ręczna kontrola § 10–12 potwierdza wartości zapisane w `expected.json`.
Pipeline empirycznie zwraca dwa konflikty kątów dachu dla `230_U`, konflikt
wysokości 15/13 m dla `230_UMW` oraz poprawnie nie interpretuje wysokości
urządzeń sportowych 5 m jako wysokości zabudowy w `230_ZP`.

Fixture nie sprawdza pobierania HTTP ani aktualnego stanu prawnego uchwały.
Nie pokrywa też tabel ani OCR. Wieloznaczność `230_U` wynika z kontynuacji § 10
na kolejnej stronie i jest oczekiwanym sygnałem ręcznej weryfikacji.

Odtworzenie współdzielonej ekstrakcji z katalogu `backend/`:

```bash
python -m scripts.build_mpzp_text_fixture \
  --url https://bip.um.bielsko.pl/api/files/322981 \
  --output-dir tests/fixtures/mpzp_documents/bielsko_biala_uchwala_viii_187_2024 \
  --note "Uchwała Nr VIII/187/2024, MPZP Józefa Lompy/Cieszyńska/Sikorski, Bielsko-Biała"
```
