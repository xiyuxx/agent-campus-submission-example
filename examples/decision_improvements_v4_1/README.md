# 决策信息、偏好与冲突恢复：实际验证（提示 4.1）

2026-10-31，业务源码 e10cc53，模型配置 deepseek-flash、thinking disabled、temperature=0，实际返回模型与 usage 见每组 trace.jsonl。7 组 Mock 与 2 组 live；全部使用合成素材，仅保存草稿。

| 场景 | 模式 | 实际结果 |
| --- | --- | --- |
| normal | Mock | 18:00 空闲，saved/completed |
| duplicate | Mock | 复用同一草稿和修订，不新增历史 |
| insufficient | Mock | 缺必要资料且无来源，needs_input、不保存 |
| blocked | Mock | 版权明确不允许，blocked、不保存 |
| save_failure | Mock | 注入提交前失败，failed，无草稿 |
| prefer_before | Mock | 选择 17:00，saved/pending，原 18:00 hard 不变 |
| save_race | Mock | 19:00 保存时被占，重新查询，保存 20:00 待确认 |
| normal | DeepSeek live | 5 次有效模型动作，原 18:00 保存成功，无可选字段补查 |
| save_race | DeepSeek live | 7 次有效模型动作，消费保存冲突后重新查询，保存 20:00 待确认 |

真实抢槽顺序为读取→审核→查询→保存19:00失败→重新查询→保存20:00→消费回执并 needs_input。占用由脚本在第一次实际保存前受控插入；不是真实运营用户抢占，不是脚本替模型选择新候选。初次和再次查询结果均保留在草稿证据中，实际数据库只有一份草稿和一条成功历史。模型业务参数不携带内部身份，工具回执及本地 Trace 保留关联。

完整离线回归实际为 **356 passed，42.22 秒**，原始输出见 [pytest.txt](regression/pytest.txt)。包括既有 342 项和新增 14 项；离线成功率不代表模型质量。首轮真实样本曾无依据补查，保留于 [4.0 记录](../decision_improvements_v4/README.md)。修正后仅各一次真实样本，不外推稳定性或成本收益。

本轮未接入外部视频审核，只提取本地审核接口；没有远程报告、任务轮询、视频理解或真实发布。原始 HTTP 请求/响应未归档；HTTP MockTransport 回归检查不能冒充 live 原始报文证据。

复现（输出必须是新目录，--live 会调用真实模型）：

```powershell
.venv/Scripts/python.exe scripts/generate_decision_examples.py --output examples/new_decision_run --live --env-file .env
.venv/Scripts/python.exe scripts/check_decision_examples.py examples/new_decision_run
```

省略 --live 时只生成七组离线实际运行。只读核查验证哈希、类型、工具调用配对、保存观察送达、输出与数据库快照的一致性；数据库快照来自生成时实际读取，克隆后核查不等于重新执行或访问原数据库。
