from compas.datastructures import Mesh
from compas.geometry import Point
from compas.geometry import Vector
from compas.geometry import closest_point_on_line
from compas.geometry import closest_point_on_polyline
from compas.geometry import distance_point_line

# ---- MESH MODIFIERS ---- #


class SnapVertexToPoint:
    """Modifier that snaps a mesh vertex to a specified point and marks it fixed.

    Attributes:
        vertex (int): vertex key to move.
        point (compas.geometry.Point): target point to snap the vertex to.
        type (str): modifier type identifier ("mesh_modifier").
    """

    def __init__(self, vertex: int, point: Point):
        self.vertex = vertex
        self.point = point
        self.type = "mesh_modifier"

    def apply(self, relaxer, mesh):
        mesh.vertex_attributes(self.vertex, "xyz", list(self.point))
        mesh.vertex_attribute(self.vertex, "fixed", True)
        return mesh


class FixedVertex:
    """Modifier that marks a mesh vertex as fixed (prevents movement).

    Attributes:
        vertex (int): vertex key to mark fixed.
        type (str): modifier type identifier ("mesh_modifier").
    """

    def __init__(self, vertex: int):
        self.vertex = vertex
        self.type = "mesh_modifier"

    def apply(self, relaxer, mesh):
        mesh.vertex_attribute(self.vertex, "fixed", True)
        return mesh


class MergeFaces:
    """Modifier that attempts to merge a list of 2 faces into a single face.

    Attributes:
        faces (list): list of 2 faces identifiers to merge.
        type (str): modifier type identifier ("mesh_modifier").
    """

    def __init__(self, faces: list):
        self.faces = faces
        self.type = "mesh_modifier"

    def apply(self, relaxer, mesh):
        try:
            mesh.merge_faces(self.faces)
        except Exception:
            print(f"Couldn't merge {self.faces}.")
        return mesh


# --- FORCE MODIFIERS


class AttractorPointModifier:
    """Force modifier that attracts interior vertices toward a specified point."""

    def __init__(self, point: Point, force: float):
        self.point = point
        self.attraction_force = force
        self.type = "force_modifier"

    def apply(self, relaxer, mesh) -> Mesh:
        for vertex in relaxer.interior_vertices:
            vertex_point = mesh.vertex_point(vertex)
            direction = Vector.from_start_end(vertex_point, self.point)
            attraction_force = direction * (1 / direction.length) * self.attraction_force
            force = mesh.vertex_attribute(vertex, "force")
            force += attraction_force
            mesh.vertex_attribute(vertex, "force", force)
        return mesh


class DirectionalForce:
    """Apply a constant directional force to interior vertices.

    Attributes:
        direction (compas.geometry.Vector): unit or non-unit direction of force.
        force (float): magnitude to scale the direction.
        type (str): modifier type identifier ("force_modifier").

    Behavior:
        Adds direction * force to the 'force' attribute of each interior vertex.
    """

    def __init__(self, direction: Vector, force: float):
        self.direction = direction
        self.force = force
        self.type = "force_modifier"

    def apply(self, relaxer, mesh):
        for vertex in relaxer.interior_vertices:
            force = mesh.vertex_attribute(vertex, "force")
            directional_force = self.direction * self.force
            force += directional_force
            mesh.vertex_attribute(vertex, "force", force)
        return mesh


class PullBoundaryToOutline:
    """Force modifier that pulls boundary vertices towards the boundary polyline.

    Attributes:
        strength (float): multiplier for the pulling force.
        type (str): modifier type identifier ("force_modifier").

    Behavior:
        For each boundary vertex, finds the closest point on the relaxer's boundary
        polyline and applies a force towards it.
    """

    def __init__(self, strength: float):
        self.strength = strength
        self.type = "force_modifier"

    def apply(self, relaxer, mesh):
        if not relaxer.boundary:
            return mesh

        for vertex in relaxer.boundary_vertices:
            # Skip if vertex is already fixed/assigned
            if vertex in relaxer.assigned_vertices:
                continue

            vertex_point = mesh.vertex_point(vertex)
            closest_point = closest_point_on_polyline(vertex_point, relaxer.boundary)
            pull_direction = Vector.from_start_end(vertex_point, closest_point)

            if pull_direction.length > 1e-6:
                pull_force = pull_direction * self.strength
                force = mesh.vertex_attribute(vertex, "force")
                force += pull_force
                mesh.vertex_attribute(vertex, "force", force)

        return mesh


