import math
import sys
import traceback

import cadwork
import element_controller as ec
import geometry_controller as gc
import utility_controller as uc


def _dist(a, b):
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _avg_point(pts):
    n = len(pts)
    return cadwork.point_3d(sum(p.x for p in pts) / n, sum(p.y for p in pts) / n, sum(p.z for p in pts) / n)


def _compute_bbox_edges(vertices):
    """Return the 12 edge pairs (i, j) of a rectangular box from 8 unordered vertices.

    Two vertices form an edge iff their distance matches one of the 3 shortest
    distinct pairwise distances (width, height, depth).
    """
    n = len(vertices)
    pairs_by_dist = sorted((_dist(vertices[i], vertices[j]), i, j) for i in range(n) for j in range(i + 1, n))

    edges = []
    edge_lengths = set()
    tol = 1e-3
    for d, i, j in pairs_by_dist:
        if all(abs(d - el) > tol for el in edge_lengths):
            if len(edge_lengths) >= 3:
                break
            edge_lengths.add(d)
        if any(abs(d - el) <= tol for el in edge_lengths):
            edges.append((i, j))
    return edges


def _closer_to_axis(p1, p2, axis_origin, axis_dir):
    """Return whichever of p1/p2 is closest to the infinite line through axis_origin along axis_dir."""

    def _rejection_len(pt):
        v = (pt.x - axis_origin.x, pt.y - axis_origin.y, pt.z - axis_origin.z)
        d = (axis_dir.x, axis_dir.y, axis_dir.z)
        t = sum(vi * di for vi, di in zip(v, d)) / sum(di**2 for di in d)
        return math.sqrt(sum((vi - t * di) ** 2 for vi, di in zip(v, d)))

    return p1 if _rejection_len(p1) <= _rejection_len(p2) else p2


def run(subtracted_beam, subtractor_beam):
    """Birdsmouth joint via boolean subtraction."""
    print("\n--- Birdsmouth: boolean subtraction ---")
    try:
        uc.disable_auto_display_refresh()
        sub_p1 = gc.get_p1(subtracted_beam)
        sub_p2 = gc.get_p2(subtracted_beam)
        tor_p1 = gc.get_p1(subtractor_beam)
        tor_xl = gc.get_xl(subtractor_beam)

        closest = _closer_to_axis(sub_p1, sub_p2, tor_p1, tor_xl)

        ext = 10.0
        if closest == sub_p1:
            ec.stretch_start_facet([subtracted_beam], (sub_p1 - sub_p2) / ext)
        else:
            ec.stretch_end_facet([subtracted_beam], (sub_p2 - sub_p1) / ext)

        # Scale bounding box from origin to fully cut through
        scaled_bbox = ec.create_bounding_box_local(subtractor_beam, [subtractor_beam])
        origin = gc.get_p1(subtractor_beam)
        gc.apply_global_scale([scaled_bbox], ext, origin)

        # Ray-cast bbox edges against subtracted beam to find intersections
        vertices = ec.get_bounding_box_vertices_local(subtractor_beam, [subtractor_beam])
        edge_pairs = _compute_bbox_edges(vertices)

        hit_idx = []
        hit_start_end_pts = []
        hit_intersection_pts = []

        for i, (vi, vj) in enumerate(edge_pairs):
            result = ec.cast_ray_and_get_element_intersections([subtracted_beam], vertices[vi], vertices[vj], 0)
            hit_ids = result.get_hit_element_ids()
            if len(hit_ids) == 1:
                hit_idx.append(i)
                hit_start_end_pts.append([vertices[vi], vertices[vj]])
                hit_intersection_pts.append(result.get_hit_vertices_by_element(hit_ids[0]))
            elif len(hit_ids) > 1:
                raise ValueError(f"More than one element hit at edge {i}. Only two-beam joints supported.")

        # Find which beam endpoint is furthest from intersection centroid
        all_pts = [pt for pts in hit_intersection_pts for pt in pts]
        avg = _avg_point(all_pts)
        sub_p1_refreshed = gc.get_p1(subtracted_beam)
        sub_p2_refreshed = gc.get_p2(subtracted_beam)
        furthest = sub_p1_refreshed if _dist(avg, sub_p1_refreshed) > _dist(avg, sub_p2_refreshed) else sub_p2_refreshed

        # Find hit edge whose intersection midpoint is closest to that furthest endpoint
        best = min(range(len(hit_intersection_pts)), key=lambda i: _dist(_avg_point(hit_intersection_pts[i]), furthest))

        print(f"  Intersection avg: ({avg.x:.1f}, {avg.y:.1f}, {avg.z:.1f})")
        print(f"  Furthest beam endpoint: ({furthest.x:.1f}, {furthest.y:.1f}, {furthest.z:.1f})")
        print(f"  Closest hit index: {best} (edge {hit_idx[best]}), dist={_dist(_avg_point(hit_intersection_pts[best]), furthest):.1f}")

        # Move scaled bbox back to align with the correct edge
        edge_vertex = hit_start_end_pts[best][0]
        scaled_vertex = origin + ext * (edge_vertex - origin)
        ec.move_element([scaled_bbox], edge_vertex - scaled_vertex)

        # Boolean subtraction
        result_ids = ec.subtract_elements([scaled_bbox], [subtracted_beam])
        print(f"  Subtraction result: {result_ids}")

        ec.delete_elements([scaled_bbox])

        uc.enable_auto_display_refresh()
        ec.recreate_elements([subtracted_beam])

    except BaseException:
        uc.enable_auto_display_refresh()
        print("  FAILED:")
        traceback.print_exc()

    print("\n--- Done. Check rafter for birdsmouth notch. ---")
    sys.stdout.flush()
