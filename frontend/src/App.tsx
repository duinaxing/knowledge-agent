import {useEffect,useState,useRef, FormEvent} from 'react';
import {BookOpen, Plus, ArrowUp, MessageSquare, Database, LogOut, FileText, ChevronRight, ShieldCheck, Activity, X, RefreshCw} from 'lucide-react';
import {api,setCsrf,errorText,ApiError} from './api';
import Admin from './Admin';

type User={id:string;display_name:string;role:string;csrf:string;epoch?:number};
type Evidence={id:string;type:string;title?:string;text?:string;document_id?:string;version_id?:string;chunk_id?:string;[k:string]:any};
type Answer={status:string;kind?:string;answer:string;evidence:Evidence[];warnings:string[];clarification?:{project_id:string;code:string;name:string}[]};
type Run={id:string;state:string;message:string;answer?:Answer;error_code?:string};
const stamp=(n:number)=>new Date(n*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const stages:Record<string,string>={search_documents:'正在检索项目文档',get_project_status:'正在核对项目状态',get_project_owner:'正在查找项目负责人',get_document_excerpt:'正在核验原文证据'};

export default function App(){
 const[user,setUser]=useState<User|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const[view,setView]=useState('chat'),[convs,setConvs]=useState<any[]>([]),[conv,setConv]=useState('');
 const[runs,setRuns]=useState<Run[]>([]),[question,setQuestion]=useState(''),[busy,setBusy]=useState(false),[progress,setProgress]=useState('');
 const[evidence,setEvidence]=useState<Evidence|null>(null),[feedback,setFeedback]=useState(''),[feedbackType,setFeedbackType]=useState('incorrect'),[note,setNote]=useState('');
 const stream=useRef<EventSource|null>(null), requestId=useRef<string|null>(null), bottom=useRef<HTMLDivElement>(null);
 const[passwordOpen,setPasswordOpen]=useState(false),[hasMore,setHasMore]=useState(false);
 const selection=useRef(0);
 const knownEpoch=useRef<number|null>(null);
 async function loadConvs(){const ticket=selection.current;const rows=await api('/conversations');if(ticket===selection.current)setConvs(rows);}
 useEffect(()=>{api<User>('/me').then(u=>{setCsrf(u.csrf);setUser(u)}).catch(()=>{}).finally(()=>setLoading(false));return()=>stream.current?.close()},[]);
 useEffect(()=>{if(user)loadConvs().catch(e=>setError(errorText(e)))},[user]);
 useEffect(()=>{
  if(!user)return;
  let cancelled=false;
  const timer=setInterval(async()=>{try{const me=await api<User>('/me');if(cancelled)return;
   if(knownEpoch.current!==null&&knownEpoch.current!==me.epoch){selection.current++;stream.current?.close();setRuns([]);setEvidence(null);setBusy(false);setProgress('');setError('知识或权限已更新，请开启新会话。');await loadConvs()}
   knownEpoch.current=me.epoch??null;
  }catch(e){if(!cancelled&&e instanceof ApiError&&e.status===401){reset();setConvs([]);setUser(null);setCsrf('')}}},5000);
  return()=>{cancelled=true;clearInterval(timer)};
 },[user]);
 useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth'})},[runs,progress]);
 function reset(){selection.current++;setFeedback('');setNote('');setHasMore(false);stream.current?.close();setConv('');setRuns([]);setEvidence(null);setBusy(false);setProgress('');setError('');requestId.current=null;}
 async function loadMessages(id:string){
  const ticket=selection.current;const data=await api('/conversations/'+id+'/messages');if(ticket!==selection.current)return;setHasMore(!!data.has_more);
  if(data.expired){setRuns([]);setEvidence(null);setBusy(false);setError(data.notice);return;}
  setRuns(data.messages);if(data.knowledge_updated)setError('知识已更新，以下为历史回答；继续提问将重新检索当前资料。');const active=data.messages.find((r:Run)=>['queued','running'].includes(r.state));
  if(active)watch(active.id,id);
 }
 async function removeConversation(id:string){if(!window.confirm('确定删除这个会话及其聊天记录？'))return;try{await api('/conversations/'+id,'DELETE');if(id===conv)reset();await loadConvs()}catch(e){setError(errorText(e))}}
 async function choose(id:string){reset();setConv(id);setView('chat');try{await loadMessages(id)}catch(e){setError(errorText(e))}}
 function watch(runId:string,conversationId:string){
  stream.current?.close();setBusy(true);setProgress('正在准备查询');
  const ticket=selection.current;const s=new EventSource('/api/runs/'+runId+'/events');stream.current=s;
  s.addEventListener('stage_started',e=>{if(ticket!==selection.current)return;const data=JSON.parse((e as MessageEvent).data);setProgress(stages[data.stage]||'正在查询')});
  s.addEventListener('done',async()=>{s.close();if(ticket!==selection.current)return;setBusy(false);setProgress('');try{await loadMessages(conversationId)}catch(e){setError(errorText(e))}});
  s.addEventListener('error',e=>{if(ticket!==selection.current)return;if((e as MessageEvent).data){s.close();setBusy(false);setRuns([]);setEvidence(null);const d=JSON.parse((e as MessageEvent).data);setError(errorText(new ApiError(d.error_code,409)))}else{setProgress('连接中断，正在重新连接…')}});
 }
 async function send(text=question){
  if(!text.trim()||busy)return;const ticket=selection.current;setError('');setBusy(true);
  try{let id=conv;if(!id){id=(await api('/conversations','POST')).id;if(ticket!==selection.current)return;setConv(id);await loadConvs();if(ticket!==selection.current)return}
   const key=requestId.current||crypto.randomUUID();requestId.current=key;
   const result=await api('/conversations/'+id+'/queries','POST',{message:text,client_request_id:key});
   if(ticket!==selection.current)return;requestId.current=null;setQuestion('');await loadMessages(id);if(ticket!==selection.current)return;await loadConvs();if(ticket===selection.current)watch(result.run_id,id);
  }catch(e){if(ticket!==selection.current)return;setBusy(false);setError(errorText(e));if(e instanceof ApiError&&e.code==='CONTEXT_CHANGED'){setRuns([]);setEvidence(null)}}
 }
 async function openEvidence(e:Evidence){const ticket=selection.current;try{const result=e.type==='document'?{...e,...await api(`/documents/${e.document_id}/versions/${e.version_id}/chunks/${e.chunk_id}`)}:e;if(ticket===selection.current)setEvidence(result)}catch(err){if(ticket!==selection.current)return;setEvidence(null);setError(errorText(err))}}

 if(loading)return <div className="loading">正在连接知序…</div>;
 if(!user)return <Login onLogin={u=>{reset();setQuestion('');setCsrf(u.csrf);setView('chat');setConvs([]);knownEpoch.current=u.epoch??null;setUser(u)}}/>;
 return <div className="shell">
  <aside className="sidebar"><div className="brand"><span><BookOpen size={22}/></span><b>知序<small>PROJECT KNOWLEDGE</small></b></div>
   <button className="new-chat" onClick={()=>{reset();setView('chat')}}><Plus size={17}/>开启新会话</button>
   <nav><button className={view==='chat'?'active':''} onClick={()=>setView('chat')}><MessageSquare size={17}/>知识查询</button>
   {user.role==='admin'&&<><button className={view==='data'?'active':''} onClick={()=>setView('data')}><Database size={17}/>数据管理</button><button className={view==='feedback'?'active':''} onClick={()=>setView('feedback')}><Activity size={17}/>反馈处理</button></>}</nav>
   <div className="nav-label">最近会话</div><div className="conversations">{convs.map((c,i)=><button key={c.id} className={c.id===conv?'selected':''} onClick={()=>choose(c.id)}><MessageSquare size={14}/><span>{c.title||`项目查询 ${convs.length-i}`}<small>{stamp(c.created_at)}</small></span><span role="button" tabIndex={0} aria-label={'删除会话 '+(c.title||'项目查询')} onClick={e=>{e.stopPropagation();removeConversation(c.id)}} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();removeConversation(c.id)}}}><X size={14}/></span></button>)}{convs.length>=50&&<button onClick={async()=>{const ticket=selection.current;const more=await api('/conversations?offset='+convs.length);if(ticket===selection.current)setConvs([...convs,...more])}}>加载更多会话</button>}</div>
   <button className="text-button" onClick={()=>setPasswordOpen(true)}>修改密码</button><div className="profile"><div className="avatar">{user.display_name[0]}</div><span>{user.display_name}<small>{user.role==='admin'?'知识管理员':'员工工作台'}</small></span><button aria-label="退出登录" onClick={async()=>{try{await api('/auth/logout','POST');reset();setUser(null)}catch(e){setError(errorText(e))}}}><LogOut size={17}/></button></div>
  </aside>
  <main><header><div><span className="eyebrow">WORKSPACE / {view==='chat'?'ASK':'MANAGE'}</span><h2>{view==='chat'?'项目知识查询':view==='data'?'知识与项目管理':'回答反馈'}</h2></div><span className="badge"><span/>合成企业数据</span></header>
   {error&&<div role="alert" className="error">{error}<button onClick={()=>setError('')} aria-label="关闭提示"><X size={16}/></button></div>}
   {view!=='chat'&&user.role==='admin'?<Admin view={view} onError={setError}/>:<><div className="chat-scroll">
    {!runs.length&&<div className="welcome"><div className="welcome-icon"><BookOpen size={30}/></div><span className="eyebrow">YOUR TEAM'S KNOWLEDGE, CONNECTED</span><h1>让每一个答案，<br/>都有据可查。</h1><p>从项目现状到历史决策，连接你有权访问的资料。<br/>提出问题，查看答案，也看见答案的来处。</p>
     <div className="suggestions">{[['掌握项目进展','北辰项目现在谁负责，是什么状态？'],['查找验收依据','PRJ-001 的验收标准是什么？'],['回溯历史决策','北辰项目上次为什么调整需求？']].map(([title,q])=><button key={q} onClick={()=>{setQuestion(q);requestId.current=null}}><FileText size={18}/><b>{title}</b><span>{q}</span><ChevronRight size={16}/></button>)}</div>
    </div>}
    <div className="messages">{conv&&<button className="text-button" onClick={async()=>{const title=window.prompt('会话名称');if(title){try{await api('/conversations/'+conv,'PATCH',{title});await loadConvs()}catch(e){setError(errorText(e))}}}}>重命名会话</button>}{conv&&<button className="text-button danger" onClick={()=>removeConversation(conv)}>删除当前会话</button>}{hasMore&&<button className="choice" onClick={async()=>{const ticket=selection.current;try{const data=await api('/conversations/'+conv+'/messages?offset='+runs.length);if(ticket!==selection.current)return;setRuns([...data.messages,...runs]);setHasMore(data.has_more)}catch(e){setError(errorText(e))}}}>加载更早的消息</button>}{runs.map(r=><section className="exchange" key={r.id}><div className="user-message">{r.message}</div>{r.answer&&<div className="assistant-message"><div className="answer-head"><BookOpen size={17}/><b>知序</b><span>{r.answer.kind==='general'?'DeepSeek 通用回答':r.answer.kind==='application'?'使用说明':r.answer.status==='partial'?'部分结果':r.answer.status==='needs_clarification'?'需要澄清':'基于授权资料'}</span></div><div className="answer-text">{r.answer.answer}</div>
     {r.answer.clarification?.map(c=><button className="choice" key={c.project_id} onClick={()=>send(c.code+' 项目，请继续回答上一个问题：'+r.message)}>{c.code} · {c.name}<ChevronRight size={16}/></button>)}
     {!!r.answer.warnings.length&&<div className="warnings">{r.answer.warnings.join('；')}</div>}
     {!!r.answer.evidence.length&&<div className="sources"><span>参考来源</span>{r.answer.evidence.map(e=><button key={e.id} onClick={()=>openEvidence(e)}><FileText size={14}/>{e.id} · {e.title||e.code+' 项目记录'}</button>)}</div>}
     <button className="text-button" onClick={()=>{setFeedback(r.id);setNote('')}}>反馈问题</button></div>}
     {r.state==='failed'&&<div className="warnings">查询未完成：{r.error_code}。请重新提问。</div>}
     {r.state==='interrupted'&&<button className="choice" onClick={async()=>{try{await api(`/runs/${r.id}/resume`,'POST');watch(r.id,conv)}catch(e){setError(errorText(e))}}}><RefreshCw size={15}/>任务中断，尝试恢复</button>}</section>)}<div ref={bottom}/></div>
   </div><div className="composer-area">{progress&&<div className="progress"><span className="pulse"/>{progress}</div>}<form className="composer" onSubmit={e=>{e.preventDefault();send()}}><textarea aria-label="输入项目问题" placeholder="询问项目状态、负责人，或查找一份决策依据…" value={question} onChange={e=>{setQuestion(e.target.value);requestId.current=null}} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}}}/><div><span><ShieldCheck size={14}/>仅查询你有权访问的信息</span><button disabled={busy||!question.trim()} aria-label="发送问题"><ArrowUp size={20}/></button></div></form><p className="composer-note">答案基于已登记资料，请结合来源时间判断。Shift + Enter 换行。</p></div></>}
  </main>
  {evidence&&<aside className="evidence-panel"><div className="panel-head"><h3>来源证据</h3><button onClick={()=>setEvidence(null)} aria-label="关闭证据"><X size={19}/></button></div><span className="eyebrow">{evidence.id} / VERIFIED SOURCE</span><h2>{evidence.title||evidence.name}</h2>{evidence.type==='document'?<><p className="muted">版本 {evidence.version_label} · {evidence.locator?.page?'第 '+evidence.locator.page+' 页':'原文段落'}</p><pre>{evidence.text}</pre></>:<dl>{Object.entries(evidence).filter(([k])=>!['id','type','project_id'].includes(k)).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{typeof v==='object'?JSON.stringify(v):k.endsWith('_at')?stamp(v):String(v??'未登记')}</dd></div>)}</dl>}</aside>}
  {passwordOpen&&<PasswordDialog close={()=>setPasswordOpen(false)} done={()=>{setPasswordOpen(false);reset();setUser(null);setView('chat')}}/>}
  {feedback&&<div className="modal-backdrop"><form className="modal" onSubmit={async e=>{e.preventDefault();try{await api('/answers/'+feedback+'/feedback','POST',{type:feedbackType,note});setFeedback('')}catch(e){setError(errorText(e))}}}><div className="panel-head"><h3>反馈回答问题</h3><button type="button" onClick={()=>setFeedback('')}><X size={18}/></button></div><label>问题类型<select value={feedbackType} onChange={e=>setFeedbackType(e.target.value)}><option value="incorrect">事实不正确</option><option value="citation">引用不准确</option><option value="missing">信息缺失</option><option value="permission">权限问题</option><option value="other">其他</option></select></label><label>补充说明<textarea value={note} onChange={e=>setNote(e.target.value)} maxLength={1000}/></label><button className="primary">提交反馈</button></form></div>}
 </div>
}

