# 原时间与替代候选合并查询：实际证据

2026-10-31，功能契约见 [第 23 号规范](../../docs/23-automatic-slot-options.md)。本目录保留合并查询初次实现的提示 3.0 证据，以及用户随后增加动态工具、偏好和恢复后的提示 4.1 复验；两者不混用。当前业务实现已随 0866446 提交，补查边界随后由 e10cc53 收紧。

## 当前版本复验

基线 1e16f93，实际业务源码与提示 4.1 的现有 live 证据相同。完整离线显式 --runxfail：**356 passed、0 failed/error/skipped/xfail**，包含合并查询 18 项、有限节点独立 oracle 128 项和后续决策边界。见 [实际输出](current-regression/pytest.txt)、[JUnit](current-regression/junit.xml)、[源码/测试摘要和命令](current-regression/run.json)。

[当前核对记录](current-audit.json) 确认被测源码、全部测试文件与当前文件匹配，七组输出、查询参数、观察送达与保存数据库状态一致；[静态检查与现有证据校验输出](current-quality-checks.txt) 中 Ruff 两项通过，现有提示 4.1 证据的 9 组运行、65 个文件哈希全部核对通过。当前归档统一 LF，[manifest.json](manifest.json) 使用归档文件的实际字节摘要。

| 场景 | 实际程序输出 | 查询次数 | 数据库结果 |
| --- | --- | --- | --- |
| 原时间空闲 | [saved/completed](current-regression/scenarios/test_automatic_options_service0/output.json) | 1 | 一份草稿、一条历史，保留 18:00 |
| 原时间冲突 | [saved/needs_input](current-regression/scenarios/test_automatic_options_service1/output.json) | 1 | 保存 19:00 待确认，一份草稿 |
| 无候选 | [needs_input](current-regression/scenarios/test_automatic_options_service2/output.json) | 1 | 未保存 |
| 资料不足 | [needs_input](current-regression/scenarios/test_automatic_options_service3/output.json) | 0 | 未查询排期、未保存 |
| 查询失败 | [failed](current-regression/scenarios/test_automatic_options_service4/output.json) | 1 | 未保存，不伪造候选 |
| 保存失败 | [failed](current-regression/scenarios/test_automatic_options_service5/output.json) | 1 | 无草稿、无成功历史 |
| 重复请求 | [reused/needs_input](current-regression/scenarios/test_automatic_options_service6/repeat/output.json) | 每次运行 1 | 同一草稿、修订 1，不新增历史 |

以上均为受控 Mock 的真实程序运行。模型调用 query_slots 时没有 requested/nearest 参数；同一观察包含原时段以及冲突时程序自动检索的候选。保存动作发生在该观察已回传之后，保存事务再次复核。每组相邻目录保存 input/output/run/trace/summary 和实际读取的 database_check；重复场景另保留 first。

现有 [提示 4.1 live 证据](../decision_improvements_v4_1/README.md) 本轮仅只读核查：正常路径 1 次排期查询；受控抢槽路径 2 次（首次保存失败后重新查询）。每次都是完整查询，无独立 nearest。已有证据保留原始运行版本和受控占用说明；本轮新真实模型调用 **0**，不把只读核对当作重跑。

## 初次实现历史

- [初次定向测试](targeted/pytest.txt)：18 passed；[最终定向归档](targeted-final/pytest.txt)：18 passed，包含七组场景。
- [初次完整回归](regression/pytest.txt)：342 passed、0 xfail；[当时核对](audit.json) 与 [Ruff](quality-checks.txt) 仅支持提示 3.0 的被测源码，不代表当前版本。
- 当时唯一迁移的旧断言为“模型必须先单独观察原时间冲突才能查询 nearest”，现改为“原时间可用不检索替代、旧 query 参数拒绝”；其余 324 项迁移后和新增 18 项共同通过。128 个 oracle 用例已走完整查询接口。

所有测试均离线，脚本化模型只用于工程验证。没有数据库迁移、真实发布或远程推送。归档发生在提交前，run.json 的 base_commit 指向父提交，被测内容以 source_sha256 和测试文件摘要识别。
