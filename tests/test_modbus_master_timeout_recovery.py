"""验证 OpenPLC Modbus Master 在终止性响应超时后的清零与恢复。"""

import sys
import time

import pytest
from pymodbus.exceptions import ModbusException

from tests.test_modbus_master_fault_recovery import (
    ControlFixtureUnavailable,
    _read_openplc_input,
    _send_control_command,
    _wait_for_openplc_input,
    _wait_for_remote_value,
)


EXPECTED_VALUE = 1234
TERMINAL_DELAY_MS = 5000
POLL_INTERVAL = 0.05
NORMAL_TIMEOUT = 15.0
ZERO_TIMEOUT = 15.0
RECOVERY_TIMEOUT = 15.0
STABLE_READS = 3


def _parse_delay_status(response: str) -> tuple[str, int]:
    """验证 status 响应，并返回支持 delayed 的 state 和 delay_ms。"""
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
    if state not in {"normal", "fault", "delayed"}:
        raise AssertionError(f"unknown simulator state: {state!r}")

    try:
        delay_ms = int(fields["delay_ms"])
    except (KeyError, ValueError) as error:
        raise AssertionError(
            f"invalid simulator delay_ms in status: {response!r}"
        ) from error

    if state in {"normal", "fault"} and delay_ms != 0:
        raise AssertionError(
            f"simulator state={state} must have delay_ms=0: {response!r}"
        )
    if state == "delayed" and delay_ms <= 0:
        raise AssertionError(
            f"simulator delayed state requires positive delay_ms: {response!r}"
        )
    return state, delay_ms


def _wait_for_stable_openplc_value(expected: int, timeout: float) -> int:
    """有界等待 %IW0 连续多次为 expected，并保留失败诊断。"""
    deadline = time.monotonic() + timeout
    consecutive = 0
    expected_observed = False
    last_observation = "not observed"

    while time.monotonic() < deadline:
        try:
            value = _read_openplc_input()
            last_observation = value
            if value == expected:
                expected_observed = True
                consecutive += 1
                if consecutive >= STABLE_READS:
                    return value
            else:
                consecutive = 0
        except (ConnectionError, ModbusException, OSError, RuntimeError) as error:
            last_observation = f"{type(error).__name__}: {error}"
            consecutive = 0
        time.sleep(POLL_INTERVAL)

    try:
        simulator_status = _send_control_command("status")
    except Exception as error:  # 仅用于补充 timeout 诊断。
        simulator_status = f"{type(error).__name__}: {error}"
    raise TimeoutError(
        f"waiting for OpenPLC %IW0={expected} for {STABLE_READS} stable reads "
        f"timed out; last={last_observation}; expected_observed={expected_observed}; "
        f"simulator_status={simulator_status!r}"
    )


def _restore_normal_state() -> None:
    """幂等取消 delay/fault，并恢复远端和 IEC observation 基线。"""
    delay_response = _send_control_command("delay_off")
    assert delay_response in {
        "OK state=normal delay_ms=0",
        "OK state=fault delay_ms=0",
    }, f"cleanup delay_off returned {delay_response!r}"

    fault_response = _send_control_command("fault_off")
    assert fault_response == "OK state=normal", (
        f"cleanup fault_off returned {fault_response!r}"
    )

    state, delay_ms = _parse_delay_status(_send_control_command("status"))
    assert (state, delay_ms) == ("normal", 0)
    _wait_for_remote_value(EXPECTED_VALUE, RECOVERY_TIMEOUT)
    _wait_for_openplc_input(EXPECTED_VALUE, RECOVERY_TIMEOUT)


def test_remote_response_timeout_zero_fills_and_recovers():
    """固定已表征的 terminal delayed-response 清零与恢复行为。

    范围仅限 OpenPLC Runtime v4.2.2、当前持久化 Remote Device 配置和
    pymodbus 3.11.2 simulator。5000 ms 是已验证的故障注入值，不代表
    Modbus 标准阈值，也不对精确清零或恢复时间作保证。
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
        _parse_delay_status(status)

        # Arrange：从任意 delay/fault 状态幂等恢复，再确认两端稳定基线。
        _restore_normal_state()
        remote_value = _wait_for_remote_value(EXPECTED_VALUE, NORMAL_TIMEOUT)
        openplc_value = _wait_for_stable_openplc_value(
            EXPECTED_VALUE,
            NORMAL_TIMEOUT,
        )
        print(f"arrange: remote HR0={remote_value}, OpenPLC %IW0={openplc_value}")

        # Act：制造已表征的 terminal response timeout，不断开 listener。
        delay_response = _send_control_command(f"delay_on {TERMINAL_DELAY_MS}")
        assert delay_response == (
            f"OK state=delayed delay_ms={TERMINAL_DELAY_MS}"
        )
        state, delay_ms = _parse_delay_status(_send_control_command("status"))
        assert (state, delay_ms) == ("delayed", TERMINAL_DELAY_MS)

        # Assert fault：只验证稳定 zero-fill，不断言精确 retry/latency。
        zero_value = _wait_for_stable_openplc_value(0, ZERO_TIMEOUT)
        print(
            "fault: "
            f"delay_ms={TERMINAL_DELAY_MS}, OpenPLC %IW0={zero_value} "
            f"stable_reads={STABLE_READS}"
        )

        # Act/Assert recovery：允许最初仍为 0，使用有界稳定读确认恢复。
        assert _send_control_command("delay_off") == "OK state=normal delay_ms=0"
        state, delay_ms = _parse_delay_status(_send_control_command("status"))
        assert (state, delay_ms) == ("normal", 0)
        restored_remote = _wait_for_remote_value(
            EXPECTED_VALUE,
            RECOVERY_TIMEOUT,
        )
        restored_openplc = _wait_for_stable_openplc_value(
            EXPECTED_VALUE,
            RECOVERY_TIMEOUT,
        )
        print(
            "recovery: "
            f"remote HR0={restored_remote}, OpenPLC %IW0={restored_openplc} "
            f"stable_reads={STABLE_READS}"
        )
    finally:
        original_error = sys.exception()
        cleanup_error = None
        if controller_reachable:
            try:
                _restore_normal_state()
            except Exception as error:  # cleanup 失败必须明确报告。
                cleanup_error = error

        if cleanup_error is not None:
            original_detail = ""
            if original_error is not None:
                original_detail = (
                    "; original test error: "
                    f"{type(original_error).__name__}: {original_error}"
                )
            raise RuntimeError(
                "terminal timeout regression cleanup failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
                f"{original_detail}"
            ) from cleanup_error
