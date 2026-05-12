# requires: compas_timber==2.1.1-rc0
import logging

import cadwork
from attribute_controller import is_beam
from attribute_controller import set_name
from compas.data import json_dump
from compas.data import json_load
from compas.geometry import Line
from compas_cadwork.conversions import point_to_cadwork
from compas_cadwork.conversions import vector_to_cadwork
from compas_cadwork.datamodel import Element
from compas_cadwork.utilities import get_all_element_ids
from compas_cadwork.utilities import remove_elements
from compas_cadwork.utilities.events import ElementDelta
from compas_timber.connections import LButtJoint
from compas_timber.connections import LMiterJoint
from compas_timber.connections import TBirdsmouthJoint
from compas_timber.connections import TButtJoint
from compas_timber.connections import XLapJoint
from compas_timber.elements import Beam
from compas_timber.elements import CutFeature
from compas_timber.elements import DrillFeature
from cwmath.cwplane3d import CwPlane3d
from cwmath.cwvector3d import CwVector3d
from element_controller import create_rectangular_beam_vectors
from element_controller import create_rectangular_panel_vectors
from element_controller import cut_corner_lap
from element_controller import cut_cross_lap
from element_controller import cut_element_with_plane
from element_controller import cut_elements_with_miter
from element_controller import cut_t_lap
from element_controller import get_active_identifiable_element_ids

from birdsmouth_joint import run as run_birdsmouth
from cw_to_ct import sync_user_attributes

LOG = logging.getLogger(__name__)


def _apply_x_lap(beam_a, beam_b, scale, **kwargs):
    id_a, id_b = beam_a.attributes["cadwork"]["id"], beam_b.attributes["cadwork"]["id"]
    cut_cross_lap([id_a, id_b], beam_a.width * scale / 2.0, 0, 0, 0, 0, 0)


def _apply_l_miter(beam_a, beam_b, scale, **kwargs):
    id_a, id_b = beam_a.attributes["cadwork"]["id"], beam_b.attributes["cadwork"]["id"]
    cut_elements_with_miter(id_a, id_b)


def _extend_l_butt(beam_a, beam_b, scale, **kwargs):
    id_a, id_b = beam_a.attributes["cadwork"]["id"], beam_b.attributes["cadwork"]["id"]
    cut_corner_lap([id_a, id_b], 0, 0, 0, 0, 0, 0, 0)


def _extend_t_butt(beam_a, beam_b, scale, **kwargs):
    id_a, id_b = beam_a.attributes["cadwork"]["id"], beam_b.attributes["cadwork"]["id"]
    cut_t_lap([id_a, id_b], 0, 0, 0, 0, 0, 0, 0)


def _apply_t_birdsmouth(beam_a, beam_b, scale, **kwargs):
    id_a, id_b = beam_a.attributes["cadwork"]["id"], beam_b.attributes["cadwork"]["id"]
    run_birdsmouth(id_a, id_b)


def apply_cuts(beam, scale):
    for f in filter(lambda x: isinstance(x, CutFeature), beam.features):
        print(f"applying cut owner: {f.owner}")
        if f.owner and f.owner == LMiterJoint.__name__:
            continue
        plane_normal = CwVector3d(*f.cutting_plane.normal)
        scaled_point = [c * scale for c in f.cutting_plane.point]
        plane = CwPlane3d(CwVector3d(*scaled_point), plane_normal)
        distance = plane.distance_to_point(CwVector3d(0.0, 0.0, 0.0))
        if plane_normal.z < 0 or plane_normal.x < 0 or plane_normal.y < 0:
            distance = -distance  # geil!
        cut_element_with_plane(beam.attributes["cadwork"]["id"], cadwork.point_3d(*plane_normal), distance)


def _point_from_corner_to_face_center(frame, ysize, zsize):
    origin = frame.point
    yaxis = frame.yaxis * ysize * 0.5
    zaxis = frame.normal * zsize * 0.5
    return origin + yaxis + zaxis


