// Disposable loopback HTML fixture: BASE_URL, ARTIFACT_ID, CHROME and NODE_PATH.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
(async () => {
 const browser = await chromium.launch({executablePath:process.env.CHROME,args:['--no-sandbox'],ignoreDefaultArgs:['--hide-scrollbars']});
 try {
  for (const theme of ['light','dark']) {
   const c = await browser.newContext({viewport:{width:1366,height:800},colorScheme:theme});
   const p = await c.newPage(), errors=[]; p.on('pageerror',e=>errors.push(e.message));
   await p.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
   const panel=p.locator('#comments'), main=p.locator('main');
   assert(!(await panel.isVisible())); const off=(await main.boundingBox()).width;
   await p.locator('#comments-toggle').click(); await p.getByText('Can we show the timezone?',{exact:true}).waitFor();
   assert(off>(await main.boundingBox()).width+300);
   await p.locator('#comments-toggle').click(); await p.locator('#full-button').click();
   const tip=p.locator('#float-tip'); if(await tip.isVisible()) await tip.getByRole('button',{name:'OK'}).click();
   assert(!(await panel.isVisible()));
   await p.locator('#float-button').press('Enter'); const mode=p.locator('[data-act=comments]');
   assert.equal(await mode.getAttribute('aria-expanded'),'false'); await mode.focus();
   await p.setViewportSize({width:899,height:800}); await p.waitForTimeout(150);
   await p.setViewportSize({width:1366,height:800}); await p.waitForTimeout(150);
   assert.equal(await p.evaluate(()=>document.activeElement.dataset.act),'comments');
   await mode.press('Enter'); assert(await panel.isVisible());
   assert.equal(await p.evaluate(()=>document.activeElement.id),'float-button');
   await p.locator('#float-button').press('Enter'); assert.equal(await mode.getAttribute('aria-expanded'),'true');
   await mode.press('Enter'); assert(!(await panel.isVisible()));
   assert.deepEqual(errors,[]); await c.close();
  }
  const low=await browser.newContext({viewport:{width:1000,height:400}}), p=await low.newPage();
  await p.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
  await p.locator('#comments-toggle').click(); await p.getByText('Can we show the timezone?',{exact:true}).click();
  const scroll=p.locator('#sheet-body');
  const metrics=await scroll.evaluate(e=>({scroll:e.scrollHeight,client:e.clientHeight,gutter:e.offsetWidth-e.clientWidth,overflow:getComputedStyle(e).overflowY}));
  assert(metrics.scroll>metrics.client && metrics.gutter>0); assert.equal(metrics.overflow,'auto');
  await scroll.hover(); await p.mouse.wheel(0,100); await p.waitForTimeout(300); assert((await scroll.evaluate(e=>e.scrollTop))>0); await low.close();
  const touch=await browser.newContext({viewport:{width:390,height:700},isMobile:true,hasTouch:true}), m=await touch.newPage();
  await m.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
  await m.locator('#full-button').tap(); assert(!(await m.locator('#comments').isVisible()));
  await m.locator('#float-button').tap(); await m.locator('[data-act=comments]').tap();
  assert(await m.locator('#comments').isVisible());
  await m.locator('#float-button').tap(); await m.locator('[data-act=comments]').tap();
  assert(!(await m.locator('#comments').isVisible())); await touch.close();
  console.log('Fullscreen layout, focus, native history scroll and narrow touch passed');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
