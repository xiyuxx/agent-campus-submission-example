# 素材审核与发布排期 Agent

当前阶段：**需求、架构与技术栈已定稿，待实现**。选择用户列出的第一个项目：素材审核与发布排期 Agent。尚未实现业务代码、接入模型或执行业务测试，本文不代表考核已完成。

业务流程以开发实现参考 v1.2 和数据库设计 v1.0 为后续开发的基线。原时间与显式候选独立于默认候选；同请求重跑重新检查、排除自身并保留未变方案的有效确认。改变原始要求需独立新请求，不会自动替代旧草稿；首版不提供原要求就地编辑、自动替换请求或已确认方案撤回。review 拒绝仅适用于尚有可确认待办的当前有效方案，已拒绝方案不会被同请求重跑恢复。上述入口和行为均待实现。

## 评审入口

| 材料 | 用途 |
| --- | --- |
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

已检查的准备环境：Windows / PowerShell、Python 3.13.12、Git 2.47.0、ripgrep 15.1.0。
这不是运行兼容性测试结果。开发基线确定为 Python 3.13，依赖在实现时锁定并验证。

确定使用 Python 3.13、argparse CLI、原生单 Agent、DeepSeek Tool Calls、HTTPX、Pydantic 2、SQLite、pytest 和 Ruff。依赖尚未安装，业务代码尚未实现。

可在仓库根目录创建隔离环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe --version
```

后续直接调用虚拟环境解释器，不要求修改 PowerShell 执行策略。
尚未创建虚拟环境或安装业务依赖；Git 已初始化并配置本地阶段性提交钩子。

## 后续运行入口

数据库方案已细化为完整快照导入、草稿证据快照、导入记录和状态事件，开发实现参考已同步至 v1.2。可运行 `python scripts/check_database_design.py` 检查设计 DDL 的约束；它仅验证 SQLite 结构，不运行 Agent 或证明业务验收通过。业务程序与正式迁移入口仍待实现。

实现阶段补齐依赖文件以及真实模型、离线 Mock、测试、证据生成的可复制命令。
当前尚无业务运行入口，不提供虚构的运行输出或通过率。

## 提交信息

- 所选项目：素材审核与发布排期 Agent（用户列出的第 1 项；平台正式题号待核对）。
- GitHub URL：待创建或由候选人提供。
- 最终 commit SHA：待最终验证并提交后填写。
- 模型：DeepSeek API，默认 deepseek-flash、非 thinking；原生循环，不使用 Runtime 框架。精确依赖锁定及 API 可用性待实现时验证。
- 用时：准备阶段与正式作答阶段分别记录；平台计时以平台为准。

仅提交实现所需的合成资料、代码及说明，避免在仓库转载试卷全文、页面或个人信息。
