"""Build a separate BK-602 corpus extension from an unmodified ULDK response.

The original reference corpus stays unchanged. Use GetParcelByXY with
result=id,geom_wkt and srid=2180; supply its captured response, not drawn geometry.
"""
import argparse
import hashlib
import json
from pathlib import Path

from shapely import wkt
from shapely.geometry import mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--uldk-response", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.corpus.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    for artifact in manifest["artifacts"].values():
        artifact["path"] = str(Path("../reference_corpus") / artifact["path"])
    raw = args.uldk_response.read_bytes()
    identifier, geom = raw.decode().strip().splitlines()[1].split("|", 1)
    polygon = wkt.loads(geom.removeprefix("SRID=2180;"))
    payload = (json.dumps({"type": "Feature", "properties": {"parcel_identifier": identifier},
                           "geometry": mapping(polygon)}, ensure_ascii=False, indent=2) + "\n").encode()
    (args.output / "sopot.geojson").write_bytes(payload)
    (args.output / "sopot-uldk.txt").write_bytes(raw)
    case_id = "supplement-031-226401-1-0002-2-66"
    artifact_id = f"geometry:{case_id}"
    manifest["artifacts"][artifact_id] = {"path": "sopot.geojson", "sha256": hashlib.sha256(payload).hexdigest(),
        "crs": "EPSG:2180", "source_id": "uldk", "response_sha256": hashlib.sha256(raw).hexdigest(),
        "request": {"url": "https://uldk.gugik.gov.pl/", "params": {"request": "GetParcelByXY",
                    "xy": "470071.25334799814,732570.4251,2180", "result": "id,geom_wkt", "srid": "2180"}}}
    empty = {"status": "unknown", "values": {"zones": []}}
    manifest["cases"].append({"case_id": case_id, "municipality": "Sopot", "artifact_ids": [artifact_id],
                              "expected": {"pog": empty, "mpzp": empty},
                              "purpose": "AU-112 third municipality; no historical ground-truth claims"})
    manifest["corpus_id"] += "+au112-sopot"
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
