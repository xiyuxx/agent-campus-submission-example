# 测试目录约定

2026-10-31 当前复验：合并查询及后续动态工具、偏好、有限冲突恢复共同完整 --runxfail 为 **356 passed、0 failed/error/xfail**（基线 1e16f93）。包含合并查询 18 项、完整查询入口的独立 oracle 128 项；Ruff 两项通过。七组实际服务场景、源码摘要与已有 live 的只读核对见 [合并查询索引](../examples/automatic_slots_v3/README.md)。下方 342/324 等是各历史阶段的结果。

2026-10-31 最新：RT-01/RT-02/ADV-01 已修复，原 15 个 xfail 全部移除且断言保留。完整 --runxfail 为 **324 passed，0 failed/error/xfail**，其中原 289 项、新增 35 项；Ruff check/format --check 通过。见 [修复记录](../docs/22-defect-fix-results.md) 和 [最终证据](../examples/defect_fixes/README.md)。以下数字为历史执行结果；当前测试应通过，不再预期反例失败。

2026-10-31 再次复测新增 150 项（含最近节点 128 个占用组合穷举）；完整结果为 274 passed、15 xfailed，新增失败属于规则审核失效、异常响应协议处理两类问题，旧 ADV-01 仍失败。详见 [测试设计、覆盖与限制](../docs/20-retest-and-test-design.md) 和 [实际证据](../examples/retest_review/README.md)。可用 `scripts/run_adversarial_tests.py --test-path tests/integration/test_retest_edge_cases.py --runxfail --output examples/my-new-retest` 单独重现新增失败；输出目录需不存在。

当前已有可执行的契约、存储、工具循环、CLI 和适配器离线测试；真实模型效果需单独验证。

2026-10-31 本地身份与双向最近节点变更后，完整离线回归实际为 **138 passed、1 xfailed**。新增 18 项检查，覆盖模型消息无内部身份、已回传证据、两侧最近/跨日、原时间优先和修订恢复。ADV-02/03/04/05 已转为通过的普通回归，只有 ADV-01 仍使用 strict xfail；下面 44/6 和历史报告描述变更前的执行结果，不能作为当前未修复数量。

- `unit/`：参数校验、工具返回规范、预算计数、终止状态等确定性逻辑。
- `integration/`：完整 CLI 和 Agent 循环，使用受控模型替身与合成工具。
- `live/`：显式开启的真实模型评估，默认测试不产生付费调用。

2026-10-31 新增 50 个对抗性业务场景，44 个通过、6 个反例失败，归为 5 类缺陷。已知缺陷保持期望断言并使用 strict xfail；xfail 不是通过，也不表示业务问题已经修复。详见 [复现报告](../docs/18-adversarial-test-review.md) 与 [原始证据索引](../examples/adversarial_review/README.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp=.tmp/tests-new
.venv/Scripts/python.exe -m pytest tests/integration/test_adversarial_workflows.py --runxfail -q --basetemp=.tmp/counterexamples-new
```

第二条用于显式运行历史反例断言，修复后的当前版本应返回退出码 0。Windows 沙箱若阻塞 asyncio 本地套接字，需要使用获准的正常执行环境；不得把环境挂起算成业务缺陷。不要为凑覆盖率编写仅复述实现的测试。


## 2026-10-31 决策改进实际记录

新增 test_decision_improvements.py 14项，覆盖可选/阻塞缺项、补查枚举、观察送达、偏好与默认摘要兼容、重复复用、原时间优先、抢槽恢复与耗尽、旧查询失效及本地审核接口绑定。完整356项通过；live另行显式启用，不在默认测试中访问API。

详见 [决策契约](../docs/24-agent-decision-improvements.md) 与 [实际证据](../examples/decision_improvements_v4_1/README.md)。
