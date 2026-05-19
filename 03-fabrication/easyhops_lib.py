import logging
import os

from easyhops.hop_job import HOPSJob
from easyhops.strategies import JackRafterCutStrategies
from easyhops.strategies import LapStrategies
from easyhops.tool_library import CastorD61
from easyhops.tool_library import SaegeD350
from easyhops.utility_commands import MachineStop

LOG = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "hops_output")


class EasyHops:
    def __init__(self, output_dir: str = DEFAULT_OUTPUT_DIR):
        self.output_dir = output_dir

    def _element_to_job(self, element) -> HOPSJob:
        """Build a HOPSJob for *element* with explicit control over dispatch."""
        job = HOPSJob.from_element(element)
        rsi = job.ref_side_index
        opp_rsi = (rsi + 2) % 4

        pre_flip = []
        post_flip = []

        for processing in element.features:
            name = processing.PROCESSING_NAME

            if name == "Lap":
                machinings = LapStrategies.milling(processing, tool=CastorD61())
                if processing.ref_side_index == opp_rsi:
                    pre_flip.extend(machinings)
                elif processing.ref_side_index == rsi:
                    post_flip.extend(machinings)
                else:
                    raise ValueError(f"Unexpected ref_side_index {processing.ref_side_index} for Lap")

            elif name == "JackRafterCut":
                assert processing.ref_side_index in (rsi, opp_rsi), f"Unexpected ref_side_index {processing.ref_side_index} for JackRafterCut"
                post_flip.extend(JackRafterCutStrategies.sawing(processing, machine_ref_side_index=rsi, tool=SaegeD350()))

        post_flip.sort(key=lambda m: {"MILLING": 0, "SAWING": 1}.get(getattr(m, "OPERATION_TYPE", ""), 2))

        if pre_flip:
            job.add(pre_flip)
            job.add(MachineStop("flip beam 180deg"))
        job.add(post_flip)

        return job

    def generate_hops(self, elements) -> dict:
        """Convert *elements* to .hop files and return ``{element.guid: filepath}``.

        Elements that fail conversion are skipped and logged as warnings.
        """
        os.makedirs(self.output_dir, exist_ok=True)
        result = {}
        for element in elements:
            try:
                job = self._element_to_job(element)
                filepath = os.path.join(self.output_dir, f"{element.guid}.hop")
                job.to_hop_file(filepath)
                result[element.guid] = filepath
                LOG.info("Wrote %s", filepath)
            except Exception as exc:
                LOG.warning("Failed to generate HOP for element %s: %s", element.guid, exc)
        return result

    def start_sim(self, hops_filepath: str, element) -> None:
        """Start a simulation run for *element* using the given .hop file."""
        raise NotImplementedError

    def execute(self, hops_filepath: str, element) -> None:
        """Send *hops_filepath* to the machine and execute machining for *element*."""
        raise NotImplementedError
