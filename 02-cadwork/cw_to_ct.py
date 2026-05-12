# requires: compas_timber==2.1.1-rc0
import attribute_controller as ac
from compas.data import json_dump
from compas.data import json_load
from compas.geometry import Frame
from compas.geometry import Line
from compas.geometry import Point
from compas.geometry import Transformation
from compas_cadwork.datamodel import Element
from compas_cadwork.utilities import get_all_element_ids
from compas_timber.elements import Beam
from compas_timber.elements import DrillFeature
from element_controller import get_active_identifiable_element_ids

# ---------------------------------------------------------------------------
# User attributes
# ---------------------------------------------------------------------------
"""Load a COMPAS Timber model, sync all cadwork data, and re-export."""


def _get_defined_user_attribute_names():
    """Return ``{index: name}`` for all non-empty user attributes 1-99."""
    attr_names = {}
    for i in range(1, 100):
        name = ac.get_user_attribute_name(i)
        if name:
            attr_names[i] = name
    return attr_names


def read_user_attributes(element_id, attr_names):
    """Read non-empty user attributes for a cadwork element.

    Parameters
    ----------
    element_id : int
        Cadwork element ID.
    attr_names : dict[int, str]
        Pre-cached mapping ``{index: name}`` of defined user attributes.

    Returns
    -------
    dict
        Index-keyed dict: ``{"1": {"name": "AttrName", "value": "val"}, ...}``
    """
    result = {}
    for i, name in attr_names.items():
        value = ac.get_user_attribute(element_id, i)
        if value:
            result[str(i)] = {"name": name, "value": value}
    return result


def sync_user_attributes(model):
    """Sync user attributes from cadwork onto all beams in ``model.element_map``."""
    if not hasattr(model, "element_map"):
        return

    attr_names = _get_defined_user_attribute_names()
    if not attr_names:
        return

    for cadwork_id, beam in model.element_map.items():
        ua = read_user_attributes(cadwork_id, attr_names)
        if ua:
            beam.attributes.setdefault("cadwork", {})["user_attributes"] = ua


# ---------------------------------------------------------------------------
# Export controller (mirrors ct_to_cw.Controller pattern)
# ---------------------------------------------------------------------------


def _scale_frame(frame, scale):
    """Scale a cadwork frame origin from mm to COMPAS m, keeping axes intact."""
    origin = Point(frame.point.x / scale, frame.point.y / scale, frame.point.z / scale)
    return Frame(origin, frame.xaxis, frame.yaxis)


