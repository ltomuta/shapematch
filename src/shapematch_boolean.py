"""Exact CAD solid intersection and directional differences for HTML export."""
import math
import numpy as np

from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Trsf
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.collections import List_TopoDS_Shape
from OCP.TopoDS import TopoDS_Compound

from shapematch_geometry import Geometry, GeometryError


def transform_shape(shape, rotation, translation, scale=1):
    """Origin-based positive similarity, matching the alignment convention."""
    matrix = np.asarray(rotation) * scale
    pose = gp_Trsf()
    pose.SetValues(*[value for i in range(3) for value in [*matrix[i], float(translation[i])]])
    return BRepBuilderAPI_Transform(shape, pose, True).Shape()


def _solids(shape):
    """Keep all solid components; face/edge contact has no common volume."""
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    count = 0
    if not shape.IsNull():
        explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        while explorer.More():
            builder.Add(compound, explorer.Current())
            count += 1
            explorer.Next()
    return compound, count


def validate_tolerance(value):
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Boolean tolerance must be a finite nonnegative distance in mm") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("Boolean tolerance must be a finite nonnegative distance in mm")
    return value


def solid_partitions(a, b, tolerance=0.):
    """Return A∩B, A−B and B−A without modifying either input body.

    Tolerance is an additional positional tolerance in native millimeters,
    applied by the CAD kernel to near-coincident entities, not a volume filter.
    Zero retains the kernel's 1e-7 mm numerical baseline.
    """
    tolerance = validate_tolerance(tolerance)
    a, count_a = _solids(a)
    b, count_b = _solids(b)
    if not count_a or not count_b:
        raise GeometryError("Boolean regions require solid CAD bodies, not only surface shells")
    regions = {}
    for key, operation, first, second in [
        ("common", BRepAlgoAPI_Common, a, b),
        ("only_a", BRepAlgoAPI_Cut, a, b),
        ("only_b", BRepAlgoAPI_Cut, b, a),
    ]:
        arguments, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
        arguments.Append(first)
        tools.Append(second)
        boolean = operation()
        boolean.SetArguments(arguments)
        boolean.SetTools(tools)
        boolean.SetNonDestructive(True)
        boolean.SetRunParallel(False)
        boolean.SetFuzzyValue(max(1e-7, tolerance))
        boolean.Build()
        if not boolean.IsDone():
            raise GeometryError(f"CAD Boolean operation failed for {key}")
        region, count = _solids(boolean.Shape())
        if not count:
            regions[key] = {"vertices": [], "triangles": [], "volume": 0., "components": 0}
            continue
        if not BRepCheck_Analyzer(region).IsValid():
            raise GeometryError(f"CAD Boolean result is invalid for {key} at {tolerance:g} mm tolerance")
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(region, properties)
        vertices, triangles = Geometry(region).triangle_mesh()
        regions[key] = {"vertices": vertices.tolist(), "triangles": triangles.tolist(),
                        "volume": abs(float(properties.Mass())), "components": count}
    return regions


def comparison_partitions(source, target, rotation, translation, scale, tolerance=0.):
    """Compute both placements in target native STEP coordinates and mm³."""
    tolerance = validate_tolerance(tolerance)
    source_rotation = np.eye(3) if source.rotationMatrix is None else source.rotationMatrix
    target_rotation = np.eye(3) if target.rotationMatrix is None else target.rotationMatrix
    target_body = transform_shape(target.objectHandle.shape, target_rotation.T,
                                  -target_rotation.T @ np.asarray(target.insertionPoint) / target.scale,
                                  1 / target.scale)
    original = transform_shape(source.objectHandle.shape, source_rotation.T,
                               -source_rotation.T @ np.asarray(source.insertionPoint) / source.scale,
                               1 / source.scale)
    aligned = transform_shape(source.objectHandle.shape, target_rotation.T @ rotation,
                              target_rotation.T @ (translation - target.insertionPoint) / target.scale,
                              scale / target.scale)
    results = {}
    for placement, source_body in (("original", original), ("aligned", aligned)):
        try:
            results[placement] = {"regions": solid_partitions(source_body, target_body, tolerance=tolerance), "error": None}
        except (RuntimeError, ValueError) as exc:
            # A failed Boolean is not an empty difference. Preserve the overlay
            # and make the unavailable classification explicit for this pose.
            results[placement] = {"regions": {}, "error": str(exc)}
    return results
