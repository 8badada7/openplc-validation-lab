# Test Matrix

本矩阵对应 OpenPLC Validation Lab v1.1 已实现的验证能力。当前完整环境的验证结果为 `11 passed`；所有结果仅适用于已记录的 OpenPLC Runtime 版本、配置和外部 fixtures。本矩阵不是完整 Modbus 协议认证。

| # | Area | Test | Scenario | Expected Result |
| ---: | --- | --- | --- | --- |
| 1 | Smoke | `test_smoke` | 执行最小 pytest sanity check。 | 基础断言成功，确认 pytest 能执行测试。 |
| 2 | Normal Communication | `test_modbus_baseline` | FC06 向 HR1024（`%MW0`）写入 `100`，PLC logic 更新后以 FC03 读取 HR1024/1025。 | 返回 `[100, 101]`，证明 `command + 1` 数据路径正常。 |
| 3 | Boundary / Negative | `test_unmapped_holding_register_is_zero_filled` | 在正常 baseline 前后读取首个未映射 HR8192。 | HR8192 返回 `[0]`，且后续通信正常；这是当前 OpenPLC characterization behavior，不是 Modbus 标准要求。 |
| 4 | Boundary / Negative | `test_holding_register_read_across_mapped_boundary` | 一次 FC03 从 mapped HR8191 跨到 unmapped HR8192。 | 返回两项，第二项为 `0`；随后正常 baseline 仍有效。 |
| 5 | Boundary / Negative | `test_unknown_device_id_returns_gateway_no_response` | 对未注册 device ID `2` 发送 FC03，再使用正常 device ID `1`。 | 收到 `0x83`、`0x0B` / Gateway No Response；后续正常请求成功。 |
| 6 | Boundary / Negative | `test_uint_command_wraps_at_16_bit_boundary` | 依次写入 `0`、`65534`、`65535`。 | 得到 `[0,1]`、`[65534,65535]`、`[65535,0]`，验证当前 UINT fixture 的 16-bit wrap。 |
| 7 | PLC Lifecycle | `test_modbus_recovers_after_plc_stop_start` | Runtime API Stop PLC → FC03 不可用 → Start PLC。 | API 达到 STOPPED/RUNNING；恢复后 HR1024/1025 为 `[100,101]`。 |
| 8 | PLC Lifecycle | `test_modbus_recovery_is_repeatable` | 连续执行两轮完整 PLC Stop/Start recovery。 | 两轮均验证真实请求中断和通信/数据恢复。 |
| 9 | Remote Device Fault | `test_remote_disconnect_zero_fills_and_recovers` | `fault_on` → remote unavailable → `fault_off`。 | `%IW0` 从 `1234` 稳定清零为 `0`，恢复后稳定为 `1234`。 |
| 10 | Runtime Lifecycle | `test_runtime_recovers_after_container_restart` | `docker restart openplc-runtime`，并观察真实中断。 | API 恢复为 v4.2.2，`StartedAt` 改变，FC04 稳定返回 `%IW0=1234`；不等同于 crash/power loss。 |
| 11 | Response Timeout | `test_remote_response_timeout_zero_fills_and_recovers` | `delay_on 5000` 保持 listener 可用并耗尽当前 retry budget，再执行 `delay_off`。 | `%IW0` 稳定为 `0`，解除延迟后稳定恢复 `1234`；`5000 ms` 仅为已验证故障注入值。 |

## Stateful Test Cleanup

- `test_uint_command_wraps_at_16_bit_boundary`：保存并恢复 original command，恢复失败会明确报告。
- `test_modbus_recovers_after_plc_stop_start`：在 `finally` 中尽力恢复 PLC RUNNING 并关闭 API session。
- `test_modbus_recovery_is_repeatable`：任一轮失败后仍尽力恢复 PLC RUNNING。
- `test_remote_disconnect_zero_fills_and_recovers`：执行 `fault_off`，恢复 remote HR0 和 `%IW0=1234`。
- `test_runtime_recovers_after_container_restart`：必要时重新启动 current container，并恢复 simulator、remote HR0 和 `%IW0`。
- `test_remote_response_timeout_zero_fills_and_recovers`：执行 `delay_off` 和 `fault_off`，恢复 simulator normal、remote HR0 和 `%IW0=1234`。

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
