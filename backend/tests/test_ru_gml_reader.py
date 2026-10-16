"""Czytnik pliku GML aktu POG z Rejestru Urbanistycznego (ADR-026) na zamrożonych, rzeczywistych plikach."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pyproj import Transformer
from shapely import from_wkt
from shapely.geometry import Point

from app.modules.imports.domain.pog import PogValidationError
from app.modules.imports.infrastructure.pog.reader import (
    assemble_ru_pog_acts,
    parse_ru_app_feature_collection,
)
from app.modules.imports.infrastructure.pog.ru_gml_reader import RuGmlError, RuGmlPogReader, RuGmlSource
from app.services.gml import GmlResponseError, UnsupportedCrsError

FIXTURES = Path(__file__).parent / "fixtures" / "ru_national"
WFS = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"
# (publishedActId, TERC, kod układu w pliku, status prawny)
ACTS = {
    1152: ("241203", 2177, "binding"),  # Jejkowice
    1735: ("200901", 2179, "binding"),  # Sejny (inna strefa PL-2000 niż Jejkowice)
    295: ("226301", 2177, "in_progress"),  # Słupsk, akt w opracowaniu
    1894: ("281401", 2178, "in_progress"),  # Barczewo, akt w opracowaniu, tylko granica
}


def _source(act_id: int, content: bytes | None = None) -> RuGmlSource:
    terc = ACTS[act_id][0]
    return RuGmlSource(
        content=content if content is not None else (FIXTURES / f"act_{act_id}.gml").read_bytes(),
        filename=f"act_{act_id}.gml",
        published_act_id=act_id,
        terc=terc,
        source_reference=WFS,
    )


@pytest.mark.parametrize("act_id", sorted(ACTS))
def test_real_gml_files_are_declared_in_the_expected_pl2000_zone(act_id: int) -> None:
    text = (FIXTURES / f"act_{act_id}.gml").read_text(encoding="utf-8")
    codes = {int(code) for code in re.findall(r'srsName="[^"]*?/EPSG/0/(\d+)"', text)}
    assert codes == {ACTS[act_id][1]}


@pytest.mark.parametrize("act_id", sorted(ACTS))
def test_reader_returns_exactly_one_act_with_every_object_of_the_file(act_id: int) -> None:
    text = (FIXTURES / f"act_{act_id}.gml").read_text(encoding="utf-8")
    expected = {
        "planning_zone": text.count("<app:StrefaPlanistyczna "),
        "ouz": text.count("<app:ObszarUzupelnieniaZabudowy "),
        "downtown_area": text.count("<app:ObszarZabudowySrodmiejskiej "),
        "social_infrastructure_standard": text.count("<app:ObszarStandardowDostepnosciInfrastrukturySpolecznej "),
    }

    batch = RuGmlPogReader(_source(act_id)).read()

    (act,) = batch.acts
    assert act.legal_status == ACTS[act_id][2] and act.teryt == ACTS[act_id][0]
    assert {kind: count for kind, count in act.feature_counts().items() if count} == {
        kind: count for kind, count in expected.items() if count
    }
    assert batch.content == (FIXTURES / f"act_{act_id}.gml").read_bytes(), "artefakt to bajty pliku z RU"
    assert batch.source_tokens == {act.act_identifier: act_id}
    assert batch.complete and batch.strict_identifiers and batch.parser_config_id == "ru-app-3.0-gml-v1"
    assert batch.media_type == "application/gml+xml"


def test_geometry_is_reprojected_to_epsg_2180_with_the_registry_axis_order() -> None:
    """Niezależny pomiar: pierwszy wierzchołek pierwszej strefy z surowego pliku (północ, wschód w EPSG:2177)."""
    text = (FIXTURES / "act_1152.gml").read_text(encoding="utf-8")
    block = text[text.index("<app:StrefaPlanistyczna ") :]
    pos_list = re.search(r"<gml:posList[^>]*>([^<]+)</gml:posList>", block).group(1).split()  # type: ignore[union-attr]
    northing, easting = float(pos_list[0]), float(pos_list[1])
    expected = Transformer.from_crs("EPSG:2177", "EPSG:2180", always_xy=True).transform(easting, northing)

    batch = RuGmlPogReader(_source(1152)).read()
    zone = next(f for f in batch.acts[0].features if f.feature_type == "planning_zone")
    ring = list(from_wkt(zone.geometry.wkt).geoms[0].exterior.coords if zone.geometry.wkt.startswith("MULTI")
                else from_wkt(zone.geometry.wkt).exterior.coords)
    assert ring[0] == pytest.approx(expected, abs=0.01)
    # 2180: wschód ~ 300–900 tys., północ ~ 100–800 tys.; Jejkowice (Śląskie) leżą w tym przedziale.
    assert 300_000 < expected[0] < 900_000 and 100_000 < expected[1] < 800_000


@pytest.mark.parametrize("act_id", [1152, 1735])
def test_zones_stay_inside_the_act_boundary_in_the_canonical_crs(act_id: int) -> None:
    batch = RuGmlPogReader(_source(act_id)).read()
    act = batch.acts[0]
    assert act.boundary is not None
    boundary = from_wkt(act.boundary.wkt)
    zones = [from_wkt(f.geometry.wkt) for f in act.features if f.feature_type == "planning_zone"]
    assert zones
    outside = [zone for zone in zones if not boundary.buffer(5).contains(zone.representative_point())]
    assert not outside, "strefy poza granicą aktu wskazywałyby błędną oś albo układ"
    assert 1_000_000 < boundary.area < 5_000_000_000


def test_resolution_number_and_date_come_from_the_resolution_document() -> None:
    objects = parse_ru_app_feature_collection(
        (FIXTURES / "act_1152.gml").read_bytes(), source_reference=WFS, reproject=True
    )
    (act,) = assemble_ru_pog_acts(objects)
    document = next(item for item in act.documents if item.relation == "uchwala")
    assert act.resolution_number == document.identification_number
    assert act.resolution_date == document.document_date
    # Uwaga o jakości źródła: w schemacie APP 2.0 pole numerIdentyfikacyjny bywa odnośnikiem do dziennika
    # urzędowego, a numer uchwały jest w tytule dokumentu — dlatego widok pokazuje też tytuł dokumentu.
    assert act.resolution_number and document.title and "Uchwała nr" in document.title
    assert document.effective_date is not None and "dziennikUrzedowy" in document.raw_attributes


def test_draft_act_with_only_a_boundary_is_read_without_features() -> None:
    (act,) = RuGmlPogReader(_source(1894)).read().acts
    assert act.legal_status == "in_progress" and act.boundary is not None
    assert not any(act.feature_counts().values())


def test_terc_mismatch_between_catalog_and_gml_is_rejected() -> None:
    wrong = RuGmlSource(
        content=(FIXTURES / "act_1152.gml").read_bytes(), filename="x.gml", published_act_id=1152,
        terc="999999", source_reference=WFS,
    )
    with pytest.raises(RuGmlError) as error:
        RuGmlPogReader(wrong).read()
    assert error.value.code == "gml_terc_mismatch"


def test_file_with_two_acts_is_rejected() -> None:
    first = (FIXTURES / "act_1152.gml").read_text(encoding="utf-8")
    act = first[first.index("<wfs:member>") : first.index("</wfs:member>") + len("</wfs:member>")]
    assert "AktPlanowaniaPrzestrzennego" in act
    doubled = first.replace(act, act + act, 1)
    with pytest.raises(RuGmlError) as error:
        RuGmlPogReader(_source(1152, doubled.encode("utf-8"))).read()
    assert error.value.code == "gml_multiple_acts"


def test_file_without_an_act_is_rejected() -> None:
    root = b'<?xml version="1.0"?><gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2"/>'
    with pytest.raises(RuGmlError) as error:
        RuGmlPogReader(_source(1152, root)).read()
    assert error.value.code == "gml_no_act"


def test_geometry_without_srs_name_and_unsupported_crs_are_rejected_not_guessed() -> None:
    text = (FIXTURES / "act_1894.gml").read_text(encoding="utf-8")
    no_srs = re.sub(r' srsName="[^"]+"', "", text)
    with pytest.raises(PogValidationError, match="srsName"):
        RuGmlPogReader(_source(1894, no_srs.encode("utf-8"))).read()
    exotic = text.replace("/EPSG/0/2178", "/EPSG/0/3035")
    with pytest.raises((PogValidationError, UnsupportedCrsError, GmlResponseError), match="3035"):
        RuGmlPogReader(_source(1894, exotic.encode("utf-8"))).read()


def test_dtd_and_entities_are_rejected_as_untrusted_input() -> None:
    text = (FIXTURES / "act_1894.gml").read_text(encoding="utf-8")
    hostile = text.replace(
        "<wfs:FeatureCollection", '<!DOCTYPE x [<!ENTITY e "boom">]>\n<wfs:FeatureCollection', 1
    )
    assert "<!DOCTYPE" in hostile
    with pytest.raises(PogValidationError):
        RuGmlPogReader(_source(1894, hostile.encode("utf-8"))).read()


def test_far_apart_communes_do_not_overlap_in_the_canonical_crs() -> None:
    jejkowice = RuGmlPogReader(_source(1152)).read().acts[0]
    sejny = RuGmlPogReader(_source(1735)).read().acts[0]
    a, b = from_wkt(jejkowice.boundary.wkt), from_wkt(sejny.boundary.wkt)  # type: ignore[union-attr]
    assert not a.intersects(b) and a.distance(b) > 200_000  # południe vs północny wschód Polski
    assert Point(a.representative_point()).distance(Point(b.representative_point())) > 200_000
