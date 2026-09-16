// @vitest-environment jsdom
import {it,expect,vi,beforeEach} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import Admin from './Admin';
const mockApi=vi.fn();
vi.mock('./api',()=>({api:(...args:unknown[])=>mockApi(...args),errorText:()=> '操作失败'}));
beforeEach(()=>{cleanup();mockApi.mockReset();mockApi.mockImplementation((path:string)=>Promise.resolve(path==='/admin/groups'?[{id:'g1',name:'测试组',user_ids:[]}]:path==='/admin/metrics'?{runs:{},jobs:{}}:[]))});
it('增加用户组并提交名称',async()=>{
 render(<Admin view="data" onError={()=>{}}/>);fireEvent.click(screen.getByRole('button',{name:'用户组'}));
 fireEvent.click(screen.getByText('增加用户组'));fireEvent.change(screen.getByLabelText('用户组名称'),{target:{value:' 新团队 '}});
 fireEvent.click(screen.getByText('保存用户组'));
 await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/admin/groups','POST',{name:'新团队'}));
});
it('删除用户组需要确认，取消不提交',async()=>{
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 render(<Admin view="data" onError={()=>{}}/>);fireEvent.click(screen.getByRole('button',{name:'用户组'}));
 fireEvent.click(await screen.findByText('删除用户组'));expect(mockApi).not.toHaveBeenCalledWith('/admin/groups/g1','DELETE');
 confirm.mockReturnValue(true);fireEvent.click(screen.getByText('删除用户组'));
 await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/admin/groups/g1','DELETE'));confirm.mockRestore();
});

it('选择多份文档，取消确认不删除，确认后批量提交',async()=>{
 mockApi.mockImplementation((path:string)=>Promise.resolve(path==='/admin/documents'?[{id:'d1',title:'规范',versions:[]},{id:'d2',title:'计划',versions:[]},{id:'d3',title:'已删',deleted_at:1,versions:[]}]:path==='/admin/metrics'?{runs:{}}:[]));
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 render(<Admin view="data" onError={()=>{}}/>);fireEvent.click(screen.getByText('文档库'));
 await screen.findByText('规范');fireEvent.click(screen.getByText('选择文档'));
 expect(screen.queryByLabelText('选择文档：已删')).not.toBeInTheDocument();
 expect(screen.queryByText('已删')).not.toBeInTheDocument();
 fireEvent.click(screen.getByLabelText('全选已加载文档'));fireEvent.click(screen.getByText('删除所选文档'));
 expect(mockApi).not.toHaveBeenCalledWith('/admin/documents/bulk-delete','POST',expect.anything());
 confirm.mockReturnValue(true);fireEvent.click(screen.getByText('删除所选文档'));
 await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/admin/documents/bulk-delete','POST',{document_ids:['d1','d2']}));confirm.mockRestore();
});

it('按用户名搜索员工并添加、移出组',async()=>{
 mockApi.mockImplementation((path:string)=>Promise.resolve(path==='/admin/groups'?[{id:'g1',name:'测试组',user_ids:['u1']}]:path==='/admin/users'?[{id:'u1',username:'alice',display_name:'员工甲',role:'employee'},{id:'u2',username:'bob',display_name:'员工乙',role:'employee'}]:path==='/admin/metrics'?{runs:{}}:[]));
 render(<Admin view="data" onError={()=>{}}/>);fireEvent.click(screen.getByRole('button',{name:'用户组'}));fireEvent.click(await screen.findByText('管理员工'));
 fireEvent.change(screen.getByLabelText('搜索员工'),{target:{value:'bob'}});
 expect(screen.queryByRole('button',{name:'移除员工：alice'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'添加员工：bob'}));
 await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/admin/groups/g1/members/u2','PUT'));
 fireEvent.click(await screen.findByRole('button',{name:'移除员工：bob'}));
 await waitFor(()=>expect(mockApi).toHaveBeenCalledWith('/admin/groups/g1/members/u2','DELETE'));
});
