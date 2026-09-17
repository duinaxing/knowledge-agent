# 质量、容量与恢复验证

本工具链只使用本地隔离数据库，不修改业务 `.env`。命令在项目根目录执行；PowerShell 入口会自动切换工作目录。

## 质量评测

`evaluation/quality-v1.json` 是固定题库和语料定义：150 个案例，开发集 50、保留集 100；主题互不重叠；十类问题各 15 个。多轮案例使全量问答为 165 次。参考标注仍为待人工复核，不属于独立盲测结论。

```powershell
./run-quality.ps1 -Split dev
./run-quality.ps1 -Split heldout
# 以下显式开关会产生真实模型调用费用：
./run-quality.ps1 -Split dev -Real -Experiment quality-v1
./run-quality.ps1 -Split heldout -Real -Experiment quality-v1
```

默认模拟生成模型、真实本地 Embedding；模拟结果仅验证 HTTP、SSE、持久化及校验程序，不能算语义质量验收。真实模式通过已配置的 DeepSeek 运行，不使用业务数据库中的问题或文件。

数据库为 `knowledge_quality_test`，文件及结果位于 `runtime/quality/`；API/Embedding/网关使用 8100/8101/8102，与压测互斥。端口占用直接退出，不结束其他服务。初始化完成的语料可以复用；不完整或版本不一致的语料拒绝静默覆盖。

每个结果目录保存：`manifest.json`、`results.jsonl`、分类 `summary.json`、`review.csv`、`REPORT.md`。回归错误仍计入分母；检索失败的可回答案例 Recall 为零。关键事实匹配是字符串检查，不能证明事实或语义正确。

独立审阅者先确认参考答案，再填写 `reference_approved=1`、`reviewer`、`human_task_success`（0/1）、引用事实数和受到引用支持的事实数；拒答题填写 `human_refusal_correct`（0/1）。没有审阅的项保持空白。

```powershell
$env:PYTHONPATH='backend/src'
python -m knowledge_quality.review runtime/quality/results/<本轮目录>/review.csv
```

验收目标：目标证据 Recall@6 ≥90%；人工任务正确率 ≥90%；引用事实支持率 ≥95%；正确拒答率 ≥95%；越权、伪造引用、串会话为零。人工审阅不完整时不会输出完整通过。冻结保留集一旦用于调参，须在报告中标记为回归集，另建版本进行独立验收。

## 模型预算

质量：300 问答／1500 模型请求；真实压测：200 问答／1000 模型请求。两类预算分开，存储于各自 `runtime/<suite>/budgets/<experiment>/admissions.sqlite`。原子预占，模型重试按新请求计数，启动新进程不清零。

同一验证批次必须保持相同 `Experiment`；更换实验编号会新建独立额度，不能用来绕过已批准批次的上限。不得删除账本来重置额度。预算用尽的任务标为受限，不算成功。工具不估算货币费用。

## 完整容量验证

```powershell
./run-validation.ps1
# 单独运行四组对照，每组 3 轮 100 人峰值：
./run-validation.ps1 -Only matrix
# 当前配置完整持续负载及复杂问法：
./run-validation.ps1 -Only full
./run-loadtest.ps1 -Profile complex -Workers 8
# 真实模型：10 预热 + 90 日常 + 100 峰值
./run-loadtest.ps1 -Real -Workers 8 -Experiment real-load-v1
```

四组是 4/8 槽位 × 开启/关闭快路径；统一合成语料、固定随机种子、模型每次调用延迟 2 秒、任务总期限 60 秒。完整测试维持原定分钟数，不通过缩短持续时间替代验收。

复杂补测分别执行不点名文档、双文档比较和同会话追问，固定 10 个同时活跃用户，独立于 100 人峰值，不外推为复杂问答的百人容量。

完整配置启用测试专用提交链路计时：总时间、鉴权、连接获取、SQL、含锁 SQL 和任务创建。各阶段存在包含关系，不能直接相加；含锁 SQL 时间不是纯锁等待，结合 PostgreSQL 采样中的锁等待数分析。`query-plans.json` 是停止负载后的合成数据代表查询计划。

客户端与服务端共享电脑，其他进程会竞争资源。报告分别呈现 HTTP 提交、任务完成、有效回答和恢复，不报告首 token 延迟。原始数据保存在 `runtime/loadtest/`，不进入 Git。

## 恢复演练

等待隔离压测全部结束，再执行：

```powershell
./run-recovery.ps1 -Source knowledge_loadtest_100
```

需要本地 PostgreSQL 工具及 8001 端口的现有 Embedding 服务进行恢复后的真实检索验证。源实例必须停止，脚本取得与测试启动器相同的排他锁、拒绝有其他数据库连接的源，再锁定表、导出 PostgreSQL 和原件。仅允许 `knowledge_loadtest_100` / `knowledge_quality_test` 两个源。

可以设置 `RECOVERY_EMBEDDING_URL=http://127.0.0.1:8101` 复用正在运行的另一套隔离测试 Embedding，避免重复加载 CPU 模型。演练在测试源中暂存中断任务与删除标记，备份结束后移除；恢复目标验证这些状态，同时对已完成回答、版本、权限、片段做摘要一致性比较。

备份包含数据库、原件、哈希、迁移与代码版本、关键表数量和 Embedding 信息。恢复仅允许 `knowledge_restore_test` 和 `runtime/recovery/restored-files`，已有任一目标就拒绝覆盖；不会自动删库重试。失败现场保留供检查。

恢复时作废登录会话，并将排队、执行中及中断任务标记为 `RESTORED_REQUIRES_RETRY`，提升执行代次；不会自动重放模型请求。原文件引用重映射到恢复目录。随后验证登录、历史、向量检索、原文与权限。备份完整性可单独检查：

```powershell
python -m knowledge_quality.recovery --verify runtime/recovery/<备份目录>
```

恢复目标 15 分钟；记录实际恢复时间和备份一致性时间点。当前没有自动备份周期，不承诺生产 RPO。恢复报告位于 `runtime/recovery/REPORT.md`。
