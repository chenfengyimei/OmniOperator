// 用量面板（D10 账本）：今日/剩余/按用途汇总/调用流水/模型能力表。
import {$,esc,usd} from './dom.js?v=20261031m17';
import {api} from './api.js?v=20261031m17';

// 用途在账本里是英文键名，界面上必须翻成人话
const PURPOSE_CN={chat:'聊天',review:'整理记忆',vision:'看图',image:'画图',title:'起标题',
  summary:'压缩旧对话',approval:'判断要不要问你',cron:'定时任务',embedding:'查记忆',
  skill:'编技能',subagent:'分头干活',test:'测连通'};
const purposeCN=p=>PURPOSE_CN[p]||p;
export async function loadUsage(){
  const d=await api('/api/llm/usage?days=7&limit=15');
  const t=d.today;
  const failures=(d.summary.by_purpose||[]).reduce((s,p)=>s+(p.failures||0),0);
  $('#usage-today').innerHTML=
    `<div class="cron-row"><span class="c-name">今天花了 ${usd(t.used_micro)}</span>`+
    `<span class="c-sched">最近 7 天说了 ${d.summary.calls||0} 次${d.summary.unknown_cost?' · 有 '+d.summary.unknown_cost+' 次算不出钱':''}${failures?' · '+failures+' 次没成功':''}</span>`+
    `<span>${t.limit_micro!=null?'还能花 '+usd(t.remaining_micro):'没设上限（只记账，不拦你）'}</span></div>`+
    (d.per_run_limit_micro?`<div class="hint" style="margin-top:6px">一轮对话最多花 ${usd(d.per_run_limit_micro)}，到点就停</div>`:'');
  $('#usage-purpose').innerHTML=d.summary.by_purpose.length?d.summary.by_purpose.map(p=>
    `<div class="cron-row"><span class="c-name">${esc(purposeCN(p.purpose))}</span>`+
    `<span class="c-sched">${p.calls} 次 · ${p.failures?p.failures+' 次没成功 · ':''}${p.unknown_cost?p.unknown_cost+' 次算不出钱 · ':''}${usd(p.cost_micro)}</span>`+
    `</div>`).join(''):'<div class="empty">最近 7 天没花过钱</div>';
  $('#usage-calls').innerHTML=d.calls.length?d.calls.map(c=>
    `<div class="cron-row"><span class="c-name" style="flex:0 0 auto">${c.outcome==='ok'?'✅':'❌'}</span>`+
    `<span class="c-sched">${esc((c.model||'').split('/').pop())} · ${esc(purposeCN(c.purpose))} · 约 ${(c.prompt_tokens||0)+(c.completion_tokens||0)} 字${c.attempts>1?' · 重试了'+c.attempts+'次':''}</span>`+
    `<span style="color:var(--dim);font-size:11.5px">${c.cost_known?usd(c.cost_micro):'这次算不出钱'}</span></div>`).join('')
    :'<div class="empty">还没花过钱</div>';
  const models=Object.entries(d.models||{});
  $('#usage-models').innerHTML=models.length?models.map(([name,cap])=>
    `<div class="cron-row"><span class="c-name" style="overflow:hidden;text-overflow:ellipsis">${esc(name)}</span>`+
    `<span class="c-sched">${cap.vision?'会看图 ':''}${cap.tools?'会调工具 ':''}${cap.stream?'边打边显示 ':''}${cap.context_tokens?'一次能读 '+cap.context_tokens+' 字':''}</span>`+
    `<span style="color:var(--dim);font-size:11.5px">${cap.input_usd_per_m!=null?'每百万字 $'+cap.input_usd_per_m:''}</span></div>`).join('')
    :'<div class="empty">还没登记任何模型（默认：会用工具、不会看图）</div>';
}
