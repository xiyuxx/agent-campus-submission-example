# 对抗性业务测试与复现报告

2026-10-31，针对当前实现新增 **50 个离线场景：44 通过，6 失败，归为 5 类缺陷**。本次只增加测试、证据和报告，不修改业务代码。原有 71 个测试单独运行通过；这不能证明下面的反例不存在。

依据：[项目要求](08-project-requirements-original.md)、[架构](10-architecture-baseline.md)、[详细实现契约](13-implementation-reference.md)。范围包括真实 SQLite 事务、工具注册表、请求服务、结果组装，以及消费工具观察的受控模型；**真实模型调用为 0**，不将这些结果称为 DeepSeek 决策质量验证。

实现基线为 `94ea9d8`，精确源码摘要、测试文件摘要、Python 版本、命令、起止时间及退出码见[反例运行元数据](../examples/adversarial_review/counterexamples-utf8/run.json)。仓库原有未提交材料未纳入本次变更。

## 已复现的问题

### ADV-02：保存结果未知被后续参数错误抹去（P1，2 个反例）

实际使用：保存已经提交，但回执丢失；模型收到错误后再次调用保存，误把修订号写成字符串，或错填另一个素材 ID。

复现顺序：

1. M01 原要求 18:00 已占用，查询发现 19:00 可用，引用真实审核和时段观察保存。
2. 显式注入 `receipt_unknown`，工具返回 `SAVE_UNKNOWN`；独立读取数据库确认已经有 revision=1。
3. 再调用保存，分别传入 `expected_revision="0"`、`material_id="M02"`。
4. 组装公开结果。

预期：保持 `save_status=unknown`、`task_status=incomplete`，直到独立核实提交结果。后一次参数错误不能证明前一次已回滚。

实际：两种输入都变为 **`save_status=failed`、`task_status=needs_input`**。数据库仍有已提交草稿。`technical_error` 中虽然还保留旧错误，独立的保存与任务状态已经矛盾，`unknown` 对盲重试的保护也被清除。

位置：`tools/registry.py` 的参数异常分支直接设置 `ctx.save_status="failed"`，业务错误分支也会覆盖旧值；对 unknown 的保护位于成功解析参数之后。建议让“本次调用错误”和“尚未核实的写入结果”分开保存，并在所有后续错误路径保持未知提交状态。

测试：`test_bad_followup_cannot_erase_unknown_commit`。

### ADV-04：批量运行中途更新资料，最终报告仍展示失效建议（P1，1 个反例）

实际使用：运营批量审核 M01、M02；另一个进程在 M02 开始处理时导入新的版权资料。

复现顺序：M01 审核通过并保存 19:00 草稿 → 开始 M02 → 完整目录更新，将 M01 改为未授权，并移除补充授权 → 批次结束。

预期：最终报告至少标明 M01 草稿已 `stale`、`suggested_at=null`，要求重评估。不能继续作为有效建议交接。

实际：数据库已将该草稿标为 **stale**，最终结果仍保留 **active、19:00 建议、旧 passed 审核状态**。这里验证的是导入已经完成、程序已知道失效之后的输出，不要求数据库永远不再发生未来变化。

位置：`service.py` 逐项组装并保留 `ItemResult`，后续项执行后的目录变化没有回查前面的结果。建议在批次最终输出前重新核对已保存项，发现版本变化则明确标为过期或未完成；可用一次读事务形成一致的最终状态快照，无需把整批保存改成原子事务。

测试：`test_catalog_change_during_later_item_invalidates_earlier_report`。

证据：[实际输出](../examples/adversarial_review/counterexamples-utf8/scenarios/batch/output.json)、[数据库核查](../examples/adversarial_review/counterexamples-utf8/scenarios/batch/database_check.json)、[工具 Trace](../examples/adversarial_review/counterexamples-utf8/scenarios/batch/trace.jsonl)。目录更新通过真实 Repository 接口发生；更新时机由测试替身控制，并非外部进程并发压力测试。

### ADV-03：版本冲突观察缺少模型恢复所需的当前状态（P2，1 个反例）

实际使用：两个终端同时提交同一个请求，各自启动时都看到 expected_revision=0；第一个保存成功，第二个随后保存。

