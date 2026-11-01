# 测试、实际结果与限制

当前契约为 [局部失效与运营体验](28-operator-experience.md)，任务生命周期沿用第27号契约。当前证据见 [v7索引](../examples/operator_experience_v7/README.md)，源码SHA256：ded21a3afeace5e55ef26572c19918d5ef8a2cab33adf3c3eb4bef719cf7f20b。旧v6及更早结果仅作历史追溯。

## 离线验证与干净环境

当前438项不同用例均有通过记录，未跳过/xfail；**尚无一次整套无失败的干净环境结果**。本轮新增20项，覆盖依赖影响、确认保持、本地时间/夏令时、中文表单、CSV与版本等。阶段一24项存储/依赖检查通过；阶段二35项相关检查和7项完整CLI通过，后续改为跨本机时区断言后7项再次通过。Ruff及格式检查通过。

实际创建两个全新venv，按锁文件安装当前wheel，隔离导入的源码摘要一致。首次wheel构建、锁依赖安装、pip check、隔离导入、demo与work list等8项检查通过。使用新安装解释器完整运行结果：

| 实际尝试 | 用例数 | 通过 | 失败 | 说明 |
| --- | --- | --- | --- | --- |
| [首次完整](../examples/operator_experience_v7/install/full-tests.xml) | 438 | 424 | 14 | 全部在Windows本机socket初始化出现WinError10013 |
| [同一新环境重查](../examples/operator_experience_v7/install/full-recheck.xml) | 438 | 379 | 59 | 57项直接socket异常；2项CLI未返回预期业务结构，产生上层KeyError |
| [第二个新环境完整](../examples/operator_experience_v7/install-final/full-suite.xml) | 438 | 418 | 20 | 全部为本机socket初始化失败 |

按用例核对以上记录，全部438项至少有一次通过，但不能据此把任一整套运行改写为成功。第二个新环境的demo也曾受同类环境错误阻断，记录保留。日志采集首次还遇到混合编码导致采集线程异常，已改为保留不可解码字节的转义表达；最初JUnit完整保留。未修改业务断言、跳过用例或替换事件循环来规避失败。相关原始报告见两个install目录，覆盖汇总见verification.json。

已准备仅手动触发的 [GitHub离线CI](../.github/workflows/offline-verification.yml)，在独立Windows runner构建wheel、安装新venv、完整执行默认测试、核验证据并保留报告。没有模型密钥、真实模型调用或部署步骤。**目前尚未推送或执行，不能声称CI通过。**

## 六项必须验收与当前运行材料

当前43次应用运行：35次Mock、8条真实模型，每条都有实际input/output/Trace/run/summary。另有数据库快照、13个任务操作快照、运营中文摘要和实际CSV。Mock与脚本中的接受、补资料、取消均为合成测试动作，不记为本人实际试用。

| 必须验收 | 实际结果与证据 |
| --- | --- |
| 正常排期 | [18:00 saved/completed](../examples/operator_experience_v7/current-runs/mock_normal/output.json) |
| 信息缺失 | [最低资料空，不保存](../examples/operator_experience_v7/current-runs/mock_missing/output.json)、[补查后仍不足](../examples/operator_experience_v7/current-runs/mock_insufficient/output.json) |
| 规则风险 | [needs_review待确认](../examples/operator_experience_v7/current-runs/mock_rule_risk/output.json)、[blocked不保存](../examples/operator_experience_v7/current-runs/mock_blocked/output.json) |
| 时段冲突 | [18:00占用，19:00保存抢槽失败，重查20:00并待确认](../examples/operator_experience_v7/current-runs/mock_save_race/trace.jsonl) |
| 重复提交 | [同草稿/修订reused](../examples/operator_experience_v7/current-runs/mock_duplicate/output.json)、[换ID或旧new_publication均阻止第二份](../examples/operator_experience_v7/current-runs/mock_explicit_repeat/output.json) |
| 保存失败 | [failed，未保存提议，零草稿](../examples/operator_experience_v7/current-runs/mock_save_failure/output.json)、[修改失败保留旧有效方案](../examples/operator_experience_v7/task-runs/edit_save_failure-state.json) |

更换占用后重跑见extended-runs/changed_occupancy：同草稿revision2，新时段待确认；确认保持和查询失败也重新运行。task-runs重生成8次应用运行及13个操作状态，覆盖未来已确认方案复用、确认到期重放、明确改期、补资料继续、取消和修改失败后重试。

## 本轮运营改进的连续验证

[最终运营样例](../examples/operator_experience_v7/operator-runs-final/journey.json)记录四次实际Mock工具循环：先检查并明确确认M01，再检查M02、按中文缺项补版权/封面、继续M02、取消M02，最后重新检查M01。

实际断言：M02补资料未使M01失效；M01仍可交接、revision1、confirmed；重新检查返回reused/completed，不新增修订或要求再次确认。相同可交接内容JSON/CSV版本一致。两项就绪的CSV与取消后的新CSV版本不同，旧CSV字节保持不变。文件见 [两项CSV](../examples/operator_experience_v7/operator-runs-final/two-materials.csv)、[取消后CSV](../examples/operator_experience_v7/operator-runs-final/after-cancel.csv)、[运营摘要](../examples/operator_experience_v7/operator-runs-final/operator-summary.txt)。前一轮三次运营运行保留于operator-runs，不覆盖历史。

## 真实模型的观察与下一动作

本轮显式8条DeepSeek live，提示6.0，requested/returned均deepseek-flash，thinking disabled、temperature=0。共36次请求，API total_tokens累计69037（含缓存，不当作账单金额），每场景仅一次。实际逐例统计见 [分析](../examples/operator_experience_v7/live-analysis.json)。

- [正常](../examples/operator_experience_v7/current-runs/live_normal/trace.jsonl)：观察18:00可用，模型保存18:00，消费回执后finish；5次模型请求、4次工具。
- [冲突恢复](../examples/operator_experience_v7/current-runs/live_save_race/trace.jsonl)：观察18:00冲突/19:00可用，选19:00，实际本地保存遭受控SLOT_CONFLICT，模型重查并改选20:00，保存后仍待确认；7次请求、6次工具。

其余路径覆盖缺资料、过期要求、软风险、阻断及保存失败。保存失败模型有一次INVALID_FINISH纠错，原事件保留；不能声称每次首答都正确。新增中文输入、依赖保持和CSV主要由离线测试/实际程序验证，不冒称全部进行过真实模型评估。当前源码、工具配对和观察送达校验见evidence-check.json。

## 边界

只审核元数据、文案与规则，不核验真实视频和版权；只有本地草稿，未预约平台或发布。规则目前全局适用，规则变化仍需相应审核；运行中观察保留全局版本并发校验。导出版本是内容标识，不是顺序批次；静态文件不会自动撤回，无多人交接回执或团队素材表同步。人工净工时未精确计时，旧4–5小时估算不包含新增阶段。当前最大的未完成验证是独立环境一次整套无失败，等待实际CI执行或允许本机回环的运行环境。
