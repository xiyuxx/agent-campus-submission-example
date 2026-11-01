# 素材审核与发布排期 Agent

发布前准备与确认助手：检查素材和规则、解决排期冲突、处理用户确认，形成可交接的本地草稿。新增统一 CLI 工作台，支持补资料后继续、修改时间、取消/恢复和交接导出。输出契约1.1；仅保存本地草稿，无真实发布能力。实际测试和模型结果见 [评估](docs/03-evaluation.md) 与 [当前证据](examples/task_workflow_v6/README.md)。

当前业务契约见 [第27号修订](docs/27-task-workflow.md)。同素材、平台、账号只有一个发布任务；版本或时间变化属于原任务修订。新方案保存成功才原子替换旧方案，失败保留旧有效草稿；新方案重新计算确认，不能沿用旧批准。旧请求及历史仍可查询。数据库版本2；兼容旧 JSON 输入，new_publication 不再授予绕过防重复的权限。

## 评审入口

| 材料 | 用途 |
| --- | --- |
| [任务工作流](docs/27-task-workflow.md) | 当前任务、修改、取消、迁移及用户操作契约 |
| [前次审核修复](docs/26-review-remediation.md) | v5历史修复、后续覆盖说明 |
| [前次交付审核](docs/25-submission-audit.md) | 本轮缺口、修正、验证与尚待提交的信息 |
| [Agent 开发流程规范](docs/17-agent-development-spec.md) | 实施手册：24 章覆盖提示/消息、上下文、工具、循环、预算、记忆、故障、竞品依据、验收与阶段提交 |
| [实际实现进度](docs/16-implementation-progress.md) | 已发生的实现、检查结果与未完成部分 |
| [数据库设计](docs/15-database-design.md) | 13 张表、字段、主外键、索引、完整导入、审计与数据库调用规范；附 DDL |
| [Git 提交规范](docs/14-git-workflow.md) | 阶段性提交、消息格式、凭据排除和自动检查 |
| [开发实现参考](docs/13-implementation-reference.md) | 开发首读：模块接口、JSON 契约、状态、事务与验收映射 |
| [架构与实施基线](docs/10-architecture-baseline.md) | 技术栈、模块、工具、状态和必须交付功能 |
| [CLI 与框架取舍](docs/12-runtime-decision.md) | 用户确认的运行方式、原生实现理由及边界 |
| [同类产品与使用体验](docs/11-product-experience.md) | 市场工具、用户旅程、CLI 交互和体验改进 |
| [工程与验证约定](docs/00-engineering-standards.md) | 项目结构、代码规范、技术选型、测试和提交标准 |
| [选题比较](docs/06-topic-selection.md) | 四题对比、选择理由及关键风险 |
| [项目要求原文](docs/08-project-requirements-original.md) | 用户提供的所选项目要求 |
| [公开工作流程实例](docs/09-real-workflow-examples.md) | 客户案例、产品流程和教学示例 |
| [需求](docs/01-requirements.md) | 用户、业务目标、验收矩阵 |
| [方案](docs/02-solution.md) | 模型决策循环、工具协议、边界和取舍 |
| [评估](docs/03-evaluation.md) | 预期、实际结果和证据索引 |
| [AI 使用记录](docs/04-ai-usage.md) | AI 辅助范围、本人贡献及验证 |
| [复盘](docs/05-retrospective.md) | 实际难点、学习、改进及用时 |

## 环境与安装

实测环境：Windows / PowerShell、Python 3.13.12、SQLite 3.51.1；使用 HTTPX 0.28.1、Pydantic 2.14.2、pytest 9.2.0、Ruff 0.17.3。全部运行、验证及构建依赖精确版本见 requirements.lock。其他系统尚未实测。

