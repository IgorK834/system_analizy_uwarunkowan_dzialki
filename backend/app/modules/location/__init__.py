"""Moduł ``location`` — wyszukiwanie i geokodowanie adresów.

Realizuje produkcyjną wyszukiwarkę adresów (Faza 11.1) w podziale warstw
modularnego monolitu: ``domain`` (czysta logika rankingu, normalizacji i
dopasowań), ``application`` (port dostawcy i przypadek użycia), ``infrastructure``
(adapter UUG/EMUiA przez httpx z guardem katalogu źródeł) oraz ``api`` (endpoint
``/api/v1/search/addresses`` z limitem zapytań i kontraktem ErrorResponse).
"""
