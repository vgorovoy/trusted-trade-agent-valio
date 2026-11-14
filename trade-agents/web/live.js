const $ = id => document.getElementById(id);
const state = {scenarios: [], selected: new Set(), mode: 'offline', runId: null,
  cursor: 0, queue: [], networkDone: false, report: null, finished: 0, total: 0,
  pollTimer: null, playTimer: null, polling: false};

const toolNames = {
  search_products: 'Search catalogue', get_product: 'Read product', get_offers: 'Get signed offers',
  plan_need: 'Plan the need', find_substitutes: 'Find safe substitutes', place_order: 'Submit proposal',
  get_order: 'Read order', add_note: 'Write audit note', finish: 'Agent finishes'
};
const fmtTime = iso => new Date(iso).toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'});

function node(tag, className, value){const n=document.createElement(tag);if(className)n.className=className;if(value!==undefined)n.textContent=value;return n}
async function api(url, options={}){
  const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${response.status}`);
  return data;
}
function setRunState(label,kind){const n=$('run-state');n.className=`run-state ${kind}`;n.replaceChildren(node('i'),document.createTextNode(label))}
function setProgress(){const pct=state.total?Math.round(state.finished/state.total*100):0;$('progress-bar').style.width=`${pct}%`;$('progress-number').textContent=`${state.finished} / ${state.total}`}
function available(){return state.scenarios.filter(s=>state.mode==='offline'||s.recorded)}

function renderChoices(){
  const q=$('scenario-search').value.trim().toLowerCase();const list=$('scenario-list');list.replaceChildren();
  for(const sc of available().filter(s=>!q||`${s.id} ${s.category} ${s.title} ${s.request}`.toLowerCase().includes(q))){
    const label=node('label','scenario-row');const box=node('input');box.type='checkbox';box.checked=state.selected.has(sc.id);
    box.addEventListener('change',()=>{box.checked?state.selected.add(sc.id):state.selected.delete(sc.id);renderCount()});
    const copy=node('span');copy.append(node('b','',sc.title));const small=node('small','',`${sc.category.toUpperCase()} · ${sc.buyer} · ${sc.tier}`);if(sc.recorded)small.append(node('span','recorded-dot',' · RECORDED'));copy.append(small);label.append(box,copy);list.append(label);
  }
  if(!list.children.length)list.append(node('div','scenario-row','No matching scenarios.'));
  renderCount();
}
function renderCount(){$('selected-count').textContent=state.selected.size;$('run-button').disabled=Boolean(state.runId)||state.selected.size===0}
function choosePreset(preset){
  const pool=available();let chosen;
  if(preset==='one')chosen=pool.filter(s=>s.id==='basic-milk');
  else if(preset==='smoke')chosen=pool.filter(s=>s.tier==='smoke');
  else if(preset==='core')chosen=pool.filter(s=>s.tier==='smoke'||s.tier==='core');
  else chosen=pool;
  state.selected=new Set((chosen.length?chosen:pool.slice(0,1)).map(s=>s.id));
  document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('active',b.dataset.preset===preset));
  renderChoices();
}
function selectMode(mode){
  if(state.runId)return;
  state.mode=mode;document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===mode));
  for(const b of document.querySelectorAll('[data-preset]'))b.disabled=mode==='recorded'&&b.dataset.preset!=='one';
  state.selected=new Set(mode==='recorded'?available().map(s=>s.id):['basic-milk']);
  $('setup-note').textContent=mode==='recorded'?'Replays saved Nemotron responses. No provider call; only scenarios with a recording can run.':'Runs locally with temporary data. The page never contacts Valio or a live AI provider.';
  document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('active',mode==='offline'&&b.dataset.preset==='one'));
  renderChoices();
}

function addEvent(kind,title,body,at){
  $('empty-stage').classList.add('hidden');const card=node('article',`event ${kind}`);
  const head=node('div','event-title');head.append(node('span','event-icon',kind==='fail'?'!':kind==='pass'?'✓':kind==='scenario'?'▶':'↳'),node('span','',title));
  if(at)head.append(node('span','event-time',fmtTime(at)));card.append(head);
  if(body)card.append(node('small','',body));$('activity').append(card);$('activity').scrollTop=$('activity').scrollHeight;
  return card;
}
function addDetails(card,label,content){const d=node('details');d.append(node('summary','',label),content);card.append(d)}
function renderEvent(ev){
  const d=ev.data||{};
  if(ev.kind==='run_started')addEvent('scenario',`Run started · ${d.total} scenario${d.total===1?'':'s'}`,d.mode==='recorded'?'Saved Nemotron replies are being replayed against the current application code.':'The local rule-based buyer agent is running in isolated test worlds.',ev.at);
  if(ev.kind==='scenario_started')addEvent('scenario',`${d.id} · ${d.title}`,`${d.buyer} asks: “${d.request}”`,ev.at);
  if(ev.kind==='tool'){
    const card=addEvent('tool',`${toolNames[d.tool]||d.tool} · step ${d.step}`,`${d.scenario_id}: ${d.result||'Tool called'}${d.error?' · ERROR':''}`,ev.at);
    if(d.args&&Object.keys(d.args).length){const pre=node('pre','',JSON.stringify(d.args,null,2));addDetails(card,'Show tool arguments',pre)}
  }
  if(ev.kind==='scenario_finished'){
    state.finished++;setProgress();$('progress-copy').textContent=d.skipped?`${d.id} skipped`:d.passed?`${d.id} passed`:`${d.id} failed`;
    const kind=d.skipped?'skip':d.passed?'pass':'fail';const card=addEvent(kind,`${d.skipped?'SKIP':d.passed?'PASS':'FAIL'} · ${d.id}`,d.skipped||`Gateway: ${d.status||'—'} · ${d.total_eur==null?'No order':`${d.total_eur.toFixed(2)} EUR`} · ${d.api_calls} Valio API calls`,ev.at);
    const checks=node('div','checks');for(const c of d.checks||[]){const row=node('div',`check ${c.ok?'':'bad'}`);row.append(node('b','',c.ok?'✓':'✕'),node('span','',`${c.name}: ${c.detail}`));checks.append(row)}
    if(checks.children.length)addDetails(card,`${(d.checks||[]).filter(c=>c.ok).length}/${d.checks.length} checks passed · inspect evidence`,checks);
    if(d.explanation)card.append(node('small','',`Agent explanation: ${d.explanation}`));
  }
  if(ev.kind==='run_failed')addEvent('fail','Run failed',d.error||'Unknown error',ev.at);
  if(ev.kind==='run_finished')addEvent('pass','Benchmark finished',`${d.passed}/${d.scored} scored scenarios passed`,ev.at);
}
function renderSummary(){
  const r=state.report;if(!r)return;const box=$('summary');box.replaceChildren();box.classList.remove('hidden');box.append(node('h4','',`Benchmark result · ${r.model}`));
  const grid=node('div','summary-grid');const values=[['Passed',`${r.passed}/${r.scored}`],['Accuracy',r.scored?`${Math.round(r.accuracy*100)}%`:'—'],['Coverage',`${r.coverage.scored}/${r.coverage.selected}`],['Safety flags',String(r.safety.violations)]];
  for(const [label,value] of values){const item=node('div');item.append(node('strong','',value),node('small','',label));grid.append(item)}box.append(grid);
  box.append(node('p','',`Scored ${r.scored} of ${r.coverage.selected}; skipped cases are excluded from accuracy. Safety flags cover only the independent checks listed by this benchmark. Live model calls: ${r.usage.live_model_calls}.`));
}
function maybeFinish(){
  if(!state.networkDone||state.queue.length)return;
  clearInterval(state.pollTimer);clearInterval(state.playTimer);state.pollTimer=state.playTimer=null;
  setRunState(state.report?'COMPLETE':'ERROR',state.report?'done':'error');
  if(state.report)renderSummary();
  state.runId=null;renderCount();
}
async function poll(){
  if(!state.runId||state.polling||state.networkDone)return;
  state.polling=true;
  try{const data=await api(`/ui/live/runs/${encodeURIComponent(state.runId)}?after=${state.cursor}`);state.cursor=data.next;state.queue.push(...data.events);if(data.done){state.networkDone=true;state.report=data.report;clearInterval(state.pollTimer);if(state.queue.length)setRunState('SHOWING TRACE','running');maybeFinish()}}
  catch(e){state.networkDone=true;clearInterval(state.pollTimer);state.queue.push({kind:'run_failed',at:new Date().toISOString(),data:{error:e.message}});maybeFinish()}
  finally{state.polling=false}
}
async function start(){
  if(state.runId||!state.selected.size)return;
  $('activity').replaceChildren();$('summary').classList.add('hidden');$('empty-stage').classList.remove('hidden');state.queue=[];state.cursor=0;state.finished=0;state.total=state.selected.size;state.networkDone=false;state.polling=false;state.report=null;setProgress();$('progress-copy').textContent='Preparing isolated test worlds…';setRunState('RUNNING','running');
  try{
    const response=await api('/ui/live/runs',{method:'POST',body:JSON.stringify({mode:state.mode,scenario_ids:[...state.selected]})});
    state.runId=response.run_id;renderCount();state.pollTimer=setInterval(poll,250);
    state.playTimer=setInterval(()=>{if(state.queue.length)renderEvent(state.queue.shift());maybeFinish()},320);
    await poll();
  }catch(e){setRunState('ERROR','error');$('progress-copy').textContent=e.message;addEvent('fail','Could not start',e.message);state.runId=null;renderCount()}
}
async function init(){
  try{const data=await api('/ui/live/scenarios');state.scenarios=data.scenarios;selectMode('offline')}
  catch(e){$('scenario-list').append(node('div','scenario-row',`Cannot load scenarios: ${e.message}. Run this page through the local web server.`));$('setup-note').textContent='Start the local web server to run real scenarios.'}
}
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>selectMode(b.dataset.mode)));
document.querySelectorAll('[data-preset]').forEach(b=>b.addEventListener('click',()=>choosePreset(b.dataset.preset)));
$('scenario-search').addEventListener('input',renderChoices);$('run-button').addEventListener('click',start);
init();
