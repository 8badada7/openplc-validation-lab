"""验证 PLC Stop/Start 后 Modbus/TCP 通信能够恢复。"""

import os
import sys
import time

import pytest
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from src.openplc_api import OpenPLCClient


MODBUS_HOST = "127.0.0.1"
MODBUS_PORT = 5020
DEVICE_ID = 1
COMMAND_ADDRESS = 1024
RESPONSE_ADDRESS = 1025
RUNNING = "STATUS:RUNNING"
STOPPED = "STATUS:STOPPED"
TRANSITIONING = "STATUS:TRANSITIONING"


def _assert_modbus_baseline() -> None:
    client = ModbusTcpClient(MODBUS_HOST, port=MODBUS_PORT, timeout=3, retries=0)
    try:
        assert client.connect(), "无法连接 Modbus Server 127.0.0.1:5020"

        written = client.write_register(COMMAND_ADDRESS, 100, device_id=DEVICE_ID)
        assert not written.isError(), f"写入 address=1024, value=100 失败：{written}"

        # 等待约 0.1 秒，让 PLC 至少完成若干 scan cycle 并更新 response。
        time.sleep(0.1)
        result = client.read_holding_registers(
            COMMAND_ADDRESS,
            count=2,
            device_id=DEVICE_ID,
        )
        assert not result.isError(), f"读取 address=1024, count=2 失败：{result}"
        assert len(result.registers) == 2, f"预期两个寄存器，实际为：{result.registers}"

        command, response = result.registers
        assert command == 100, f"command 预期为 100，实际为 {command}"
        assert response == 101, f"response 预期为 101，实际为 {response}"
    finally:
        client.close()


def _assert_modbus_request_unavailable() -> None:
    client = ModbusTcpClient(MODBUS_HOST, port=MODBUS_PORT, timeout=3, retries=0)
    try:
        connected = client.connect()
        print(f"interruption connect result: {connected}")
        try:
            result = client.read_holding_registers(
                COMMAND_ADDRESS,
                count=2,
                device_id=DEVICE_ID,
            )
        except (ModbusException, OSError) as error:
            print(f"interruption request error: {type(error).__name__}: {error}")
            return

        if result.isError():
            print(f"interruption Modbus error response: {result}")
            return

        registers = getattr(result, "registers", None)
        if not isinstance(registers, list) or len(registers) < 2:
            print(f"interruption invalid response: {result}")
            return

        pytest.fail(
            "PLC 已 STOPPED，但 FC03 意外返回有效数据："
            f"command={registers[0]}, response={registers[1]}"
        )
    finally:
        client.close()


def _wait_for_modbus_ready(timeout: float = 5.0, poll_interval: float = 0.2) -> None:
    deadline = time.monotonic() + timeout
    last_result = "尚未尝试"

    while True:
        client = ModbusTcpClient(MODBUS_HOST, port=MODBUS_PORT, timeout=1, retries=0)
        try:
            connected = client.connect()
            if not connected:
                last_result = "connect() returned False"
            else:
                result = client.read_holding_registers(
                    COMMAND_ADDRESS,
                    count=2,
                    device_id=DEVICE_ID,
                )
                registers = getattr(result, "registers", None)
                if not result.isError() and isinstance(registers, list) and len(registers) >= 2:
                    return
                last_result = repr(result)
        except (ModbusException, OSError) as error:
            last_result = f"{type(error).__name__}: {error}"
        finally:
            client.close()

        if time.monotonic() >= deadline:
            pytest.fail(f"等待 Modbus 恢复超时；最后结果：{last_result}")
        time.sleep(poll_interval)


def _wait_for_stable_plc_status(
    api: OpenPLCClient,
    timeout: float = 5.0,
    poll_interval: float = 0.2,
) -> str:
    deadline = time.monotonic() + timeout
    status = api.get_status()
    while status == TRANSITIONING:
        if time.monotonic() >= deadline:
            raise TimeoutError("cleanup 等待 PLC 结束 TRANSITIONING 状态超时")
        time.sleep(poll_interval)
        status = api.get_status()
    return status


def _restore_plc_running(api: OpenPLCClient) -> None:
    status = _wait_for_stable_plc_status(api)
    if status != RUNNING:
        api.start_plc()
        api.wait_for_status(RUNNING)


def _login_from_environment() -> OpenPLCClient:
    username = os.getenv("OPENPLC_USERNAME")
    password = os.getenv("OPENPLC_PASSWORD")
    if not username or not password:
        pytest.skip(
            "需要设置 OPENPLC_USERNAME 和 OPENPLC_PASSWORD 才能运行 PLC recovery test"
        )

    api = OpenPLCClient()
    try:
        api.login(username, password)
    except Exception:
        api.close()
        raise
    return api


def _cleanup_api(api: OpenPLCClient, original_error: BaseException | None) -> None:
    cleanup_error = None
    try:
        # cleanup：即使测试中途失败，也尽最大努力让 SUT 回到 RUNNING。
        _restore_plc_running(api)
    except Exception as error:  # cleanup 必须保留并报告恢复失败信息。
        cleanup_error = error
    finally:
        api.close()

    if cleanup_error is not None:
        original_detail = ""
        if original_error is not None:
            original_detail = (
                "；原始测试错误："
                f"{type(original_error).__name__}: {original_error}"
            )
        raise RuntimeError(
            "PLC cleanup 失败，SUT 可能未恢复为 RUNNING："
            f"{type(cleanup_error).__name__}: {cleanup_error}"
            f"{original_detail}"
        ) from cleanup_error


def _run_recovery_cycle(api: OpenPLCClient, cycle_message: str | None = None) -> None:
    if cycle_message is not None:
        print(cycle_message)

    initial_status = api.get_status()
    assert initial_status == RUNNING, f"PLC 初始状态应为 {RUNNING}，实际为 {initial_status}"

    # Act / Assert interruption：停止 PLC 后，真正的 FC03 请求必须不可用。
    assert api.stop_plc() == "STOP:OK"
    assert api.wait_for_status(STOPPED) == STOPPED
    _assert_modbus_request_unavailable()

    # Act / Assert recovery：启动 PLC，以状态和 FC03 轮询判断通信就绪。
    assert api.start_plc() == "START:OK"
    assert api.wait_for_status(RUNNING) == RUNNING
    _wait_for_modbus_ready()
    _assert_modbus_baseline()


def test_modbus_recovers_after_plc_stop_start():
    api = _login_from_environment()
    try:
        # Arrange：确认 PLC 初始通信正常。
        _wait_for_modbus_ready()
        _assert_modbus_baseline()
        _run_recovery_cycle(api)
    finally:
        _cleanup_api(api, sys.exception())


def test_modbus_recovery_is_repeatable():
    api = _login_from_environment()
    try:
        for cycle in range(2):
            _run_recovery_cycle(api, f"recovery cycle {cycle + 1}/2")
    finally:
        _cleanup_api(api, sys.exception())
