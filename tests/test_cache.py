import json
from pathlib import Path
import shutil

import pytest

from shapematch import Point
from shapematch_cache import CacheError, load_model, save_model


def test_portable_roundtrip(cached_model, tmp_path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    shutil.move(cached_model.filepath, folder / "source.stp")
    cached_model.filepath = str(folder / "source.stp")
    cache = save_model(cached_model, folder / "cube.features.json")
    data = json.loads(cache.read_text())
    assert data["source"]["path"] == "source.stp"
    assert "objectHandle" not in cache.read_text()
    shutil.move(folder, tmp_path / "moved")
    restored = load_model(tmp_path / "moved/cube.features.json")
    assert restored.featureMaxPoints == cached_model.featureMaxPoints
    assert restored.centroid == Point(0, 0, 0)
    assert restored.rayModelIntersection2(Point(0, 0, 0), Point(1, 0, 0)) == [Point(.5, 0, 0)]


@pytest.mark.parametrize("change", ["missing", "changed", "version", "point", "nan", "missing_field"])
def test_invalid_cache(cached_model, tmp_path, change):
    path = save_model(cached_model, tmp_path / "cache.json")
    data = json.loads(path.read_text())
    if change == "missing":
        Path(cached_model.filepath).unlink()
    elif change == "changed":
        with Path(cached_model.filepath).open("a") as stream:
            stream.write("\n")
    elif change == "version":
        data["version"] = 99
    elif change == "point":
        data["features"]["centroid"] = [1, 2]
    elif change == "nan":
        data["transform"]["scale"] = float("nan")
    else:
        del data["features"]["surfacePoints"]
    path.write_text(json.dumps(data))
    with pytest.raises(CacheError):
        load_model(path)


def test_legacy_rejected_before_deserialization(tmp_path):
    path = tmp_path / "old.obj"
    path.write_bytes(b"not even a pickle")
    with pytest.raises(CacheError, match="regenerate from STEP"):
        load_model(path)


def test_nonfinite_features_are_not_written(cached_model, tmp_path):
    cached_model.featureSphereDisps = [float("nan")]
    target = tmp_path / "features.json"
    with pytest.raises(CacheError, match="finite"):
        save_model(cached_model, target)
    assert not target.exists()
