import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

import shapematch as sm
from shapematch_cache import save_model
from shapematch_html import comparison_payload, visualize_result, write_html
from shapematch_workflows import main
from shapematch_geometry import Geometry


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.scripts = []
        self.payload = ""
        self.in_payload = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs = dict(attrs)
            self.scripts.append(attrs)
            self.in_payload = attrs.get("id") == "comparison-data"

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_payload = False

    def handle_data(self, text):
        if self.in_payload:
            self.payload += text



def test_mesh_has_valid_winding_locations_and_scale(overlay):
    source, _, _ = overlay
    vertices, faces = source.objectHandle.triangle_mesh()
    assert vertices.shape == (24, 3)
    assert faces.shape == (12, 3)
    assert faces.min() >= 0 and faces.max() < len(vertices)
    volume = np.sum(np.einsum("ij,ij->i", vertices[faces[:, 0]],
                             np.cross(vertices[faces[:, 1]], vertices[faces[:, 2]]))) / 6
    assert volume == pytest.approx(source.scale ** 3)


def test_display_mesh_does_not_fill_drilled_holes():
    box = BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, -1), 2, 2, 2).Shape()
    drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -2), gp_Dir(0, 0, 1)), .25, 4).Shape()
    vertices, faces = Geometry(BRepAlgoAPI_Cut(box, drill).Shape()).triangle_mesh()
    triangles = vertices[faces]
    top = triangles[np.all(np.isclose(triangles[:, :, 2], 1), axis=1)]
    assert len(top) > 2
    centers = top.mean(axis=1)
    assert np.min(np.linalg.norm(centers[:, :2], axis=1)) > .24


def test_benchmark_transforms_are_removed_in_original_and_fitted_views(overlay):
    source, target, comparison = overlay
    data = comparison_payload(source, target, comparison)
    native = np.asarray(data["source"]["original"])
    np.testing.assert_allclose(np.abs(native), .5, atol=1e-12)
    np.testing.assert_allclose(data["source"]["aligned"], native, atol=1e-12)
    np.testing.assert_allclose(np.abs(data["target"]["vertices"]), .5, atol=1e-12)
    assert data["fit_scale"] == pytest.approx(1)
    assert data["units"] == "mm"


def test_recovered_mesh_uses_fit_rather_than_just_native_placement(overlay):
    source, target, comparison = overlay
    native_rotation = sm.angleAxis2RotMat([0, 0, 1], .35)
    native_translation = np.array([2, -3, 4])
    fit_rotation = target.rotationMatrix @ native_rotation @ source.rotationMatrix.T
    fit_scale = target.scale / source.scale
    fit_translation = (np.array(target.insertionPoint) + target.scale * target.rotationMatrix @ native_translation
                       - fit_scale * fit_rotation @ source.insertionPoint)
    comparison["alignment_transform"].update(rotation=fit_rotation.tolist(), translation=fit_translation.tolist())
    data = comparison_payload(source, target, comparison, "original")
    expected = np.array(data["source"]["original"]) @ native_rotation.T + native_translation
    np.testing.assert_allclose(data["source"]["aligned"], expected, atol=1e-12)
    assert data["placement"] == "original"


def test_html_is_offline_and_filenames_remain_inert(overlay, tmp_path):
    source, target, comparison = overlay
    source.name = '</script><script>window.CAD_FILENAME_EXECUTED=true</script>"&.stp'
    output = write_html(source, target, comparison, tmp_path / "nested/view.html")
    document = Document(output.read_text(encoding="utf-8"))
    assert all("src" not in script for script in document.scripts)
    assert len(document.scripts) == 3
    assert json.loads(document.payload)["source"]["name"] == source.name
    assert source.name not in output.read_text(encoding="utf-8")
    assert "Plotly.newPlot" in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("change", ["scale", "reflection", "translation", "frame"])
