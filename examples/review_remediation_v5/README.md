# 分享审核修复：当前实际证据

2026-11-01，业务实现提交ee5bf4a，提示5.0、输出schema1.1，源码摘要见[阶段三核验](phase3/verification.json)。本目录记录当前修复，不将旧4.1 live套到新源码。

- [阶段二](phase2/verification.json)：完整性与时间，380项通过；保留初轮375/1失败与修复说明。
- [阶段三](phase3/verification.json)：完整392项通过（65.78秒），原360+新增32；保留初轮389/1契约迁移失败。
- [当前场景](current-runs/index.json)：15次Mock与8次live，输入输出/Trace/元数据/实际数据库快照/合成目录齐全。正常、重复、跨请求拦截和显式再次发布为连续场景，数据库快照按各次时点保存。
- [补充运行](extended-runs)：变占用重新提交、确认保持、查询失败，共5次Mock；与上面合计28次业务运行。
- [live逐步分析](live-analysis.json)：8条单次样本，37次请求、累计total_tokens 68099，配置deepseek-flash、thinking disabled、temperature=0。最低资料空与保存失败各有一次INVALID_FINISH纠错；不宣称无纠错成功或总体稳定率。
- [新环境安装](install-check.json)、[安装后demo](installed-demo-output.json)：锁依赖、当前wheel和隔离导入通过，源码摘要匹配；demo退出4为预期阻断，并明确回放业务时间。

全部预期、实际结果及限制见 [评估](../../docs/03-evaluation.md)，完整契约见 [第26号说明](../../docs/26-review-remediation.md)。Trace只保留工具参数/结果和简短依据，不记录内部思维过程或密钥。

复现必须使用新输出目录。默认不调用模型；历史日期已过去时显式传入参考时间，run.json标记replay，实际执行日志时间不改：

```powershell
.venv/Scripts/python.exe scripts/generate_review_examples.py --output runtime/new-review --as-of 2026-11-01T00:00:00Z
.venv/Scripts/python.exe scripts/generate_review_examples.py --output runtime/new-live-review --live --env-file .env --as-of 2026-11-01T00:00:00Z
.venv/Scripts/python.exe scripts/generate_examples.py --mode mock --cases changed_occupancy confirmation tool_failure --output runtime/new-extended --as-of 2026-11-01T00:00:00Z
.venv/Scripts/python.exe scripts/check_evidence.py examples/review_remediation_v5 --live-directory examples/review_remediation_v5/current-runs
.venv/Scripts/python.exe scripts/check_decision_examples.py examples/review_remediation_v5/current-runs
```

当前28次业务运行与8条live均使用默认真实业务时钟，日期在执行时尚未过期；上述--as-of只是后续复现方式。历史证据可显式--allow-historical核查，此参数不表示源码与当前一致，不重新解释历史业务规则。
