// @vitest-environment jsdom
import {it,expect,vi,beforeEach} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import DocumentPreview from './DocumentPreview';
const mockApi=vi.fn();
vi.mock('./api',()=>({api:(...a:unknown[])=>mockApi(...a),errorText:()=> '加载失败'}));
beforeEach(()=>{cleanup();mockApi.mockReset()});
const document={id:'d1',title:'验收规范',versions:[{id:'v1',version_label:'v1',processing:'ready',publication:'published'},{id:'v2',version_label:'v2',processing:'ready',publication:'draft'}]};
it('查看版本正文并加载后续片段',async()=>{
 mockApi.mockImplementation((p:string)=>Promise.resolve({chunks:[{id:p,ordinal:0,text:p.includes('offset=1')?'后续正文':p.includes('v2')?'新版正文':'原文内容',locator:{page:1}}],has_more:!p.includes('offset=1')}));
 render(<DocumentPreview document={document} close={()=>{}}/>);
 expect(await screen.findByText('原文内容')).toBeInTheDocument();
 fireEvent.click(screen.getByText('加载后续正文'));expect(await screen.findByText('后续正文')).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('查看版本'),{target:{value:'v2'}});
 expect(await screen.findByText('新版正文')).toBeInTheDocument();expect(screen.queryByText('原文内容')).not.toBeInTheDocument();
});
it('错误可见且可重试',async()=>{
 mockApi.mockRejectedValueOnce(new Error('fail')).mockResolvedValue({chunks:[],has_more:false});
 render(<DocumentPreview document={document} close={()=>{}}/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('加载失败');
 fireEvent.click(screen.getByText('刷新内容'));await waitFor(()=>expect(screen.queryByRole('alert')).not.toBeInTheDocument());
 expect(await screen.findByText('该版本没有可显示的正文。')).toBeInTheDocument();
});
