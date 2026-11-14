// Menu extraction creates an editable draft; all reviewed needs request one combined basket.
const menuEl=id=>document.getElementById(id);
const menuEscape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const MAX_MENU_IMAGE=5*1024*1024;
let menuDraft=null;
let menuBusy=false;
const menuRequest=async(path,payload)=>{
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status})`);
  return data;
};
function menuMessage(message,error=false){const el=menuEl('menu-message');el.textContent=message;el.classList.toggle('menu-error',error)}
let presetLoading=false;
let imagePreviewUrl=null;
let menuBasketAttempt=null;
function updateImagePreview(){
  if(imagePreviewUrl)URL.revokeObjectURL(imagePreviewUrl);
  const file=menuEl('menu-image').files[0];menuEl('menu-image-preview').classList.toggle('hidden',!file);
  if(file){imagePreviewUrl=URL.createObjectURL(file);menuEl('menu-image-thumbnail').src=imagePreviewUrl;menuEl('menu-image-name').textContent=file.name;}
}
function setInputFile(id,file){const transfer=new DataTransfer();if(file)transfer.items.add(file);menuEl(id).files=transfer.files;}
function updatePreset(mode){
  menuEl('request-kind').value=mode;
  menuEl('mode-select').value=mode==='product'?'autonomous':'confirm_cart';menuEl('mode-select').disabled=mode!=='product';
  menuEl('menu-mode-help').textContent=mode==='product'?'A product request can proceed under the signed mandate. Adding a photo or documents routes the request through a reviewed draft first.':'This creates an editable ingredient draft, not an order. Supply quantities and dates; incomplete notes need clarification. Use portions = 1 when service counts or final quantities are already supplied. Each reviewed basket still requires confirmation.';
  document.querySelectorAll('[data-menu-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.menuMode===mode)));
}
async function selectMenuInput(mode){
  if(menuBusy||presetLoading)return;
  presetLoading=true;menuEl('menu-draft-button').disabled=true;
  try{
    updatePreset(mode);menuEl('buyer-select').value='restaurant';menuEl('menu-guests').value='1';
    menuDraft=null;menuEl('menu-preview').classList.add('hidden');
    for(const id of ['menu-document','recipe-document','stock-document','menu-image'])setInputFile(id,null);
    if(mode==='product'){menuEl('menu-text').value='200 litres of lactose-free milk for Monday.';menuMessage('Product example loaded. Edit the request, then Run agent.');}
    else{
      menuEl('menu-text').value='';
      const response=await fetch(mode==='handwritten'?'/ui/menu/handwritten-example':'/ui/menu/example');const example=await response.json();
      if(!response.ok)throw new Error(typeof example.detail==='string'?example.detail:'The supplied example is unavailable. You can enter your own request and attachments.');
      menuEl('menu-text').value=example.text||'';menuEl('menu-guests').value=String(example.guests||1);menuEl('buyer-select').value=example.buyer||'restaurant';
      for(const doc of example.documents||[]){const id={menu:'menu-document',recipe:'recipe-document',stock:'stock-document'}[doc.kind];if(id)setInputFile(id,new File([doc.content],doc.name,{type:'application/json'}));}
      if(example.image_url){const image=await fetch(example.image_url);if(!image.ok)throw new Error('Synthetic image could not be loaded. Attach a JPEG or PNG manually.');const blob=await image.blob();setInputFile('menu-image',new File([blob],example.image_name||'synthetic-kitchen-note.png',{type:blob.type||'image/png'}));}
      menuMessage('Synthetic example loaded. Review the visible request and attachments, then Run agent. No model call or order yet.');
    }
  }catch(error){menuMessage(error.message,true)}finally{updateImagePreview();presetLoading=false;menuEl('menu-draft-button').disabled=mode==='product'&&menuEl('run-button').disabled;saveRequestState();}
}
menuEl('request-kind').addEventListener('change',()=>selectMenuInput(menuEl('request-kind').value));
document.querySelectorAll('[data-menu-mode]').forEach(button=>button.addEventListener('click',()=>selectMenuInput(button.dataset.menuMode)));
menuEl('menu-image').addEventListener('change',updateImagePreview);
new MutationObserver(()=>{if(menuEl('request-kind').value==='product'&&!menuBusy&&!presetLoading){menuEl('menu-draft-button').disabled=menuEl('run-button').disabled;menuEl('menu-draft-button').textContent=menuEl('run-button').disabled?'Agent working…':'Run agent';}}).observe(menuEl('run-button'),{attributes:true,attributeFilter:['disabled']});
async function menuInputText(){
  const sections=[];const request=menuEl('menu-text').value.trim();if(request)sections.push(request);
  {
    for(const [id,label] of [['menu-document','MENU / SERVICE COUNTS'],['recipe-document','RECIPE CARDS'],['stock-document','STOCK / RESERVATIONS / INCOMING RECEIPTS']]){
      const file=menuEl(id).files[0];if(!file)continue;
      if(!/\.(txt|md|json)$/i.test(file.name))throw new Error(`${label}: attach a .txt, .md or .json file. PDFs are not supported.`);
      if(file.size>100*1024)throw new Error(`${label}: file exceeds 100 KB.`);
      const content=await file.text();if(!content.trim())throw new Error(`${label}: file is empty.`);
      sections.push(`SUPPLIED ${label} — INPUT DATA\n${content}\nEND SUPPLIED ${label}`);
    }
  }
  const text=sections.join('\n\n');
  if(text.length>12000)throw new Error(`Combined request and documents contain ${text.length.toLocaleString('en-GB')} characters. Maximum is 12,000; shorten the files or request. Nothing was sent.`);
  return text;
}
function menuImage(file){
  return new Promise((resolve,reject)=>{
    if(!file){resolve(null);return}
    if(!['image/jpeg','image/png'].includes(file.type)||file.size>MAX_MENU_IMAGE){reject(new Error('Attach a JPEG or PNG image, at most 5 MB.'));return}
    const reader=new FileReader();
    reader.onerror=()=>reject(new Error('Could not read the image.'));
    reader.onload=()=>resolve({image_base64:String(reader.result).split(',')[1],mime:file.type});
    reader.readAsDataURL(file);
  });
}
function renderMenuDraft(draft){
  const assumptions=(draft.assumptions||[]).map(x=>`<li>${menuEscape(x)}</li>`).join('');
  const warnings=(draft.warnings||[]).map(x=>`<li>${menuEscape(x)}</li>`).join('');
  const rows=(draft.needs||[]).map((need,index)=>`<tr data-menu-row="${index}"><td><input aria-label="Ingredient ${index+1}" data-menu-field="ingredient" type="text" maxlength="200" value="${menuEscape(need.ingredient)}" required><p>${menuEscape((need.for_dishes||[]).join(', '))}</p></td><td><input aria-label="Amount ${index+1}" data-menu-field="amount" type="number" min="0.001" max="1000000" step="any" value="${menuEscape(need.amount)}" required><select aria-label="Unit ${index+1}" data-menu-field="unit">${['KG','L','ST'].map(unit=>`<option${need.unit===unit?' selected':''}>${unit}</option>`).join('')}</select></td><td><input aria-label="Delivery date ${index+1}" data-menu-field="date" type="date" value="${menuEscape(need.date)}" required></td><td><input aria-label="Dietary requirements ${index+1}" data-menu-field="constraints" type="text" value="${menuEscape((need.constraints||[]).join(', '))}"><p>Signed kitchen restrictions always apply.</p></td><td><p class="menu-row-result" role="status"></p></td></tr>`).join('');
  const target=menuEl('menu-preview');
  target.classList.remove('hidden');
  target.innerHTML=`<h3>Chef review: ingredient draft</h3><p><b>Estimated quantities for ${menuEscape(draft.guests)} portions — no orders created.</b> Check the recipe, yields, stock already on hand and dates. This is not a demand forecast or a completed shopping basket.</p>${assumptions?`<h4>Assumptions to verify</h4><ul>${assumptions}</ul>`:''}${warnings?`<h4>Warnings / missing information</h4><ul>${warnings}</ul>`:''}<p>Review every row, then request <b>one complete basket</b>. If any ingredient cannot be resolved, no partial order is created. You will still confirm the complete products and price; policy exceptions can need a further approval.</p><label class="menu-review-check menu-review-all"><input type="checkbox" id="menu-reviewed">I reviewed all ingredients, quantities, dates and requirements</label><p class="menu-review-note">Changing any ingredient clears this review. The complete basket still needs product and price confirmation.</p><div class="menu-table-wrap"><table><thead><tr><th>Ingredient</th><th>Amount / unit</th><th>Delivery</th><th>Requirements</th><th>Resolution</th></tr></thead><tbody>${rows}</tbody></table></div><div class="menu-basket-action"><button class="primary-button" type="button" id="menu-basket-button" disabled>Request complete basket</button><p id="menu-basket-result" role="status" aria-live="polite"></p></div>`;
}
menuEl('menu-form').addEventListener('submit',async event=>{
  event.preventDefault();if(menuBusy||presetLoading)return;
  const form=event.currentTarget;if(!form.reportValidity())return;
  const hasAttachments=['menu-image','menu-document','recipe-document','stock-document'].some(id=>menuEl(id).files.length);
  if(menuEl('request-kind').value==='product'&&!hasAttachments){if(menuEl('run-button').disabled)return;menuEl('request-input').value=menuEl('menu-text').value;menuEl('agent-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));return;}
  const guests=Number(menuEl('menu-guests').value);
  if(!Number.isInteger(guests)||guests<1||guests>10000){menuMessage('Enter a whole number of portions from 1 to 10000.',true);return}
  menuBusy=true;menuEl('menu-draft-button').disabled=true;menuMessage('Drafting ingredients. No orders are being created.');
  try{
    const image=await menuImage(menuEl('menu-image').files[0]);
    const text=await menuInputText();
    if(!text&&!image)throw new Error('Describe a dish/menu or attach an image.');
    const buyer=menuEl('buyer-select').value;
    const draft=await menuRequest('/ui/menu/draft',{text,guests,buyer,...(image||{})});
    if(draft.status==='needs_clarification'){menuDraft=null;const target=menuEl('menu-preview');target.classList.remove('hidden');target.innerHTML='<h3>More information needed</h3><p>No order was created. Add the missing facts to the request and run again.</p>';for(const question of draft.questions||draft.warnings||[]){const p=document.createElement('p');p.textContent=typeof question==='string'?question:JSON.stringify(question);target.append(p)}menuMessage('Clarification required. No purchasable ingredient draft was created.');return;}
    menuDraft={...draft,buyer};renderMenuDraft(menuDraft);
    menuMessage(`Draft ready: ${(draft.needs||[]).length} ingredients. Review the complete ingredient draft before requesting any basket.`);
  }catch(error){menuMessage(error.message,true)}finally{menuBusy=false;menuEl('menu-draft-button').disabled=false}
});
function updateMenuReviewButtons(){
  const reviewed=Boolean(menuEl('menu-reviewed')?.checked);
  menuEl('menu-preview').querySelectorAll('#menu-basket-button').forEach(button=>{
    button.disabled=button.dataset.submitted==='true'||!reviewed||menuBusy;
  });
}
function invalidateMenuReview(event){
  const review=menuEl('menu-reviewed');if(!review)return;
  if(event.target!==review){
    if(!event.target.matches('[data-menu-field]'))return;
    review.checked=false;
    const button=menuEl('menu-basket-button');if(button){delete button.dataset.submitted;button.textContent='Request complete basket';}
  }
  updateMenuReviewButtons();
}
menuEl('menu-preview').addEventListener('change',invalidateMenuReview);
menuEl('menu-preview').addEventListener('input',invalidateMenuReview);
menuEl('menu-preview').addEventListener('click',async event=>{
  const button=event.target.closest('#menu-basket-button');if(!button||button.disabled||menuBusy||!menuDraft)return;
  if(!menuEl('menu-reviewed')?.checked)return;
  const rows=[...menuEl('menu-preview').querySelectorAll('[data-menu-row]')];
  const needs=[];
  for(const row of rows){
    const fields={};const inputs=[...row.querySelectorAll('[data-menu-field]')];
    if(inputs.some(input=>!input.reportValidity()))return;
    inputs.forEach(input=>{fields[input.dataset.menuField]=input.value});
    const amount=Number(fields.amount);if(!Number.isFinite(amount)||amount<=0)return;
    needs.push({ingredient:fields.ingredient.trim(),search_hint_fi:fields.ingredient.trim()===menuDraft.needs[Number(row.dataset.menuRow)].ingredient?(menuDraft.needs[Number(row.dataset.menuRow)].search_hint_fi||fields.ingredient.trim()):fields.ingredient.trim(),amount,unit:fields.unit,date:fields.date,constraints:fields.constraints.split(',').map(x=>x.trim()).filter(Boolean)});
    row.querySelector('.menu-row-result').textContent='';
  }
  const output=menuEl('menu-basket-result');menuBusy=true;updateMenuReviewButtons();
  output.classList.remove('menu-error');output.textContent='Resolving every ingredient into one complete basket…';
  // Freeze the entire review while its single request is pending, never individual rows.
  menuEl('menu-preview').querySelectorAll('input,select').forEach(input=>{input.disabled=true});
  try{
    const body={buyer:menuDraft.buyer,reviewed:true,needs};const fingerprint=JSON.stringify(body);
    if(menuBasketAttempt?.fingerprint!==fingerprint)menuBasketAttempt={fingerprint,key:typeof crypto.randomUUID==='function'?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('')};
    const result=await menuRequest('/ui/menu/basket',{...body,idempotency_key:menuBasketAttempt.key});
    const unresolved=result.line_errors||[];
    if(unresolved.length||result.status==='needs_clarification'||result.status==='needs_review'){
      for(const item of unresolved){const row=rows[item.index??item.need_index];if(row)row.querySelector('.menu-row-result').textContent=item.reason||item.message||'Ingredient needs clarification.';}
      output.textContent=result.explanation||'Some ingredients remain unresolved. No order was created. Edit the rows and review the complete draft again.';
      output.classList.add('menu-error');menuEl('menu-reviewed').checked=false;
    }else if(result.error){throw new Error(result.error)}else{
      output.textContent=`${result.status}: ${result.explanation||'Review the complete basket in Exchange / Approvals.'}${result.order?.order_id?' · '+result.order.order_id:''}`;
      const cart=document.createElement('ul');for(const line of result.lines||[]){const item=document.createElement('li');item.textContent=`${line.product_name||line.name||line.product_id||'Product'} · ${line.quantity??line.amount??''} ${line.unit||''}`;cart.append(item)}output.append(cart);
      if(result.order?.total_eur!=null){const total=document.createElement('p');total.textContent=`Complete basket total: €${Number(result.order.total_eur).toFixed(2)}`;output.append(total);}
      for(const warning of result.warnings||[]){const note=document.createElement('p');note.className='menu-error';note.textContent='Review before confirming: '+(typeof warning==='string'?warning:JSON.stringify(warning));output.append(note);}
      button.dataset.submitted='true';button.textContent='Complete basket requested';
      document.dispatchEvent(new Event('menu-basket-created'));
    }
  }catch(error){
    output.textContent=`${error.message} Outcome may be unknown. Check Exchange / Approvals before requesting this basket again.`;
    output.classList.add('menu-error');button.dataset.submitted='true';button.textContent='Check outcome before retry';
  }finally{menuBusy=false;menuEl('menu-preview').querySelectorAll('input,select').forEach(input=>{input.disabled=false});updateMenuReviewButtons()}
});

for(const id of ['menu-text','menu-guests','buyer-select','menu-image','menu-document','recipe-document','stock-document'])menuEl(id).addEventListener('input',()=>{menuDraft=null;menuEl('menu-preview').classList.add('hidden')});
updatePreset('product');
restoreRequestState();

let requestSaveVersion=0;
async function saveRequestState(){
  const version=++requestSaveVersion;
  const value={preset:menuEl('request-kind').value,text:menuEl('menu-text').value,buyer:menuEl('buyer-select').value,mode:menuEl('mode-select').value,guests:menuEl('menu-guests').value,files:[]};
  try{
    for(const id of ['menu-document','recipe-document','stock-document','menu-image']){const file=menuEl(id).files[0];if(!file)continue;
      if(id==='menu-image'){const image=await menuImage(file);value.files.push({id,name:file.name,type:file.type,base64:image.image_base64});}
      else if(file.size<=100*1024)value.files.push({id,name:file.name,type:file.type,text:await file.text()});
    }
    if(version!==requestSaveVersion)return;
    try{sessionStorage.setItem('tta.kitchen-request.v1',JSON.stringify(value));}catch{value.reattach=value.files.map(file=>file.name);value.files=[];sessionStorage.setItem('tta.kitchen-request.v1',JSON.stringify(value));}
  }catch{/* Browser storage can be unavailable; draft and ordering remain independent. */}
}
function restoreRequestState(){
  try{const value=JSON.parse(sessionStorage.getItem('tta.kitchen-request.v1')||'null');if(!value||!['product','menu','handwritten'].includes(value.preset))return;
    updatePreset(value.preset);menuEl('menu-text').value=String(value.text||'').slice(0,12000);menuEl('buyer-select').value=value.buyer||'restaurant';menuEl('menu-guests').value=value.guests||'1';if(value.preset==='product')menuEl('mode-select').value=value.mode||'autonomous';
    for(const file of value.files||[]){if(!['menu-document','recipe-document','stock-document','menu-image'].includes(file.id))continue;const body=file.base64?Uint8Array.from(atob(file.base64),c=>c.charCodeAt(0)):file.text;setInputFile(file.id,new File([body],file.name,{type:file.type}));}
    updateImagePreview();if(value.reattach?.length)menuMessage('Request restored. Reattach these files before running: '+value.reattach.join(', '));
  }catch{/* Ignore an unavailable or obsolete browser session snapshot. */}
}
for(const id of ['menu-text','menu-guests','buyer-select','mode-select','menu-image','menu-document','recipe-document','stock-document'])menuEl(id).addEventListener('change',saveRequestState);
menuEl('menu-text').addEventListener('input',saveRequestState);
