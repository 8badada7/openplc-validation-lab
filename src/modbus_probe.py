"""单次读写 probe：验证已确认的地址映射，不扫描或自动更换地址。"""

import time

import pymodbus
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException


def main():
    # holding register 是可读写的 16 位寄存器；address 是协议中的零基地址。
    # 当前项目配置中，1024/1025 已实测对应 %MW0/%MW1。
    command_address = 1024
    response_address = 1025
    device_id = 1
    print(f"pymodbus version: {pymodbus.__version__}")
    print(f"addresses used: command={command_address}, response={response_address}")
    print(f"device_id: {device_id}")

    # ModbusTcpClient 是通过 TCP 向设备发送 Modbus 请求的客户端。
    client = ModbusTcpClient("127.0.0.1", port=5020, timeout=3, retries=0)
    try:
        connected = client.connect()
        print(f"connection result: {connected}")
        if not connected:
            print("Modbus exception/error: connection failed; read/write skipped")
            return 1

        # FC03：从起始地址读取两个连续 holding registers，即 1024 和 1025。
        before = client.read_holding_registers(command_address, count=2, device_id=device_id)
        # isError() 判断返回对象是否表示错误；TCP 连通并不代表 Modbus 请求成功。
        if before.isError():
            print(f"Modbus exception/error (read before): {before}")
            return 1
        print(f"values before: command={before.registers[0]}, response={before.registers[1]}")

        # FC06：向单个 holding register 写入 command=100。
        written = client.write_register(command_address, 100, device_id=device_id)
        print(f"write result: {written}")
        if written.isError():
            print(f"Modbus exception/error (write): {written}")
            return 1

        # 给 PLC 约 0.1 秒处理数据；实际 scan cycle 尚未测量。
        time.sleep(0.1)
        after = client.read_holding_registers(command_address, count=2, device_id=device_id)
        if after.isError():
            print(f"Modbus exception/error (read after): {after}")
            return 1
        print(f"values after: command={after.registers[0]}, response={after.registers[1]}")
        print("expected values after: command=100, response=101")
        print("Modbus exception/error: none")
        return 0
    except (ModbusException, OSError) as error:
        print(f"Modbus exception/error: {type(error).__name__}: {error}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
