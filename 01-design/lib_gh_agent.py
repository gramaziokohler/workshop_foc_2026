"""Shared utilities for pipeline GH agent components.

Provides the GrasshopperAgent duck-typed class (satisfies the AgentLauncher
contract) and helper functions for starting/stopping the agent on a
BackgroundWorker thread.  Each pipeline component imports from here instead
of duplicating the boilerplate.
"""

import threading
import logging

# logging.basicConfig(
#     level=logging.DEBUG,
#     format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
#     filename="/Users/chenkasirer/repos/GKR/workshop_caadria_2026/grasshopper/pipeline/grasshopper_agent.log",
#     filemode="a",
# )

from antikythera_agents.launcher import AgentLauncher

LOG = logging.getLogger("GrasshopperAgent")


class GrasshopperAgentLauncher(AgentLauncher):
    def _initialize_agents(self):
        pass

    def on_task_start(self, message):
        try:
            super().on_task_start(message)
        except Exception:
            LOG.exception("on_task_start failed (message may be incompatible with this antikythera version)")


class GrasshopperPendingTask:
    def __init__(self, task_data, event):
        self.task_data = task_data
        self.event = event
        self.task_outputs = {}


class GrasshopperAgent:
    """Duck-typed Agent that satisfies the AgentLauncher contract.

    ``execute_task()`` is called on a launcher worker thread.  It stores the
    task on the BackgroundWorker and calls ``worker.update_result()`` to
    trigger a GH solution re-expiry via compas_ghpython's timer mechanism.
    It then blocks until RunScript fires the ``result_event`` via the submit
    pulse.
    """

    def __init__(self, task_type, worker, logger_name="GrasshopperAgent"):
        self._task_type = task_type
        self._worker = worker
        self._initialized = True
        # self.logger = logging.getLogger(logger_name)

    def can_claim_task(self, task):
        return task.type == self._task_type  # and not getattr(self._worker, "pending_task", None)

    def execute_task(self, task, context=None):
        LOG.debug(f"started executing task: {task.id} of type {task.type}")
        event = threading.Event()
        self._worker.pending_task = GrasshopperPendingTask(
            task_data={
                "id": task.id,
                "type": task.type,
                "inputs": {i.name: i.value for i in (task.inputs or [])},
                "params": {p.name: p.value for p in (task.params or [])},
                "output_keys": [o.name for o in (task.outputs or [])],
            },
            event=event,
        )

        LOG.debug("pending task set on worker, calling update_result to trigger GH timer")
        self._worker.update_result(self._worker.pending_task)

        try:
            LOG.debug("waiting for task result event to be set")
            while not event.wait(timeout=0.5):
                if context and context.is_cancelled:
                    raise RuntimeError("Task cancelled while waiting for result")
                if self._worker._is_cancelled:
                    raise RuntimeError("Agent stopped while waiting for result")
        finally:
            LOG.debug("finished executing task")
            outputs = self._worker.pending_task.task_outputs
            self._worker.pending_task = None
            return outputs

    def dispose(self):
        self._initialized = False

    def list_tools(self):
        return {"execute": "execute_task"}


def run_agent(
    worker,
    task_type,
    broker_host,
    broker_port,
    logger_name="GrasshopperAgent",
):
    """Start an AgentLauncher on a BackgroundWorker thread.

    Call this as the ``work_function`` of a ``BackgroundWorker``.
    """
    worker.pending_task = None
    worker.result_event = None
    worker.task_result = None

    gh_agent = GrasshopperAgent(task_type, worker, logger_name=logger_name)
    launcher = GrasshopperAgentLauncher(broker_host, broker_port)
    launcher.agents[task_type.split(".", 1)[0]] = gh_agent
    worker.launcher = launcher
    launcher.start()
    worker.display_message("listening")


def stop_agent(worker):
    """Dispose callback for BackgroundWorker.stop_instance_by_component."""
    LOG.debug("stop_agent called")
    launcher = worker.launcher
    if launcher:
        try:
            launcher.stop()
        except Exception:
            pass
    event = worker.result_event
    if event:
        event.set()
    worker.display_message("stopped")
