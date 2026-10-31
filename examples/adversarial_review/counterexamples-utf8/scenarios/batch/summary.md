# 素材审核与排期结果：needs_input

模式：mock；请求：REQ-1；运行：RUN-1f286ab1-213b-4d87-a62b-df8dd7cd7221

仅保存本地草稿；未接入真实发布，未预约真实平台时段。

## M01

- 规则预检：passed
- 原要求：2026-11-03T18:00:00+08:00；约束：hard
- 当前建议：2026-11-03T19:00:00+08:00
- 确认状态：pending
- 草稿保存：saved；ID：D-e0b74092-017a-4cd7-a57d-90d8f58ebdde；修订：1；有效性：active
- 本项状态：needs_input
- 待办（confirm）：change_time / 是否接受 2026-11-03T11:00:00.000000Z 替代原要求 2026-11-03T10:00:00.000000Z？

## M02

- 规则预检：needs_info
- 原要求：2026-11-03T18:00:00+08:00；约束：hard
- 当前建议：无有效建议
- 确认状态：not_required
- 草稿保存：not_attempted；ID：无；修订：None；有效性：None
- 本项状态：needs_input
- 风险：R-COPYRIGHT-MISSING / 缺少版权状态，请补查授权资料
- 风险：R-COVER-MISSING / 示例业务规则要求提供封面引用
- 待办（input）：missing:copyright_status / 请补充素材 copyright_status 并显式导入
- 待办（input）：missing:cover_ref / 请补充素材 cover_ref 并显式导入
