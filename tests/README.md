# 测试入口

本轮本地全新安装和独立Windows CI各完整通过438项测试，零跳过，见[v8原始报告](../examples/acceptance_v8/README.md)。此前三次Windows socket环境失败仍保留在[v7](../examples/operator_experience_v7/README.md)，不抹除历史失败，也不声称本机环境问题已根治。当前契约见 [第28号文档](../docs/28-operator-experience.md)。默认不调用真实模型。

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

局部失效、本机时间、表单和CSV分别见test_dependency_invalidation.py、test_operator_language.py、test_operator_exports.py和test_work_cli.py。手动离线CI见.github/workflows/offline-verification.yml，提交d711311上的实际运行已成功，未向CI提供模型凭据。
