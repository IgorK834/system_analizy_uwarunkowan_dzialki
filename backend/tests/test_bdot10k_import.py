"""Import paczki powiatowej BDOT10k (Task 23.1) na zamrożonych wycinkach prawdziwych paczek: czytnik GML, publikacja w
PostGIS, odtwarzalność (ten sam hash → te same liczności), wydania per powiat i pobieranie z allowlisty hostów."""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text

from app.core.data_sources import get_catalog
from app.db.session import SessionLocal
from app.models.bdot10k import BdotBuilding, BdotRelease, BdotRoad
from app.models.versioned import DataRelease, DataSource, ImportRun, SourceArtifact
from app.modules.imports.api.cli import parse_args
from app.modules.imports.application.bdot10k_import import REGRESSION_WARNING_RATIO
from app.modules.imports.application.common import ImportSourceNotRunnable
from app.modules.imports.composition import run_bdot10k_command
from app.modules.imports.domain.bdot10k import Bdot10kValidationError, scope_key
from app.modules.imports.infrastructure.bdot10k.download import (
    Bdot10kDownloadError,
    allowed_hosts_from,
    download_package,
    package_url,
)
from app.modules.imports.infrastructure.bdot10k.reader import (
    BDOT10K_ARCHIVE_LIMITS,
    Bdot10kReadError,
    ZipBdot10kReader,
    sha256_of,
)
from app.modules.imports.infrastructure.bdot10k.repository import release_label, stats_for_release
from app.shared.safe_archive import ArchiveLimits, UnsafeArchiveError
from tests.bdot_support import (
    POWIATS,
    expected,
    import_package,
    package_path,
    purge_bdot,
)

MAPPING = get_catalog().get("bdot10k").field_mapping
FREE_TEXT_FIELDS = {"nazwa", "informacjaDodatkowa", "uwagi", "identyfikatorEGiB"}


@pytest.fixture
def clean_bdot():
    with SessionLocal() as session:
        purge_bdot(session)
    yield
    with SessionLocal() as session:
        purge_bdot(session)


# --- czytnik ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("teryt", POWIATS)
def test_reader_returns_exactly_the_objects_of_the_frozen_package_in_epsg2180(teryt: str) -> None:
    batch = ZipBdot10kReader(package_path(teryt), teryt_powiat=teryt, mapping=MAPPING).read()

    assert (len(batch.roads), len(batch.buildings)) == (
        expected(teryt)["road_feature_count"],
        expected(teryt)["building_feature_count"],
    )
    assert len(batch.boundaries) == 1  # fixture niesie wyłącznie obiekt powiatu
    assert batch.package_sha256 == sha256_of(package_path(teryt)) == expected(teryt)["fixture_package"]["sha256"]
    assert all(feature.geometry_wkb for feature in (*batch.roads, *batch.buildings, *batch.boundaries))


def test_reader_never_exposes_free_text_or_the_egib_identifier() -> None:
    batch = ZipBdot10kReader(package_path("1261"), teryt_powiat="1261", mapping=MAPPING).read()

    seen = {name for feature in (*batch.roads, *batch.buildings) for name in feature.attributes}

    assert seen and not (seen & FREE_TEXT_FIELDS)
    assert {"lokalnyId", "kategoriaIstnienia", "wersja"} <= seen


def test_reader_rejects_a_package_of_another_powiat() -> None:
    with pytest.raises(Bdot10kValidationError, match="powiatu 1261"):
        ZipBdot10kReader(package_path("1261"), teryt_powiat="1465", mapping=MAPPING).read()


def test_reader_reports_a_missing_file_and_a_corrupt_archive(tmp_path: Path) -> None:
    with pytest.raises(Bdot10kReadError, match="nie istnieje"):
        ZipBdot10kReader(tmp_path / "brak.zip", teryt_powiat="1261", mapping=MAPPING).read()
    broken = tmp_path / "1261_GML.zip"
    broken.write_bytes(b"to nie jest archiwum")
    with pytest.raises(Exception, match="ZIP"):
        ZipBdot10kReader(broken, teryt_powiat="1261", mapping=MAPPING).read()


