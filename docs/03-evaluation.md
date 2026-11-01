# 测试、实际结果与限制

2026-11-01分享审核修复已实施。最低完整性、业务时间、跨请求重复和失败提议的当前契约见 [第26号文档](26-review-remediation.md)，当前实际证据见 [v5索引](../examples/review_remediation_v5/README.md)。旧360项及提示4.1是历史基线，不代表新源码效果。

## 离线结果与环境

Windows/PowerShell、Python3.13.12、依赖沿用requirements.lock。当前完整回归 **392 passed，65.78秒，0失败/xfail**，包含原360项和32项新边界。见 [pytest](../examples/review_remediation_v5/phase3/pytest.txt)、[JUnit](../examples/review_remediation_v5/phase3/junit.xml)、[源码及测试摘要](../examples/review_remediation_v5/phase3/verification.json)。证据检查器随后适配1.1/历史核查，四项篡改反例另行重跑通过。

初轮完整性/时间回归375通过、1失败：旧M03缺新要求的媒体引用；补齐合成夹具引用，保留旧审核失效断言后380通过。跨请求阶段初轮389通过、1失败：旧用例把新请求视为独立发布，现显式声明new_publication，原容量断言保留；新增默认换ID拦截与并发用例。初轮失败和最终输出均保留，不用预期失败冒充通过。归档仅统一LF及尾空格，原始输出保留本地runtime。

全新venv按锁文件安装当前wheel、pip check、隔离导入和CLI demo均通过；安装源码摘要与当前一致。demo明确使用夹具业务时间回放，实际日志时间不改。见 [安装报告](../examples/review_remediation_v5/install-check.json)。README测试前先创建.tmp，修复新checkout嵌套basetemp缺父目录问题。

## 真实程序材料

当前源码生成20次Mock业务运行和8条live，共28次，均有input/output/trace/run/summary；另有执行后实际数据库快照及合成目录。重复和确认的连续场景共用场景数据库，每份快照表示该次执行当时的状态，不是最终数据库的统一快照。

| 场景 | 当前实际结果 |
| --- | --- |
| 正常 | [18:00 saved/completed](../examples/review_remediation_v5/current-runs/mock_normal/output.json) |
| 同请求重复 | [同ID/修订reused](../examples/review_remediation_v5/current-runs/mock_duplicate/output.json) |
| 换请求ID重复 | [needs_input，返回related_drafts，无第二份草稿](../examples/review_remediation_v5/current-runs/mock_cross_request/output.json) |
| 显式再次发布及再次重跑 | [第二份saved](../examples/review_remediation_v5/current-runs/mock_explicit_repeat/output.json)、[复用第二份](../examples/review_remediation_v5/current-runs/mock_explicit_repeat_again/output.json) |
| 最低资料全空 | [needs_info，不保存](../examples/review_remediation_v5/current-runs/mock_missing/output.json) |
| 补查后完整 | [needs_info→补查→passed→saved](../examples/review_remediation_v5/current-runs/mock_supplement/trace.jsonl) |
| 仍缺资料 | [needs_input，不保存](../examples/review_remediation_v5/current-runs/mock_insufficient/output.json) |
| 过去hard要求 | [REQUEST_EXPIRED，不顺延、不保存](../examples/review_remediation_v5/current-runs/mock_past/output.json) |
| 软风险/阻断 | [needs_review并待确认](../examples/review_remediation_v5/current-runs/mock_rule_risk/output.json)、[blocked不保存](../examples/review_remediation_v5/current-runs/mock_blocked/output.json) |
| 保存失败 | [failed，保留19:00未保存提议，零草稿](../examples/review_remediation_v5/current-runs/mock_save_failure/output.json) |
| 抢槽 | [19:00失败→重查→20:00待确认，一份草稿](../examples/review_remediation_v5/current-runs/mock_save_race/trace.jsonl) |
| 回执丢失/未知 | [核对恢复](../examples/review_remediation_v5/current-runs/mock_receipt_lost/output.json)、[unknown/incomplete](../examples/review_remediation_v5/current-runs/mock_receipt_unknown/output.json) |
| 变占用后重新提交 | [同草稿revision2、新20:00待确认](../examples/review_remediation_v5/extended-runs/changed_occupancy/output.json) |
| 确认保持 | [重跑reused/confirmed](../examples/review_remediation_v5/extended-runs/confirmation/output.json) |
| 查询失败 | [技术failed，不伪装无候选](../examples/review_remediation_v5/extended-runs/tool_failure/output.json) |

时间等值、查询后到期、确认前到期、候选到期、两写者并发、旧草稿与新失败提议分离、无依据模型时间、确认重放、预算及协议边界由可执行测试覆盖。没有将“可保留提议”改写为保存成功；proposed_plan的未保存状态不可直接confirm。

## 真实模型的两条路径

本轮显式运行8条DeepSeek live，提示5.0，requested/returned均deepseek-flash，thinking disabled、temperature=0。每场景各一次，共37次模型请求，API返回total_tokens累计68099（包含缓存用量，不作为账单费用）。真实请求/返回标识和usage见各run.json；源码摘要为3bf99cc483bd3bb223b42e1598c9053a61e4dbbc4f55b7be425548ba6336b50a。

- [正常live](../examples/review_remediation_v5/current-runs/live_normal/trace.jsonl)：读取→审核passed→观察18:00可用→模型保存18:00→消费保存回执后finish；5次请求、4次工具。
- [抢槽live](../examples/review_remediation_v5/current-runs/live_save_race/trace.jsonl)：观察18:00冲突、19:00可用→模型选19:00→工具实际保存遭SLOT_CONFLICT→模型重查→观察20:00可用→保存20:00→消费回执后needs_input；7次请求、6次工具。占用由脚本在首次保存前受控注入，不称生产事故。

其余live分别验证最低资料空、仍缺资料、过期要求、软风险、明确阻断和保存失败，最终状态均符合本次预期。**两例需要终态纠错**：最低资料空时模型先申请needs_input，但缺少真实规则检查，被拒绝后执行审核；保存失败时模型先申请block，被要求按技术失败finish后正确结束failed。最低资料空的首个模型说明还提及非必需许可资料，公开结构化缺项由可信规则产生，不能据此把该说明当准确必填清单。完整动作及纠错见 [逐步分析](../examples/review_remediation_v5/live-analysis.json)。

8条小样本不能证明稳定模型成功率。终态正确依赖程序校验及有限纠错，不能宣称模型每次首答都正确，也不能用392项离线通过率代替live质量。

## 限制

审核只覆盖元数据和文案，synthetic引用不证明真实媒体可读或版权合法；没有视频理解、真实发布、企业鉴权、全局最优排期或已确认草稿通用撤回。new_publication只声明再次发布，不取消或替代已有草稿。业务时钟回放只用于显式历史验证，Mock不自动绕过时间限制。个人实际净工时未独立计时，约4–5小时仅此前交互执行粗估，后续本人试用仍未发生。

## 当前远程克隆复验

新提交aa8b325推送后从GitHub重新克隆，使用新安装当前wheel的隔离解释器执行全部测试：392 passed，65.11秒。README的.tmp前置步骤、Ruff、28次业务证据和归档清单核查均通过，见 [实际记录](../examples/review_remediation_v5/remote-verification/verification.json)。最后仅归档结果，无新增业务源码、数据、测试或依赖修改。
