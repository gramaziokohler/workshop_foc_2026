import logging
import socket
import time
from enum import IntFlag
from typing import Optional


class Colors:
    SENT = "\033[96m"  # Cyan
    RECV = "\033[92m"  # Green
    RESET = "\033[0m"


LOG = logging.getLogger(__name__)


class CncTableStatus(IntFlag):
    """Status bits for the CNC table (Legacy/Specific Command)."""

    NONE = 0
    READY = 1 << 0  # Bit 0: Table ready
    RUNNING = 1 << 1  # Bit 1: Table running
    FINISHED = 1 << 2  # Bit 2: Table finished
    ABORTED = 1 << 3  # Bit 3: Table aborted
    REQUESTED = 1 << 4  # Bit 4: Table requested
    ACTIVE = 1 << 6  # Bit 6: Table active
    START_SAVED = 1 << 7  # Bit 7: Table start saved


class CncMachineStatus(IntFlag):
    """Byte 1: General Machine Status."""

    NONE = 0
    NO_EMERGENCY_STOP = 1 << 0  # Bit 0: Machine has no emergency stop active
    SWITCHED_ON = 1 << 1  # Bit 1: Machine switched on
    READY = 1 << 2  # Bit 2: Machine is ready to work
    ERROR = 1 << 3  # Bit 3: Machine is in error
    AUTO_MODE = 1 << 5  # Bit 5: Machine is in automatic program mode
    MANUAL_MODE = 1 << 6  # Bit 6: Machine is in manual mode
    RESET_PULSE = 1 << 7  # Bit 7: Reset signal pulse


class CncMovementStatus(IntFlag):
    """Byte 3: Movement Status."""

    NONE = 0
    WAITING_FOR_ACK = 1 << 0  # Bit 0: Machine waits for user acknowledge
    SPINDLE_STOPPED = 1 << 1  # Bit 1: Milling spindle and drilling head is not moving
    AXES_MOVING = 1 << 2  # Bit 2: Axes in cnc channel are moving
    MOVEMENT_STOP_ACTIVE = 1 << 3  # Bit 3: Movement stop active


class CncFieldStatus(IntFlag):
    """Byte 10 (Field A) / Byte 15 (Field D): Field Status."""

    NONE = 0
    READY_TO_START = 1 << 0  # Bit 0: Field is active and program is ready to start
    RUNNING = 1 << 1  # Bit 1: Field is working. Program is running
    ENDED = 1 << 2  # Bit 2: Field ready. Program ended
    CANCELED = 1 << 3  # Bit 3: Field canceled
    VACUUM_ON = 1 << 6  # Bit 6: Field vacuum is on
    VACUUM_OFF = 1 << 7  # Bit 7: Field vacuum is off


class CncPinStatus(IntFlag):
    """Byte 12 (Field A) / Byte 17 (Field D): Pin Status."""

    NONE = 0
    RETRACTED = 1 << 0  # Bit 0: Field pins are retracted
    EXTENDED = 1 << 1  # Bit 1: Field pins are extended


