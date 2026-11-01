# 测试入口

当前完整回归392项离线测试通过（原360+新增32）；审核修复的范围与实际结果见 [第26号契约](../docs/26-review-remediation.md)，实际输出见 [v5证据](../examples/review_remediation_v5/README.md)。默认不调用付费模型。

全新checkout先按根README安装，再执行：

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
.venv/Scripts/python.exe -m pytest -q --runxfail --basetemp=.tmp/pytest
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m ruff format --check src tests scripts
```

unit覆盖类型与规则，integration覆盖存储、工具循环、CLI、预算和反例。live由证据生成脚本显式启用，不属于默认pytest。Mock验证工程流程，不能充当真实模型决策证据。

历史结果按版本解释：[初次对抗](../docs/18-adversarial-test-review.md)、[再次复测](../docs/20-retest-and-test-design.md)、[修复结果](../docs/22-defect-fix-results.md)、[合并查询](../examples/automatic_slots_v3/README.md)、[提示4.1](../examples/decision_improvements_v4_1/README.md)、[360项远程复验](../examples/submission_push_20261101/README.md)。旧xfail不表示当前仍未修复，旧live不代表修改后新源码效果。
