"""Capture isolated, timed FreeCAD or standalone research runs (not a runtime dependency)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import importlib.util
import io
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ["cube/unit_cube.stp", "sphere/unit_sphere.stp",
            "cylinder/unit_cyl.stp", "torus/unit_torus.stp",
            "miscellanea/tetrahedron.scaled.stp"]
FEATURES = ["featureMaxPoints", "featureMinPoints", "featureMaxCentres",
            "featureMinCentres", "spherePoints", "featureSphereDisps"]


def finite(value):
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [finite(v) for v in value]
    if hasattr(value, "tolist"):
        return finite(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def worker(backend, fixture, seed):
    import numpy as np
    random.seed(seed)
    np.random.seed(seed)
    result = {"fixture": fixture, "seed": seed, "backend": backend,
              "python": sys.version, "numpy": np.__version__}
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            if backend == "freecad":
                sys.path.extend(["/usr/lib/freecad-python3/lib", "/usr/lib/python3/dist-packages"])
                # Compatibility shim only for the unmodified historical implementation.
                np.Infinity = np.inf
                np.asfarray = lambda p: np.asarray(p, dtype=float)
                np.mat = np.asmatrix
                spec = importlib.util.spec_from_file_location("reference_legacy", ROOT / "legacy/shapematch.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                result["kernel"] = module.F_app.Version()
            else:
                sys.path.insert(0, str(ROOT / "src"))
                import shapematch as module
                from importlib.metadata import version
                result["kernel"] = version("cadquery-ocp")
            module.isVerbose = False
            module.viewerFlag = module.generativeSceneFlag = False
            module.playsound = lambda *args: None
            path = str(ROOT / "data/primitives" / fixture)
            transform = {"insertionPoint": module.Point(2, 3, 4), "scale": 2,
                         "rotation": 23 + seed, "rotationAxis": [1, 2, 3]}
            result["transform"] = transform
            shape = module.STEPmodel()
            shape.importSTEPmodel(path, **transform)
            if backend == "freecad":
                diagonal = shape.objectHandle.BoundBox.DiagonalLength
            else:
                diagonal = shape.objectHandle.diagonal
            result["diagonal"] = diagonal
            origin = module.Point(2, 3, 4)
            result["intersections"] = []
            for direction in [(1, .31, .27), (-.4, 1, .13), (.2, .3, 1)]:
                endpoint = module.Point(*(a + b for a, b in zip(origin, direction)))
                hits = shape.rayModelIntersection2(origin, endpoint)
                result["intersections"].append(sorted([list(p) for p in hits]))
            # Persist geometry even if historical feature extraction subsequently fails.
            print(json.dumps(finite(result)), file=sys.__stdout__, flush=True)
            source = module.getModelFeatures2(path, origin, 2, 23 + seed, [1, 2, 3])
            if source is None:
                raise RuntimeError("No feature representation returned")
            result["features"] = {key: getattr(source, key) for key in FEATURES}
            print(json.dumps(finite(result)), file=sys.__stdout__, flush=True)
            target = module.getModelFeatures2(path, module.Point(-3, 1, 2), 3, 61, [2, 1, 1])
            negative_path = ROOT / "data/primitives" / (FIXTURES[2] if fixture == FIXTURES[0] else FIXTURES[0])
            negative = module.getModelFeatures2(str(negative_path), origin, 2, 0, [0, 0, 1])
            result["histograms"] = {"positive": module.distanceRank2(source, target),
                                    "negative": module.distanceRank2(source, negative)}
            result["ranking"] = sorted(result["histograms"], key=result["histograms"].get, reverse=True)
            print(json.dumps(finite(result)), file=sys.__stdout__, flush=True)
            result["normalized_alignment_errors"] = {
                "positive": module.predictionTest5(source, target)[0] / (diagonal if backend == "freecad" else target.objectHandle.diagonal),
                "negative": module.predictionTest5(source, negative)[0] / (diagonal if backend == "freecad" else negative.objectHandle.diagonal)}
            result["status"] = "ok"
    except Exception:
        result["status"] = "error"
        result["error"] = traceback.format_exc()
    result["log_tail"] = log.getvalue()[-3000:]
    print(json.dumps(finite(result)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["freecad", "ocp"], required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=300,
                        help="Per-case subprocess timeout in seconds (default: 300)")
    parser.add_argument("--worker", nargs=2)
    args = parser.parse_args()
    if args.worker:
        worker(args.backend, args.worker[0], int(args.worker[1]))
        return
    if not args.output:
        parser.error("--output is required")

    def run(case):
        fixture, seed = case
        command = [sys.executable, __file__, "--backend", args.backend, "--worker", fixture, str(seed)]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
            output = proc.stdout
            status = "crash" if proc.returncode else "error"
            detail = proc.stderr[-2000:]
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else exc.stdout or ""
            status, detail = "timeout", f"Exceeded {args.timeout} seconds"
        records = []
        for line in output.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                pass
        record = records[-1] if records else {"fixture": fixture, "seed": seed}
        record.setdefault("status", status)
        if record["status"] in ("timeout", "crash"):
            record["error"] = detail
        return record

    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(run, [(f, s) for f in FIXTURES for s in (0, 1, 2)]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"backend": args.backend, "records": records}, indent=2) + "\n")
    print({s: sum(r["status"] == s for r in records) for s in {r["status"] for r in records}})


if __name__ == "__main__":
    main()
