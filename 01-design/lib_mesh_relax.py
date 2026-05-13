from compas.datastructures import Mesh
from compas.geometry import NurbsSurface
from compas.geometry import Point
from compas.geometry import Polyline
from compas.geometry import Vector
from compas.geometry import closest_point_on_polyline
from compas_rhino.conversions import polyline_to_compas


class MeshRelaxerGoals:
    """Collection of goals for the relaxation of the mesh.

    Attributes
    ----------
    target_boundary
        Optional boundary constraint passed to the mesh relaxer.
    target_corners
        Optional corners constraint passed to the mesh relaxer.
    target_surface
        Optional surface object (must provide ``closest_point``) used
        when snapping centerline endpoints to a surface.
    """

    def __init__(self, target_boundary: Polyline = None, target_corners: list[Point] = None, target_surface: NurbsSurface = None):
        self.target_boundary = target_boundary
        self.target_corners = target_corners
        self.target_surface = target_surface

    @classmethod
    def from_brep(cls, brep) -> "MeshRelaxerGoals":
        """Create a `MeshRelaxerGoals` object from a Brep."""
        curve = brep.Faces[0].OuterLoop.To3dCurve().ToPolyline(1, 1, 0, 0)
        curve = curve.TryGetPolyline()[1]

        target_boundary = polyline_to_compas(curve)
        vertices = [v.Location for v in brep.Vertices]
        target_corners = [Point(v.X, v.Y, v.Z) for v in vertices]

        target_surface = NurbsSurface.from_native(brep.Faces[0].UnderlyingSurface())
        return cls(target_boundary=target_boundary, target_corners=target_corners, target_surface=target_surface)


