// Loopback PNG fixture: BASE_URL, ARTIFACT_ID, CHROME and NODE_PATH; SHOTS is optional.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const dotVisible = locator => locator.evaluate(e => getComputedStyle(e, '::after').content === '""');
(async () => {
 const browser = await chromium.launch({executablePath:process.env.CHROME,args:['--no-sandbox']});
 const results = {};
 try {
  for (const theme of ['light','dark']) {
   const c = await browser.newContext({viewport:{width:1366,height:800},colorScheme:theme});
   const p = await c.newPage(); const errors=[]; p.on('pageerror',e=>errors.push(e.message));
   await p.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
   await p.locator('#comments-toggle.needs-attention').waitFor();
   const panel=p.locator('#comments'), main=p.locator('main'), mode=p.locator('[data-act=comment]');
   const floating=p.locator('#float-button'), modeIcon=mode.locator('.fan-sq');
   assert.equal(await panel.isVisible(),false); assert.equal(await p.locator('#pins .pin').count(),3);
   const offWidth=(await main.boundingBox()).width;
   if (process.env.SHOTS) await p.screenshot({path:`${process.env.SHOTS}/${theme}-ordinary-off.png`});
   await p.locator('#comments-toggle').click(); assert(await panel.isVisible());
   const onWidth=(await main.boundingBox()).width; assert(offWidth>onWidth+300);
   if (process.env.SHOTS) await p.screenshot({path:`${process.env.SHOTS}/${theme}-ordinary-on.png`});
   await p.locator('#comments-toggle').click(); assert(!(await panel.isVisible()));
   await p.locator('#full-button').click();
   const tip=p.locator('#float-tip'); if(await tip.isVisible()) await tip.getByRole('button',{name:'OK'}).click();
   assert(!(await panel.isVisible())); assert.equal(await p.locator('#pins .pin').count(),3);
   assert(await dotVisible(floating));
   assert.equal(await floating.getAttribute('aria-describedby'),'comment-attention');
   if (process.env.SHOTS) await p.screenshot({path:`${process.env.SHOTS}/${theme}-full-off.png`});
   await floating.click();
   assert(!(await dotVisible(floating)));
   assert(await dotVisible(modeIcon));
   const offColor=await modeIcon.evaluate(e=>getComputedStyle(e).backgroundColor);
   assert.equal(await mode.getAttribute('aria-checked'),'false');
   assert.equal(await p.getByRole('menuitem',{name:/^(Show|Hide) comments$/}).count(),0);
   if (process.env.SHOTS) await p.screenshot({path:`${process.env.SHOTS}/${theme}-full-off-menu.png`});
   await mode.click(); assert(await panel.isVisible());
   assert(await dotVisible(floating));
   assert(await floating.locator('.float-mode').isVisible());
   if (process.env.SHOTS) await p.screenshot({path:`${process.env.SHOTS}/${theme}-full-on.png`});
   await floating.click(); assert.equal(await mode.getAttribute('aria-checked'),'true');
   assert(!(await dotVisible(floating)));
   assert(await dotVisible(modeIcon));
   assert.notEqual(await modeIcon.evaluate(e=>getComputedStyle(e).backgroundColor),offColor);
   assert.equal(await mode.locator('.comment-check').count(),0);
   if (process.env.SHOTS) await p.screenshot({path:`${process.env.SHOTS}/${theme}-full-on-menu.png`});
   await mode.click(); assert(!(await panel.isVisible()));
   await p.locator('#pins .pin').first().click(); assert(await panel.isVisible());
   assert.equal(await p.locator('#comments-toggle').getAttribute('aria-pressed'),'false');
   await p.getByRole('button',{name:'Close comments'}).click(); assert(!(await panel.isVisible()));
   await p.locator('#pins .pin').first().click(); await p.keyboard.press('Escape'); assert(!(await panel.isVisible()));
   results[theme]={offWidth,onWidth,pins:3}; assert.deepEqual(errors,[]); await c.close();
  }
  // Three pins can remain while none belongs to Need you.
  {
   const c=await browser.newContext({viewport:{width:1366,height:800}}), p=await c.newPage();
   await p.route('**/comments', async route => {
    const response=await route.fetch(); const comments=await response.json();
    for (const comment of comments) { comment.agent='waiting'; comment.replies=[]; }
    await route.fulfill({response, json:comments});
   });
   await p.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
   await p.waitForFunction(() => document.querySelectorAll('#pins .pin').length === 3);
   assert(!(await p.locator('#comments-toggle').evaluate(e=>e.classList.contains('needs-attention'))));
   if (process.env.SHOTS) await p.screenshot({path:`${process.env.SHOTS}/no-need-ordinary.png`});
   await p.locator('#full-button').click(); const tip=p.locator('#float-tip');
   if(await tip.isVisible()) await tip.getByRole('button',{name:'OK'}).click();
   await p.locator('#float-button').click();
   assert(!(await p.locator('[data-act=comment]').evaluate(e=>e.classList.contains('needs-attention'))));
   if (process.env.SHOTS) await p.screenshot({path:`${process.env.SHOTS}/no-need-full-menu.png`});
   await p.keyboard.press('Escape');
   assert(!(await dotVisible(p.locator('#float-button'))));
   assert.equal(await p.locator('#float-button').getAttribute('aria-describedby'),null);
   await c.close();
  }
  // A read-only pin may be folded with a typed reply and reopened intact.
  {
   const c=await browser.newContext({viewport:{width:1366,height:800}}), p=await c.newPage();
   await p.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
   await p.locator('#comments-toggle.needs-attention').waitFor();
   await p.locator('#pins .pin').first().click();
   const reply=p.getByRole('textbox',{name:'Reply to the agent'});
   await reply.fill('Keep reply while folded');
   await p.getByRole('button',{name:'Close comments'}).click();
   assert(!(await p.locator('#comments').isVisible()));
   await p.locator('#pins .pin').first().click();
   assert.equal(await reply.inputValue(),'Keep reply while folded');
   await p.keyboard.press('Escape'); assert(!(await p.locator('#comments').isVisible()));
   await p.locator('#pins .pin').first().click();
   assert.equal(await reply.inputValue(),'Keep reply while folded'); await c.close();
  }
  // Toggling placement and crossing the breakpoint cannot erase a compose.
  {
   const c=await browser.newContext({viewport:{width:1366,height:800}}), p=await c.newPage();
   await p.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
   await p.locator('#comments-toggle.needs-attention').waitFor();
   await p.locator('#comments-toggle').click();
   await p.getByRole('button',{name:'+ About the whole file',exact:true}).click();
   const field=p.getByRole('textbox',{name:'Comment',exact:true}); await field.fill('Retained desktop compose');
   await p.locator('#comments-toggle').click(); assert(!(await p.locator('#comments').isVisible()));
   await p.keyboard.press('Escape'); await p.locator('#comments-toggle').click();
   assert.equal(await field.inputValue(),'Retained desktop compose');
   await p.locator('#comments-toggle').click(); await p.setViewportSize({width:899,height:800});
   await p.setViewportSize({width:1366,height:800}); await p.locator('#comments-toggle').click();
   assert.equal(await field.inputValue(),'Retained desktop compose'); await c.close();
  }
  const c=await browser.newContext({viewport:{width:390,height:700},isMobile:true,hasTouch:true}); const p=await c.newPage();
  await p.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
  await p.locator('#strip-text').getByText(/need you/).waitFor();
  assert(await p.locator('#comments').isVisible()); assert.equal(await p.locator('#pins .pin').count(),3);
  assert.equal(await p.locator('#comments-toggle.needs-attention').count(),0);
  await p.locator('#full-button').tap(); assert(!(await p.locator('#comments').isVisible()));
  await p.locator('#float-button').tap(); await p.locator('[data-act=comment]').tap(); assert(await p.locator('#comments').isVisible());
  await p.locator('#float-button').tap();
  assert(!(await dotVisible(p.locator('#float-button'))));
  assert.equal(await p.locator('[data-act=comment]').getAttribute('aria-checked'),'true');
  await p.keyboard.press('Escape');
  await p.setViewportSize({width:1366,height:800}); assert(await p.locator('#comments').isVisible());
  await p.setViewportSize({width:390,height:700}); assert(await p.locator('#comments').isVisible()); await c.close();
  if (process.env.SHOTS) fs.writeFileSync(`${process.env.SHOTS}/measurements.json`,JSON.stringify(results,null,2)); console.log(results);
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
