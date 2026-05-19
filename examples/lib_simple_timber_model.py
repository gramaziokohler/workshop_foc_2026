from compas.datastructures import Mesh
from compas.geometry import Line
from compas.geometry import Vector
from compas_timber.connections import JointTopology
from compas_timber.connections import LMiterJoint
from compas_timber.connections import TButtJoint
from compas_timber.connections import XLapJoint
from compas_timber.elements import Beam
from compas_timber.model import TimberModel
from timber_design.workflow import JointRuleSolver
from timber_design.workflow import TopologyRule


class TimberModelCreator:
    """
    A helper class to create a Timber Model from simple lines.

    This class demonstrates the three main steps of computational timber design:
    1. INPUT: Converting abstract centerlines (geometric Lines) into physical objects (Beams).
    2. RULES: Defining how these objects should connect where they intersect (Joints).
    3. SOLVING: Calculating the geometry of those connections (Processing).
    """

    def __init__(self, lines: list[Line], beam_width: float = 0.08, beam_height: float = 0.10):
        self.lines = lines
        self.timber_model = TimberModel()
        self.beam_width = beam_width
        self.beam_height = beam_height
        self._rules = []
        self.tolerance = 0.05  # The maximum distance between lines to consider them as "touching"

    @classmethod
    def from_mesh(cls, mesh: Mesh, beam_width: float = 0.08, beam_height: float = 0.10):
        """
        Helper method to look at a Mesh and treat every edge as a potential beam.
        """
        lines = [mesh.edge_line(edge) for edge in mesh.edges()]
        return cls(lines, beam_width, beam_height)

    def create_timber_model(self, process_joinery: bool = True) -> TimberModel:
        """
        The main recipe for generating the model.
        """
        print(f"--- Starting Timber Model Generation ---")

        # Step 1: Geometry
        self._create_beams()
        print(f"Generated {len(list(self.timber_model.beams))} beams.")

        # Step 2: Definitions
        self._add_topology_rules()

        # Step 3: Calculation
        self._apply_rules(process_joinery)
        print("Model generation complete.")

        return self.timber_model

    def _create_beams(self) -> None:
        """
        Goes through every line in the input and converts it into a Beam object.
        """
        # The 'z_vector' defines the "up" direction for the beam's cross-section.
        # This determines how the rectangle of the beam is rotated around the line.
        cross_section_orientation = Vector(0, 0, 1)

        for line in self.lines:
            beam = Beam.from_centerline(centerline=line, width=self.beam_width, height=self.beam_height, z_vector=cross_section_orientation)
            self.timber_model.add_element(beam)

    def _add_topology_rules(self) -> None:
        """
        Defines the 'logic' of connections.
        Instead of placing every joint manually, we tell the code:
        "Whenever you see two beams meeting in an X-shape, place a Lap Joint."
        """
        # Case 1: CROSSING (X-Shape)
        # When two beams pass each other (typical in grid shells or reciprocal frames).
        self._rules.append(TopologyRule(topology_type=JointTopology.TOPO_X, joint_type=XLapJoint, max_distance=self.tolerance))

        # Case 2: MEETING (T-Shape)
        # When one beam ends perpendicularly against another.
        self._rules.append(TopologyRule(topology_type=JointTopology.TOPO_T, joint_type=TButtJoint, max_distance=self.tolerance))

        # Case 3: CORNER (L-Shape)
        # When two beams meet at their ends (like a picture frame).
        self._rules.append(TopologyRule(topology_type=JointTopology.TOPO_L, joint_type=LMiterJoint, max_distance=self.tolerance))

    def _apply_rules(self, process_joinery: bool) -> None:
        """
        Runs the solver to find intersections and apply the rules we defined above.
        """
        solver = JointRuleSolver(self._rules)

        # Find pairs of beams that match our rules
        found_joints = solver.apply_rules_to_model(self.timber_model)
        print(f"Found and applied {len(found_joints)} joint rules.")

        # Actually cut the geometry (this can be slow for large models)
        if process_joinery:
            print("Processing geometry (cutting joints)...")
            self.timber_model.process_joinery()
