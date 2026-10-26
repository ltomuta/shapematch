import math

import numpy as np
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf
from scipy.spatial.distance import correlation, euclidean

import shapematch as sm
from shapematch_alignment import fit_points, register, surface_errors, surface_rmse, transform
from shapematch_geometry import Geometry
from shapematch_workflows import compare
from conftest import ROOT


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("scale", [.2, 1, 4])
def test_similarity_recovers_known_transform(seed, scale):
    points = np.random.default_rng(seed).normal(size=(20, 3))
    rotation = sm.angleAxis2RotMat([1, 2, 3], .7 + seed)
    translation = np.array([100, -5, 42])
    target = transform(points, rotation, translation, scale)
    recovered_r, recovered_t, recovered_s = fit_points(points, target)
    np.testing.assert_allclose(recovered_r, rotation, atol=1e-12)
    np.testing.assert_allclose(recovered_t, translation, atol=1e-12)
    assert recovered_s == pytest.approx(scale)
    np.testing.assert_allclose(transform(points, recovered_r, recovered_t, recovered_s), target, atol=1e-12)


def test_rigid_fit_ignores_biased_model_centroids(capsys):
    points = np.array([[0., 0., 0.], [2, 0, 0], [0, 3, 0], [0, 0, 4]])
    rotation = sm.angleAxis2RotMat([0, 0, 1], .6)
    target = transform(points, rotation, np.array([3., -5., 2.]))
    r, t = sm.rigidTransform3D(points.T, sm.Point(100, -10, 9), target.T, sm.Point(0, 0, 0))
    np.testing.assert_allclose(sm.transformPoints([sm.Point(*p) for p in points], r, t), target, atol=1e-12)
    assert "reflection" not in capsys.readouterr().out


def test_reflected_correspondences_cannot_return_mirror_transform():
    points = np.random.default_rng(7).normal(size=(30, 3))
    target = points * [-1, 1, 1]
    rotation, translation, scale = fit_points(points, target)
    assert np.linalg.det(rotation) == pytest.approx(1)
    assert np.linalg.norm(transform(points, rotation, translation, scale) - target) > 1


def test_collinear_correspondences_are_underconstrained():
    with pytest.raises(ValueError, match="Collinear"):
        fit_points([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 0, 0], [1, 0, 0], [2, 0, 0]])


def test_downscaling_integer_points_does_not_truncate():
    points = sm.scalePoints([sm.Point(1, 2, 3)], sm.Point(0, 0, 0), .25)
    np.testing.assert_allclose(points, [[.25, .5, .75]])


@pytest.mark.parametrize("method, expected", [("Correlation", correlation), ("Euclidean", euclidean)])
def test_histogram_signed_differences_cannot_cancel(method, expected):
    a, b = [1., 2., 3.], [3., 2., 1.]
    assert sm.distanceMeasure([a], [b], method) == pytest.approx(expected(np.array(a) / sum(a), np.array(b) / sum(b)))
    assert sm.distanceMeasure([a], [b], method) > 0


@pytest.mark.parametrize("a,b", [([1, 1], [1, 2]), ([0, 0], [1, 1]), ([1], [1, 2, 3])])
def test_degenerate_histograms_stay_finite_without_mutation(a, b):
    original_a, original_b = a[:], b[:]
    assert math.isfinite(sm.distanceMeasure([a], [b]))
    assert (a, b) == (original_a, original_b)


def test_histogram_candidate_list_is_noninteractive_and_best_first(cached_model, monkeypatch):
    import copy
    cached_model.featureMaxPoints = [sm.Point(i, 0, 0) for i in (1, 2, 3)]
    partial = copy.copy(cached_model)
    partial.featureMaxPoints = cached_model.featureMaxPoints[:2]

    def unexpected_input(*args):
        pytest.fail("Histogram matching must not prompt for input")

    monkeypatch.setattr("builtins.input", unexpected_input)
    scores = sm.distanceRank2(cached_model, [partial, cached_model])
    assert scores[0] == pytest.approx(1)
    assert scores[0] > scores[1]


def test_inside_solid_has_nonzero_boundary_distance():
    cube = Geometry(BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, -1), 2, 2, 2).Shape())
    points, distances = cube.closest_points([[0, 0, 0], [3, 0, 0]])
    np.testing.assert_allclose(distances, [1, 2], atol=1e-7)