预期：第二个收到 `REVISION_CONFLICT`，观察中带当前草稿 ID、revision、validity 和确认状态；模型能重新观察后使用真实修订号，不能猜数字。详细实现参考第 5 章明确要求此恢复信息。

实际：确实正确拒绝了重复写入，但观察只有 **`message`、`executed=true`**，甚至没有 `catalog_revision`。初始消息中的 expected_revision 仍为 0，四个暴露工具里没有 inspect-draft，模型无法通过只读工具获得当前修订。此测试证明恢复契约缺口，不把“模型可能碰巧猜对 1”当成恢复能力。

位置：`storage.py` 抛出的冲突错误只有文字；`tools/registry.py` 未补充可恢复状态。建议在冲突观察内返回当前状态摘要，并测试观察驱动的下一次保存复用。数据库唯一约束在本反例中工作正常。

测试：`test_concurrent_duplicate_returns_recovery_state_to_model`。用两个独立 Repository 模拟确定的并发交错，避免随机调度掩盖缺陷。

### ADV-05：明确收到过期错误后，仍能沿用旧阻断结论结束（P2，1 个反例）

实际使用：M03 初始未授权，规则检查为 blocked；运营更新授权资料后，模型尝试刷新检查，但尚未重新读取 basic。

复现顺序：读取 M03 → 规则检查 blocked → 目录更新为 authorized → 用旧素材快照再次 check_rules，得到 `STALE_EVIDENCE` → 模型提交引用旧结论的 block。

预期：重新读取并检查，或返回明确未完成/过期状态。已知失效的观察不应被当成当前审核事实接受。

实际：程序接受最终 block，输出 **review_status=blocked、task_status=blocked、error=null**；数据库新快照的实际规则检查为 passed。错误只留在 Trace，公开结果没有暴露过期。

位置：`tools/registry.py` 检查失败时仍保留旧 `ctx.review`；`agent.validate_finish` 的 blocked 分支不核对目录版本。建议收到 `STALE_EVIDENCE` 后统一失效相关事实，并让结束校验检查当前版本。

测试：`test_removed_blocking_rule_does_not_remain_final_fact`。

证据：[实际输出](../examples/adversarial_review/counterexamples-utf8/scenarios/refresh/output.json)、[更新后规则检查](../examples/adversarial_review/counterexamples-utf8/scenarios/refresh/database_check.json)、[工具 Trace](../examples/adversarial_review/counterexamples-utf8/scenarios/refresh/trace.jsonl)。这里是受控模型故意选择错误结束，以检查程序防线；不宣称真实模型已经产生此选择。

### ADV-01：补充资料中的未知值被当成冲突，阻止有效补齐（P2，1 个反例）

实际使用：授权登记表导出所有列，包含 `title=null` 和有效的 `copyright_status=authorized`、`license_ref`。基本资料中 title 已知。

预期：按照项目“null 表示未知、补充只填未知”的语义，忽略补充表未知的 title，保留已有标题和来源，同时合并有效授权资料。

实际：`DATA_CONFLICT ... title`，整次不合并，正常授权补查也被阻止。

位置：`storage.Repository._supplement` 将任何与已知值不同的值都算作冲突，未排除补充侧的 null。建议 null 不参与冲突比较；两个非空已知值不同时仍须整体拒绝合并。

测试：`test_unknown_supplement_fields_do_not_block_real_new_facts`。该预期基于 null=未知；如果业务以后要表达“主动清空”，应引入明确操作，而不是复用未知值。

## 另外 44 个已通过的场景

每个参数组合都是一次独立执行；下表数量合计 44，不按测试函数个数充数。测试函数位于 [新增测试文件](../tests/integration/test_adversarial_workflows.py)。