class TargetEdgeLengthSpringForce:
    """Spring-like force that drives edges toward a target length.

    Attributes:
        target_edge_length (float): desired edge length.
        K (float): spring stiffness constant.
        type (str): modifier type identifier ("force_modifier").

    Behavior:
        For each interior vertex, sums spring forces from adjacent edges based
        on the difference between current and target lengths.
    """

    def __init__(self, target_edge_length: float, K: float):
        self.target_edge_length = target_edge_length
        self.K = K
        self.type = "force_modifier"

    def apply(self, relaxer, mesh) -> Mesh:
        for vertex in relaxer.interior_vertices:
            force = mesh.vertex_attribute(vertex, "force")
            nbvxs = mesh.vertex_neighbors(vertex)
            for vnb in nbvxs:
                edge = (vertex, vnb)
                edge_vector = mesh.edge_vector(edge)
                edge_length = edge_vector.length
                length_difference = edge_length - self.target_edge_length
                edge_direction = edge_vector.unitized()
                spring_force = edge_direction * (self.K * length_difference)
                force += spring_force
            mesh.vertex_attribute(vertex, "force", force)
        return mesh


class SameBorderEdgeLength:
    """Force modifier to make boundary vertex incident boundary edges have similar lengths.

    Attributes:
        strength (float): scaling factor for the corrective force.
        ignore_corner_points (bool): whether to skip assigned/corner vertices.
        type (str): modifier type identifier ("force_modifier").

    Behavior:
        For each boundary vertex, applies a force along the longest boundary
        edge toward equalizing lengths with the shortest boundary edge.
    """

    def __init__(self, strength: float, ignore_corner_points: bool = False):
        self.strength = strength
        self.ignore_corner_points = ignore_corner_points
        self.type = "force_modifier"

    def apply(self, relaxer, mesh):
        for vertex in relaxer.boundary_vertices:
            if not self.ignore_corner_points:
                if vertex in relaxer.assigned_vertices:
                    continue
            bedges = []
            for edge in mesh.vertex_edges(vertex):
                if mesh.is_edge_on_boundary(edge):
                    if edge[0] == vertex:
                        bedges.append(edge)
                    else:
                        bedges.append((edge[1], edge[0]))

            longest_edge = max(bedges, key=lambda e: mesh.edge_length(e))
            shortest_edge = min(bedges, key=lambda e: mesh.edge_length(e))
            delta_length = mesh.edge_length(longest_edge) - mesh.edge_length(shortest_edge)
            if delta_length == 0:
                continue
            force = mesh.vertex_attribute(vertex, "force")
            this_force = mesh.edge_vector(longest_edge).unitized() * (self.strength * delta_length)
            force += this_force
            mesh.vertex_attribute(vertex, "force", force)

        return mesh


class FixedBoundaryVertices:
    """Modifier that marks all boundary vertices as fixed (prevents movement).

    Attributes:
        type (str): modifier type identifier ("mesh_modifier").
    """

    def __init__(self):
        self.type = "mesh_modifier"

    def apply(self, relaxer, mesh):
        for vertex in relaxer.boundary_vertices:
            mesh.vertex_attribute(vertex, "fixed", True)
        return mesh


