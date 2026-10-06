# Eksperyment centroid vs pełne przecięcie (BK-602)

Plik jest generowany z `summary.json`; nie jest edytowany ręcznie.

## Zamrożony manifest

| Pole | Wartość |
|---|---|
| `commit_sha` | `cbefc0a22ba65bbcdfd205132ebd8070c34120db` |
| `manifest_sha256` | `eb48ff51a15306042a9cb3e8863053682061b5af2e6d7ab57a8aec3a8b90e4c2` |
| `corpus_sha256` | `65f16dc25c4fcde8ba6ee9051b3604f909477be7eebdb205fe8dcb8a1786d106` |
| `zone_layers_manifest_sha256` | `None` |

Reguła: centroid to `shapely` `parcel.centroid` w EPSG:2180, zachowany tam, gdzie wypada (poza działką wklęsłą, w dziurze). Punkt na granicy liczy się przez `covers`; kilka stref pokrywających punkt to remis (raportowane wszystkie), brak strefy to `none`; oba są „bez rozstrzygnięcia”. Przecięcia liczą produkcyjne analizatory POG i MPZP (pola w m², nie predykat `intersects`).

## 1. Kontrole z ręcznie ustalonymi wynikami

| Przypadek | Położenie centroidu | Strefy centroidu | Strefy przecięcia (udział %) | Pominięte | Widmo | Dominująca | centroid = dominująca | Pominięty obszar % |
|---|---|---|---|---|---|---|---|---:|
| `control-rectangle-60-40` | inside_parcel | A (unique) | A 60.000, B 40.000 | B | — | A | True | 40.000 |
| `control-concave-l-shape` | outside_parcel | N (unique) | S 55.556, W 44.444 | S,W | N | S | False | 100.000 |
| `control-hole` | in_hole | A (unique) | A 53.571, B 46.429 | B | — | A | True | 46.429 |
| `control-boundary-tie` | inside_parcel | A,B (tie) | A 50.000, B 50.000 | — | — | remis | None | 0.000 |

Zgodność z oczekiwaniami policzonymi ręcznie: **tak**
Zgodność dwóch produkcyjnych analizatorów (POG i MPZP) na kontrolach: **tak**.

Różnica parametrów widocznych dla użytkownika (kontrola z parametrami, bez średnich):

- `control-rectangle-60-40` `max_building_height_m`: centroid [9.0], przecięcie A=9.0 (60.0%); B=12.0 (40.0%); różnica: **tak**.

## 2. Dolne granice utraty informacji z zamrożonych udziałów korpusu (dane rzeczywiste)

Jedno przypisanie pomija co najmniej `k−1` stref i co najmniej `100 − udział największej` procent działki, niezależnie od tego, gdzie wypadnie centroid. Wynik nie wymaga geometrii stref.

Przypadki z zamrożonymi udziałami stref: 11; gwarantowana utrata strefy: 0.273 (3/11); utrata strefy o udziale ≥ 0.1%: 0.273 (3/11).

| Przypadek | Warstwa | Strefy | Udziały % | Min. pominięte strefy | Min. pominięty obszar % | Maks. pominięty obszar % |
|---|---|---|---|---:|---:|---:|
| `real-005-126105-9-0001-580-4` | mpzp | KP.1 | 100.0000 | 0 | 0.0000 | 0.0000 |
| `real-006-126105-9-0001-49-2` | mpzp | MW/U.4 | 100.0000 | 0 | 0.0000 | 0.0000 |
| `real-007-126105-9-0001-540-15` | mpzp | KD/L+T,U.11,ZP.4 | 97.6289, 2.3287, 0.0417 | 2 | 2.3711 | 99.9583 |
| `real-008-126105-9-0119-181-10` | mpzp | ZPb.9 | 100.0000 | 0 | 0.0000 | 0.0000 |
| `real-009-246101-1-0056-155-3` | pog | SK | 99.9872 | 0 | 0.0128 | 0.0128 |
| `real-010-246101-1-0055-68-13` | pog | SW | 99.9991 | 0 | 0.0009 | 0.0009 |
| `real-011-246101-1-0081-7` | pog | SW | 100.0000 | 0 | 0.0000 | 0.0000 |
| `real-012-246101-1-0056-110-1` | pog | SU | 99.9978 | 0 | 0.0022 | 0.0022 |
| `real-027-246101-1-0001-1043` | pog | SJ | 99.9986 | 0 | 0.0014 | 0.0014 |
| `real-028-246101-1-0004-737-26` | pog | SO,SJ | 80.3407, 19.6593 | 1 | 19.6593 | 80.3407 |
| `real-029-246101-1-0004-741-132` | pog | SO,SJ | 50.0708, 49.9292 | 1 | 49.9292 | 50.0708 |

## 3. Wyniki dokładne na zamrożonych geometriach stref rzeczywistych

Brak zamrożonych geometrii stref rzeczywistych: pomiar dokładny dla poniższych przypadków nie został wykonany (nie jest zastąpiony żadną wartością).

| Przypadek | Warstwa | Powód braku |
|---|---|---|
| `real-005-126105-9-0001-580-4` | mpzp | source not redistributable (contract_required); geometry not frozen |
| `real-006-126105-9-0001-49-2` | mpzp | source not redistributable (contract_required); geometry not frozen |
| `real-007-126105-9-0001-540-15` | mpzp | source not redistributable (contract_required); geometry not frozen |
| `real-008-126105-9-0119-181-10` | mpzp | source not redistributable (contract_required); geometry not frozen |
| `real-009-246101-1-0056-155-3` | pog | zone geometry not frozen (RU WFS layer not captured) |
| `real-010-246101-1-0055-68-13` | pog | zone geometry not frozen (RU WFS layer not captured) |
| `real-011-246101-1-0081-7` | pog | zone geometry not frozen (RU WFS layer not captured) |
| `real-012-246101-1-0056-110-1` | pog | zone geometry not frozen (RU WFS layer not captured) |
| `real-027-246101-1-0001-1043` | pog | zone geometry not frozen (RU WFS layer not captured) |
| `real-028-246101-1-0004-737-26` | pog | zone geometry not frozen (RU WFS layer not captured) |
| `real-029-246101-1-0004-741-132` | pog | zone geometry not frozen (RU WFS layer not captured) |

