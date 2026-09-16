// @vitest-environment jsdom
import {describe,it,expect,vi,beforeEach} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup,act} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from './App';
const mockApi=vi.fn();
vi.mock('./api',async()=>{const actual=await vi.importActual<any>('./api');return {...actual,api:(...a:unknown[])=>mockApi(...a)}});
beforeEach(()=>{cleanup();mockApi.mockReset();Element.prototype.scrollIntoView=vi.fn()});
describe('员工关键交互',()=>{
 it('未登录显示表单并呈现错误',async()=>{mockApi.mockRejectedValue(new Error('offline'));render(<App/>);await screen.findByText('登录工作台');fireEvent.change(screen.getByLabelText('密码'),{target:{value:'wrong'}});fireEvent.click(screen.getByText('进入工作台'));expect(await screen.findByRole('alert')).toBeInTheDocument()});
 it('员工不显示管理员入口',async()=>{mockApi.mockImplementation((p:string)=>Promise.resolve(p==='/me'?{id:'u1',display_name:'员工1',role:'employee',csrf:'x'}:[]));render(<App/>);await screen.findByText('知识查询');expect(screen.queryByText('数据管理')).not.toBeInTheDocument();expect(screen.getByLabelText('发送问题')).toBeDisabled()});
 it('过期会话隐藏旧消息并提示重开',async()=>{mockApi.mockImplementation((p:string)=>Promise.resolve(p==='/me'?{id:'u1',display_name:'员工1',role:'employee',csrf:'x'}:p==='/conversations'?[{id:'c1',created_at:1,expired:true}]:{expired:true,messages:[],notice:'知识或权限已更新，请开启新会话'}));render(<App/>);fireEvent.click(await screen.findByText('项目查询 1'));expect(await screen.findByRole('alert')).toHaveTextContent('请开启新会话');expect(screen.queryByText('机密正文')).not.toBeInTheDocument()});
});

it('注册入口提交普通账号并登录',async()=>{mockApi.mockImplementation((p:string)=>p==='/me'?Promise.reject(new Error('no')):Promise.resolve(p==='/auth/login'?{id:'u3',display_name:'new_user',role:'employee',csrf:'x'}:[]));render(<App/>);fireEvent.click(await screen.findByText('没有账号？注册'));fireEvent.change(screen.getByLabelText('账号'),{target:{value:'new_user'}});fireEvent.change(screen.getByLabelText('密码'),{target:{value:'123456'}});fireEvent.change(screen.getByLabelText('确认密码'),{target:{value:'123456'}});fireEvent.click(screen.getByText('注册并登录'));await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/auth/register','POST',{username:'new_user',password:'123456',display_name:'new_user'}));expect(await screen.findByText('知识查询')).toBeInTheDocument()});
it('修改密码提交旧密码和新密码',async()=>{mockApi.mockImplementation((p:string)=>Promise.resolve(p==='/me'?{id:'u1',display_name:'员工',role:'employee',csrf:'x'}:[]));render(<App/>);fireEvent.click(await screen.findByText('修改密码'));fireEvent.change(screen.getByLabelText('当前密码'),{target:{value:'123456'}});fireEvent.change(screen.getByLabelText('新密码'),{target:{value:'654321'}});fireEvent.change(screen.getByLabelText('确认新密码'),{target:{value:'654321'}});fireEvent.click(screen.getByText('保存并重新登录'));await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/auth/password','POST',{current_password:'123456',new_password:'654321'}));expect(await screen.findByText('登录工作台')).toBeInTheDocument()});
it('知识更新后的可读历史仍显示',async()=>{mockApi.mockImplementation((p:string)=>Promise.resolve(p==='/me'?{id:'u1',display_name:'员工',role:'employee',csrf:'x'}:p==='/conversations'?[{id:'c1',title:'验收讨论',created_at:1}]:{expired:false,knowledge_updated:true,messages:[{id:'r1',state:'completed',message:'验收条件',answer:{answer:'旧版验收说明',status:'answered',warnings:[],evidence:[]}}]}));render(<App/>);fireEvent.click(await screen.findByText('验收讨论'));expect(await screen.findByText('旧版验收说明')).toBeInTheDocument()});

it('切换会话后丢弃迟到的原文响应',async()=>{
 let resolve:any;
 const pending=new Promise(r=>{resolve=r});
 mockApi.mockImplementation((p:string)=>Promise.resolve(p==='/me'?{id:'u1',display_name:'员工',role:'employee',csrf:'x'}:p==='/conversations'?[{id:'c1',title:'对抗测试',created_at:1}]:p.includes('/chunks/')?pending:{expired:false,messages:[{id:'r1',message:'问题',state:'completed',answer:{status:'answered',answer:'有引用的回答',warnings:[],evidence:[{id:'E1',type:'document',title:'受控来源',document_id:'d1',version_id:'v1',chunk_id:'x'}]}}]}));
 render(<App/>);fireEvent.click(await screen.findByText('对抗测试'));fireEvent.click(await screen.findByText('E1 · 受控来源'));
 await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/documents/d1/versions/v1/chunks/x'));
 fireEvent.click(screen.getByText('开启新会话'));
 await act(async()=>{resolve({text:'迟到的机密原文',title:'受控来源'});await pending});
 expect(screen.queryByText('迟到的机密原文')).not.toBeInTheDocument();expect(screen.queryByText('来源证据')).not.toBeInTheDocument();
});
it('切换后迟到的提交不会重开旧会话监听',async()=>{
 let resolve:any;const pending=new Promise(r=>{resolve=r});
 mockApi.mockImplementation((p:string,m:string)=>p==='/me'?Promise.resolve({id:'u1',display_name:'员工',role:'employee',csrf:'x'}):p==='/conversations'?Promise.resolve(m==='POST'?{id:'old'}:[]):p.endsWith('/queries')?pending:Promise.resolve({messages:[]}));
 render(<App/>);await screen.findByText('知识查询');fireEvent.change(screen.getByLabelText('输入项目问题'),{target:{value:'测试'}});fireEvent.click(screen.getByLabelText('发送问题'));
 await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/conversations/old/queries','POST',expect.anything()));fireEvent.click(screen.getByText('开启新会话'));
 await act(async()=>{resolve({run_id:'oldrun'});await pending});
 expect(mockApi).not.toHaveBeenCalledWith('/conversations/old/messages');expect(screen.getByLabelText('发送问题')).not.toBeDisabled();
});
