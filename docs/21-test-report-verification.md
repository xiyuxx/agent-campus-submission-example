# 测试报告核实与修改建议

2026-10-31，核实 [docs/20 复测报告](20-retest-and-test-design.md)。本轮仅复测、核对证据和提出修复方案，未修改业务源码、原测试预期或 xfail 标记，真实模型调用为 0。

## 1. 核实结论与版本

报告的统计、缺陷分组和影响范围成立。本轮实际运行基线为 bbd693a；从报告基线 74cdcac 到 bbd693a 没有 src/shadow_agent 变更，且实际业务源码摘要、新测试文件摘要均与报告归档一致。因此不是用已修复代码或不同测试版本复核旧结果。

| 范围 | 本轮实际结果 | 退出码 | 与报告比较 |
| --- | --- | --- | --- |
| 既有 139 项，不收集新增文件 | 138 passed、1 xfailed | 0 | 一致 |
| 新增 150 项，--runxfail | 136 passed、14 failed、0 error | 1 | 一致；RT-01 6 项，RT-02 8 项 |
| 全部 289 项，保留 strict xfail | 274 passed、15 xfailed | 0 | 一致；15 项未修复，不计通过 |
| ADV-01 单独 --runxfail | 1 failed | 1 | 仍因补充 title=null 被判 DATA_CONFLICT |

已检查 JUnit 中全部 15 个 skipped 节点的 type 都是 pytest.xfail，排除了把普通跳过误记为预期失败的情况。新增反例的 14 项都是测试调用阶段的 failure，没有被 fixture/setup error 混入缺陷数量。

独立归档见 [核实证据索引](../examples/test_report20_verification/README.md)，包含实际命令、源码与测试摘要、pytest 文本、JUnit、反例场景文件和辅助数据库检查。不能从 14/150 推算生产故障概率；128 个节点占用组合是有限空间的穷举，不是 128 项独立业务能力。

## 2. RT-01：先修复审核事实失效链

建议优先级最高，严重程度维持 P2。问题影响业务审核输出与结束判定；本轮也验证了保存层的独立防线仍有效，没有证明越权草稿写入。

### 已核实的原因

[registry.py](../src/shadow_agent/tools/registry.py) 99–103 行只在素材 facts_hash 改变时清除 review/review_id，目录版本改变只清除 slots。只更新规则后重新 basic，素材摘要相同，便形成新 snapshot + 旧 review 的组合。

[agent.py](../src/shadow_agent/agent.py) 28 行核对目录与 snapshot；[result.py](../src/shadow_agent/result.py) 27–30 行也采用这一判断。两处均遗漏 review 自己绑定的版本与素材摘要，因此重读 basic 反而让旧审核通过“新 snapshot”的检查。

### 建议修改

1. basic/supplement 成功后，同时比较有效事实摘要与 catalog_revision。任一变化，使旧 review、review_id 和相关可保存资格失效；目录变化仍失效时段观察。观察历史和 Trace 保留，不能继续作为本轮当前证据。
2. 统一定义审核新鲜度判断，结束与结果组装都使用它。至少要求：

```text
review.catalog_revision == snapshot.catalog_revision == current_catalog_revision
review.material_sha256 == snapshot.facts_hash()
```

3. validate_finish 遇到旧审核应返回明确的缺步骤/过期错误，要求模型选择重新 check_rules；程序不能替模型自动执行审核。纠错次数耗尽时 incomplete。
4. assemble_result 仅使用符合上述条件的审核派生 review_status、risk_reasons 和缺项；已失效时 review_status=null，明确过期或未完成。不能继续把旧 blocked/passed 当当前结论。
5. 保留保存事务对版本和真实规则的复核。资料和目录都没变时重复 basic，可以保留仍然有效的审核，避免无必要的重复调用。

### 已补查的影响边界

本轮另建独立临时库，得到“旧 passed → 更新规则为 blocked → 重读相同素材”的组合。公开结果确实仍为 passed；随后使用受控送达标记尝试保存，真实事务返回 STALE_EVIDENCE，独立 SQL 查询 drafts 行数为 0。结果见 [save-guard.json](../examples/test_report20_verification/save-guard.json)。这是受控上下文检查，送达标记不是实际模型消费证据。

因此修复对象是审核事实链，不应据此宣称已存在未经授权保存，更不能削弱保存前复核来让错误状态“保持一致”。

### 必须保留与补充的回归

- 原有四种旧审核状态、结束边界、完整服务 6 个失败断言，以及显式重新检查的 4 个正向对照。
- 素材/目录都未变时重复 basic；已补查后重读 basic，退回未补查快照；资料摘要变化但目录版本未变的有效快照切换。
- 首次规则检查后、重读后、最终输出前分别发生规则变更；不能只修报告的一个调用顺序。
- 过期审核保存继续被拒绝；重新取得并回传新审核后，按新结论正常继续。保持本地身份绑定，不向模型重新暴露内部 ID 或修订。

