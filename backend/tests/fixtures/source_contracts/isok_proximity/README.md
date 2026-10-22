# ISOK — kontrakt bliskości (AU-205)

Kontrakt `nz-core:HazardArea` zweryfikowano ponownie 20.10.2026 na publicznym
WFS PGW Wody Polskie. GetCapabilities miał SHA-256
`0b4309d9a327807f01ce82fca6735454bf6ad4b84b6567d0e581d5c37dddb5a5`.
Pełna odpowiedź dla bufora 1 km działki `141801_4.0701.23/8` zawierała 7
rzeczywistych cech i miała SHA-256
`d44b0f143937456be04f52c8b4c688209105e96726d5f3170c9963172cd24794`.
Ze względu na rozmiar 15,7 MB nie duplikuje istniejącej dużej odpowiedzi w Git;
regresję parsera opiera się nadal na nieprzetworzonym
`../isok_getfeature.xml`, a `gora-kalwaria-q10.json` zamraża identyfikator,
atrybuty i niezależny pomiar Shapely na pełnej odpowiedzi.

GetCapabilities potwierdza także `nz-core:RiskZone` i
`nz-core:ExposedElement`, lecz ich ogólna semantyka INSPIRE nie potwierdza
równoważności z produktem WORP OnGeo. Ocena WORP pozostaje
`contract_required` i nie jest wyliczana.

