# 改动后复测与测试设计说明

2026-10-31，本轮测试基线为 `74cdcac`。按更新后的 [本地身份绑定与双向最近节点契约](19-local-model-boundary.md) 测试，保留默认候选允许跨日、模型不填写业务 ID/修订的行为。本轮只增加测试、归档能力和说明，不修改业务实现，不调用真实模型，不推送远程。

## 实际结果

| 执行范围 | 实际结果 | 说明 |
| --- | --- | --- |
| 改动后的既有测试 139 项 | 138 passed、1 xfailed | 上轮 ADV-02/03/04/05 的五个失败断言当前通过；ADV-01 仍失败 |
| 本轮新增 150 项，启用 `--runxfail` | 136 passed、14 failed、0 error | 14 个失败属于下面 2 类问题，不是 14 类独立 bug |
| 全部 289 项，保留 strict xfail | 274 passed、15 xfailed | 15=旧 ADV-01 1 项+本轮规则失效 6 项+协议边界 8 项；xfail 不算通过 |

实际输出、JUnit、源码和测试文件摘要见 [证据索引](../examples/retest_review/README.md)。新增测试位于 [test_retest_edge_cases.py](../tests/integration/test_retest_edge_cases.py)。这里的通过/失败是确定性工程测试结果，不是生产故障率或模型任务成功率。

## 当前仍可复现的 bug

### RT-01：只改审核规则、重读相同素材后，旧审核被重新当作有效事实（P2，6 个失败断言）

最小序列：读取素材 → 检查规则 → 运营导入新规则，素材内容不变 → 模型重新 read_material basic → 模型沿用旧结论结束。

实际复现了两种方向：旧 passed 在新规则已要求 blocked 时仍显示 passed；旧 blocked/needs_info/needs_review 在新规则已不命中时仍被保留。对于旧 blocked，最终结束校验会接受 block，完整服务输出 `review_status=blocked`、`task_status=blocked`、`error=null`，而独立读取新规则检查为 passed。

原因：`tools/registry.py` 第 99 行附近只在素材 facts_hash 改变时清空 review；目录版本改变时只清空 slots。规则变更不会改变素材摘要。重新读取后 snapshot 已是新版本，而 review 仍是旧版本。`agent.validate_finish` 第 28 行附近只比较 snapshot 与当前目录版本，遗漏 review 与 snapshot 的版本关系；`result.assemble_result` 同样无法识别这个组合。

本轮新增 4 个审核状态组合、1 个直接结束校验、1 个完整 Agent 服务运行，均失败；另有 4 个对照用例证明显式重新执行 check_rules 后可以获得正确新结论。因此问题定位在事实失效和结束边界，不是规则运算器。

建议：规则/目录版本变化时使旧审核失效；结束和输出同时检查 review.catalog_revision 与当前有效素材/目录版本。**本轮未证明能绕过保存事务写入未授权草稿**：保存层仍有独立的新规则核对；这里已经证明的是审核输出和结束判断错误。

证据：[实际错误输出](../examples/retest_review/counterexamples-final/scenarios/rule-refresh/output.json)、[新规则独立核查](../examples/retest_review/counterexamples-final/scenarios/rule-refresh/database_check.json)、[Trace](../examples/retest_review/counterexamples-final/scenarios/rule-refresh/trace.jsonl)。规则通过真实导入接口更新，测试只控制更新时间和模型动作。此变体补充了上轮 ADV-05：上轮覆盖“旧快照检查收到 STALE_EVIDENCE”，本轮覆盖“重新 basic 后素材相同、审核却未失效”。

### RT-02：异常模型响应结构逃逸协议边界，一次可恢复异常直接终止任务（P2，8 个失败断言）

构造返回 HTTP 200 且 JSON 可解析，但 message 为 null/数组/字符串，或 tool_calls 元素为 null/数字，或 function 为 null/数组，共 7 种结构。预期适配器统一产生 MODEL_PROTOCOL，并由现有有界纠错处理，不执行工具。

实际：这些结构在 `models/deepseek.py` 第 65、93、96 行附近调用 `.get()` 时抛出 AttributeError，未转换为协议错误。完整服务用例进一步模拟“只有第一次 message=null，后续响应完全正常”：实际只请求 1 次，立即 `failed/INTERNAL_ERROR/AttributeError`，没有进入允许的协议纠错，也没有保存草稿。

另有 3 个对照结构（根对象数组、空 choices、缺角色）被正确归为 MODEL_PROTOCOL。建议在每层 `.get()` 前校验对象/数组元素类型，继续使用统一协议错误；不要把所有内部异常无区别地变成可重试错误。

证据：[服务实际输出](../examples/retest_review/counterexamples-final/scenarios/provider-recovery/output.json)、[运行次数](../examples/retest_review/counterexamples-final/scenarios/provider-recovery/run.json)、[全部异常断言](../examples/retest_review/counterexamples-final/pytest.txt)。使用 HTTPX MockTransport 注入合法 JSON 的畸形结构，未访问网络，不宣称 DeepSeek 在真实调用中已经返回过这些结构。

### ADV-01：补充表未知值仍被当作冲突（P2，既有 1 个失败断言）