def _repack(source: Path, target: Path, *, drop: str | None = None, rename: tuple[str, str] | None = None) -> Path:
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for info in archive.infolist():
            if drop and info.filename.endswith(drop):
                continue
            name = info.filename.replace(*rename) if rename else info.filename
            out.writestr(name, archive.read(info))
    return target


def test_package_without_a_required_class_is_rejected_by_name(tmp_path: Path) -> None:
    repacked = _repack(package_path("0262"), tmp_path / "0262_GML.zip", drop="__OT_BUBD_A.xml")

    with pytest.raises(Bdot10kReadError, match="OT_BUBD_A"):
        ZipBdot10kReader(repacked, teryt_powiat="0262", mapping=MAPPING).read()


def test_package_declaring_another_coordinate_system_is_not_guessed(tmp_path: Path) -> None:
    with zipfile.ZipFile(package_path("0262")) as archive:
        names = archive.namelist()
        road = next(name for name in names if name.endswith("__OT_SKJZ_L.xml"))
        with zipfile.ZipFile(tmp_path / "0262_GML.zip", "w") as out:
            for name in names:
                data = archive.read(name)
                out.writestr(name, data.replace(b'srsName="EPSG:2180"', b'srsName="EPSG:4326"') if name == road else data)

    with pytest.raises(Bdot10kReadError, match="EPSG:4326|układ"):
        ZipBdot10kReader(tmp_path / "0262_GML.zip", teryt_powiat="0262", mapping=MAPPING).read()


def test_archive_that_escapes_the_target_directory_is_refused(tmp_path: Path) -> None:
    evil = tmp_path / "0262_GML.zip"
    with zipfile.ZipFile(evil, "w") as out:
        out.writestr("../../PL.PZGiK.1.0262__OT_SKJZ_L.xml", b"<x/>")

    with pytest.raises(UnsafeArchiveError):
        ZipBdot10kReader(evil, teryt_powiat="0262", mapping=MAPPING).read()


def test_archive_limits_stop_a_package_that_decompresses_too_much(tmp_path: Path) -> None:
    tiny = ArchiveLimits(max_entries=200, max_total_uncompressed_bytes=1024, max_entry_uncompressed_bytes=1024)
    with pytest.raises(UnsafeArchiveError):
        ZipBdot10kReader(package_path("0262"), teryt_powiat="0262", mapping=MAPPING, limits=tiny).read()
    assert BDOT10K_ARCHIVE_LIMITS.max_total_uncompressed_bytes >= 2 * 1024**3  # największe realne paczki ~0,8 GB


# --- publikacja w PostGIS -----------------------------------------------------------------------------


@pytest.mark.integration
def test_each_frozen_package_is_published_with_its_counts_extent_and_provenance(tmp_path: Path, clean_bdot) -> None:
    with SessionLocal() as session:
        for teryt in POWIATS:
            outcome = import_package(session, teryt, tmp_path)
            stats = outcome.stats
            assert outcome.status == "succeeded" and outcome.data_release_id and outcome.import_run_id
            assert (stats["roads_imported"], stats["buildings_imported"]) == (
                expected(teryt)["road_feature_count"],
                expected(teryt)["building_feature_count"],
            )
            assert stats["roads_rejected"] == stats["buildings_rejected"] == 0
            assert stats["created"] == 1 and stats["package_sha256"] == expected(teryt)["fixture_package"]["sha256"]

        releases = session.execute(select(DataRelease).join(DataSource).where(DataSource.source_id == "bdot10k")).scalars().all()
        assert {release.scope_key for release in releases} == {scope_key(teryt) for teryt in POWIATS}
        assert all(release.is_active for release in releases)  # sześć powiatów aktywnych równocześnie
        for teryt in POWIATS:
            info = session.execute(select(BdotRelease).where(BdotRelease.teryt_powiat == teryt)).scalar_one()
            assert info.package_sha256 == expected(teryt)["fixture_package"]["sha256"]
            assert info.road_count == expected(teryt)["road_feature_count"]
            assert info.data_as_of is not None and info.imported_at is not None
            in_table = stats_for_release(session, info.data_release_id)
            assert in_table["roads_in_table"] == in_table["road_count"] == info.road_count
            assert in_table["buildings_in_table"] == in_table["building_count"] == info.building_count
        area = session.execute(text("SELECT ST_Area(extent) FROM bdot_releases WHERE teryt_powiat = '1261'")).scalar_one()
        assert area > 1e8  # granica powiatu, nie zerowy obrys (Kraków ~ 327 km²)


