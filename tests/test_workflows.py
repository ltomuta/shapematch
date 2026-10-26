import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import shapematch as sm
from shapematch_workflows import _select, compare, main
from conftest import ROOT


def test_cli_validation(tmp_path, capsys):
    with pytest.raises(SystemExit) as result:
        main(["singlePRtest", "-s"])
    assert result.value.code == 2
    assert "scenegraph visualization is deferred" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["singlePRtest"])
    assert "both -sf and -nf" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["multitest", "-I", str(tmp_path / "missing")])
    assert main(["multitest", "-I", str(tmp_path), "-O", str(tmp_path / "out")]) == 1
    assert "regenerate" in capsys.readouterr().err


def test_legacy_cli_input(tmp_path, capsys):
    old = tmp_path / "old.obj"
    old.write_bytes(b"old cache")
    assert main(["singlePRtest", "-sf", str(old), "-nf", str(old), "-O", str(tmp_path)]) == 1
    assert "regenerate from STEP" in capsys.readouterr().err


def test_model_selection_is_bounded(monkeypatch):
    import shapematch_workflows as workflows
    attempted = []

    def fail(path):
        attempted.append(path)
        raise ValueError("invalid model")

    monkeypatch.setattr(workflows, "_extract", fail)
    with pytest.raises(ValueError, match="20 attempts"):
        _select(range(100))
    assert len(attempted) == 20


def test_empty_and_zero_signatures_do_not_crash(cached_model):
    other = sm.STEPmodel()
    other.centroid = sm.Point(0, 0, 0)
    assert sm.listIntersection2(cached_model, other)[:2] == ([], [])
    other.featureMaxPoints = [sm.Point(0, 0, 0)]
    assert sm.listIntersection2(cached_model, other)[:2] == ([], [])


def test_sphere_signature_without_registration_points(cached_model):
    cached_model.featureMaxPoints = []
    cached_model.featureSphereDisps = [1.0]
    assert sm.distanceRank2(cached_model, cached_model) == 1.0
    assert sm.predictionTest5(cached_model, cached_model)[0] == pytest.approx(0, abs=1e-10)


@pytest.mark.integration
def test_cli_without_freecad_or_graphics(tmp_path):
    # A process-wide import guard proves that installed system FreeCAD cannot
    # accidentally satisfy a runtime import. No system site-packages are enabled.
    guard = tmp_path / "sitecustomize.py"
    guard.write_text('''import importlib.abc, sys
class NoCADApp(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"FreeCAD", "FreeCADGui", "Part", "pivy", "PySide2", "PySide6", "PyQt5", "PyQt6"}:
            raise ImportError("Forbidden headless dependency: " + fullname)
sys.meta_path.insert(0, NoCADApp())
''')
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), str(ROOT / "src")])}
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    output = tmp_path / "features"
    args = ["singlePRtest", "-sf", str(ROOT / "data/primitives/miscellanea/tetrahedron.scaled.stp"),
            "-nf", str(ROOT / "data/primitives/cube/unit_cube.stp"), "--html", "-O", str(output), "--seed", "0"]

    def run(arguments):
        return subprocess.run([sys.executable, "-m", "shapematch", *arguments], env=env,
                              capture_output=True, text=True, timeout=120)

    result = run(args)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((output / "comparisons.json").read_text())
    assert data["positive_comparison"]["histogram"] > data["negative_comparison"]["histogram"]
    assert data["positive_comparison"]["alignment_found"]
    assert len(list(output.glob("*.features.json"))) == 3
    assert (output / "comparison.html").is_file()
    assert "alignment_transform" in data["negative_comparison"]
    # Repeated seed must reproduce the values, regardless of cache filenames/timings.
    again = run([*args[:-3], str(tmp_path / "repeat"), "--seed", "0"])
    assert again.returncode == 0, again.stdout + again.stderr
    repeated = json.loads((tmp_path / "repeat/comparisons.json").read_text())
    assert data["positive_comparison"] == repeated["positive_comparison"]
    assert data["negative_comparison"] == repeated["negative_comparison"]
    result = run(["multitest", "-I", str(output), "-O", str(tmp_path / "ranked"), "--seed", "0"])
    assert result.returncode == 0, result.stdout + result.stderr
    comparisons = json.loads((tmp_path / "ranked/rankings.json").read_text())["comparisons"]
    assert len(comparisons) == 9
    assert (tmp_path / "ranked/comparisons.csv").is_file()


def test_comparisons_do_not_mutate_cached_features(cached_model):
    # Pure sphere signatures also exercise the zero-error path without SVD noise.
    cached_model.featureMaxPoints = []
    cached_model.featureSphereDisps = [1.0]
    before = cached_model.centroid
    result = compare(cached_model, cached_model, .9)
    assert result["alignment_found"] and result["alignment_error"] == pytest.approx(0, abs=1e-10)
    assert cached_model.centroid == before


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_nested_report_and_caches_survive_relocation(cached_model, tmp_path, monkeypatch, separator):
    import shapematch_html as html
    from shapematch_cache import save_model, write_json

    bundle = tmp_path / "CAD bundle"
    inputs = bundle / "input"
    inputs.mkdir(parents=True)
    caches = []
    for role in ("source", "negative"):
        source = inputs / f"{role}.stp"
        shutil.copyfile(cached_model.filepath, source)
        cached_model.filepath = str(source)
        caches.append(save_model(cached_model, inputs / f"{role}.json"))
    output = bundle / "output"
    assert main(["singlePRtest", "-sf", str(caches[0]), "-nf", str(caches[1]),
                 "-pf", str(caches[0]), "-O", str(output), "-of", "reports/comparisons.csv"]) == 0
    report = output / "reports" / "comparisons.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert all(value.startswith("../source_") or value.startswith("../negative_")
               for value in data["caches"].values())
    assert all("\\" not in value for value in data["caches"].values())
    data["caches"] = {key: value.replace("/", separator) for key, value in data["caches"].items()}
    write_json(report, data)
    moved = tmp_path / "moved"
    shutil.move(bundle, moved)
    loaded = []

    def capture(source, target, comparison, output, *args):
        loaded.extend([source, target])
        return output

    monkeypatch.setattr(html, "write_html", capture)
    html.visualize_result(moved / "output/reports/comparisons.json", moved / "view.html")
    assert [Path(model.filepath) for model in loaded] == [moved / "input/source.stp", moved / "input/negative.stp"]
