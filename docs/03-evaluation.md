# 测试、实际结果与限制

当前契约为 [局部失效与运营体验](28-operator-experience.md)，任务生命周期沿用第27号契约。最新按题目复验见[v8](../examples/acceptance_v8/README.md)，运营连续样例见 [v7索引](../examples/operator_experience_v7/README.md)，二者业务源码SHA256均为ded21a3afeace5e55ef26572c19918d5ef8a2cab33adf3c3eb4bef719cf7f20b。旧v6及更早结果仅作历史追溯。

## 离线验证与干净环境

**最新本地全新安装及独立Windows CI均一次完整通过438项测试，零失败、零跳过/xfail。** 本地147.61秒、CI31.04秒；两份原始JUnit与安装9项检查见[v8本地](../examples/acceptance_v8/clean-install/verification.json)、[CI](../examples/acceptance_v8/ci/verification.json)。wheel安装后隔离导入的源码摘要均与当前一致。CI运行提交d711311，运行[41320127909](https://github.com/xiyuxx/agent-campus-submission-example/actions/runs/41320127909)整体success，不读取.env或调用模型。

另59项定向验收全部通过，Ruff及66个Python文件格式检查通过。此前优化新增20项，阶段一24项、阶段二35项和7项CLI的原始结果仍保留。下列为本轮成功之前的历史失败，不能删去，也不能推断本机socket问题已经根治。

实际创建两个全新venv，按锁文件安装当前wheel，隔离导入的源码摘要一致。首次wheel构建、锁依赖安装、pip check、隔离导入、demo与work list等8项检查通过。使用新安装解释器完整运行结果：

| 实际尝试 | 用例数 | 通过 | 失败 | 说明 |
| --- | --- | --- | --- | --- |
| [首次完整](../examples/operator_experience_v7/install/full-tests.xml) | 438 | 424 | 14 | 全部在Windows本机socket初始化出现WinError10013 |
| [同一新环境重查](../examples/operator_experience_v7/install/full-recheck.xml) | 438 | 379 | 59 | 57项直接socket异常；2项CLI未返回预期业务结构，产生上层KeyError |
| [第二个新环境完整](../examples/operator_experience_v7/install-final/full-suite.xml) | 438 | 418 | 20 | 全部为本机socket初始化失败 |

按用例核对以上记录，全部438项至少有一次通过，但不能据此把任一整套运行改写为成功。第二个新环境的demo也曾受同类环境错误阻断，记录保留。日志采集首次还遇到混合编码导致采集线程异常，已改为保留不可解码字节的转义表达；最初JUnit完整保留。未修改业务断言、跳过用例或替换事件循环来规避失败。相关原始报告见两个install目录，覆盖汇总见verification.json。

仅手动触发的 [GitHub离线CI](../.github/workflows/offline-verification.yml)已执行；除完整测试外，已核验v7的43条应用证据和两条真实路径。原始报告、run ID、提交SHA、步骤和时间见[v8 CI归档](../examples/acceptance_v8/ci/github-run.json)。CI证明离线工程行为，不替代真实模型评估。

## 六项必须验收与当前运行材料

最新v8重新执行28条：20次Mock、8条真实模型，每条都有实际input/output/Trace/run/summary及数据库快照。六项逐条映射见[v8](../examples/acceptance_v8/README.md)，另重跑更换占用/确认/工具失败。下表保留同源码v7的43次运行与运营连续记录：35次Mock、8条live，另有13个任务操作快照、中文摘要和CSV。Mock与脚本中的接受、补资料、取消均为合成测试动作，不记为本人实际试用。

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

最新v8显式8条DeepSeek live，提示6.0，requested/returned均deepseek-flash，thinking disabled、temperature=0。共36次请求，API total_tokens累计68928（含缓存，不当作账单金额），每场景一次。实际逐例统计见[v8分析](../examples/acceptance_v8/live-analysis.json)。此前v7的36次请求/69037 tokens是独立历史执行，不混作最新一轮统计。

- [正常](../examples/acceptance_v8/current-runs/live_normal/trace.jsonl)：观察18:00可用，模型保存18:00，消费回执后finish；5次模型请求、4次工具。
- [冲突恢复](../examples/acceptance_v8/current-runs/live_save_race/trace.jsonl)：观察18:00冲突/19:00可用，选19:00，实际本地保存遭受控SLOT_CONFLICT，模型重查并改选20:00，保存后仍待确认；7次请求、6次工具。

其余路径覆盖缺资料、过期要求、软风险、阻断及保存失败。v8保存失败模型仍有一次INVALID_FINISH纠错，原事件保留；不能声称每次首答都正确。新增中文输入、依赖保持和CSV主要由离线测试/实际程序验证，不冒称全部进行过真实模型评估。最新源码、工具配对和观察送达校验见v8/evidence-check-initial.json。

## 边界

只审核元数据、文案与规则，不核验真实视频和版权；只有本地草稿，未预约平台或发布。规则目前全局适用，规则变化仍需相应审核；运行中观察保留全局版本并发校验。导出版本是内容标识，不是顺序批次；静态文件不会自动撤回，无多人交接回执或团队素材表同步。人工净工时未精确计时，旧4–5小时估算不包含新增阶段。独立环境完整复验现已完成，仍缺本人独立试用、多次模型稳定性与长期并发证据；不能把此次通过外推到这些范围。
