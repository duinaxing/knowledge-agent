let csrf = '';
export function setCsrf(value:string){csrf=value;}
export class ApiError extends Error {constructor(public code:string, public status:number){super(code)}}
export const notices:Record<string,string> = {
 REQUEST_TOO_LARGE:'请求内容过大，请缩小文件或输入内容。', REQUEST_TIMEOUT:'上传或请求超时，请重试。',
 EVIDENCE_REQUIRED:'这个问题涉及项目或文档，需要获取证据后才能回答，请重新提问。',
 GROUP_NAME_EXISTS:'用户组名称已存在，请换一个名称。',
 PROJECT_CODE_EXISTS:'项目编号已被现有项目使用，请更换编号。已删除项目的编号可以重新使用。',
 USERNAME_TAKEN:'用户名已存在，请换一个。', INVALID_ARGUMENT:'请检查输入；用户名为 3–40 位小写字母、数字或下划线，密码至少 6 位。',
 CONTEXT_CHANGED:'知识或权限已更新，请开启新会话。', NOT_FOUND_OR_FORBIDDEN:'资源不存在或你无权访问。',
 INVALID_CREDENTIALS:'账号或密码不正确。', CSRF_REJECTED:'登录状态已变化，请刷新后重试。',
 CONVERSATION_BUSY:'当前会话仍有查询进行中。', MODEL_NOT_CONFIGURED:'生成模型尚未配置。',
 EMBEDDING_NOT_CONFIGURED:'文档向量模型尚未配置，请联系管理员。', DEADLINE_EXCEEDED:'查询超时，请重新提问。',
 REVISION_CONFLICT:'资料已被其他操作更新，请刷新后重试。', CONFLICT:'数据冲突，请刷新后重试。',
 UNSUPPORTED_FORMAT:'仅支持 Markdown、TXT 和文本型 PDF。', RATE_LIMITED:'尝试次数过多，请稍后再试。'
};
export function errorText(e:unknown){return e instanceof ApiError ? notices[e.code] || `操作失败：${e.code}` : '连接失败，请检查服务是否启动。'}
export async function api<T=any>(path:string, method='GET', body?:unknown):Promise<T>{
 const form=body instanceof FormData;
 const res=await fetch('/api'+path,{method,credentials:'same-origin',headers:{...(form?{}:{'Content-Type':'application/json'}),...(method==='GET'?{}:{'X-CSRF-Token':csrf})},body:body===undefined?undefined:form?body:JSON.stringify(body)});
 const data=await res.json(); if(!res.ok)throw new ApiError(data.error_code || 'INVALID_ARGUMENT',res.status);return data;
}
