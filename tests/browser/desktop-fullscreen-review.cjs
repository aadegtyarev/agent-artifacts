// Disposable loopback fixtures: BASE_URL, ARTIFACT_ID (HTML), EMPTY_ID (pinless), CHROME, NODE_PATH.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const base=process.env.BASE_URL;
async function menu(p,name) { await p.locator('#float-button').press('Enter'); await p.locator(`[data-act="${name}"]`).press('Enter'); }
async function full(p) { await p.locator('#full-button').click(); const tip=p.locator('#float-tip'); if(await tip.isVisible()) await tip.getByRole('button',{name:'OK'}).click(); }
async function pin(p) { const box=await p.locator('main iframe').boundingBox(); await p.mouse.click(box.x+box.width/2+155,box.y+150); }
async function makePage(browser,id=process.env.ARTIFACT_ID) {
 const p=await browser.newPage({viewport:{width:1366,height:800}});
 await p.addInitScript(()=>{if(window===window.top){localStorage.setItem('agent-artifacts:author-key','aaaaaaaaaaaaaaaaaaaaaa');localStorage.setItem('agent-artifacts:name','Alex');}});
 await p.goto(`${base}/a/${id}`); return p;
}
async function list(p) { await p.locator('#comments-toggle').click(); await p.getByText('Can we show the timezone?',{exact:true}).waitFor(); }
const placing = p => p.locator('body').evaluate(el=>el.classList.contains('commenting'));
const start = p => p.getByRole('button',{name:'Add comment',exact:true}).click();
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME,args:['--no-sandbox']});
 try {
  // Each dirty editor survives mode off, exit (or resize), then selecting the pin.
  for(const kind of ['compose','reply','edit']) for(const transition of ['exit','resize']) {
   console.log(`retention ${kind} ${transition}`);
   const p=await makePage(browser); await list(p);
   let field;
   if(kind==='compose') { await start(p); await p.getByRole('button',{name:'+ About the whole file',exact:true}).click(); field=p.getByRole('textbox',{name:'Comment',exact:true}); }
   if(kind==='reply') { await p.getByText('Can we show the timezone?',{exact:true}).click(); field=p.getByRole('textbox',{name:'Reply to the agent'}); }
   if(kind==='edit') { await p.getByText('Keep the full timestamp in CSV.',{exact:false}).first().click(); await p.getByRole('button',{name:'Edit',exact:true}).click(); field=p.locator('#comments textarea:visible'); }
   const text=`Retained ${kind} ${transition}`; await field.fill(text); const handle=await field.elementHandle(); await full(p);
   assert(!(await p.locator('#comments').isVisible())); await p.keyboard.press('Escape'); assert.equal(await handle.inputValue(),text);
   if(transition==='exit') await menu(p,'exit');
   else { await p.setViewportSize({width:899,height:800}); await p.setViewportSize({width:1366,height:800}); }
   await p.waitForTimeout(500); await pin(p);
   assert.equal(await field.inputValue(),text); assert(await field.isVisible());
   if(kind==='compose') { await pin(p); assert.equal(await field.inputValue(),text); }
   assert(!(await placing(p))); await p.close();
  }
  // Escape never discards typed text; explicit Discard keeps Undo.
  // Restored anchored compose keeps its anchor and remains protected against replacement.
  {
   const p=await makePage(browser); await list(p); await full(p); await menu(p,'comment');
   await p.frameLocator('main iframe').getByText('Share the result',{exact:true}).click();
   const field=p.getByRole('textbox',{name:'Comment',exact:true}); await field.fill('Undo anchored compose'); const handle=await field.elementHandle();
   await p.keyboard.press('Escape'); assert.equal(await handle.inputValue(),'Undo anchored compose');
   assert(!(await placing(p))); await p.getByRole('button',{name:'Discard',exact:true}).click();
   const undo=p.getByRole('button',{name:'Undo',exact:true}); assert(await undo.isVisible());
   await menu(p,'comments'); assert(await undo.isVisible()); await undo.click();
   assert.equal(await field.inputValue(),'Undo anchored compose');
   assert(!(await placing(p)));
   let submitted; await p.route('**/drafts',async route=>{if(route.request().method()!=='POST') return route.continue(); submitted=route.request().postDataJSON(); await route.fulfill({status:500,body:'{}'});});
   await p.getByRole('button',{name:'Add',exact:true}).click(); await p.waitForTimeout(100);
   assert.equal(submitted.text,'Undo anchored compose'); assert.equal(submitted.anchor.type,'element'); assert.equal(submitted.anchor.text,'Share the result'); await p.close();
  }
  {
   const p=await makePage(browser); await list(p); await full(p); await menu(p,'comment');
   await p.getByRole('button',{name:'+ About the whole file',exact:true}).click(); const field=p.getByRole('textbox',{name:'Comment',exact:true});
   await field.fill('Old retained text'); await p.getByRole('button',{name:'Discard',exact:true}).click();
   await p.getByRole('button',{name:'+ About the whole file',exact:true}).click(); await field.fill('Newer text');
   await p.getByRole('button',{name:'Undo',exact:true}).click(); assert.equal(await field.inputValue(),'Newer text');
   await p.getByText('Save or discard your changes first').waitFor(); assert(await p.getByRole('button',{name:'Undo',exact:true}).isVisible()); await p.close();
  }
  // Empty pinless content supports placement and restores typed work after closing the panel.
  {
   const p=await makePage(browser,process.env.EMPTY_ID); assert(!(await p.locator('#comments').isVisible()));
   await full(p); assert(!(await p.locator('#comments').isVisible())); await menu(p,'comment'); assert(await p.locator('#comments').isVisible());
   await menu(p,'comment'); await menu(p,'exit'); assert(!(await p.locator('#comments').isVisible()));
   await full(p); await menu(p,'comment'); assert(await p.locator('#comments').isVisible());
   await p.getByRole('button',{name:'+ About the whole file',exact:true}).click(); const field=p.getByRole('textbox',{name:'Comment',exact:true});
   await field.fill('Retained pinless compose'); await menu(p,'comment'); await menu(p,'exit');
   assert(!(await p.locator('#comments').isVisible())); await p.locator('#comments-toggle').click(); assert.equal(await field.inputValue(),'Retained pinless compose');
   await p.locator('#comments-toggle').click(); await p.setViewportSize({width:899,height:800}); await p.locator('#sheet-strip').click();
   assert(await field.isVisible()); assert.equal(await field.inputValue(),'Retained pinless compose'); await p.close();
  }
  // Only Show's fetch is changed; clean kind change navigates, dirty changes stay.
  for(const kind of ['compose','reply','edit','clean']) {
   console.log(`kind-change Show ${kind}`); const p=await makePage(browser); await list(p);
   if(kind==='compose') {await start(p); await p.getByRole('button',{name:'+ About the whole file',exact:true}).click(); await p.getByRole('textbox',{name:'Comment',exact:true}).fill('Unsaved compose');}
   if(kind==='reply') {await p.getByText('Can we show the timezone?',{exact:true}).click(); await p.getByRole('textbox',{name:'Reply to the agent'}).fill('Unsaved reply');}
   if(kind==='edit') {await p.getByText('Keep the full timestamp in CSV.',{exact:false}).first().click(); await p.getByRole('button',{name:'Edit',exact:true}).click(); await p.locator('#comments textarea:visible').fill('Unsaved edit');}
   await full(p);
   await p.route(`**/a/${process.env.ARTIFACT_ID}`,async route=>{if(route.request().resourceType()!=='fetch') return route.continue(); const response=await route.fetch(); await route.fulfill({response,body:(await response.text()).replace('data-kind="html"','data-kind="png"')});});
   await p.locator('.banner').evaluate(el=>{el.hidden=false;});
   if(kind==='clean') {await Promise.all([p.waitForNavigation(),p.getByRole('button',{name:'Show',exact:true}).click()]); assert(!(await p.locator('body').evaluate(el=>el.classList.contains('full'))));}
   else {await p.getByRole('button',{name:'Show',exact:true}).click(); await p.getByText('Save or discard your changes before showing the new file').waitFor(); assert(await p.locator('.banner').isVisible());
    await menu(p,'comments'); const expected={compose:'Unsaved compose',reply:'Unsaved reply',edit:'Unsaved edit'}[kind]; assert.equal(await p.locator('#comments textarea:visible').inputValue(),expected);}
   await p.close();
  }
  {
   const p=await makePage(browser); await list(p); await p.getByText('Can we show the timezone?',{exact:true}).click();
   await p.getByRole('textbox',{name:'Reply to the agent'}).fill('Reply during same-kind update'); await full(p);
   await p.locator('.banner').evaluate(el=>{el.hidden=false;}); await p.getByRole('button',{name:'Show',exact:true}).click();
   await p.locator('.banner').waitFor({state:'hidden'}); assert(await p.locator('body').evaluate(el=>el.classList.contains('full')));
   await menu(p,'comments'); assert.equal(await p.getByRole('textbox',{name:'Reply to the agent'}).inputValue(),'Reply during same-kind update'); await p.close();
  }
  // A pinless archive has no replaceable content; its Show guard also keeps work.
  {
   const p=await makePage(browser,process.env.EMPTY_ID); await full(p); await menu(p,'comment');
   await p.getByRole('button',{name:'+ About the whole file',exact:true}).click();
   const field=p.getByRole('textbox',{name:'Comment',exact:true}); await field.fill('Archive work');
   await menu(p,'comment'); await p.locator('.banner').evaluate(el=>{el.hidden=false;});
   await p.getByRole('button',{name:'Show',exact:true}).click();
   await p.getByText('Save or discard your changes before showing the new file').waitFor();
   await menu(p,'comment'); assert.equal(await field.inputValue(),'Archive work'); await p.close();
  }
  // A long desktop composer fits the short viewport and scrolls internally.
  {
   const p=await makePage(browser,process.env.EMPTY_ID); await p.setViewportSize({width:1000,height:400});
   await p.locator('#comments-toggle').click(); await start(p); await p.getByRole('button',{name:'+ About the whole file',exact:true}).click();
   const field=p.getByRole('textbox',{name:'Comment',exact:true});
   await field.fill(Array.from({length:40},(_,i)=>`Line ${i+1}`).join('\n'));
   const metrics=await field.evaluate(e=>({scroll:e.scrollHeight,client:e.clientHeight,bottom:e.getBoundingClientRect().bottom,resize:getComputedStyle(e).resize}));
   const send=await p.locator('#sheet-foot .send').boundingBox();
   assert(metrics.scroll>metrics.client && metrics.bottom<=400); assert.equal(metrics.resize,'none'); assert(send.y+send.height<=400);
   await p.setViewportSize({width:899,height:800}); await p.setViewportSize({width:900,height:800});
   assert((await field.inputValue()).includes('Line 40')); await p.close();
  }
  console.log('Fullscreen dirty state, Undo and Show regression checks passed');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