class HolzherCncClient:
    """Client for communicating with the Holz-her CNC machine via TCP.

    Parameters
    ----------
    host : str, optional
        The IP address of the CNC machine, by default "169.254.75.52".
    command_port : int, optional
        The port for sending commands, by default 23023.
    response_port : int, optional
        The port for receiving responses, by default 23024.
    timeout : float, optional
        The timeout for socket operations in seconds, by default 5.0.
    """

    def __init__(self, host: str = "169.254.75.52", command_port: int = 23023, response_port: int = 23024, timeout: float = 5.0):
        self.host = host
        self.command_port = command_port
        self.response_port = response_port
        self.timeout = timeout
        self._command_socket: Optional[socket.socket] = None
        self._response_socket: Optional[socket.socket] = None
        self._buffer = ""

        # Internal state
        self._machine_status = CncMachineStatus.NONE
        self._movement_status = CncMovementStatus.NONE
        self._field_a_status = CncFieldStatus.NONE
        self._field_a_pins = CncPinStatus.NONE
        self._field_d_status = CncFieldStatus.NONE
        self._field_d_pins = CncPinStatus.NONE

        # Shadow registers for OUTPUT bytes to avoid read-modify-write issues with Input bytes
        self._field_a_control = 0  # To track Byte 10 output
        self._field_d_control = 0  # To track Byte 15 output

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self):
        """Establishes connection to the CNC machine.

        Raises
        ------
        Exception
            If connection fails.
        """
        LOG.info(f"Connecting to CNC at {self.host} (CMD: {self.command_port}, RESP: {self.response_port})...")
        try:
            # Connect to response port first (Machine often expects this listener first)
            self._response_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._response_socket.settimeout(self.timeout)
            self._response_socket.connect((self.host, self.response_port))

            # Connect to command port
            self._command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._command_socket.settimeout(self.timeout)
            self._command_socket.connect((self.host, self.command_port))

            LOG.info("Connected to CNC.")

            # clear buffer after hello
            self._clear_response_buffer()

            # This is problematic, disabled for now
            # # Fetch initial state
            # self.request_machine_state()
        except Exception as e:
            LOG.error(f"Failed to connect to CNC at {self.host}: {e}")
            self.close()
            raise

    def _clear_response_buffer(self):
        """Clears any pending data in the response socket buffer."""
        if not self._response_socket:
            return

        try:
            self._response_socket.settimeout(0.1)
            while True:
                try:
                    chunk = self._response_socket.recv(4096)
                    if not chunk:
                        break
                    LOG.info(f"Cleared initial buffer data: {chunk}")
                except socket.timeout:
                    break
        except Exception as e:
            LOG.warning(f"Error clearing buffer: {e}")
        finally:
            self._response_socket.settimeout(self.timeout)

    def close(self):
        """Closes the connection to the CNC machine."""
        if self._command_socket:
            try:
                self._command_socket.close()
            except Exception as e:
                LOG.warning(f"Error closing command socket: {e}")
            self._command_socket = None

        if self._response_socket:
            try:
                self._response_socket.close()
            except Exception as e:
                LOG.warning(f"Error closing response socket: {e}")
            self._response_socket = None
        LOG.info("Disconnected from CNC.")

    def _recv_message(self) -> str:
        """Receives a single null-terminated message from the response socket.

        Returns
        -------
        str
            The received message string.
        """
        if not self._response_socket:
            raise ConnectionError("Not connected to CNC")

        while "\x00" not in self._buffer:
            try:
                chunk = self._response_socket.recv(4096)
                if not chunk:
                    raise ConnectionError("Response socket closed by remote")
                self._buffer += chunk.decode()
            except socket.timeout:
                if self._buffer:
                    LOG.warning(f"Timeout while receiving message. Partial buffer: {repr(self._buffer)}")
                raise

        line, self._buffer = self._buffer.split("\x00", 1)
        return line.strip()

    def _handle_async_event(self, line: str):
        """Handles asynchronous events from the CNC machine.

        Parameters
        ----------
        line : str
            The raw event string received from the CNC.
        """
        try:
            # Format: TYPE;index;value
            parts = line.split(";")
            if len(parts) < 3:
                LOG.warning(f"Unknown async event format: {line}")
                return

            event_type = parts[0]
            index = int(parts[1])

            if event_type in ("GET_BYTE", "SET_BYTE"):
                value = int(parts[2])
                if index == 1:
                    self._machine_status = CncMachineStatus(value)
                    status = self._machine_status
                    LOG.info(f"Async Event: Machine Status changed to {repr(status)} (Value: {value})")
                elif index == 3:
                    self._movement_status = CncMovementStatus(value)
                    status = self._movement_status
                    LOG.info(f"Async Event: Movement Status changed to {repr(status)} (Value: {value})")
                elif index == 10:
                    self._field_a_status = CncFieldStatus(value)
                    status = self._field_a_status
                    LOG.info(f"Async Event: Field A Status changed to {repr(status)} (Value: {value})")
                elif index == 12:
                    self._field_a_pins = CncPinStatus(value)
                    status = self._field_a_pins
                    LOG.info(f"Async Event: Field A Pins changed to {repr(status)} (Value: {value})")
                elif index == 15:
                    self._field_d_status = CncFieldStatus(value)
                    status = self._field_d_status
                    LOG.info(f"Async Event: Field D Status changed to {repr(status)} (Value: {value})")
                elif index == 17:
                    self._field_d_pins = CncPinStatus(value)
                    status = self._field_d_pins
                    LOG.info(f"Async Event: Field D Pins changed to {repr(status)} (Value: {value})")
                else:
                    LOG.info(f"Async Event: Byte {index} changed to {value} (Binary: {value:08b})")

            elif event_type in ("GET_INT", "SET_INT"):
                value = int(parts[2])
                LOG.info(f"Async Event: Int {index} changed to {value}")

            elif event_type in ("GET_FLOAT", "SET_FLOAT"):
                value = float(parts[2])
                LOG.info(f"Async Event: Float {index} changed to {value}")

            else:
                LOG.warning(f"Unknown async event type: {line}")

        except Exception as e:
            LOG.error(f"Error parsing async event '{line}': {e}")

    def send_command(self, command: str) -> str:
        """Sends a command to the CNC and waits for a response.

        Parameters
        ----------
        command : str
            The command string to send.

        Returns
        -------
        str
            The response string from the CNC.

        Raises
        ------
        ConnectionError
            If not connected to the CNC.
        socket.timeout
            If no response is received within the timeout period.
        """
        if not self._command_socket:
            raise ConnectionError("Not connected to CNC")

        msg = command.strip() + "\n"
        LOG.info(f"{Colors.SENT}SENT: {command.strip()}{Colors.RESET}")
        try:
            self._command_socket.sendall(msg.encode())

            # Determine command type for lenient matching (e.g., SET_BYTE)
            cmd_type = command.strip().split()[0] if command.strip() else ""

            # Read responses until we get the ACK (COMMAND_EXECUTED)
            # Any non-async, non-ACK message is considered the result.
            result_response = None

            while True:
                response = self._recv_message()
                LOG.info(f"{Colors.RECV}RECV: {response}{Colors.RESET}")

                # If the response is an exact echo of the command type (e.g. SET_BYTE... for SET_BYTE command),
                # accept it as success even without COMMAND_EXECUTED prefix.
                if cmd_type and response.startswith(cmd_type):
                    # But be careful not to mistake async GET_BYTE for a response if we didn't send GET_BYTE
                    if cmd_type in ("SET_BYTE", "SET_INT", "SET_FLOAT", "GESPANNT", "ACTIV"):
                        # This acts as a confirmation for these commands
                        result_response = response
                        break

                if response.startswith(("GET_BYTE", "GET_INT", "GET_FLOAT")):
                    self._handle_async_event(response)
                    continue

                if response.startswith("COMMAND_EXECUTED"):
                    # This is the ACK.

                    # IGNORE late-arriving CONNECT messages if they get interpreted here
                    if "CLIENT_CONNECT" in response or "CONNECTED" in response:
                        LOG.info(f"Ignored late handshake message: {response}")
                        continue

                    # Also check if the ACK contains a state update (e.g. "COMMAND_EXECUTED:GET_BYTE;1;71")
                    if ":" in response:
                        _, payload = response.split(":", 1)
                        if payload.startswith(("GET_BYTE", "SET_BYTE", "GET_INT", "SET_INT", "GET_FLOAT", "SET_FLOAT")):
                            self._handle_async_event(payload)

                    if result_response is None:
                        result_response = response  # Fallback if no other result
                    break

                # This is likely the result (echo + value)
                result_response = response

            return result_response if result_response else ""

        except Exception as e:
            LOG.error(f"Error communicating with CNC: {e}")
            raise

    def _parse_response(self, response: str) -> str:
        """Parses the response from the CNC machine, extracting the value.

        Parameters
        ----------
        response : str
            The raw response string.

        Returns
        -------
        str
            The extracted value from the response.
        """
        if "\t" in response:
            return response.split("\t")[-1]
        return response

    def activate_table(self, table_id: str, active: bool) -> None:
        """Activates or deactivates a table.

        Parameters
        ----------
        table_id : str
            The table identifier ("A" or "D").
        active : bool
            True to activate (1), False to deactivate (0).
        """
        state = "1" if active else "0"
        response = self.send_command(f"ACTIV\t{table_id}\t{state}")
        if not response.startswith("COMMAND_EXECUTED"):
            raise RuntimeError(f"Failed to set activation state for table {table_id} to {state}. Response: {response}")

    def set_byte(self, index: int, value: int) -> str:
        """Sets a byte in the PLC interface.

        Parameters
        ----------
        index : int
            The byte index.
        value : int
            The value to set (0-255).

        Returns
        -------
        str
            The response from the CNC.
        """
        return self.send_command(f"SET_BYTE\t{index}\t{value}")

    def request_machine_state(self) -> None:
        """Requests current status bytes from the CNC (1, 3, 10, 12, 15, 17)."""
        # Byte 1: Machine Status
        self.send_command("GET_BYTE\t1")
        # Byte 3: Movement Status
        self.send_command("GET_BYTE\t3")
        # Byte 10: Field A Status
        self.send_command("GET_BYTE\t10")
        # Byte 12: Field A Pins
        self.send_command("GET_BYTE\t12")
        # Byte 15: Field D Status
        self.send_command("GET_BYTE\t15")
        # Byte 17: Field D Pins
        self.send_command("GET_BYTE\t17")

    def start_program(self, table_id: str) -> None:
        """Starts the program on the specified table.

        Parameters
        ----------
        table_id : str
            The table identifier ("A" or "D").

        Sets Byte 10 (Field A) or 15 (Field D), Bit 0 to 1.
        """
        if table_id == "A":
            current_control = self._field_a_control
            byte_index = 10
        elif table_id == "D":
            current_control = self._field_d_control
            byte_index = 25
        else:
            raise ValueError(f"Invalid table_id: {table_id}. Must be 'A' or 'D'.")

        # Set Bit 0 (Start Program)
        new_control = current_control | (1 << 0)

        # Update shadow register
        if table_id == "A":
            self._field_a_control = new_control
        else:
            self._field_d_control = new_control

        self.set_byte(byte_index, new_control)

    @property
    def is_emergency_stop(self) -> bool:
        """True if the emergency stop is NOT active (i.e., NO_EMERGENCY_STOP is set)."""
        # Note: CONSTANT IS NO_EMERGENCY_STOP. So if bit is 1, No ESTOP.
        # If is_emergency_stop implies "is the button pressed?", then it is pressed if bit is 0.
        return not bool(self._machine_status & CncMachineStatus.NO_EMERGENCY_STOP)

    @property
    def switched_on(self) -> bool:
        """True if the machine is switched on."""
        return bool(self._machine_status & CncMachineStatus.SWITCHED_ON)

    @property
    def ready(self) -> bool:
        """True if the machine is ready to work."""
        return bool(self._machine_status & CncMachineStatus.READY)

    @property
    def manual_mode(self) -> bool:
        """True if the machine is in manual mode."""
        return bool(self._machine_status & CncMachineStatus.MANUAL_MODE)

    def get_vacuum(self, table_id: str) -> bool:
        """Gets the vacuum state for the specified field.

        Parameters
        ----------
        table_id : str
            The table identifier ("A" or "D").

        Returns
        -------
        bool
            True if vacuum is on, False otherwise.
        """
        if table_id == "A":
            return bool(self._field_a_status & CncFieldStatus.VACUUM_ON)
        elif table_id == "D":
            return bool(self._field_d_status & CncFieldStatus.VACUUM_ON)
        else:
            raise ValueError(f"Invalid table_id: {table_id}. Must be 'A' or 'D'.")

    def set_vacuum(self, table_id: str, value: bool) -> None:
        """Sets the vacuum state for the specified field.

        Parameters
        ----------
        table_id : str
            The table identifier ("A" or "D").
        value : bool
            True to turn vacuum on, False to turn off.
        """
        if table_id == "A":
            byte_index = 10
        elif table_id == "D":
            byte_index = 25
        else:
            raise ValueError(f"Invalid table_id: {table_id}. Must be 'A' or 'D'.")

        if value:
            new_value = 112
        else:
            new_value = 176

        self.set_byte(byte_index, new_value)

    def get_status(self, table_id: str) -> CncTableStatus:
        """Gets the status of a table.

        Parameters
        ----------
        table_id : str
            The table identifier ("A" or "D").

        Returns
        -------
        CncTableStatus
            The status as a CncTableStatus flag.
        """
        response = self.send_command(f"GET_STATUS\t{table_id}")
        try:
            return CncTableStatus(int(self._parse_response(response)))
        except ValueError:
            LOG.error(f"Failed to parse status from response: {response}")
            return CncTableStatus(0)

    def get_hop_file(self, zero_point: str) -> str:
        """Gets the hop file currently assigned to a zero point.

        Parameters
        ----------
        zero_point : str
            Identifier for the zero point (e.g., "AH").

        Returns
        -------
        str
            The name of the hop file.
        """
        response = self._parse_response(self.send_command(f"GET_HOP_FILE\t{zero_point}"))
        if ";" in response:
            return response.split(";")[-1]
        return response

    def add_hop_file(self, zero_point: str, file_path: str, count: int = 1) -> str:
        """Adds a hop file to a specific zero point.

        Parameters
        ----------
        zero_point : str
            Identifier for the zero point (e.g., "DV", "AV", "DH", "AH").
        file_path : str
            Path to the .hop file.
        count : int, optional
            Number of parts to produce, by default 1.

        Returns
        -------
        str
            The response from the CNC.
        """
        return self._parse_response(self.send_command(f"ADD_HOP_FILE\t{zero_point}\t{file_path}\t{count}"))

    def load_machineload(self, file_path: str) -> str:
        """Loads a machine load file (.jlx) on the CNC.

        Parameters
        ----------
        file_path : str
            Path to the .jlx file.

        Returns
        -------
        str
            The response from the CNC.
        """
        return self._parse_response(self.send_command(f"LOAD_MACHINELOAD\t{file_path}"))

    def remove_hop_file(self, zero_point: str) -> str:
        """Removes the hop file from the given zero point.

        Parameters
        ----------
        zero_point : str
            Identifier for the zero point (e.g., "DV", "AV", "DH", "AH").

        Returns
        -------
        str
            The response from the CNC.
        """
        return self._parse_response(self.send_command(f"REMOVE_HOP_FILE\t{zero_point}"))

    def write_variable(self, zero_point: str, var_name: str, value: str) -> str:
        """Writes a variable to a hop file on a zero point.

        WARNING: Write variables before activating the hop file.

        Parameters
        ----------
        zero_point : str
            Identifier for the zero point.
        var_name : str
            Name of the variable exported from Hops.
        value : str
            Value to set.

        Returns
        -------
        str
            The response from the CNC.
        """
        return self._parse_response(self.send_command(f"WRITEVAR\t{zero_point}\t{var_name}\t{value}"))