def test_invalid_stored_fit_is_not_silently_displayed(overlay, change):
    source, target, comparison = overlay
    pose = comparison["alignment_transform"]
    if change == "scale":
        pose["scale"] = -1
    elif change == "reflection":
        pose["rotation"] = np.diag([-1., 1., 1.]).tolist()
    elif change == "translation":
        pose["translation"] = [float("nan"), 0, 0]
    else:
        pose["frame"] = "unknown"
    with pytest.raises(ValueError, match="valid proper"):
        comparison_payload(source, target, comparison)


def test_optional_dependency_error_is_actionable(overlay, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "plotly.offline", None)
    with pytest.raises(ValueError, match=r"pip install.*html"):
        write_html(*overlay, tmp_path / "view.html")


@pytest.mark.parametrize("old_report", [False, True])
def test_saved_results_reuse_fit_or_refit_old_reports(overlay, tmp_path, monkeypatch, old_report):
    import shapematch_workflows as workflows
    source, target, comparison = overlay
    saved = copy.deepcopy(comparison)
    if old_report:
        saved.pop("alignment_transform")
    calls = []

    def refit(*args):
        calls.append(True)
        return comparison

    monkeypatch.setattr(workflows, "compare", refit)
    for model, name in [(source, "source.json"), (target, "target.json")]:
        save_model(model, tmp_path / name)
    report = tmp_path / "comparisons.json"
    report.write_text(json.dumps({"format": "shapematch.single-result", "version": 1,
                                  "caches": {"source": "source.json", "negative": "target.json"},
                                  "negative_comparison": saved}))
    output = visualize_result(report, tmp_path / "result.html")
    assert len(calls) == int(old_report)
    assert json.loads(Document(output.read_text(encoding="utf-8")).payload)["metrics"]["alignment_error"] == 0
    # Exercise the real parser/command as well as the Python API.
    assert main(["visualize", str(report), "-o", str(tmp_path / "cli.html"), "--placement", "original"]) == 0
    assert json.loads(Document((tmp_path / "cli.html").read_text(encoding="utf-8")).payload)["placement"] == "original"


def test_visualize_rejects_wrong_report_and_output(overlay, tmp_path, capsys):
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"format":"shapematch.results"}')
    assert main(["visualize", str(wrong), "-o", str(tmp_path / "view.html")]) == 1
    assert "singlePRtest" in capsys.readouterr().err
    with pytest.raises(ValueError, match=".html"):
        write_html(*overlay, tmp_path / "view.json")


def test_custom_tolerance_is_exported_and_invalid_cli_values_fail(overlay, tmp_path, capsys):
    data = comparison_payload(*overlay, boolean_tolerance=.02)
    assert data["boolean_tolerance"] == .02
    assert sorted([data["boolean_tolerance"], *[level["tolerance"] for level in data["boolean_variants"]]]) == [0, .001, .01, .02, .1]
    assert data["metrics"]["alignment_error"] == overlay[2]["alignment_error"]
    for value in ("-1", "nan", "inf"):
        with pytest.raises(SystemExit) as result:
            main(["visualize", str(tmp_path / "unused.json"), "--boolean-tolerance", value])
        assert result.value.code == 2
        assert "finite nonnegative" in capsys.readouterr().err


def test_app_preview_worker_supports_boolean_regions(overlay, monkeypatch):
    from shapematch_app.workers import cad_operation
    import shapematch_cache
    source, target, comparison = overlay
    monkeypatch.setattr(shapematch_cache, "load_model",
                        lambda cache, source_path: source if cache == "query" else target)
    request = {"query_cache": "query", "query": "query.step", "cache": "target",
               "path": "target.step", "comparison": comparison}
    assert cad_operation("preview", request)["boolean"] is None
    result = cad_operation("preview", {**request, "include_booleans": True})
    for placement in ("aligned", "original"):
        assert result["boolean"][placement]["error"] is None
        assert set(result["boolean"][placement]["regions"]) == {"common", "only_a", "only_b"}
    assert len(result["boolean_variants"]) == 3