@pytest.mark.integration
def test_the_same_package_hash_gives_the_same_release_and_the_same_object_counts(tmp_path: Path, clean_bdot) -> None:
    with SessionLocal() as session:
        first = import_package(session, "0262", tmp_path)
        counts_before = (
            session.execute(select(func.count()).select_from(BdotRoad)).scalar_one(),
            session.execute(select(func.count()).select_from(BdotBuilding)).scalar_one(),
        )
        second = import_package(session, "0262", tmp_path)
        counts_after = (
            session.execute(select(func.count()).select_from(BdotRoad)).scalar_one(),
            session.execute(select(func.count()).select_from(BdotBuilding)).scalar_one(),
        )

        assert second.data_release_id == first.data_release_id  # to samo wydanie, nie kolejne
        assert counts_before == counts_after == (
            expected("0262")["road_feature_count"],
            expected("0262")["building_feature_count"],
        )
        assert (first.stats["created"], second.stats["created"]) == (1, 0)
        assert second.stats["activated"] == 0  # było już aktywne
        assert second.import_run_id != first.import_run_id  # przebieg jest zapisany także dla powtórki
        label = release_label("0262", expected("0262")["fixture_package"]["sha256"])
        assert session.execute(select(DataRelease.version_label).where(DataRelease.id == first.data_release_id)).scalar_one() == label


@pytest.mark.integration
def test_a_new_package_replaces_only_its_own_powiat_and_the_old_release_stays_for_rollback(
    tmp_path: Path, clean_bdot
) -> None:
    changed = tmp_path / "changed" / "1261_GML.zip"
    changed.parent.mkdir()
    shutil.copy(package_path("1261"), changed)
    # Inna paczka (inny hash): komentarz ZIP zmienia bajty archiwum, ale nie zawartość obiektów.
    with zipfile.ZipFile(changed, "a") as archive:
        archive.comment = b"inne wydanie"
    with SessionLocal() as session:
        old = import_package(session, "1261", tmp_path)
        other = import_package(session, "0262", tmp_path)
        new = import_package(session, "1261", tmp_path, package=changed)

        assert new.data_release_id not in {old.data_release_id, other.data_release_id}
        flags = {
            row.id: row.is_active
            for row in session.execute(select(DataRelease).join(DataSource).where(DataSource.source_id == "bdot10k")).scalars()
        }
        assert flags == {old.data_release_id: False, other.data_release_id: True, new.data_release_id: True}
        # Dane poprzedniego wydania zostały (rollback = ponowna aktywacja), nie zostały usunięte.
        assert session.execute(
            select(func.count()).select_from(BdotRoad).where(BdotRoad.data_release_id == old.data_release_id)
        ).scalar_one() == expected("1261")["road_feature_count"]


@pytest.mark.integration
def test_dry_run_validates_and_counts_but_writes_nothing(tmp_path: Path, clean_bdot) -> None:
    with SessionLocal() as session:
        outcome = import_package(session, "2816", tmp_path, dry_run=True)

        assert outcome.status == "dry_run_only" and outcome.data_release_id is None
        assert outcome.stats["roads_imported"] == expected("2816")["road_feature_count"]
        assert session.execute(select(func.count()).select_from(BdotRoad)).scalar_one() == 0
        assert session.execute(
            select(func.count()).select_from(DataRelease).join(DataSource).where(DataSource.source_id == "bdot10k")
        ).scalar_one() == 0


