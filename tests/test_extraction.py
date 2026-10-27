"""Regression controls for origin dependence and nonconverging feature seeds."""
import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Trsf

import shapematch as sm
from shapematch_geometry import Geometry


@pytest.mark.parametrize("scale", [.1, 1, 10])
def test_centroid_detects_thin_solid_far_from_world_origin(scale):
    model = sm.STEPmodel()
    pose = gp_Trsf()
    pose.SetValues(scale, 0, 0, 10000, 0, scale, 0, -20000, 0, 0, scale, 30000)
    shape = BRepPrimAPI_MakeBox(1, 2, .01).Shape()
    model.objectHandle = Geometry(BRepBuilderAPI_Transform(shape, pose, True).Shape())
    sm.centroidTranslation(model)
    assert model.surfacePoints
    np.testing.assert_allclose(model.centroid, np.array([10000, -20000, 30000]) + scale*np.array([.5, 1, .005]),
                               atol=model.objectHandle.tolerance*10)


@pytest.mark.parametrize("kind", ["localMax", "localMin"])
def test_radial_refinement_rejects_stalled_endpoint(kind, monkeypatch):
    # Returning one endpoint reproduces the equality that previously left the
    # bracket unchanged forever. A valid circular feature must make progress.
    points = [sm.Point(1, 0, 0), sm.Point(2, .1, 0)]
    endpoint = points[0] if kind == "localMax" else points[1]
    calls = []
    def project(*args):
        calls.append(True)
        return endpoint
    monkeypatch.setattr(sm, "proj2object2", project)
    assert sm._refine_radial_bracket(None, sm.Point(0, 0, 0), points, kind, .001) == (None, None)
    assert len(calls) == 1


def test_missing_cluster_never_returns_a_label_as_circle_center(monkeypatch):
    monkeypatch.setattr(sm, "rotatedSurroundArray", lambda *args: [sm.Point(1, 0, 0)])
    monkeypatch.setattr(sm, "proj2object2", lambda *args: None)
    result = sm.getMaxMin(sm.STEPmodel(), sm.Point(1, 0, 0), sm.Point(0, 0, 0), .1, .001, 35)
    assert result == (None, "noCluster", None)
