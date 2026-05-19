import base64
import logging
import os
from typing import Any
from typing import Dict

from antikythera.models import Task
from antikythera_agents.base_agent import Agent
from antikythera_agents.decorators import agent
from antikythera_agents.decorators import tool

# Assuming HolzherCncClient is in the same directory or package.
from .cnc_client import HolzherCncClient

LOG = logging.getLogger(__name__)

_HOPS_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "hops_output")


@agent(type="foc")
class CncAgent(Agent):
    """CNC agent for the FOC 2026 workshop.

    Receives a raw HOP file, writes it to disk, and executes the full
    machining sequence: load file → activate table → activate vacuum → start program.
    """

    def __init__(self):
        super().__init__()
        host = os.environ.get("CNC_IP_ADDRESS", "localhost")
        self.zero_point = os.environ.get("CNC_ZERO_POINT", "DV")

        self.client = HolzherCncClient(host=host)
        try:
            self.client.connect()
            LOG.info("CncAgent connected to %s", host)
        except Exception as exc:
            LOG.warning("CncAgent could not connect to %s: %s", host, exc)

    def dispose(self):
        self.client.close()
        super().dispose()

    @tool(name="mill")
    def mill(self, task: Task) -> Dict[str, Any]:
        """Receive a HOP file, write it to disk, and load it on the CNC.
        The human operator takes it from there.

        Inputs
        ------
        hops_file : bytes
            Raw content of the .hop file to load.
        filename : str, optional
            Filename to use when saving to disk. Defaults to "job.hop".
        """
        hops_file: bytes = task.get_input_value("hops_file")
        if hops_file is None:
            raise ValueError("Missing required input 'hops_file'.")

        filename = task.get_input_value("filename") or "job.hop"

        # Write the file locally
        os.makedirs(_HOPS_OUTPUT_DIR, exist_ok=True)
        local_path = os.path.join(_HOPS_OUTPUT_DIR, filename)
        if isinstance(hops_file, str):
            hops_file = base64.b64decode(hops_file)
        with open(local_path, "wb") as fh:
            fh.write(hops_file)
        LOG.info("Wrote HOP file to %s", local_path)

        # Load the file on the CNC
        LOG.info("Loading HOP file on CNC: %s (zero_point=%s)", local_path, self.zero_point)
        self.client.add_hop_file(self.zero_point, local_path, count=1)

        return {"done": True, "local_path": local_path}

    #     slab_name = task.context["slab_name"]
    #     stock_name = task.context["stock_name"]

    #     fabrication_files = [
    #         f"{slab_name}_{stock_name}_bis",
    #         f"{slab_name}_{stock_name}",
    #     ]

    #     hop_files = [f"{f}.hop" for f in fabrication_files]
    #     jlx_files = [f"{f}.jlx" for f in fabrication_files]

    #     hop_path = os.path.join(fab_dirpath, slab_name, "hops", "merged")

    #     abs_hop_paths = [os.path.join(hop_path, fn) for fn in hop_files]
    #     abs_jlx_paths = [os.path.join(hop_path, fn) for fn in jlx_files]

    #     LOG.debug(f"Looking for HOPS files in {abs_hop_paths}")

    #     found_indices = [i for i, path in enumerate(abs_hop_paths) if os.path.exists(path)]

    #     if not found_indices:
    #         raise FileNotFoundError(f"Could not find HOPS file for slab '{slab_name}' and stock '{stock_name}' in '{hop_path}'")

    #     first_hops_file, second_hops_file = None, None
    #     first_jlx_file, second_jlx_file = None, None

    #     # Scenario: Both exist ([0, 1]) -> first=0 (bis file), second=1
    #     if len(found_indices) == 2:
    #         first_hops_file = abs_hop_paths[0]
    #         second_hops_file = abs_hop_paths[1]

    #         if os.path.exists(abs_jlx_paths[0]):
    #             first_jlx_file = abs_jlx_paths[0]
    #         if os.path.exists(abs_jlx_paths[1]):
    #             second_jlx_file = abs_jlx_paths[1]

    #     # Scenario: Only one exists -> first=found_index
    #     elif len(found_indices) == 1:
    #         idx = found_indices[0]
    #         first_hops_file = abs_hop_paths[idx]
    #         if os.path.exists(abs_jlx_paths[idx]):
    #             first_jlx_file = abs_jlx_paths[idx]
    #     print(f"Found fabrication files for slab '{slab_name}' and stock '{stock_name}':")
    #     print(f"  HOPS: {first_hops_file}, {second_hops_file}")
    #     print(f"  JLX: {first_jlx_file}, {second_jlx_file}")
    #     return {
    #         "first_hops_file": first_hops_file,
    #         "second_hops_file": second_hops_file,
    #         "first_jlx_file": first_jlx_file,
    #         "second_jlx_file": second_jlx_file,
    #     }

    # @tool(name="get_hop_file")
    # def get_hop_file(self, task: Task) -> Dict[str, Any]:
    #     """
    #     Gets the hop file currently assigned to a zero point.

    #     Required inputs in task:
    #         zero_point (str): Identifier for the zero point (e.g., "DV", "AV", "DH", "AH").
    #     """
    #     zero_point = task.get_input_value("zero_point") or task.get_param_value("zero_point")
    #     if not zero_point:
    #         raise ValueError("Input or Param 'zero_point' is required.")

    #     hop_file = self.client.get_hop_file(zero_point)
    #     return {"hop_file": hop_file}

    # @tool(name="remove_hop_file")
    # def remove_hop_file(self, task: Task) -> Dict[str, Any]:
    #     """
    #     Removes the hop file from the given zero point.

    #     Required inputs in task:
    #         zero_point (str): Identifier for the zero point (e.g., "DV", "AV", "DH", "AH").
    #     """
    #     zero_point = task.get_input_value("zero_point") or task.get_param_value("zero_point")
    #     if not zero_point:
    #         raise ValueError("Input or Param 'zero_point' is required.")

    #     response = self.client.remove_hop_file(zero_point)
    #     return {"response": response, "done": True}

    # @tool(name="add_hop_file")
    # def add_hop_file(self, task: Task) -> Dict[str, Any]:
    #     """
    #     Adds a hop file to a specific zero point.

    #     Required inputs in task:
    #         zero_point (str): Identifier for the zero point (e.g., "DV", "AV", "DH", "AH").
    #         file_path (str): Path to the .hop file.

    #     Optional inputs:
    #         count (int): Number of parts to produce. Defaults to 1.
    #     """
    #     zero_point = task.get_input_value("zero_point") or task.get_param_value("zero_point")
    #     file_path = task.get_input_value("file_path") or task.get_param_value("file_path")

    #     if not zero_point:
    #         raise ValueError("Input or Param 'zero_point' is required.")
    #     if not file_path:
    #         raise ValueError("Input or Param 'file_path' is required.")

    #     # Transform path for CNC context
    #     # The files need to be copied/made available to the CNC's shared folder
    #     # and we need to provide the path as seen by the CNC local filesystem.
    #     filename = os.path.basename(file_path)

    #     cnc_file_path = f"{self.remote_dir}{filename}"

    #     LOG.info(f"Adding hop file to CNC: {cnc_file_path} (derived from {file_path})")

    #     response = self.client.add_hop_file(zero_point, cnc_file_path, count=1)
    #     return {"response": response, "done": True}

    # @tool(name="load_machineload")
    # def load_machineload(self, task: Task) -> Dict[str, Any]:
    #     """
    #     Loads a machine load file (.jlx) on the CNC.

    #     Required inputs in task:
    #         file_path (str): Path to the .jlx file.
    #     """
    #     file_path = task.get_input_value("file_path") or task.get_param_value("file_path")
    #     if not file_path:
    #         raise ValueError("Input or Param 'file_path' is required.")

    #     # Transform path for CNC context
    #     filename = os.path.basename(file_path)
    #     cnc_file_path = f"{self.remote_dir}{filename}"

    #     LOG.info(f"Loading machine load file on CNC: {cnc_file_path} (derived from {file_path})")

    #     response = self.client.load_machineload(cnc_file_path)
    #     return {"response": response, "done": True}

    # @tool(name="activate_table")
    # def activate_table(self, task: Task) -> Dict[str, Any]:
    #     """
    #     Activates or deactivates a table based on the zero point.

    #     Required inputs in task:
    #         zero_point (str): Identifier for the zero point (e.g., "DV", "AV", "DH", "AH").
    #         active (bool): True to activate, False to deactivate.
    #     """
    #     zero_point = task.get_input_value("zero_point") or task.get_param_value("zero_point")

    #     active = task.get_input_value("active")
    #     if active is None:
    #         active = task.get_param_value("active")

    #     if not zero_point:
    #         raise ValueError("Input or Param 'zero_point' is required.")
    #     if active is None:
    #         raise ValueError("Input or Param 'active' is required.")

    #     table_id = zero_point[0].upper()
    #     if table_id not in ["A", "D"]:
    #         raise ValueError(f"Could not derive table ID (A/D) from zero_point '{zero_point}'. Expected start with 'A' or 'D'.")
    #     try:
    #         self.client.activate_table(table_id, active)
    #         return {"message": f"Table {table_id} activation set to {active}.", "done": True}
    #     except Exception as e:
    #         return {"message": f"Table {table_id} activation set to {active} but returned exception {e}.", "done": True}

    # @tool(name="activate_vacuum")
    # def activate_vacuum(self, task: Task) -> Dict[str, Any]:
    #     """
    #     Activates or deactivates a table based on the zero point.

    #     Required inputs in task:
    #         zero_point (str): Identifier for the zero point (e.g., "DV", "AV", "DH", "AH").
    #         active (bool): True to activate, False to deactivate.
    #     """
    #     zero_point = task.get_input_value("zero_point") or task.get_param_value("zero_point")

    #     active = task.get_input_value("active")
    #     if active is None:
    #         active = task.get_param_value("active")

    #     if not zero_point:
    #         raise ValueError("Input or Param 'zero_point' is required.")
    #     if active is None:
    #         raise ValueError("Input or Param 'active' is required.")

    #     table_id = zero_point[0].upper()
    #     if table_id not in ["A", "D"]:
    #         raise ValueError(f"Could not derive table ID (A/D) from zero_point '{zero_point}'. Expected start with 'A' or 'D'.")

    #     try:
    #         self.client.set_vacuum(table_id, active)
    #     except:
    #         pass
    #     return {"message": f"Table {table_id} vacuum set to {active}.", "done": True}

    # @tool(name="start_program")
    # def start_program(self, task: Task) -> Dict[str, Any]:
    #     """
    #     Start the program based on the zero point.

    #     Required inputs in task:
    #         zero_point (str): Identifier for the zero point (e.g., "DV", "AV", "DH", "AH").
    #     """
    #     zero_point = task.get_input_value("zero_point") or task.get_param_value("zero_point")

    #     if not zero_point:
    #         raise ValueError("Input or Param 'zero_point' is required.")

    #     table_id = zero_point[0].upper()
    #     if table_id not in ["A", "D"]:
    #         raise ValueError(f"Could not derive table ID (A/D) from zero_point '{zero_point}'. Expected start with 'A' or 'D'.")

    #     self.client.start_program(table_id)
    #     return {"message": f"Started program in table {table_id}.", "done": True}
