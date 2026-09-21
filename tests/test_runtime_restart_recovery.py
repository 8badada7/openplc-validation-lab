"""验证 OpenPLC Runtime 容器重启后的自动恢复行为。"""

import json
import shutil
import subprocess
import sys
import time
import warnings

import pytest
import requests
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
from urllib3.exceptions import InsecureRequestWarning

from tests.test_modbus_master_fault_recovery import (
    ControlFixtureUnavailable,
    _parse_status,
    _send_control_command,
    _wait_for_openplc_input,
    _wait_for_remote_value,
)


CONTAINER_NAME = "openplc-runtime"
HISTORICAL_CONTAINER_NAME = "openplc-runtime-v4.1.9-candidate"
EXPECTED_RUNTIME_VERSION = "v4.2.2"
RUNTIME_VERSION_URL = "https://127.0.0.1:8443/api/version"

OPENPLC_HOST = "127.0.0.1"
OPENPLC_MODBUS_PORT = 5020
OPENPLC_DEVICE_ID = 1
OPENPLC_INPUT_REGISTER = 0
EXPECTED_VALUE = 1234

POLL_INTERVAL = 0.1
PROBE_TIMEOUT = 0.5
RESTART_COMMAND_TIMEOUT = 30.0
RECOVERY_TIMEOUT = 30.0
STABLE_READS = 3


def _run_docker(*arguments: str, timeout: float = 5.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"docker {' '.join(arguments)} timed out after {timeout:.1f}s"
        ) from error


