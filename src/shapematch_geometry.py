"""Headless Open CASCADE geometry boundary. Coordinates use STEP reader units (mm)."""
import math
from pathlib import Path

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool, BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform, BRepBuilderAPI_MakeVertex, BRepBuilderAPI_MakeSolid
from OCP.BRepLib import BRepLib
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus
from OCP.Geom import Geom_Line
from OCP.GeomAPI import GeomAPI_IntCS
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt, gp_Trsf
from OCP.IFSelect import IFSelect_RetDone
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_REVERSED, TopAbs_IN, TopAbs_ON
from OCP.Standard import Standard_NullObject
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound


class GeometryError(ValueError):
    """An input cannot be used as a closed CAD surface model."""


class Geometry:
    def __init__(self, shape):
        if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
            raise GeometryError("STEP model is empty or has invalid geometry")
        self.import_warnings = []
        # STEP exports may carry cosmetic surfaces alongside actual bodies.
        # Keep solids intact (including cavity shells); only standalone closed
        # shells may be promoted to solids. Open surfaces never define volume.
        builder = BRep_Builder()
        bodies = TopoDS_Compound()
        builder.MakeCompound(bodies)
        solids = TopExp_Explorer(shape, TopAbs_SOLID)
        count, excluded = 0, 0
        while solids.More():
            solid = solids.Current()
            shells = TopExp_Explorer(solid, TopAbs_SHELL)
            while shells.More():
                if not BRep_Tool.IsClosed_s(shells.Current()):
                    raise GeometryError("STEP solid contains an open shell")
                shells.Next()
            builder.Add(bodies, solid)
            count += 1
            solids.Next()
        shells = TopExp_Explorer(shape, TopAbs_SHELL, TopAbs_SOLID)
        while shells.More():
            if not BRep_Tool.IsClosed_s(shells.Current()):
                excluded += 1
            else:
                solid = BRepBuilderAPI_MakeSolid(TopoDS.Shell(shells.Current())).Solid()
                if not BRepLib.OrientClosedSolid_s(solid) or not BRepCheck_Analyzer(solid).IsValid():
                    raise GeometryError("STEP closed shell cannot form a valid solid")
                builder.Add(bodies, solid)
                count += 1
            shells.Next()
        if not count:
            raise GeometryError("STEP model must contain a closed shell")
        if excluded:
            self.import_warnings.append(f"Ignored {excluded} open surface shells; matching closed bodies only")
        loose_faces = TopExp_Explorer(shape, TopAbs_FACE, TopAbs_SHELL)
        if loose_faces.More():
            self.import_warnings.append("Ignored standalone surface faces; matching closed bodies only")
        shape = bodies
        self.shape = shape
        bounds = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bounds, False, False)
        lower, upper = bounds.CornerMin(), bounds.CornerMax()
        self.bounds_center = tuple((a + b) / 2 for a, b in zip(
            (lower.X(), lower.Y(), lower.Z()), (upper.X(), upper.Y(), upper.Z())))
        self.diagonal = lower.Distance(upper)
        self.tolerance = max(1e-7, 1e-7 * self.diagonal)
        self._intersector = IntCurvesFace_ShapeIntersector()
        self._intersector.Load(shape, self.tolerance)
        self._face_intersectors = None
        self._analytic_faces = set()
        # A solid treats an interior vertex as zero distance. Distance to a
        # compound of faces measures the boundary, including internal hole walls.
        self._faces = []
        builder = BRep_Builder()
        self._boundary = TopoDS_Compound()
        builder.MakeCompound(self._boundary)
        faces = TopExp_Explorer(shape, TopAbs_FACE)
        while faces.More():
            face = TopoDS.Face(faces.Current())
            self._faces.append(face)
            builder.Add(self._boundary, face)
            faces.Next()
        self._samples = {}

    def principal_frame(self):
        """Volume, center of mass and orthonormal principal inertia axes."""
        import numpy as np
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(self.shape, properties)
        volume = abs(properties.Mass())
        if volume <= 0:
            raise GeometryError("Alignment requires nonzero enclosed volume")
        center = properties.CentreOfMass()
        inertia = properties.MatrixOfInertia()
        matrix = np.array([[inertia.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
        return volume, np.array([center.X(), center.Y(), center.Z()]), np.linalg.eigh(matrix)[1]

    def planar_frame(self):
        """A face-normal frame resolves isotropic inertia, e.g. a rotated cube."""
        import numpy as np
        first = None
        for face in self._faces:
            surface = BRepAdaptor_Surface(face)
            if surface.GetType() != GeomAbs_Plane:
                continue
            direction = surface.Plane().Axis().Direction()
            normal = np.array([direction.X(), direction.Y(), direction.Z()])
            if first is None:
                first = normal
                continue
            cross = np.cross(first, normal)
            if np.linalg.norm(cross) > 1e-6:
                third = cross / np.linalg.norm(cross)
                return np.column_stack((first, np.cross(third, first), third))
        return None

    def closest_points(self, points, boundary=None):
        """Exact closest bounded-surface points, also for points inside a solid."""
        import numpy as np
        closest, distances = [], []
        tool = BRepExtrema_DistShapeShape()
        tool.LoadS2(self._boundary if boundary is None else boundary)
        for point in points:
            tool.LoadS1(BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, point))).Vertex())
            if not tool.Perform() or not tool.NbSolution():
                raise GeometryError("Closest surface point computation failed")
            p = tool.PointOnShape2(1)
            closest.append((p.X(), p.Y(), p.Z()))
            distances.append(tool.Value())
        return np.array(closest), np.array(distances)

    def triangle_mesh(self):
        """A display mesh with explicit topology, including holes and face locations.

        This tessellation is only for visualization; distances still use exact
        CAD faces. Reverse oriented faces to preserve outward triangle winding.
        """
        import numpy as np
        mesher = BRepMesh_IncrementalMesh(self.shape, self.diagonal * .0005, False, .15, False)
        if not mesher.IsDone():
            raise GeometryError("Cannot tessellate CAD geometry for visualization")
        vertices, triangles = [], []
        for face in self._faces:
            location = TopLoc_Location()
            mesh = BRep_Tool.Triangulation_s(face, location)
            if mesh is None:
                raise GeometryError("Cannot visualize an unmeshed CAD face")
            offset = len(vertices)
            for i in range(1, mesh.NbNodes() + 1):
                point = mesh.Node(i).Transformed(location.Transformation())
                vertices.append((point.X(), point.Y(), point.Z()))
            for i in range(1, mesh.NbTriangles() + 1):
                a, b, c = mesh.Triangle(i).Get()
                if face.Orientation() == TopAbs_REVERSED:
                    b, c = c, b
                triangles.append((offset + a - 1, offset + b - 1, offset + c - 1))
        return np.asarray(vertices, dtype=float), np.asarray(triangles, dtype=int)

    def surface_samples(self, count=96):
        """Deterministic area samples, with at least one on every bounded face.

        Triangles select sample locations only; projection back to each exact CAD
        face prevents tessellation error from appearing as geometric deviation.
        """
        import numpy as np
        if count in self._samples:
            return self._samples[count]
        BRepMesh_IncrementalMesh(self.shape, self.diagonal * .002, False, .2, False)
        triangles, areas = [], []
        for face in self._faces:
            location = TopLoc_Location()
            mesh = BRep_Tool.Triangulation_s(face, location)
            if mesh is None:
                raise GeometryError("Cannot sample an unmeshed CAD face")
            face_triangles = []
            for i in range(1, mesh.NbTriangles() + 1):
                nodes = [mesh.Node(j).Transformed(location.Transformation()) for j in mesh.Triangle(i).Get()]
                face_triangles.append([(p.X(), p.Y(), p.Z()) for p in nodes])
            face_triangles = np.asarray(face_triangles)
            face_areas = np.linalg.norm(np.cross(face_triangles[:, 1] - face_triangles[:, 0],
                                                 face_triangles[:, 2] - face_triangles[:, 0]), axis=1) / 2
            valid = face_areas > 0
            if not valid.any():
                raise GeometryError("CAD face has no nondegenerate sampling triangles")
            triangles.append(face_triangles[valid])
            areas.append(face_areas[valid])
        # Allocate the remaining budget by area. Face minima deliberately keep
        # small holes represented rather than silently dropping them.
        totals = np.array([a.sum() for a in areas])
        extra = max(0, count - len(areas)) * totals / totals.sum()
        allocation = np.floor(extra).astype(int) + 1
        remainder = max(count, len(areas)) - allocation.sum()
        allocation[np.argsort(-(extra - np.floor(extra)), kind="stable")[:remainder]] += 1
        rng = np.random.default_rng(0)
        samples = []
        for face, face_triangles, face_areas, amount in zip(self._faces, triangles, areas, allocation):
            selection = rng.choice(len(face_triangles), size=amount, p=face_areas / face_areas.sum())
            chosen = face_triangles[selection]
            uv = rng.random((amount, 2))
            root = np.sqrt(uv[:, 0])
            points = ((1 - root[:, None]) * chosen[:, 0]
                      + (root * (1 - uv[:, 1]))[:, None] * chosen[:, 1]
                      + (root * uv[:, 1])[:, None] * chosen[:, 2])
            samples.extend(self.closest_points(points, face)[0])
        self._samples[count] = np.asarray(samples)
        return self._samples[count]

    @classmethod
    def read_step(cls, path, translation, scale, rotation):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"STEP source does not exist: {path}")
        if not math.isfinite(scale) or scale <= 0:
            raise GeometryError("Scale must be finite and positive")
        values = [float(rotation[i][j]) * scale for i in range(3) for j in range(3)]
        if not all(math.isfinite(v) for v in [*values, *translation]):
            raise GeometryError("Transform values must be finite")
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != IFSelect_RetDone or reader.TransferRoots() == 0:
            raise GeometryError(f"Cannot read STEP geometry: {path}")
        shape = reader.OneShape()
        # Validate before transformation so malformed input cannot be masked by a copy.
        original = cls(shape)
        if values == [1., 0., 0., 0., 1., 0., 0., 0., 1.] and all(float(v) == 0 for v in translation):
            # Native-coordinate scans need no deep CAD copy or second topology
            # validation, which dominate loading time for large assemblies.
            return original
        transform = gp_Trsf()
        transform.SetValues(
            *[value for i in range(3)
              for value in [*values[3 * i:3 * i + 3], float(translation[i])]]
        )
        result = cls(BRepBuilderAPI_Transform(original.shape, transform, True).Shape())
        result.import_warnings = original.import_warnings + result.import_warnings
        return result

    def _face_hits(self, line, lower, upper):
        """Exact per-face retry for kernel failures at analytic surface poles.

        Intersecting an untrimmed support alone is insufficient: classify every
        result against the bounded face so holes and trimming remain respected.
        Unknown/nonanalytic failures propagate rather than dropping a face.
        """
        if self._face_intersectors is None:
            self._face_intersectors = []
            for face in self._faces:
                tool = IntCurvesFace_ShapeIntersector()
                tool.Load(face, self.tolerance)
                self._face_intersectors.append(tool)
        hits = []
        for index, (face, tool) in enumerate(zip(self._faces, self._face_intersectors)):
            if index not in self._analytic_faces:
                try:
                    tool.Perform(line, lower, upper)
                    if not tool.IsDone():
                        raise GeometryError("Surface intersection did not complete")
                    hits.extend((tool.WParameter(i), tool.Pnt(i)) for i in range(1, tool.NbPnt() + 1))
                    continue
                except Standard_NullObject:
                    self._analytic_faces.add(index)
            kind = BRepAdaptor_Surface(face).GetType()
            if kind not in (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus):
                raise GeometryError(f"Ray intersection failed on face {index} ({kind})")
            intersection = GeomAPI_IntCS(Geom_Line(line), BRep_Tool.Surface_s(face))
            if not intersection.IsDone():
                raise GeometryError(f"Analytic ray intersection failed on face {index}")
            for i in range(1, intersection.NbPoints() + 1):
                point = intersection.Point(i)
                distance = line.Direction().XYZ().Dot(point.XYZ() - line.Location().XYZ())
                if lower <= distance <= upper and BRepClass_FaceClassifier(face, point, self.tolerance).State() in (TopAbs_IN, TopAbs_ON):
                    hits.append((distance, point))
        return hits

    def intersections(self, origin, through, forward=True):
        """Intersect bounded faces with a line/ray through two points, in signed order."""
        if not all(math.isfinite(float(v)) for v in [*origin, *through]):
            raise GeometryError("Intersection coordinates must be finite")
        direction = tuple(float(b) - float(a) for a, b in zip(origin, through))
        if math.hypot(*direction) == 0:
            raise GeometryError("Ray direction must be nonzero")
        line = gp_Lin(gp_Pnt(*origin), gp_Dir(*direction))
        # All boundary points lie within half the box diagonal of its center.
        # Finite limits avoid overflow without clipping rays from distant origins.
        bound = math.dist(origin, self.bounds_center) + self.diagonal + self.tolerance
        lower, upper = (0.0 if forward else -bound), bound
        if self._face_intersectors is not None:
            hits = self._face_hits(line, lower, upper)
        else:
            try:
                self._intersector.Perform(line, lower, upper)
                if not self._intersector.IsDone():
                    raise GeometryError("Surface intersection did not complete")
                hits = [(self._intersector.WParameter(i), self._intersector.Pnt(i))
                        for i in range(1, self._intersector.NbPnt() + 1)]
            except Standard_NullObject:
                hits = self._face_hits(line, lower, upper)
                self.import_warnings.append("Used exact analytic fallback for a CAD ray intersection failure")
        hits.sort(key=lambda item: item[0])
        points = []
        for distance, point in hits:
            if forward and distance <= self.tolerance:
                continue
            xyz = (point.X(), point.Y(), point.Z())
            if not points or math.dist(points[-1], xyz) > self.tolerance:
                points.append(xyz)
        return points