@pytest.mark.integration
def test_publication_is_refused_when_the_catalog_does_not_confirm_the_source(tmp_path: Path, clean_bdot) -> None:
    from app.modules.imports.application.bdot10k_import import run_bdot10k_import
    from app.modules.imports.application.common import ImportRelease
    from app.modules.imports.infrastructure.bdot10k.geometry import ShapelyGeometryTools

    release = ImportRelease(
        source_id="bdot10k", version_label="x", published_at=datetime.now(timezone.utc), publication_allowed=False
    )
    with pytest.raises(ImportSourceNotRunnable):
        run_bdot10k_import(
            ZipBdot10kReader(package_path("0262"), teryt_powiat="0262", mapping=MAPPING),
            release, ShapelyGeometryTools(), None, MAPPING, teryt_powiat="0262",
        )


@pytest.mark.integration
def test_a_package_much_smaller_than_the_active_release_is_published_with_a_regression_warning(
    tmp_path: Path, clean_bdot
) -> None:
    keep = 10  # zachowujemy 10 dróg z paczki 0262 — o wiele mniej niż 80% poprzedniego wydania
    source = package_path("0262")
    with zipfile.ZipFile(source) as archive:
        road_name = next(name for name in archive.namelist() if name.endswith("__OT_SKJZ_L.xml"))
        text_xml = archive.read(road_name).decode("utf-8")
        head, *members = text_xml.split("<gml:featureMember>")
        trimmed = head + "".join("<gml:featureMember>" + member for member in members[:keep])
        trimmed = trimmed.rsplit("</gml:featureMember>", 1)[0] + "</gml:featureMember></gml:FeatureCollection>"
        target = tmp_path / "small" / "0262_GML.zip"
        target.parent.mkdir()
        with zipfile.ZipFile(target, "w") as out:
            for name in archive.namelist():
                out.writestr(name, trimmed.encode("utf-8") if name == road_name else archive.read(name))
    with SessionLocal() as session:
        import_package(session, "0262", tmp_path)
        outcome = import_package(session, "0262", tmp_path, package=target)

        assert outcome.stats["roads_imported"] == keep
        assert any(item.startswith("regression:roads:") for item in outcome.warnings)
        assert keep < expected("0262")["road_feature_count"] * REGRESSION_WARNING_RATIO


@pytest.mark.integration
def test_the_original_package_is_stored_content_addressed_with_its_sha256(tmp_path: Path, clean_bdot) -> None:
    with SessionLocal() as session:
        outcome = import_package(session, "0262", tmp_path)
        artifact = session.execute(select(SourceArtifact).where(SourceArtifact.content_hash == expected("0262")["fixture_package"]["sha256"])).scalar_one()
        run = session.get(ImportRun, outcome.import_run_id)

        stored = Path(artifact.uri)
        assert stored.is_file() and hashlib.sha256(stored.read_bytes()).hexdigest() == artifact.content_hash
        assert artifact.media_type == "application/zip" and artifact.size_bytes == package_path("0262").stat().st_size
        assert run.status == "succeeded" and run.stats["created"] is True
        assert run.checkpoint["artifact_hash"] == artifact.content_hash


# --- polecenie CLI i pobieranie ----------------------------------------------------------------------


def test_cli_parses_the_bdot10k_command_with_optional_package() -> None:
    command = parse_args(["bdot10k", "--powiat", "1261", "--package", "/data/1261_GML.zip", "--dry-run"])
    assert (command.name, command.source_id, command.powiat, command.package, command.dry_run) == (
        "bdot10k", "bdot10k", "1261", "/data/1261_GML.zip", True
    )
    assert parse_args(["bdot10k", "--powiat", "1465"]).package is None
    with pytest.raises(SystemExit):
        parse_args(["bdot10k"])  # --powiat jest wymagany


