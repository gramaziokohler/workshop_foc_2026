import Rhino.Geometry as rg  # type: ignore
from compas.datastructures import Mesh
from compas.geometry import NurbsSurface
from compas.itertools import linspace


class BaseMesher:
    """Small shared base class for Brep-based meshers."""

    def __init__(self, u_count: int, v_count: int, brep: rg.Brep):
        self.u_count = u_count
        self.v_count = v_count
        self.brep = brep
        self.mesh = Mesh()

    @property
    def face(self) -> rg.BrepFace:
        return self.brep.Faces[0]

    @property
    def surface(self) -> NurbsSurface:
        return NurbsSurface.from_native(self.face.UnderlyingSurface())

    def is_vertex_on_face(self, vertex_key) -> bool:
        point = self.mesh.vertex_point(vertex_key)
        _, face_u, face_v = self.face.ClosestPoint(rg.Point3d(point.x, point.y, point.z))
        relation = self.face.IsPointOnFace(face_u, face_v)

        if relation == rg.PointFaceRelation.Interior:
            return True
        if relation == rg.PointFaceRelation.Boundary:
            return True

        return False

    def _vertex_key(self, u_index: int, v_index: int) -> int:
        return next(self.mesh.vertices_where({"u": u_index, "v": v_index}))

    def _filtered_face_vertices(self, vertex_keys: list[int]) -> list[int]:
        filtered_vertices = []

        for vertex_key in vertex_keys:
            if self.is_vertex_on_face(vertex_key):
                filtered_vertices.append(vertex_key)

        return filtered_vertices


class QuadMesher(BaseMesher):
    """Generate a quad mesh from a single-face Brep.

    Parameters
    ----------
    u_count : int
        Number of cells in the U direction.
    v_count : int
        Number of cells in the V direction.
    brep : rg.Brep
        Input Brep. Only the first face is used.
    full_quads : bool, optional
        If ``True``, keep only complete quads.
        If ``False``, allow clipped boundary polygons.

    Attributes
    ----------
    mesh : compas.datastructures.Mesh
        Output mesh.
    """

    def __init__(self, u_count: int, v_count: int, brep: rg.Brep, full_quads: bool = False):
        super().__init__(u_count=u_count, v_count=v_count, brep=brep)
        self.full_quads = full_quads

    def generate_vertices(self) -> None:
        """Sample the surface on a regular UV grid and store mesh vertices."""
        u_values = list(linspace(self.surface.domain_u[0], self.surface.domain_u[1], self.u_count + 1))
        v_values = list(linspace(self.surface.domain_v[0], self.surface.domain_v[1], self.v_count + 1))

        for ui, u in enumerate(u_values):
            for vi, v in enumerate(v_values):
                point = self.surface.point_at(u, v)
                self.mesh.add_vertex(x=point.x, y=point.y, z=point.z, u=ui, v=vi)
        return None

    def generate_mesh(self) -> Mesh:
        self.generate_vertices()

        for u in range(self.u_count):
            for v in range(self.v_count):
                v1 = self._vertex_key(u, v)
                v2 = self._vertex_key(u, v + 1)
                v3 = self._vertex_key(u + 1, v + 1)
                v4 = self._vertex_key(u + 1, v)
                face_vertices = self._filtered_face_vertices([v1, v2, v3, v4])
                if len(face_vertices) < 3:
                    continue

                if self.full_quads and len(face_vertices) != 4:
                    continue

                self.mesh.add_face(face_vertices)

        self.mesh.remove_unused_vertices()
        return self.mesh


class SimplestTriMesher(QuadMesher):
    def generate_mesh(self) -> Mesh:
        super().generate_mesh()
        self.mesh.quads_to_triangles()
        return self.mesh


class SimplestHexaMesher(SimplestTriMesher):
    def __init__(self, u_count: int, v_count: int, brep: rg.Brep, full_hexas: bool = False):
        super().__init__(u_count=u_count, v_count=v_count, brep=brep)
        self.full_hexas = full_hexas

    def generate_mesh(self) -> Mesh:
        super().generate_mesh()
        self.mesh = self.mesh.dual(include_boundary=True)
        return self.mesh


