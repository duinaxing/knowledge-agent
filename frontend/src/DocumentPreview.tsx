import {useEffect,useRef,useState} from 'react';
import {X} from 'lucide-react';
import {api,errorText} from './api';

type Version={id:string;version_label:string;processing:string;publication:string};
type Chunk={id:string;ordinal:number;text:string;locator?:{page?:number;section?:string}};
export default function DocumentPreview({document,close}:{document:{id:string;title:string;versions:Version[]};close:()=>void}){
 const[version,setVersion]=useState(document.versions[0]?.id||''),[chunks,setChunks]=useState<Chunk[]>([]);
 const[busy,setBusy]=useState(false),[error,setError]=useState(''),[more,setMore]=useState(false);
 const[generation,setGeneration]=useState(0);const request=useRef(0);
 const selected=document.versions.find(v=>v.id===version);
 async function load(offset:number,ticket:number){
  setBusy(true);setError('');
  try{const data=await api(`/admin/documents/${document.id}/versions/${version}/content?offset=${offset}`);
   if(ticket!==request.current)return;
   setChunks(old=>offset?[...old,...data.chunks]:data.chunks);setMore(data.has_more);
  }catch(e){if(ticket===request.current)setError(errorText(e))}
  finally{if(ticket===request.current)setBusy(false)}
 }
 useEffect(()=>{const ticket=++request.current;setChunks([]);setMore(false);if(version)load(0,ticket);return()=>{request.current++}},[version,generation]);
 return <div className="modal-backdrop"><section className="modal document-preview" role="dialog" aria-modal="true" aria-label="文档内容">
  <div className="panel-head"><h3>{document.title}</h3><button onClick={close} aria-label="关闭文档"><X size={18}/></button></div>
  <label>查看版本<select value={version} onChange={e=>setVersion(e.target.value)}>{document.versions.map(v=><option key={v.id} value={v.id}>{v.version_label} · {v.publication==='published'?'已发布':'草稿'}</option>)}</select></label>
  <p className="muted">以下为用于知识检索的完整分段正文，保留原文文本；相邻段落可能有重叠。PDF 展示解析后的文字。</p>
  {error&&<p role="alert" className="error">{error}</p>}
  {!busy&&!error&&!chunks.length&&<p className="warnings">{selected?.processing==='failed'?'文档处理失败，尚无可预览正文，请关闭后重试处理。':selected?.processing==='ready'?'该版本没有可显示的正文。':'正文尚未处理完成，请稍后刷新。'}</p>}
  {chunks.map(c=><article className="preview-chunk" key={c.id}><small>片段 {c.ordinal+1}{c.locator?.page?` · 第 ${c.locator.page} 页`:''}{c.locator?.section?` · ${c.locator.section}`:''}</small><pre>{c.text}</pre></article>)}
  {busy&&<p role="status">正在加载正文…</p>}
  {more&&<button disabled={busy} onClick={()=>load(chunks.length,request.current)}>加载后续正文</button>}
  <button className="text-button" disabled={busy} onClick={()=>setGeneration(g=>g+1)}>刷新内容</button>
 </section></div>
}
