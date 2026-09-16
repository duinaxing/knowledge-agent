# 依赖来源与许可证说明

项目 2 独立编写，未拷贝项目 1 实现代码。选型依据原提案，第三方包通过正常依赖引用。

| 依赖 | 上游许可证 |
| --- | --- |
| FastAPI / SQLAlchemy / Alembic | MIT |
| LangGraph / langgraph-checkpoint-postgres | MIT |
| pgvector / pgvector-python | PostgreSQL License / MIT |
| psycopg | LGPL-3.0（发行时需保留相应说明） |
| pypdf | BSD-3-Clause |
| jieba | MIT |
| rank-bm25 | Apache-2.0 |
| React / Vite / TypeScript | MIT / MIT / Apache-2.0 |
| lucide-react | ISC |
| sentence-transformers / Transformers | Apache-2.0 |
| BAAI/bge-small-zh-v1.5 权重 | MIT（见模型卡） |

这是主要依赖摘要，分发前还应按锁文件复核全部传递依赖、容器基础镜像和许可证文本。DeepSeek 是远程服务，其使用受供应商服务条款约束，不包含模型权重再分发授权。项目未擅自为用户代码指定开源许可证。
