# Łódź — wypis dla terenu 6.8.MW/U

- Dokument: Uchwała Nr LXXVIII/2337/23 Rady Miejskiej w Łodzi z 5 lipca
  2023 r.
- Źródło: <https://mapa.mpu.lodz.pl/_wypisy/190/6.8.MW_U.html>.
- Data pobrania: 15 lipca 2026 r. (`2026-07-15T18:13:38.612150+00:00`).
- Format: prawdziwy, statyczny HTML; fixture nie jest syntetyczny.

Ręczna kontrola tekstu potwierdza maksimum 40% dla wspólnej reguły terenów
`6.5.MW/U` i `6.8.MW/U` oraz fragment „w odległości do 4,0 m od tej granicy”.
Escapowanie ukośnika działa: segmentacja odnajduje `6.8.MW/U`.

## Znane ograniczenia

Ekstraktor powierzchni zabudowy nie obsługuje konstrukcji „wskaźnik …
maksimum 40%”, a regex `setback_m` nie dopuszcza słów „do” i „tej”. Oba
parametry są dlatego uczciwie zapisane w `expected.json` jako nieznalezione.

Szerokie okno ekstraktora wysokości interpretuje obecnie 4,0 m, 11,0 m i
15,0 m jako konflikt `max_building_height_m`, mimo że 4,0 m jest odległością,
a 15,0 m pochodzi z innego warunku. Ten fałszywy konflikt jest opisanym known
issue, ale nie jest traktowany jako prawidłowa wartość domenowa w
`expected.json`. Z tego powodu pole oczekiwanej ręcznej weryfikacji strefy ma
wartość `null`. Fixture nie pokrywa tabel, PDF ani OCR.

Odtworzenie z katalogu `backend/`:

```bash
python -m scripts.build_mpzp_text_fixture \
  --url https://mapa.mpu.lodz.pl/_wypisy/190/6.8.MW_U.html \
  --output-dir tests/fixtures/mpzp/lodz_mw_u \
  --note "Uchwała Nr LXXVIII/2337/23, wypis dla terenu 6.8.MW/U, Łódź"
```
