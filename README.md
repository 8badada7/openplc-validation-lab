# OpenPLC Validation Lab

基于 Python 的 OpenPLC Modbus/TCP 系统可靠性与自动化验证项目。

## Current Status

已建立基于 OpenPLC Runtime、Modbus/TCP、Python 和 pytest 的基础系统验证链路。

目前已完成：

- 基于 Docker 的 OpenPLC Runtime v4.2.2 测试环境
- PLC 编译、上传、运行与停止/启动基础验证
- 项目级 Modbus/TCP Server 配置与服务监听验证
- `%MW0` / `%MW1` 与 Holding Register `1024` / `1025` 的地址映射验证
- Python + pymodbus 端到端读写验证
- pytest 正常通信基线测试，当前测试结果：`2 passed`

当前正在向连接中断与恢复、协议边界与异常请求、故障注入，以及真实 OpenPLC Issue 的复现与回归验证扩展。

## Validation Roadmap

- Modbus/TCP 功能与通信行为验证
- 连接中断与恢复测试
- 协议边界与异常请求测试
- 基于真实 OpenPLC Issue 的故障复现与回归验证
- 自动化测试、日志和测试证据管理
