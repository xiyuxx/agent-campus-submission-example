# 改动后复测证据

2026-10-31，基线 `74cdcac`，真实模型调用为 0。设计、覆盖解释和复现步骤见 [复测报告](../../docs/20-retest-and-test-design.md)。

| 运行 | 实际结果 | 元数据 |
| --- | --- | --- |
| 既有 139 项 | 138 passed、1 xfailed | [baseline/run.json](baseline/run.json) |
| 新增 150 项，`--runxfail` | 136 passed、14 failed、0 error | [counterexamples-final/run.json](counterexamples-final/run.json) |
| 完整 289 项 | 274 passed、15 xfailed | [regression-final/run.json](regression-final/run.json) |

每组含实际 pytest 文本、JUnit 和运行元数据；最终两组包含测试文件摘要清单。pytest 文本/XML 只规范化换行和行尾空白。scenarios 中的服务 input/output/run/summary/trace 均为实际执行产生，database_check 是独立数据库核查；规则更新时间、模型动作和 HTTP 异常由测试控制，不冒充真实模型或真实网关故障。

14 个新增失败对应 2 类 bug，旧 xfail 对应 ADV-01；没有把参数化变体数量说成独立缺陷数量。正常完整回归的退出码 0 不代表 15 个 xfail 已修复。原有历史证据保持原始版本。
