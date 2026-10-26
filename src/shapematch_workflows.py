"""Reproducible headless CLI workflows around the research algorithms."""
import argparse
import copy
import csv
import json
import math
from pathlib import Path
import random
import sys
import uuid

import numpy as np

from shapematch_cache import CacheError, FORMAT, load_model, save_model, stored_path, write_json


def _extract(path):
    import shapematch as sm
    path = Path(path)
    if path.suffix.lower() in (".obj", ".json"):
        return load_model(path)
    if path.suffix.lower() not in (".stp", ".step"):
        raise ValueError(f"Expected a STEP file or JSON feature cache: {path}")
    model = sm.getModelFeatures2(
        str(path.resolve()), sm.Point(*(random.uniform(0, 10) for _ in range(3))),
        random.uniform(5, 15), random.uniform(0, 360), [random.random() for _ in range(3)])
    if model is None or model.centroid is None:
        raise ValueError(f"No surface features found: {path}")
    return model


def _select(candidates):
    candidates = list(candidates)
    random.shuffle(candidates)
    failures = []
    for path in candidates[:20]:
        try:
            return _extract(path)
        except (ValueError, RuntimeError, OSError) as exc:
            failures.append(f"{path}: {exc}")
    raise ValueError("No usable model found in at most 20 attempts. " + "; ".join(failures))


def _clone(model):
    result = copy.copy(model)
    result.__dict__ = {k: v if k == "objectHandle" else copy.deepcopy(v)
                       for k, v in model.__dict__.items()}
    return result


def compare(source, target, threshold, estimate_scale=True):
    """Keep pair comparisons independent of centroid corrections in predictionTest5."""
    import shapematch as sm
    source, target = _clone(source), _clone(target)
    rank = float(sm.distanceRank2(source, target, methodName="Correlation"))
    if not math.isfinite(rank):
        raise ValueError("Feature histogram comparison produced a nonfinite score")
    # Feature extraction can miss holes or yield too few points. A descriptor is
    # a hint, not grounds for skipping the independent surface registration.
    error, rotation, translation, scale = sm.predictionTest5(source, target, numberOfPoints=20, DistThreshold=1e-2, estimate_scale=estimate_scale)
    error = float(error)
    diagnostics = source.alignmentDiagnostics
    # The fit/error live in the placed target frame. Undo only the benchmark's
    # target scale for reporting physical mm; normalize in that same frame.
    return {"histogram": rank, "alignment_error": error / target.scale if math.isfinite(error) else None,
            "normalized_alignment_error": error / target.objectHandle.diagonal if math.isfinite(error) else None,
            "forward_error": float(diagnostics["forward_rmse"]) / target.scale,
            "reverse_error": float(diagnostics["reverse_rmse"]) / target.scale,
            "error_units": "mm", "histogram_passed": rank > threshold,
            "alignment_found": math.isfinite(error), "histogram_threshold": threshold,
            "alignment_transform": {"rotation": np.asarray(rotation).tolist(),
                                    "translation": translation, "scale": scale,
                                    "frame": "placed_source_to_placed_target"}}


def _save(model, directory, role):
    path = Path(directory) / f"{Path(model.filepath).stem}_{role}_{uuid.uuid4().hex[:12]}.features.json"
    return save_model(model, path)


def single_test(output, inputs=None, source=None, negative=None, positive=None, html_path=None, placement="aligned", boolean_tolerance=0.):
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    candidates = sorted(p for p in Path(inputs).rglob("*")
                        if p.suffix.lower() in (".stp", ".step")) if inputs else []
    source_model = _extract(source) if source else _select(candidates)
    remaining = [p for p in candidates if p.resolve() != Path(source_model.filepath).resolve()]
    negative_model = _extract(negative) if negative else _select(remaining)
    positive_model = _extract(positive or source_model.filepath)
    if Path(source_model.filepath).resolve() == Path(negative_model.filepath).resolve():
        raise ValueError("Negative model must have a different STEP source")
    caches = {role: str(_save(model, directory, role)) for role, model in
              [("source", source_model), ("positive", positive_model), ("negative", negative_model)]}
    result = {"source": source_model.name, "positive": positive_model.name,
            "negative": negative_model.name, "caches": caches,
            "positive_comparison": compare(source_model, positive_model, .5),
            "negative_comparison": compare(source_model, negative_model, .5)}
    if html_path is not None:
        from shapematch_html import write_html
        report = write_html(source_model, negative_model, result["negative_comparison"], html_path, placement, boolean_tolerance)
        result["html_report"] = str(report)
    return result


