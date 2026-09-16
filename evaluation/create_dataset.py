"""Synthetic evaluation draft. Labels require independent human review before acceptance."""
import json
from pathlib import Path

templates = [
 ('owner','{p} 的负责人是谁？','负责人{i}',[]),
 ('owner','{p} 的测试人员是谁？','测试员{i}',[]),
 ('status','{p} 当前处于哪个阶段？','testing|development',[]),
 ('status','{p} 登记的健康状态是什么？','delayed|on_track',[]),
 ('status','{p} 的计划日期是什么？','2026-09-27|2026-09-20',[]),
 ('status','{p} 的基线日期是多少？','2026-09-20',[]),
 ('document','{p} 验收时 P1 缺陷要求是什么？','P1 缺陷为零',[2]),
 ('document','{p} 核心用例通过率要达到多少？','{rate}%',[2]),
 ('document','{p} 验收需要谁签字？','交付负责人',[2]),
 ('history','{p} 历史启动会议登记了什么阶段？','规划阶段',[3]),
 ('history','{p} 启动会议的计划日期是什么？','2026-09-20',[3]),
 ('history','{p} 为什么将批量导入放入下一期？','先验证查询链路可用性',[4]),
 ('history','{p} 的需求变更优先做什么？','核心查询功能',[4]),
 ('cross','{p} 当前负责人及验收要求是什么？','负责人{i};P1 缺陷为零',[2]),
 ('cross','{p} 当前状态和启动会议阶段有什么不同？','当前与历史分开说明',[3]),
 ('cross','{p} 当前负责人、状态及需求调整依据是什么？','负责人{i};核心查询功能',[4]),
 ('no_answer','{p} 实际延期的根因是什么？','资料未记录，不推断',[7]),
 ('no_answer','{p} 的年度预算是多少钱？','没有预算证据',[]),
 ('no_answer','{p} 去年盈利了多少？','没有盈利证据',[]),
 ('conflict','{p} 启动纪要写规划，现在是不是仍在规划？','历史纪要不覆盖当前登记状态',[3]),
]
heldout = {
5: [
 ('document','客户资料能通过云桥接口修改吗？','只读，不提供修改',[1,4]),
 ('document','云桥查询延迟验收上限是多少？','P95 800毫秒',[2]),
 ('document','云桥结果允许哪些字段？','customer_id、display_name、region',[2]),
 ('document','云桥数据更新需要多快可见？','十五分钟',[2]),
 ('document','云桥交付包缺一份回滚步骤可以交付吗？','不可以；三项材料',[6]),
 ('document','云桥灰度流量和观察时长？','10%；三十分钟',[9]),
 ('document','云桥错误率到什么程度要回滚？','超过1%',[9]),
 ('document','云桥空值和权限字段要测什么？','空值过滤、无权限字段隐藏',[5]),
 ('history','云桥为何取消写入能力？','写权限评审未完成',[4]),
 ('history','云桥初次讨论的交付日？','2026-09-30',[3]),
 ('history','云桥起初包含写接口吗？','包含',[3]),
 ('cross','云桥现在负责人和只读决策依据？','负责人5；写权限评审未完成',[4]),
 ('cross','云桥当前计划日期与启动讨论一致吗？','2026-09-20与2026-09-30',[3]),
 ('cross','云桥谁测试，需要保留什么查询标识？','测试员5；追踪编号',[5]),
 ('no_answer','云桥实际客户数量是多少？','没有数量证据',[7]),
 ('no_answer','云桥贡献了多少收入？','未记录收入',[7]),
 ('no_answer','谁导致云桥实际延期？','没有实际根因和责任证据',[7]),
 ('conflict','启动纪要说有写入，云桥现在还能写吗？','历史范围已调整为只读',[3,4]),
 ('owner','云桥交付范围当前由谁负责？','负责人5',[]),
 ('status','云桥登记的进度阶段是什么？','development',[]),
],
6: [
 ('document','青禾会实时同步供应商数据吗？','每晚批次',[1]),
 ('document','青禾验收抽样数量与金额一致率？','200条；100%',[2]),
 ('document','青禾导出能含银行账号吗？','禁止',[2]),
 ('document','青禾导出必须带哪两个元数据？','批次号、生成时间',[2]),
 ('document','青禾怎样判定重复记录？','supplier_id与batch_id联合',[5]),
 ('document','青禾本批不完整时用户看到什么？','上一批数据；本批失败',[9]),
 ('document','青禾交付要带哪些材料？','字段映射表、批次校验报告、操作手册',[6]),
 ('document','青禾核对结果由谁在哪份文件确认？','业务代表；批次校验报告',[6]),
 ('history','青禾为何从实时改为批处理？','上游夜间完整导出',[4]),
 ('history','青禾自动通知安排在哪一期？','下一阶段',[4]),
 ('history','青禾启动时如何讨论供应商接入批数？','两批；数量未确定',[3]),
 ('cross','青禾目前谁负责，实时承诺为何调整？','负责人6；夜间完整导出',[4]),
 ('cross','青禾当前测试人员及抽样要求？','测试员6；200条；100%',[2]),
 ('cross','青禾当前状态和上游风险分别是什么？','on_track；导出时间不固定',[7]),
 ('no_answer','青禾每批具体接入几家供应商？','未确定',[3]),
 ('no_answer','青禾实际成本和利润各多少？','未记录',[7]),
 ('no_answer','青禾停滞应该追责谁？','实际责任人未记录',[7]),
 ('conflict','青禾启动说实时，现在仍是实时吗？','已改每晚批次',[3,4]),
 ('owner','青禾当前项目负责人姓名？','负责人6',[]),
 ('status','青禾现在处在什么项目阶段？','development',[]),
]}
rows=[]
for i in range(1,7):
    for j,(category,question,expected,doc_numbers) in enumerate(templates if i<=4 else heldout[i],1):
        rows.append({'id':f'Q{i:02}-{j:02}','split':'dev' if i<=4 else 'heldout',
            'topic_group':f'p{i}','user_id':f'u{((i-1)%3)*2+1}', 'project_id':f'p{i}',
            'category':category, 'question':question.format(p=f'PRJ-{i:03}'),
            'expected':expected.format(i=i,rate=90+i), 'relevant_document_ids':[f'd{i:02}{n:02}' for n in doc_numbers],
            'reference_review':'pending_human_review'})
target=Path(__file__).with_name('questions.jsonl')
target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
print({'questions':len(rows),'dev':80,'heldout':40,'human_reviewed':False})
