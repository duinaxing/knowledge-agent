# 知序 · 企业项目知识查询 Agent

项目 2：员工联合查询授权项目的当前状态、负责人和历史文档，查看来源、时间与不确定性。范围依据 [产品技术文档](PRODUCT_TECH_SPEC.md)。

当前实现包括 React 管理/查询界面、FastAPI、统一 ACL、版本化文档、四工具 LangGraph、任务/SSE、模型适配与基线评测入口。PostgreSQL 工程测试、进程崩溃恢复和真实模型联调已执行；完整产品质量门槛与人工评测状态见 [验收记录](docs/ACCEPTANCE.md)。

## 本次功能更新

- 知识查询：相关文档使用 RAG 并展示引用；通用问题由 DeepSeek 回答，显示“DeepSeek 通用回答”。
- 会话侧栏的删除入口及聊天区“删除当前会话”可删除会话，操作前会确认。
- 项目记录新增“删除项目”。关联文档保留管理员预览，但退出知识查询，避免误删或扩大权限。
- 编辑项目使用“项目简要情况”代替原“别名”输入框。
- 数据库新增迁移 0003；现有数据已迁移，迁移前已备份。

## 账号、知识库与历史会话

- 管理员：`admin` / `123456`。只有管理员可打开数据管理，上传、发布和授权知识文档。
- 普通用户可在登录页注册（用户名 3–40 位小写字母、数字、下划线，密码至少 6 位），注册后只能问答；可读组织公开知识或管理员授予的资料。
- 侧栏可新建、选择和分页浏览会话；聊天区可重命名、加载更早消息。注销、重登和改密不会清除历史。
- 点击“修改密码”，提供旧密码与新密码，成功后所有设备需重新登录。
- 知识更新后的历史仍保留；被删除或撤权的来源所涉及的历史回答会隐藏。新提问使用当前资料。
- [七项功能核对与性能验收](docs/FEATURE_ACCEPTANCE.md) 包含测试方法、实测结果及部署边界。

## 模型分工

- **生成回答：DeepSeek**，通过官方兼容接口调用 `deepseek-flash`，模型名可配置。
- **语义检索：本地 BAAI/bge-small-zh-v1.5**，CPU 执行，512 维；不需要另外购买 Embedding API。也支持替换为兼容 `/embeddings` 的 API。
- BM25 和向量检索都先按用户权限和版本过滤，再用 RRF 融合。可选远程 reranker 默认关闭。
- `MODEL_MODE=test` 的哈希向量只服务确定性测试，绝不能用于语义评测或展示真实效果。

## 100 人隔离压力测试

[2026-09-16 优化复测](docs/PERFORMANCE_OPTIMIZATION_2026-09-16.md)：加入指定文档的简单问答快路径，应用默认 8 槽位。模拟峰值 100/100 按时回答、P95 28.985 秒；提交 P95 1.093 秒仍略高于目标，真实模型与持续压力尚未验收。

技术细节和验收口径见 [100 人并发压测方案](docs/LOAD_TEST_PLAN_100_USERS.md)。`./run-loadtest.ps1 -Profile smoke` 验证工具链与单轮峰值；`./run-loadtest.ps1` 执行完整模拟场景；`./run-loadtest.ps1 -Real` 显式启用真实模型，最多 200 问答 / 1000 次模型请求。结果在 `runtime/loadtest/results/`，不使用业务数据库。

[首次验证结果](docs/LOAD_TEST_RESULTS_2026-09-15.md)：100 人峰值提交全部成功，但仅 42 个有效回答，未达峰值目标；完整持续测试和真实模型阶段尚未执行。

## 当前电脑一键启动

**一键启动：双击 `start.cmd`**。自动启动数据库、Embedding、API、后台任务和前端，检查就绪后打开浏览器。重复启动会复用服务；启动失败会保留错误提示，日志在 `runtime/`。不会重置用户、文档或会话，也不会重新导入演示数据。

命令行也可运行 `./start.ps1`；`./start.ps1 -Restart` 重启应用服务，`./start.ps1 -NoBrowser` 仅启动、不打开浏览器。本入口适用于已按下文安装和初始化的当前电脑。

本机已安装依赖、下载 BGE 权重并初始化合成数据。在 `project/` 执行：

```powershell
./start-postgres.ps1
./start-local.ps1
```

