# GitHub推送后克隆复验

2026-11-01，从公开目标仓库重新克隆，核对远程main与本地提交f3e7b9d8fff8b3b59f0af062a4d8b75706ff5be2一致。原22次历史与交付修正均已上传，未改写历史或强推。此目录由后续文档提交归档，不把自身提交误称为已经运行上述测试的基线。

使用此前按requirements.lock全新安装的wheel环境、Python隔离导入，在克隆仓库运行测试和检查：

- [pytest](pytest.txt)与[JUnit](junit.xml)：360 passed，46.95秒。
- [Ruff检查](ruff-check.txt)、[格式检查](ruff-format.txt)：通过。
- [业务证据检查](evidence-check.txt)：17次运行通过，包括15次Mock及2条既有live。
- [live归档检查](live-archive-check.txt)：9组运行、65个文件哈希通过。
- 两份manifest在远程克隆中与实际文件字节一致，277个文档文件链接无缺失，必需目录/文件完整，.env未跟踪。

完整执行参数、输出和范围见[verification.json](verification.json)。未新增真实模型调用；无私密凭据或原始聊天导出。用户之后计划自行试用，尚无本人试用结果，本报告不能替代本人独立验证。