if __name__ == "__main__":
    # Smoke test
    logging.basicConfig(level=logging.DEBUG)

    # Use localhost for testing if the real machine is not available
    # You can start a dummy server to test this: `nc -l 23023` and `nc -l 23024`
    # TEST_HOST = "127.0.0.1"
    TEST_HOST = "169.254.75.52"  # Real CNC IP

    print(f"--- Starting CNC Client Smoke Test on {TEST_HOST} ---")
    print("Ensure you have a server listening or the CNC is connected.")
    print("To test locally with netcat (in two terminals):")
    print("  nc -l 23023")
    print("  nc -l 23024")

    try:
        with HolzherCncClient(host=TEST_HOST, timeout=10.0) as client:
            print("Connected! Listening for incoming data... (Ctrl+C to stop)")

            client.add_hop_file("AV", r"D:\CAMPUS\Data\Hops\Hop\antikythera\S11_R00.hop")
            time.sleep(1)

            # ********
            # client.activate_table("A", True)
            try:
                client.activate_table("A", True)
                time.sleep(1)
            except TimeoutError:
                LOG.error("Cannot deactivate table A")

            # client.activate_table("D", True)
            try:
                client.activate_table("D", True)
                time.sleep(1)
            except TimeoutError:
                LOG.error("Cannot deactivate table D")
            # ********

            try:
                client.set_vacuum("A", True)
            except TimeoutError:
                LOG.error("Cannot set vacuum A")
            time.sleep(1)

            try:
                client.set_vacuum("D", True)
            except TimeoutError:
                LOG.error("Cannot set vacuum D")
            time.sleep(1)

            client.set_vacuum("A", False)
            time.sleep(1)
            client.set_vacuum("D", False)
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nUser interrupted.")
    except ConnectionRefusedError:
        print("Connection refused. Is the server/CNC running?")
    except Exception as e:
        print(f"An error occurred: {e}")

    print("--- Finished ---")
