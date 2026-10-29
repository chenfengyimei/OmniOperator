"""真实 Chromium 的延迟响应、目标归属和小屏交互回归；不调用真实模型/设备。"""
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright
from test_web import _client


@pytest.fixture
def ui(tmp_path):
    client,runtime=_client(tmp_path)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        page=browser.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(r):
            url=urlsplit(r.request.url)
            response=client.request(r.request.method,url.path+('?' + url.query if url.query else ''),
                                    content=r.request.post_data,headers={'Content-Type':'application/json'})
            r.fulfill(status=response.status_code,body=response.content,headers={'Content-Type':response.headers.get('content-type','')})
        page.route('**/*',route)
        try:
            yield page,client,runtime,errors
        finally:
            page.close();browser.close()
            assert runtime.close()['complete'];client.close()


def delay_script(match):
    return '''window.__pending=[];window.__calls=[];
      const originalFetch=window.fetch;
      window.fetch=(url,options={})=>{
        window.__calls.push({url:String(url),body:options.body});
        if (MATCH) return new Promise((resolve,reject)=>{
          const row={url:String(url),resolve:data=>resolve(new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}}))};
          window.__pending.push(row);
          options.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
        });
        return originalFetch(url,options);
      };'''.replace('MATCH',match)


def test_late_job_output_cannot_overwrite_new_selection(ui):
    page,client,runtime,errors=ui
    host=runtime.command_jobs;rows=[]
    from omni.tools.shell.job_logs import CommandLog
    for text in ('任务 A','任务 B'):
        row=host.store.create(session_id=1,run_id='fixture',owner='fixture',owner_identity='',command=text,shell='powershell',cwd='.',timeout_s=30)
        host.store.update(row['job_id'],'fixture',status='succeeded')
        CommandLog(host.log_dir,row['job_id'],host.config.log_bytes).append('stdout',text+' 的日志')
        rows.append(row)
    page.add_init_script(delay_script(f"String(url).startsWith('/api/command-jobs/{rows[0]['job_id']}?')"))
    page.goto('http://127.0.0.1/jobs/')
    page.locator('#jobs button').filter(has_text='任务 A').click()
    page.wait_for_function('window.__pending.length===1')
    page.locator('#jobs button').filter(has_text='任务 B').click()
    page.evaluate('(data)=>window.__pending[0].resolve(data)',{'job':{**rows[0],'status':'succeeded'},'records':[{'seq':20,'stream':'stdout','text':'旧任务 A 的迟到日志'}],'next_after':20,'gap':False})
    page.locator('#output').get_by_text('任务 B 的日志',exact=False).wait_for()
    assert '任务 A' not in page.locator('#output').inner_text()
    assert '任务 B' in page.locator('#detail').inner_text()
    assert not errors


def test_clipboard_resolution_never_types_into_another_terminal(ui):
    page,_,_,errors=ui
    page.add_init_script('''
      const originalFetch=window.fetch;
      window.fetch=(url,options)=>{
        const data=String(url)==='/api/terminal/options'?{available:true,shells:['powershell'],roots:['.'],detach_ttl_s:600}:
          String(url)==='/api/terminal/sessions'?{sessions:[{id:'A',label:'终端 A',shell:'powershell',cwd:'.',status:'running'},{id:'B',label:'终端 B',shell:'powershell',cwd:'.',status:'running'}]}:null;
        return data?Promise.resolve(new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}})):originalFetch(url,options);
      };
      window.__writes=[];
      window.WebSocket=class {constructor(url){this.url=url;this.readyState=1;setTimeout(()=>this.onopen?.(),10)}send(data){const message=JSON.parse(data);if(message.type==='input')window.__writes.push({url:this.url,data:message.data})}close(){this.readyState=3;this.onclose?.()}};
      Object.defineProperty(navigator,'clipboard',{value:{readText:()=>new Promise(resolve=>window.__clipboard=resolve),writeText:async()=>{}}});
    ''')
    page.goto('http://127.0.0.1/terminal/')
    page.locator('#tabs button').filter(has_text='终端 A').click()
    page.locator('#paste').click();page.wait_for_function('!!window.__clipboard')
    page.locator('#tabs button').filter(has_text='终端 B').click()
    page.evaluate("window.__clipboard('不允许错终端输入')")
    page.wait_for_timeout(80)
    assert page.evaluate('window.__writes')==[]
    assert not errors


