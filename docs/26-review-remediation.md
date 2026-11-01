# 分享审核问题修复契约（2026-11-01）

用户确认实施。覆盖最低完整性、时间有效性、跨请求重复、失败方案保留、全新checkout复现和当前交付入口。该文补充第13/19/23/24号契约；本节为实施契约，实际结果另记，不把计划当执行证据。

## 最低完整性

业务字段仍可未知并可导入；审核永远执行公开的系统最低完整性规则，再执行导入的扩展规则。最低规则完整定义进入RuleCheck.rules及草稿证据，不作为隐藏保存条件。所有类型要求type/title；video/image要求media_ref，text要求caption；source_asset_id/license_ref不自动必填。缺项为needs_info，可补查后重审；blocked优先级保留。SYS-MINIMUM-前缀保留给系统规则，导入不得冒用。保存事务复核同一套规则。此明确基线取代早期“全部必填仅由导入规则决定”的表述；不需要修改数据库结构。

## 业务时间

运行默认使用真实UTC当前时刻，目标必须严格晚于它。查询拒绝过期原要求（REQUEST_EXPIRED），不自动顺延hard要求；候选中的过去节点标为不可用，不冒充占用。保存事务再次核对原要求和目标，接受确认前核对草稿目标。过期有效草稿转stale并释放本地容量；用户可重新指定原要求，旧确认不复用。

Repository增加独立business_clock/replay_at；原clock继续记录实际数据库审计时间，Budget继续使用monotonic。CLI run/confirm/review显式--as-of可回放；run.json记录clock_mode/reference_time，Trace与执行起止仍记录真实时间。Mock本身不绕过时间校验。demo明确使用内置夹具参考时间，说明为回放；普通run不自动启用。测试通过显式固定业务时钟保持跨日期可复现。

## 跨请求发布意图

RequestItem新增new_publication: bool=false，仅由用户输入，模型工具不能设置。默认值省略时保持旧canonical摘要；true参与请求摘要。已有另一个request的同material_id/version/platform/account有效未来草稿时，查询和保存事务返回DUPLICATE_PUBLICATION，不自动新建。结果needs_input并展示related_drafts，提示使用原请求复用；不能把其他请求草稿记为本次保存成功。只有显式new_publication=true才允许独立再次发布；同request重复仍复用。stale/rejected/过期草稿不拦截；不同版本/平台账号不混为同一发布意图。并发默认提交只有一份成功，保存事务是最终防线。

## 拟议方案与持久化结果

输出RequestResult升级schema_version=1.1（读取兼容历史1.0）；输入和Trace版本保留1.0。ItemResult新增可空proposed_plan及related_drafts。proposed_plan记录真实已消费审核/时段观察支撑的最后一次合法保存提议：suggested_at、pending_items、persistence_status（not_saved/saved/unknown）、validity（requires_recheck/current/invalidated）和error。无依据或畸形参数不产生有效提议。

顶层suggested_at和confirmation_status继续描述当前持久化草稿；proposed_plan独立展示本次建议。明确保存失败保留提议但标requires_recheck/not_saved；抢槽、过期、过期资料则invalidated；回执未知为unknown。旧有效草稿与新失败提议同时展示，不能混淆。未保存方案不能调用confirm。摘要清楚标注未保存及需重新核实，不凭历史查询声称当前可用。终态仍failed/incomplete，不能因保留建议改成成功或普通待确认。

## 验证与阶段

1. 交付说明：先创建.tmp，AGENTS与测试入口标当前，历史单列。文档检查后本地提交。
2. 完整性与时间：类型组合缺项、补查重审、事务绕过、时间等值/边界/时区/确认/回放/历史草稿、既有完整回归。
3. 发布意图与输出：同请求/换ID/显式重复/并发/失效版本；保存失败/旧草稿保留/抢槽/未知回执/无依据参数；同步类型、设计、测试和示例。
4. 新证据：当前源码全部离线检查与正常/不足/工具失败材料；显式live至少重新验证正常和抢槽恢复，并补不足、风险/阻断、保存失败等小样本。样本不外推稳定成功率。旧证据保留原协议及源码摘要，仅历史校验不冒充当前。

正式提交在最后实际Git操作后提供完整SHA，README引用已验证基线；不尝试在commit自身文件内嵌入自身SHA。个人净工时仍未精确计时，既有粗估与后续本人试用分开记录。

## 阶段二实际结果

最低完整性和业务时间已实现，无数据库结构迁移。20项新边界与既有回归共380项通过（52.46秒），Ruff通过。初轮375通过/1失败，因旧M03无新要求media_ref，补齐合成媒体引用后保留原审核失效断言通过。证据见 [阶段二](../examples/review_remediation_v5/phase2/verification.json)。此时尚未完成跨请求防重复、新失败输出或新live。

## 阶段三实际结果

默认跨请求防重复、显式new_publication、并发保存守卫和proposed_plan输出已实现。输出版本1.1，兼容读取历史1.0。完整392项通过（65.78秒），含32项新测试。初轮389通过/1失败为旧“新请求独立发布”用例，改为显式再次发布后保留原容量断言；默认换ID拦截另有新用例。阶段二失败归档只规范LF和尾空格，原始日志留runtime。证据见 [阶段三](../examples/review_remediation_v5/phase3/verification.json)。尚未生成新live，本阶段不宣称模型效果。
