const state = {orders: [], choices: [], audit: {entries: [], chain_valid: false}, jwks: {}, selected: null, view: 'exchange', perspective: 'buyer', busy: false, setup_required: null};
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = value => new Intl.NumberFormat('en-FI', {style:'currency',currency:'EUR'}).format(Number(value || 0));
const date = value => value ? new Intl.DateTimeFormat('en-GB',{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)) : '—';
const short = value => value ? String(value).slice(0, 13) + '…' : '—';
const statusText = {placed:'Placed',pending_approval:'Needs approval',awaiting_cart_confirmation:'Confirm cart',proposal:'Proposal only',rejected:'Rejected',cart_declined:'Cart declined',expired:'Expired',awaiting_choice:'Choose an option'};
const api = async (path, options = {}) => {
  const response = await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  const data = await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  return data;
};
let toastTimer;
function toast(message, error=false){const el=$('toast');el.textContent=message;el.classList.toggle('error',error);el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),4500)}
function humanStatus(order){return `<span class="status ${escapeHtml(order.status)}">${escapeHtml(statusText[order.status] || order.status)}</span>`}
function validBadge(value){return value === true ? '<span class="verified">Verified</span>' : value === false ? '<span class="unverified">Invalid</span>' : '<span class="not-yet">No evidence yet</span>'}
function buyerName(id){return ({'hotel-chain-001': 'Hotel kitchen', 'school-espoo-001': 'School kitchen', 'hospital-espoo-001': 'Hospital kitchen'})[id] || id}
function quantitySummary(line){
  const offer=line.offer||line;
  const qty=Number(line.quantity), size=Number(offer.content?.per_sales_unit);
  const unit=offer.content?.unit;
  const base=`${line.quantity} ${offer.sales_unit||'sales units'} · ${offer.name||line.name||line.product_id||'Product'}`;
  if(Number.isFinite(qty)&&qty>0&&Number.isFinite(size)&&size>0&&unit){
    return `${base} — ${qty} × ${size} ${unit} = ${Number((qty*size).toFixed(6))} ${unit} total`;
  }
  return `${base} — pack contents not verified; check the sales unit`;
}
function orderName(order){const lines=order.data?.cart?.lines || order.data?.lines || [];const first=lines[0];return first?.name || first?.offer?.name || `Order ${order.order_id}`}

