// Read-only evidence view. Judging weights are not scores awarded to this demo.
const proof=document.getElementById('demo-proof');
let proofData=null,proofBusy=false;
function proofNode(tag,text,className){const n=document.createElement(tag);n.textContent=text;if(className)n.className=className;return n}
function proofRender(){
 const selected=document.querySelector('.order-item.selected')?.dataset.order;
 const order=proofData?.orders?.find(o=>o.order_id===selected)||proofData?.orders?.[0];
 const v=order?.verification||{},d=order?.data||{};
 const offers=Array.isArray(v.offers)&&v.offers.length>0&&v.offers.every(x=>x===true);
 const humanDone=Boolean(d.cart_confirmation||d.approval);
 const waiting=['awaiting_cart_confirmation','pending_approval'].includes(order?.status);
 const milestones=[['Request → offer',!order?'Not run':offers?'Signed offers verified':'Offer evidence needs review',offers],['Human decision',!order?'Not run':waiting?'Needs your review':humanDone?'Decision recorded':'No human decision evidenced',humanDone],['Demo receipt',!order?'Not run':order.status==='placed'&&v.receipt===true?'Signed demo receipt verified':order.status==='placed'?'Receipt needs review':'Not placed',order?.status==='placed'&&v.receipt===true]];
 const strip=document.getElementById('proof-milestones');strip.replaceChildren();for(const [title,status,ok] of milestones){const item=proofNode('div','',ok?'proof-step evidence':'proof-step');item.append(proofNode('span',title),proofNode('strong',status));strip.append(item)}
 document.getElementById('proof-context').textContent=order?`Evidence for ${order.order_id} · ${order.status}. Simulated order; no real delivery booked.`:'No order evidence yet. Choose a request preset and run the agent.';
 document.getElementById('proof-e2e').textContent=order?.status==='placed'&&v.receipt===true?'Demo receipt available; inspect offers, decisions and audit below.':waiting?'The workflow reached a human review step; no receipt yet.':'End-to-end evidence is not complete for this order.';
 document.getElementById('proof-guardrails').textContent=!order?'No order checks yet.':v.mandate===true&&offers?'Mandate and offer signatures verified. Inspect the policy decision; this does not prove prevention of every input error.':'Check evidence is incomplete or needs attention. Inspect signatures and the policy decision.';
}
async function proofRefresh(){if(proofBusy||document.hidden||document.getElementById('view-exchange')?.classList.contains('hidden'))return;proofBusy=true;try{const r=await fetch('/ui/overview');if(!r.ok)throw new Error('unavailable');proofData=await r.json();proofRender()}catch{document.getElementById('proof-context').textContent='Evidence unavailable. Existing display may be stale; no successful outcome is inferred.'}finally{proofBusy=false}}
if(proof){proofRefresh();setInterval(proofRefresh,10000);document.addEventListener('menu-basket-created',proofRefresh);document.getElementById('refresh')?.addEventListener('click',proofRefresh);document.getElementById('order-list')?.addEventListener('click',()=>setTimeout(proofRender,0));document.addEventListener('visibilitychange',proofRefresh)}