function Login({onLogin}:{onLogin:(u:User)=>void}){
 const[username,setUsername]=useState('employee1'),[password,setPassword]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[register,setRegister]=useState(false),[confirm,setConfirm]=useState('');
 async function submit(e:FormEvent){e.preventDefault();setBusy(true);try{if(register){if(password!==confirm)throw new Error('两次密码不一致');await api('/auth/register','POST',{username,password,display_name:username})}onLogin(await api('/auth/login','POST',{username,password}))}catch(e){setError(errorText(e))}finally{setBusy(false)}}
 return <div className="login-page"><div className="login-story"><div className="brand"><span><BookOpen size={24}/></span><b>知序<small>PROJECT KNOWLEDGE</small></b></div><h1>团队的知识，<br/>清晰的答案。</h1><p>理解项目的现在，找到决策的过去。<br/>让信息可追溯，让协作有依据。</p><div className="login-foot"><ShieldCheck size={18}/>权限隔离 · 来源引用 · 历史版本</div></div><form className="login-form" onSubmit={submit}><span className="eyebrow">WELCOME TO YOUR WORKSPACE</span><h2>{register?'注册账号':'登录工作台'}</h2><p className="muted">此环境使用合成企业数据进行演示。</p><label>账号<input value={username} onChange={e=>setUsername(e.target.value)} autoComplete="username" pattern={register?"[a-z0-9_]{3,40}":undefined} title={register?"3–40 位小写字母、数字或下划线":undefined} required/></label><label>密码<input type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete={register?'new-password':'current-password'} minLength={register?6:1} required/></label>{register&&<label>确认密码<input type="password" value={confirm} onChange={e=>setConfirm(e.target.value)} minLength={6} required/></label>}{register&&password!==confirm&&<p className="muted">两次密码需一致</p>}{error&&<p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy||(register&&password!==confirm)}>{busy?'正在处理…':register?'注册并登录':'进入工作台'}</button><button type="button" className="text-button" onClick={()=>{setRegister(!register);setError('')}}>{register?'已有账号，去登录':'没有账号？注册'}</button><p className="muted small">演示账号：employee1–employee6 / admin<br/>密码由本地 SEED_PASSWORD 配置。</p></form></div>
}


function PasswordDialog({close,done}:{close:()=>void;done:()=>void}){
 const[error,setError]=useState(''),[busy,setBusy]=useState(false);
 return <div className="modal-backdrop"><form className="modal" onSubmit={async e=>{e.preventDefault();const data=new FormData(e.currentTarget);if(data.get('next')!==data.get('confirm')){setError('两次新密码不一致');return}setBusy(true);try{await api('/auth/password','POST',{current_password:data.get('current'),new_password:data.get('next')});done()}catch(e){setError(errorText(e))}finally{setBusy(false)}}}><h3>修改密码</h3><p>修改成功后，所有设备需重新登录，历史会话会保留。</p><label>当前密码<input name="current" type="password" autoComplete="current-password" required/></label><label>新密码<input name="next" type="password" autoComplete="new-password" minLength={6} maxLength={200} required/></label><label>确认新密码<input name="confirm" type="password" autoComplete="new-password" minLength={6} required/></label>{error&&<p role="alert">{error}</p>}<button className="primary" disabled={busy}>保存并重新登录</button><button type="button" onClick={close}>取消</button></form></div>
}
