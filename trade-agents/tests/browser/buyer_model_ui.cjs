// Optional Playwright regression for the Exchange buyer-model selector.
// NODE_PATH=/tmp/junction-demo-recording/node_modules node tests/browser/buyer_model_ui.cjs
// Serves repository files with mocked endpoints only; no model, supplier or order call.
const {chromium}=require('playwright');const fs=require('node:fs/promises');const path=require('node:path');const assert=require('node:assert/strict');
const shots=process.env.TTA_SHOT_DIR||'';
const idle=page=>page.waitForFunction(()=>!document.querySelector('#run-button').disabled);
async function run(scheme,models,check){
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:1000},colorScheme:scheme});const errors=[];const runs=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('http://exchange.test/**',async route=>{const name=new URL(route.request().url()).pathname.slice(1);
      if(name==='ui/overview')return route.fulfill({json:{orders:[],choices:[],audit:{entries:[],chain_valid:true},jwks:{keys:[]}}});
      if(name==='ui/agent/models')return route.fulfill({json:models});
      if(name==='ui/agent/run'){const body=route.request().postDataJSON();runs.push(body);return route.fulfill({json:{status:'no_order',explanation:'TEST MOCK',buyer_model:{label:body.provider==='verda-deepseek'?'DeepSeek 4.1 Flash on Verda':'Gemini 3.5 Flash'}}})}
      if(!['index.html','app.css','menu.css','menu.js','app.js','demo-proof.css','demo-proof.js'].includes(name))return route.abort();
      await route.fulfill({body:await fs.readFile(path.join(__dirname,'../../web',name)),contentType:name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'text/javascript'})});
    await page.goto('http://exchange.test/index.html');
    await page.waitForFunction(()=>document.querySelector('#buyer-model-select').options[0].textContent.includes('(default)'));
    await check(page,runs);
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    if(shots){if(!(await page.locator('#buyer-model-settings').getAttribute('open')===''))await page.locator('#buyer-model-settings summary').click();await page.locator('#menu-form').screenshot({path:path.join(shots,`buyer-model-${scheme}-390.png`)});}
    assert.deepEqual(errors,[]);
  }finally{await browser.close()}
}
(async()=>{
  // Only Gemini configured: one disabled default option; the request carries no provider.
  await run('light',{default:{id:'',label:'Gemini 3.5 Flash'},options:[]},async(page,runs)=>{
    const select=page.locator('#buyer-model-select');const settings=page.locator('#buyer-model-settings');
    // Hidden behind a collapsed Settings disclosure by default; the main screen stays uncluttered.
    assert(await settings.isVisible());assert(!(await select.isVisible()));await settings.locator('summary').click();
    assert(await select.isVisible());assert(await select.isDisabled());
    assert.equal(await select.locator('option').count(),1);
    await page.locator('#menu-draft-button').click();
    await page.waitForTimeout(200);await idle(page);
    assert.equal(runs.length,1);assert(!('provider' in runs[0]));
    assert.equal(await page.locator('#buyer-model-used').textContent(),'Buyer model: Gemini 3.5 Flash');
    await page.locator('[data-menu-mode=menu]').click();assert(!(await settings.isVisible()));
    await page.locator('[data-menu-mode=product]').click();assert(await settings.isVisible());
  });
  // Nemotron on Verda configured (default): Gemini and others listed, the choice is sent as provider; default still sends nothing.
  await run('dark',{default:{id:'',label:'Nemotron 3.5 Super VL on Verda'},options:[{id:'gemini',label:'Gemini 3.5 Flash'},{id:'verda-deepseek',label:'DeepSeek 4.1 Flash on Verda'}]},async(page,runs)=>{
    const select=page.locator('#buyer-model-select');await page.locator('#buyer-model-settings summary').click();assert(!(await select.isDisabled()));
    assert.deepEqual(await select.locator('option').allTextContents(),['Nemotron 3.5 Super VL on Verda (default)','Gemini 3.5 Flash','DeepSeek 4.1 Flash on Verda']);
    await page.locator('#menu-draft-button').click();await page.waitForTimeout(200);await idle(page);
    await select.selectOption('verda-deepseek');await page.locator('#menu-draft-button').click();await page.waitForTimeout(200);await idle(page);
    assert.equal(runs.length,2);assert(!('provider' in runs[0]));assert.equal(runs[1].provider,'verda-deepseek');
    assert.equal(await page.locator('#buyer-model-used').textContent(),'Buyer model: DeepSeek 4.1 Flash on Verda');
  });
  console.log('PASS buyer-model selector: default sends no provider, configured options only, product-only, settings disclosure, result line, light/dark, 390px');
})().catch(e=>{console.error(e);process.exit(1)});