def _cache_files(directory, excluded):
    files = []
    for path in sorted(Path(directory).rglob("*.json")):
        if path.resolve() in excluded:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise CacheError(f"Invalid JSON in {path}: {exc}") from exc
        if isinstance(data, dict) and data.get("format") == FORMAT:
            files.append(path)
    if not files:
        raise CacheError("No JSON feature caches found; regenerate legacy .obj caches from STEP with singlePRtest")
    return files


def multi_test(directory, csv_path, json_path):
    paths = _cache_files(directory, {Path(csv_path).resolve(), Path(json_path).resolve()})
    # Relative paths disambiguate equal filenames in nested input directories.
    models = {p.relative_to(directory).as_posix(): load_model(p) for p in paths}
    rows = []
    matrix = {}
    for source_name, source in models.items():
        matrix[source_name] = {}
        for target_name, target in models.items():
            result = compare(source, target, .9)
            rows.append({"source": source_name, "target": target_name, **result})
            matrix[source_name][target_name] = result["alignment_error"]
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(csv_path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source", *models])
        writer.writeheader()
        for source, values in matrix.items():
            writer.writerow({"source": source, **values})
    write_json(json_path, {"format": "shapematch.results", "version": 1, "comparisons": rows})
    return rows


def _output(directory, filename):
    path = Path(filename)
    return path if path.is_absolute() else directory / path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shapematch", description="Headless STEP shape feature matching")
    commands = parser.add_subparsers(dest="command", required=True)
    for command, aliases in [("singlePRtest", []), ("multiPRtest", ["multitest"])]:
        sub = commands.add_parser(command, aliases=aliases)
        sub.add_argument("-I", "--input_dir_path", type=Path)
        sub.add_argument("-O", "--output_dir_path", type=Path, default=Path("shapematch-output"))
        sub.add_argument("-of", "--csv_file", default="comparisons.csv")
        sub.add_argument("--seed", type=int, default=0)
        sub.add_argument("-v", "--verbose", action="store_true")
        sub.add_argument("-s", "-s2", "--scenegraph", "--model_scene", "--debug_scenegraph",
                         dest="visualization", action="store_true", help=argparse.SUPPRESS)
        if command == "singlePRtest":
            sub.add_argument("-sf", "--source_file", type=Path)
            sub.add_argument("-nf", "--negative_file", type=Path)
            sub.add_argument("-pf", "--positive_file", type=Path)
            sub.add_argument("--html", type=Path, nargs="?", const=Path("comparison.html"),
                             help="Export an offline HTML overlay of source and compared part")
            sub.add_argument("--placement", choices=["aligned", "original"], default="aligned",
                             help="Initial HTML view: recovered alignment or original STEP coordinates")
            sub.add_argument("--boolean-tolerance", type=float, default=0., metavar="MM",
                             help="Initial HTML Boolean positional tolerance in mm (default: 0, numerical baseline)")
        else:
            sub.add_argument("-obf", "--obj_file", "--json_file", dest="json_file", default="rankings.json")
    visual = commands.add_parser("visualize", help="Export an HTML overlay from saved singlePRtest results")
    visual.add_argument("result", type=Path, help="Path to the saved comparisons.json")
    visual.add_argument("-o", "--output", type=Path, default=Path("shapematch-output/comparison.html"))
    visual.add_argument("--pair", choices=["negative", "positive"], default="negative",
                        help="Show the compared part (default) or the self-comparison control")
    visual.add_argument("--placement", choices=["aligned", "original"], default="aligned")
    visual.add_argument("--boolean-tolerance", type=float, default=0., metavar="MM",
                        help="Initial Boolean positional tolerance in mm (default: 0, numerical baseline)")
    app = commands.add_parser("app", help="Open the local STEP library in a browser")
    app.add_argument("--port", type=int, default=0)
    app.add_argument("--workers", type=int, default=2)
    app.add_argument("--timeout", type=float, default=300.)
    app.add_argument("--no-browser", action="store_true")
    app.add_argument("--data-dir", type=Path, help="Override the local application-data directory")
    args = parser.parse_args(argv)
    if args.command == "app":
        from shapematch_app.server import launch
        return launch(args.port, args.workers, args.timeout, not args.no_browser, args.data_dir)
    if hasattr(args, "boolean_tolerance"):
        from shapematch_boolean import validate_tolerance
        try:
            validate_tolerance(args.boolean_tolerance)
        except ValueError as exc:
            parser.error(str(exc))
    if args.command == "visualize":
        try:
            from shapematch_html import visualize_result
            report = visualize_result(args.result, args.output, args.pair, args.placement, args.boolean_tolerance)
            print(f"HTML comparison written to {report}")
            return 0
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"shapematch: {exc}", file=sys.stderr)
            return 1
    if args.visualization:
        parser.error("Legacy scenegraph visualization is deferred; use singlePRtest --html or visualize for an HTML overlay")
    if args.input_dir_path is not None and not args.input_dir_path.is_dir():
        parser.error(f"Input directory does not exist: {args.input_dir_path}")
    if args.command == "singlePRtest" and args.input_dir_path is None and not (args.source_file and args.negative_file):
        parser.error("Provide -I with STEP models, or both -sf and -nf")
    if args.command != "singlePRtest" and args.input_dir_path is None:
        parser.error("Provide -I with JSON feature caches")
    import shapematch as sm
    sm.isVerbose = args.verbose
    random.seed(args.seed)
    np.random.seed(args.seed % (2**32))
    try:
        args.output_dir_path.mkdir(parents=True, exist_ok=True)
        csv_path = _output(args.output_dir_path, args.csv_file)
        if csv_path.suffix.lower() != ".csv":
            parser.error("CSV output must use a .csv extension")
        if args.command == "singlePRtest":
            html_path = _output(args.output_dir_path, args.html) if args.html is not None else None
            if html_path is not None and html_path.suffix.lower() != ".html":
                parser.error("HTML output must use a .html extension")
            result = single_test(args.output_dir_path, args.input_dir_path, args.source_file,
                                 args.negative_file, args.positive_file, html_path, args.placement, args.boolean_tolerance)
            result["seed"] = args.seed
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                fields = ["source", "target", "histogram", "alignment_error", "normalized_alignment_error",
                          "forward_error", "reverse_error", "error_units", "histogram_passed",
                          "alignment_found", "histogram_threshold"]
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for role in ("positive", "negative"):
                    row = {"source": result["source"], "target": result[role], **result[role + "_comparison"]}
                    writer.writerow({key: row[key] for key in fields})
            # Resolve caches from the report location, including custom nested
            # CSV paths, so a report/cache/STEP bundle can move between OSes.
            result["caches"] = {role: stored_path(path, csv_path.parent)
                                for role, path in result["caches"].items()}
            write_json(csv_path.with_suffix(".json"), {"format": "shapematch.single-result", "version": 1, **result})
            if html_path is not None:
                print(f"HTML comparison written to {html_path}")
        else:
            json_path = _output(args.output_dir_path, args.json_file)
            if json_path.suffix.lower() != ".json":
                parser.error("Ranked results now use .json; replace legacy -obf *.obj output names")
            multi_test(args.input_dir_path, csv_path, json_path)
        print(f"Results written to {csv_path}")
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"shapematch: {exc}", file=sys.stderr)
        return 1
