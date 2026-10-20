"""Freeze real OT_SULN_L classes with existing parcel-centred road/building fixtures.

No network; every source XML byte is preserved. Mini-packages are test excerpts,
not deployable county coverage. Source-package SHA-256 and full-class counts are recorded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from xml.etree import ElementTree


def build(packages: Path, output: Path, counties: list[str]) -> None:
    fixtures = Path(__file__).resolve().parents[1] / "tests/fixtures/bdot10k"
    for county in counties:
        target = output / county
        target.mkdir(parents=True, exist_ok=True)
        source = packages / f"{county}_GML.zip"
        with zipfile.ZipFile(source) as archive:
            name = next(name for name in archive.namelist() if name.endswith("__OT_SULN_L.xml"))
            payload = archive.read(name)
        root = ElementTree.fromstring(payload)
        ns = {"ot": "urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0"}
        features = root.findall(".//ot:OT_SULN_L", ns)
        kinds: dict[str, int] = {}
        for feature in features:
            kind = feature.findtext("ot:rodzaj", namespaces=ns)
            kinds[kind] = kinds.get(kind, 0) + 1
        with zipfile.ZipFile(fixtures / county / f"{county}_GML.zip") as base:
            contents = {member: base.read(member) for member in base.namelist()}
        contents[name] = payload
        fixture = target / f"{county}_GML.zip"
        with zipfile.ZipFile(fixture, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member, content in sorted(contents.items()):
                info = zipfile.ZipInfo(member, date_time=(2026, 10, 17, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        manifest = {"source_url": f"https://opendata.geoportal.gov.pl/bdot10k/schemat2021/{county[:2]}/{county}_GML.zip",
            "source_package_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_package_bytes": source.stat().st_size, "class_xml_sha256": hashlib.sha256(payload).hexdigest(),
            "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            "feature_count": len(features), "kinds": kinds, "attribution": "GUGiK — BDOT10k",
            "crs": "EPSG:2180", "scope": "whole OT_SULN_L class; road/building excerpt (tests only)"}
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--county", action="append", default=[])
    args = parser.parse_args()
    build(args.packages, args.output, args.county or ["1261", "0262", "2461"])
