"""AU-113: replay 20 real UUG responses, including all six audit queries."""
import hashlib
import json
from pathlib import Path

import pytest
import respx

from app.modules.location.application.ports import AddressSearchProviderError
from app.modules.location.domain.models import AddressQuery
from app.modules.location.domain.normalization import uug_query_variants
from app.modules.location.infrastructure.uug_adapter import UugAddressSearchProvider, _localities

FIXTURES = Path(__file__).parent / "fixtures/uug/au113"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["query"])
async def test_real_address_forms(case):
    with respx.mock(assert_all_called=True) as router:
        for attempt in case["attempts"]:
            payload = (FIXTURES / attempt["file"]).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == attempt["sha256"]
            router.get(MANIFEST["source"], params=attempt["params"]).respond(200, content=payload)
        result = await UugAddressSearchProvider().search(AddressQuery(q=case["query"]))
    assert len(result) == case["result_count"] > 0
    city = case["attempts"][-1]["params"]["address"].split(",")[0]
    assert result[0].parts.city == city
    assert all(14 < item.point.lon < 25 and 49 < item.point.lat < 55 for item in result)


@pytest.mark.parametrize("payload", [b"<html>server error</html>", b"[]", b"{}", b'{"results":[]}'])
async def test_schema_drift_is_a_provider_error(payload):
    with respx.mock() as router:
        router.get(MANIFEST["source"]).respond(200, content=payload)
        with pytest.raises(AddressSearchProviderError):
            await UugAddressSearchProvider().search(AddressQuery(q="Warszawa, Marszałkowska 1"))


def test_unknown_locality_variants_are_bounded_and_keep_house_number():
    variants = uug_query_variants("Długa 12a/3 Nieznana Wieś", _localities())
    assert variants[0] == "Nieznana Wieś, Długa 12a/3"
    assert len(variants) <= 6
    assert all("12a/3" in variant for variant in variants)
    assert uug_query_variants("Krakow", _localities()) == ("Kraków",)


async def test_missing_locality_dictionary_is_a_provider_error(monkeypatch):
    def missing():
        raise OSError("missing file")
    monkeypatch.setattr("app.modules.location.infrastructure.uug_adapter._localities", missing)
    with pytest.raises(AddressSearchProviderError, match="Słownik"):
        await UugAddressSearchProvider().search(AddressQuery(q="Warszawa, Marszałkowska 1"))


async def test_nonfinite_source_coordinates_are_not_empty_success():
    attempt = MANIFEST["cases"][0]["attempts"][0]
    data = json.loads((FIXTURES / attempt["file"]).read_bytes())
    for result in data["results"].values():
        result["x"] = "NaN"
    with respx.mock() as router:
        router.get(MANIFEST["source"]).respond(200, json=data)
        with pytest.raises(AddressSearchProviderError, match="współrzędnych"):
            await UugAddressSearchProvider().search(AddressQuery(q="Warszawa, Marszałkowska 1"))
