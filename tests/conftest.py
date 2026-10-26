from pathlib import Path
import shutil

import numpy as np
import shapematch as sm

import pytest

from shapematch import Point, STEPmodel

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cube_path():
    return ROOT / "data/primitives/cube/unit_cube.stp"


@pytest.fixture
def cached_model(tmp_path, cube_path):
    source = tmp_path / "source.stp"
    shutil.copyfile(cube_path, source)
    model = STEPmodel()
    model.importSTEPmodel(source)
    model.centroid = Point(0, 0, 0)
    model.featureMaxPoints = [Point(.5, .5, .5), Point(-.5, -.5, -.5)]
    model.surfacePoints = list(model.featureMaxPoints)
    return model


@pytest.fixture
def overlay(cube_path):
    source, target = sm.STEPmodel(), sm.STEPmodel()
    source.importSTEPmodel(cube_path, sm.Point(10, -5, 3), .25, 47, [1, 2, 3])
    target.importSTEPmodel(cube_path, sm.Point(-4, 7, -2), 3, 73, [2, 1, 3])
    rotation = target.rotationMatrix @ source.rotationMatrix.T
    scale = target.scale / source.scale
    translation = np.array(target.insertionPoint) - scale * rotation @ source.insertionPoint
    comparison = {
        "alignment_error": 0., "forward_error": 0., "reverse_error": 0.,
        "normalized_alignment_error": 0., "histogram_threshold": .5,
        "alignment_transform": {"rotation": rotation.tolist(), "translation": translation.tolist(),
                                "scale": scale, "frame": "placed_source_to_placed_target"},
    }
    source.centroid, target.centroid = source.insertionPoint, target.insertionPoint
    return source, target, comparison
