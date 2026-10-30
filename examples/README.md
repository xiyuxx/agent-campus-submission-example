# 运行证据约定

当前没有业务执行记录。数据库设计的实际结构检查记录单独存放于 [design_checks/database-design.txt](design_checks/database-design.txt)，不属于 Agent、Mock/live 或六类业务运行证据。下面是目标结构，不是已经生成的业务证据：

```text
examples/
  normal/{input.json,output.json,trace.jsonl,run.json}
  insufficient/{input.json,output.json,trace.jsonl,run.json}
  tool_failure/{input.json,output.json,trace.jsonl,run.json}
  test-results.txt
```

每组证据来自同一次实际执行。`run.json` 记录命令、代码版本、运行模式、模型标识、时间、退出码及配置摘要；不得包含密钥。
模型支持时记录采样参数，不承诺模型输出逐字确定。

`trace.jsonl` 事件协议见 `docs/00-engineering-standards.md`。每条记录必须标明 `live` 或 `mock`。
Mock 记录只说明工程流程可执行，不支持真实模型能力结论。

若模型不可用，保留错误事实并记录缺失证据，不用 Mock 冒充真实路径。
评估文档只引用真实存在且已审查的文件。
