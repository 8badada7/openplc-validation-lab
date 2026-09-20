# OpenPLC Issue #691 Validation Report

## 1. Background

OpenPLC Editor Issue #691 描述了 Modbus/TCP 连接丢失后 reset-to-zero 行为未按预期生效、IEC 映射值仍保留最后成功值的问题。官方 Release Notes 随后声明包含相关修复。

Issue 描述和 Release Notes 分别属于公开缺陷报告与官方修复声明。本项目使用可控远程 Modbus/TCP 设备和独立观测通道，对历史行为与当前修复后行为进行了独立验证。

## 2. Validation Environment

### Historical candidate

- OpenPLC Runtime v4.1.9

### Fixed behavior validation

- OpenPLC Runtime v4.2.2

### Test components

- OpenPLC Runtime；
- Python Modbus simulator；
- pytest controller；
- Modbus observation channel。

Python simulator 提供确定的远端 Holding Register 值，并支持受控停止和恢复 Modbus 服务。OpenPLC Runtime 作为 Modbus TCP Master/Client 读取远端数据，观测通道通过 OpenPLC Modbus Slave 读取对应 IEC 映射值。

## 3. Test Configuration

- Remote Device：Python Modbus Simulator
- Protocol：Modbus/TCP
- Host：`host.docker.internal`
- Port：`15020`
- Device ID：`1`
- Function：FC03
- Offset：`0`
- Length：`1`
- IEC Location：`%IW0`
- Cycle Time：`100 ms`
- Error Handling：`set-to-zero`
- Observation：FC04 address `0`

## 4. Historical Behavior Reproduction

在 OpenPLC Runtime v4.1.9 和当前测试配置下，验证过程如下。

### Normal

- Remote HR0：`1234`
- IEC `%IW0`：`1234`

### Fault

- 受控停止 simulator 的 Modbus 服务，使远端连接丢失。

### Evidence

- 真实 FC03 请求失败，确认远端 Modbus 服务不可用；
- Runtime `MODBUS_MASTER` 日志记录读取或连接失败；
- 通过独立 FC04 observation channel 持续读取 IEC `%IW0`。

### Result

- 故障窗口内 20/20 次观测均保持 `1234`；
- 未观察到 `%IW0` 清零。

### Conclusion

在 OpenPLC Runtime v4.1.9 和当前测试配置下，已复现连接丢失后 IEC 映射值保持最后成功值的 historical stale-value behavior。

## 5. Fixed Behavior Validation

在 OpenPLC Runtime v4.2.2 和对应 Remote Device 配置下，正式回归测试验证了以下行为。

### Normal

- Remote HR0：`1234`
- IEC `%IW0`：`1234`

### Fault

- 远端 Modbus/TCP 连接丢失；
- IEC `%IW0` 变为 `0`。

### Recovery

- 恢复远端 Modbus 服务；
- OpenPLC 恢复通信；
- IEC `%IW0` 恢复为 `1234`。

该结果构成当前测试配置下的 fixed behavior regression validation。

## 6. Evidence Summary

### Historical reproduction evidence

- FC03 connection failure；
- Runtime `MODBUS_MASTER` log；
- FC04 IEC observation。

### Recovery evidence

- `fault_off` command；
- Remote HR0 restored；
- IEC value restored。

## 7. Evidence Boundary

- Issue #691 的问题描述不是本项目发现，而是公开缺陷报告；
- Release Notes 是官方修复声明，不是本项目测试证据；
- Runtime v4.1.9 的结果仅适用于该版本和当前测试配置，不代表全部旧版本；
- Runtime v4.2.2 的结果仅适用于该版本和当前测试配置，不代表所有新版本；
- 本报告不构成完整 Modbus 协议认证或对所有 OpenPLC 配置的行为保证。

## 8. Related Files

- `tests/test_modbus_master_fault_recovery.py`
- `src/modbus_sim_server.py`
- `src/modbus_fault_characterization.py`
