// Optional browser regression: install Playwright/Chromium in a development environment,
// then run `node trade-agents/tests/browser/live_ui.cjs` (or set NODE_PATH).
// All responses are mocked; this test never calls a model or supplier.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:1000}});const errors=[];let failStart=false,failRun=false;page.on('pageerror',e=>errors.push(e.message));let posted;
 const scenarios=[{id:'basic-milk',title:'Basic milk',buyer:'hotel',category:'basic',request:'Buy milk',tier:'smoke',recorded:true},{id:'other',title:'Other case',buyer:'school',category:'basic',request:'Buy oats',tier:'core',recorded:false}];
 await page.route('http://bench.test/**',async route=>{const name=new URL(route.request().url()).pathname.slice(1);if(!['live.html','live.css','live.js'].includes(name))return route.abort();await route.fulfill({body:await fs.readFile(path.join(__dirname,'../../web',name)),contentType:name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'text/javascript'});});
 await page.route('**/ui/live/**',async route=>{const u=route.request().url();let data;
 if(new URL(u).pathname.endsWith('/scenarios'))data={scenarios};
 else if(u.endsWith('/capabilities'))data={live_available:true,providers:[{id:'gemini',label:'Google',configured:true,models:[{id:'fixed-model',label:'Fixed model'}]},{id:'verda',label:'Verda',configured:false,models:[{id:'fixed-other',label:'Other model'}]}]};
 else if(route.request().method()==='POST'){if(failStart){await route.fulfill({status:429,json:{detail:'Run budget exhausted'}});return}posted=route.request().postDataJSON();data={run_id:'test'};}
 else data={next:5,done:true,events:[{kind:'run_started',data:{mode:'live',provider:'gemini',model:'fixed-model',total:1}},{kind:'model_message',data:{text:'I found an eligible offer. <script>alert(1)</script>'}},{kind:'tool',data:{tool:'plan_need',args:{amount:2},result:'Plan created'}},{kind:'scenario_finished',data:{id:'basic-milk',status:'pending_approval',passed:true,total_eur:270,api_calls:3,checks:[{name:'approval',ok:true,detail:'Human decision required'}]}},{kind:'run_finished',data:{passed:1,scored:1}}],report:{model:'gemini:fixed-model',scored:1,passed:1,accuracy:1,coverage:{selected:1,scored:1},usage:{live_model_calls:2},safety:{violations:0}}};
 if(failRun&&u.includes('/runs/'))data={next:1,done:true,error:'Provider timeout',report:null,events:[{kind:'run_failed',data:{error:'Provider timeout'}}]};
 await route.fulfill({json:data});});
 await page.goto('http://bench.test/live.html');await page.locator('#mode-live').click();await page.waitForFunction(()=>!document.querySelector('#run-button').disabled);
 assert.equal(await page.locator('#scenario-list input[type=radio]').count(),2);await page.locator('#scenario-list input').nth(1).check();assert.equal(await page.locator('#scenario-list input:checked').count(),1);
 await page.locator('#provider-select').selectOption('verda');assert(await page.locator('#run-button').isDisabled());await page.locator('#provider-select').selectOption('gemini');
 await page.locator('#run-button').click();await page.waitForFunction(()=>document.querySelector('#run-state').textContent==='Complete');
 assert.deepEqual(posted,{mode:'live',dataset:'synthetic',scenario_ids:['other'],provider:'gemini',model:'fixed-model'});assert((await page.locator('#activity').textContent()).includes('Public model message'));assert((await page.locator('#activity').textContent()).includes('Human stage: Decision required'));assert.equal(await page.locator('#activity script').count(),0);assert.equal(errors.length,0);
 await page.waitForTimeout(500);await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('#mode-recorded').click();assert.equal(await page.locator('#scenario-list input').count(),1);await page.locator('#mode-offline').click();assert.equal(await page.locator('#scenario-list input[type=checkbox]').count(),2);
 failStart=true;await page.locator('#mode-live').click();await page.locator('#run-button').click();await page.waitForFunction(()=>document.querySelector('#run-state').textContent==='Error');assert((await page.locator('#activity').textContent()).includes('Run budget exhausted'));assert(!(await page.locator('#run-button').isDisabled()));
 failStart=false;failRun=true;await page.locator('#run-button').click();await page.waitForFunction(()=>document.querySelector('#run-state').textContent==='Error');assert((await page.locator('#activity').textContent()).includes('Provider timeout'));assert(await page.locator('#summary').evaluate(n=>n.classList.contains('hidden')));assert.equal(errors.length,0);
 console.log('PASS: live fixed models, unavailable provider, single selection, payload, real trace, separate human/checks, escaped messages, replay/offline, responsive layout');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
