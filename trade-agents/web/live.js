const $ = id => document.getElementById(id);
const state = {scenarios: [], selected: new Set(), mode: 'offline', runId: null,
  cursor: 0, queue: [], networkDone: false, report: null, finished: 0, total: 0,
  pollTimer: null, playTimer: null, polling: false, starting: false, failed: false, capabilities: null, capabilityError: '', dataset: 'synthetic', loadingDataset: false};

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
function available(){return state.scenarios.filter(s=>state.mode!=='recorded'||s.recorded)}

function renderChoices(){
  const q=$('scenario-search').value.trim().toLowerCase();const list=$('scenario-list');list.replaceChildren();
  for(const sc of available().filter(s=>!q||`${s.id} ${s.category} ${s.title} ${s.request}`.toLowerCase().includes(q))){
    const label=node('label','scenario-row');const box=node('input');box.type=state.mode==='live'?'radio':'checkbox';box.name='scenario';box.disabled=Boolean(state.runId)||state.starting;box.checked=state.selected.has(sc.id);
    box.addEventListener('change',()=>{if(state.mode==='live')state.selected=new Set([sc.id]);else box.checked?state.selected.add(sc.id):state.selected.delete(sc.id);renderCount()});
    const copy=node('span');copy.append(node('b','',sc.title));const small=node('small','',`${sc.buyer[0].toUpperCase()+sc.buyer.slice(1)}, ${sc.category}`);if(sc.recorded)small.append(node('span','recorded-dot','Recorded'));copy.append(small);label.append(box,copy);list.append(label);
  }
  if(!list.children.length)list.append(node('div','scenario-row','No matching scenarios.'));
  renderCount();
}
function selectedProvider(){return state.capabilities?.providers?.find(p=>p.id===$('provider-select').value)}
function liveReady(){return state.capabilities?.live_available && selectedProvider()?.configured && Boolean($('model-select').value)}
function renderCount(){
  const busy=Boolean(state.runId)||state.starting||state.loadingDataset;
  $('selected-count').textContent=state.selected.size;
  $('run-button').disabled=busy||state.selected.size===0||(state.mode==='live'&&(!liveReady()||state.selected.size!==1));
  $('run-button').textContent=state.mode==='live'?'Run live model':'Run';
  for(const control of document.querySelectorAll('[data-mode],#provider-select,#model-select,#dataset-select'))control.disabled=busy||(state.dataset==='official_snapshot'&&control.dataset.mode&&control.dataset.mode!=='live');
  for(const control of document.querySelectorAll('[data-preset]'))control.disabled=busy||(state.mode!=='offline'&&control.dataset.preset!=='one');
}
function renderModels(){
  const select=$('model-select');select.replaceChildren();
  for(const model of selectedProvider()?.models||[]){const option=node('option','',model.label||model.id);option.value=model.id;select.append(option)}
  renderProviderStatus();renderCount();
}
function renderProviderStatus(){
  $('provider-status').textContent=state.capabilityError||(!state.capabilities?.live_available?'Live runs are not enabled on this server.':!selectedProvider()?.configured?'This provider is not configured on the server.':'Configured on this server. One scenario per run; provider usage may be billed.');
}
function renderProviders(){
  const select=$('provider-select');select.replaceChildren();
  for(const provider of state.capabilities?.providers||[]){const option=node('option','',provider.label+(provider.configured?'':' · unavailable'));option.value=provider.id;select.append(option)}
  const first=state.capabilities?.providers?.find(p=>p.configured);if(first)select.value=first.id;
  renderModels();
}
function presetPool(preset,pool){
  if(preset==='one')return pool.filter(s=>s.id==='basic-milk');
  if(preset==='smoke')return pool.filter(s=>s.tier==='smoke');
  if(preset==='core')return pool.filter(s=>s.tier==='smoke'||s.tier==='core');
  return pool;
}
function renderPresetCounts(){
  const pool=available();
  for(const b of document.querySelectorAll('[data-preset]')){
    b.dataset.label??=b.textContent;b.replaceChildren(b.dataset.label);
    if(b.dataset.preset!=='one'&&state.scenarios.length)b.append(' · ',node('span','preset-count',String(presetPool(b.dataset.preset,pool).length)));
  }
}
function choosePreset(preset){
  if(state.runId||state.starting||(state.mode==='live'&&preset!=='one'))return;
  const pool=available();const chosen=presetPool(preset,pool);
  state.selected=new Set((chosen.length?chosen:pool.slice(0,1)).map(s=>s.id));
  document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('active',b.dataset.preset===preset));
  renderChoices();
}
function selectMode(mode){
  if(state.runId||state.starting)return;
  if(!['offline','recorded','live'].includes(mode))mode='offline';
  if(state.dataset==='official_snapshot')mode='live';
  state.mode=mode;document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('active',b.dataset.mode===mode);b.setAttribute('aria-pressed',String(b.dataset.mode===mode))});
  for(const b of document.querySelectorAll('[data-preset]'))b.disabled=mode==='recorded'&&b.dataset.preset!=='one';
  state.selected=new Set(mode==='recorded'?available().map(s=>s.id):available().slice(0,1).map(s=>s.id));
  $('live-settings').classList.toggle('hidden',mode!=='live');renderProviderStatus();
  $('setup-note').textContent=state.dataset==='official_snapshot'?'One live model case after a bounded official mock API capture. Later tools use the snapshot; no real supplier order is placed.':mode==='live'?'Real model calls against synthetic supplier data. Exactly one case; no real supplier orders. Server credentials stay on the server.':mode==='recorded'?'Replays saved Nemotron responses. No provider call; only scenarios with a recording can run.':'Runs locally with temporary data. The page never contacts Valio or a live AI provider.';
  document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('active',mode==='offline'&&b.dataset.preset==='one'));
  renderPresetCounts();renderChoices();
}

