# 对抗性业务测试证据索引

2026-10-31，离线工程验证，无真实模型调用。完整场景和预期见 [复现报告](../../docs/18-adversarial-test-review.md)，可执行入口为 [归档脚本](../../scripts/run_adversarial_tests.py)。

| 目录 | 内容 | 结果 |
| --- | --- | --- |
| [counterexamples-utf8](counterexamples-utf8/run.json) | 新增 50 场景，启用 `--runxfail` | 44 passed、6 failed、0 error；5 类缺陷 |
| [regression-utf8](regression-utf8/run.json) | 原有与新增 121 场景，保留 strict xfail | 115 passed、6 xfailed；xfail 不代表修复 |

每组包含实际 `pytest.txt`、`junit.xml`、`run.json`；pytest 文本与 XML 仅规范化换行和行尾空白，结果及错误信息保持实际值。场景子目录中的 input/output/trace/run/summary 由应用服务实际生成，`database_check.json` 是测试在最终断言前查询真实数据库所得。无数据库文件、密钥或真实隐私数据。

`counterexamples-utf8` 的非零退出码是预期业务断言失败，不是伪造的通过证据。受控模型和资料更新时机只用于重现防线缺陷，不计作真实模型动态路径。
