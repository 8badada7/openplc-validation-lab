# Test Matrix

本矩阵对应 OpenPLC Validation Lab v1.1 已实现的验证能力。当前完整环境的验证结果为 `11 passed`；所有结果仅适用于已记录的 OpenPLC Runtime 版本、配置和外部 fixtures。本矩阵不是完整 Modbus 协议认证。

| # | Test Area | Test | Action / Fault | Main Oracle | Recovery / Cleanup |
| ---: | --- | --- | --- | --- | --- |
| 1 | Smoke | `test_smoke` | 执行最小 pytest sanity check。 | 基础断言成功，确认 pytest 能执行测试。 | 不改变外部状态。 |
| 2 | Normal Communication | `test_modbus_baseline` | 使用 FC06 向 HR1024（`%MW0`）写入 command `100`，等待 PLC logic 更新后使用 FC03 读取 HR1024/1025。 | 返回 `[100, 101]`，证明 command 经 PLC `command + 1` 逻辑产生 response。 | 始终关闭 Modbus client；fixture 保留确定状态 `100/101`。 |
| 3 | Boundary / Negative | `test_unmapped_holding_register_is_zero_filled` | 在正常 baseline 前后读取当前首个未映射 Holding Register 8192。 | 请求不是 error，HR8192 返回 `[0]`，且后续正常通信仍有效；这是当前项目已表征的 OpenPLC 行为，不是 Modbus 标准要求。 | 只读请求并关闭 client，不改变 PLC 数据。 |
| 4 | Boundary / Negative | `test_holding_register_read_across_mapped_boundary` | 使用一次 FC03 从 mapped HR8191 跨到 unmapped HR8192，读取两个寄存器。 | 返回两项，第二项对应 HR8192 且为 `0`；随后正常 baseline 仍有效。 | 只读请求并关闭 client。 |
| 5 | Boundary / Negative | `test_unknown_device_id_returns_gateway_no_response` | 使用未注册 device ID `2` 发送 FC03，随后在同一 client 上恢复使用正常 device ID `1`。 | 收到 Modbus ExceptionResponse：device ID `2`、function code `0x83`、exception code `0x0B` / Gateway No Response；后续正常请求成功。 | 关闭 client；不修改 PLC 数据或 server 配置。 |
| 6 | Boundary / Negative | `test_uint_command_wraps_at_16_bit_boundary` | 依次向 command 写入 `0`、`65534`、`65535`，通过有界轮询读取 command/response。 | 分别得到 `[0, 1]`、`[65534, 65535]`、`[65535, 0]`，验证当前 UINT fixture 的 16-bit `command + 1` 回绕。 | 保存并恢复 original command；恢复失败会明确报告；关闭 client。 |
| 7 | PLC Lifecycle | `test_modbus_recovers_after_plc_stop_start` | 通过 Runtime API Stop PLC，发送真实 FC03 确认请求不可用，再 Start PLC 并等待通信恢复。 | API 状态达到 STOPPED/RUNNING；停止期间 FC03 无有效数据；恢复后 HR1024/1025 为 `[100, 101]`。 | `finally` 中尽力将 PLC 恢复为 RUNNING 并关闭 API session。该测试不重启 Docker container。 |
| 8 | PLC Lifecycle | `test_modbus_recovery_is_repeatable` | 连续执行两轮完整 PLC Stop → request failure → Start → recovery。 | 两轮均分别满足停止状态、真实 FC03 失败、启动状态和 `[100, 101]` 数据恢复，不是单轮测试的重复别名。 | 任一轮失败后仍尽力恢复 PLC RUNNING，并关闭 API session。 |
| 9 | Remote Device Fault | `test_remote_disconnect_zero_fills_and_recovers` | `fault_on` 使 simulator 的远端 Modbus 服务不可用，确认真实 FC03 失败；随后执行 `fault_off`。 | 正常状态 remote HR0 / `%IW0` 为 `1234`；connection-loss 期间 `%IW0` 连续稳定为 `0`；恢复后连续稳定为 `1234`。 | 尽力执行 `fault_off`，并恢复 remote HR0 和 `%IW0` 到 `1234`；cleanup failure 可见。 |
| 10 | Runtime Lifecycle | `test_runtime_recovers_after_container_restart` | 对 current Runtime 执行 `docker restart openplc-runtime`，并要求实际观察到 container、API 或 FC04 的中断。 | restart 成功且 `StartedAt` 改变；Runtime API 恢复为 v4.2.2；FC04 连续稳定读取 `%IW0=1234`。该结果不等同于 crash 或 host power-loss recovery。 | 确保 simulator 为 normal；必要时重新启动 current container；恢复 remote HR0 和 `%IW0` 到 `1234`。 |
| 11 | Response Timeout | `test_remote_response_timeout_zero_fills_and_recovers` | `delay_on 5000` 保持 service/listener 可用但延迟目标 FC03 响应，使当前 retry budget 耗尽；随后执行 `delay_off`。 | delayed 状态确认后 `%IW0` 连续稳定为 `0`；解除延迟后 remote HR0 和 `%IW0` 连续稳定恢复为 `1234`。`5000 ms` 仅是当前故障注入值。 | 幂等执行 `delay_off` 和 `fault_off`，恢复 simulator normal、remote HR0 和 `%IW0=1234`；cleanup failure 可见。 |

## Coverage Summary

当前已覆盖：

- normal Modbus/TCP communication；
- protocol and boundary characterization；
- PLC service lifecycle recovery；
- Remote Device connection-loss behavior and recovery；
- Runtime container lifecycle restart recovery；
- terminal delayed-response timeout behavior and recovery；
- state restoration for tests that alter PLC, simulator, or Runtime state。

## Current Boundaries

当前不覆盖：

- complete Modbus protocol certification；
- all function codes and address spaces；
- multi-device topology；
- network-level packet loss, corruption, or reordering；
- performance, load, stress, or long-duration soak testing；
- crash or host power-loss equivalence；
- hardware-in-the-loop（HIL）；
- all OpenPLC Runtime versions and configuration combinations。

## Test Design Notes

- 业务结果优先于 TCP connect-only 检查：连接成功不代表 Modbus request 或 PLC 数据路径已经可用。
- 故障注入前先建立 known-good baseline，确保后续状态变化具有可判定性。
- 异步状态变化使用基于 `time.monotonic()` 的 bounded polling，避免无限等待和系统时钟调整影响。
- 对 zero-fill 和恢复等收敛过程使用连续稳定读取，避免将单次瞬态值误判为最终状态。
- 外部 fixture 确实不可用时测试可以 skip；fixture 已建立后的协议、控制或业务行为不匹配必须 fail。
- 会改变 PLC、simulator 或 Runtime 状态的测试使用 best-effort cleanup，并明确暴露恢复失败。