在仓库根目录创建独立环境并安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
```

直接调用虚拟环境解释器，不要求修改 PowerShell 执行策略。克隆后按 [Git 规范](docs/14-git-workflow.md) 启用本地钩子。受限 Windows 环境如在 asyncio 初始化处挂起，需要允许本机回环 socket；该行为在纯离线 Mock 中也会发生。

## 使用

日常操作从工作台进入，不需要编写请求/确认 JSON、管理请求 ID 或草稿修订：

```powershell
.\.venv\Scripts\python.exe -m shadow_agent seed --data data --db runtime/work.db
.\.venv\Scripts\python.exe -m shadow_agent work --db runtime/work.db --mode mock --as-of 2026-11-01T00:00:00Z
```

上述时间是合成夹具回放，界面会标明 Mock 只验证流程。真实使用传 `--mode live --env-file .env` 并使用实际未来时间，去掉 `--as-of`。选择素材后可检查/继续、修改时间、补资料、确认、取消、显式恢复或查看技术详情；最多三项批量检查。可交接表示本地草稿有效且确认完毕，未预约或发布。

自动化工作入口示例（每次检查会生成新的证据目录；重复业务操作复用任务）：

```powershell
.\.venv\Scripts\python.exe -m shadow_agent work --db runtime/work.db --action check --material M01 --at 2026-11-03T18:00:00+08:00 --timezone Asia/Shanghai --constraint hard --candidate 2026-11-03T19:00:00+08:00 --as-of 2026-11-01T00:00:00Z
.\.venv\Scripts\python.exe -m shadow_agent work --db runtime/work.db --action confirm --material M01 --decision accept --as-of 2026-11-01T00:00:00Z
.\.venv\Scripts\python.exe -m shadow_agent work --db runtime/work.db --action export --file runtime/handoff-first.json --as-of 2026-11-01T00:00:00Z
```

`--action edit --material M01 --at <新时刻>` 修改原任务；`cancel` 取消本地任务并释放容量，`reopen` 显式重新安排；`supplement --fields <业务字段JSON文件>` 更新资料后继续检查。交接文件必须为新路径，导出重新复核状态，排除失效、未确认和修改未完成的方案。外部发布人员仍需执行及核对真实发布。

已有数据库先备份，再执行 `python -m shadow_agent migrate --db <数据库路径>`。迁移遇到多份同素材/账号有效草稿时整体拒绝，返回冲突 ID，需明确保留方案；不会自动合并或删除。v1历史证据保留原样，不直接当当前版本证据。

一键演示默认 Mock，不需要密钥，资料和数据库隔离于 runtime/demo。重复 demo 使用相同业务请求，可观察草稿复用：

```powershell
.\.venv\Scripts\python.exe -m shadow_agent demo
.\.venv\Scripts\python.exe -m shadow_agent review --db runtime/demo/agent.db
```

demo 明确使用内置2026-11-01业务参考时间回放，实际执行时间不改。包含冲突、缺资料和阻断三项，所以整批退出码为 4 是预期业务结果；已保存部分仍保留。自动化入口：

```powershell
.\.venv\Scripts\python.exe -m shadow_agent seed --data data --db runtime/agent.db
.\.venv\Scripts\python.exe -m shadow_agent run --input data/request.json --mode mock --db runtime/agent.db --output runtime/runs/first
.\.venv\Scripts\python.exe -m shadow_agent inspect-draft --id <实际草稿ID> --db runtime/agent.db
.\.venv\Scripts\python.exe -m shadow_agent confirm --input confirmation.json --db runtime/agent.db
```

review 保留为兼容确认菜单，默认暂不处理；confirm 的 JSON 格式见 [确认契约](docs/13-implementation-reference.md#11-用户确认与-review)。不可变 JSON 请求不能改写；日常修改要求通过 work 自动创建原任务的新内部请求。素材也可按下列完整目录入口更新。

```powershell
.\.venv\Scripts\python.exe -m shadow_agent seed --data data --db runtime/agent.db --update --dry-run
.\.venv\Scripts\python.exe -m shadow_agent seed --data data --db runtime/agent.db --update
```

更新是四文件完整目录。缺文件失败；缺席资料退出当前目录，已有排期被新集合替换，历史保留。实际目录变化让旧 active 草稿统一 stale；同内容导入 noop 不失效。预览是检查工具，程序不会猜测文件漏项。

真实模型必须显式 live；只有这个分支加载所指定 .env 中的 DeepSeek 配置，不读取 GitHub 凭据，不自动加载 .env：

```powershell
.\.venv\Scripts\python.exe -m shadow_agent run --input data/request.json --mode live --env-file .env --db runtime/agent.db --output runtime/runs/live-first
```

正常 run 使用真实业务时间，过期原要求需要补新时间；已确认且仍有效的未来方案可复用。候选在保存前过期可有界重查，新的偏离仍待确认，不自动顺延 hard 要求。历史数据可显式 `--as-of 2026-11-01T00:00:00Z` 回放，work/confirm/review 同样支持；Mock本身不绕过时间检查。最低要求为 type/title；视频图片还需 media_ref，文字还需 caption。

输出1.1中，`suggested_at` 和 `draft` 表示持久化方案；`proposed_plan` 单列本次建议及未保存/未知/失效状态。未保存方案不能确认。`related_drafts` 提供被拦截的已有草稿引用。旧 `new_publication` 字段仅兼容历史输入，不能再建独立重复任务。

stdout 是 UTF-8 JSON；中文摘要和进度说明走 stderr。退出码：0=完成/查询成功，2=输入或配置错误，3=需用户输入/确认，4=规则阻断，5=未完成，1=技术失败。输出目录不能覆盖；每次输出 input.json、output.json、summary.md、trace.jsonl、run.json。

## 检查与复现

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
.\.venv\Scripts\python.exe -m pytest --basetemp=.tmp/pytest -q --runxfail
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe scripts/check_evidence.py examples/task_workflow_v6 --live-directory examples/task_workflow_v6/current-runs
.\.venv\Scripts\python.exe scripts/check_decision_examples.py examples/task_workflow_v6/current-runs
```

