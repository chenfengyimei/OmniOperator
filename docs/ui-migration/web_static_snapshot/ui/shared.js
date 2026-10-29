/* 本地工作台公共交互：请求、反馈、对话框和导航；不触发能力操作。 */
(() => {
  'use strict';
  const labels={queued:'等待执行',running:'运行中',cancelling:'停止中',succeeded:'已完成',failed:'失败',cancelled:'已取消',timed_out:'已超时',unknown:'待核查',idle:'未连接',starting:'连接中',ready:'已连接',closed:'已断开',disabled:'未启用',exited:'已退出',absent:'未发现',confirmed:'已确认',drifted:'需复查'};
  function element(tag,text,className){const node=document.createElement(tag);if(text!=null)node.textContent=text;if(className)node.className=className;return node}
  function notify(message,tone='info'){
    let region=document.getElementById('ui-notices');
    if(!region){region=element('div',null,'ui-notices');region.id='ui-notices';region.setAttribute('aria-live','polite');document.body.append(region)}
    const node=element('div',message,'ui-notice');node.dataset.tone=tone;region.append(node);
    while(region.children.length>3)region.firstChild.remove();setTimeout(()=>node.remove(),6500);
  }
  async function request(url,options={}){
    const {timeoutMs,signal,...fetchOptions}=options,controller=new AbortController();
    const method=(options.method||'GET').toUpperCase(),writing=!['GET','HEAD'].includes(method);
    const headers=new Headers(options.headers||{});
    if(options.body!==undefined&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
    const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});
    if(signal?.aborted)controller.abort();
    const timeout=timeoutMs??(writing?60000:15000),timer=timeout?setTimeout(abort,timeout):null;
    try{
      const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...fetchOptions,headers,signal:controller.signal});
      let data;try{data=await response.json()}catch{throw Error('服务返回了无法读取的内容，请确认工作台版本与连接状态')}
      if(!response.ok){
        let message=typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(row=>`${(row.loc||[]).slice(1).join('.')}: ${row.msg}`).join('；'):'请求未完成';
        if(response.status===401){message='工作台尚未配对或配对已失效，请返回工作台重新配对';document.dispatchEvent(new CustomEvent('omni:auth-required'))}
        if(message==='Not Found')message='未找到内容或接口，请确认服务已更新';
        const error=Error(message);error.status=response.status;throw error;
      }
      return data;
    }catch(error){
      if(error.name==='AbortError'&&!signal?.aborted){const timeoutError=Error(writing?'等待响应超时；操作可能已经下发，请核查后再操作':'读取超时，请检查连接后刷新');timeoutError.uncertain=writing;throw timeoutError}
      throw error;
    }finally{if(timer)clearTimeout(timer);signal?.removeEventListener('abort',abort)}
  }
  let dialog;
  function ask({title='确认操作',message='',input,confirmLabel='确认',danger=false}){
    if(dialog?.open)return Promise.resolve(input===undefined?false:null);
    if(!dialog){dialog=element('dialog',null,'ui-dialog');dialog.id='ui-dialog';document.body.append(dialog)}
    const previous=document.activeElement,form=element('form');form.method='dialog';
    const heading=element('h2',title);heading.id='ui-dialog-title';dialog.setAttribute('aria-labelledby',heading.id);
    const content=element('p',message),actions=element('div',null,'ui-dialog-actions');
    const cancel=element('button','取消'),confirm=element('button',confirmLabel);cancel.value='cancel';cancel.formNoValidate=true;confirm.value='confirm';confirm.className=danger?'ui-danger':'ui-primary';
    form.append(heading,content);let field;
    if(input!==undefined){const label=element('label','名称');field=element('input');field.value=input;field.required=true;field.maxLength=60;label.append(field);form.append(label)}
    actions.append(cancel,confirm);form.append(actions);dialog.replaceChildren(form);dialog.returnValue='';
    return new Promise(resolve=>{
      dialog.addEventListener('close',()=>{const accepted=dialog.returnValue==='confirm';resolve(input===undefined?accepted:accepted?field.value.trim():null);previous?.focus?.()},{once:true});
      dialog.showModal();(field||cancel).focus();
    });
  }
  function badge(state){const node=element('span',labels[state]||state||'未知','ui-badge');node.dataset.state=state;return node}
  function date(value){if(value==null)return '—';const date=new Date(typeof value==='number'?value*1000:value);return Number.isNaN(date.getTime())?'—':date.toLocaleString()}
  function mount(){
    const page=location.pathname.split('/').filter(Boolean)[0]||'workbench';document.body.dataset.uiPage=page;
    const nav=element('nav',null,'ui-global-nav');nav.setAttribute('aria-label','工作台功能导航');
    for(const [path,label]of [['/','对话工作台'],['/capabilities/','能力总览'],['/terminal/','人工终端'],['/jobs/','后台任务'],['/mcp/','MCP 服务'],['/android/','安卓控制'],['/project/','项目索引']]){
      const link=element('a',label);link.href=path;
      if(location.pathname===path)link.setAttribute('aria-current','page');
      if(page==='workbench'&&path!=='/'){link.target='_blank';link.rel='noopener'}nav.append(link);
    }
    (document.querySelector('body>header')||document.body.firstChild).after(nav);
    if(page==='workbench'){
      const panes=element('div',null,'ui-panes');panes.setAttribute('aria-label','工作台区域');
      for(const [id,label]of [['sessions','会话'],['chat','对话'],['details','资料与工具']]){
        const button=element('button',label);button.type='button';button.dataset.pane=id;
        button.onclick=()=>{document.body.dataset.pane=id;panes.querySelectorAll('button').forEach(node=>node.setAttribute('aria-pressed',String(node===button)))};panes.append(button);
      }
      document.querySelector('body>header').append(panes);panes.querySelector('[data-pane="chat"]').click();
      const settings=element('button','设置','ui-settings');settings.type='button';settings.onclick=()=>document.getElementById('open-settings')?.click();document.querySelector('body>header').append(settings);
    }
    document.body.classList.add('ui-ready');
  }
  window.OmniUI={request,notify,ask,badge,date,element,labels};
  document.addEventListener('omni:auth-required',()=>notify('需要配对：请返回工作台打开控制台提供的配对链接','error'));
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
