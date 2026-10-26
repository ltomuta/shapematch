"""Real spawn supervision and native CAD regression tests."""
import os
from pathlib import Path
import threading
import time

import pytest

from shapematch_app import workers
from shapematch_app.workers import Supervisor, WorkerError
from shapematch_cache import source_hash
ROOT = Path(__file__).resolve().parents[1]


def controlled_entry(connection, operation, data):
    if operation == "crash":
        os._exit(7)
    if operation == "wait":
        time.sleep(10)
    connection.send((True, "x" * data.get("bytes", 1)))
    connection.close()


def test_worker_crash_timeout_cancel_and_recovery(monkeypatch):
    monkeypatch.setattr(workers, "_entry", controlled_entry)
    supervisor = Supervisor(2, timeout=2)
    with pytest.raises(WorkerError, match="crashed"):
        supervisor.run("crash", {})
    with pytest.raises(WorkerError, match="timed out"):
        supervisor.run("wait", {})
    event = threading.Event()
    timer = threading.Timer(.2, event.set)
    timer.start()
    with pytest.raises(WorkerError, match="Cancelled"):
        supervisor.run("wait", {}, event)
    timer.join()
    # A result larger than the pipe buffer must be drained before joining.
    assert len(supervisor.run("ok", {"bytes": 500000})) == 500000


@pytest.mark.parametrize("size", [1., 2.])
def test_rigid_registration_constrains_hypotheses_and_refinement(size):
    import numpy as np
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf
    from shapematch_alignment import register
    from shapematch_geometry import Geometry
    source = Geometry(BRepPrimAPI_MakeBox(1., 2., 3.).Shape())
    pose = gp_Trsf()
    pose.SetValues(size, 0, 0, 7, 0, size, 0, -4, 0, 0, size, 2)
    target = Geometry(BRepBuilderAPI_Transform(source.shape, pose, True).Shape())
    candidate = (np.eye(3), np.array([7, -4, 2]), size)
    similarity = register(source, target, [candidate])
    rigid = register(source, target, [candidate], estimate_scale=False)
    assert similarity[1][2] == pytest.approx(size)
    assert similarity[0] < 1e-6
    assert rigid[1][2] == 1
    assert rigid[0] < 1e-6 if size == 1 else rigid[0] > .1


@pytest.mark.integration
def test_spawned_native_extraction_comparison_preview_and_duplicate_rebinding(tmp_path):
    import shutil
    from shapematch_cache import load_model
    original = ROOT / 'data/primitives/cube/unit_cube.stp'
    query = tmp_path / "native ü.STEP"
    duplicate = tmp_path / "copy.step"
    shutil.copyfile(original, query)
    shutil.copyfile(original, duplicate)
    cache = tmp_path / "features.json"
    supervisor = Supervisor(timeout=300)
    extracted = supervisor.run("extract", {"path": str(query), "hash": source_hash(query), "cache": str(cache)})
    assert set(extracted["timings"]) == {"step_loading", "feature_extraction"}
    assert extracted["volume"] == pytest.approx(1)
    # No random benchmark placement enters the app's feature cache.
    model = load_model(cache)
    assert model.scale == 1 and list(model.insertionPoint) == [0, 0, 0]
    query.unlink()
    assert load_model(cache, source_path=duplicate).filepath == str(duplicate)
    data = {"query": str(duplicate), "query_cache": str(cache), "path": str(duplicate), "cache": str(cache)}
    result = supervisor.run("compare", {**data, "estimate_scale": False})
    assert result["alignment_error"] < 1e-6
    assert result["alignment_transform"]["scale"] == 1
    preview = supervisor.run("preview", {**data, "comparison": result})
    assert preview["boolean"] is None
    assert len(preview["source"]["triangles"]) > 0
