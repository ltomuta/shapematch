import json
import math
from pathlib import Path

import numpy as np
import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCP.gp import gp_Pln, gp_Pnt, gp_Dir
from OCP.TopoDS import TopoDS_Shape

from shapematch import Point, STEPmodel
from shapematch_geometry import Geometry, GeometryError
from conftest import ROOT


def test_line_ray_and_shared_vertices(cube_path):
    model = STEPmodel()
    model.importSTEPmodel(cube_path)
    assert model.rayModelIntersection2(Point(0, 0, 0), Point(1, 0, 0)) == [Point(.5, 0, 0)]
    assert model.rayModelIntersection(Point(0, 0, 0), Point(1, 0, 0)) == [Point(-.5, 0, 0), Point(.5, 0, 0)]
    assert model.rayModelIntersection2(Point(.5, 0, 0), Point(1, 0, 0)) == []
    assert model.rayModelIntersection2(Point(2, 2, 0), Point(3, 2, 0)) == []
    hits = model.rayModelIntersection2(Point(-2, -2, -2), Point(0, 0, 0))
    np.testing.assert_allclose(hits, [(-.5, -.5, -.5), (.5, .5, .5)], atol=1e-7)


@pytest.mark.parametrize("scale", [.25, 1, 2, 1000])
def test_transform_and_unbounded_ray(cube_path, scale):
    model = STEPmodel()
    model.importSTEPmodel(cube_path, Point(1000, -4, 2), scale, 90, [0, 0, 1])
    hits = model.rayModelIntersection2(Point(0, -4, 2), Point(1, -4, 2))
    np.testing.assert_allclose(hits, [(1000 - .5 * scale, -4, 2), (1000 + .5 * scale, -4, 2)], atol=model.objectHandle.tolerance)
    # Reload must preserve the cached transform, including downscaling.
    model.importSTEPmodel()
    assert model.scale == scale


def test_sphere_tangent_and_miss():
    geometry = Geometry(BRepPrimAPI_MakeSphere(1).Shape())
    np.testing.assert_allclose(geometry.intersections((-2, 1, 0), (-1, 1, 0)), [(0, 1, 0)], atol=1e-7)
    assert geometry.intersections((-2, 2, 0), (-1, 2, 0)) == []


@pytest.mark.parametrize("scale", [0, -1, math.nan, math.inf])
def test_invalid_scale(cube_path, scale):
    with pytest.raises(GeometryError, match="Scale"):
        STEPmodel().importSTEPmodel(cube_path, scale=scale)


def test_invalid_geometry_and_inputs(cube_path, tmp_path):
    with pytest.raises(GeometryError, match="empty|invalid"):
        Geometry(TopoDS_Shape())
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), -1, 1, -1, 1).Face()
    with pytest.raises(GeometryError, match="closed shell"):
        Geometry(face)
    with pytest.raises(FileNotFoundError):
        STEPmodel().importSTEPmodel(tmp_path / "missing.step")
    invalid = tmp_path / "invalid.step"
    invalid.write_text("not a STEP file")
    with pytest.raises(GeometryError, match="Cannot read"):
        STEPmodel().importSTEPmodel(invalid)
    model = STEPmodel()
    model.importSTEPmodel(cube_path)
    with pytest.raises(GeometryError, match="nonzero"):
        model.rayModelIntersection2(Point(0, 0, 0), Point(0, 0, 0))


