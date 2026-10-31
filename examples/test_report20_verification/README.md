# docs/20 独立核实证据

2026-10-31，核实代码 bbd693a；业务源码摘要和新增测试摘要与原报告基线 74cdcac 对应归档一致。没有修改业务源码或测试预期，真实模型调用为 0。

| 范围 | 实际结果 | 原始输出 |
| --- | --- | --- |
| 既有 139 项 | 138 passed、1 xfailed | [baseline/pytest.txt](baseline/pytest.txt) |
| 新增 150 项，--runxfail | 136 passed、14 failed、0 error | [counterexamples/pytest.txt](counterexamples/pytest.txt) |
| 全部 289 项 | 274 passed、15 xfailed | [regression/pytest.txt](regression/pytest.txt) |
| 旧 ADV-01，--runxfail | 1 failed，DATA_CONFLICT:title | [legacy-null/pytest.txt](legacy-null/pytest.txt) |

[verification.json](verification.json) 保存独立调用命令、时间、退出码、JUnit 统计和源码/测试摘要；[audit.json](audit.json) 保存与原报告的摘要比对、14 个实际失败及 15 个 pytest.xfail 的核查。

[save-guard.json](save-guard.json) 是额外受控上下文检查：输出旧 passed、新规则实际 blocked、保存返回 STALE_EVIDENCE、独立 SQL 草稿数为 0。手动设置送达标记仅为隔离检查保存防线，不计真实模型路径或原 289 项套件的一项。

counterexamples/regression 下的 scenarios 文件由本轮实际测试产生并由已有归档脚本复制；旧报告目录保持原样。JUnit 中 xfail 表示为 skipped，但已核实每个节点的 type 均为 pytest.xfail；退出码 0 不能证明这些缺陷已修复。

结论与修改建议见 [核实说明](../../docs/21-test-report-verification.md)。