def test_chat_response_does_not_change_the_viewed_conversation(ui):
    page,_,runtime,errors=ui
    a=runtime.store.new_session('会话 A');b=runtime.store.new_session('会话 B')
    runtime.store.append_message(a,'assistant','A 的原历史')
    runtime.store.append_message(b,'assistant','B 的原历史')
    runtime.use_session(a)
    page.add_init_script(delay_script("String(url)==='/api/chat'"))
    page.goto('http://127.0.0.1/')
    page.locator('#messages').get_by_text('A 的原历史').wait_for()
    page.locator('#input').fill('A 的请求');page.locator('#send').click()
    page.wait_for_function('window.__pending.length===1')
    page.locator('#sessions li').filter(has_text='会话 B').click()
    page.locator('#messages').get_by_text('B 的原历史').wait_for()
    page.evaluate('(data)=>window.__pending[0].resolve(data)',{'session_id':a,'status':'done','cost_micro':0})
    page.wait_for_function("document.querySelector('#send').disabled===false")
    assert page.locator('#messages').get_by_text('B 的原历史').is_visible()
    assert page.locator('#sessions li.active').inner_text().startswith('会话 B')
    assert not errors


def test_chinese_composition_enter_does_not_send(ui):
    page,_,_,_=ui
    page.add_init_script(delay_script("String(url)==='/api/chat'"))
    page.goto('http://127.0.0.1/');page.locator('#chips .chip').first.wait_for()
    page.locator('#input').fill('中文候选词')
    page.locator('#input').evaluate("el=>el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}))")
    assert page.locator('#input').input_value()=='中文候选词'
    assert page.evaluate('window.__pending.length')==0


def test_main_workbench_has_usable_mobile_panels(ui):
    page,_,_,errors=ui
    page.set_viewport_size({'width':390,'height':844});page.goto('http://127.0.0.1/')
    page.locator('#chips .chip').first.wait_for()
    assert page.locator('#chat').bounding_box()['width']>=340
    assert page.locator('[data-pane="sessions"]').is_visible()
    page.locator('[data-pane="sessions"]').click()
    assert page.locator('#sessions').is_visible()
    page.locator('[data-pane="details"]').click()
    assert page.locator('[data-tab="skills"]').is_visible()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors


def test_chat_failure_restores_draft_without_automatic_resend(ui):
    page,_,_,errors=ui
    page.add_init_script('''const realFetch=window.fetch;window.__chatCalls=0;
      window.fetch=(url,options)=>{if(String(url)==='/api/chat'){window.__chatCalls++;return Promise.reject(new TypeError('网络断开'))}return realFetch(url,options)};''')
    page.goto('http://127.0.0.1/');page.locator('#chips .chip').first.wait_for()
    page.locator('#input').fill('需要保留的中文草稿');page.locator('#send').click()
    page.locator('#messages').get_by_text('网络断开',exact=False).wait_for()
    assert page.locator('#input').input_value()=='需要保留的中文草稿'
    assert page.locator('#send').is_enabled()
    assert page.evaluate('window.__chatCalls')==1
    assert not errors