def test_hole_difference_is_measured_in_both_directions():
    cube_shape = BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, -1), 2, 2, 2).Shape()
    drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -2), gp_Dir(0, 0, 1)), .25, 4).Shape()
    plain = Geometry(cube_shape)
    drilled = Geometry(BRepAlgoAPI_Cut(cube_shape, drill).Shape())
    a, b = plain.surface_samples(), drilled.surface_samples()
    errors = surface_errors(plain, drilled, np.eye(3), np.zeros(3), 1, a, b)
    assert errors[1].max() > .1  # Hole wall lies inside the other solid, not on its boundary.
    assert surface_rmse(errors) > 0
    inverse = surface_errors(drilled, plain, np.eye(3), np.zeros(3), 1, b, a)
    assert surface_rmse(inverse) == pytest.approx(surface_rmse(errors))


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("fixture", ["cube/unit_cube.stp", "sphere/unit_sphere.stp",
                                     "cylinder/unit_cyl.stp", "torus/unit_torus.stp",
                                     "miscellanea/CubePyramid.stp"])
def test_real_geometry_recovers_self_alignment_without_feature_points(seed, fixture):
    path = ROOT / "data/primitives" / fixture
    source, target = sm.STEPmodel(), sm.STEPmodel()
    source.importSTEPmodel(path, sm.Point(2, 3, 4), .7, 23 + seed, [1, 2, 3])
    target.importSTEPmodel(path, sm.Point(-3, 1, 2), 3, 61, [2, 1, 1])
    # No feature extraction or access to known placement metadata in register().
    error, (r, t, s), _ = register(source.objectHandle, target.objectHandle)
    assert np.linalg.det(r) == pytest.approx(1)
    assert s == pytest.approx(3 / .7, rel=1e-6)
    assert error / target.objectHandle.diagonal < 1e-6


def test_scale_composition_and_physical_error_units(cube_path):
    source, target = sm.STEPmodel(), sm.STEPmodel()
    source.importSTEPmodel(cube_path, sm.Point(2, 3, 4), .25, 23, [1, 2, 3])
    target.importSTEPmodel(cube_path, sm.Point(-3, 1, 2), 3, 61, [2, 1, 1])
    source.centroid, target.centroid = source.insertionPoint, target.insertionPoint
    original = np.array([[-.5, -.5, -.5], [.5, -.5, -.5], [-.5, .5, -.5], [-.5, -.5, .5]])
    a = transform(original, source.rotationMatrix, np.array(source.insertionPoint), source.scale)
    b = transform(original, target.rotationMatrix, np.array(target.insertionPoint), target.scale)
    # Deliberately inaccurate descriptor scale; paired fitting must correct it.
    estimate = 8.
    scaled_b = (b - target.centroid) / estimate + target.centroid
    errors, r, t, scale = sm.SVDerror(source, target, a.tolist(), scaled_b.tolist(), b.tolist(), estimate, 20)
    assert scale == pytest.approx(target.scale / source.scale)
    np.testing.assert_allclose(transform(a, r, t, scale), b, atol=1e-12)
    assert sm.RMSError(errors) < 1e-10
    # Histogram rejection cannot suppress an independent valid geometric fit.
    result = compare(source, target, .9)
    assert not result["histogram_passed"]
    assert result["alignment_found"]
    assert result["error_units"] == "mm"
    assert result["alignment_error"] < 1e-10


def test_nonzero_errors_are_independent_of_benchmark_scaling():
    plain = BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, -1), 2, 2, 2).Shape()
    drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -2), gp_Dir(0, 0, 1)), .25, 4).Shape()
    drilled = BRepAlgoAPI_Cut(plain, drill).Shape()
    plain_samples = Geometry(plain).surface_samples()
    drilled_samples = Geometry(drilled).surface_samples()

    def model(shape, samples, scale, offset):
        pose = gp_Trsf()
        pose.SetValues(scale, 0, 0, offset, 0, scale, 0, -offset, 0, 0, scale, offset)
        result = sm.STEPmodel()
        result.objectHandle = Geometry(BRepBuilderAPI_Transform(shape, pose, True).Shape())
        # Hold the physical sample locations fixed. Independently re-meshing a
        # symmetric shape can otherwise change this sampled RMS estimate.
        result.objectHandle._samples[96] = samples * scale + np.array([offset, -offset, offset])
        result.scale = scale
        result.centroid = sm.Point(offset, -offset, offset)
        return result

    first = compare(model(plain, plain_samples, 1, 0), model(drilled, drilled_samples, 1, 0), .9)
    placed = compare(model(plain, plain_samples, .25, 10), model(drilled, drilled_samples, 3, -5), .9)
    assert first["alignment_error"] > .01
    for field in ("alignment_error", "normalized_alignment_error", "forward_error", "reverse_error"):
        assert placed[field] == pytest.approx(first[field], rel=1e-5, abs=1e-7)
