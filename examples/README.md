# 实际运行证据入口

[GitHub推送后克隆复验](submission_push_20261101/README.md)记录远程基线、360项独立回归与归档核对。

当前交付入口为 [2026-11-01复核](submission_audit_20261101/README.md)：当前源码15次离线业务运行、最终360项测试、干净安装与证据检查报告。

真实模型优先看 [提示4.1归档](decision_improvements_v4_1/README.md)：7组Mock和2条DeepSeek live。两条live源码摘要与当前一致，本轮只读核查，没有重复调用模型。观察与下一动作的逐步对比见 [评估](../docs/03-evaluation.md#真实模型的两条路径)。

其他目录是历史阶段、旧协议、反例与修复证据，应按各自README、run.json和源码摘要解读，不将旧失败或旧成功套到当前源码。设计检查仅验证文档或DDL，不充当Agent运行。Mock仅验证工程流程。

业务运行包含input.json、output.json、trace.jsonl、run.json、summary.md；记录自动生成，参数和观察只保留必要信息，不包含密钥、原始私密上下文或内部思维过程。测试套件的run.json是另一契约，不能当作业务输入输出。

复现命令和只读检查见 [根README](../README.md)。新运行必须使用新目录，不覆盖历史。数据库快照来自执行当时的真实查询；克隆后检查快照一致性不等于重新访问原数据库。
