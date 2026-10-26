import math

import numpy as np
import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.TopAbs import TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from shapematch_boolean import solid_partitions, validate_tolerance
from shapematch_geometry import Geometry, GeometryError
from shapematch_html import comparison_payload


def box(origin=(0, 0, 0), size=1):
    return BRepPrimAPI_MakeBox(gp_Pnt(*origin), size, size, size).Shape()


@pytest.mark.parametrize("offset, expected", [
    ((0, 0, 0), (1, 0, 0)),       # Identical bodies: empty directional differences.
    ((.5, 0, 0), (.5, .5, .5)),   # Partial overlap.
    ((2, 0, 0), (0, 1, 1)),       # Disjoint bodies.
    ((1, 0, 0), (0, 1, 1)),       # Face contact has no common solid volume.
    ((1, 1, 0), (0, 1, 1)),       # Edge contact.
    ((1, 1, 1), (0, 1, 1)),       # Point contact.
])
def test_partition_volumes_and_empty_regions(offset, expected):
    regions = solid_partitions(box(), box(offset))
    for key, volume in zip(("common", "only_a", "only_b"), expected):
        result = regions[key]
        assert result["volume"] == pytest.approx(volume, abs=1e-9)
        assert bool(result["triangles"]) == bool(volume)
        assert bool(result["components"]) == bool(volume)
        if not volume:
            assert result["vertices"] == []
    assert regions["common"]["volume"] + regions["only_a"]["volume"] == pytest.approx(1)
    assert regions["common"]["volume"] + regions["only_b"]["volume"] == pytest.approx(1)


def test_containment_and_cavity_winding():
    regions = solid_partitions(box(size=2), box((.5, .5, .5)))
    assert regions["common"]["volume"] == pytest.approx(1)
    assert regions["only_a"]["volume"] == pytest.approx(7)
    assert regions["only_b"]["volume"] == 0
    # Both the outer boundary and the cavity boundary must have correct winding.
    vertices = np.array(regions["only_a"]["vertices"])
    faces = np.array(regions["only_a"]["triangles"])
    mesh_volume = np.einsum("ij,ij->i", vertices[faces[:, 0]],
                            np.cross(vertices[faces[:, 1]], vertices[faces[:, 2]])).sum() / 6
    assert mesh_volume == pytest.approx(7)


def test_hole_in_b_is_material_only_in_a():
    plain = box(size=2)
    drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(1, 1, -1), gp_Dir(0, 0, 1)), .25, 4).Shape()
    drilled = BRepAlgoAPI_Cut(plain, drill).Shape()
    regions = solid_partitions(plain, drilled)
    removed = math.pi * .25 ** 2 * 2
    assert regions["only_a"]["volume"] == pytest.approx(removed)
    assert regions["common"]["volume"] == pytest.approx(8 - removed)
    assert regions["only_b"]["volume"] == 0
    reverse = solid_partitions(drilled, plain)
    assert reverse["only_b"]["volume"] == pytest.approx(removed)
    assert reverse["only_a"]["volume"] == 0
    # Non-destructive Boolean evaluation must not change either input's volume.
    assert Geometry(plain).principal_frame()[0] == pytest.approx(8)
    assert Geometry(drilled).principal_frame()[0] == pytest.approx(8 - removed)


def test_disconnected_solid_components_are_preserved():
    builder = BRep_Builder()
    two = TopoDS_Compound()
    builder.MakeCompound(two)
    builder.Add(two, box())
    builder.Add(two, box((3, 0, 0)))
    regions = solid_partitions(two, BRepPrimAPI_MakeBox(4, 1, 1).Shape())
    assert regions["common"]["components"] == 2
    assert regions["common"]["volume"] == pytest.approx(2)
    assert regions["only_b"]["volume"] == pytest.approx(2)
    assert regions["only_a"]["components"] == 0


def test_benchmark_poses_do_not_leak_into_boolean_volumes(overlay):
    data = comparison_payload(*overlay)
    for placement in ("original", "aligned"):
        assert data["boolean"][placement]["error"] is None
        regions = data["boolean"][placement]["regions"]
        assert regions["common"]["volume"] == pytest.approx(1)
        assert regions["only_a"]["volume"] == 0
        assert regions["only_b"]["volume"] == 0