class TriMesher(BaseMesher):
    def generate_vertices(self) -> None:
        u_line_count = self.u_count + 1
        v_line_count = self.v_count + 2

        u_values = list(linspace(self.surface.domain_u[0], self.surface.domain_u[1], u_line_count))
        v_values = list(linspace(self.surface.domain_v[0], self.surface.domain_v[1], v_line_count))

        for ui, u in enumerate(u_values):
            for vi, v in enumerate(v_values):
                point = self.surface.point_at(u, v)
                self.mesh.add_vertex(x=point.x, y=point.y, z=point.z, u=ui, v=vi)
        return None

    def generate_mesh(self) -> Mesh:
        self.generate_vertices()
        for ui in range(self.u_count):
            for vi in range(self.v_count):
                if (ui + vi) % 2 == 0:  # is even
                    v1 = self._vertex_key(ui, vi + 1)
                    v2 = self._vertex_key(ui + 1, vi + 2)
                    v3 = self._vertex_key(ui + 1, vi)
                else:
                    v1 = self._vertex_key(ui, vi)
                    v2 = self._vertex_key(ui, vi + 2)
                    v3 = self._vertex_key(ui + 1, vi + 1)

                face_vertices = self._filtered_face_vertices([v1, v2, v3])

                if len(face_vertices) < 3:
                    continue

                self.mesh.add_face(face_vertices)
        self.mesh.remove_unused_vertices()
        return self.mesh


class HexaMesher(BaseMesher):
    def __init__(self, u_count: int, v_count: int, brep: rg.Brep, full_hexas: bool = False):
        super().__init__(u_count=u_count, v_count=v_count, brep=brep)
        self.full_hexas = full_hexas

    def generate_vertices(self) -> None:
        u_lines = self.u_count * 2 + 2
        v_lines = self.v_count * 2 + 2

        u_values = list(linspace(self.surface.domain_u[0], self.surface.domain_u[1], u_lines))
        v_values = list(linspace(self.surface.domain_v[0], self.surface.domain_v[1], v_lines))

        for ui, u in enumerate(u_values):
            for vi, v in enumerate(v_values):
                point = self.surface.point_at(u, v)
                self.mesh.add_vertex(x=point.x, y=point.y, z=point.z, u=ui, v=vi)
        return None

    def generate_mesh(self) -> Mesh:
        self.generate_vertices()
        for ui in range(self.u_count):
            for vi in range(self.v_count):
                if vi % 2 == 0:  # is even
                    v1 = self._vertex_key(ui * 2, vi * 2 + 1)
                    v2 = self._vertex_key(ui * 2, vi * 2 + 2)
                    v3 = self._vertex_key(ui * 2 + 1, vi * 2 + 3)
                    v4 = self._vertex_key(ui * 2 + 2, vi * 2 + 2)
                    v5 = self._vertex_key(ui * 2 + 2, vi * 2 + 1)
                    v6 = self._vertex_key(ui * 2 + 1, vi * 2 + 0)
                else:  # is odd
                    v1 = self._vertex_key(ui * 2 + 1, vi * 2 + 1)
                    v2 = self._vertex_key(ui * 2 + 1, vi * 2 + 2)
                    v3 = self._vertex_key(ui * 2 + 2, vi * 2 + 3)
                    v4 = self._vertex_key(ui * 2 + 3, vi * 2 + 2)
                    v5 = self._vertex_key(ui * 2 + 3, vi * 2 + 1)
                    v6 = self._vertex_key(ui * 2 + 2, vi * 2 + 0)

                if self.full_hexas and all([self.is_vertex_on_face(v) for v in [v1, v2, v3, v4, v5, v6]]):
                    self.mesh.add_face([v1, v2, v3, v4, v5, v6])

                elif not self.full_hexas:
                    face_vertices = self._filtered_face_vertices([v1, v2, v3, v4, v5, v6])

                    if len(face_vertices) < 3:
                        continue

                    self.mesh.add_face(face_vertices)

        self.mesh.remove_unused_vertices()
        return self.mesh