def test_mcp_late_catalog_cannot_offer_wrong_server_approval(ui):
    page,_,_,errors=ui
    page.add_init_script('''const realFetch=window.fetch;
      window.fetch=(url,options)=>String(url)==='/api/mcp/overview'?Promise.resolve(new Response(JSON.stringify({servers:[
        {name:'服务 A',enabled:true,state:'idle',schema_state:'absent'},
        {name:'服务 B',enabled:true,state:'idle',schema_state:'absent'}]}))):realFetch(url,options);''')
    page.add_init_script(delay_script("String(url).includes('/catalog?')||String(url).endsWith('/health')"))
    page.goto('http://127.0.0.1/mcp/')
    page.locator('.server').filter(has_text='服务 A').get_by_role('button',name='查看完整目录与授权').click()
    page.wait_for_function('window.__pending.length===1')
    page.locator('.server').filter(has_text='服务 B').get_by_role('button',name='诊断',exact=True).click()
    page.wait_for_function('window.__pending.length===2')
    page.evaluate("window.__pending[1].resolve({executable_found:false,server:'服务 B'})")
    page.locator('#detail').get_by_text('服务 B',exact=False).wait_for()
    page.evaluate("window.__pending[0].resolve({total:1,confirmed:false,fingerprint:'A指纹',tools:[{name:'A工具'}]})")
    page.wait_for_timeout(60)
    assert page.locator('#detail-title').inner_text()=='服务 B'
    assert page.locator('#confirm').count()==0
    assert 'A工具' not in page.locator('#catalog').inner_text()
    assert not errors


@pytest.mark.parametrize('path',['terminal','jobs','mcp','android','capabilities','project'])
def test_capability_pages_have_mobile_navigation_and_safe_initial_state(ui,path):
    page,_,_,errors=ui
    page.set_viewport_size({'width':390,'height':844});page.goto(f'http://127.0.0.1/{path}/')
    page.locator('.ui-global-nav').wait_for()
    assert page.locator('.ui-global-nav a').count()==9
    assert page.locator('.ui-global-nav a[aria-current]').count()==1
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    if path=='android':
        for name in ['tap','type','launch','live','snapshot','observe']:
            assert page.locator('#'+name).is_disabled()
    if path=='jobs':assert page.locator('#cancel').is_disabled()
    if path=='terminal':assert page.locator('#paste').is_disabled()
    assert not errors


def test_request_validation_hides_echoed_input_and_dialog_cancel_is_safe(ui):
    page,_,_,errors=ui
    page.add_init_script('''const realFetch=window.fetch;window.fetch=(url,options)=>String(url)==='/invalid'?
      Promise.resolve(new Response(JSON.stringify({detail:[{loc:['body','timeout'],msg:'must be positive',input:'PRIVATE_SECRET'}]}),{status:422})):realFetch(url,options);''')
    page.goto('http://127.0.0.1/jobs/');page.locator('.ui-global-nav').wait_for()
    message=page.evaluate("async()=>{try{await OmniUI.request('/invalid')}catch(e){return e.message}}")
    assert message=='timeout: must be positive'
    page.evaluate("()=>{window.answer='pending';void OmniUI.ask({title:'确认测试',message:'取消不会执行操作',danger:true}).then(answer=>window.answer=answer)}")
    assert page.get_by_role('button',name='取消',exact=True).evaluate('el=>el===document.activeElement')
    page.keyboard.press('Escape');page.wait_for_function('window.answer===false')
    assert not errors


def test_exited_terminal_replays_output_without_enabling_input(ui):
    page,_,_,errors=ui
    page.add_init_script('''const realFetch=window.fetch;
      window.fetch=(url,options)=>{
        const data=String(url)==='/api/terminal/options'?{available:true,shells:['cmd'],roots:['.']}:
          String(url)==='/api/terminal/sessions'?{sessions:[{id:'exited',label:'已退出的终端',status:'exited',exit_code:0}]}:null;
        return data?Promise.resolve(new Response(JSON.stringify(data))):realFetch(url,options);
      };window.__connections=0;
      window.WebSocket=class {constructor(){window.__connections++;this.readyState=1;setTimeout(()=>{
        this.onopen?.();this.onmessage?.({data:JSON.stringify({type:'output',seq:1,data:'退出前保留的输出'})});
        this.onmessage?.({data:JSON.stringify({type:'exit',status:'exited',exit_code:0})});this.close();},20)}
        send(){}close(){this.readyState=3;this.onclose?.()}};''')
    page.goto('http://127.0.0.1/terminal/')
    page.locator('#state').get_by_text('退出码 0',exact=False).wait_for()
    page.locator('.xterm-rows').get_by_text('退出前保留的输出',exact=False).wait_for()
    assert page.locator('#paste').is_disabled()
    assert page.evaluate('window.__connections')==1
    assert not errors


