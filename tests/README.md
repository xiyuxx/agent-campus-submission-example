# 测试入口

当前418项不同用例均有通过记录；整套执行存在Windows socket环境失败，结合定向复核覆盖全部用例，不描述为单次整套无失败。实际JUnit与失败历史见 [v6验证](../examples/task_workflow_v6/phase2/verification.json)。当前任务契约见 [第27号文档](../docs/27-task-workflow.md)。默认不调用付费模型。

全新checkout先按根README安装，再执行：

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
.venv/Scripts/python.exe -m pytest -q --runxfail --basetemp=.tmp/pytest
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m ruff format --check src tests scripts
```

unit覆盖类型与规则，integration覆盖存储、工具循环、CLI、预算和反例。live由证据生成脚本显式启用，不属于默认pytest。Mock验证工程流程，不能充当真实模型决策证据。

若环境不允许asyncio本机回环socket，需要在允许该能力的环境执行。可用 `python scripts/verify_offline.py --output runtime/offline-check-new` 分模块隔离运行，目录必须为新路径；脚本完整保存所有模块的stdout/stderr/JUnit，不自动重试或跳过失败。任务与操作入口重点见test_tasks.py、test_work_cli.py、test_workflow_fixes.py。

历史结果按版本解释：[初次对抗](../docs/18-adversarial-test-review.md)、[再次复测](../docs/20-retest-and-test-design.md)、[修复结果](../docs/22-defect-fix-results.md)、[合并查询](../examples/automatic_slots_v3/README.md)、[提示4.1](../examples/decision_improvements_v4_1/README.md)、[360项远程复验](../examples/submission_push_20261101/README.md)。旧xfail不表示当前仍未修复，旧live不代表修改后新源码效果。
