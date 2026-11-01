# 素材审核与发布排期 Agent

发布前准备与确认 CLI：检查素材、处理风险和时间冲突，形成可确认的本地草稿。**仅保存草稿，不预约平台、不执行真实发布。** 技术栈为 Python 3.13、原生单 Agent、DeepSeek Tool Calls、HTTPX、Pydantic、SQLite。

## 快速运行与评审

在仓库根目录执行（Windows / PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m shadow_agent demo --root runtime/demo-first
```

demo离线无需密钥，明确回放合成资料的2026-11-01业务时间；包含阻断场景，所以退出码4是预期结果。Mock只验证流程。日常工作台：

```powershell
.\.venv\Scripts\python.exe -m shadow_agent seed --data data --db runtime/work.db
.\.venv\Scripts\python.exe -m shadow_agent work --db runtime/work.db --mode mock --as-of 2026-11-01T00:00:00Z
```

选择素材后检查、按问题补资料、确认、改期、取消/恢复，按e导出CSV或JSON。日期输入如 `2026-11-03 22:00`，直接使用电脑本地时区；界面与CSV显示本地日期和偏移，无需填写时区名称或字段JSON。最多三项批量处理。真实使用改为 `--mode live --env-file .env`，去掉回放参数并提供实际未来时间；配置见 [.env.example](.env.example)。

| 六项必须验收 | 当前实际输入、输出与Trace |
| --- | --- |
| 正常排期 | [正常](examples/operator_experience_v7/current-runs/mock_normal/output.json) |
| 素材信息缺失 | [缺资料](examples/operator_experience_v7/current-runs/mock_missing/output.json) |
| 规则风险 | [待确认风险](examples/operator_experience_v7/current-runs/mock_rule_risk/output.json)、[阻断](examples/operator_experience_v7/current-runs/mock_blocked/output.json) |
| 时段冲突 | [抢槽后重查并待确认](examples/operator_experience_v7/current-runs/mock_save_race/trace.jsonl) |
| 重复提交 | [复用原草稿](examples/operator_experience_v7/current-runs/mock_duplicate/output.json) |
| 保存失败 | [失败、未保存提议和数据库快照](examples/operator_experience_v7/current-runs/mock_save_failure/output.json) |

两条真实模型路径：[正常时段可用](examples/operator_experience_v7/current-runs/live_normal/trace.jsonl)、[冲突与保存抢槽后调整](examples/operator_experience_v7/current-runs/live_save_race/trace.jsonl)。测试、纠错、安装和样本边界统一见 [评估](docs/03-evaluation.md) 与 [当前证据](examples/operator_experience_v7/README.md)。

限制：审核仅针对提供的元数据和规则，无视频理解或真实版权核验；本地容量不等于平台预约。改期须明确确认，阻断不能普通确认绕过。CSV/JSON是静态快照，改期/取消后需重新导出并核对版本。模型小样本不证明稳定成功率。本地完整测试仍受Windows回环socket环境限制，独立CI待执行；不能声称一次整套无失败。人工净投入未精确计时，不能声明三小时完成。

## 操作与数据

同素材/平台/账号一个稳定任务，时间和内容变化修订原任务。新方案保存失败保留旧有效草稿；未确认、修改未完成、取消或失效的方案不交接。无关素材补资料保留已有确认；实际依赖改变仍需复查。执行预算保留每项模型/工具各16次、180秒，整批最多三项/540秒，耗尽明确未完成。

CSV包含素材、账号、本地时间、审核/风险、确认、草稿版本、保存状态、交接版本和导出时间。相同内容JSON/CSV版本一致；旧附件不会自动更新，导出不代表已交付或发布。自动化示例：

```powershell
.\.venv\Scripts\python.exe -m shadow_agent work --db runtime/work.db --action check --material M01 --at "2026-11-03 18:00" --constraint hard --candidate "2026-11-03 19:00" --as-of 2026-11-01T00:00:00Z
.\.venv\Scripts\python.exe -m shadow_agent work --db runtime/work.db --action confirm --material M01 --decision accept --as-of 2026-11-01T00:00:00Z
.\.venv\Scripts\python.exe -m shadow_agent work --db runtime/work.db --action export --file runtime/handoff-first.csv --as-of 2026-11-01T00:00:00Z
```

工程JSON入口run/confirm/inspect-draft仍保留；显式IANA时区和带偏移ISO时间仍支持。输入timezone=local表示本机时区，已保存的绝对时刻不随电脑时区改变而改写；夏令时歧义要求明确偏移。文件及运行目录不能覆盖。

合成data目录用于初始化；日常补资料保存在SQLite，数据库是当前业务状态。`seed --update`是显式四文件完整快照替换，不是日常增量同步，会按导入内容覆盖当前资料；建议先加 `--dry-run`查看实际影响范围。当前未接团队素材表。已有v1数据库备份后执行 `migrate --db <路径>`升级至v2；多份同素材/账号有效草稿会明确拒绝迁移。

## 验证与复现

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp/pytest
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe scripts/verify_install.py --runtime runtime/install-new --output runtime/install-report-new --full-tests
.\.venv\Scripts\python.exe scripts/check_evidence.py examples/operator_experience_v7 --live-directory examples/operator_experience_v7/current-runs
```

已准备 [手动离线CI](.github/workflows/offline-verification.yml)，当前尚未推送/执行；不会调用模型、读取.env或部署。

默认测试离线；安装脚本在全新venv按锁文件安装wheel，并可完整运行测试。验证结果以实际报告为准。业务资料回放不改变真实日志时间。重生成证据使用新目录：

```powershell
.\.venv\Scripts\python.exe scripts/generate_review_examples.py --output runtime/scenarios-new --as-of 2026-11-01T00:00:00Z
.\.venv\Scripts\python.exe scripts/generate_review_examples.py --live --env-file .env --output runtime/live-new --as-of 2026-11-01T00:00:00Z
```

故障只由显式run --fault或合成场景脚本注入并记录，模型不能开启。stdout为UTF-8 JSON，交互/摘要走stderr。退出码0完成/查询成功、2输入配置错误、3需输入/确认、4阻断、5未完成、1技术失败。

## 交付说明

[需求](docs/01-requirements.md) · [方案](docs/02-solution.md) · [评估](docs/03-evaluation.md) · [AI使用](docs/04-ai-usage.md) · [复盘](docs/05-retrospective.md) · [当前体验契约](docs/28-operator-experience.md) · [历史设计索引](docs/history-index.md)

- 题目：素材审核与发布排期 Agent；未获得独立数字题号，不杜撰编号。
- 仓库：[xiyuxx/agent-campus-submission-example](https://github.com/xiyuxx/agent-campus-submission-example)。前一版本已推送，本轮体验优化按阶段本地提交；最终SHA和推送情况以实际交付回复为准。
- 精确依赖见requirements.lock；以Windows/Python3.13实测环境为准，不外推其他平台。
- 用户负责业务修正与取舍，AI辅助实现和验证；本人试用不预写结果。此前4–5小时估算仅涵盖早期47轮交互执行，本次新增阶段另计，人工净工时未精确记录。

真实密钥仅存忽略的.env，不提交真实隐私资料或完整试卷。克隆后按 [Git规范](docs/14-git-workflow.md) 启用正常密钥/消息钩子。