def test_regions_are_computed_for_each_placement(overlay):
    source, target, comparison = overlay
    pose = comparison["alignment_transform"]
    pose["translation"] = (np.array(pose["translation"])
                           + target.scale * target.rotationMatrix @ np.array([2, 0, 0])).tolist()
    data = comparison_payload(source, target, comparison)
    original = data["boolean"]["original"]["regions"]
    aligned = data["boolean"]["aligned"]["regions"]
    assert original["common"]["volume"] == pytest.approx(1)
    assert aligned["common"]["volume"] == 0
    assert aligned["only_a"]["volume"] == pytest.approx(1)
    assert aligned["only_b"]["volume"] == pytest.approx(1)


def test_recovered_uniform_scale_is_cubed_in_native_volumes(overlay):
    source, target, comparison = overlay
    pose = comparison["alignment_transform"]
    pose["scale"] *= 2
    pose["translation"] = (np.array(target.insertionPoint)
                           - pose["scale"] * np.array(pose["rotation"]) @ source.insertionPoint).tolist()
    data = comparison_payload(source, target, comparison)
    original = data["boolean"]["original"]["regions"]
    aligned = data["boolean"]["aligned"]["regions"]
    assert original["common"]["volume"] == pytest.approx(1)
    assert aligned["common"]["volume"] == pytest.approx(1)
    assert aligned["only_a"]["volume"] == pytest.approx(7)
    assert aligned["only_b"]["volume"] == 0


def test_boolean_failure_is_distinct_from_empty_difference(overlay, monkeypatch):
    import shapematch_boolean as booleans
    original = booleans.solid_partitions
    calls = []

    def fail_aligned(a, b, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise GeometryError("Native Boolean failed")
        return original(a, b, **kwargs)

    monkeypatch.setattr(booleans, "solid_partitions", fail_aligned)
    data = comparison_payload(*overlay)
    assert data["boolean"]["original"]["error"] is None
    assert data["boolean"]["aligned"]["error"] == "Native Boolean failed"
    assert data["boolean"]["aligned"]["regions"] == {}
    assert data["source"]["triangles"] and data["target"]["triangles"]


def test_closed_shell_overlay_remains_available_without_solid_booleans(overlay):
    source, target, comparison = overlay
    shell = TopExp_Explorer(source.objectHandle.shape, TopAbs_SHELL).Current()
    source.objectHandle = Geometry(shell)
    data = comparison_payload(source, target, comparison)
    assert data["source"]["triangles"]
    for placement in ("original", "aligned"):
        assert "solid CAD bodies" in data["boolean"][placement]["error"]


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), float("-inf"), "not-a-distance"])
def test_boolean_tolerance_requires_a_finite_nonnegative_distance(value):
    with pytest.raises(ValueError, match="finite nonnegative"):
        validate_tolerance(value)


def test_tolerance_removes_positional_skins_without_removing_a_real_hole():
    a = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
    shifted = BRepPrimAPI_MakeBox(gp_Pnt(.0005, 0, 0), 10, 10, 10).Shape()
    drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(5, 5, -1), gp_Dir(0, 0, 1)), .25, 12).Shape()
    b = BRepAlgoAPI_Cut(shifted, drill).Shape()
    strict = solid_partitions(a, b)
    tolerant = solid_partitions(a, b, tolerance=.001)
    hole_volume = math.pi * .25 ** 2 * 10
    assert strict["only_b"]["volume"] == pytest.approx(.05)
    assert tolerant["only_b"]["volume"] == 0
    assert tolerant["only_a"]["volume"] == pytest.approx(hole_volume)
    assert strict["only_a"]["volume"] > tolerant["only_a"]["volume"]
    # Increasing a tolerance must not mutate the strict input shapes or results.
    again = solid_partitions(a, b)
    assert again["only_b"]["volume"] == pytest.approx(strict["only_b"]["volume"])


def test_differences_larger_than_the_tolerance_remain():
    results = solid_partitions(box(), box((.02, 0, 0)), tolerance=.001)
    assert results["only_a"]["volume"] == pytest.approx(.02)
    assert results["only_b"]["volume"] == pytest.approx(.02)


def test_tolerance_is_in_native_mm_after_benchmark_pose_removal(overlay):
    source, target, comparison = overlay
    pose = comparison["alignment_transform"]
    pose["translation"] = (np.asarray(pose["translation"])
                           + target.scale * target.rotationMatrix @ np.array([.0005, 0, 0])).tolist()
    data = comparison_payload(source, target, comparison, boolean_tolerance=.001)
    assert data["boolean_tolerance"] == .001
    assert data["boolean"]["aligned"]["regions"]["only_a"]["volume"] == 0
    baseline = next(level for level in data["boolean_variants"] if level["tolerance"] == 0)
    assert baseline["placements"]["aligned"]["regions"]["only_a"]["volume"] == pytest.approx(.0005)