默认测试完全离线。重生成场景证据时使用新目录，不覆盖已归档结果：

```powershell
.\.venv\Scripts\python.exe scripts/generate_review_examples.py --output runtime/reproduced-mock --as-of 2026-11-01T00:00:00Z
.\.venv\Scripts\python.exe scripts/generate_review_examples.py --live --env-file .env --output runtime/reproduced-live --as-of 2026-11-01T00:00:00Z
.\.venv\Scripts\python.exe scripts/generate_task_examples.py --output runtime/reproduced-tasks
```

故障只通过显式 `run --fault` 或场景脚本注入，写入 run.json，模型不能触发。支持 save_before、save_once、receipt_lost、receipt_unknown、read_failure、query_failure。Mock 是工程替身，不作为真实模型能力证据。

限制：本地规划容量不代表平台预约；审核只针对合成元数据与规则；本地确认没有企业鉴权；完整目录更新使其他草稿保守失效；短历史不做自动摘要，体积超限明确 incomplete；没有全局最优或真实发布。取消只作用于本地任务，外部交接后的变更需另行协调。

## 提交信息

- 题目标识：素材审核与发布排期 Agent（用户再次核对的题目标题；未提供独立数字题号，不杜撰编号）。
- GitHub URL：[xiyuxx/agent-campus-submission-example](https://github.com/xiyuxx/agent-campus-submission-example)。既有版本已公开；本轮优化执行本地阶段提交，尚未推送。
- 最终 commit SHA：本地以 `git rev-parse HEAD` 和交付回复为准；提交GitHub前需推送并核对远程SHA。既有历史完整保留，不改写提交；不在commit自身文件内嵌其SHA。
- 模型：DeepSeek API，实测请求/返回均为 deepseek-flash，thinking 显式 disabled；小样本结论见评估，不外推稳定成功率。
- 用时估算：约 4–5 小时的 AI 辅助交互与执行时间（含 AI 运行/等待，按既有 47 轮响应窗口合并约 4 小时 15 分粗估）；人工净工时未单独计时，后续本人试用另计，见 [复盘](docs/05-retrospective.md#时间记录)。个人需求修正与 AI 实现范围见 [贡献记录](docs/04-ai-usage.md)。

仅提交实现所需的合成资料、代码及说明，避免在仓库转载试卷全文、页面或个人信息。
