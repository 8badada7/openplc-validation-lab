"""固化 OpenPLC Runtime v4.2.2 当前 Holding Register 边界行为。"""

import sys
import time

from pymodbus.client import ModbusTcpClient
from pymodbus.constants import ExcCodes
from pymodbus.exceptions import ModbusException


MODBUS_HOST = "127.0.0.1"
MODBUS_PORT = 5020
DEVICE_ID = 1
UNKNOWN_DEVICE_ID = 2
BASELINE_ADDRESS = 1024
LAST_MAPPED = 8191
FIRST_UNMAPPED = 8192
POLL_TIMEOUT = 5.0
POLL_INTERVAL = 0.05


def _assert_normal_baseline(client: ModbusTcpClient) -> list[int]:
    result = client.read_holding_registers(
        BASELINE_ADDRESS,
        count=2,
        device_id=DEVICE_ID,
    )
    assert not result.isError(), f"正常 baseline 读取失败：{result}"
    assert len(result.registers) == 2, (
        f"正常 baseline 预期返回两个寄存器，实际为 {result.registers}"
    )

    command, response = result.registers
    # PLC 变量是 16-bit UINT；0xFFFF + 1 时按 UINT 范围回绕为 0。
    expected_response = (command + 1) & 0xFFFF
    assert response == expected_response, (
        "正常 baseline 应满足 response == command + 1；"
        f"实际 command={command}, response={response}"
    )
    return result.registers


def _wait_for_fixture_values(
    client: ModbusTcpClient,
    expected_command: int,
    timeout: float = POLL_TIMEOUT,
) -> list[int]:
    """有界轮询，等待 command 生效且 response 满足 UINT fixture 关系。"""
    deadline = time.monotonic() + timeout
    last_result = "尚未读取"

    while True:
        try:
            result = client.read_holding_registers(
                BASELINE_ADDRESS,
                count=2,
                device_id=DEVICE_ID,
            )
            registers = getattr(result, "registers", None)
            if (
                not result.isError()
                and isinstance(registers, list)
                and len(registers) == 2
                and registers[0] == expected_command
                and registers[1] == ((registers[0] + 1) & 0xFFFF)
            ):
                return registers
            last_result = repr(result)
        except (ModbusException, OSError) as error:
            last_result = f"{type(error).__name__}: {error}"

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"等待 command={expected_command} 生效超时；最后结果：{last_result}"
            )
        time.sleep(POLL_INTERVAL)


def test_unmapped_holding_register_is_zero_filled():
    """验证未映射地址补 0，且请求后正常通信仍然可用。"""
    client = ModbusTcpClient(
        MODBUS_HOST,
        port=MODBUS_PORT,
        timeout=3,
        retries=0,
    )
    try:
        assert client.connect(), "无法连接 Modbus Server 127.0.0.1:5020"
        _assert_normal_baseline(client)

        result = client.read_holding_registers(
            FIRST_UNMAPPED,
            count=1,
            device_id=DEVICE_ID,
        )
        assert not result.isError(), f"未映射地址读取返回错误：{result}"
        assert result.registers == [0], (
            f"address={FIRST_UNMAPPED} 预期零填充 [0]，实际为 {result.registers}"
        )

        _assert_normal_baseline(client)
    finally:
        client.close()


def test_holding_register_read_across_mapped_boundary():
    """验证从最后一个映射地址读取到首个未映射地址的当前行为。"""
    client = ModbusTcpClient(
        MODBUS_HOST,
        port=MODBUS_PORT,
        timeout=3,
        retries=0,
    )
    try:
        assert client.connect(), "无法连接 Modbus Server 127.0.0.1:5020"

        result = client.read_holding_registers(
            LAST_MAPPED,
            count=2,
            device_id=DEVICE_ID,
        )
        assert not result.isError(), f"跨 mapped/unmapped 边界读取失败：{result}"
        assert len(result.registers) == 2, (
            f"跨边界读取预期返回两个寄存器，实际为 {result.registers}"
        )
        # registers[0] 是有效映射地址 8191，其值不应固定为 0。
        assert result.registers[1] == 0, (
            f"未映射地址 {FIRST_UNMAPPED} 预期补 0，实际为 {result.registers[1]}"
        )

        _assert_normal_baseline(client)
    finally:
        client.close()


