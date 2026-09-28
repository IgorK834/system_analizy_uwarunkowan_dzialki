"""Wspólne fixture'y testów backendu."""

from __future__ import annotations

import pytest

from app.core.settings import settings
from app.modules.analysis.infrastructure.terrain_raster import clear_native_grid_cache


@pytest.fixture(autouse=True)
def terrain_relief_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zwykłe CI nie odpytuje WCS NMT (BK-302).

    Pochodne rastra są domyślnie wyłączone, a testy BK-302 włączają je jawnie i
    wstrzykują zamrożone odpowiedzi z ``tests/fixtures/terrain``. Pamięć
    DescribeCoverage jest czyszczona, aby testy nie zależały od kolejności.
    """
    monkeypatch.setattr(settings, "terrain_relief_enabled", False)
    clear_native_grid_cache()
