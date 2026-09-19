"""验证 OpenPLC Modbus Master 在远端设备断开后的清零与恢复行为。"""

import socket
import sys
import time

import pytest
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 15021
CONTROL_TIMEOUT = 3.0

REMOTE_HOST = "127.0.0.1"
REMOTE_PORT = 15020
REMOTE_DEVICE_ID = 1
REMOTE_REGISTER = 0
REMOTE_EXPECTED_VALUE = 1234

OPENPLC_HOST = "127.0.0.1"
OPENPLC_MODBUS_PORT = 5020
OPENPLC_DEVICE_ID = 1
OPENPLC_INPUT_REGISTER = 0

POLL_INTERVAL = 0.05
NORMAL_TIMEOUT = 15.0
ZERO_TIMEOUT = 10.0
RECOVERY_TIMEOUT = 15.0
STABLE_READS = 3


class ControlFixtureUnavailable(ConnectionError):
    """控制端口在 TCP connect 阶段不可达。"""


def _send_control_command(command: str) -> str:
    """发送一条 newline-delimited 控制命令并返回一行稳定响应。"""
    try:
        connection = socket.create_connection(
            (CONTROL_HOST, CONTROL_PORT),
            timeout=CONTROL_TIMEOUT,
        )
    except OSError as error:
        raise ControlFixtureUnavailable(
            f"cannot connect to simulator control at {CONTROL_HOST}:{CONTROL_PORT}"
        ) from error

    try:
        connection.settimeout(CONTROL_TIMEOUT)
        connection.sendall((command + "\n").encode("utf-8"))

        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > 4096:
                raise RuntimeError("simulator control response is unexpectedly large")
    except socket.timeout as error:
        raise RuntimeError(
            f"simulator control timed out after connecting for command {command!r}"
        ) from error
    except OSError as error:
        raise RuntimeError(
            f"simulator control exchange failed after connecting for command {command!r}"
        ) from error
    finally:
        connection.close()

    if not response:
        raise RuntimeError(f"simulator control returned an empty response to {command!r}")
    if not response.endswith(b"\n"):
        raise RuntimeError(
            f"simulator control returned an unterminated response to {command!r}"
        )

    try:
        text = response.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("simulator control returned invalid UTF-8") from error
    if not text:
        raise RuntimeError(f"simulator control returned an empty response to {command!r}")
    if text.startswith("ERROR"):
        raise RuntimeError(f"simulator control rejected {command!r}: {text}")
    return text


def _parse_status(response: str) -> str:
    """验证控制器状态格式，并返回 normal 或 fault。"""
    parts = response.split()
    if not parts or parts[0] != "OK":
        raise AssertionError(f"malformed simulator status response: {response!r}")

    fields = {}
    for part in parts[1:]:
        if "=" not in part:
            raise AssertionError(f"malformed simulator status field: {part!r}")
        key, value = part.split("=", 1)
        fields[key] = value

    if fields.get("controller") != "alive":
        raise AssertionError(f"simulator controller is not alive: {response!r}")
    state = fields.get("state")
    if state not in {"normal", "fault"}:
        raise AssertionError(f"unknown simulator state: {state!r}")
    return state


def _read_remote_hr0() -> int:
    client = ModbusTcpClient(
        REMOTE_HOST,
        port=REMOTE_PORT,
        timeout=1,
        retries=0,
    )
    try:
        if not client.connect():
            raise ConnectionError("remote Modbus connect() returned False")
        response = client.read_holding_registers(
            REMOTE_REGISTER,
            count=1,
            device_id=REMOTE_DEVICE_ID,
        )
        registers = getattr(response, "registers", None)
        if response.isError() or not isinstance(registers, list) or len(registers) != 1:
            raise RuntimeError(f"remote FC03 returned invalid response: {response!r}")
        return registers[0]
    finally:
        client.close()


def _read_openplc_input() -> int:
    client = ModbusTcpClient(
        OPENPLC_HOST,
        port=OPENPLC_MODBUS_PORT,
        timeout=1,
        retries=0,
    )
    try:
        if not client.connect():
            raise ConnectionError("OpenPLC observation connect() returned False")
        response = client.read_input_registers(
            OPENPLC_INPUT_REGISTER,
            count=1,
            device_id=OPENPLC_DEVICE_ID,
        )
        registers = getattr(response, "registers", None)
        if response.isError() or not isinstance(registers, list) or len(registers) != 1:
            raise RuntimeError(f"OpenPLC FC04 returned invalid response: {response!r}")
        return registers[0]
    finally:
        client.close()


def _wait_for_remote_value(expected: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    last_observation = "not observed"

    while time.monotonic() < deadline:
        try:
            value = _read_remote_hr0()
            last_observation = value
            if value == expected:
                return value
        except (ConnectionError, ModbusException, OSError, RuntimeError) as error:
            last_observation = f"{type(error).__name__}: {error}"
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        f"waiting for remote HR0={expected} timed out; last={last_observation}"
    )


