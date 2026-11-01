# 当前发布任务与工作流证据

对应源码SHA256：`d032dfd1363714d2eb2abd9217c10f7f3903570b5ee697be85978cf1243b1b93`。业务实现提交基线为`dab483c`；最终交付还包含本目录和文档。源码摘要校验不依赖在文件中预填最终commit。v5及更早证据是历史版本。

## 检查结果

- [阶段一JUnit](phase1/junit.xml)：397项整套通过。
- [当前418项覆盖](phase2/verification.json)：所有不同用例均有通过记录，来自整套尝试及显式定向复核；不是一次无失败的单进程整套。最近整套409通过/9项Windows socket环境失败，随后CLI/时间12项复核通过，任务模块14项通过。原JUnit及失败历史保留，没有跳过或改写失败。
- [实际输出验收](acceptance-check.json)：六项必须场景及确认、防重复、失败保留旧方案、取消/交接等附加断言通过。
- [源码和Trace校验](evidence-check.json)：36条应用运行当前摘要一致、工具配对及观察送达正确，真实模型正常/抢槽路径检查通过。
- [隔离安装](install/verification.json)：当前wheel、新venv、锁依赖、pip check、隔离导入、demo和work list共8项检查通过；同目录保留实际日志。

## 实际程序材料

| 目录 | 内容与数量 |
| --- | --- |
| [current-runs](current-runs/index.json) | 15次Mock + 8条live；正常、缺资料、风险/阻断、重复、保存失败、抢槽、回执和补查 |
| [task-runs](task-runs/action-log.json) | 8次Mock运行 + 13个操作状态快照；检查→确认→交接、原要求到期后复用、到期确认重放、用户改期、取消、补资料、修改失败和重试 |
| extended-runs | 5次Mock；更换占用再提交、确认保持、查询失败，包括连续场景的before运行 |
| [live-analysis](live-analysis.json) | 实际模型动作、调用量和纠错；8条共36次请求，total_tokens累计68960（含缓存，不等于账单金额） |

每次应用运行有input.json、output.json、trace.jsonl、summary.md、run.json。附加数据库快照由运行后查询得到；连续场景共享数据库，快照只描述该时刻。task-runs的用户接受、修改、补资料和取消是明确合成输入，经真实业务方法执行，不能记作候选人本人试用。

两条真实路径为 [live_normal](current-runs/live_normal/trace.jsonl) 和 [live_save_race](current-runs/live_save_race/trace.jsonl)。后者真实本地保存遭受控抢槽，模型消费错误后重查并改选20:00，仍待确认。保存失败live曾先提出block，程序拒绝后改为finish，最终failed、未保存；原纠错见 [Trace](current-runs/live_save_failure/trace.jsonl)。每个场景仅一次，小样本不外推稳定成功率。新增任务流程及候选到期恢复主要由离线测试验证。

## 复现

按根README安装后，在仓库根目录执行：

```powershell
.venv/Scripts/python.exe scripts/check_evidence.py examples/task_workflow_v6 --live-directory examples/task_workflow_v6/current-runs
.venv/Scripts/python.exe scripts/check_decision_examples.py examples/task_workflow_v6/current-runs
.venv/Scripts/python.exe scripts/check_decision_examples.py examples/task_workflow_v6/task-runs
.venv/Scripts/python.exe scripts/generate_task_examples.py --output runtime/tasks-new
.venv/Scripts/python.exe scripts/generate_review_examples.py --output runtime/scenarios-new --as-of 2026-11-01T00:00:00Z
```

显式加`--live --env-file .env`到generate_review_examples启用真实模型；离线默认不需要密钥。必须使用新输出目录。安装复现可执行`python scripts/verify_install.py --runtime runtime/install-new --output runtime/install-report-new`；分模块测试可执行`python scripts/verify_offline.py --output runtime/offline-new`，不会自动重试、跳过或伪装通过。

归档文本仅规范LF和行尾空格；运行事实、错误、决策和结果不改写。原始安装日志保留在本地runtime，原始测试JUnit在.tmp。manifest.json覆盖本目录全部其余文件，不覆盖自身；子目录清单只覆盖该批原始运行，顶层清单同时覆盖验证与说明。

限制和失败详见 [评估](../../docs/03-evaluation.md)。仅本地草稿，没有真实发布、平台预约或本人独立试用结果。用户授权后，业务及证据基线1a2527d已推送并核对远程main一致；本轮未进行新的GitHub克隆复验。后续提交仅同步推送状态和本清单。
