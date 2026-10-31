# 一次查询返回排期选项：模型工具协议 v3

2026-10-31，用户确认：原时间冲突后检索替代节点属于确定性查询，由程序一次完成；模型消费候选后推荐，不额外决定是否查询 nearest。本文取代第 19 号规范中 requested/nearest 分两次工具调用的约定，其余本地身份、双向节点、确认与幂等边界继续有效。

## 契约与职责

query_slots 仅接受可选 reason_summary，调用参数为 {} 即可。删除 query 参数，旧 requested/nearest 参数按未知字段拒绝；不存在独立 nearest 工具。模型仍决定何时调用排期查询、推荐哪个已返回方案、是否补查/请求输入/阻断，并提出保存草稿；工具不调用模型、不自动挑选方案、不自动保存或确认。

同一次短 SQLite 读事务依次执行：

1. 精确查询原时间，包括它不在预设节点中的情形。
2. 原时间可用时直接返回原方案，不扫描替代节点。
3. 原时间冲突时，在原有合法范围内查前后各一个最近空闲节点，允许跨日，返回零、一或两个候选。

省略/null candidate_times 使用全部预设节点，[] 没有替代空间，显式候选限制范围；不扩大范围、不改原要求。两部分观察共享读快照与目录版本。查询任一部分失败时返回错误观察，不交付部分成功包。

## 类型与模型观察

本地 ScheduleOptions 聚合 requested: SlotObservation 和 alternatives: SlotObservation | null；原时段可用时 alternatives 为 null，冲突且无候选时为真实查询得到的空 slots。每次工具执行只生成一个 observation_id，预算计一次工具调用。本地继续保存目录、冲突来源、checked_slots 和来源身份。

模型投影字段为 requested_slot、alternatives_searched、alternative_source、alternatives。节点仅包含带时区的 at、available、direction、conflict_sources，不带业务身份、观察 ID、目录版本或占用记录 ID。提示版本升为 3.0，用户请求/确认/结果的 schema_version=1.0 不变。

下例为设计示意，不是执行证据：

```json
{
  "tool": "query_slots",
  "ok": true,
  "code": "SLOT_CONFLICT",
  "retryable": false,
  "data": {
    "requested_slot": {"at": "2026-11-03T18:00:00+08:00", "available": false, "direction": "requested", "conflict_sources": ["schedule"]},
    "alternatives_searched": true,
    "alternative_source": "default",
    "alternatives": [
      {"at": "2026-11-03T17:00:00+08:00", "available": true, "direction": "before", "conflict_sources": []},
      {"at": "2026-11-03T19:00:00+08:00", "available": true, "direction": "after", "conflict_sources": []}
    ]
  }
}
```

原时段冲突属于成功查询的事实，ok=true/code=SLOT_CONFLICT；真实查询失败为 ok=false。无候选允许 needs_input，不能伪称已保存。有可用方案时仍需保存并消费回执才能按可信状态结束。

## 保存与历史兼容

保存只能选择最新一次完整查询包中的可用时刻，而且该包必须在后续有效模型请求中实际回传。不能退回较早查询包，即使它已消费；原时段重新可用后，旧替代候选也不再具备保存资格。同响应查询后猜候选保存仍被拒绝。

保存事务保留对目录、规则、真实事实、合法候选、修订及实时占用的独立复核；查询后抢槽返回冲突，由模型消费结果后选择重新查询。改期始终生成待确认项，不改变 hard/soft 原要求。

草稿历史沿用原 slot_observation 与 slot_observations 结构，从真实完整查询包展开原时段和替代两份观察；同一工具观察 ID 可以关联它实际执行的两个确定性子查询。原时段可用只记录确实执行的原时段观察。内部 query_occupancy 保留为 Repository 基础查询及历史测试接口，不向模型暴露。无需 DDL 迁移，新旧草稿历史都可读取。

## 验证记录

新增 18 项定向测试已实际通过，包括七组完整离线服务场景：正常、冲突、无候选、资料不足、查询失败、保存失败、重复提交。既有 128 个有限占用组合的独立 oracle 改走完整查询接口，保留双向最近算法断言；旧前置测试改为检查原时间可用时不检索候选并拒绝旧 query 参数。完整回归与实际证据在执行后记于 [后续实现与验证契约](24-agent-decision-improvements.md)。本轮不调用真实模型，旧 live 记录不代表 v3 验证。