@pytest.mark.integration
def test_command_runs_a_local_package_and_a_dry_run_through_the_composition_root(tmp_path: Path, clean_bdot) -> None:
    with SessionLocal() as session:
        dry = run_bdot10k_command(session, source_id="bdot10k", dry_run=True, powiat="0262", package=str(package_path("0262")))
        assert dry.status == "dry_run_only" and dry.stats["teryt_powiat"] == "0262"
        with pytest.raises(Bdot10kValidationError):
            run_bdot10k_command(session, source_id="bdot10k", dry_run=True, powiat="12", package=str(package_path("0262")))


def test_package_url_comes_from_the_catalog_template() -> None:
    template = next(item.url for item in get_catalog().get("bdot10k").resources if item.role == "county_package")
    assert package_url(template, "1261") == "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/12/1261_GML.zip"
    assert package_url(template, "0262").endswith("/02/0262_GML.zip")
    assert allowed_hosts_from([template]) == {"opendata.geoportal.gov.pl"}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_download_streams_to_a_directory_and_hashes_while_writing(tmp_path: Path) -> None:
    payload = package_path("0262").read_bytes()
    hosts = frozenset({"opendata.geoportal.gov.pl"})
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=payload, headers={"content-length": str(len(payload))})

    result = download_package(
        "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/02/0262_GML.zip",
        tmp_path / "dl", allowed_hosts=hosts, max_bytes=10**7, timeout_seconds=5, client=_client(handler),
    )

    assert result.sha256 == hashlib.sha256(payload).hexdigest() and result.size_bytes == len(payload)
    assert result.path.read_bytes() == payload and result.path.parent == tmp_path / "dl"
    assert seen == ["https://opendata.geoportal.gov.pl/bdot10k/schemat2021/02/0262_GML.zip"]


@pytest.mark.parametrize(
    "url",
    [
        "http://opendata.geoportal.gov.pl/bdot10k/schemat2021/02/0262_GML.zip",  # nie https
        "https://evil.example.test/bdot10k/schemat2021/02/0262_GML.zip",  # host poza listą
        "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/02/0262.txt",  # nie ZIP
    ],
)
def test_download_refuses_unsafe_addresses_without_any_request(tmp_path: Path, url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - nie może być wywołany
        raise AssertionError("żądanie nie powinno wyjść")

    with pytest.raises(Bdot10kDownloadError):
        download_package(url, tmp_path, allowed_hosts=frozenset({"opendata.geoportal.gov.pl"}), max_bytes=10**7,
                         timeout_seconds=5, client=_client(handler))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(302, headers={"location": "https://evil.example.test/x.zip"}), "HTTP 302"),  # brak przekierowań
        (httpx.Response(200, content=b""), "pustą paczkę"),
        (httpx.Response(200, content=b"x" * 50, headers={"content-length": "50"}), "limit"),
    ],
)
def test_download_failures_are_controlled_errors_and_leave_no_partial_file(
    tmp_path: Path, response: httpx.Response, message: str
) -> None:
    with pytest.raises(Bdot10kDownloadError, match=message):
        download_package(
            "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/02/0262_GML.zip", tmp_path,
            allowed_hosts=frozenset({"opendata.geoportal.gov.pl"}), max_bytes=10, timeout_seconds=5,
            client=_client(lambda request: response),
        )
    assert not list(tmp_path.glob("*.zip"))


def test_a_connection_error_does_not_leak_the_url_in_the_message(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom https://opendata.geoportal.gov.pl/secret")

    with pytest.raises(Bdot10kDownloadError) as caught:
        download_package(
            "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/02/0262_GML.zip", tmp_path,
            allowed_hosts=frozenset({"opendata.geoportal.gov.pl"}), max_bytes=10**7, timeout_seconds=5,
            client=_client(handler),
        )
    assert "secret" not in str(caught.value) and "ConnectError" in str(caught.value)