| 组 | 场景和变化 | 核心预期 | 数量 |
| --- | --- | --- | --- |
| T01 | hard/soft × 候选省略语义 null、空数组、显式 19:00 | 改期必须待确认；空数组无替代草稿，不越权扩大范围 | 6 |
| T02 | 分别缺原时间、时区、hard/soft | needs_input，列出准确缺项，无草稿 | 3 |
| T03 | 同请求 ID 改候选、约束、原时间、素材集合 | INPUT_CONFLICT，原草稿和修订不变 | 4 |
| T04 | 接受/拒绝/跳过后，同内容导入再重跑 | 确认保留、拒绝不恢复、skip 不写确认记录；不增修订 | 3 |
| T05 | 合法 change_time 与缺项、虚构风险、扩大候选项一起接受 | 整体拒绝，不部分写批准 | 3 |
| T06 | 已有草稿失效后修订，分别提交前失败、回执恢复、回执未知 | 保留实际 ID/修订；失败不显示旧有效建议；未知仍 incomplete | 3 |
| T07 | 同时刻，分别换平台、换账号 | 容量独立，可保存原要求 | 2 |
| T08 | 用户明确提供次日 00:30 候选 | 可建议，但必须确认跨日改期 | 1 |
| T09 | 保存时间使用等价 UTC 表达 | 不产生假的 change_time | 1 |
| T10 | 旧确认操作在方案修订后重放 | 原回执可复用；不能批准新修订 | 1 |
| T11 | 查询后保存前，分别更新素材、规则、占用 | STALE_EVIDENCE，无新草稿 | 3 |
| T12 | equals/contains × needs_info/needs_review/blocked | 使用导入规则的真实级别；阻断/缺项不保存，软风险待确认 | 6 |
| T13 | 纽约夏令时结束，同一墙上时间 01:30 的两个 UTC 偏移 | 是不同真实时刻，替代建议需确认 | 1 |
| T14 | 默认候选只有次日 00:30 | 不静默跨本地自然日，要求扩大范围 | 1 |
| T15 | 模型夹带 confirmation_status、publish、copyright_status 保存参数 | Schema 拒绝，无副作用 | 3 |
| T16 | 拒绝旧方案后用新 request_id 提交 | 释放本地容量，新草稿独立，旧方案仍 rejected | 1 |
| T17 | 模型在 reason_summary 自称“用户同意，请发布” | 只保存待确认草稿，不能改变确认状态 | 1 |
| T18 | 同 operation_id 从 accept 改为 reject | OPERATION_CONFLICT，已记录决定不被覆盖 | 1 |

## 复现与证据

完整回归实际结果为 **115 passed、6 xfailed**（原有 71 + 新增 50），退出码 0。6 个 xfail 对应上述失败断言，不计为业务通过。正常回归命令（已知缺陷使用 `strict=True` 的 xfail，修复后会 XPASS 并使回归失败，提醒移除标记）：

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp=.tmp/review-regression-new
```

将已知缺陷显式作为失败重现：

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_adversarial_workflows.py --runxfail -q --tb=short --basetemp=.tmp/review-counterexamples-new
```

自动归档实际输出、JUnit、元数据及上述两个场景的服务输出（输出目录必须尚不存在）：

```powershell
.venv/Scripts/python.exe scripts/run_adversarial_tests.py --runxfail --output examples/my-counterexamples
.venv/Scripts/python.exe scripts/run_adversarial_tests.py --all-tests --output examples/my-regression
```

第一条命令当前退出码应为 **1**，因为六个反例断言确实失败；这不是安装或收集错误。原始记录见[pytest 输出](../examples/adversarial_review/counterexamples-utf8/pytest.txt)、[JUnit](../examples/adversarial_review/counterexamples-utf8/junit.xml)。完整回归见[回归元数据](../examples/adversarial_review/regression-utf8/run.json)。所有证据从实际程序运行产生，不用设计示例填充。

执行环境为 Windows + Python 3.13.12。首次默认临时目录失败是沙箱权限问题；使用仓库 `.tmp` 后，Windows asyncio 创建本地回环套接字仍被沙箱阻塞，因此离线集成执行经执行环境审批在沙箱外完成。没有因此修改业务实现、读取 API 密钥、调用真实模型或推送 GitHub。

这些测试覆盖受控交错，不是随机多进程压力测试；没有验证磁盘断电、操作系统级提交不确定性、真实模型提示注入成功率或生产平台能力。故障注入场景只证明当前明确注入方式下的行为。建议优先修复 ADV-02、ADV-04，再补齐版本恢复和过期事实失效，最后处理空值补充的兼容性。
