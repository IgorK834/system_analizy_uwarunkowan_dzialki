# Rzeczywisty korpus referencyjny BK-002

Ten katalog zawiera 30 rzeczywistych działek, ich geometrie ULDK w
`EPSG:2180`, znormalizowane obserwacje źródłowe i oczekiwane wyniki domenowe.
Jest odrębny od syntetycznych regresji w `fixtures/parcels/`.

`manifest.json` jest jedynym punktem wejścia. Dla każdego przypadku zapisuje:

- identyfikator działki i TERYT;
- datę oraz metodę weryfikacji;
- lokalne artefakty i ich SHA-256;
- status `available`, `unknown` albo `manual_review` każdej sekcji;
- wartości oczekiwane z jednostkami i tolerancje;
- jawne niejednoznaczności oraz podstawę redystrybucji.

`artifacts/parcels/` zawiera rzeczywiste wielokąty zwrócone przez ULDK. Nie są
to wygenerowane prostokąty. `artifacts/evidence/` zawiera własne pomiary i
obserwacje źródeł urzędowych. Korpus nie zawiera danych właścicieli ani innych
danych osobowych EGiB. Surowe geometrie WFS MPZP Krakowa nie są publikowane;
zapisano tylko wyniki pomiaru, identyfikatory aktu i sumy kontrolne odpowiedzi.

Walidacja lokalna:

```bash
cd backend
pytest tests/test_reference_corpus.py -q
```

Test blokuje funkcje połączeń sieciowych i ponownie ładuje cały korpus. Zmiana
geometrii lub dowodu bez aktualizacji manifestu powoduje błąd SHA-256.

