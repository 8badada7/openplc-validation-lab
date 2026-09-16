# OpenPLC Modbus/TCP 验证依据

## 1. 文档目的

本文档记录 OpenPLC Modbus/TCP 系统验证的依据及其证据等级。验证项分为公开报告的缺陷、官方修复声明和项目主动设计的工程测试场景。三类信息必须独立记录，不能把修复声明或测试设想写成已经验证的结果。

## 2. 证据分类

### 2.1 已报告缺陷

存在可追溯的公开 Issue，且 Issue 明确描述了实际观察到的异常行为。已报告缺陷可以作为故障复现和回归测试来源，但本项目仍需独立执行测试后才能形成自己的结论。

### 2.2 修复声明

官方 Release Notes 或变更记录将某个问题列为修复项。修复声明说明维护方发布了相关变更，但不等同于本项目已经验证修复有效。

### 2.3 尚未验证的工程测试场景

根据协议、系统状态和可靠性风险主动设计的测试场景。此类场景用于发现问题；在获得可重复证据前，不得称为 OpenPLC Bug。

## 3. 一手资料

### 3.1 OpenPLC Editor GitHub Issue #691

- 来源：[Modbus/TCP: Incorrect Behavior When the Connection Is Lost · Issue #691](https://github.com/Autonomy-Logic/openplc-editor/issues/691)
- 证据类型：已报告缺陷。
- 公开描述：通信正常时数据读写正常；连接丢失后，配置为 reset-to-zero 的值仍被保留，没有按预期清零。

### 3.2 OpenPLC Editor v4.2.11 Release Notes

- 来源：[OpenPLC Editor v4.2.11 Release Notes](https://github.com/Autonomy-Logic/openplc-editor/releases/tag/v4.2.11)
- 证据类型：修复声明。
- 公开描述：发布说明将 “Modbus/TCP: incorrect behavior when the connection is lost (issue #691)” 列为修复项。
- 使用限制：该记录只能证明官方声明包含相关修复，不能替代独立回归测试。

### 3.3 OpenPLC Runtime Modbus TCP Slave 官方文档

- 来源：[OpenPLC Runtime Plugin System — Modbus TCP Slave](https://github.com/Autonomy-Logic/openplc-runtime/blob/main/core/src/drivers/README.md)
- 证据类型：接口与测试范围依据。
- 文档描述的主要数据区域与功能码：
  - Coils：FC01、FC05、FC15（0x0F）。
  - Discrete Inputs：FC02。
  - Holding Registers：FC03、FC06、FC16（0x10）。
  - Input Registers：FC04。
- 使用限制：官方文档用于确定候选测试范围，不代表本项目已经验证当前实现与文档完全一致。

## 4. 验证项

### VB-001：连接丢失后的 reset-to-zero 行为

- 类型：已报告缺陷对应的回归验证。
- 缺陷依据：OpenPLC Editor Issue #691。
- 修复依据：OpenPLC Editor v4.2.11 Release Notes 中的修复声明。
- 待验证内容：建立正常通信并形成非零值，触发连接丢失，检查配置为 reset-to-zero 的值是否在明确的判定窗口内清零；恢复连接后检查通信与数据状态。
- 当前状态：尚未由本项目执行。不能声称 v4.2.11 的修复已经通过验证。

### VB-002：Runtime 停止、重启与连接恢复

- 类型：主动设计的可靠性测试场景。
- 测试依据：Runtime 生命周期变化会中断客户端与服务端之间的通信状态，需要验证中断识别和恢复路径。
- 待验证内容：客户端识别连接中断的方式、超时与错误信息、Runtime 恢复后的重连能力、恢复时间，以及恢复后的数据正确性。
- 当前状态：尚未执行，不是已确认的 OpenPLC Bug。

### VB-003：Modbus 地址边界与非法请求

- 类型：主动设计的协议测试场景。
- 测试依据：OpenPLC Runtime Modbus TCP Slave 官方文档描述的数据区域和功能码。
- 待验证内容：
  - 配置范围内的合法地址。
  - 起始地址、结束地址和跨边界的多点请求。
  - 越界地址。
  - 不支持的功能码、非法数量、截断或格式错误的请求。
  - Coils、Discrete Inputs、Input Registers 和 Holding Registers 的读写权限及响应差异。
  - 异常请求之后的正常通信是否仍可继续。
- 当前状态：尚未执行，不代表上述场景当前一定存在缺陷。

## 5. 当前结论边界

| 验证项 | 已报告缺陷 | 官方修复声明 | 本项目已验证 |
| --- | --- | --- | --- |
| VB-001：连接丢失后的 reset-to-zero 行为 | 是，Issue #691 | 是，v4.2.11 Release Notes | 否 |
| VB-002：Runtime 停止、重启与连接恢复 | 否 | 不适用 | 否 |
| VB-003：Modbus 地址边界与非法请求 | 否 | 不适用 | 否 |

后续只有在记录测试环境、版本、配置、步骤、实际响应、日志和可重复结果后，才能更新“本项目已验证”状态。
