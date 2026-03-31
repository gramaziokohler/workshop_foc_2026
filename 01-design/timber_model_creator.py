from compas.datastructures import Mesh
from compas_timber.connections import JointTopology
from compas_timber.connections import LMiterJoint
from compas_timber.connections import TBirdsmouthJoint
from compas_timber.connections import TButtJoint
from compas_timber.connections import TStepJoint
from compas_timber.connections import XLapJoint
from compas_timber.elements import Beam
from compas_timber.model import TimberModel
from rf_system import RFSystem
from timber_design.workflow import CategoryRule
from timber_design.workflow import JointRuleSolver
from timber_design.workflow import TopologyRule


class TimberModelCreator:
    """
    A helper class to create a Timber Model from an instance of `RFSystem` class.

    This class demonstrates the three main steps of computational timber design:
    1. INPUT: Converting abstract centerlines (geometric lines extracted from an RFSystem's mesh) into physical objects (Beams).
    2. RULES: Defining how these objects should connect where they intersect (Joints).
    3. SOLVING: Calculating the geometry of those connections (Processing).
    """

    def __init__(self, rf_system: RFSystem, beam_width: float = 0.08, beam_height: float = 0.10, tolerance: float = 0.02):
        self.rf_system = rf_system
        self.timber_model = TimberModel()
        self.beam_width = beam_width
        self.beam_height = beam_height
        self.joining_errors = []

        # Rule solver settings.
        self.tolerance = tolerance  # The maximum distance between lines to consider them as "touching"
        self._rules = []

    def create_timber_model(self, process_joinery: bool = True) -> TimberModel:
        """
        The main recipe for generating the model.
        """
        print(f"--- Starting Timber Model Generation ---")

        # Step 1: Geometry
        self._create_beams()
        print(f"Generated {len(list(self.timber_model.beams))} beams.")

        # Step 2: Definitions
        self._rules = []  # Reset rules
        self._add_rules()

        # Step 3: Calculation
        self._apply_rules(process_joinery)
        print("Model generation complete.")

        return self.timber_model

    # --------------------------------------------------------------------------
    # Beam creation
    # --------------------------------------------------------------------------

    def _create_beams(self) -> None:
        """
        Convert every RF edge into a `Beam` and store the beam back on the edge.
        """
        mesh: Mesh = self.rf_system.mesh

        for edge in mesh.edges():
            centerline = mesh.edge_attribute(edge, "centerline")
            normal = mesh.edge_attribute(edge, "normal")

            beam = Beam.from_centerline(centerline, width=self.beam_width, height=self.beam_height, z_vector=normal)
            beam.attributes["category"] = self._edge_category(edge)
            self.timber_model.add_element(beam)

            # Keep track of edge-to-beam relationship
            mesh.edge_attribute(edge, "beam", beam)

    def _edge_category(self, edge) -> str:
        if self.rf_system.mesh.is_edge_on_boundary(edge):
            return "boundary"

        return "interior"

    # --------------------------------------------------------------------------
    # Rule definition
    # --------------------------------------------------------------------------

    def _add_rules(self) -> None:
        """
        Defines the 'logic' of connections.
        """
        # Case 1: TWO INTERIOR BEAMS (CATEGORY)
        # When two interior beams meet, assign a lap joint
        self._rules.append(CategoryRule(XLapJoint, "interior", "interior", max_distance=self.tolerance))

        # Case 2: AN INTERIOR BEAM MEETS A BOUNDARY BEAM (CATEGORY)
        # When an interior beam meets a boundary beam, assign a butt or step joint
        self._rules.append(CategoryRule(TStepJoint, "interior", "boundary", max_distance=self.tolerance))

        # Case 3: TWO BOUNDARY BEAMS (CATEGORY)
        # When two boundary beams meet (usually at the corners), assign a miter joint
        self._rules.append(CategoryRule(LMiterJoint, "boundary", "boundary", max_distance=self.tolerance))

        # Case 4: MEETING (T-Shape)
        # The default rule for topological T-joints (one beam ends against the face of another)
        self._rules.append(TopologyRule(topology_type=JointTopology.TOPO_T, joint_type=TBirdsmouthJoint, max_distance=self.tolerance))

    def _apply_rules(self, process_joinery: bool) -> None:
        """
        Runs the solver to find intersections and apply the rules we defined above.
        """
        self.joining_errors = []
        solver = JointRuleSolver(self._rules)

        # Find pairs of beams that match our rules
        self.joining_errors, unjoined_clusters = solver.apply_rules_to_model(self.timber_model)

        print(f"Found {len(self.joining_errors)} joining errors and {len(unjoined_clusters)} unjoined clusters, using {len(self._rules)} rules.")
        if self.joining_errors:
            print("Joining errors:")
            for error in self.joining_errors:
                print(f" - {error}")

        # Actually cut the geometry (this can be slow for large models)
        if process_joinery:
            print("Processing geometry (cutting joints)...")
            self.timber_model.process_joinery()
