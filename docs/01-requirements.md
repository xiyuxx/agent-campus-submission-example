# 需求与验收

当前交付为“素材审核与发布排期 Agent”。用户只提供题目标题，未提供独立数字题号，不杜撰编号。硬验收与产品优化的关系见 [任务契约](28-operator-experience.md)，实际结果见 [评估](03-evaluation.md)。

## 用户、工作流和范围

内容团队完成素材制作后，用程序查找待处理素材、检查资料和风险、协调时段、处理确认，随后把本地排期草稿交给发布人员。审核对象是合成元数据、文案和规则；不判断真实视频、不核验真实版权、不预约平台或发布。

素材 ID、元数据、原始时间要求、已有排期与规则是业务输入。日常 work 入口自动管理内部请求和运行目录；保留 JSON run/confirm 供工程评审。每次最多三项，逐项事务，部分成功保留，不承诺整批最优。

同 material_id/platform/account_id 只有一个稳定任务；素材版本和时间变化属于原任务修订。用户明确修改要求才生成不可变内部请求，新方案成功保存才替换旧方案。取消释放本地容量并保留历史，恢复必须明确选择。旧 new_publication 字段不授予再次发布权限。

## 硬条件

- 四类业务工具：素材读取 basic/supplement、规则检查、时段查询、排期草稿保存。
- 模型实际读取工具观察，决定补查、候选选择、重新查询、请求确认或阻断。工作清单和用户操作服务不能替代这个循环。
- 每项返回审核状态、风险原因、建议时段、待确认项、确认状态、草稿保存状态，并区分未保存拟议方案和数据库方案。
- 原 hard/soft 要求保留，任何模型建议的偏离均产生 change_time，候选和偏好不代表用户同意；blocked 不能普通确认绕过。
- 所有素材要求 type/title；视频图片还需 media_ref，文字还需 caption。最低规则公开进入审核和保存证据。资料不足可补查，仍不足则 needs_input。
- 时段查询在同一事务检查原时间，必要时返回授权范围前后各一个最近空闲节点，可跨日；显式 [] 不回退到默认节点。模型不能扩大范围。
- 默认真实业务时钟，目标严格在未来。原要求到期但已确认未来方案仍有效，可复用该方案；候选保存前过期可有界重查，不能自动接受改期。显式 --as-of 只回放业务时间。
- 保存事务复核素材、规则、时段和修订；重复请求复用，跨请求不能创建第二个任务。修改失败保留旧有效方案，不能继承旧批准到新方案。
- 确认绑定具体草稿修订，由明确用户动作记录；回执重放保留历史结果并刷新当前有效性。取消或已有新要求时不能批准旧方案。
- 保存明确失败返回 failed；无法核实为 unknown/incomplete，核验后再继续，不能虚报成功。
- 每项最多16次模型/工具尝试、180秒；最多三项、总540秒；临时失败额外重试最多一次，协议纠错和时段重查最多两次。耗尽明确 incomplete。

## 验收映射

下列均须实现、测试并有实际运行证据；优化实现证据链接见 [v7索引](../examples/operator_experience_v7/README.md)，最新按题目复验见[v8](../examples/acceptance_v8/README.md)。二者业务源码相同；历史 v5/v4 只用于追溯。

| 验收 | 实现与可执行测试 | 当前证据目录（均含实际输入/输出/Trace） |
| --- | --- | --- |
| 正常排期 | storage/registry/agent；test_agent、test_automatic_slot_options | current-runs/mock_normal、live_normal |
| 素材缺信息 | policy 与补查；test_supplement_unknowns、test_review_remediation | current-runs/mock_missing、mock_insufficient、mock_supplement |
| 规则风险 | 分清 needs_review/blocked；test_adversarial_workflows | current-runs/mock_rule_risk、mock_blocked |
| 时段冲突 | 合并查询、抢槽恢复；test_decision_improvements | current-runs/mock_save_race、live_save_race |
| 重复提交 | 请求复用、任务唯一、并发守卫；test_storage、test_tasks | current-runs/mock_duplicate、mock_cross_request |
| 保存失败 | 回滚、提议保留、旧方案保留；test_tasks、test_review_remediation | current-runs/mock_save_failure；task-runs/edit_save_failure |
| 更换占用后重跑 | 当前方案重核、修订、旧确认失效；test_decision_improvements | extended-runs/changed_occupancy |
| 确认与时间 | test_workflow_fixes、test_tasks、test_work_cli | task-runs/approved_future_reuse、expired_confirmation_replay.json |
| 模型决策循环 | 原生 Agent、DeepSeek；test_provider、test_runtime_limits | 两条以上 live 与 evidence-check.json 的观察/后续动作 |
| 交付完整性 | README、五份文档、锁依赖、离线 Mock、源码证据校验 | v8本地/独立CI完整测试、新安装、清单和评估记录 |

新优化验收还包括：一次补资料后继续、修改成功原子替换、失败保留旧方案、取消释放容量、恢复需明确操作、旧请求不得覆盖新要求、导出再次核验和迁移歧义拒绝。对应 test_tasks 与 test_work_cli。所有运行只使用合成资料；本人试用尚无独立结果。

## 当前运营优化

用户确认以考核和本人试用为主。本轮按素材/补充来源、规则及选定账号时段复核持久化草稿，无关资料更新保持已有修订和确认；运行中的观察仍受全局版本并发校验。预览和导入返回实际失效范围及原因。

work普通交互直接使用电脑本地时区和逐项资料提问，明确绝对时间；原工程JSON入口保留。CSV/JSON只导出当前可交接方案，包含稳定内容版本、导出时间及静态快照提醒，保留旧文件。实际连续样例见v7/operator-runs-final。本轮本地全新安装及独立Windows CI均完整通过438项测试；最新28条实际程序记录覆盖六项业务验收，含8条live。正式独立数字题号和人工精确用时仍未获得，不以实现通过补造提交信息。
