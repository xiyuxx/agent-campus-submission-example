# 本地身份与双向最近节点：实际运行证据

2026-10-31，业务源码提交 410ae2b，提示版本 2.0。以下文件全部来自实际程序运行；Mock 与 live 分开，不是设计预期输出。

| 目录 | 模式 | 实际结果 |
| --- | --- | --- |
| normal | Mock | 原 18:00 空闲，saved/completed |
| duplicate | Mock | 同原请求第二次运行，复用相同草稿 ID，第 1 版不变 |
| two_sides | Mock | 18:00 冲突，返回 17:00/19:00，选 19:00 保存待确认 |
| cross_day | Mock | 返回前一天 23:00 与后一天 09:00，选后者保存待确认 |
| insufficient | Mock | 缺版权且无补查资料，needs_input、不保存 |
| save_failure | Mock | 显式注入提交前失败，failed，无已保存草稿 |
| live_normal | DeepSeek live | 7 次模型/6 次工具，保存原 18:00，completed |
| live_two_sides | DeepSeek live | 6 次模型/5 次工具，选 19:00，saved、pending、needs_input |

每组含 input.json、catalog.json、output.json、summary.md、trace.jsonl、run.json，另有对实际本地数据库读取的 database_check.json。数据库文件留在忽略的 runtime/local_binding_nearest_v2，不提交。完整清单见 [index.json](index.json)；类型、配对、观察、投影字段与数据库交叉核查见 [checks.json](checks.json)。

## 真实双向冲突路径

用户仅要求 2026-11-03 18:00，未提供候选。账号预设 17:00、17:30、18:00、18:30、19:00，其中 17:30、18:00、18:30 已占用。

模型实际依次：读取基本资料 → 规则检查 → 查询原时间（冲突）→ nearest → 保存 19:00 → 消费回执并 needs_input。nearest 工具先跳过 17:30 占用找到 17:00，再跳过 18:30 占用找到 19:00，两侧各一个。模型选 19:00，只创建一份待确认草稿；原 18:00 hard 不变，未发布。

17:00 和 19:00 都距离原要求一小时；选择 19:00 不证明它比 17:00 更近，也不代表用户已接受。正常 live 路径虽已通过规则，仍自行选择补查授权引用，未据此声明最少调用。

模型业务消息使用白名单投影；内部 ID、修订、完整来源和核查证据留在本地。Trace 中出现内部 ID 是本地审计记录，不等同于将它们发送模型。HTTP MockTransport 测试捕获实际模型请求并检查身份隔离；live 未归档原始 HTTP 报文，投影重建核查不能冒充报文归档。

## 实际检查与复现

完整离线回归为 **138 passed、1 xfailed**；原始归档见 [pytest.txt](regression/pytest.txt) 与 [run.json](regression/run.json)。已知 ADV-01（补查 null 字段冲突）仍未修复。该结果不是模型成功率。

生成命令（目标必须为新目录，live 显式启用；会产生模型调用）：

```powershell
.venv/Scripts/python.exe scripts/generate_nearest_examples.py --output examples/local_binding_nearest_v2 --live --env-file .env
```

离线核查已生成证据，原本地库存在时可交叉核对（不调用模型）：

```powershell
.venv/Scripts/python.exe scripts/check_nearest_examples.py examples/local_binding_nearest_v2 --runtime runtime/local_binding_nearest_v2
```

克隆后没有 runtime 数据库时省略 --runtime，仍可核查类型、调用/观察关联、保存回执送达和当前源码重建的投影。核查会更新 checks.json，运行前保留原记录以便比较。全套默认测试不产生真实 API 调用。
