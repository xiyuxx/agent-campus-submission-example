# 缺陷修复实际证据

本目录为 2026-10-31 三项修复的离线执行记录。真实模型调用 0，受控 HTTP 响应不代表 DeepSeek 服务实际返回过畸形内容。历史失败复现见 [第 20 号报告复核](../test_report20_verification/README.md)。

每次归档的 run.json 包含源码摘要、当时 HEAD/脏状态、测试文件摘要、实际命令和测试结果；测试发生在提交前，因此 HEAD 指向父提交，源码摘要才标识被测内容。pytest.txt/JUnit 为程序实际输出，scenarios 为真实运行的合成场景，不含凭据或原始模型上下文。

- [RT-01 首轮](rt01/pytest.txt)：285 passed、2 failed、9 xfailed。两项失败为新增测试误用异常文本匹配 code，随后修正。
- [RT-01 最终完整回归](rt01-final/pytest.txt)：287 passed、9 xfailed、0 failed/error；9 个 xfail 为尚未修复的 RT-02（8）与 ADV-01（1）。新增邻域 7 项全通过。源码与测试摘要见 [run.json](rt01-final/run.json)。Ruff check/format --check 通过。

修复说明见 [第 22 号记录](../../docs/22-defect-fix-results.md)。
