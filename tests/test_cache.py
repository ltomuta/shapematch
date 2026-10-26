import json
from pathlib import Path
import shutil

import pytest

from shapematch import Point
from shapematch_cache import CacheError, load_model, save_model, write_json


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


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_nested_cache_paths_travel_between_platforms(cached_model, tmp_path, separator):
    bundle = tmp_path / "CAD bundle é"
    sources = bundle / "STEP files"
    sources.mkdir(parents=True)
    source = sources / "cube é 部件.stp"
    shutil.copyfile(cached_model.filepath, source)
    cached_model.filepath = str(source)
    cache = save_model(cached_model, bundle / "features" / "cube.json")
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert data["source"]["path"] == "../STEP files/cube é 部件.stp"
    data["source"]["path"] = data["source"]["path"].replace("/", separator)
    write_json(cache, data)
    moved = tmp_path / "relocated"
    shutil.move(bundle, moved)
    restored = load_model(moved / "features" / "cube.json")
    assert Path(restored.filepath) == moved / "STEP files" / "cube é 部件.stp"
    assert restored.rayModelIntersection2(Point(0, 0, 0), Point(1, 0, 0)) == [Point(.5, 0, 0)]


def test_cross_drive_cache_uses_absolute_source(cached_model, tmp_path, monkeypatch):
    # ntpath.relpath raises ValueError when the two paths have different drives.
    def different_drives(*args):
        raise ValueError("path is on a different drive")

    monkeypatch.setattr("shapematch_cache.os.path.relpath", different_drives)
    cache = save_model(cached_model, tmp_path / "cache.json")
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert data["source"]["path"] == Path(cached_model.filepath).as_posix()
    assert load_model(cache).featureMaxPoints == cached_model.featureMaxPoints


def test_json_replacement_and_failed_write_preserve_existing_file(tmp_path):
    path = tmp_path / "output.json"
    write_json(path, {"value": 1})
    write_json(path, {"value": 2})
    with pytest.raises(ValueError):
        write_json(path, {"value": float("nan")})
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 2}
    assert list(tmp_path.iterdir()) == [path]
