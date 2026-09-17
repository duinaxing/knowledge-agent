# Knowledge Agent · 知序

面向企业内部资料的知识问答工作台。管理员在浏览器中维护项目、文档和用户组；员工使用独立会话提问，结合 DeepSeek 与授权知识库获得带来源片段的回答，也可以直接进行通用问答。

技术栈：**React + TypeScript · FastAPI · LangGraph · PostgreSQL / pgvector · 本地中文 Embedding**。范围依据 [产品技术文档](PRODUCT_TECH_SPEC.md)。

[功能](#核心功能) · [启动](#快速开始) · [验证结果](#当前验证状态) · [质量与压测](#质量容量与恢复验证) · [文档](#文档)

## 核心功能

| 模块 | 能力 |
|---|---|
| 知识查询 | 授权文档 RAG、引用片段与版本展示、项目状态和负责人查询；通用问题交给 DeepSeek |
| 会话管理 | 每个用户独立的新建、重命名、删除、分页历史；重新登录后继续查看 |
| 项目记录 | 新增、编辑、删除项目，维护项目简要情况、人员角色、交付日期和阻塞事项 |
| 文档库 | 上传、内容预览、版本发布、访问授权、单份或批量删除 |
| 用户组与处理任务 | 增删用户组、维护员工归属；查看文档解析和索引任务状态 |
| 反馈处理 | 记录有问题的回答，供管理员查看相关回答并处理反馈 |
| 账号 | 注册、登录、注销、修改密码；数据管理仅向管理员开放 |

## 当前验证状态

以下为 **2026-09-17 本机测试**，详细口径见 [验收汇总](docs/VALIDATION_STATUS_20260917.md)。

| 验证项 | 结果与边界 |
|---|---|
| 后端自动回归 | 153 项通过，使用隔离 PostgreSQL 测试库 |
| 质量开发集 | 50 例 / 55 次问答完成；模拟规划下目标证据 Recall@6 为 92.86%，不代表真实语义正确率 |
| 100 人持续混合负载 | 模拟模型，30 分钟 1739/1739 有效回答，端到端 P95 3.016 秒 |
| 100 人集中提问 | 三轮均 100/100 在 60 秒内回答；提交 P95 1.59–1.67 秒，**尚未达到 1 秒目标** |
| 备份恢复 | 数据库、原件、权限与历史验证通过，测试规模恢复耗时 17.406 秒 |
| 待完成 | 本批次真实 DeepSeek 质量与压测、独立人工语义审阅 |

客户端和服务端共用一台电脑，以上不能作为生产容量承诺。已有历史真实模型实验与本批次隔离验证分别记录，项目尚未全面验收。

## 账号、知识库与历史会话

- 管理员：`admin` / `123456`。只有管理员可打开数据管理，上传、发布和授权知识文档。
- 普通用户可在登录页注册（用户名 3–40 位小写字母、数字、下划线，密码至少 6 位），注册后只能问答；可读组织公开知识或管理员授予的资料。
- 侧栏可新建、选择和分页浏览会话；聊天区可重命名、加载更早消息。注销、重登和改密不会清除历史。
- 点击“修改密码”，提供旧密码与新密码，成功后所有设备需重新登录。
- 知识更新后的历史仍保留；被删除或撤权的来源所涉及的历史回答会隐藏。新提问使用当前资料。
- [七项功能核对与性能验收](docs/FEATURE_ACCEPTANCE.md) 包含测试方法、实测结果及部署边界。

## 模型分工

- **生成回答：DeepSeek**，通过兼容接口调用；仓库配置示例使用 `deepseek-flash`，请按实际可用模型设置 `MODEL_NAME`。
- **语义检索：本地 BAAI/bge-small-zh-v1.5**，CPU 执行，512 维；不需要另外购买 Embedding API。也支持替换为兼容 `/embeddings` 的 API。
- BM25 和向量检索都先按用户权限和版本过滤，再用 RRF 融合。可选远程 reranker 默认关闭。
- `MODEL_MODE=test` 的哈希向量只服务确定性测试，绝不能用于语义评测或展示真实效果。

## 质量、容量与恢复验证

[验证指南](docs/VALIDATION_GUIDE.md) 包含 150 例冻结题库、四组槽位 / 快路径对照、复杂问法、故障注入与一致备份恢复。测试使用独立数据库和目录，不修改业务 `.env`。

先安装验证依赖：`python -m pip install -e './backend[test,local-embedding,loadtest]'`。以下 PowerShell 脚本沿用本机 Python 路径，新环境先调整脚本中的路径。

```powershell
./run-quality.ps1 -Split dev
./run-loadtest.ps1 -Profile smoke -Workers 8
./run-validation.ps1                  # 四组对照 + 完整模拟负载 + 复杂问法
./run-recovery.ps1                    # 停止源测试实例后执行；目标已存在则拒绝覆盖
```

默认生成模型为模拟模式；真实本地 Embedding 仍参与检索。显式 `-Real` 才会发起真实生成模型调用：质量预算 300 问答 / 1500 模型请求，真实压测 200 / 1000，两份预算分开，重试计数、重启不清零。同一批次保持相同 `-Experiment`，不能换编号绕过上限。

结果保存在 `runtime/quality/`、`runtime/loadtest/` 和 `runtime/recovery/`。验收报告：[质量](docs/VALIDATION_QUALITY_20260917.md) · [容量](docs/VALIDATION_CAPACITY_20260917.md) · [恢复](docs/VALIDATION_RECOVERY_20260917.md)。

## 快速开始

需要 Python 3.11+、Node.js / npm，以及 PostgreSQL + pgvector。下面的命令均从本仓库根目录执行。

```powershell
git clone https://github.com/duinaxing/knowledge-agent.git
cd knowledge-agent
```

### 已初始化的 Windows 环境

**一键启动：双击 `start.cmd`**。自动启动数据库、Embedding、API、后台任务和前端，检查就绪后打开浏览器。重复启动会复用服务；启动失败会保留错误提示，日志在 `runtime/`。不会重置用户、文档或会话，也不会重新导入演示数据。

命令行也可运行 `./start.ps1`；`./start.ps1 -Restart` 重启应用服务，`./start.ps1 -NoBrowser` 仅启动、不打开浏览器。本入口适用于已按下文安装和初始化的当前电脑。

也可以分步启动数据库和应用：

```powershell
./start-postgres.ps1
./start-local.ps1
```

访问 [本地工作台](http://127.0.0.1:5173)。合成演示账号为 `admin` 和 `employee1` 至 `employee6`，默认初始化密码 `123456`；实际密码以初始化时的 `SEED_PASSWORD` 或之后修改的密码为准。修改配置不会重置已有账号密码。

### 首次安装

先完成下节的依赖安装、`.env` 配置和模型下载（步骤 1–3），然后安装本地 PostgreSQL 运行时并初始化：

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

1. 激活 Python 3.11+ 虚拟环境（本机使用 `conda activate hello_agents_py311`），在仓库根目录执行：

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

本仓库的 [GitHub Actions](.github/workflows/ci.yml) 配置了工程测试和 PostgreSQL smoke。本地通过记录不代表本次 GitHub Actions 已通过，远程结果以 Actions 页面为准。

## 文档

- [2026-09-17 验证交付状态](docs/VALIDATION_STATUS_20260917.md)：质量、容量、恢复三份报告及尚未通过项。

- [2026-09-14 对抗性审查及修复报告](docs/ADVERSARIAL_REVIEW_2026-09-14.md)

- [架构与实现边界](docs/ARCHITECTURE.md)
- [验收记录与已知限制](docs/ACCEPTANCE.md)
- [480 次真实模型评测报告](docs/EVALUATION.md)
- [3 分钟演示脚本](docs/DEMO.md)
- [依赖许可证](docs/LICENSES.md)
- [OpenAPI](docs/openapi.json)，运行时也可访问 `/docs`。

`.env`、原件、模型、运行日志与评测原始输出均被 `.gitignore` 排除。演示数据均为合成数据；本地默认凭据与启动脚本不适合直接用于公开部署。