def test_unknown_device_id_returns_gateway_no_response():
    """验证当前仅注册 device_id=1 时，ID 2 的异常响应和连接连续性。"""
    client = ModbusTcpClient(
        MODBUS_HOST,
        port=MODBUS_PORT,
        timeout=3,
        retries=0,
    )
    try:
        assert client.connect(), "无法连接 Modbus Server 127.0.0.1:5020"
        _assert_normal_baseline(client)

        result = client.read_holding_registers(
            BASELINE_ADDRESS,
            count=2,
            device_id=UNKNOWN_DEVICE_ID,
        )
        assert result.isError(), f"未注册 device_id=2 意外返回正常响应：{result}"
        assert result.dev_id == UNKNOWN_DEVICE_ID, (
            f"异常响应预期回显 device_id=2，实际为 {result.dev_id}"
        )
        assert result.function_code == 0x83, (
            f"FC03 异常响应的 function code 预期为 0x83，实际为 {result.function_code:#x}"
        )
        assert result.exception_code == ExcCodes.GATEWAY_NO_RESPONSE, (
            "未注册 device_id 的异常码预期为 GATEWAY_NO_RESPONSE，"
            f"实际为 {result.exception_code}"
        )
        assert result.exception_code == 11

        # 同一 client 再次完成正常读取，证明异常响应没有破坏后续通信。
        _assert_normal_baseline(client)
    finally:
        client.close()


def test_uint_command_wraps_at_16_bit_boundary():
    """验证当前 UINT fixture 在 16-bit 最大值处的已确认回绕行为。"""
    client = ModbusTcpClient(
        MODBUS_HOST,
        port=MODBUS_PORT,
        timeout=3,
        retries=0,
    )
    original_command = None
    try:
        assert client.connect(), "无法连接 Modbus Server 127.0.0.1:5020"
        original_command = _assert_normal_baseline(client)[0]

        expected_results = {
            0: [0, 1],
            65534: [65534, 65535],
            65535: [65535, 0],
        }
        for value, expected in expected_results.items():
            written = client.write_register(
                BASELINE_ADDRESS,
                value,
                device_id=DEVICE_ID,
            )
            assert not written.isError(), f"写入 UINT value={value} 失败：{written}"

            registers = _wait_for_fixture_values(client, value)
            print(f"UINT value={value}, registers={registers}")
            assert registers == expected, (
                f"UINT value={value} 预期为 {expected}，实际为 {registers}"
            )

            if value == 65535:
                # OpenPLC Runtime v4.2.2 当前 UINT fixture 已确认在 16-bit 边界回绕。
                command, response = registers
                assert command == 65535
                assert response == 0
    finally:
        original_error = sys.exception()
        restore_error = None
        try:
            if original_command is not None:
                restored_write = client.write_register(
                    BASELINE_ADDRESS,
                    original_command,
                    device_id=DEVICE_ID,
                )
                assert not restored_write.isError(), (
                    f"恢复 original_command={original_command} 写入失败：{restored_write}"
                )
                restored = _wait_for_fixture_values(client, original_command)
                expected_response = (original_command + 1) & 0xFFFF
                assert restored == [original_command, expected_response], (
                    f"状态恢复失败：预期 {[original_command, expected_response]}，"
                    f"实际为 {restored}"
                )
        except Exception as error:  # cleanup 失败必须明确报告。
            restore_error = error
        finally:
            client.close()

        if restore_error is not None:
            original_detail = ""
            if original_error is not None:
                original_detail = (
                    "；原始测试错误："
                    f"{type(original_error).__name__}: {original_error}"
                )
            raise RuntimeError(
                "UINT boundary test 状态恢复失败："
                f"{type(restore_error).__name__}: {restore_error}"
                f"{original_detail}"
            ) from restore_error


# 以上断言只固化 OpenPLC Runtime v4.2.2 与当前 mapping 的 characterization
# behavior。device_id 测试同时固定 pymodbus 3.11.2 与 OpenPLC 当前仅注册
# device_id=1 时的表现；不代表所有 Modbus/TCP server 都必须返回 code 11。
# UINT 回绕测试仅针对当前 UINT fixture，不泛化到其他 PLC 数据类型。
