# 素材审核与发布排期 Agent

当前版本已完成分享审核修复：**392项离线测试通过，20次Mock业务运行、8条DeepSeek live留有实际证据**。支持按类型最低完整性、过去时间拦截、跨请求默认防重复和失败拟议方案保留。输出契约1.1；仅保存本地草稿，无真实发布能力。详见 [评估](docs/03-evaluation.md)、[当前证据](examples/review_remediation_v5/README.md) 和 [修复契约](docs/26-review-remediation.md)。

业务流程以开发实现参考和第 19、23、24、26 号修订为准，数据库设计 v1.0 为存储基线。原时间与显式候选独立于默认候选；同请求重跑重新检查、排除自身并保留未变方案的有效确认。改变原始要求需独立新请求；同素材版本和账号已有有效草稿时默认阻止新建，显式 `new_publication=true` 才表示再次发布，不会自动替代旧草稿；首版不提供原要求就地编辑、自动替换请求或已确认方案撤回。review 拒绝仅适用于尚有可确认待办的当前有效方案，已拒绝方案不会被同请求重跑恢复。

## 评审入口

| 材料 | 用途 |
| --- | --- |
| [当前审核修复](docs/26-review-remediation.md) | 新业务契约、阶段实际结果和当前证据 |
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

review 是数字菜单，默认暂不处理；confirm 的 JSON 格式见 [确认契约](docs/13-implementation-reference.md#11-用户确认与-review)。原要求不能就地改写；补原时间/候选需新 request_id，补素材则改资料文件并显式更新。

```powershell
.\.venv\Scripts\python.exe -m shadow_agent seed --data data --db runtime/agent.db --update --dry-run
.\.venv\Scripts\python.exe -m shadow_agent seed --data data --db runtime/agent.db --update
```

更新是四文件完整目录。缺文件失败；缺席资料退出当前目录，已有排期被新集合替换，历史保留。实际目录变化让旧 active 草稿统一 stale；同内容导入 noop 不失效。预览是检查工具，程序不会猜测文件漏项。

真实模型必须显式 live；只有这个分支加载所指定 .env 中的 DeepSeek 配置，不读取 GitHub 凭据，不自动加载 .env：

```powershell
.\.venv\Scripts\python.exe -m shadow_agent run --input data/request.json --mode live --env-file .env --db runtime/agent.db --output runtime/runs/live-first
```

正常 run 使用真实业务时间，拒绝已经到期的原要求；不会自动顺延 hard 时间。历史数据可显式 `run --as-of 2026-11-01T00:00:00Z` 回放，confirm/review 同样支持；Mock本身不绕过时间检查。元数据最低要求为 type/title；视频图片还需 media_ref，文字还需 caption。

输出1.1中，`suggested_at` 和 `draft` 表示持久化方案；`proposed_plan` 单列本次建议及未保存/未知/失效状态。未保存方案不能确认。`related_drafts` 提供被拦截的已有草稿引用。`items[].new_publication=true` 仅由用户输入，模型无权设置。

stdout 是 UTF-8 JSON；中文摘要和进度说明走 stderr。退出码：0=完成/查询成功，2=输入或配置错误，3=需用户输入/确认，4=规则阻断，5=未完成，1=技术失败。输出目录不能覆盖；每次输出 input.json、output.json、summary.md、trace.jsonl、run.json。

## 检查与复现

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
.\.venv\Scripts\python.exe -m pytest --basetemp=.tmp/pytest -q --runxfail
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe scripts/check_evidence.py examples/review_remediation_v5 --live-directory examples/review_remediation_v5/current-runs
.\.venv\Scripts\python.exe scripts/check_decision_examples.py examples/review_remediation_v5/current-runs
```

默认测试完全离线。重生成场景证据时使用新目录，不覆盖已归档结果：

```powershell
.\.venv\Scripts\python.exe scripts/generate_review_examples.py --output runtime/reproduced-mock --as-of 2026-11-01T00:00:00Z
.\.venv\Scripts\python.exe scripts/generate_review_examples.py --live --env-file .env --output runtime/reproduced-live --as-of 2026-11-01T00:00:00Z
```

故障只通过显式 `run --fault` 或场景脚本注入，写入 run.json，模型不能触发。支持 save_before、save_once、receipt_lost、receipt_unknown、read_failure、query_failure。Mock 是工程替身，不作为真实模型能力证据。

限制：本地规划容量不代表平台预约；审核只针对提供的合成元数据与规则；本地确认没有企业鉴权；保守目录版本可能导致额外复查；短历史不做自动摘要，体积超限明确 incomplete；没有全局最优、真实发布或通用撤回入口。

## 提交信息

- 题目标识：素材审核与发布排期 Agent（用户再次核对的题目标题；未提供独立数字题号，不杜撰编号）。
- GitHub URL：[xiyuxx/agent-campus-submission-example](https://github.com/xiyuxx/agent-campus-submission-example)。仓库公开，原24次历史已上传；审核修复按阶段提交，本轮最终上传SHA由Git实际返回并在交付回复提供。
- 最终 commit SHA：以本轮完成后的 `git rev-parse HEAD`、GitHub main 和交付回复为准。既有历史已完整保留，不改写提交；本提交不能在自身文件中嵌入自身 SHA。
- 模型：DeepSeek API，实测请求/返回均为 deepseek-flash，thinking 显式 disabled；小样本结论见评估，不外推稳定成功率。
- 用时估算：约 4–5 小时的 AI 辅助交互与执行时间（含 AI 运行/等待，按既有 47 轮响应窗口合并约 4 小时 15 分粗估）；人工净工时未单独计时，后续本人试用另计，见 [复盘](docs/05-retrospective.md#时间记录)。个人需求修正与 AI 实现范围见 [贡献记录](docs/04-ai-usage.md)。

仅提交实现所需的合成资料、代码及说明，避免在仓库转载试卷全文、页面或个人信息。
