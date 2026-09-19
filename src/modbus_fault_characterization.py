"""对远程 Modbus 设备断开与恢复进行一次带时间戳的表征实验。"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_SCRIPT = PROJECT_ROOT / "src" / "modbus_sim_server.py"

SIMULATOR_HOST = "127.0.0.1"
SIMULATOR_PORT = 15020
OPENPLC_HOST = "127.0.0.1"
OPENPLC_PORT = 5020
DEVICE_ID = 1
REMOTE_HR0_ADDRESS = 0
OPENPLC_IW0_ADDRESS = 0
EXPECTED_VALUE = 1234

POLL_INTERVAL = 0.05
SIMULATOR_READY_TIMEOUT = 5.0
NORMAL_TIMEOUT = 15.0
ZERO_FILL_TIMEOUT = 10.0
RECOVERY_TIMEOUT = 15.0
FAULT_HOLD_TIME = 2.0
STABLE_SAMPLE_COUNT = 3


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _start_simulator() -> subprocess.Popen[bytes]:
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

    return subprocess.Popen(
        [sys.executable, str(SIMULATOR_SCRIPT)],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def _stop_simulator(process: subprocess.Popen[bytes]) -> None:
    """只终止本脚本创建的 simulator 进程，用它注入远端设备消失故障。"""
    if process.poll() is not None:
        return

    if sys.platform == "win32":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=3)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass

    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _read_simulator_hr0() -> int:
    client = ModbusTcpClient(
        SIMULATOR_HOST,
        port=SIMULATOR_PORT,
        timeout=1,
        retries=0,
    )
    try:
        if not client.connect():
            raise ConnectionError("simulator connect() returned False")
        response = client.read_holding_registers(
            REMOTE_HR0_ADDRESS,
            count=1,
            device_id=DEVICE_ID,
        )
        if response.isError() or getattr(response, "registers", None) != [EXPECTED_VALUE]:
            raise RuntimeError(f"simulator FC03 returned {response!r}")
        return response.registers[0]
    finally:
        client.close()


def _wait_for_simulator_ready(timeout: float = SIMULATOR_READY_TIMEOUT) -> int:
    """以真实 FC03=[1234] 判断 simulator 就绪，而不是只检查 TCP。"""
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            return _read_simulator_hr0()
        except (ConnectionError, ModbusException, OSError, RuntimeError) as error:
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"simulator FC03 readiness timeout; last error: {last_error}")


def _open_observation_client() -> ModbusTcpClient:
    client = ModbusTcpClient(
        OPENPLC_HOST,
        port=OPENPLC_PORT,
        timeout=1,
        retries=0,
    )
    if not client.connect():
        client.close()
        raise ConnectionError("OpenPLC observation channel connect() returned False")
    return client


def _read_openplc_iw0(client: ModbusTcpClient) -> int:
    response = client.read_input_registers(
        OPENPLC_IW0_ADDRESS,
        count=1,
        device_id=DEVICE_ID,
    )
    registers = getattr(response, "registers", None)
    if response.isError() or not isinstance(registers, list) or len(registers) != 1:
        raise RuntimeError(f"OpenPLC FC04 observation failed: {response!r}")
    return registers[0]


def _wait_for_value(
    client: ModbusTcpClient,
    expected: int,
    timeout: float,
) -> tuple[float, str]:
    deadline = time.monotonic() + timeout
    last_value: int | str = "not observed"
    while time.monotonic() < deadline:
        last_value = _read_openplc_iw0(client)
        if last_value == expected:
            return time.monotonic(), _timestamp()
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"waiting for OpenPLC %IW0={expected} timed out; last value={last_value}"
    )


def _wait_for_stable_value(
    client: ModbusTcpClient,
    expected: int,
    timeout: float,
    required_samples: int = STABLE_SAMPLE_COUNT,
) -> tuple[float, str]:
    deadline = time.monotonic() + timeout
    consecutive = 0
    first_monotonic = 0.0
    first_timestamp = ""
    last_value: int | str = "not observed"

    while time.monotonic() < deadline:
        last_value = _read_openplc_iw0(client)
        if last_value == expected:
            if consecutive == 0:
                first_monotonic = time.monotonic()
                first_timestamp = _timestamp()
            consecutive += 1
            if consecutive >= required_samples:
                return first_monotonic, first_timestamp
        else:
            consecutive = 0
            first_monotonic = 0.0
            first_timestamp = ""
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        f"OpenPLC %IW0 did not remain {expected} for {required_samples} samples; "
        f"last value={last_value}"
    )


def _confirm_zero_stable(client: ModbusTcpClient) -> tuple[bool, int]:
    deadline = time.monotonic() + FAULT_HOLD_TIME
    sample_count = 0
    stable = True
    while time.monotonic() < deadline:
        sample_count += 1
        if _read_openplc_iw0(client) != 0:
            stable = False
        time.sleep(POLL_INTERVAL)
    return stable, sample_count


def main() -> int:
    experiment_start = _timestamp()
    observation_client: ModbusTcpClient | None = None
    simulator: subprocess.Popen[bytes] | None = None
    experiment_error: BaseException | None = None

    normal_value: int | str = "NOT ESTABLISHED"
    normal_timestamp = "NOT ESTABLISHED"
    fault_timestamp = "NOT INJECTED"
    zero_timestamp = "NOT OBSERVED"
    zero_latency: float | None = None
    zero_stable = False
    zero_samples = 0
    remote_ready_timestamp = "NOT READY"
    recovered_timestamp = "NOT OBSERVED"
    recovery_latency: float | None = None
    recovered_stable = False
    final_value: int | str = "UNKNOWN"
    restoration = "NOT ATTEMPTED"

    print(f"EXPERIMENT_START: {experiment_start}")

    try:
        observation_client = _open_observation_client()
        simulator = _start_simulator()
        simulator_hr0 = _wait_for_simulator_ready()

        _, normal_timestamp = _wait_for_stable_value(
            observation_client,
            EXPECTED_VALUE,
            NORMAL_TIMEOUT,
        )
        normal_value = _read_openplc_iw0(observation_client)

        fault_monotonic = time.monotonic()
        fault_timestamp = _timestamp()
        _stop_simulator(simulator)

        zero_monotonic, zero_timestamp = _wait_for_value(
            observation_client,
            0,
            ZERO_FILL_TIMEOUT,
        )
        zero_latency = zero_monotonic - fault_monotonic
        zero_stable, zero_samples = _confirm_zero_stable(observation_client)
        if not zero_stable:
            raise RuntimeError("OpenPLC %IW0 did not remain zero during fault hold")

        simulator = _start_simulator()
        simulator_hr0 = _wait_for_simulator_ready()
        remote_ready_monotonic = time.monotonic()
        remote_ready_timestamp = _timestamp()

        recovered_monotonic, recovered_timestamp = _wait_for_value(
            observation_client,
            EXPECTED_VALUE,
            RECOVERY_TIMEOUT,
        )
        recovery_latency = recovered_monotonic - remote_ready_monotonic
        _wait_for_stable_value(
            observation_client,
            EXPECTED_VALUE,
            RECOVERY_TIMEOUT,
        )
        recovered_stable = True
        final_value = _read_openplc_iw0(observation_client)

    except BaseException as error:
        experiment_error = error
    finally:
        try:
            if simulator is None or simulator.poll() is not None:
                simulator = _start_simulator()
            _wait_for_simulator_ready()

            if observation_client is None:
                observation_client = _open_observation_client()
            _wait_for_stable_value(
                observation_client,
                EXPECTED_VALUE,
                RECOVERY_TIMEOUT,
            )
            final_value = _read_openplc_iw0(observation_client)
            restoration = "PASS"
        except BaseException as cleanup_error:
            restoration = f"FAIL: {type(cleanup_error).__name__}: {cleanup_error}"
            if experiment_error is None:
                experiment_error = cleanup_error
        finally:
            if observation_client is not None:
                observation_client.close()

    print("NORMAL:")
    print(f"- simulator HR0: {EXPECTED_VALUE if normal_value == EXPECTED_VALUE else 'UNKNOWN'}")
    print(f"- OpenPLC %IW0: {normal_value}")
    print(f"- NORMAL_ESTABLISHED: {normal_timestamp}")
    print("FAULT:")
    print(f"- fault timestamp: {fault_timestamp}")
    print(f"- first zero timestamp: {zero_timestamp}")
    print(
        "- zero_fill_latency_s: "
        + (f"{zero_latency:.6f}" if zero_latency is not None else "NOT OBSERVED")
    )
    print(f"- zero stable: {zero_stable} ({zero_samples} samples)")
    print("RECOVERY:")
    print(f"- remote ready timestamp: {remote_ready_timestamp}")
    print(f"- first 1234 timestamp: {recovered_timestamp}")
    print(
        "- recovery_latency_s: "
        + (f"{recovery_latency:.6f}" if recovery_latency is not None else "NOT OBSERVED")
    )
    print(f"- recovered stable: {recovered_stable}")
    print("FINAL:")
    print(f"- simulator running: {simulator is not None and simulator.poll() is None}")
    print(f"- simulator pid: {simulator.pid if simulator is not None else 'UNKNOWN'}")
    print(f"- %IW0: {final_value}")
    print("- observation channel: closed after final successful FC04 read")
    print(f"- cleanup/restoration: {restoration}")

    if experiment_error is not None:
        print(
            "CHARACTERIZATION_RESULT: FAIL: "
            f"{type(experiment_error).__name__}: {experiment_error}"
        )
        return 1

    print("CHARACTERIZATION_RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