def test_terminal_reconnect_is_bounded_and_manual_retry_is_available(ui):
    page,_,_,errors=ui
    page.add_init_script('''const realFetch=window.fetch;
      window.fetch=(url,options)=>{
        const data=String(url)==='/api/terminal/options'?{available:true,shells:['cmd'],roots:['.']}:
          String(url)==='/api/terminal/sessions'?{sessions:[{id:'retry',label:'测试终端',status:'running'}]}:null;
        return data?Promise.resolve(new Response(JSON.stringify(data))):realFetch(url,options);
      };window.__connections=0;const timeout=window.setTimeout;
      window.setTimeout=(fn,ms,...args)=>timeout(fn,[1000,2000,4000,8000,15000].includes(ms)?1:ms,...args);
      window.WebSocket=class {constructor(){window.__connections++;this.readyState=3;timeout(()=>this.onclose?.(),5)}send(){}close(){this.onclose?.()}};''')
    page.goto('http://127.0.0.1/terminal/')
    page.locator('#state').get_by_text('恢复连接未成功',exact=False).wait_for()
    assert page.evaluate('window.__connections')<=6
    page.locator('#reconnect').click()
    page.wait_for_function('window.__connections>6')
    page.locator('#state').get_by_text('恢复连接未成功',exact=False).wait_for()
    assert page.evaluate('window.__connections')<=12
    assert page.locator('#paste').is_disabled()
    assert not errors


def test_settings_and_details_are_reachable_on_mobile(ui):
    page,_,_,errors=ui
    page.set_viewport_size({'width':390,'height':844});page.goto('http://127.0.0.1/')
    page.locator('#chips .chip').first.wait_for();page.locator('.ui-settings').click()
    assert page.locator('#hub').is_visible()
    assert page.locator('.hub-body').bounding_box()['width']>=330
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors


def test_cancel_after_switch_uses_original_run_and_confirmation_target(ui):
    page,_,runtime,errors=ui
    a=runtime.store.new_session('运行会话 A');b=runtime.store.new_session('查看会话 B');runtime.use_session(a)
    page.add_init_script('''const realFetch=window.fetch;window.__cancelled=[];
      window.fetch=(url,options={})=>{
        if(String(url).startsWith('/api/runs?'))return Promise.resolve(new Response(JSON.stringify({runs:[{run_id:'run-A',status:'running'}]})));
        if(String(url)==='/api/runs/run-A/events?after=0')return Promise.resolve(new Response(JSON.stringify({events:[]})));
        if(String(url).endsWith('/cancel')){window.__cancelled.push(String(url));return Promise.resolve(new Response('{}'))}
        return realFetch(url,options);
      };''')
    page.add_init_script(delay_script("String(url)==='/api/chat'"))
    page.goto('http://127.0.0.1/');page.locator('#chips .chip').first.wait_for()
    page.locator('#input').fill('A 的运行');page.locator('#send').click()
    page.locator('#active-run button').get_by_text('停止此回合',exact=True).wait_for()
    page.locator('#sessions li').filter(has_text='查看会话 B').click()
    page.wait_for_function('(id)=>document.querySelector("#sessions li.active .s-id")?.textContent==="#"+id',arg=b)
    page.locator('#active-run button').filter(has_text='停止此回合').click()
    assert page.locator('#ui-dialog-title').inner_text()==f'停止会话 #{a} 的回合'
    page.get_by_role('button',name='停止回合',exact=True).click()
    page.wait_for_function('window.__cancelled.length===1')
    assert page.evaluate('window.__cancelled')==['/api/runs/run-A/cancel']
    page.evaluate('(data)=>window.__pending[0].resolve(data)',{'session_id':a,'status':'cancelled'})
    page.wait_for_function('!document.querySelector("#send").disabled')
    assert page.locator('#active-run').is_hidden()
    assert not errors