class IncreaseForceAroundBorders:
    """Increase forces on interior vertices adjacent to boundary vertices.

    Attributes:
        strength (float): multiplier for the boundary-proximity force.
        type (str): modifier type identifier ("force_modifier").

    Behavior:
        For each interior vertex, inspects neighboring vertices; if a neighbor
        is on the boundary, adds a scaled force in the direction of that edge.
    """

    def __init__(self, strength: float):
        self.strength = strength
        self.type = "force_modifier"

    def apply(self, relaxer, mesh):
        for vertex in relaxer.interior_vertices:
            neighboring_vertex = mesh.vertex_neighbors(vertex)
            force = mesh.vertex_attribute(vertex, "force")
            for nvx in neighboring_vertex:
                if mesh.is_vertex_on_boundary(nvx):
                    boundary_force = mesh.edge_vector((vertex, nvx)).unitized() * self.strength * 0.0001
                    force += boundary_force
            mesh.vertex_attribute(vertex, "force", force)
        return mesh


class SimplifyBorderEdges:
    """Mesh modifier that simplifies single-face boundary vertices by merging faces.

    Behavior:
        For boundary vertices connected to only one face, removes that face and
        recreates a face without the vertex to simplify the border.
        type (str): modifier type identifier ("mesh_modifier").
    """

    def __init__(self):
        self.type = "mesh_modifier"

    def apply(self, relaxer, mesh):
        for vertex in relaxer.boundary_vertices:
            vc_edges = mesh.vertex_edges(vertex)
            if all([mesh.is_edge_on_boundary(e) for e in vc_edges]):
                # the vertex is connected only to one face
                face = mesh.vertex_faces(vertex)[0]
                vertices = mesh.face_vertices(face)
                mesh.delete_face(face)
                vertices.remove(vertex)
                mesh.add_face(vertices)
        return mesh


class InteriorBrepEdgeAttractor:
    """Attract interior vertices toward the closest brep interior edge with damping.

    Attributes:
        attraction_damping (float): additional damping applied to attraction.
        max_distance (float): maximum allowed magnitude for applied attraction force.
        type (str): modifier type identifier ("force_modifier").

    Behavior:
        Finds the closest brep interior edge to each interior vertex, computes a
        damped attraction vector toward the edge, and accumulates it into the
        vertex 'force' attribute unless it exceeds max_distance.
    """

    def __init__(self, attraction_damping: float, max_distance: float):
        self.type = "force_modifier"
        self.attraction_damping = attraction_damping
        self.max_distance = max_distance

    def apply(self, relaxer, mesh) -> Mesh:
        for vertex in mesh.vertices():
            if vertex in relaxer.boundary_vertices:
                continue
            vertex_point = mesh.vertex_point(vertex)
            closest_edge = min(
                relaxer.brep_interior_edges,
                key=lambda e: distance_point_line(vertex_point, e),
            )
            closest_point = closest_point_on_line(vertex_point, closest_edge)
            force = mesh.vertex_attribute(vertex, "force")
            edge_force = Vector.from_start_end(vertex_point, closest_point) * relaxer.damping * self.attraction_damping
            if edge_force.magnitude > self.max_distance:
                continue
            force += edge_force
            mesh.vertex_attribute(vertex, "force", force)
        return mesh