## 4. Symulacja na rzeczywistych geometriach 30 działek (nie jest to zagospodarowanie gminy)

Dla każdej działki korpusu: 2 orientacje × 9 ułamków cięcia (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9) = 18 scenariuszy; dwie strefy to półpłaszczyzny przecięte w prostokącie otaczającym. Wszystkie scenariusze są raportowane, bez wyboru niekorzystnych.

Działki: 30; położenie centroidu względem działki: `{"in_hole":1,"inside_parcel":27,"outside_parcel":2}`; działki z dziurami: 2; działki o wypukłości < 0,95: 18.

### Wszystkie scenariusze

- przypadków: 540 (z przecięciem ze strefą: 540, wielostrefowych: 540);
- centroid pomija co najmniej jedną strefę: 1.000 (540/540);
- … istotną (udział ≥ 0.1%): 1.000 (540/540);
- wśród wielostrefowych: 1.000 (540/540);
- liczba pominiętych stref (suma): 540 (istotnych: 540); dla działek przecinających dwie strefy pominięcie strefy jest z definicji pewne (jedno przypisanie), więc informacyjna jest wielkość pominiętego obszaru;
- pominięty obszar działki ≥ próg: ≥1%: 0.993 (536/540); ≥5%: 0.835 (451/540); ≥10%: 0.711 (384/540); ≥25%: 0.426 (230/540); ≥50%: 0.009 (5/540);
- strefa centroidu ≠ strefa o największym polu (tylko rozstrzygnięte): 0.009 (5/540);
- … gdy remis/brak strefy liczymy jako „nie dominująca”: 0.009 (5/540);
- brak rozstrzygnięcia centroidu (remis lub brak strefy): 0.000 (0/540) `{"unique":540}`;
- centroid w strefie, która nie przecina działki (strefa-widmo): 0.000 (0/540);
- położenie centroidu: `{"in_hole":18,"inside_parcel":486,"outside_parcel":36}`.

### Scenariusze, w których działka przecina obie strefy

- przypadków: 540 (z przecięciem ze strefą: 540, wielostrefowych: 540);
- centroid pomija co najmniej jedną strefę: 1.000 (540/540);
- … istotną (udział ≥ 0.1%): 1.000 (540/540);
- wśród wielostrefowych: 1.000 (540/540);
- liczba pominiętych stref (suma): 540 (istotnych: 540); dla działek przecinających dwie strefy pominięcie strefy jest z definicji pewne (jedno przypisanie), więc informacyjna jest wielkość pominiętego obszaru;
- pominięty obszar działki ≥ próg: ≥1%: 0.993 (536/540); ≥5%: 0.835 (451/540); ≥10%: 0.711 (384/540); ≥25%: 0.426 (230/540); ≥50%: 0.009 (5/540);
- strefa centroidu ≠ strefa o największym polu (tylko rozstrzygnięte): 0.009 (5/540);
- … gdy remis/brak strefy liczymy jako „nie dominująca”: 0.009 (5/540);
- brak rozstrzygnięcia centroidu (remis lub brak strefy): 0.000 (0/540) `{"unique":540}`;
- centroid w strefie, która nie przecina działki (strefa-widmo): 0.000 (0/540);
- położenie centroidu: `{"in_hole":18,"inside_parcel":486,"outside_parcel":36}`.

### Według wypukłości działki (solidity = pole / pole otoczki wypukłej; scenariusze dwustrefowe)

| Przedział | działki | scenariusze | pominięty obszar ≥ 10% | pominięty obszar ≥ 25% | centroid ≠ dominująca (rozstrzygnięte) | bez rozstrzygnięcia |
|---|---:|---:|---|---|---|---|
| `solidity<0.80` | 10 | 180 | 0.756 (136/180) | 0.472 (85/180) | 0.028 (5/180) | 0.000 (0/180) |
| `0.80<=solidity<0.95` | 8 | 144 | 0.715 (103/144) | 0.431 (62/144) | 0.000 (0/144) | 0.000 (0/144) |
| `solidity>=0.95` | 12 | 216 | 0.671 (145/216) | 0.384 (83/216) | 0.000 (0/216) | 0.000 (0/216) |

## Ograniczenia

- geometrie stref rzeczywistych nie są zamrożone (host Rejestru Urbanistycznego był nieosiągalny podczas badania); do czasu ich zamrożenia sekcja 3 jest pusta, a wnioski o dokładnym odsetku działek z pominięciem strefy opierają się na granicach (sekcja 2) i symulacji (sekcja 4);
- warstwy MPZP Krakowa nie mogą być redystrybuowane (katalog: `contract_required`), więc ich geometrii nie zamrożono nawet po odzyskaniu dostępu; dla nich zostają granice z zamrożonych udziałów;
- symulacja pokazuje własność metody centroidu na rzeczywistych kształtach działek, nie częstość granic stref w Polsce; liczby zależą od przyjętej rodziny cięć;
- aplikacja produkcyjna nie przypisuje stref po samym centroidzie (discovery MPZP używa wielu punktów, a analiza POG i MPZP — pełnych przecięć); eksperyment uzasadnia tę decyzję, nie mierzy błędu produktu.
