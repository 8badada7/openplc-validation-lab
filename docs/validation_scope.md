# OpenPLC Modbus/TCP 系统验证范围

## 1. 文档目的

本文档定义 OpenPLC Modbus/TCP 系统可靠性与自动化验证工作的范围、目标和边界。验证活动面向系统行为，不以开发 PLC 产品或扩展 OpenPLC 功能为目标。

## 2. 被测对象

被测对象（System Under Test, SUT）为 OpenPLC，重点关注 OpenPLC Runtime 对外提供的 Modbus/TCP 通信行为，以及客户端、网络连接、Runtime 状态和 PLC 数据之间的交互。

当前 SUT 范围同时包括：

- OpenPLC Runtime 作为 Modbus TCP Server 时的通信行为；
- OpenPLC Runtime 作为 Modbus TCP Master/Client 时的远程设备通信行为；
- Remote Device 数据读取；
- Modbus 通信状态变化对 IEC 数据映射的影响。

每次实际测试应记录所用 OpenPLC 版本、运行配置、操作系统、通信参数和寄存器映射，避免将不同环境的结果混为一谈。

## 3. 主要接口

主要验证接口为 Modbus/TCP。计划覆盖 OpenPLC Runtime 当前支持的主要数据区域和常见功能码，包括：

- Coils
- Discrete Inputs
- Input Registers
- Holding Registers
- 对应的单点或多点读写请求
- Modbus 异常响应

具体地址范围、数据映射和可写权限以实际使用的 OpenPLC Runtime 版本及配置为准。

除 Modbus/TCP 外，测试环境可能使用以下接口辅助验证：

- Runtime API；
- Editor 连接接口；
- Runtime 日志。

这些接口仅用于测试管理、版本确认和证据采集，不改变 Modbus/TCP 作为主要被测通信接口的定位。

## 4. 验证目标

### 4.1 功能正确性

- 验证受支持的寄存器类型和功能码能够按配置完成读写。
- 验证请求地址、数量、数据值与响应内容一致。
- 验证只读与可写数据区域的行为符合接口定义。
- 验证异常响应不会被误判为成功结果。

### 4.2 通信可靠性

- 验证连接建立、连续请求和主动断开过程的稳定性。
- 验证重复连接与多轮读写不会产生持续性错误或陈旧连接。
- 记录超时、断线和通信失败时的客户端行为与 Runtime 可观测信息。

### 4.3 异常处理

- 验证越界地址、不支持的功能码、非法数量和格式异常请求的处理行为。
- 检查异常请求是否得到明确、可识别的失败结果。
- 检查单次异常是否影响后续合法请求，或导致 Runtime 崩溃、挂起和不可恢复状态。

### 4.4 连接恢复

- 验证连接丢失后通信相关组件能否识别中断，并验证恢复后的通信和数据状态。
- 验证 OpenPLC Runtime 停止并重新启动后能否重新建立连接。
- 验证恢复后的读写能力、数据状态和响应一致性。
- 记录恢复时间、重试次数和恢复过程中的错误信息。

### 4.5 回归测试

- 将有公开依据的已报告问题转化为可重复测试。
- 对官方声明已修复的问题验证修复行为，而不是直接采用发布说明作为测试结论。
- 固化测试输入、步骤、断言、日志和报告，支持在不同版本间重复执行。

## 5. 测试边界

- 当前以软件环境中的 OpenPLC Runtime 和 Modbus/TCP 客户端交互为主要测试边界。
- 测试判定基于协议响应、客户端状态、Runtime 状态、数据变化、日志和时间信息。
- 地址边界和超时阈值必须来自实际版本、配置或明确的测试前提，不在文档中预设未经核实的数值。
- 已报告问题、官方修复声明和本项目测试结果必须分别记录，不得相互替代。
- 在尚未执行测试时，只记录测试设计，不编写通过或失败结论。

## 6. 当前不测试的内容

- 不验证复杂真实硬件、现场 I/O 和完整工业产线。
- 不验证 Modbus/TCP 之外的其他工业协议。
- 不以 PLC 控制逻辑和控制算法本身作为验证重点；必要的控制逻辑仅作为测试夹具。
- 不开发或测试复杂 Web 前端、数据库平台、云平台和大模型功能。
- 不开展认证级安全评估、渗透测试、极限性能测试或正式功能安全认证。

## 7. 当前验证状态

截至当前阶段，项目已经完成：

- Modbus/TCP 基础通信验证；
- Remote Device 正常通信验证；
- connection loss fault injection；
- OpenPLC Runtime v4.1.9 historical stale-value behavior reproduction；
- OpenPLC Runtime v4.2.2 fixed behavior regression validation。

详细实验过程和证据见：

- `validation_basis.md`；
- `issue_691_validation.md`。