class ExportController:
    """Reads cadwork element data and syncs it back onto a COMPAS Timber model.

    Mirrors the ``Controller`` class in ``ct_to_cw.py`` — uses
    ``compas_cadwork.datamodel.Element`` for all cadwork queries and handles
    beams, walls/groups, and drillings.

    Parameters
    ----------
    scale : float
        Conversion factor cadwork (mm) -> COMPAS (m).  Default ``1000.0``.
    """

    def __init__(self, scale=1000.0):
        self.model = None
        self.scale = scale

    def load_model_from_file(self, file_path):
        """Load a COMPAS Timber model from JSON and rebuild the element map."""
        self.model = json_load(file_path)
        self._rebuild_element_map()
        return self.model

    def load_model(self, model):
        """Load a COMPAS Timber model and rebuild the element map."""
        self.model = model
        self._rebuild_element_map()

    def export_model_to_file(self, file_path):
        """Sync all cadwork data back into the model and export to JSON."""
        self._sync_walls_and_groups()
        self._sync_geometry()
        self._handle_new_elements()
        sync_user_attributes(self.model)
        json_dump(self.model, file_path, pretty=True)
        print(f"  Exported to {file_path}")

    def export_model(self):
        """Sync all cadwork data back into the model and return it."""
        self._sync_walls_and_groups()
        self._sync_geometry()
        self._handle_new_elements()
        sync_user_attributes(self.model)
        return self.model

    # -- element map ----------------------------------------------------------

    def _rebuild_element_map(self):
        """Re-establish ``model.element_map`` by matching cadwork element names.

        The forward import (``ct_to_cw.py``) names each cadwork element
        ``beam_{graphnode}``.  This function wraps every cadwork element with
        ``Element`` and rebuilds the bidirectional mapping.
        """
        name_to_element = {}
        for eid in get_all_element_ids():
            element = Element(eid)
            name_to_element[element.name] = element

        self.model.element_map = {}
        for beam in self.model.beams:
            expected_name = f"beam_{beam.graphnode}"
            element = name_to_element.get(expected_name)
            if element is None:
                print(f"  WARNING: no cadwork element found for '{expected_name}', skipping")
                continue
            beam.attributes.setdefault("cadwork", {})["id"] = element.id
            beam.attributes["name"] = expected_name
            self.model.element_map[element.id] = beam

        print(f"  Mapped {len(self.model.element_map)}/{len(list(self.model.beams))} beams")

    # -- geometry sync --------------------------------------------------------

    def _sync_geometry(self):
        """Update beam geometry from cadwork back onto the COMPAS Timber model.

        Reads current frame, width, height, and length via ``Element``
        properties and converts from mm to m.
        """
        if not self.model.element_map:
            return

        for cadwork_id, beam in self.model.element_map.items():
            element = Element(cadwork_id)
            frame = _scale_frame(element.frame, self.scale)
            beam.transformation = Transformation.from_frame(frame)
            beam.width = element.width / self.scale
            beam.height = element.height / self.scale
            beam.length = element.length / self.scale
            beam.reset_computed_properties()

    # -- walls / groups -------------------------------------------------------

    def _sync_walls_and_groups(self):
        """Associate walls with groups, mirroring ct_to_cw pattern."""
        if not hasattr(self.model, "walls"):
            return
        for index, wall in enumerate(self.model.walls):
            wall.group = self.model.add_group(name=f"wall0{index}", element=wall)

    # -- new elements ---------------------------------------------------------

    def _handle_new_elements(self):
        """Detect cadwork elements not yet in element_map and add them.

        Uses ``Element`` to distinguish beams from drillings, mirroring
        ``ct_to_cw.Controller.handle_new_elements()``.
        """
        known_ids = set(self.model.element_map.keys())
        new_elements = [Element(eid) for eid in get_active_identifiable_element_ids() if eid not in known_ids]
        if not new_elements:
            return

        beam_count = 0
        drill_count = 0
        for element in new_elements:
            if element.is_drilling:
                self._add_drilling(element)
                drill_count += 1
            elif element.is_beam:
                self._add_beam(element)
                beam_count += 1

        print(f"  Added {beam_count} new beam(s) and {drill_count} drilling(s) from cadwork")

    def _add_beam(self, element):
        """Create a COMPAS Timber ``Beam`` from a cadwork ``Element``."""
        frame = _scale_frame(element.frame, self.scale)
        beam = Beam(
            frame,
            element.length / self.scale,
            element.width / self.scale,
            element.height / self.scale,
        )
        beam.attributes.setdefault("cadwork", {})["id"] = element.id
        beam.attributes["name"] = element.name
        self.model.add_element(beam)
        self.model.element_map[element.id] = beam

    def _add_drilling(self, e_drill):
        """Create ``DrillFeature`` objects on beams in contact with the drilling."""
        drilled_elements = e_drill.get_elements_in_contact()
        for e_b in drilled_elements:
            beam = self.model.element_map.get(e_b.id)
            if beam is None:
                print(f"  WARNING: drilled element {e_b.id} not in element_map, skipping")
                continue
            drill_line = Line.from_point_direction_length(
                Point(
                    e_drill.frame.point.x / self.scale,
                    e_drill.frame.point.y / self.scale,
                    e_drill.frame.point.z / self.scale,
                ),
                e_drill.frame.xaxis,
                e_drill.length / self.scale,
            )
            beam.add_features(
                [
                    DrillFeature(
                        drill_line,
                        diameter=e_drill.width / self.scale,
                        length=e_drill.length / self.scale,
                        is_joinery=False,
                    )
                ]
            )
