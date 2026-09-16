"""Preserve history while applying current authorization to stored source snapshots."""
from .security import can_read
from .db import Document, Project, Version


def readable_answer(db, who, answer, knowledge_changed=False):
    if not answer:
        return answer
    if knowledge_changed and answer.get('kind')=='general' and 'access_scope' not in answer:
        return {'status':'no_answer','answer':'此旧版回答缺少上下文权限记录，知识更新后已隐藏。',
                'facts':[],'evidence':[],'warnings':[],'clarification':None}
    scopes=list(answer.get('access_scope',[]))
    scopes += [{'kind':'project','id':c.get('project_id')} for c in answer.get('clarification') or []]
    for scope in scopes:
        kind=scope.get('kind')
        if kind not in ('project','document') or not can_read(db,who,kind,db.get(Project if kind=='project' else Document,scope.get('id'))):
            return {'status':'no_answer','answer':'此历史回答的上下文包含当前不可访问的资料，内容已隐藏。',
                    'facts':[],'evidence':[],'warnings':['来源访问权限已变化。'],'clarification':None}
    for e in answer.get('evidence', []):
        if e.get('type')=='document':
            doc=db.get(Document,e.get('document_id'))
            version=db.get(Version,e.get('version_id'))
            allowed=can_read(db,who,'document',doc) and version and version.document_id==doc.id and version.publication=='published'
        else:
            allowed=can_read(db,who,'project',db.get(Project,e.get('project_id')))
        if not allowed:
            return {'status':'no_answer','answer':'此回答涉及已删除或当前无权访问的资料，内容已隐藏。',
                    'facts':[],'evidence':[],'warnings':['历史记录已保留，来源访问权限已变化。'],'clarification':None}
    return answer
