# 需求与验收

当前交付为素材审核与发布排期 Agent，使用 CLI、原生单 Agent 与本地合成资料。2026-11-01 已实施 [分享审核修复](26-review-remediation.md)，当前392项离线与8条live均有记录；实际结果见 [评估](03-evaluation.md) 和 [本轮审核](25-submission-audit.md)。用户再次核对的题目标题为“素材审核与发布排期 Agent”，未提供独立数字题号，不能把排列顺序冒充正式编号。

## 用户、目标与范围

内容团队需要找出待处理素材，识别资料缺口、规则风险及发布冲突，得到可由负责人确认的排期草稿。素材 ID 表示指定版本、平台和账号的一次发布单元。审核对象是提供的元数据、文案及规则，不识别真实视频或核验真实版权。

输入包括素材及补充资料、审核规则、已有排期、预设时间节点，以及包含 request_id、material_id、发布时间要求的 JSON。每次 1–3 项逐项处理；同批部分成功保留，不承诺批次最优或整体原子性。

输出1.1新增proposed_plan，保留合法的未保存建议及其失效/待重查状态；draft和顶层suggested_at仍描述持久化状态，未保存建议不能confirm。输出逐项提供审核状态、风险原因、原始要求、建议时段、确认状态、待办、草稿保存状态、ID/修订、任务状态和证据引用。仅保存本地草稿，未接入真实发布或平台预约。

## 不可越过的边界

- 原始 hard/soft 时间要求保留；偏离原时刻一律产生 change_time 待确认项。偏好不是批准，模型不能代用户同意。
- 最低可审核资料为type/title，视频图片还需media_ref，文字还需caption；规则证据明确列出最低规则。缺必要资料返回 needs_input；明确阻断返回 blocked。软风险可以保存待确认草稿，不能用普通确认绕过阻断。
- query_slots 在一个读事务检查原时间；冲突时自动返回合法范围内前后各一个最近可用节点，允许跨日。显式 [] 不回退，省略/null 使用平台账号预设节点。模型消费观察后推荐。
- 原要求及候选必须在未来；过去hard要求请求重新指定，保存与接受确认复核时间。历史回放显式记录业务参考时间。
- 同 request_id + material_id 仅一份草稿；相同方案复用 ID 和修订，当前确认保留。原输入改变需新 request_id；默认新请求若撞上同素材版本/平台/账号的有效未来草稿则阻止创建，返回related_drafts；用户显式new_publication才允许再次发布，仍不替代旧请求。
- 资料、规则或占用改变后重新评估；方案修订使旧确认失效。查询和保存排除自身，但计入其他请求的有效草稿。
- confirm 绑定具体草稿及 revision，来自用户显式输入，不调用模型。拒绝只适用于有效方案的未决可确认项，同请求重跑不能恢复已拒绝方案。
- 保存前事务复核依据和容量。明确失败报告 failed；提交结果无法核实报告 unknown/incomplete，禁止盲目重试或虚报成功。
- 每项模型/工具各最多 16 次、180 秒；整批最多 540 秒，临时失败最多额外重试一次，协议反馈最多两次，保存冲突重查最多两次。预算耗尽明确 incomplete。

详细契约以 [实现参考](13-implementation-reference.md)、[本地绑定](19-local-model-boundary.md)、[合并查询](23-automatic-slot-options.md) 、[决策契约](24-agent-decision-improvements.md) 和 [当前修复契约](26-review-remediation.md) 为准。

## 需求—实现—测试—证据

下表保留前次验收映射；当前源码全部结果以 [新评估矩阵](03-evaluation.md#真实程序材料) 和 [v5证据](../examples/review_remediation_v5/README.md) 为准。以下历史目录均保存实际程序生成的 input、output、trace、run；同目录数据库快照由执行后查询产生。Mock 验证工程流程，live 单列，不能互相替代。

| ID | 要求及核对结果 | 实现/测试 | 实际证据 |
| --- | --- | --- | --- |
| B01 | 正常排期：passed，18:00，saved/completed | registry.py、storage.py；test_agent.py | [正常](../examples/submission_audit_20261101/scenarios/normal/output.json) |
| B02 | 资料不足：needs_info，列缺口，无草稿 | policy.py；test_supplement_unknowns.py | [缺资料](../examples/submission_audit_20261101/scenarios/insufficient/output.json) |
| B03 | 规则风险需确认；未授权阻断且不保存 | policy.py；test_adversarial_workflows.py | [软风险](../examples/submission_audit_20261101/scenarios/rule_risk/output.json)、[阻断](../examples/submission_audit_20261101/scenarios/blocked/output.json) |
| B04 | 原18:00冲突，建议19:00待确认，hard不变 | storage.py、agent.py；test_automatic_slot_options.py | [冲突](../examples/submission_audit_20261101/scenarios/slot_conflict/output.json) |
| B05 | 重复提交只有一份草稿，ID/修订相同，reused | storage.py；test_agent.py 跨进程用例 | [重复](../examples/submission_audit_20261101/scenarios/duplicate/database_state.json) |
| B06 | 注入保存前失败，failed，无草稿或历史 | registry.py；test_agent.py | [保存失败](../examples/submission_audit_20261101/scenarios/save_failure/output.json)、[数据库](../examples/submission_audit_20261101/scenarios/save_failure/database_state.json) |
| B07 | 改占用再提交，修订同一草稿，新建议待确认 | storage.py；test_decision_improvements.py | [变占用](../examples/submission_audit_20261101/scenarios/changed_occupancy/output.json) |
| B08 | 用户确认后复用保留确认，版本/阻断不可绕过 | storage.py、cli.py；test_cli.py | [确认](../examples/submission_audit_20261101/scenarios/confirmation/output.json) |
| B09 | 同ID不同输入拒绝，不覆盖原草稿 | storage.py；test_cli.py、test_storage.py | [本轮360项结果](../examples/submission_audit_20261101/junit-final.xml) |
| G01/G02 | 真实模型消费观察，正常与抢槽后动作不同 | agent.py、deepseek.py | [两条live及逐步比较](03-evaluation.md#真实模型的两条路径) |
| G03 | 正常、资料不足、工具失败三组完整材料 | service.py、trace.py | [证据索引](../examples/submission_audit_20261101/README.md) |
| G04/G05 | 预算、异常、未完成与无密钥Mock | config.py；test_runtime_limits.py | [全部测试结果](../examples/submission_audit_20261101/pytest-final.txt) |
| G06 | 锁依赖、全新环境安装、五份文档 | pyproject.toml、requirements.lock | [安装核查](../examples/submission_audit_20261101/install-check.json) |

表中源码在 src/shadow_agent/，测试在 tests/integration/。本地功能和证据已核对，已有历史已推送至公开 GitHub 仓库；本轮交付修改随新提交上传。最终远程 SHA 以推送后核对结果为准。正式题号仍待核实，公开仓库无需单独授权评审账号。
