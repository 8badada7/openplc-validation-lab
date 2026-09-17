# OpenPLC Validation Lab

基于 Python 的 OpenPLC Modbus/TCP 系统可靠性与自动化验证项目。

## Current Status

已建立基于 OpenPLC Runtime、Modbus/TCP、Python 和 pytest 的系统验证基础链路。

目前已完成：

- 基于 Docker 的 OpenPLC Runtime v4.2.2 测试环境
- PLC 编译、上传、运行与基础 smoke validation
- Modbus/TCP Server 配置、监听与寄存器映射验证
- Python + pymodbus 端到端读写验证
- pytest 正常通信 baseline 自动化测试
- PLC Stop/Start 服务中断与自动恢复验证
- 连续两轮 Stop/Start recovery stability 验证
- 基于 Runtime HTTPS API + JWT 的 PLC 状态控制
- Modbus Holding Register mapped/unmapped 边界行为验证与自动化
- 未注册 device_id 的异常响应与后续连接可用性验证
- UINT 16-bit 数据边界与回绕行为验证
- 适用测试场景中的 bounded polling 与状态恢复

当前自动化测试结果：`8 passed`

下一阶段将开展进一步的故障注入，并推进真实 OpenPLC Issue 的复现与回归验证，重点包括 Issue #691。

## Validation Roadmap

- Modbus/TCP 功能与通信行为验证
- 连接中断与恢复测试
- 协议边界与异常请求测试
- 基于真实 OpenPLC Issue 的故障复现与回归验证
- 自动化测试、日志和测试证据管理
