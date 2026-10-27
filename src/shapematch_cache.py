"""Versioned, data-only feature caches; CAD handles are reconstructed from STEP."""
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import tempfile

FORMAT = "shapematch.features"
VERSION = 1
POINT_LISTS = ("featureMaxPoints", "featureMinPoints", "surfacePoints",
               "rotSymRidgePoints", "rotSymGroovePoints", "featureMaxCentres",
               "featureMinCentres", "spherePoints", "centrePoints")
SCALAR_LISTS = ("featureMaxCurveDisps", "featureMinCurveDisps", "featureSphereDisps")


class CacheError(ValueError):
    pass


def stored_path(path, directory):
    """Use portable relative paths, or absolute paths for different Windows drives.

    A cross-drive reference works locally but cannot travel with a moved bundle.
    Forward slashes are accepted by pathlib on all supported platforms.
    """
    path = Path(path).resolve()
    try:
        return Path(os.path.relpath(path, Path(directory).resolve())).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_stored_path(directory, value):
    """Read portable paths and historical Windows relative paths on Unix."""
    path = Path(value)
    windows = PureWindowsPath(value)
    if os.name != "nt" and windows.drive:
        raise CacheError("Windows absolute path cannot be resolved on this platform; "
                         "regenerate with STEP sources and caches on the same drive")
    candidate = Path(directory) / path
    # Prefer a real native filename, since backslashes are legal on Unix.
    if os.name != "nt" and "\\" in str(value) and not candidate.exists():
        candidate = Path(directory) / windows.as_posix()
    return candidate.resolve()


def source_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    """Replace atomically and never emit nonstandard JSON NaN/Infinity values."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as stream:
            name = stream.name
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return path


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CacheError("Cache coordinates and feature values must be finite numbers")
    return float(value)


def _point(value):
    from shapematch import Point
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise CacheError("Cache points require three coordinates")
    return Point(*(_number(v) for v in value))


def _validate_model(model):
    model.insertionPoint = _point(model.insertionPoint)
    model.centroid = _point(model.centroid)
    model.rotationAxis = list(_point(model.rotationAxis))
    model.rotation = _number(model.rotation)
    model.scale = _number(model.scale)
    if model.scale <= 0:
        raise CacheError("Cache scale must be positive")
    model.generationTime = _number(model.generationTime)
    for field in POINT_LISTS:
        setattr(model, field, [_point(p) for p in getattr(model, field)])
    for field in SCALAR_LISTS:
        setattr(model, field, [_number(v) for v in getattr(model, field)])


def save_model(model, path):
    path = Path(path).resolve()
    if path.suffix.lower() != ".json":
        raise CacheError("Feature caches must use a .json extension")
    _validate_model(model)
    source = Path(model.filepath).resolve()
    features = {key: getattr(model, key) for key in (*POINT_LISTS, *SCALAR_LISTS)}
    features["centroid"] = model.centroid
    data = {
        "format": FORMAT, "version": VERSION,
        "source": {"path": stored_path(source, path.parent), "sha256": source_hash(source)},
        "transform": {"translation": model.insertionPoint, "scale": model.scale,
                      "rotation_degrees": model.rotation, "rotation_axis": model.rotationAxis},
        "features": features, "generation_time": model.generationTime,
        "surface_status": model.surfaceStatus,
    }
    return write_json(path, data)


def load_model(path, load_geometry=True, source_path=None):
    """Load features, optionally rebinding to a byte-identical source copy.

    The override still verifies the stored SHA-256 before geometry is imported.
    This lets a shared app cache outlive its originally referenced STEP path.
    """
    from shapematch import STEPmodel
    path = Path(path).resolve()
    if path.suffix.lower() == ".obj":
        raise CacheError("Legacy .obj caches are unsupported; regenerate from STEP with singlePRtest")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["format"] != FORMAT or data["version"] != VERSION:
            raise CacheError("Unsupported feature cache format/version; regenerate from STEP")
        source = (Path(source_path).resolve() if source_path is not None else
                  resolve_stored_path(path.parent, data["source"]["path"]))
        if not source.is_file():
            raise CacheError(f"Cache STEP source is missing: {source}")
        if source_hash(source) != data["source"]["sha256"]:
            raise CacheError(f"Cache STEP source changed; regenerate features: {source}")
        model = STEPmodel()
        model.filepath, model.name = str(source), source.name
        model.insertionPoint = data["transform"]["translation"]
        model.scale = data["transform"]["scale"]
        model.rotation = data["transform"]["rotation_degrees"]
        model.rotationAxis = data["transform"]["rotation_axis"]
        model.centroid = data["features"]["centroid"]
        model.surfaceStatus = data["surface_status"]
        model.generationTime = data["generation_time"]
        for key in (*POINT_LISTS, *SCALAR_LISTS):
            setattr(model, key, data["features"][key])
        _validate_model(model)
        if load_geometry:
            model.importSTEPmodel()
        return model
    except CacheError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise CacheError(f"Cannot load feature cache {path}: {exc}") from exc
