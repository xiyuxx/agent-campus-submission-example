# 测试、实际结果与限制

当前任务工作流已实现，契约见 [第27号文档](27-task-workflow.md)。[v6证据索引](../examples/task_workflow_v6/README.md) 对应当前源码；v5及更早结果只作历史追溯，不能代替修改后的验证。

## 离线验证与失败

Windows/PowerShell、Python3.13.12，锁文件依赖不变。当前418项不同用例全部有通过记录，未跳过或xfail；**不是一次单进程整套无失败**。最近整套执行409通过/9失败，失败均发生在Windows asyncio本机回环socket初始化，出现WinError10013及未完成初始化的_ssock清理错误。随后对CLI和时间恢复12项定向复核全部通过；任务模块14项也独立通过。逐用例合并覆盖418项，实际JUnit、失败与复核方法见 [验证报告](../examples/task_workflow_v6/phase2/verification.json)。

第一阶段397项整套通过，见 [JUnit](../examples/task_workflow_v6/phase1/junit.xml)。实施中发现旧“拒绝后独立再发”的断言与用户新业务约束冲突，改为显式重新安排原任务，保留容量释放和历史不变断言；迁移DDL说明同步后字节一致性测试通过。其他全量/分模块尝试出现过环境错误，次数记录在验证报告，未把失败日志改成成功。scripts/verify_offline.py提供逐模块隔离入口，不自动重试或跳过测试。

Ruff检查和格式检查通过。新增覆盖：历史确认回执与当前有效性分离、未来已确认方案复用、候选保存前到期后的模型重查、极端日期返回INVALID_INPUT、任务唯一性、原子替换、修改失败保留旧方案、取消/恢复、旧请求越权、导出复核、补资料版本和迁移歧义。任务记账失败也核对公开结果与最后final事件一致。

## 六项业务与操作优化的实际材料

当前生成28次Mock和8条真实模型运行，共36条应用记录；全部包含input/output/summary/trace/run。对比工具观察与后续动作、核验源码摘要及Trace配对的 [只读报告](../examples/task_workflow_v6/evidence-check.json) 已通过。Mock是工程替身；脚本中的用户接受、修改、取消是明确标注的合成测试动作，不是候选人本人试用记录。

| 验收 | 当前实际结果与文件 |
| --- | --- |
| 正常排期 | [正常18:00 saved/completed](../examples/task_workflow_v6/current-runs/mock_normal/output.json) |
| 素材信息缺失 | [最低资料缺失needs_info](../examples/task_workflow_v6/current-runs/mock_missing/output.json)、[补查仍不足](../examples/task_workflow_v6/current-runs/mock_insufficient/output.json)、[补查后继续](../examples/task_workflow_v6/current-runs/mock_supplement/trace.jsonl) |
| 规则风险 | [needs_review并待确认](../examples/task_workflow_v6/current-runs/mock_rule_risk/output.json)、[blocked不保存](../examples/task_workflow_v6/current-runs/mock_blocked/output.json) |
| 时段冲突 | [18:00占用、19:00抢槽失败、重查20:00并待确认](../examples/task_workflow_v6/current-runs/mock_save_race/trace.jsonl) |
| 重复提交 | [同ID修订reused](../examples/task_workflow_v6/current-runs/mock_duplicate/output.json)、[换ID阻止新建](../examples/task_workflow_v6/current-runs/mock_cross_request/output.json)、[旧显式再次发布也阻止](../examples/task_workflow_v6/current-runs/mock_explicit_repeat/output.json) |
| 保存失败 | [failed、未保存提议、零草稿](../examples/task_workflow_v6/current-runs/mock_save_failure/output.json)；[修改失败保留旧有效方案](../examples/task_workflow_v6/task-runs/edit_save_failure-state.json) |
| 更换占用再提交 | [原草稿revision2，新20:00待确认](../examples/task_workflow_v6/extended-runs/changed_occupancy/output.json) |
| 确认与到期 | [原要求过期后复用已确认未来草稿](../examples/task_workflow_v6/task-runs/approved_future_reuse/output.json)、[历史回执confirmed而当前stale](../examples/task_workflow_v6/task-runs/expired_confirmation_replay.json) |
| 用户补资料/修改/取消 | [补资料继续](../examples/task_workflow_v6/task-runs/supplement_and_continue/output.json)、[明确新时间](../examples/task_workflow_v6/task-runs/user_changes_time/output.json)、[取消后无交接项](../examples/task_workflow_v6/task-runs/cancel_confirmed_or_ready.json) |
| 查询失败与未知回执 | [技术failed](../examples/task_workflow_v6/extended-runs/tool_failure/output.json)、[unknown/incomplete](../examples/task_workflow_v6/current-runs/mock_receipt_unknown/output.json)、[凭据核验恢复](../examples/task_workflow_v6/current-runs/mock_receipt_lost/output.json) |

每份数据库快照表示对应执行时刻。连续场景共享数据库，不能把早期快照当作最终状态。任务操作记录包含输入、实际返回、当时工作清单和重新核验的交接内容；新要求未成功保存、未确认或已失效时不导出。

## 两条真实模型路径

本轮显式DeepSeek live 8条，每场景一次；提示6.0，requested/returned均deepseek-flash，thinking disabled、temperature=0。共36次模型请求，API total_tokens累计68960（含缓存，不作为账单金额）。源码SHA256为d032dfd1363714d2eb2abd9217c10f7f3903570b5ee697be85978cf1243b1b93。详见 [逐步分析](../examples/task_workflow_v6/live-analysis.json)。

- [正常](../examples/task_workflow_v6/current-runs/live_normal/trace.jsonl)：读取→审核→观察18:00可用→模型选择保存18:00→消费回执后finish，5次模型请求、4次工具。
- [冲突恢复](../examples/task_workflow_v6/current-runs/live_save_race/trace.jsonl)：观察18:00冲突/19:00可用→模型选19:00→真实本地保存返回SLOT_CONFLICT→模型重查→观察20:00可用→保存20:00并待确认，7次模型请求、6次工具。抢槽是明确受控故障，不称真实生产事故。

其余真实路径覆盖资料缺失、过期要求、软风险、阻断和保存失败。保存失败模型先提出block，被INVALID_FINISH拒绝后改为finish，最终结构化结果保持failed且零草稿；纠错原文保留，不能宣称每次首答正确。8条单次样本不证明稳定成功率；候选到期恢复、任务修改/取消等新增流程主要由离线集成与CLI测试验证，不冒称全部做过live。

## 安装与交付

当前wheel在全新venv安装锁定依赖，pip check、隔离导入、demo及work list共8项检查通过，安装源码摘要与上述一致。见 [安装报告](../examples/task_workflow_v6/install/verification.json) 与同目录实际日志。demo使用明确回放时间，退出4来自合成阻断场景，未隐藏为成功。用户随后明确授权推送，业务及证据基线1a2527d已上传并核对远程main一致；本轮未进行新的远程克隆复验。

## 已知限制

Windows本机socket限制尚依赖运行环境允许，整套一次性无失败未验证；分批通过不能隐藏这一点。审核仅元数据/规则，不证明媒体可读或版权合法；没有真实发布、企业鉴权、全局最优、断电恢复或长期高并发保证。目录更新保守地失效所有有效草稿。取消只影响本地任务，交接后外部发布需另行协调。人工净工时未精确记录，本轮AI执行不计为本人独立操作；既有4–5小时估算不包含本轮。