def test_mixed_solid_and_open_shell_excludes_only_surfaces():
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopoDS import TopoDS_Compound, TopoDS_Shell
    builder = BRep_Builder()
    mixed, surface = TopoDS_Compound(), TopoDS_Shell()
    builder.MakeCompound(mixed)
    builder.MakeShell(surface)
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(100, 0, 0), gp_Dir(1, 0, 0)), -2, 2, -2, 2).Face()
    builder.Add(surface, face)
    builder.Add(mixed, BRepPrimAPI_MakeBox(1, 2, 3).Solid())
    builder.Add(mixed, surface)
    geometry = Geometry(mixed)
    assert geometry.principal_frame()[0] == pytest.approx(6)
    assert geometry.diagonal == pytest.approx(math.sqrt(14))
    np.testing.assert_allclose(geometry.intersections((-1, 1, 1), (0, 1, 1)), [(0, 1, 1), (1, 1, 1)])
    assert "Ignored 1 open surface" in geometry.import_warnings[0]
    with pytest.raises(GeometryError, match="closed shell"):
        Geometry(surface)


def test_standalone_closed_shell_becomes_oriented_solid():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    shell = TopExp_Explorer(BRepPrimAPI_MakeBox(1, 2, 3).Solid(), TopAbs_SHELL).Current()
    geometry = Geometry(shell.Reversed())
    assert geometry.principal_frame()[0] == pytest.approx(6)
    np.testing.assert_allclose(geometry.principal_frame()[1], [.5, 1, 1.5])


def test_analytic_intersection_fallback_preserves_hole_trimming(monkeypatch):
    import shapematch_geometry as adapter
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2
    from OCP.Standard import Standard_NullObject
    box = BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, -1), 2, 2, 2).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -2), gp_Dir(0, 0, 1)), .25, 4).Shape()
    geometry = Geometry(BRepAlgoAPI_Cut(box, hole).Shape())

    class FailingIntersector:
        def Load(self, *args):
            pass

        def Perform(self, *args):
            raise Standard_NullObject("forced analytic fallback")

    geometry._intersector = FailingIntersector()
    monkeypatch.setattr(adapter, "IntCurvesFace_ShapeIntersector", FailingIntersector)
    # Untrimmed plane intersections would incorrectly close the axial hole.
    assert geometry.intersections((0, 0, -3), (0, 0, -2)) == []
    np.testing.assert_allclose(geometry.intersections((0, 0, 0), (1, 0, 0), False),
                               [(-1, 0, 0), (-.25, 0, 0), (.25, 0, 0), (1, 0, 0)], atol=1e-7)
    np.testing.assert_allclose(geometry.intersections((0, 0, 0), (1, 0, 0)), [(.25, 0, 0), (1, 0, 0)], atol=1e-7)


def test_surface_point_deduplication_survives_cache_list_replacement(cube_path):
    model = STEPmodel()
    model.importSTEPmodel(cube_path)
    model.rayModelIntersection2(Point(0, 0, 0), Point(1, 0, 0))
    model.surfacePoints = [Point(.5, 0, 0), Point(-.5, 0, 0)]
    model.rayModelIntersection2(Point(0, 0, 0), Point(1, 0, 0))
    assert model.surfacePoints == [Point(.5, 0, 0), Point(-.5, 0, 0)]
    with pytest.raises(GeometryError, match="finite"):
        model.rayModelIntersection2(Point(0, 0, 0), Point(math.inf, 0, 0))


REFERENCE = json.loads((Path(__file__).parent / "reference/freecad-initial.json").read_text())["records"]


@pytest.mark.parametrize("record", REFERENCE, ids=lambda r: f'{r["fixture"]}-{r["seed"]}')
def test_freecad_reference_intersections(record):
    assert "intersections" in record, "Reference geometry must have been captured even if feature extraction failed"
    model = STEPmodel()
    transform = record["transform"]
    model.importSTEPmodel(ROOT / "data/primitives" / record["fixture"], **transform)
    origin = Point(*transform["insertionPoint"])
    tolerance = max(1e-7, 1e-7 * record["diagonal"])
    for direction, reference in zip([(1, .31, .27), (-.4, 1, .13), (.2, .3, 1)], record["intersections"]):
        through = Point(*(a + b for a, b in zip(origin, direction)))
        actual = sorted(model.rayModelIntersection2(origin, through))
        np.testing.assert_allclose(actual, reference, atol=tolerance, rtol=0)