def _inspect_container(name: str, *, missing_ok: bool = False) -> dict | None:
    result = _run_docker("inspect", name)
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip()
        if missing_ok and ("No such object" in error or "No such container" in error):
            return None
        raise RuntimeError(f"docker inspect {name} failed: {error}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"docker inspect {name} returned invalid JSON") from error
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError(f"docker inspect {name} returned unexpected data")
    return payload[0]


def _container_state(snapshot: dict) -> tuple[str | None, str | None]:
    state = snapshot.get("State", {})
    health = state.get("Health") or {}
    return state.get("Status"), health.get("Status")


def _probe_runtime_api() -> tuple[bool, str]:
    try:
        # Runtime 使用本地自签名证书；仅对此 readiness 请求关闭 TLS 验证。
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            response = requests.get(
                RUNTIME_VERSION_URL,
                timeout=PROBE_TIMEOUT,
                verify=False,
            )
        payload = response.json()
        valid = (
            response.status_code == 200
            and payload == {"version": EXPECTED_RUNTIME_VERSION}
        )
        return valid, f"HTTP {response.status_code}, payload={payload!r}"
    except (requests.RequestException, ValueError) as error:
        return False, f"{type(error).__name__}: {error}"


def _probe_openplc_fc04() -> tuple[bool, int | None, str]:
    client = ModbusTcpClient(
        OPENPLC_HOST,
        port=OPENPLC_MODBUS_PORT,
        timeout=PROBE_TIMEOUT,
        retries=0,
    )
    try:
        if not client.connect():
            return False, None, "connect() returned False"
        response = client.read_input_registers(
            OPENPLC_INPUT_REGISTER,
            count=1,
            device_id=OPENPLC_DEVICE_ID,
        )
        registers = getattr(response, "registers", None)
        if response.isError() or not isinstance(registers, list) or len(registers) != 1:
            return False, None, f"invalid FC04 response: {response!r}"
        return True, registers[0], f"registers={registers!r}"
    except (ModbusException, OSError, RuntimeError) as error:
        return False, None, f"{type(error).__name__}: {error}"
    finally:
        client.close()


def _assert_external_fixtures() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not available")

    try:
        current = _inspect_container(CONTAINER_NAME, missing_ok=True)
    except RuntimeError as error:
        pytest.fail(f"Docker fixture inspection failed: {error}")
    if current is None:
        pytest.skip(f"required Docker container {CONTAINER_NAME!r} does not exist")

    status, health = _container_state(current)
    assert status == "running", (
        f"{CONTAINER_NAME} baseline must be running, actual state={status!r}"
    )
    assert health in {None, "healthy"}, (
        f"{CONTAINER_NAME} baseline is unhealthy: health={health!r}"
    )

    candidate = _inspect_container(HISTORICAL_CONTAINER_NAME, missing_ok=True)
    if candidate is not None:
        candidate_status, _ = _container_state(candidate)
        assert candidate_status != "running", (
            f"historical candidate {HISTORICAL_CONTAINER_NAME} must remain stopped"
        )
    return current


def _assert_api_baseline() -> None:
    ready, detail = _probe_runtime_api()
    assert ready, f"Runtime API baseline is not ready: {detail}"


def _observe_restart_recovery() -> dict:
    action_started = time.monotonic()
    process = subprocess.Popen(
        ["docker", "restart", CONTAINER_NAME],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    interruption_observed = False
    interruption_evidence = None
    recovery_deadline = None
    stable_reads = 0
    api_recovered = False
    fc04_recovered = False
    last_api = "not probed"
    last_fc04 = "not probed"
    last_container_state = "not inspected"

    try:
        while True:
            now = time.monotonic()
            elapsed = now - action_started
            return_code = process.poll()

            try:
                snapshot = _inspect_container(CONTAINER_NAME)
                container_status, _ = _container_state(snapshot)
                last_container_state = str(container_status)
            except RuntimeError as error:
                container_status = None
                last_container_state = f"{type(error).__name__}: {error}"

            api_ok, last_api = _probe_runtime_api()
            fc04_ok, value, last_fc04 = _probe_openplc_fc04()

            if not interruption_observed:
                if container_status != "running":
                    interruption_observed = True
                    interruption_evidence = f"container state={container_status!r}"
                elif not api_ok:
                    interruption_observed = True
                    interruption_evidence = f"Runtime API unavailable: {last_api}"
                elif not fc04_ok:
                    interruption_observed = True
                    interruption_evidence = f"FC04 unavailable: {last_fc04}"

                if interruption_observed:
                    recovery_deadline = now + RECOVERY_TIMEOUT
                    stable_reads = 0
                    print(
                        "restart interruption observed: "
                        f"elapsed={elapsed:.3f}s, evidence={interruption_evidence}"
                    )

            if interruption_observed:
                api_recovered = api_ok
                if fc04_ok and value == EXPECTED_VALUE:
                    stable_reads += 1
                else:
                    stable_reads = 0
                fc04_recovered = stable_reads >= STABLE_READS

            if (
                interruption_observed
                and api_recovered
                and fc04_recovered
                and return_code is not None
            ):
                stdout, stderr = process.communicate()
                assert return_code == 0, (
                    f"docker restart failed with code {return_code}: {stderr.strip()}"
                )
                return {
                    "interruption_evidence": interruption_evidence,
                    "elapsed": elapsed,
                    "stable_reads": stable_reads,
                    "restart_stdout": stdout.strip(),
                }

            if return_code is None and elapsed >= RESTART_COMMAND_TIMEOUT:
                raise TimeoutError(
                    "docker restart command timed out; "
                    f"interruption_observed={interruption_observed}, "
                    f"container={last_container_state}, API={last_api}, FC04={last_fc04}"
                )
            if (
                interruption_observed
                and recovery_deadline is not None
                and now >= recovery_deadline
            ):
                raise TimeoutError(
                    "Runtime recovery timed out; "
                    f"restart_return_code={return_code}, container={last_container_state}, "
                    f"API={last_api}, FC04={last_fc04}, stable_reads={stable_reads}"
                )
            if return_code is not None and not interruption_observed:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    "docker restart completed without an observed post-action "
                    "unavailable transition; recovery cannot be accepted; "
                    f"code={return_code}, stdout={stdout.strip()!r}, "
                    f"stderr={stderr.strip()!r}, API={last_api}, FC04={last_fc04}"
                )

            time.sleep(POLL_INTERVAL)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _restore_external_state(controller_reachable: bool) -> None:
    if controller_reachable:
        response = _send_control_command("fault_off")
        assert response == "OK state=normal", (
            f"cleanup fault_off returned {response!r}"
        )

    snapshot = _inspect_container(CONTAINER_NAME)
    status, _ = _container_state(snapshot)
    if status != "running":
        result = _run_docker("start", CONTAINER_NAME, timeout=RESTART_COMMAND_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError(
                f"cleanup docker start failed: {(result.stderr or result.stdout).strip()}"
            )

    _wait_for_remote_value(EXPECTED_VALUE, RECOVERY_TIMEOUT)
    _wait_for_openplc_input(EXPECTED_VALUE, RECOVERY_TIMEOUT)


def test_runtime_recovers_after_container_restart():
    """固定 v4.2.2 与当前持久化 PLC fixture 的 restart recovery 行为。

    测试要求先观察到 restart 后真实的服务不可用状态，再接受 API、PLC、
    Modbus 和数据链路恢复。本测试不代表 crash/power-loss 等价性，也不对
    恢复耗时或其他 OpenPLC 版本作保证。
    """
    initial = _assert_external_fixtures()
    initial_started_at = initial["State"]["StartedAt"]
    _assert_api_baseline()

    try:
        status = _send_control_command("status")
    except ControlFixtureUnavailable:
        pytest.skip(
            "controlled Modbus simulator is not running on 127.0.0.1:15021"
        )

    controller_reachable = True
    try:
        _parse_status(status)
        assert _send_control_command("fault_off") == "OK state=normal"

        remote_value = _wait_for_remote_value(EXPECTED_VALUE, RECOVERY_TIMEOUT)
        openplc_value = _wait_for_openplc_input(EXPECTED_VALUE, RECOVERY_TIMEOUT)
        print(
            "arrange: "
            f"Runtime={EXPECTED_RUNTIME_VERSION}, remote HR0={remote_value}, "
            f"OpenPLC %IW0={openplc_value}"
        )

        result = _observe_restart_recovery()

        current = _inspect_container(CONTAINER_NAME)
        current_status, _ = _container_state(current)
        current_started_at = current["State"]["StartedAt"]
        assert current_status == "running", (
            f"container must be running after recovery, actual={current_status!r}"
        )
        assert current_started_at != initial_started_at, (
            "container StartedAt did not change after docker restart"
        )

        print(
            "recovery: "
            f"evidence={result['interruption_evidence']}; "
            f"API={EXPECTED_RUNTIME_VERSION}; FC04=[{EXPECTED_VALUE}] "
            f"stable_reads={result['stable_reads']}; "
            f"StartedAt changed={current_started_at != initial_started_at}"
        )
    finally:
        original_error = sys.exception()
        cleanup_error = None
        try:
            _restore_external_state(controller_reachable)
        except Exception as error:
            cleanup_error = error

        if cleanup_error is not None:
            original_detail = ""
            if original_error is not None:
                original_detail = (
                    "; original test error: "
                    f"{type(original_error).__name__}: {original_error}"
                )
            raise RuntimeError(
                "Runtime restart regression cleanup failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
                f"{original_detail}"
            ) from cleanup_error