class ImportController:
    def __init__(self, scale=1000.0):
        self.model = None
        self.scale = scale
        self._delta = ElementDelta()

    def load_model_from_file(self, file_path):
        model = json_load(file_path)
        self.load_model(model)
        return model

    def load_model(self, model):
        LOG.debug(f"loading model: {model}")
        LOG.debug("creating beams..")
        self.create_beams(model, self.scale)
        LOG.debug("creating connections..")
        self.create_connections(model, self.scale)
        self.model = model
        self._delta.reset()
        LOG.debug("model loaded")
        return model

    def clear_model(self):
        remove_elements(list(get_all_element_ids()))

    def export_model_to_file(self, file_path):
        for index, wall in enumerate(self.model.walls):
            wall.group = self.model.add_group(name=f"wall0{index}", element=wall)
        new_elements = [Element.from_id(e) for e in get_active_identifiable_element_ids()]
        if new_elements:
            self.handle_new_elements(new_elements)
        sync_user_attributes(self.model)
        json_dump(self.model, file_path)

    def handle_new_elements(self, new_elements):
        print(f"new elements:{new_elements}")
        for element in new_elements:
            if element.is_drilling:
                drilled_elements = element.get_elements_in_contact()
                self.add_drilling(element, drilled_elements)
            elif is_beam(element.id):
                self.add_beam(element)

    def add_beam(self, e_beam):
        beam = Beam(e_beam.frame, e_beam.length, e_beam.width, e_beam.height)
        # HACK: mega hack, figure out how to get available groups, and how to associate beams with HK in cadwork
        group = self.model.walls[1].group
        self.model.add_element(beam, parent=group)
        self.model._beams.append(beam)

    def add_drilling(self, e_drill, e_beams):
        for e_b in e_beams:
            beam = self.model.element_map[e_b.id]
            drill_line = Line.from_point_direction_length(e_drill.frame.point, e_drill.frame.xaxis, e_drill.length)
            beam.add_features(
                [
                    DrillFeature(
                        drill_line,
                        diameter=e_drill.width,
                        length=e_drill.length,
                        is_joinery=False,
                    )
                ]
            )

    @staticmethod
    def create_beams(model, scale):
        model.element_map = {}
        for beam in model.beams:
            origin = cadwork.point_3d(*[c * scale for c in beam.frame.point])
            xaxis = cadwork.point_3d(*beam.frame.xaxis)
            zaxis = cadwork.point_3d(*beam.frame.normal)
            element_id = create_rectangular_beam_vectors(
                beam.width * scale,
                beam.height * scale,
                beam.length * scale,
                origin,
                xaxis,
                zaxis,
            )
            beam.attributes.setdefault("cadwork", {})["id"] = element_id
            beam.attributes["name"] = f"beam_{beam.graphnode}"
            model.element_map[element_id] = beam
            set_name([element_id], beam.attributes["name"])

    @staticmethod
    def create_walls(model, scale):
        for wall in model.walls:
            origin = _point_from_corner_to_face_center(wall.frame, wall.width, wall.height)
            origin = origin * scale
            origin = point_to_cadwork(origin)
            xaxis = vector_to_cadwork(wall.frame.xaxis)
            zaxis = vector_to_cadwork(wall.frame.normal)
            element_id = create_rectangular_panel_vectors(
                wall.width * scale,
                wall.height * scale,
                wall.length * scale,
                origin,
                xaxis,
                zaxis,
            )
            set_name([element_id], wall.name)

    @staticmethod
    def create_connections(model, scale):
        joint_map = {
            LMiterJoint: _apply_l_miter,
            TButtJoint: _extend_t_butt,
            LButtJoint: _extend_l_butt,
            XLapJoint: _apply_x_lap,
            TBirdsmouthJoint: _apply_t_birdsmouth,
        }
        for joint in model.joints:
            beam_a, beam_b = joint.elements
            applier = joint_map.get(type(joint))
            if applier:
                applier(beam_a, beam_b, scale, joint=joint)
            else:
                print(f"no applier for {type(joint)}. skipping...")
