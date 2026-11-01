# 测试、实际结果与限制

2026-11-01 交付复核：当前源码的六类必验场景及确认/变占用等扩展场景通过；最终完整离线回归 **360 passed，44.07秒，0失败/xfail**。这是工程测试结果，不是模型任务成功率。完整材料见 [本轮证据索引](../examples/submission_audit_20261101/README.md)。

## 配置与执行范围

Windows/PowerShell、Python 3.13.12；依赖精确版本见 requirements.lock。原业务356项完整回归先通过（42.13秒），修复交付检查器并增加4个证据篡改反例后，最终360项全部通过。命令和源码标识见 [audit.json](../examples/submission_audit_20261101/audit.json)，原始 [pytest输出](../examples/submission_audit_20261101/pytest-final.txt) 与 [JUnit](../examples/submission_audit_20261101/junit-final.xml) 可核对。

初次默认临时目录不可写，改用仓库 .tmp 后又受限于本机回环 socket；中止这些尝试后在允许回环的环境完成离线回归。初次失败和中止不计为通过。

全新 venv 按 requirements.lock 安装依赖、构建并安装当前 wheel、pip check、隔离导入及CLI demo全部通过。demo退出4是其中未授权素材被阻断的预期结果，正常素材确实保存了待确认草稿。见 [安装报告](../examples/submission_audit_20261101/install-check.json) 和 [实际输出](../examples/submission_audit_20261101/installed-demo-output.json)。

## 六类验收及扩展场景

[场景目录](../examples/submission_audit_20261101/scenarios) 包含12类场景、15次实际Mock程序运行；重复、确认、变占用另保留before运行。每次都有输入、输出、Trace和运行元数据；最终场景另有目录快照及数据库查询结果。

| 场景 | 预期 | 实际结果与证据 |
| --- | --- | --- |
| 正常 | 审核通过、原时间保存 | [passed/saved/completed，18:00](../examples/submission_audit_20261101/scenarios/normal/output.json) |
| 信息不足 | 列出缺项、不编造 | [needs_info/needs_input，未保存](../examples/submission_audit_20261101/scenarios/insufficient/output.json) |
| 规则风险 | 软风险待确认，阻断不得保存 | [软风险saved/pending](../examples/submission_audit_20261101/scenarios/rule_risk/output.json)、[未授权blocked](../examples/submission_audit_20261101/scenarios/blocked/output.json) |
| 时段冲突 | 原18:00不变，建议19:00待确认 | [hard仍保留，saved/pending](../examples/submission_audit_20261101/scenarios/slot_conflict/output.json) |
| 重复提交 | 同ID/修订，仅一份草稿 | [reused](../examples/submission_audit_20261101/scenarios/duplicate/output.json)、[一份草稿一条历史](../examples/submission_audit_20261101/scenarios/duplicate/database_state.json) |
| 保存失败 | 明确failed，无成功行 | [failed](../examples/submission_audit_20261101/scenarios/save_failure/output.json)、[数据库零草稿](../examples/submission_audit_20261101/scenarios/save_failure/database_state.json) |
| 工具查询失败 | 技术失败不得伪装无候选 | [failed/未保存](../examples/submission_audit_20261101/scenarios/tool_failure/output.json) |
| 改占用再提交 | 同草稿新修订，旧确认失效 | [revision2，新建议20:00/pending](../examples/submission_audit_20261101/scenarios/changed_occupancy/output.json) |
| 显式确认 | 当前修订有效接受，重跑保留 | [reused/confirmed](../examples/submission_audit_20261101/scenarios/confirmation/output.json) |
| 回执丢失/未知 | 可核实时恢复；否则未完成 | [恢复saved](../examples/submission_audit_20261101/scenarios/receipt_lost/output.json)、[unknown/incomplete](../examples/submission_audit_20261101/scenarios/receipt_unknown/output.json) |

跨进程去重、版本冲突、确认重放、拒绝后重跑、规则更新、异常响应、并发抢槽、预算和超时等由tests/integration覆盖。新增4个反例验证检查器拒绝未来观察、输出篡改、源码不符及缺文件。最近节点另有128个独立oracle占用组合。不得把旧报告的xfail计为当前通过，历史缺陷及修复见 [复测](20-retest-and-test-design.md)、[核实](21-test-report-verification.md)、[修复](22-defect-fix-results.md)。

## 真实模型的两条路径

本轮新增真实模型调用为0。复核的是2026-10-31归档的提示4.1两条DeepSeek live，源码摘要与当前源码完全相同：eda8b63468af712cc51f447a499a29b6a785506ef8241d100186727094e58996。配置为deepseek-flash、thinking disabled、temperature=0；返回模型、响应ID、usage及预算见各run.json。两条均为单次样本，不能外推稳定成功率。

1. [正常Trace](../examples/decision_improvements_v4_1/live_normal/trace.jsonl)：step6查询观察表明18:00可用；step7模型消费该观察，选择save_draft(18:00)；step8保存成功；step9消费回执后finish。5次有效模型动作、4次工具调用。
2. [抢槽Trace](../examples/decision_improvements_v4_1/live_save_race/trace.jsonl)：step6原18:00冲突，返回17:00和19:00；step7模型依据prefer_after选择19:00；step8实际保存时返回SLOT_CONFLICT；step9消费错误后选择query_slots；step10新观察显示19:00已占、20:00可用；step11改存20:00；step12保存成功；step13消费回执后needs_input。7次有效模型动作、6次工具调用，最终只有一份待确认草稿。

占用由脚本在首次保存前受控注入，是真实工具失败观察，但不是生产并发事故；重查及候选选择来自模型实际响应。Trace含工具参数、返回和简短依据，不保存内部思维过程。逐步机器比较见 [evidence-check.json](../examples/submission_audit_20261101/evidence-check.json)，完整9组归档的65个文件哈希也由check_decision_examples核对通过。

## 仍未证明的部分

合成样本和少量live不能证明真实视频审核能力、总体模型成功率、成本或温度参数收益。没有长期并发、断电/进程强杀、大数据量性能或跨系统原子性验证。本地确认没有企业身份鉴权。其他操作系统未实测。

GitHub认证和目标仓库查询均返回HTTP200；目标仓库公开，已有22次历史已正常推送。本轮用户已授权提交/推送交付修正；最终SHA与远程main在操作完成后核对，不伪造上传结果。本人贡献与可观察时间口径见 [AI使用](04-ai-usage.md) 和 [复盘](05-retrospective.md)。

## GitHub推送后复验

2026-11-01，提交f3e7b9d已推送并从GitHub重新克隆，使用既有干净安装的锁定wheel环境执行：360 passed，46.95秒；Ruff、17次业务运行、9组既有归档和两份manifest均通过，277个文档文件链接无缺失。本段为新增独立复验，与此前44.07秒的本地回归分开记录。详见 [克隆复验记录](../examples/submission_push_20261101/README.md)。无新业务代码或真实模型调用。