async function refresh(silent=false){
  try{
    const data=await api('/ui/overview');Object.assign(state,data);
    if(!state.selected || !state.orders.some(o=>o.order_id===state.selected)) state.selected=state.orders[0]?.order_id || null;
    const setupNeeded=Boolean(state.setup_required);
    $('setup-banner').classList.toggle('hidden',!setupNeeded);
    $('run-button').disabled=setupNeeded;
    $('gateway-status-text').textContent=setupNeeded?'Catalogue setup needed':'Gateway connected';
    $('gateway-status').classList.toggle('needs-setup',setupNeeded);
    $('updated').textContent=setupNeeded?'Catalogue setup needed':`Updated ${new Intl.DateTimeFormat('en-GB',{timeStyle:'short'}).format(new Date())}`;
    let clockNote=$('demo-clock-note');if(!clockNote){clockNote=document.createElement('p');clockNote.id='demo-clock-note';clockNote.className='menu-help';$('updated').closest('header').insertAdjacentElement('afterend',clockNote)}
    clockNote.hidden=!data.demo_clock?.frozen;clockNote.textContent=data.demo_clock?.frozen?`Demo business clock: ${date(data.demo_clock.business_time)} (frozen for Valio mock delivery cutoffs). Order and audit timestamps use this clock; the refresh time above is your actual local time.`:'';
    $('metric-orders').textContent=state.orders.length;$('exchange-count').textContent=state.orders.length;
    const pending=state.orders.filter(o=>['awaiting_cart_confirmation','pending_approval'].includes(o.status)).length + state.choices.filter(c=>c.status==='awaiting_choice').length;
    $('metric-pending').textContent=pending;$('inbox-count').textContent=pending;$('inbox-label').textContent=`${pending} open`;
    const verified=state.audit.chain_valid && state.orders.every(o=>o.verification.mandate === true && o.verification.offers.every(x=>x===true) && o.verification.receipt !== false);
    $('metric-trust').textContent=setupNeeded?'Setup needed':!state.orders.length?'Ready':verified?'Verified':'Review needed';$('metric-trust-note').textContent=setupNeeded?'Install the catalogue to use Exchange':!state.orders.length?'Audit chain intact, no orders yet':verified?'Signed records and audit chain intact':'One or more checks need attention';
    $('chain-status').textContent=setupNeeded?'Catalogue not installed':state.audit.chain_valid?'Chain verified':`Chain broken${state.audit.first_broken_seq?' at #'+state.audit.first_broken_seq:''}`;
    $('key-id').textContent=state.jwks.keys?.[0]?.kid ? `kid: ${state.jwks.keys[0].kid}` : 'No public key available';
    render();
  }catch(e){if(!silent)toast(`Gateway unavailable: ${e.message}`,true);$('updated').textContent='Gateway unavailable';$('metric-trust').textContent='Offline'}
}
function render(){renderOrders();renderDetail();renderInbox();renderAudit()}
function renderOrders(){
  $('order-list').innerHTML='<div class="list-heading">Recent exchanges</div>'+ (state.orders.length ? state.orders.map(o=>`<button class="order-item ${state.selected===o.order_id?'selected':''}" data-order="${escapeHtml(o.order_id)}"><div class="order-item-top"><strong>${escapeHtml(buyerName(o.buyer_id))}</strong>${humanStatus(o)}</div><small>${escapeHtml(orderName(o))}</small><div class="order-item-bottom"><span>${escapeHtml(date(o.data?.received_at_wall||o.created_at))}</span><b>${money(o.total_eur)}</b></div></button>`).join('') : state.setup_required?'<div class="empty"><span class="empty-icon">↔</span>No exchanges yet.<br>Install the catalogue to start one.</div>':'<div class="empty"><span class="empty-icon">↔</span>No exchanges yet.<br>Ask the agent to buy something above.</div>');
}
function renderDetail(){
  const o=state.orders.find(x=>x.order_id===state.selected);
  if(!o){$('order-detail').innerHTML='<div class="empty"><span class="empty-icon">◇</span>Select an exchange to see its offers,<br>policy decision and receipt.</div>';return}
  const d=o.data||{},v=o.verification||{},lines=d.cart?.lines||d.lines||[];
  const need=d.cart?.rationale||d.rationale||'The agent submitted this order under the buyer mandate.';
  const reasons=d.decision?.reasons||[];
  const placed=o.status==='placed';
  const decisionMessage=placed ? (d.approvals?.length ? 'Human approval received. The demo order is committed and its signed receipt is available. Original exception: '+reasons.join('; ') : 'The demo order is committed and its signed receipt is available.') : reasons.join('; ')||d.autonomy?.reason||'Review the current order status.';
  const flow=[['1','Buyer mandate',false],['2','Signed offer',false],['3','Policy check',false],['4',o.status==='placed'?'Receipt':'Human step',o.status!=='placed']];
  const steps=flow.map((s,i)=>`${i?'<div class="flow-line"></div>':''}<div class="flow-step ${s[2]?'pending':''}"><span class="step-circle">${s[2]?'!':'✓'}</span><strong>${escapeHtml(s[1])}</strong></div>`).join('');
  const actors=state.perspective==='buyer' ? ['Buyer agent','Valio Aimo'] : ['Valio Aimo','Buyer agent'];
  const policyPanel=state.perspective==='valio' ? `<div class="decision-box"><strong>Initial policy review · ${escapeHtml(d.decision?.outcome||'pending')}</strong>${o.status==='placed'&&d.approvals?.length?'Human approval received; demo order committed.<br>':''}${(d.decision?.checks||[]).map(c=>`${escapeHtml(c.rule)}: ${escapeHtml(c.outcome)} · ${escapeHtml(c.detail)}`).join('<br>')||'No policy checks recorded.'}</div>` : '';
  $('order-detail').innerHTML=`<div class="detail-header"><div><div class="detail-kicker">${escapeHtml(actors[0])} ↔ ${escapeHtml(actors[1])}</div><h3>${escapeHtml(orderName(o))}</h3><p>${escapeHtml(o.order_number||o.order_id)}, ${escapeHtml(date(o.data?.received_at_wall||o.created_at))}${o.data?.received_at_wall?' · actual received time':' · business-clock time (actual time not recorded)'}</p></div>${humanStatus(o)}</div><div class="flow">${steps}</div><div class="detail-columns"><div class="detail-card"><h4>Order & offer</h4>${lines.map((line,i)=>{const item=line.offer||line;const source=(d.lines||[]).find(x=>x.offer_id===line.offer_id);const notes=item.delivery?.notes||source?.offer?.delivery?.notes||[];return `<div class="line-item"><span>${escapeHtml(line.quantity)} ${escapeHtml(item.sales_unit||'units')} · ${escapeHtml(item.name||line.name||line.product_id)}${line.substitutes_for?` <small>· replaces ${escapeHtml(line.substitutes_for)}</small>`:''}${line.fills_shortfall_of?` <small>· tops up ${escapeHtml(line.fills_shortfall_of)}</small>`:''}${line.needs_kitchen_check?' <small><b>· check with the kitchen</b></small>':''}${notes.map(n=>`<small> · ${escapeHtml(n)}</small>`).join('')}</span><b>${money(line.line_total_eur ?? Number(line.quantity)*Number(item.unit_price||0))}</b></div>`}).join('')}<div class="line-item"><strong>Total excl. VAT</strong><b>${money(o.total_eur)}</b></div><p><b>Original agent proposal:</b> ${escapeHtml(need)}</p>${o.buyer_model?.label?`<p class="buyer-model-proposed"><b>Buyer model:</b> ${escapeHtml(o.buyer_model.label)}${o.buyer_model.model&&o.buyer_model.model!=='offline'?` <small>(${escapeHtml(o.buyer_model.model)})</small>`:''}</p>`:''}</div><div class="detail-card"><h4>Cryptographic checks</h4><div class="signature-row"><span>Buyer mandate</span>${validBadge(v.mandate)}</div><div class="signature-row"><span>Gateway-signed offers (${v.offers?.length||0})</span>${validBadge((v.offers||[]).length ? (v.offers.includes(false) ? false : v.offers.includes(null) ? null : true) : null)}</div><div class="signature-row"><span>Gateway-signed demo receipt</span>${validBadge(v.receipt)}</div><div class="signature-row"><span>Audit chain</span>${validBadge(state.audit.chain_valid)}</div></div></div><div class="decision-box ${o.status!=='placed'?'warn':''}"><strong>${escapeHtml(o.status==='placed'?'Demo order committed':d.decision?.outcome==='needs_approval'?'Human approval required':o.status==='awaiting_cart_confirmation'?'Buyer confirmation required':o.status==='proposal'?'Recommendation only':'Policy decision')}</strong>${escapeHtml(decisionMessage)}</div>${d.autonomy&&d.autonomy.effective!==d.autonomy.requested?`<div class="decision-box ${o.status==='awaiting_cart_confirmation'?'warn':''}"><strong>${o.status==='awaiting_cart_confirmation'?'Cart confirmation required':d.cart_confirmation?'Cart confirmation completed':'Autonomy limit applied'} · ${escapeHtml(d.autonomy.requested)} → ${escapeHtml(d.autonomy.effective)}</strong>${escapeHtml(d.autonomy.reason||'')}${d.cart_confirmation?' — The signed cart confirmation has already been received. No further cart confirmation is requested.':''}${o.status==='pending_approval'?' A separate policy exception still needs approval.':''}</div>`:''}${policyPanel}<div class="detail-footer"><button class="text-button" data-view="audit" data-subject="${escapeHtml(o.order_id)}">View audit trail</button><span class="hash">${escapeHtml(o.data?.receipt?.claims?.audit_head?.hash ? 'SHA-256 '+short(o.data.receipt.claims.audit_head.hash) : 'Awaiting receipt')}</span></div>`;
}
function renderChoice(choice){
  const options=choice.options||[];
  const cheapest=Math.min(...options.map(o=>Number(o.total_eur)));
  const approver=(choice.approvers||choice.mandate?.approvers||[])[0]||'';
  const optionCards=options.map((option,index)=>{
    const delta=Number(option.total_eur)-cheapest;
    const premium=cheapest>0?delta/cheapest*100:null;
    const lines=(option.lines||[]).map(line=>{
      const offer=line.offer||line;
      const reliability=offer.delivery_reliability;
      const delivery=offer.requested_date||line.requested_date||choice.requested_date;
      return `<li><b>${escapeHtml(offer.name||line.name||offer.product_id)}</b><br>${escapeHtml(line.quantity)} ${escapeHtml(offer.sales_unit||'units')} · Delivery: ${escapeHtml(delivery||'no data')}<br>Delivery reliability: ${reliability==null?'no data':escapeHtml((Number(reliability)*100).toFixed(1))+'%'} (catalogue estimate)</li>`;
    }).join('');
    const explanation=option.explanation||option.trade_off||(index===0?'Lower price; review the delivery reliability risk.':'Higher reliability; review the extra cost.');
    const reasons=(option.approval_reasons||[]).map(reason=>`<p class="choice-reason">${escapeHtml(reason)}</p>`).join('');
    return `<section class="choice-option"><h4>${index===0?'Lower price':'Higher reliability'}</h4><strong class="choice-price">${money(option.total_eur)}</strong><small>excl. VAT${delta>0?' · +'+money(delta)+(premium==null?'':' / +'+premium.toFixed(1)+'%'):' · price baseline'}</small><ul>${lines}</ul><p>${escapeHtml(explanation)}</p>${reasons}<button class="primary-button" data-choice="${escapeHtml(choice.choice_id)}" data-selection="${index}" data-approver="${escapeHtml(approver)}">Choose ${money(option.total_eur)}</button></section>`;
  }).join('');
  return `<article class="inbox-card choice-card"><div class="inbox-head"><div><div class="section-kicker">HUMAN CHOICE · ${escapeHtml(buyerName(choice.buyer_id))}</div><h3>Price or delivery reliability?</h3><p>${escapeHtml(choice.choice_id)} · Offers valid until ${escapeHtml(date(choice.expires_at))}</p></div><span class="status pending_approval">Decision required</span></div><div class="decision-box warn"><strong>No order has been placed</strong>${escapeHtml(choice.explanation||'Compare both baskets and explicitly choose the trade-off.')} ${choice.premium_limit_pct==null?'':`Price premium preference: ${escapeHtml(choice.premium_limit_pct)}%. `} Hard safety rules still apply; the selected offer is checked again before ordering.</div><div class="choice-options">${optionCards}</div><div class="inbox-actions"><input aria-label="Choice decision note" placeholder="Optional reason for your choice" data-note="${escapeHtml(choice.choice_id)}"><button class="danger-button" data-choice="${escapeHtml(choice.choice_id)}" data-selection="reject" data-approver="${escapeHtml(approver)}">Reject both</button></div></article>`;
}
function renderInbox(){
  const pending=state.orders.filter(o=>['awaiting_cart_confirmation','pending_approval'].includes(o.status));
  const cards=pending.map(o=>{const cart=o.status==='awaiting_cart_confirmation';const reasons=o.data?.decision?.reasons||[];const approver=o.data?.mandate?.approvers?.[0]||'';return `<article class="inbox-card"><div class="inbox-head"><div><div class="section-kicker">${cart?'Cart confirmation':'Policy exception'}, ${escapeHtml(buyerName(o.buyer_id)).toLowerCase()}</div><h3>${escapeHtml(orderName(o))}</h3><p>${escapeHtml(date(o.data?.received_at_wall||o.created_at))}. Decide by ${escapeHtml(date(o.hold_until))}</p></div>${humanStatus(o)}</div><div class="decision-box warn"><strong>${cart?'Confirm the exact basket':'Review the exception'}</strong>${escapeHtml(cart?(o.data.cart?.rationale||'Confirm the cart that the agent filled. A separate approval may still be needed.'):reasons.join('; ')||'Policy requires a human decision.')}<br><b>${money(o.total_eur)} excl. VAT</b><ul class="quantity-review">${(o.data?.lines||o.data?.cart?.lines||[]).map(line=>`<li>${escapeHtml(quantitySummary(line))}</li>`).join('')}</ul><small>Confirm quantities and sales units against the kitchen need. Missing ingredients cannot be detected without a recipe or expected list.</small></div><div class="inbox-actions"><input aria-label="Decision note" placeholder="Optional note" data-note="${escapeHtml(o.order_id)}"><button class="${cart?'primary-button':'primary-button'}" data-action="${cart?'confirm':'approve'}" data-id="${escapeHtml(o.order_id)}" data-approver="${escapeHtml(approver)}">${cart?'Confirm signed cart':'Approve order'}</button><button class="danger-button" data-action="${cart?'decline':'reject'}" data-id="${escapeHtml(o.order_id)}" data-approver="${escapeHtml(approver)}">${cart?'Decline':'Reject'}</button></div></article>`}).join('');
  const choices=state.choices.filter(c=>c.status==='awaiting_choice').map(renderChoice).join('');
  $('inbox-list').innerHTML=choices+cards||'<div class="empty inbox-card"><span class="empty-icon">✓</span>Nothing needs a decision right now.</div>';
}
function renderAudit(){
  const entries=state.audit.entries||[];const orderIds=new Set(state.orders.map(o=>o.order_id));
  let filtered=state.perspective==='buyer'?entries.filter(e=>orderIds.has(e.subject)||(['agent.note','need.planned'].includes(e.event)||e.event.startsWith('choice.'))):entries;
  if(state.auditSubject) filtered=filtered.filter(e=>e.subject===state.auditSubject);
  $('audit-list').innerHTML=filtered.length?filtered.slice().reverse().map(e=>`<div class="audit-entry"><span class="seq">#${escapeHtml(e.seq)}</span><div><b>${escapeHtml(e.event)}</b><p>${escapeHtml(date(e.ts))}</p></div><div><b>${escapeHtml(e.actor)}</b><p>${escapeHtml(e.summary)}</p></div><code title="${escapeHtml(e.hash)}">${escapeHtml(short(e.hash))}</code></div>`).join(''):'<div class="empty">No audit events yet.</div>';
}
function switchView(view){if(!['exchange','inbox','audit','trust'].includes(view))return;state.view=view;document.querySelectorAll('.view').forEach(el=>el.classList.toggle('hidden',el.id!==`view-${view}`));document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===view));const titles={exchange:['Trade exchange','Every decision between your purchasing agent and Valio Aimo, in one place.'],inbox:['Approvals','Confirm the exact cart or decide on a policy exception.'],audit:['Audit trail','What each party did, in order, with a verifiable hash chain.'],trust:['Trust protocol','Signed documents that agents and people can verify.']};$('page-title').textContent=titles[view][0];$('page-subtitle').textContent=titles[view][1];$('breadcrumb').textContent={exchange:'Exchange',inbox:'Approvals',audit:'Audit trail',trust:'Trust protocol'}[view];}
async function decision(action,id,approver){
  if(state.busy)return;state.busy=true;const note=document.querySelector(`[data-note="${CSS.escape(id)}"]`)?.value||'';
  try{
    if(['confirm','decline'].includes(action)){
      const signed=await api('/api/demo/wallet/cart-confirmations',{method:'POST',body:JSON.stringify({order_id:id,confirmed_by:approver,decision:action,note})});
      await api(`/api/v1/carts/${encodeURIComponent(id)}/${action}`,{method:'POST',body:JSON.stringify({confirmation:signed.confirmation})});
    }else await api(`/api/v1/approvals/${encodeURIComponent(id)}/${action}`,{method:'POST',body:JSON.stringify({approver,note})});
    state.selected=id;
    await refresh(true);
    const updated=state.orders.find(order=>order.order_id===id);
    if(updated?.status==='placed'){
      switchView('exchange');render();
      toast('Demo order placed. Receipt and verification are shown below.');
    }else if(updated?.status==='pending_approval'){
      toast('Cart confirmed. A policy exception still needs approval.');
    }else{
      switchView('exchange');render();
      toast(statusText[updated?.status]||'Decision recorded. Review the order status.');
    }
  }catch(e){toast(e.message,true)}finally{state.busy=false}
}
async function decideChoice(id,selection,approver){
  if(state.busy)return;state.busy=true;
  const note=document.querySelector(`[data-note="${CSS.escape(id)}"]`)?.value||'';
  try{
    const result=await api(`/ui/choices/${encodeURIComponent(id)}/decision`,{method:'POST',body:JSON.stringify({approver,selection:selection==='reject'?null:Number(selection),note})});
    if(result.order?.order_id)state.selected=result.order.order_id;
    toast(selection==='reject'?'Both options declined':'Choice recorded; '+(statusText[result.order?.status]||result.status));
    await refresh(true);
  }catch(e){toast(e.message,true)}finally{state.busy=false}
}
document.addEventListener('click',e=>{
  const nav=e.target.closest('[data-view]');if(nav){if(nav.dataset.view==='audit')state.auditSubject=nav.dataset.subject||null;switchView(nav.dataset.view);renderAudit();return}
  const order=e.target.closest('[data-order]');if(order){state.selected=order.dataset.order;renderOrders();renderDetail();return}
  const perspective=e.target.closest('[data-perspective]');if(perspective){state.perspective=perspective.dataset.perspective;document.querySelectorAll('[data-perspective]').forEach(x=>x.classList.toggle('selected',x===perspective));renderDetail();renderAudit();return}
  const example=e.target.closest('[data-example]');if(example){$('request-input').value=example.dataset.example;$('buyer-select').value=example.dataset.buyer;return}
  const choice=e.target.closest('[data-choice]');if(choice){decideChoice(choice.dataset.choice,choice.dataset.selection,choice.dataset.approver);return}
  const action=e.target.closest('[data-action]');if(action)decision(action.dataset.action,action.dataset.id,action.dataset.approver);
});
$('refresh').addEventListener('click',()=>refresh());
document.addEventListener('menu-basket-created',()=>refresh(true));
$('agent-form').addEventListener('submit',async e=>{e.preventDefault();if(state.busy)return;state.busy=true;const button=$('run-button');button.disabled=true;button.classList.add('loading');button.textContent='Agent working…';try{const result=await api('/ui/agent/run',{method:'POST',body:JSON.stringify({request:$('request-input').value,buyer:$('buyer-select').value,mode:$('mode-select').value,...($('buyer-model-select')?.value?{provider:$('buyer-model-select').value}:{})})});if(result.order_id)state.selected=result.order_id;await refresh(true);if(result.choice_id)switchView('inbox');showBuyerModel(result.buyer_model);toast(result.order_id?`Agent finished: ${statusText[result.status]||result.status}`:result.explanation||'Agent finished without an order',!!result.error)}catch(err){toast(err.message,true)}finally{state.busy=false;button.disabled=false;button.classList.remove('loading');button.textContent='Run agent'}});
refresh();
// Optional buyer model for product requests. Only server-configured live models are listed;
// the empty value keeps the server default, so nothing changes when no choice is made.
async function loadBuyerModels(){
  const select=$('buyer-model-select');if(!select)return;
  try{
    const data=await api('/ui/agent/models');
    const options=[{id:'',label:data.default?.label||'Server default model'},...(data.options||[])];
    select.replaceChildren(...options.map(o=>{const option=document.createElement('option');option.value=o.id;option.textContent=o.id?o.label:`${o.label} (default)`;return option}));
    select.disabled=options.length<2;select.title=options.length<2?'No other live buyer models are configured on this server.':'';
  }catch{select.disabled=true}
}
function showBuyerModel(model){const el=$('buyer-model-used');if(!el)return;el.textContent=model?.label?`Buyer model: ${model.label}`:'';el.classList.toggle('hidden',!model?.label)}
loadBuyerModels();
