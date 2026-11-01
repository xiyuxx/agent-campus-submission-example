# 按题目要求的本轮复验

本轮在实际提交 `d7113116edfa70b1868b328d165933a2c77c96a1` 上执行。业务源码 SHA256 为 `ded21a3afeace5e55ef26572c19918d5ef8a2cab33adf3c3eb4bef719cf7f20b`，与 v7 相同；未修改业务代码。资料为合成素材，业务时间显式回放至 2026-11-01，执行日志保留真实时间。仅保存本地草稿。

## 六项必须验收

下列链接指向实际输出；同目录包含 input.json、trace.jsonl、run.json、summary.md 和数据库快照。

| 验收点 | 实际结果 | 证据 |
| --- | --- | --- |
| 正常排期 | M01原18:00可用，审核通过，saved/completed | [正常](current-runs/live_normal/output.json) |
| 信息缺失 | 最低资料缺失，needs_info，不保存；补查仍不足也停止 | [最低缺项](current-runs/live_missing/output.json)、[补查不足](current-runs/live_insufficient/output.json) |
| 规则风险 | 软风险保存待确认；版权阻断不保存，不能普通确认绕过 | [待确认](current-runs/live_rule_risk/output.json)、[阻断](current-runs/live_blocked/output.json) |
| 时段冲突 | 18:00占用，19:00受控抢槽失败，重查20:00；保存后仍需确认，原硬要求不改写 | [冲突Trace](current-runs/live_save_race/trace.jsonl) |
| 重复提交 | 同草稿revision1返回reused，数据库只有一份；换请求ID仍不新建 | [重复](current-runs/mock_duplicate/database_check.json)、[换ID](current-runs/mock_cross_request/output.json) |
| 保存失败 | 明确failed；拟议方案not_saved，数据库零草稿 | [真实模型失败处理](current-runs/live_save_failure/output.json)、[数据库](current-runs/live_save_failure/database_check.json) |

另按审核要点[更换占用后重复提交](extended-runs/changed_occupancy/output.json)：保留同草稿，更新到revision2，新建议20:00且待确认；数据库仍只有一份。[明确确认后复用](extended-runs/confirmation/output.json)返回completed/confirmed；[查询工具失败](extended-runs/tool_failure/output.json)明确failed且不保存。

共28条新应用记录：20次Mock、8条live。离线Mock只验证工程流程，不能证明模型决策；故障由合成场景明确注入，未调用真实发布平台。每条结果均由程序实际执行，不是设计样例。

## 两条真实模型决策路径

- [正常](current-runs/live_normal/trace.jsonl)：读取素材→规则检查→观察18:00可用→保存18:00→消费成功回执→结束。5次模型、4次工具。
- [冲突](current-runs/live_save_race/trace.jsonl)：观察18:00冲突、19:00可用→选19:00→保存返回SLOT_CONFLICT→再次query_slots→选20:00→保存并消费回执→待用户确认。7次模型、6次工具。

[只读核验](evidence-check-initial.json)检查28条源码摘要、动作/结果配对、观察送达及上述不同观察后的动作。Trace记录工具参数、返回摘要和简短依据，不记录内部思维过程。

本轮8条live使用deepseek-flash、提示6.0、thinking disabled、temperature=0，共36次请求、68928 total_tokens（含缓存，不是费用）。[统计](live-analysis.json)保留保存失败场景一次INVALID_FINISH：模型错误地请求阻断，校验拒绝后模型改为结束，最终仍明确失败、零草稿。每场景一次，不外推稳定成功率。

## 测试与复现

[59项定向测试](acceptance-tests.xml)全部通过，零跳过，覆盖Agent循环、预算耗尽/超时/协议、确认依赖、本机时间/夏令时、CSV、交互CLI。Ruff检查和66个Python文件格式检查通过。此前v7的三次完整失败仍按历史保留，不用本轮定向通过覆盖。

本地[完整干净安装](clean-install/verification.json)9项检查通过，包含438项默认测试全通过，零跳过，147.61秒。独立GitHub Windows runner[运行41320127909](https://github.com/xiyuxx/agent-campus-submission-example/actions/runs/41320127909)整体success，同样9项安装/运行检查和438项完整测试全通过，零跳过，31.04秒；原始[JUnit](ci/full-tests.xml)、[安装报告](ci/verification.json)、[run/commit/步骤](ci/github-run.json)随仓库归档，不依赖30天artifact留存。两环境均安装当前wheel并以隔离导入核验源码摘要。

CI执行基线d711311；本轮后续提交只增加文档和验证材料，不修改业务源码、测试或工作流。CI不读取.env，不使用模型密钥，不执行live或部署。独立CI还验证了同源码v7的43条既有记录。本轮28条新记录在本地通过同一只读检查器。

在仓库根目录按README安装依赖后，以新路径复现：

```powershell
.venv/Scripts/python.exe scripts/generate_review_examples.py --live --env-file .env --output runtime/acceptance-new --as-of 2026-11-01T00:00:00Z
.venv/Scripts/python.exe scripts/generate_examples.py --mode mock --cases changed_occupancy confirmation tool_failure --as-of 2026-11-01T00:00:00Z --output runtime/acceptance-extended-new
.venv/Scripts/python.exe scripts/check_evidence.py examples/acceptance_v8 --live-directory examples/acceptance_v8/current-runs
.venv/Scripts/python.exe scripts/verify_install.py --runtime runtime/acceptance-install-new --output runtime/acceptance-install-report-new --full-tests
```

归档文本仅规范LF和行尾空白，CSV保留实际字节；修改前后摘要见archive-normalization.json，原文件备份在忽略的本地runtime，CI artifact不变。顶层manifest覆盖本目录其余全部文件，排除自身。

本轮由AI辅助执行，合成确认不等于候选人亲自试用。无视频理解、真实版权核验、平台预约或发布；CSV依然是须核对版本的静态快照。题目标题已明确，未获独立数字题号；人工净用时未精确计时。当前产品优化连续操作与CSV样例见[v7](../operator_experience_v7/README.md)。