访问 [本地工作台](http://127.0.0.1:5173)。员工账号 `employee1` 至 `employee6`，管理员 `admin`，当前所有账号密码统一为 `123456`；初始化配置为 `.env` 的 `SEED_PASSWORD`。修改代码后用 `./start-local.ps1 -Restart` 重启本项目服务。

新电脑使用下方依赖/模型配置步骤，然后安装独立数据库运行时并初始化：

```powershell
conda create --prefix ./runtime/postgres -c conda-forge postgresql=16 pgvector=0.8.6 -y
./start-postgres.ps1
# .env 设置 DATABASE_URL=postgresql+psycopg://knowledge:knowledge@127.0.0.1:5432/knowledge
$env:PYTHONPATH = 'backend/src'
python -m knowledge_agent.bootstrap --migrate --seed
./start-local.ps1
# 管理页确认索引 ready 后执行：
python -m knowledge_agent.bootstrap --publish-synthetic
```

数据库脚本仅绑定 127.0.0.1，使用本地合成演示凭据。启动脚本的 Python 路径应按新电脑环境调整。

## 依赖配置与可选 SQLite 演示

下列 SQLite 演示模式便于在尚无 PostgreSQL 的电脑上联调，**不验证 PostgreSQL 锁、pgvector 算子和持久检查点**。正式架构仍为下一节的 PostgreSQL。

1. 激活约定环境：`conda activate hello_agents_py311`。在 `project/` 执行：

   ```powershell
   python -m pip install -e './backend[test,local-embedding]'
   Push-Location frontend
   npm ci
   Pop-Location
   ```

2. 首次将 `.env.example` 复制为 `.env` 并配置。已有 `.env` 时不要覆盖。设置 DeepSeek 密钥、本地 Embedding 配置及至少 6 位的 `SEED_PASSWORD`（本地演示为 `123456`）：

   ```dotenv
   MODEL_BASE_URL=https://api.deepseek.com
   MODEL_NAME=deepseek-flash
   MODEL_API_KEY=填写自己的密钥
   MODEL_MODE=real
   EMBEDDING_BASE_URL=http://127.0.0.1:8001
   EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
   EMBEDDING_DIM=512
   EMBEDDING_API_KEY=local-only
   ```

3. 下载真实模型：

   ```powershell
   $env:PYTHONPATH = 'backend/src'
   python -m knowledge_agent.local_embeddings --download
   ```

4. 启动服务：`./start-local.ps1 -SqliteDemo`。生成 7 个账号、3 个组、6 个项目及 60 份合成文档；worker 处理上传队列。
5. 在文档索引完成后发布合成版本：

   ```powershell
   $env:DATABASE_URL = 'sqlite:///runtime/demo.db'
   python -m knowledge_agent.bootstrap --publish-synthetic
   ```

6. 打开 [本地工作台](http://127.0.0.1:5173)。员工账号 `employee1` 至 `employee6`，管理员 `admin`。密码为初始化时的 `SEED_PASSWORD`，修改环境值不会重设已存在密码。

启动脚本后台运行服务，PID 与日志位于 `runtime/`。不要在同一演示 SQLite 库上启动多份 worker；并发能力通过 PostgreSQL 环境评测。

## PostgreSQL / pgvector 部署

需要 Docker Engine / Docker Desktop 与 Compose。绑定本机端口，演示账号不应直接公开部署。

```powershell
docker compose up --build -d
# 等待 / 管理页确认 60 份文档处理为 ready，再发布：
docker compose exec worker python -m knowledge_agent.bootstrap --publish-synthetic
```

访问 [Compose 工作台](http://localhost:8080)。模型权重由宿主机 `runtime/models` 只读挂载；先执行本机模型下载步骤。迁移/seed 由 init 服务执行，原件与数据库分别位于具名 volume。

已有 PostgreSQL 可配置 `DATABASE_URL`，然后执行 `python -m knowledge_agent.bootstrap --migrate --seed`。需要安装 vector 和 btree_gist 扩展的权限。API 和 worker 分别用 `uvicorn knowledge_agent.api:app`、`python -m knowledge_agent.worker` 启动。

## 常用查询与权限演示

- `employee1`：问“PRJ-001 现在谁负责，是什么状态？”；追问“那验收标准呢？”
- `employee2`：问“北辰项目现在谁负责？”；其拥有两个同名项目权限，会先澄清。
- 管理员发布新版本后，历史保留并提示知识更新；修改 ACL 后，相关历史回答重新检查访问权限。
- 上传仅支持 UTF-8 Markdown/TXT 与文本 PDF，10 MiB / 100 页；新文档默认拒绝员工读取，需设置 ACL 并发布。
- 管理员查看反馈只返回该条回答，不可读取其他员工完整会话。

## 测试与评测

```powershell
python -m pytest backend/tests -q
Push-Location frontend
npm test
npm run build
Pop-Location
python evaluation/create_dataset.py
python evaluation/run.py --split dev --limit 2 --trials 1 --baselines B2,B3
```

默认评测连接 `.env` 的数据库，需要先准备并发布合成数据；SQLite 演示评测另设 `DATABASE_URL=sqlite:///runtime/demo.db`。原始保留集命令（功能已更新，新增验收文档会改变当前语料；如需严格复现实验应还原对应合成数据快照）：

```powershell
python evaluation/run.py --split heldout --trials 3 --concurrency 5 --baselines B0,B1,B2,B3
```

它会产生真实模型调用费用。基线分别为授权关键词搜索、固定文档 RAG、固定跨源编排、动态 Agent。所有结果记录模型、调用数、用量和耗时。120 题是**合成题与参考答案草稿**，80/40 按项目分组切分；保留项目使用客户目录和供应商审计两个独立主题，尚待独立人工复核，未填写的人工作答成功率/引用支持率保持 null。

项目独立成为 Git 仓库时，`.github/workflows/ci.yml` 可直接使用；在当前上级目录运行时，嵌套 workflow 不会自动触发。CI 含工程测试和真实 PostgreSQL smoke；尚未在远程 CI 执行。

## 文档

- [2026-09-14 对抗性审查及修复报告](docs/ADVERSARIAL_REVIEW_2026-09-14.md)

- [架构与实现边界](docs/ARCHITECTURE.md)
- [验收记录与已知限制](docs/ACCEPTANCE.md)
- [480 次真实模型评测报告](docs/EVALUATION.md)
- [3 分钟演示脚本](docs/DEMO.md)
- [依赖许可证](docs/LICENSES.md)
- [OpenAPI](docs/openapi.json)，运行时也可访问 `/docs`。

`.env`、原件、模型、运行日志与评测原始输出均被 `.gitignore` 排除。所有演示数据明确为合成，项目 1 未修改。
