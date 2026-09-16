# 真实模型评测记录

2026-09-12；数据库 postgresql；生成 deepseek-flash；Embedding BAAI/bge-small-zh-v1.5。
保留集 40 题 × 3 次 × 4 基线，共 480 条；并发 5。合成资料与参考标注由开发者编写，尚未独立人工复核。

| 基线 | answered / partial / no_answer / retrieval_only / failed | 完成请求 P50 / P95 秒 |
| --- | --- | --- |
| B0 | 0 / 0 / 0 / 120 / 0 | 0.11 / 0.14 |
| B1 | 77 / 25 / 18 / 0 / 0 | 1.48 / 2.20 |
| B2 | 102 / 1 / 17 / 0 / 0 | 1.64 / 2.41 |
| B3 | 104 / 4 / 12 / 0 / 0 | 5.21 / 7.44 |

B0 为关键词检索，B1 为固定文档 RAG，B2 为固定跨源流程，B3 为动态 Agent。answered 等状态由应用输出，不能当作人工判定的任务正确率。

任务成功率、引用支持率、事实覆盖率保持未评定；不能仅凭状态分布断言动态 Agent 优于固定流程。当前证据最多六段，报告中的检索 recall@5 只用于检查文档召回，不能替代答案评审。

## 待人工复核的样例

- B1 / Q05-12 / 第 1 次：云桥现在负责人和只读决策依据？ → partial。请核对证据是否充分、是否正确表达缺失信息。
- B1 / Q05-13 / 第 1 次：云桥当前计划日期与启动讨论一致吗？ → partial。请核对证据是否充分、是否正确表达缺失信息。
- B2 / Q06-15 / 第 1 次：青禾每批具体接入几家供应商？ → partial。请核对证据是否充分、是否正确表达缺失信息。
- B3 / Q06-15 / 第 1 次：青禾每批具体接入几家供应商？ → partial。请核对证据是否充分、是否正确表达缺失信息。
- B3 / Q06-15 / 第 2 次：青禾每批具体接入几家供应商？ → partial。请核对证据是否充分、是否正确表达缺失信息。

## 复现与审阅

`python evaluation/run.py --split heldout --trials 3 --concurrency 5 --baselines B0,B1,B2,B3 --output runtime/evaluation/heldout-postgres.jsonl`

`python evaluation/report.py runtime/evaluation/heldout-postgres.jsonl`

原始结果 SHA-256：`2bdd7f4491d5ae6c58e55c1171c23ca37f02854a2975e17f96597e9f4008415a`。

原始结果与 `*.review.csv` 在 runtime/evaluation；包含每次答案、证据和实际返回的模型用量。审阅表留空的人工字段必须由独立审阅者填写，先核对参考答案，再逐事实检查引用。没有服务商 token 用量的本地 Embedding 记录保持 null；本报告未估算金额。

局限：同一电脑同时运行数据库、CPU Embedding 与 Web 服务，未做独占负载隔离；这是一次合成保留集观测，尚非企业使用效果或完整产品验收结论。