## 3. RT-02：把结构错误挡在适配器边界

严重程度维持 P2，建议第二修复。当前影响是一次协议结构异常就终止可恢复任务；测试没有证明真实 DeepSeek 曾返回这些畸形结构，也没有显示已经执行了部分工具。

### 已核实的原因

[deepseek.py](../src/shadow_agent/models/deepseek.py) 61–106 行取得 message、tool_calls 元素及 function 后，提前调用 .get()。这些值为 null、数字、字符串或数组时可能抛 AttributeError，而当前异常转换没有覆盖这种结构边界。服务兜底因此把它当 INTERNAL_ERROR 终止。

完整恢复反例实际只有 1 次模型请求、0 次工具调用，返回 failed/INTERNAL_ERROR/AttributeError；没有进入允许的协议纠错。见 [新复现输出](../examples/test_report20_verification/counterexamples/scenarios/provider-recovery/output.json) 和 [运行元数据](../examples/test_report20_verification/counterexamples/scenarios/provider-recovery/run.json)。

### 建议修改

1. 先逐层校验结构，再访问字段：根对象、choices 数组、唯一 choice 对象、message 对象、tool_calls 数组及每个调用/function 对象。可用小型 Pydantic 响应模型或明确的类型判断，不必增加供应商 SDK。
2. 区分 message=null 与合法的 message.content=null：工具调用消息可以没有正文，不能因修复而拒绝合法 Tool Calls。
3. 外部结构错误统一转换为 MODEL_PROTOCOL，交给现有最多两次协议反馈及总模型预算处理；不要在适配器暗加另一套重试，也不要把所有内部异常都当成可恢复协议错误。
4. 整轮通过结构、调用唯一性、混合终态、finish_reason 和数量检查后再交给执行器。保留响应大小、参数大小和非 thinking 边界。
5. 错误日志只记录安全的层级路径和原因；不回灌完整响应，不输出凭据或 reasoning_content。

### 必须保留与补充的回归

- 原 7 种畸形结构和 3 个正确分类对照；坏一次后恢复的完整服务用例。
- 持续畸形时按协议上限 incomplete，不能无限请求。
- 第一条调用合法、第二条畸形时整组零执行；tool_calls 本身为错误容器；arguments 类型错误。
- 合法空 content + 工具调用、正常最终 JSON、原有 HTTP 429/5xx 有限重试继续通过。协议反馈与传输重试分别计数，总预算共同约束。

## 4. ADV-01：将未知补充值从冲突集合排除

严重程度维持 P2，建议第三修复；改动局部，但需保留整次冲突不部分合并的规则。

[storage.py](../src/shadow_agent/storage.py) 496 行目前只判断基本值已知且不相等，未排除补充值为 null。当前契约中 null/空白表示未知，无法证明它与基本标题矛盾。辅助复测的实际失败见 [legacy-null/pytest.txt](../examples/test_report20_verification/legacy-null/pytest.txt)。

建议冲突条件为“基本值非空、补充值非空且不相等”；补充侧未知值不参与覆盖或冲突。基本值未知而补充值已知时才填入，两个已知相同值保持原来源，两个已知不同值仍整次拒绝。主动清空字段属于另一个操作语义，当前 null 不承担该含义。

补充回归覆盖：缺省/null/空白补充值、基本值未知、已知相同/不同、有效授权与无关未知字段混合，以及一项真正冲突时所有其他字段都不合并。无需改变现有数据库结构。

## 5. 对测试与修复验收的意见

报告将 14 个失败分成 2 类、另外保留旧 ADV-01，口径正确；将 MockTransport 与真实模型分开、把 xfail 与通过分开也有实际证据支持。最近节点独立 oracle 使用 datetime 集合，与 SQL 排序实现分离，适合作为回归基线。保留用户已确认的跨日双向最近节点和本地身份绑定，不能用旧版“限同日”预期修改现行为。

建议每类缺陷单独形成小提交：先用 --runxfail 固定反例，修改对应边界，确认预期真实通过后移除该类 strict xfail，再跑完整回归。当前 289 项全部修复后的目标是 289 passed、0 xfailed；新加邻域用例另计。修改断言来接受错误输出、扩大 xfail 范围或捕获所有异常都不能算修复。

后续扩充顺序：先补三个修复点的相邻状态序列，再做持续多进程 seed/run/confirm 交错，最后考虑真实磁盘故障、强杀恢复与真实模型多次采样。有限节点穷举不覆盖长期并发、全部日期/时区或模型可靠性，当前无需为此改变业务范围。
