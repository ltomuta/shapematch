"""Compare captured geometry and matching decisions without requiring FreeCAD."""
import argparse
import json
import math
from pathlib import Path


def compare(freecad, standalone):
    previous = {(r["fixture"], r["seed"]): r for r in freecad["records"]}
    records = []
    for actual in standalone["records"]:
        before = previous[(actual["fixture"], actual["seed"])]
        tolerance = max(1e-7, 1e-7 * before["diagonal"])
        geometry_equal = all(
            len(old) == len(new) and all(math.dist(a, b) <= tolerance for a, b in zip(old, new))
            for old, new in zip(before["intersections"], actual["intersections"]))
        result = {"fixture": actual["fixture"], "seed": actual["seed"],
                  "geometry_equal": geometry_equal, "freecad_status": before["status"],
                  "ocp_status": actual["status"]}
        if "histograms" in before and "histograms" in actual:
            result["ranking_equal"] = before["ranking"] == actual["ranking"]
            result["gate_changes"] = [
                {"pair": pair, "threshold": threshold}
                for pair in ("positive", "negative") for threshold in (.5, .9)
                if (before["histograms"][pair] > threshold) != (actual["histograms"][pair] > threshold)]
        else:
            result["matching_unavailable"] = before.get("error", "No reference histogram captured").splitlines()[-1]
        records.append(result)
    return {"records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("freecad", type=Path)
    parser.add_argument("ocp", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.freecad.read_text()), json.loads(args.ocp.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f'{sum(r["geometry_equal"] for r in result["records"])}/{len(result["records"])} geometry cases agree')
