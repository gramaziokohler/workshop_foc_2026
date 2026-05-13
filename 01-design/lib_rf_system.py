from compas.datastructures import Mesh
from compas.geometry import Line
from compas.geometry import Point
from compas.geometry import Vector
from compas.geometry import intersection_line_line


class RFSystem:
    """
    Reciprocal-frame helper built on top of a COMPAS mesh.
    All computed RF data is written back as edge attributes of the mesh.

    The mesh edges become the "members" of the RF system. This class stores extra
    edge attributes needed later for timber fabrication:
    - ``centerline``: geometric line used to create a beam
    - ``normal``: orientation vector for the beam cross-section
    - ``next_edge`` / ``prev_edge``: neighboring RF edges around the local face
    """

    def __init__(self, mesh: Mesh):
        self.mesh = mesh
        self.timber_model = None

    @property
    def centerlines(self) -> list:
        return [self.mesh.edge_attribute(edge, "centerline") for edge in self.mesh.edges()]

    def copy(self) -> "RFSystem":
        return RFSystem(mesh=self.mesh.copy())

    # --------------------------------------------------------------------------
    # RF DATASTRUCTURE SETUP
    # --------------------------------------------------------------------------

    def create_rf_datastructure(self) -> None:
        """
        Compute and store all RF edge attributes.
        """
        # Initialize centerline + normal for every edge.
        for edge in self.mesh.edges():
            self._set_centerline(edge)

            # Boundary edges are valid members, but they have incomplete RF neighborhood data,
            # so we can skip computing those attributes for them.
            if self.mesh.is_edge_on_boundary(edge):
                continue

            self._set_normal(edge)
            self._set_edge_neighborhood(edge)

    def _set_centerline(self, edge) -> None:
        """Store the geometric line representation of a mesh edge."""
        self.mesh.edge_attribute(edge, "centerline", self.mesh.edge_line(edge))

    def _set_normal(self, edge) -> None:
        """Store an orientation normal for an edge (averaged on interior edges, single-face on boundary)."""
        self.mesh.edge_attribute(edge, "normal", self._compute_edge_normal(edge))

    def _set_edge_neighborhood(self, edge) -> None:
        next_edge = self._compute_next_rf_edge(edge)
        prev_edge = self._compute_prev_rf_edge(edge)

        # Store local RF connectivity for interior edges
        self.mesh.edge_attribute(edge, "next_edge", next_edge)
        self.mesh.edge_attribute(edge, "prev_edge", prev_edge)

    def _compute_next_rf_edge(self, edge):
        """Return the next halfedge around the face of the given halfedge."""
        face = self.mesh.halfedge_face(edge)
        halfedges = self.mesh.face_halfedges(face)
        index = halfedges.index(edge)
        return halfedges[(index + 1) % len(halfedges)]

    def _compute_prev_rf_edge(self, edge):
        """Return the previous RF edge by walking the opposite halfedge's face cycle."""
        reversed_edge = (edge[1], edge[0])
        # Reversing first, then taking "next", is a neat way to get the previous RF relation
        return self._compute_next_rf_edge(reversed_edge)

    def _compute_edge_normal(self, edge) -> Vector:
        """
        Compute an edge orientation vector.

        Interior edges use the average of the two neighboring face normals.
        Boundary edges use the single adjacent face normal.
        """
        face_a, face_b = self.mesh.edge_faces(edge)
        normal_a = self.mesh.face_normal(face_a)
        normal_b = self.mesh.face_normal(face_b)

        edge_normal = normal_a + normal_b
        edge_normal.unitize()

        return edge_normal

    # --------------------------------------------------------------------------
    # RF SYSTEM CENTERLINES ROTATION
    # --------------------------------------------------------------------------

    def eccentrize_centerlines(self, eccentricity: float) -> Mesh:
        """
        Shift interior centerlines so beams overlap like a reciprocal frame.

        Positive values push line ends in the local RF directions.
        """
        for edge in self.mesh.edges():
            if self.mesh.is_edge_on_boundary(edge):
                continue

            next_edge = self.mesh.edge_attribute(edge, "next_edge")
            prev_edge = self.mesh.edge_attribute(edge, "prev_edge")
            centerline = self.mesh.edge_attribute(edge, "centerline")

            next_direction = self.mesh.edge_direction(next_edge).unitized()
            prev_direction = self.mesh.edge_direction(prev_edge).unitized()

            start_shift = prev_direction * eccentricity
            end_shift = (-start_shift) + next_direction * eccentricity

            centerline.start += start_shift
            centerline.end += end_shift
            self.mesh.edge_attribute(edge, "centerline", centerline)

        return self.mesh

    def eccentrize_centerlines_attractor_point(self, point: Point, factor: float) -> None:
        """
        Moves the centerlines of the RF system edges towards or away from an attractor point.
        The amount of movement is determined by the distance to the point.
        """
        for edge in self.mesh.edges():
            if self.mesh.is_edge_on_boundary(edge):
                continue
            next_edge = self.mesh.edge_attribute(edge, "next_edge")
            prev_edge = self.mesh.edge_attribute(edge, "prev_edge")
            centerline = self.mesh.edge_attribute(edge, "centerline")

            next_edge_direction = self.mesh.edge_direction(next_edge).unitized()
            prev_edge_direction = self.mesh.edge_direction(prev_edge).unitized()

            node_0 = self.mesh.vertex_point(edge[0])
            node_1 = self.mesh.vertex_point(edge[1])
            eccentricity_0 = point.distance_to_point(node_0) * factor
            eccentricity_1 = point.distance_to_point(node_1) * factor

            start_displacement = prev_edge_direction * eccentricity_0
            end_displacement = -start_displacement + next_edge_direction * eccentricity_1

            centerline.start += start_displacement
            centerline.end += end_displacement
            self.mesh.edge_attribute(edge, "centerline", centerline)

    # ==========================================================
    # -------------------- A01 Challenge 02 --------------------

    def eccentrize_centerlines_attractor_curve(self, curve, factor: float) -> None:
        """Eccentrize centerlines base on the distance to an attractor curve."""
        for edge in self.mesh.edges():
            if self.mesh.is_edge_on_boundary(edge):
                continue
            next_edge = self.mesh.edge_attribute(edge, "next_edge")
            prev_edge = self.mesh.edge_attribute(edge, "prev_edge")
            centerline = self.mesh.edge_attribute(edge, "centerline")

            next_edge_direction = self.mesh.edge_direction(next_edge).unitized()
            prev_edge_direction = self.mesh.edge_direction(prev_edge).unitized()

            node_0 = self.mesh.vertex_point(edge[0])
            node_1 = self.mesh.vertex_point(edge[1])
            closest_point_0 = curve.closest_point(node_0)
            closest_point_1 = curve.closest_point(node_1)
            eccentricity_0 = closest_point_0.distance_to_point(node_0) * factor
            eccentricity_1 = closest_point_1.distance_to_point(node_1) * factor

            start_displacement = prev_edge_direction * eccentricity_0
            end_displacement = -start_displacement + next_edge_direction * eccentricity_1

            centerline.start += start_displacement
            centerline.end += end_displacement
            self.mesh.edge_attribute(edge, "centerline", centerline)

    def extend_centerlines(self, extension: float) -> None:
        """
        Extend interior centerlines and trim them at adjacent boundary edges when needed.
        """
        for edge in self.mesh.edges():
            if self.mesh.is_edge_on_boundary(edge):
                continue
            extend_start = not self.mesh.is_vertex_on_boundary(edge[0])
            extend_end = not self.mesh.is_vertex_on_boundary(edge[1])

            centerline: Line = self.mesh.edge_attribute(edge, "centerline")
            direction = centerline.direction.unitized()
            if extend_start and extend_end:
                centerline.start += direction * (-extension)
                centerline.end += direction * extension * 2
            elif extend_start and not extend_end:
                centerline.start += direction * (-extension)
                centerline.end += direction * extension
            elif not extend_start and extend_end:
                centerline.end += direction * extension

            self.mesh.edge_attribute(edge, "centerline", centerline)

    # --------------------------------------------------------------------------
    # A03 Adjust centerlines optimization
    # --------------------------------------------------------------------------

    def compute_spring_forces(self, damping):
        for edge in self.mesh.edges():
            if self.mesh.is_edge_on_boundary(edge):
                continue
            # get the data of the edge
            centerline: Line = self.mesh.edge_attribute(edge, "centerline")
            next_edge = self.mesh.edge_attribute(edge, "next_edge")
            prev_edge = self.mesh.edge_attribute(edge, "prev_edge")
            next_centerline: Line = self.mesh.edge_attribute(next_edge, "centerline")
            prev_centerline: Line = self.mesh.edge_attribute(prev_edge, "centerline")

            # compute the vector to the end target
            _, target_end_point = intersection_line_line(centerline, next_centerline)
            vector_to_end_target = Vector.from_start_end(centerline.end, Point(*target_end_point)) * damping
            self.mesh.edge_attribute(edge, "vector_to_end_target", vector_to_end_target)

            # compute the vector to the start target
            _, target_start_point = intersection_line_line(centerline, prev_centerline)
            vector_to_start_target = Vector.from_start_end(centerline.start, Point(*target_start_point)) * damping
            self.mesh.edge_attribute(edge, "vector_to_start_target", vector_to_start_target)

    def apply_spring_forces(self):
        for edge in self.mesh.edges():
            if self.mesh.is_edge_on_boundary(edge):
                continue

            centerline: Line = self.mesh.edge_attribute(edge, "centerline")
            vector_to_end_target = self.mesh.edge_attribute(edge, "vector_to_end_target")
            vector_to_start_target = self.mesh.edge_attribute(edge, "vector_to_start_target")
            centerline.start += vector_to_start_target
            centerline.end += vector_to_end_target + vector_to_start_target.flipped()
            self.mesh.edge_attribute(edge, "centerline", centerline)

    def snap_centerlines_to_surface(self, goals):
        if not goals.target_surface:
            return
        for edge in self.mesh.edges():
            centerline = self.mesh.edge_attribute(edge, "centerline")
            new_start = goals.target_surface.closest_point(centerline.start)
            new_end = goals.target_surface.closest_point(centerline.end)
            centerline.start = new_start
            centerline.end = new_end
            self.mesh.edge_attribute(edge, "centerline", centerline)
