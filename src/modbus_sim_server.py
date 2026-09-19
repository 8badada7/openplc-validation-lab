"""可控的最小 Modbus/TCP 远程设备模拟器。"""

import asyncio

from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import ModbusTcpServer


MODBUS_HOST = "0.0.0.0"
MODBUS_PORT = 15020
CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 15021
DEVICE_ID = 1
HR0_VALUE = 1234


def _create_context() -> ModbusServerContext:
    # ModbusDeviceContext 会把线上的 address=0 转成内部地址 1，因此数据块
    # 从 1 开始。每次恢复服务都重建 context，使 HR0 确定为 1234。
    holding_registers = ModbusSequentialDataBlock(1, [HR0_VALUE] + [0] * 9)
    device = ModbusDeviceContext(hr=holding_registers)
    return ModbusServerContext(devices={DEVICE_ID: device}, single=False)


class ControllableModbusDevice:
    """保持控制器存活，并独立启停真正的 Modbus 服务。"""

    def __init__(self) -> None:
        self._server: ModbusTcpServer | None = None
        self._lock = asyncio.Lock()

    async def status(self) -> str:
        async with self._lock:
            state = self._state_unlocked()
        return (
            f"OK controller=alive state={state} "
            f"modbus={MODBUS_HOST}:{MODBUS_PORT} device_id={DEVICE_ID} hr0={HR0_VALUE}"
        )

    async def fault_on(self) -> str:
        """幂等关闭 Modbus listener 及已有连接，但保留控制通道。"""
        changed = False
        async with self._lock:
            if self._server is not None:
                server = self._server
                self._server = None
                await server.shutdown()
                changed = True
        if changed:
            print("FAULT ON: Modbus service unavailable", flush=True)
        return "OK state=fault"

    async def fault_off(self) -> str:
        """幂等恢复 Modbus 服务，并把 HR0 重置为确定值 1234。"""
        changed = False
        async with self._lock:
            if self._server is None:
                server = ModbusTcpServer(
                    _create_context(),
                    address=(MODBUS_HOST, MODBUS_PORT),
                )
                await server.serve_forever(background=True)
                self._server = server
                changed = True
        if changed:
            print("FAULT OFF: Modbus service restored", flush=True)
        return "OK state=normal"

    async def close(self) -> None:
        async with self._lock:
            if self._server is not None:
                server = self._server
                self._server = None
                await server.shutdown()

    def _state_unlocked(self) -> str:
        if self._server is not None and self._server.is_active():
            return "normal"
        return "fault"


async def _handle_control_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    device: ControllableModbusDevice,
) -> None:
    try:
        raw_command = await reader.readline()
        command = raw_command.decode("utf-8", errors="replace").strip().lower()

        if command == "status":
            response = await device.status()
        elif command == "fault_on":
            response = await device.fault_on()
        elif command == "fault_off":
            response = await device.fault_off()
        else:
            response = "ERROR unknown_command"

        writer.write((response + "\n").encode("utf-8"))
        await writer.drain()
    except (ConnectionError, OSError):
        # 控制客户端提前断开不应终止 simulator。
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def run_server() -> None:
    """同时运行 loopback 控制通道和可启停的 Modbus 服务。"""
    device = ControllableModbusDevice()
    control_server: asyncio.AbstractServer | None = None

    try:
        # Modbus 监听 0.0.0.0，供 Docker Desktop 容器通过
        # host.docker.internal 访问；控制通道严格限制在 loopback。
        await device.fault_off()
        control_server = await asyncio.start_server(
            lambda reader, writer: _handle_control_client(reader, writer, device),
            CONTROL_HOST,
            CONTROL_PORT,
        )

        print("Simulated Modbus/TCP device starting", flush=True)
        print(f"modbus: {MODBUS_HOST}:{MODBUS_PORT}", flush=True)
        print(f"control: {CONTROL_HOST}:{CONTROL_PORT}", flush=True)
        print(f"device_id: {DEVICE_ID}", flush=True)
        print(f"HR0: {HR0_VALUE}", flush=True)

        await control_server.serve_forever()
    finally:
        if control_server is not None:
            control_server.close()
            await control_server.wait_closed()
        await device.close()
        print("Simulated Modbus/TCP device stopped", flush=True)


def main() -> None:
    """启动模拟器，并允许 Ctrl+C 正常关闭两个 listener。"""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("Shutdown requested by Ctrl+C", flush=True)


if __name__ == "__main__":
    main()