补充资料导出 title=null，同时提供真实授权字段；基本标题已知。当前仍因 title 的未知值返回 DATA_CONFLICT，整次不合并有效授权资料。该问题继续由既有 strict xfail 实际执行覆盖；详见 [原始复现报告](18-adversarial-test-review.md)。

## 用例如何设计

先从需求写出可核验的约束，再找会使这些约束失效的输入或动作顺序，而不是以当前实现的输出作为期望。

1. **业务约束**：改期不等于用户批准；保存必须有真实提交事实；一份逻辑请求只能有一个草稿；已知过期的审核不能当作当前事实；模型不能指定本地身份；预算耗尽必须明确未完成。
2. **输入分组与边界**：把“省略/null/空数组”分开，把 hard/soft、四种审核状态、不同平台账号、相同时刻不同表达、前后跨日分别测试。对相邻边界采用成对输入，避免只有一个正常样例。
3. **状态序列与交错**：不仅测试“保存一次”，也测试“查询后被抢槽”“保存后回执丢失再传错参数”“确认后修订再重放旧确认”“规则更新后重读相同素材”。大量 bug 发生在两个合法操作组合之后。
4. **独立预期值**：最近节点测试用 datetime 集合减去占用集合，再分别求原时刻之前的最大值和之后的最小值；不调用被测 SQL 查询或复用其排序代码来计算预期。保存测试独立查数据库及修订历史，不只相信工具返回文字。
5. **受控故障和负向模型动作**：强制回执丢失、畸形 HTTP 响应、伪造授权和沿用旧结论，验证程序会阻断或如实停止。受控模型负责稳定触发错误选择，不用于证明真实模型质量。
6. **修复后保留反例并扩大邻域**：同一最小反例通过，不表示同类问题已全部修复。本轮就在 ADV-05 邻近序列中发现“重新 basic、素材不变、规则已变”的遗漏。known defect 用 strict xfail 标记；`--runxfail` 能显示真实失败，修复后 XPASS 则提醒移除标记。

## 覆盖了哪些情况，是否足够

新增 150 项的实际构成如下；128 个占用组合是一个有限模型的穷举，不是 128 条互不相关的业务需求。

| 组 | 变化 | 执行数量 | 实际结果 |
| --- | --- | --- | --- |
| 双向最近节点 | 6 个节点，前后各 3 个，含前一天/后一天；全部 2^6 种占用；默认/显式两种范围；输入逆序、显式重复节点 | 128 | 全部通过 |
| 原时间独立查询 | 默认节点为空、原时间空闲 | 1 | 通过 |
| 规则变更后失效 | 四种旧审核状态+结束边界+完整服务 | 6 | 全部失败，同属 RT-01 |
| 显式重新审核对照 | 四种旧审核状态，刷新后再执行 check_rules | 4 | 全部通过 |
| 畸形响应与对照 | 7 种畸形结构+3 种已正确处理结构 | 10 | 3 通过、7 失败 |
| 单次畸形后恢复 | 只坏第一次，后续提供正常受控响应 | 1 | 失败，同属 RT-02 |

结合既有 139 项，核心验收、确认/幂等、事务失败、模型工具权限、版本、调用预算、体积限制、CLI 和 HTTP 协议都有实际测试；适合当前阶段发现和防止核心逻辑回归。**仍不能据此声称覆盖完毕或程序无 bug**，本次新反例本身就说明只重跑旧用例不足。

尚未充分覆盖：更多真实进程持续并发 seed/run/confirm 的随机交错；进程强杀、真实磁盘满或断电下的恢复；多年大量节点、长期运行的资源和延迟；真实模型多次采样、文案提示注入、需求歧义和实际网络故障。这些与已有两写者测试、故障注入、体积上限测试分别是不同验证层次。

没有将“全局最优排期”或“无条件禁止跨日”等当前契约明确不承诺的能力算作 bug，也没有用代码行覆盖率替代业务判断。

## 低概率、小样本与极端事件

包含这些事件：提交已成功而回执丢失、恰好在查询与保存之间被占用、旧批准重放、夏令时出现两个 01:30、跨本地午夜、模型夹带授权、第一次 HTTP 响应结构异常。本轮新增的是规则变更后重读相同素材，以及一次畸形响应后的恢复边界。

它们是**人为控制触发时机的低概率路径测试**，不是随机抽样。某个反例只要满足合法前提并能稳定复现一次，就足以证明对应 bug；但一次通过不能证明永不失败，也不能从 14/150 估算生产故障概率。生产发生频率需要日志、实际输入分布和更多真实运行数据；这里没有虚构这些信息。

## 复现命令

```powershell
# 新增 150 项，直接显示已知失败；当前应退出 1
.venv/Scripts/python.exe scripts/run_adversarial_tests.py --test-path tests/integration/test_retest_edge_cases.py --runxfail --output examples/retest-my-counterexamples

# 全部 289 项；strict xfail 不算业务通过，输出目录必须尚不存在
.venv/Scripts/python.exe scripts/run_adversarial_tests.py --all-tests --test-path tests/integration/test_retest_edge_cases.py --output examples/retest-my-regression
```

执行使用 Python 3.13.12；Windows asyncio 在沙箱内创建本地套接字的限制沿用前次，经执行环境审批在沙箱外运行离线集成测试。未改动已有未提交 README 和其他证据目录。建议先处理规则审核失效，再完善模型响应结构校验与已有 null 补充冲突。