def _wait_for_openplc_input(
    expected: int,
    timeout: float,
    stable_reads: int = STABLE_READS,
) -> int:
    deadline = time.monotonic() + timeout
    consecutive = 0
    last_observation = "not observed"

    while time.monotonic() < deadline:
        try:
            value = _read_openplc_input()
            last_observation = value
            consecutive = consecutive + 1 if value == expected else 0
            if consecutive >= stable_reads:
                return value
        except (ConnectionError, ModbusException, OSError, RuntimeError) as error:
            last_observation = f"{type(error).__name__}: {error}"
            consecutive = 0
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        f"waiting for OpenPLC %IW0={expected} for {stable_reads} stable reads "
        f"timed out; last={last_observation}"
    )


def _remote_fc03_is_unavailable() -> bool:
    """发送真实 FC03；不能得到有效 [1234] 即视为远端服务不可用。"""
    client = ModbusTcpClient(
        REMOTE_HOST,
        port=REMOTE_PORT,
        timeout=1,
        retries=0,
    )
    try:
        client.connect()
        try:
            response = client.read_holding_registers(
                REMOTE_REGISTER,
                count=1,
                device_id=REMOTE_DEVICE_ID,
            )
        except (ModbusException, OSError):
            return True

        registers = getattr(response, "registers", None)
        return response.isError() or registers != [REMOTE_EXPECTED_VALUE]
    finally:
        client.close()


def test_remote_disconnect_zero_fills_and_recovers():
    """固定当前 OpenPLC v4.2.2 + set-to-zero 配置的已验证行为。

    controlled pymodbus simulator 通信丢失后，映射的 IEC input 应清零；
    远端恢复后，OpenPLC Master 应自动重连并恢复数据。本测试不是性能
    benchmark，也不声称所有 Modbus/TCP server 或 OpenPLC 版本行为相同。
    """
    controller_reachable = False
    try:
        try:
            status = _send_control_command("status")
        except ControlFixtureUnavailable:
            pytest.skip(
                "controlled Modbus simulator is not running on 127.0.0.1:15021"
            )

        controller_reachable = True
        _parse_status(status)

        # Arrange：幂等恢复 normal，并建立远端与 IEC observation 的已知基线。
        assert _send_control_command("fault_off") == "OK state=normal"
        remote_value = _wait_for_remote_value(
            REMOTE_EXPECTED_VALUE,
            NORMAL_TIMEOUT,
        )
        openplc_value = _wait_for_openplc_input(
            REMOTE_EXPECTED_VALUE,
            NORMAL_TIMEOUT,
        )
        print(f"arrange: remote HR0={remote_value}, OpenPLC %IW0={openplc_value}")

        # Act/Assert fault：只关闭远端 Modbus 服务，PLC 与 Runtime 保持运行。
        assert _send_control_command("fault_on") == "OK state=fault"
        assert _remote_fc03_is_unavailable(), (
            "fault_on 后远端 FC03 仍意外返回有效 [1234]"
        )
        zero_value = _wait_for_openplc_input(0, ZERO_TIMEOUT)
        print(f"fault: remote FC03 unavailable, OpenPLC %IW0={zero_value}")

        # Act/Assert recovery：恢复服务并以真实 FC03/FC04 有界轮询判断就绪。
        assert _send_control_command("fault_off") == "OK state=normal"
        restored_remote = _wait_for_remote_value(
            REMOTE_EXPECTED_VALUE,
            RECOVERY_TIMEOUT,
        )
        restored_openplc = _wait_for_openplc_input(
            REMOTE_EXPECTED_VALUE,
            RECOVERY_TIMEOUT,
        )
        print(
            "recovery: "
            f"remote HR0={restored_remote}, OpenPLC %IW0={restored_openplc}"
        )
    finally:
        original_error = sys.exception()
        cleanup_error = None
        if controller_reachable:
            try:
                response = _send_control_command("fault_off")
                assert response == "OK state=normal", (
                    f"cleanup fault_off returned {response!r}"
                )
                _wait_for_remote_value(REMOTE_EXPECTED_VALUE, RECOVERY_TIMEOUT)
                _wait_for_openplc_input(REMOTE_EXPECTED_VALUE, RECOVERY_TIMEOUT)
            except Exception as error:  # cleanup 失败必须明确报告。
                cleanup_error = error

        if cleanup_error is not None:
            original_detail = ""
            if original_error is not None:
                original_detail = (
                    "；原始测试错误："
                    f"{type(original_error).__name__}: {original_error}"
                )
            raise RuntimeError(
                "remote fault regression 状态恢复失败："
                f"{type(cleanup_error).__name__}: {cleanup_error}"
                f"{original_detail}"
            ) from cleanup_error
