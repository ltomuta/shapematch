"""Measure native loading, extraction, registration and Boolean-free preview separately.

Run from an installed checkout: python tools/app_profile.py --output /tmp/app-profile.json
These are measurements, not performance promises; spawn startup is recorded separately.
"""
import argparse
import json
from pathlib import Path
import tempfile
import time

from shapematch_app.workers import Supervisor
from shapematch_cache import source_hash, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", type=Path, nargs="*")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "data/primitives"
    paths = args.paths or [root / name for name in ("cube/unit_cube.stp", "cylinder/unit_cyl.stp", "miscellanea/CubePyramid.stp")]
    worker = Supervisor()
    measurements = []
    with tempfile.TemporaryDirectory() as directory:
        for index, path in enumerate(paths):
            cache = str(Path(directory) / f"{index}.json")
            started = time.perf_counter()
            extracted = worker.run("extract", {"path": str(path), "hash": source_hash(path), "cache": cache})
            extraction_wall = time.perf_counter() - started
            data = {"query": str(path), "query_cache": cache, "path": str(path), "cache": cache}
            compared = worker.run("compare", data)
            preview = worker.run("preview", {**data, "comparison": compared})
            measurements.append({"fixture": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
                                 **extracted["timings"], **compared["timings"], **preview["timings"],
                                 "extraction_worker_wall": extraction_wall,
                                 "self_normalized_rms": compared["normalized_alignment_error"]})
    write_json(args.output, {"measurements": measurements})
    print(json.dumps(measurements, indent=2))


if __name__ == "__main__":
    main()