class MeshRelaxer:
    """
    Relax a mesh by moving its vertices to their average position of their neighbors.

    Parameters
    ----------
    mesh : Mesh
        The mesh to relax.
    iterations : int, optional
        The number of iterations to perform.
    damping : float, optional
        The damping factor.
    modifiers : list, optional
        A list of modifiers to apply during the relaxation.
    goals : MeshRelaxerGoals, optional
        The goals for the relaxation.
    snap_to_surface : bool, optional
        Whether to snap to the surface.

    Attributes
    ----------
    mesh : Mesh
        The mesh to relax.
    iterations : int
        The number of iterations to perform.
    damping : float
        The damping factor.
    modifiers : list
        A list of modifiers to apply during the relaxation.
    goals : MeshRelaxerGoals
        The goals for the relaxation.
    snap_to_surface : bool
        Whether to snap to the surface.
    boundary_vertices : list[int]
        The boundary vertices.
    interior_vertices : list[int]
        The interior vertices.

    """

    def __init__(
        self,
        mesh: Mesh,
        iterations=50,
        damping=0.2,
        modifiers: list = None,
        goals: MeshRelaxerGoals = None,
        snap_to_surface: bool = True,
    ):

        self.mesh = mesh
        self.iterations = iterations
        self.damping = damping
        self.goals = goals
        self.modifiers = modifiers or []
        self.snap_to_surface = snap_to_surface

        self.assigned_vertices = set()
        self.step = 0

        self.set_vertices_default_attributes()

    @property
    def boundary_vertices(self):
        """Return the boundary vertices of the mesh."""
        bvs = [v for v in self.mesh.vertices() if self.mesh.is_vertex_on_boundary(v)]
        return bvs

    @property
    def interior_vertices(self):
        """Return the interior vertices of the mesh."""
        ivs = [v for v in self.mesh.vertices() if not self.mesh.is_vertex_on_boundary(v)]
        return ivs

    # ---- MODIFIERS ---- #

    def set_vertices_default_attributes(self) -> None:
        """Set default attributes for all vertices."""
        for vertex in self.mesh.vertices():
            self.mesh.vertex_attribute(vertex, "fixed", False)
        return None

    # ---- RELAXATION ---- #

    def relax(self) -> Mesh:
        """Relax the mesh using the specified parameters.

        Returns:
            Mesh: The relaxed mesh.
        """
        # Welcome to the spa tratment for your mesh!
        # if corners are not provided, we conside all boundary vertices as corners
        if not self.goals.target_corners:
            self.goals.target_corners = self.goals.target_boundary.points[:-1]  # exclude last point because it's the same as the first one\

        self._apply_mesh_modifiers()

        for _ in range(self.iterations):
            self._compute_interior_forces()

            if self.goals.target_boundary:
                self._compute_boundary_forces()
            if self.goals.target_corners:
                self._compute_corner_forces()
            if self.modifiers:
                self._compute_force_modifiers()

            self._apply_forces()
            self.step += 1

        # Here is the mesh, relaxed and refreshed!~
        # Thank you for choosing our spa services.
        return self.mesh

    def _apply_mesh_modifiers(self):
        """Apply mesh modifiers to the mesh."""
        for mod in self.modifiers:
            if mod.type == "mesh_modifier":
                self.mesh = mod.apply(self, self.mesh)

    def _compute_boundary_forces(self):
        """Compute boundary forces for the mesh."""
        for vertex in self.boundary_vertices:
            if self.mesh.vertex_attribute(vertex, "not_boundary"):
                continue

            # vector towards closest point on polyline
            vertex_point = self.mesh.vertex_point(vertex)
            projected_point = Point(*closest_point_on_polyline(vertex_point, self.goals.target_boundary))
            force = Vector.from_start_end(vertex_point, projected_point)
            force *= self.damping
            self.mesh.vertex_attribute(vertex, "force", force)

    def _compute_interior_forces(self):
        """Compute interior forces for the mesh."""
        for vertex in self.mesh.vertices():
            if vertex in self.boundary_vertices:
                continue
            force = Vector(0, 0, 0)
            neighbors = self.mesh.vertex_neighbors(vertex)
            for neighbor in neighbors:
                neighbor_force = self.mesh.edge_vector((vertex, neighbor))
                force += neighbor_force * self.damping / len(neighbors)
            self.mesh.vertex_attribute(vertex, "force", force)

    def _compute_corner_forces(self):
        """Compute corner forces for the mesh."""
        self.assigned_vertices = set()
        for point in self.goals.target_corners:
            # find the closes boundary vertex,
            # if the vertex has already been assigned, nevermind, just go on
            closest_vertex = min(self.boundary_vertices, key=lambda v: self.mesh.vertex_point(v).distance_to_point(point))
            # if distance  < 100:
            #     self.mesh.vertex_attribute(closest_vertex, "fixed", True)
            if closest_vertex in self.assigned_vertices:
                continue
            self.assigned_vertices.add(closest_vertex)
            # compute the force toward the corner
            vertex_point = self.mesh.vertex_point(closest_vertex)
            corner_force = Vector.from_start_end(vertex_point, point) * self.damping * 2
            # add the corner force to the existing force of the vertex
            force = self.mesh.vertex_attribute(closest_vertex, "force")
            force += corner_force
            self.mesh.vertex_attribute(closest_vertex, "force", force)

    def _compute_force_modifiers(self):
        """Compute force modifiers for the mesh."""
        for mod in self.modifiers:
            if mod.type == "force_modifier":
                self.mesh = mod.apply(self, self.mesh)

    def _apply_forces(self):
        """Apply forces to the mesh."""
        for vertex in self.mesh.vertices():
            # get the force and compute the new point poistion of the vertex
            force = self.mesh.vertex_attribute(vertex, "force")
            vertex_point = self.mesh.vertex_point(vertex)
            new_point = vertex_point + force
            # project the new point onto the surface if required
            if self.goals.target_surface and self.snap_to_surface:
                new_point = self.goals.target_surface.closest_point(new_point)

            if self.mesh.vertex_attribute(vertex, "fixed"):  # the vertex is fixed, do not move it
                continue

            # update the vertex position
            self.mesh.vertex_attribute(vertex, "x", new_point.x)
            self.mesh.vertex_attribute(vertex, "y", new_point.y)
            self.mesh.vertex_attribute(vertex, "z", new_point.z)