class BoundaryEdgeLengthOptimizer:
    """
    Force modifier that tries to optimize the lengths of boundary edges.
    If `target_edge_length` is None the modifier computes the average
    length of all boundary edges and applies spring forces to drive
    boundary edges towards that target.
    """

    def __init__(self, target_edge_length: float = None, K: float = 1.0):
        self.target_edge_length = target_edge_length
        self.K = K
        self.type = "force_modifier"

    def apply(self, relaxer, mesh) -> Mesh:
        # determine the target length (average boundary edge length if not given)
        target = self.target_edge_length
        if target is None:
            total = 0.0
            count = 0
            bvs = relaxer.boundary_vertices
            for v in bvs:
                for nb in mesh.vertex_neighbors(v):
                    if nb in bvs and v < nb:
                        total += mesh.edge_vector((v, nb)).length
                        count += 1
            if count > 0:
                target = total / count
            else:
                return mesh

        # determine which boundary vertices correspond to corners and skip them
        corner_vertices = set()
        if hasattr(relaxer, "corners") and relaxer.corners:
            # compute a tolerance from the average boundary edge length
            try:
                bvs = relaxer.boundary_vertices
                # compute average boundary edge length
                total_len = 0.0
                cnt = 0
                for v in bvs:
                    for nb in mesh.vertex_neighbors(v):
                        if nb in bvs and v < nb:
                            total_len += mesh.edge_vector((v, nb)).length
                            cnt += 1
                avg_len = total_len / cnt if cnt else 0.0
                tol = max(1e-6, avg_len * 0.001)
            except Exception:
                tol = 1e-6

            for corner in relaxer.corners:
                # if corners are passed as vertex indices
                if isinstance(corner, int):
                    if corner in relaxer.boundary_vertices:
                        corner_vertices.add(corner)
                    continue

                # otherwise assume a Point-like object: find the closest boundary vertex
                try:
                    closest_vertex = min(relaxer.boundary_vertices, key=lambda v: mesh.vertex_point(v).distance_to_point(corner))
                    dist = mesh.vertex_point(closest_vertex).distance_to_point(corner)
                    if dist <= tol:
                        corner_vertices.add(closest_vertex)
                    else:
                        # still add closest vertex to be conservative
                        corner_vertices.add(closest_vertex)
                except ValueError:
                    pass

        # helper: find local boundary tangent (unit vector) for a boundary vertex
        def _boundary_tangent_at_vertex(v_idx):
            pts = relaxer.goals.target_boundary.points
            p = mesh.vertex_point(v_idx)
            best_dist = float("inf")
            best_tangent = None
            for i in range(len(pts) - 1):
                a = pts[i]
                b = pts[i + 1]
                ab = Vector.from_start_end(a, b)
                denom = ab.dot(ab)
                if denom == 0:
                    continue
                ap = Vector.from_start_end(a, p)
                t = ap.dot(ab) / denom
                if t < 0:
                    t = 0
                elif t > 1:
                    t = 1
                closest = Point(a.x + ab.x * t, a.y + ab.y * t, a.z + ab.z * t)
                d = p.distance_to_point(closest)
                if d < best_dist:
                    best_dist = d
                    if ab.length > 0:
                        best_tangent = ab.unitized()
            if best_tangent is None:
                return Vector(1, 0, 0)
            return best_tangent

        # apply spring-like forces only to boundary vertices, constrained along boundary tangent
        for v in relaxer.boundary_vertices:
            # do not modify vertices that are designated as corners
            if v in corner_vertices:
                continue
            force = mesh.vertex_attribute(v, "force")
            if force is None:
                force = Vector(0, 0, 0)

            tangent = _boundary_tangent_at_vertex(v)

            for nb in mesh.vertex_neighbors(v):
                if nb not in relaxer.boundary_vertices:
                    continue
                edge_vector = mesh.edge_vector((v, nb))
                edge_length = edge_vector.length
                length_diff = edge_length - target
                edge_dir = edge_vector.unitized()
                spring_force = edge_dir * (self.K * length_diff)
                # project spring force onto the local boundary tangent so the
                # vertex moves along the brep edge rather than off-surface
                projected_mag = spring_force.dot(tangent)
                spring_force_proj = tangent * projected_mag
                force += spring_force_proj

            mesh.vertex_attribute(v, "force", force)

        return mesh


class FixedBoundaryVertices:
    """Modifier that marks all boundary vertices as fixed (prevents movement).

    Attributes:
        type (str): modifier type identifier ("mesh_modifier").
    """

    def __init__(self):
        self.type = "mesh_modifier"

    def apply(self, relaxer, mesh):
        for vertex in relaxer.boundary_vertices:
            mesh.vertex_attribute(vertex, "fixed", True)
        return mesh