function addEvent(kind,title,body,at,meta){
  $('empty-stage').classList.add('hidden');const card=node('article',`event ${kind}`);
  const head=node('div','event-title');head.append(node('span','event-icon',kind==='fail'?'!':kind==='pass'?'✓':kind==='scenario'?'▶':'↳'),node('span','',title));
  if(at)head.append(node('span','event-time',fmtTime(at)));card.append(head);
  if(meta)card.append(node('span','event-meta',meta));
  if(body)card.append(node('small','',body));$('activity').append(card);$('activity').scrollTop=$('activity').scrollHeight;
  return card;
}
function addDetails(card,label,content){const d=node('details');d.append(node('summary','',label),content);card.append(d)}
function renderEvent(ev){
  const d=ev.data||{};
  if(ev.kind==='run_started')addEvent('scenario',`Started ${d.total} scenario${d.total===1?'':'s'}`,d.mode==='live'?`Live ${d.provider||'provider'} / ${d.model||'model'} calls using ${d.dataset==='official_snapshot'?'a captured official mock API snapshot':'a synthetic supplier world'}.`:d.mode==='recorded'?'Saved Nemotron replies are being replayed against the current application code.':'The local rule-based buyer agent is running in isolated test worlds.',ev.at);
  if(ev.kind==='capture_started')addEvent('scenario','Capturing official mock API inputs',d.message,ev.at);
  if(ev.kind==='snapshot'){const card=addEvent('scenario','Official mock snapshot captured',`Captured: ${d.capture_completed_at}. Delivery: ${d.requested_date}. ${d.product_count} products; ${d.supplier_capture_http_calls} supplier HTTP calls. ${d.availability_freshness}`,ev.at,`SHA-256 ${d.snapshot_sha256}`);card.append(node('small','',`Scenario clock: ${d.business_clock} · ${d.clock_label}`));addDetails(card,'Capture provenance',node('pre','',JSON.stringify(d,null,2)));}
  if(ev.kind==='scenario_started'){
    const tier=state.scenarios.find(s=>s.id===d.id)?.tier;
    const scenarioCard=addEvent('scenario',d.title,`${d.buyer[0].toUpperCase()+d.buyer.slice(1)} kitchen asks: “${d.request}”`,ev.at,[d.id,tier,d.category].filter(Boolean).join(' · '));
    if(d.expected_status)scenarioCard.append(node('small','',`Capture-dependent expected outcome: ${d.expected_status}. ${d.positive_transactable?'Purchase is feasible under this snapshot.':'This is not evidence of a feasible purchase; inspect the captured constraints.'}`));
  }
  if(ev.kind==='model_call')addEvent('model',`Model call ${d.call}`,d.model||'Calling the selected provider…',ev.at);
  if(ev.kind==='model_usage')addEvent('tool',`Usage for call ${d.call}`,`${d.input_tokens??'Unknown'} input tokens · ${d.output_tokens??'Unknown'} output tokens · Cost ${d.cost_usd==null?'unknown':`${d.cost_usd} USD`}`,ev.at);
  if(ev.kind==='model_message')addEvent('model','Public model message',d.text||'(No public text)',ev.at,d.scenario_id);
  if(ev.kind==='tool'){
    const card=addEvent('tool',toolNames[d.tool]||d.tool,`${d.result||'Tool called'}${d.error?'. Tool returned an error.':''}`,ev.at);
    if(d.response){addDetails(card,'Tool response',node('pre','',typeof d.response==='string'?d.response:JSON.stringify(d.response,null,2)))}
    if(d.args&&Object.keys(d.args).length){const pre=node('pre','',JSON.stringify(d.args,null,2));addDetails(card,'Arguments',pre)}
  }
  if(ev.kind==='scenario_finished'){
    state.finished++;setProgress();$('progress-copy').textContent=d.skipped?`${d.id} skipped`:d.passed?`${d.id} passed`:`${d.id} failed`;
    const kind=d.skipped?'skip':d.passed?'pass':'fail';const card=addEvent(kind,`${d.skipped?'Skipped':d.passed?'Passed':'Failed'}: ${d.id}`,d.skipped||`Gateway ${d.status||'made no decision'}, ${d.total_eur==null?'no order':`${d.total_eur.toFixed(2)} EUR`}, ${d.api_calls??0} ${state.dataset==='official_snapshot'?'snapshot-backed gateway':'synthetic supplier'} API calls`,ev.at);
    const outcome=node('div','outcome-fields');outcome.append(node('div','',`Gateway outcome: ${d.status||'No order state reported'}`),node('div','',`Human stage: ${d.human_stage==='scripted'?'Scripted benchmark decisions; not a live user approval.':['pending_approval','awaiting_cart_confirmation','awaiting_choice'].includes(d.status)?'Decision required; this isolated benchmark does not provide manual approval.':'No pending human decision reported'}`));card.append(outcome);
    if(d.error)addEvent('fail','Scenario error',d.error,ev.at);
    const checks=node('div','checks');for(const c of d.checks||[]){const row=node('div',`check ${c.ok?'':'bad'}`);row.append(node('b','',c.ok?'✓':'✕'),node('span','',`${c.name}: ${c.detail}`));checks.append(row)}
    if(checks.children.length)addDetails(card,`Deterministic checks: ${(d.checks||[]).filter(c=>c.ok).length} of ${d.checks.length} passed`,checks);
    if(d.explanation)card.append(node('small','',`Agent explanation: ${d.explanation}`));
  }
  if(ev.kind==='run_failed'){state.failed=true;addEvent('fail','Run failed',d.error||'Unknown error',ev.at)}
  if(ev.kind==='run_finished')addEvent(d.scored>0&&d.passed===d.scored?'pass':'skip','Benchmark finished',`${d.passed}/${d.scored} scored scenarios passed`,ev.at);
}
function ring(value,label){
  const ns='http://www.w3.org/2000/svg',r=48,len=2*Math.PI*r,wrap=node('div',`summary-ring ${value>=.85?'':value>=.6?'mid':'low'}`),svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox','0 0 112 112');
  for(const cls of ['track','value']){const c=document.createElementNS(ns,'circle');c.setAttribute('class',cls);c.setAttribute('cx','56');c.setAttribute('cy','56');c.setAttribute('r',r);c.style.strokeDasharray=len;c.style.strokeDashoffset=cls==='value'?len:0;svg.append(c)}
  wrap.append(svg,node('b','',label));
  requestAnimationFrame(()=>requestAnimationFrame(()=>{svg.lastChild.style.strokeDashoffset=len*(1-Math.max(0,Math.min(1,value)))}));
  return wrap;
}
function renderSummary(){
  const r=state.report;if(!r)return;const box=$('summary');box.replaceChildren();box.classList.remove('hidden');box.append(ring(r.scored?r.accuracy:0,r.scored?`${Math.round(r.accuracy*100)}%`:'—'),node('h4','',`Result for ${r.model}`));
  const grid=node('div','summary-grid');const values=[['Passed',`${r.passed}/${r.scored}`],['Model calls',String(r.execution?.actual_model_calls??r.usage?.live_model_calls??'Unknown')],['Coverage',`${r.coverage.scored}/${r.coverage.selected}`],['Safety flags',String(r.safety?.violations??'Unknown')]];
  for(const [label,value] of values){const item=node('div');item.append(node('strong','',value),node('small','',label));grid.append(item)}box.append(grid);
  box.append(node('p','',`Scored ${r.scored} of ${r.coverage.selected}; skipped cases are excluded from accuracy. Safety flags cover only the independent checks listed by this benchmark.`));
}
function maybeFinish(){
  if(!state.networkDone||state.queue.length)return;
  clearInterval(state.pollTimer);clearInterval(state.playTimer);state.pollTimer=state.playTimer=null;
  setRunState(state.failed||!state.report?'Error':'Complete',state.failed||!state.report?'error':'done');
  if(state.report)renderSummary();
  state.runId=null;state.starting=false;renderChoices();
}
async function poll(){
  if(!state.runId||state.polling||state.networkDone)return;
  state.polling=true;
  try{const data=await api(`/ui/live/runs/${encodeURIComponent(state.runId)}?after=${state.cursor}`);state.cursor=data.next;state.queue.push(...data.events);if(data.done){state.networkDone=true;state.report=data.report;if(data.error){state.failed=true;if(!data.events.some(e=>e.kind==='run_failed'))state.queue.push({kind:'run_failed',data:{error:data.error}});}clearInterval(state.pollTimer);if(state.queue.length)setRunState('Showing trace','running');maybeFinish()}}
  catch(e){state.networkDone=true;clearInterval(state.pollTimer);state.queue.push({kind:'run_failed',at:new Date().toISOString(),data:{error:e.message}});maybeFinish()}
  finally{state.polling=false}
}
async function start(){
  if(state.runId||state.starting||!state.selected.size||(state.mode==='live'&&(!liveReady()||state.selected.size!==1)))return;
  state.starting=true;state.failed=false;renderChoices();
  $('activity').replaceChildren();$('summary').classList.add('hidden');$('empty-stage').classList.remove('hidden');state.queue=[];state.cursor=0;state.finished=0;state.total=state.selected.size;state.networkDone=false;state.polling=false;state.report=null;setProgress();$('progress-copy').textContent='Preparing isolated test worlds…';setRunState('Running','running');
  try{
    const response=await api('/ui/live/runs',{method:'POST',body:JSON.stringify({mode:state.mode,dataset:state.dataset,scenario_ids:[...state.selected],...(state.mode==='live'?{provider:$('provider-select').value,model:$('model-select').value}:{})})});
    state.runId=response.run_id;state.starting=false;renderCount();state.pollTimer=setInterval(poll,250);
    state.playTimer=setInterval(()=>{while(state.queue.length)renderEvent(state.queue.shift());maybeFinish()},100);
    await poll();
  }catch(e){setRunState('Error','error');$('progress-copy').textContent=e.message;addEvent('fail','Could not start',e.message);state.runId=null;state.starting=false;renderChoices()}
}
function renderDatasets(){
  const select=$('dataset-select');select.replaceChildren();
  const datasets=state.capabilities?.datasets||[{id:'synthetic',label:'Synthetic safety scenarios',configured:true}];
  for(const dataset of datasets){const option=node('option','',dataset.label+(dataset.configured?'':' · unavailable'));option.value=dataset.id;option.disabled=!dataset.configured;select.append(option)}
}
async function selectDataset(dataset,mode){
  if(state.runId||state.starting||state.loadingDataset)return;
  state.loadingDataset=true;state.scenarios=[];state.selected.clear();renderChoices();
  try{
    const data=await api(`/ui/live/scenarios?dataset=${encodeURIComponent(dataset)}`);
    state.dataset=dataset;$('dataset-select').value=dataset;state.scenarios=data.scenarios;
    $('dataset-scope').textContent=state.capabilities?.datasets?.find(d=>d.id===dataset)?.scope||(dataset==='synthetic'?'Synthetic supplier inputs; no Valio API capture.':'Official mock API capture; orders remain simulated.');
    selectMode(dataset==='official_snapshot'?'live':mode||'offline');
  }catch(e){$('dataset-scope').textContent=`Cannot load this dataset: ${e.message}`;state.scenarios=[];state.selected.clear();}
  finally{state.loadingDataset=false;renderChoices();renderPresetCounts();}
}
async function init(){
  const preset=new URLSearchParams(window.location.search);
  try{state.capabilities=await api('/ui/live/capabilities');renderProviders();renderDatasets();}
  catch(e){state.capabilityError=`Live configuration unavailable: ${e.message}`;renderProviders();renderDatasets();}
  let dataset=preset.get('dataset')||state.capabilities?.default_dataset||'synthetic';
  if(!preset.has('dataset')&&(preset.get('mode')==='offline'||preset.get('mode')==='recorded'||(preset.get('scenario')&&!preset.get('scenario').startsWith('real-'))))dataset='synthetic';
  if(![...$('dataset-select').options].some(o=>o.value===dataset&&!o.disabled))dataset='synthetic';
  await selectDataset(dataset,preset.get('mode'));
  const provider=preset.get('provider');if(state.capabilities?.providers?.some(p=>p.id===provider)){$('provider-select').value=provider;renderModels()}
  const scenario=preset.get('scenario');if(available().some(s=>s.id===scenario)){state.selected=new Set([scenario]);renderChoices()}
}
$('dataset-select').addEventListener('change',()=>selectDataset($('dataset-select').value));
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>selectMode(b.dataset.mode)));
document.querySelectorAll('[data-preset]').forEach(b=>b.addEventListener('click',()=>choosePreset(b.dataset.preset)));
$('provider-select').addEventListener('change',renderModels);$('model-select').addEventListener('change',renderCount);
$('scenario-search').addEventListener('input',renderChoices);$('run-button').addEventListener('click',start);
init();
