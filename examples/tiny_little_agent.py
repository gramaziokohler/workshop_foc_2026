import time

from antikythera.models import Task
from antikythera.plugin import PLUGIN_MANAGER
from antikythera_agents import agent, tool, Agent
from antikythera_agents.launcher import AgentLauncher


@agent(type="guess")
class GuessAgent(Agent):
    @tool(name="number")
    def guess_number(self, task: Task):
        num = int(input("Enter a number and see if you guess correctly: "))
        return {"number": num}


if __name__ == "__main__":
    PLUGIN_MANAGER._auto_discovery_done = True

    launcher = AgentLauncher("antikythera.ethz.ch", 443, mqtt_transport="websockets", tls=True)
    launcher.start()

    print("Agent running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        launcher.stop()
