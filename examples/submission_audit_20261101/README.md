# 2026-11-01 当前版本交付核查

本目录为实际程序运行与校验结果，源码摘要见 [audit.json](audit.json)。原始复核不新增真实模型调用，当时未提交或推送；用户随后授权推送，历史操作事实保留在audit.json，最新进展见docs/25-submission-audit.md。

- [最终质量检查](quality-checks.json)：Ruff、证据协议和归档哈希检查通过，文档链接及交付候选文件凭据扫描通过。
- [历史核查统计](history-review.json)：5个既有聊天47轮，仅存标题、时间和统计，不导出原始对话；跨度不充当个人工时。
- [目标仓库只读检查](github-repository-check.json)：HTTP200、公开仓库，Token报告push权限；没有远程写入。
- [最终pytest原始输出](pytest-final.txt)、[JUnit](junit-final.xml)：360 passed，44.07秒。此前原356项结果保留于pytest.txt/junit.xml。
- [scenarios](scenarios)：12类场景共15次Mock业务运行，覆盖正常、资料不足、软风险、阻断、冲突、重复、保存失败、查询失败、变占用、确认、回执丢失和结果未知。重复/确认/变占用保留before。最终场景附实际数据库快照及合成目录，before与后续运行共用同一场景数据库。
- [证据一致性及live路径比较](evidence-check.json)：15次当前Mock加2条既有live，共17次业务运行；校验类型、模式、源码、调用/观察送达及最终事件。报告来自只读分析，不是新模型执行。
- [干净安装核查](install-check.json)、[安装后实际demo输出](installed-demo-output.json)：新venv、精确依赖、当前wheel、pip check、隔离导入和CLI demo通过。退出4为预期阻断。
- [既有live归档](../decision_improvements_v4_1/README.md)：2026-10-31，deepseek-flash、thinking disabled、temperature=0，两条均单次样本；受控抢槽不代表生产事故。

预期与实际逐项映射见 [评估](../../docs/03-evaluation.md)。本地运行数据库和虚拟环境留在忽略目录runtime，不提交；归档数据库JSON是当时查询事实，不宣称是克隆后重查结果。

复现需要新输出目录，Mock不需密钥：

```powershell
.venv/Scripts/python.exe scripts/generate_examples.py --mode mock --output runtime/reproduced-mock
.venv/Scripts/python.exe scripts/check_evidence.py examples/submission_audit_20261101/scenarios --live-directory examples/decision_improvements_v4_1
.venv/Scripts/python.exe scripts/check_decision_examples.py examples/decision_improvements_v4_1
```

GitHub认证检查只记录配置存在及HTTP状态，不记录账号资料或凭据。用户已授权提交与推送，已有历史已推送；最终SHA在完成后核对，正式题号仍待核实。
