"""验证已运行的 OpenPLC 正常 Modbus/TCP 读写基线。"""

import time

from pymodbus.client import ModbusTcpClient


def test_modbus_baseline():
    # setup：创建客户端并连接；前提是 PLC 已运行且配置了对应寄存器。
    client = ModbusTcpClient("127.0.0.1", port=5020, timeout=3, retries=0)
    try:
        # assert 检查实际结果是否符合预期；不满足时让测试失败并说明原因。
        assert client.connect(), "无法连接 Modbus Server 127.0.0.1:5020"

        # 已验证的映射：1024 -> %MW0 (command)，1025 -> %MW1 (response)。
        written = client.write_register(1024, 100, device_id=1)
        assert not written.isError(), f"写入 address=1024, value=100 失败：{written}"

        # 等待约 0.1 秒，让 PLC 扫描逻辑更新 response；这不是扫描周期测量。
        time.sleep(0.1)
        result = client.read_holding_registers(1024, count=2, device_id=1)
        assert not result.isError(), f"读取 address=1024, count=2 失败：{result}"
        assert len(result.registers) == 2, f"预期返回两个寄存器，实际为：{result.registers}"

        command, response = result.registers
        assert command == 100, f"command (address=1024) 预期为 100，实际为 {command}"
        assert response == 101, f"response (address=1025) 预期为 101，实际为 {response}"
    finally:
        # teardown：无论测试通过、断言失败还是发生异常，都关闭客户端连接。
        client.close()
