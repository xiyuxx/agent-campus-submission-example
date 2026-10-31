# 缺陷修复实际证据

本目录为 2026-10-31 三项修复的离线执行记录。真实模型调用 0，受控 HTTP 响应不代表 DeepSeek 服务实际返回过畸形内容。历史失败复现见 [第 20 号报告复核](../test_report20_verification/README.md)。

每次归档的 run.json 包含源码摘要、当时 HEAD/脏状态、测试文件摘要、实际命令和测试结果；测试发生在提交前，因此 HEAD 指向父提交，源码摘要才标识被测内容。pytest.txt/JUnit 为程序实际输出，scenarios 为真实运行的合成场景，不含凭据或原始模型上下文。

- [RT-01 首轮](rt01/pytest.txt)：285 passed、2 failed、9 xfailed。两项失败为新增测试误用异常文本匹配 code，随后修正。
- [RT-01 最终完整回归](rt01-final/pytest.txt)：287 passed、9 xfailed、0 failed/error；9 个 xfail 为尚未修复的 RT-02（8）与 ADV-01（1）。新增邻域 7 项全通过。源码与测试摘要见 [run.json](rt01-final/run.json)。Ruff check/format --check 通过。
- [RT-02 完整回归](rt02/pytest.txt)：311 passed、1 xfailed（ADV-01），0 failed/error。[单次坏响应后恢复](rt02/scenarios/provider-recovery/output.json) 实际保存草稿，返回 needs_input；[run.json](rt02/run.json) 记录当时测试文件摘要。
- [RT-02 最终结构测试](rt02-structure-final/pytest.txt)：测试闭包显式绑定后 16 passed、0 xfailed，静态检查也通过。最终测试摘要见 [run.json](rt02-structure-final/run.json)。业务源码与 RT-02 完整回归相同。
- [三项修复最终完整回归](final-regression/pytest.txt)：显式 --runxfail，324 passed，0 failed/error/skipped/xfail；包含原 289 项和新增 35 项。[核对记录](final-regression/audit.json) 确认原用例全部保留并通过、源码/全部测试摘要匹配最终文件；[Ruff 原始输出](final-regression/quality-checks.txt) 两项检查均通过。[run.json](final-regression/run.json) 保存测试配置与摘要。

修复说明见 [第 22 号记录](../../docs/22-defect-fix-results.md)。
