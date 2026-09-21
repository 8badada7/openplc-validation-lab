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
MIN_DELAY_MS = 100
MAX_DELAY_MS = 10000


class DelayState:
    """维护延迟故障状态，并使旧 generation 的等待请求失效。"""

    def __init__(self) -> None:
        self.state = "normal"
        self.delay_ms = 0
        self.generation = 0

    def set_delay(self, delay_ms: int) -> bool:
        if self.state == "delayed" and self.delay_ms == delay_ms:
            return False
        self._transition("delayed", delay_ms)
        return True

    def set_normal(self) -> bool:
        if self.state == "normal" and self.delay_ms == 0:
            return False
        self._transition("normal", 0)
        return True

    def set_fault(self) -> bool:
        if self.state == "fault" and self.delay_ms == 0:
            return False
        self._transition("fault", 0)
        return True

    def _transition(self, state: str, delay_ms: int) -> None:
        self.generation += 1
        self.state = state
        self.delay_ms = delay_ms


class DelayedModbusDeviceContext(ModbusDeviceContext):
    """仅对目标 FC03 HR0 请求施加非阻塞响应延迟。"""

    def __init__(self, delay_state: DelayState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._delay_state = delay_state

    async def async_getValues(self, func_code, address, count=1):
        if func_code == 3 and address == 0 and count == 1:
            state = self._delay_state
            generation = state.generation
            delay_ms = state.delay_ms

            if state.state == "delayed" and delay_ms > 0:
                # asyncio.sleep 只挂起当前请求 coroutine，不阻塞共享 event loop，
                # 因此 control listener 在延迟期间仍能处理 status/delay_off。
                await asyncio.sleep(delay_ms / 1000.0)

                # delay_off/fault_on 等状态切换会递增 generation。旧请求醒来
                # 后取消自身，使旧 transaction 不会在恢复阶段发送迟到响应。
                if (
                    state.generation != generation
                    or state.state != "delayed"
                ):
                    raise asyncio.CancelledError

        return self.getValues(func_code, address, count)


def _create_context(delay_state: DelayState) -> ModbusServerContext:
    # ModbusDeviceContext 会把线上的 address=0 转成内部地址 1，因此数据块
    # 从 1 开始。每次恢复服务都重建 context，使 HR0 确定为 1234。
    holding_registers = ModbusSequentialDataBlock(1, [HR0_VALUE] + [0] * 9)
    device = DelayedModbusDeviceContext(delay_state, hr=holding_registers)
    return ModbusServerContext(devices={DEVICE_ID: device}, single=False)


class ControllableModbusDevice:
    """保持控制器存活，并独立启停真正的 Modbus 服务。"""

    def __init__(self) -> None:
        self._server: ModbusTcpServer | None = None
        self._lock = asyncio.Lock()
        self._delay_state = DelayState()

    async def status(self) -> str:
        async with self._lock:
            state = self._state_unlocked()
            delay_ms = self._delay_state.delay_ms
        return (
            f"OK state={state} delay_ms={delay_ms} controller=alive "
            f"modbus={MODBUS_HOST}:{MODBUS_PORT} device_id={DEVICE_ID} hr0={HR0_VALUE}"
        )

    async def fault_on(self) -> str:
        """幂等关闭 Modbus listener 及已有连接，但保留控制通道。"""
        changed = False
        async with self._lock:
            self._delay_state.set_fault()
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
            self._delay_state.set_normal()
            if self._server is None:
                server = ModbusTcpServer(
                    _create_context(self._delay_state),
                    address=(MODBUS_HOST, MODBUS_PORT),
                )
                await server.serve_forever(background=True)
                self._server = server
                changed = True
        if changed:
            print("FAULT OFF: Modbus service restored", flush=True)
        return "OK state=normal"

    async def delay_on(self, delay_ms: int) -> str:
        """幂等启用目标 FC03 的非阻塞响应延迟。"""
        changed = False
        async with self._lock:
            if self._server is None or not self._server.is_active():
                return "ERROR cannot enable delay while state=fault"
            changed = self._delay_state.set_delay(delay_ms)
        if changed:
            print(f"DELAY ON: FC03 HR0 response delayed by {delay_ms} ms", flush=True)
        return f"OK state=delayed delay_ms={delay_ms}"

    async def delay_off(self) -> str:
        """幂等取消延迟，并使旧 generation 的 pending request 失效。"""
        changed = False
        async with self._lock:
            if self._server is None or not self._server.is_active():
                self._delay_state.set_fault()
                return "OK state=fault delay_ms=0"
            changed = self._delay_state.set_normal()
        if changed:
            print("DELAY OFF: normal FC03 response restored", flush=True)
        return "OK state=normal delay_ms=0"

    async def close(self) -> None:
        async with self._lock:
            if self._server is not None:
                server = self._server
                self._server = None
                await server.shutdown()

    def _state_unlocked(self) -> str:
        if self._server is None or not self._server.is_active():
            return "fault"
        return self._delay_state.state


async def _handle_control_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    device: ControllableModbusDevice,
) -> None:
    try:
        raw_command = await reader.readline()
        command = raw_command.decode("utf-8", errors="replace").strip().lower()
        parts = command.split()

        if command == "status":
            response = await device.status()
        elif command == "fault_on":
            response = await device.fault_on()
        elif command == "fault_off":
            response = await device.fault_off()
        elif parts and parts[0] == "delay_on":
            if len(parts) != 2:
                response = "ERROR delay_on requires one integer milliseconds argument"
            else:
                try:
                    delay_ms = int(parts[1])
                except ValueError:
                    response = "ERROR delay_ms must be an integer"
                else:
                    if not MIN_DELAY_MS <= delay_ms <= MAX_DELAY_MS:
                        response = (
                            f"ERROR delay_ms must be between "
                            f"{MIN_DELAY_MS} and {MAX_DELAY_MS}"
                        )
                    else:
                        response = await device.delay_on(delay_ms)
        elif command == "delay_off":
            response = await device.delay_off()
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
