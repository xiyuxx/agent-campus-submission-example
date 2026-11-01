# 当前运营体验与局部失效证据

业务源码基线b91fc03，SHA256为`ded21a3afeace5e55ef26572c19918d5ef8a2cab33adf3c3eb4bef719cf7f20b`。后续文档、验证脚本和CI准备不改变业务源码；最终commit以实际Git输出为准。v6及更早记录是历史版本。

## 当前检查结论

- 局部失效：24项存储/依赖检查通过，见[阶段一](phase1/junit.xml)。初轮22通过/2失败也保留于initial-junit.xml：统一移除素材错误码；旧用例改用实际时段冲突，保留历史回执/当前失效断言。
- 运营输入/导出：35项相关检查和7项CLI通过，见[相关JUnit](phase2/operator-junit.xml)、[CLI](phase2/cli-junit.xml)；跨本机时区输入断言调整后[7项再次通过](phase2/cli-portable.xml)。
- 全部438项均有通过记录，未跳过，但没有一次完整无失败。三个整套实际结果424/14、379/59、418/20（通过/失败），见[汇总](verification.json)。失败主要为Windows本机回环socket WinError10013；第二次有2项CLI上层结构错误。原始完整JUnit保留，不能用合并覆盖冒充一次全绿。
- 两个全新venv安装当前wheel，源码摘要匹配。首次8项安装/运行检查通过，完整测试仍失败；第二个环境demo也受同类限制，随后完整执行仍有20项环境失败。见install及install-final目录。首次日志采集混合编码缺陷已修复，原JUnit保留。
- [只读证据检查](evidence-check-final.json)：43条应用运行的源码摘要、Trace配对、观察送达及两条真实路径通过。初次39条检查报告也保留。
- [手动离线CI](../../.github/workflows/offline-verification.yml)已备齐，独立Windows runner、Python3.13、锁依赖、wheel安装和全套默认离线测试；尚未推送/执行，无CI通过结论。

## 实际运行与交接样例

| 目录 | 说明 |
| --- | --- |
| [current-runs](current-runs/index.json) | 15次Mock + 8条live，覆盖正常、信息不足、风险/阻断、冲突、重复、保存失败、回执与补查 |
| extended-runs | 5次Mock，更换占用再提交、确认保持和查询失败，包括before记录 |
| [task-runs](task-runs/action-log.json) | 8次Mock、13个任务操作快照：未来方案复用、确认过期重放、修改、补资料、取消及保存失败恢复 |
| operator-runs | 初次3次Mock运营连续记录，保留原始样例 |
| [operator-runs-final](operator-runs-final/journey.json) | 4次Mock：M01确认、M02缺资料、中文表单补齐并继续，取消M02后重查M01仍reused/confirmed/revision1 |

共35次Mock、8条live。每次应用运行包含input/output/trace/run/summary，附数据库快照；快照表示对应执行时刻。合成用户接受、补资料、取消不是候选人本人试用。

直接查看：[两项就绪CSV](operator-runs-final/two-materials.csv)、[取消后CSV](operator-runs-final/after-cancel.csv)、[M01确认保持的JSON快照](operator-runs-final/after-unrelated-update.json)、[中文运营摘要](operator-runs-final/operator-summary.txt)。CSV使用UTF-8 BOM并保留实际导出换行字节；包含本机日期/偏移、审核/风险、确认、草稿版本、保存状态、内容版本和导出时间。旧CSV未覆盖，内容相同的CSV/JSON版本一致。

## 真实模型路径与限制

提示6.0、deepseek-flash、thinking disabled、temperature=0，每场景仅一次。8条共36次请求，total_tokens累计69037（含缓存，不是账单费用），见[live分析](live-analysis.json)。

[正常路径](current-runs/live_normal/trace.jsonl)观察18:00可用后选择保存18:00并消费回执；[抢槽路径](current-runs/live_save_race/trace.jsonl)观察18:00冲突后先选19:00，受控保存冲突后模型重查并改选20:00，仍待确认。保存失败有一次INVALID_FINISH纠错，原事件保留。小样本不外推稳定成功率；新增表单/CSV/确认保持主要由离线实际执行验证。

## 复现

按根README安装后，在仓库根目录使用新输出目录：

```powershell
.venv/Scripts/python.exe scripts/check_evidence.py examples/operator_experience_v7 --live-directory examples/operator_experience_v7/current-runs
.venv/Scripts/python.exe scripts/check_decision_examples.py examples/operator_experience_v7/operator-runs-final
.venv/Scripts/python.exe scripts/generate_operator_examples.py --output runtime/operator-new
.venv/Scripts/python.exe scripts/verify_install.py --runtime runtime/install-new --output runtime/install-report-new --full-tests
```

重生成真实模型材料使用generate_review_examples.py，显式`--live --env-file .env`。只读验证不调用模型，不改原证据。文本归档只规范LF及行尾空格，CSV实际字节保留；原测试/安装文件备份在忽略的runtime中。顶层manifest覆盖其余全部归档文件，排除自身。

当前尚未推送本轮优化或执行CI。只保存本地草稿，无平台预约或真实发布；本人独立试用未记作完成。剩余验证和全部限制见[评估](../../docs/03-evaluation.md)。
