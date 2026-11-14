// Menu extraction only creates an editable draft. Each reviewed need is a separate action.
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
menuEl('request-kind').addEventListener('change',()=>{
  const menu=menuEl('request-kind').value==='menu';
  menuEl('agent-form').classList.toggle('hidden',menu);
  menuEl('menu-form').classList.toggle('hidden',!menu);
  if(menu&&!menuEl('menu-text').value)menuEl('menu-text').value=menuEl('request-input').value;
});
// A dish name is not a catalogue product. Guide ambiguous meal requests into review.
menuEl('agent-form').addEventListener('submit',event=>{
  const text=menuEl('request-input').value;
  if(!/(ingredients|recipe|menu|makarooni|makaroni|macaroni|lasagn|casserole|laatikko|lattikko)/i.test(text))return;
  event.preventDefault();event.stopImmediatePropagation();
  menuEl('request-kind').value='menu';menuEl('request-kind').dispatchEvent(new Event('change'));
  menuEl('menu-text').value=text;
  const portions=text.match(/(?:for|для)\s*(\d+)\s*(?:kids|children|people|guests|pupils|portions|дет)/i);
  if(portions&&Number(portions[1])<=10000)menuEl('menu-guests').value=portions[1];
  menuMessage('This looks like a dish or menu. Draft and review its ingredients first; no order has been created.');
},true);
menuEl('menu-example').addEventListener('click',()=>{menuEl('request-kind').value='menu';menuEl('request-kind').dispatchEvent(new Event('change'));menuEl('menu-text').value='Makaroonilaatikko for 1000 children. Milk-free school kitchen; draft ingredients for chef review.';menuEl('menu-guests').value='1000';menuEl('menu-buyer').value='school';});
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
  const rows=(draft.needs||[]).map((need,index)=>`<tr data-menu-row="${index}"><td><input aria-label="Ingredient ${index+1}" data-menu-field="ingredient" type="text" maxlength="200" value="${menuEscape(need.ingredient)}" required><p>${menuEscape((need.for_dishes||[]).join(', '))}</p></td><td><input aria-label="Amount ${index+1}" data-menu-field="amount" type="number" min="0.001" max="1000000" step="any" value="${menuEscape(need.amount)}" required><select aria-label="Unit ${index+1}" data-menu-field="unit">${['KG','L','ST'].map(unit=>`<option${need.unit===unit?' selected':''}>${unit}</option>`).join('')}</select></td><td><input aria-label="Delivery date ${index+1}" data-menu-field="date" type="date" value="${menuEscape(need.date)}" required></td><td><input aria-label="Dietary requirements ${index+1}" data-menu-field="constraints" type="text" value="${menuEscape((need.constraints||[]).join(', '))}"><p>Signed kitchen restrictions always apply.</p></td><td><button class="secondary-button" type="button" data-menu-buy="${index}" disabled>Request this basket</button><p class="menu-row-result" role="status"></p></td></tr>`).join('');
  const target=menuEl('menu-preview');
  target.classList.remove('hidden');
  target.innerHTML=`<h3>Chef review: ingredient draft</h3><p><b>Estimated quantities for ${menuEscape(draft.guests)} portions — no orders created.</b> Check the recipe, yields, stock already on hand and dates. This is not a demand forecast or a completed shopping basket.</p>${assumptions?`<h4>Assumptions to verify</h4><ul>${assumptions}</ul>`:''}${warnings?`<h4>Warnings / missing information</h4><ul>${warnings}</ul>`:''}<p>Each reviewed ingredient below opens its own basket in <b>confirm-cart mode</b>. You must still confirm the exact products and price; policy exceptions can need a further approval. A successful ingredient does not mean the whole menu is covered.</p><label class="menu-review-check menu-review-all"><input type="checkbox" id="menu-reviewed">I reviewed all ingredients, quantities, dates and requirements</label><p class="menu-review-note">Changing any ingredient clears this review. Each basket still needs its own product and price confirmation.</p><div class="menu-table-wrap"><table><thead><tr><th>Ingredient</th><th>Amount / unit</th><th>Delivery</th><th>Requirements</th><th>Request a basket</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
menuEl('menu-form').addEventListener('submit',async event=>{
  event.preventDefault();if(menuBusy)return;
  const form=event.currentTarget;if(!form.reportValidity())return;
  const guests=Number(menuEl('menu-guests').value);
  if(!Number.isInteger(guests)||guests<1||guests>10000){menuMessage('Enter a whole number of portions from 1 to 10000.',true);return}
  menuBusy=true;menuEl('menu-draft-button').disabled=true;menuMessage('Drafting ingredients. No orders are being created.');
  try{
    const image=await menuImage(menuEl('menu-image').files[0]);
    const text=menuEl('menu-text').value.trim();
    if(!text&&!image)throw new Error('Describe a dish/menu or attach an image.');
    const buyer=menuEl('menu-buyer').value;
    const draft=await menuRequest('/ui/menu/draft',{text,guests,buyer,...(image||{})});
    menuDraft={...draft,buyer};renderMenuDraft(menuDraft);
    menuMessage(`Draft ready: ${(draft.needs||[]).length} ingredients. Review the complete ingredient draft before requesting any basket.`);
  }catch(error){menuMessage(error.message,true)}finally{menuBusy=false;menuEl('menu-draft-button').disabled=false}
});
function updateMenuReviewButtons(){
  const reviewed=Boolean(menuEl('menu-reviewed')?.checked);
  menuEl('menu-preview').querySelectorAll('[data-menu-buy]').forEach(button=>{
    button.disabled=button.dataset.submitted==='true'||!reviewed||menuBusy;
  });
}
function invalidateMenuReview(event){
  const review=menuEl('menu-reviewed');if(!review)return;
  if(event.target!==review){
    if(!event.target.matches('[data-menu-field]'))return;
    review.checked=false;
  }
  updateMenuReviewButtons();
}
menuEl('menu-preview').addEventListener('change',invalidateMenuReview);
menuEl('menu-preview').addEventListener('input',invalidateMenuReview);
menuEl('menu-preview').addEventListener('click',async event=>{
  const button=event.target.closest('[data-menu-buy]');if(!button||button.disabled||menuBusy)return;
  const row=button.closest('[data-menu-row]'),fields={};
  row.querySelectorAll('[data-menu-field]').forEach(input=>{fields[input.dataset.menuField]=input.value});
  if(!menuEl('menu-reviewed')?.checked)return;
  const amount=Number(fields.amount);
  const inputs=[...row.querySelectorAll('[data-menu-field]')];
  if(inputs.some(input=>!input.reportValidity())||!Number.isFinite(amount)||amount<=0){return}
  const request=`${amount} ${fields.unit} ${fields.ingredient}, delivery ${fields.date}${fields.constraints.trim()?', '+fields.constraints.trim():''}.`;
  menuBusy=true;button.disabled=true;button.dataset.submitted='true';updateMenuReviewButtons();
  row.querySelectorAll('input,select').forEach(input=>{input.disabled=true});
  const output=row.querySelector('.menu-row-result');output.textContent='Requesting a basket for this ingredient only…';
  try{
    const result=await menuRequest('/ui/agent/run',{buyer:menuDraft.buyer,mode:'confirm_cart',request});
    output.textContent=`${result.status}: ${result.explanation||'Review the result in Exchange / Approvals.'}${result.order_id?' · '+result.order_id:''}${result.choice_id?' · '+result.choice_id:''}`;
    output.classList.toggle('menu-error',Boolean(result.error));
    button.textContent='Ingredient request submitted';
    document.dispatchEvent(new Event('menu-basket-created'));
  }catch(error){
    output.textContent=`${error.message} Outcome may be unknown. Check Exchange / Approvals before submitting this ingredient again.`;
    output.classList.add('menu-error');button.textContent='Check outcome before retry';
  }finally{menuBusy=false;updateMenuReviewButtons()}
});
