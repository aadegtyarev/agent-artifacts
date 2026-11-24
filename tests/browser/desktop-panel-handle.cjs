// Loopback PNG fixture: BASE_URL, ARTIFACT_ID, CHROME, NODE_PATH; SHOTS is optional.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const url = `${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`;
const width = async locator => Math.round((await locator.boundingBox()).width);
const placing = page => page.locator('body').evaluate(el=>el.classList.contains('commenting'));
const shot = (page, name) => process.env.SHOTS && page.screenshot({ path: `${process.env.SHOTS}/${name}.png` });
async function startDrag(page, handle) {
  const box = await handle.boundingBox(), x = box.x + box.width / 2, y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  return { x, y };
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  try {
    for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({viewport:{width:1366,height:800},colorScheme:theme});
      if (process.env.TEXT_SIZE) await context.addInitScript(size => localStorage.setItem('agent-artifacts:text-size', size), process.env.TEXT_SIZE);
      const page = await context.newPage(), errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(url);
      const panel = page.locator('#comments'), handle = page.locator('#panel-handle');
      const file = page.locator('.stage > main');
      await handle.waitFor();
      await page.waitForFunction(() => document.querySelectorAll('#pins .pin').length === 3);
      assert.equal(await handle.getAttribute('aria-expanded'), 'false');
      assert(!(await panel.isVisible()));
      await handle.click();
      assert.equal(await handle.getAttribute('aria-expanded'), 'true');
      assert(!(await placing(page)));
      const initial = await width(panel), fileWidth = await width(file);
      await handle.press('ArrowLeft');
      assert.equal(await width(panel), initial + 24);
      await handle.press('ArrowRight');
      assert.equal(await width(panel), initial);

      let point = await startDrag(page, handle);
      await page.mouse.move(point.x - 110, point.y, {steps:5});
      await page.mouse.up();
      const widened = await width(panel);
      assert(Math.abs(widened - (initial + 110)) <= 1);
      assert((await width(file)) < fileWidth);
      await handle.evaluate(el => el.blur());
      await shot(page, `${theme}-open`);

      point = await startDrag(page, handle);
      await page.mouse.move(point.x + 75, point.y, {steps:4});
      await page.mouse.up();
      const narrowed = await width(panel);
      assert(Math.abs(narrowed - (widened - 75)) <= 1);
      point = await startDrag(page, handle);
      await page.mouse.move(point.x - 40, point.y, {steps:4});
      await handle.press('Escape');
      await page.mouse.up();
      assert.equal(await width(panel), narrowed);
      assert(!(await placing(page)));

      point = await startDrag(page, handle);
      await page.mouse.move(1364, point.y, {steps:10});
      assert(await page.locator('.stage.panel-snap').count());
      await page.mouse.up();
      assert(!(await panel.isVisible()));
      assert.equal(await handle.getAttribute('aria-expanded'), 'false');
      assert.equal(await page.locator('#pins .pin').count(), 3);
      const tab = await handle.boundingBox();
      assert.equal(Math.round(tab.x + tab.width), 1366);
      await handle.evaluate(el => el.blur());
      await shot(page, `${theme}-folded`);
      await handle.press('Space');
      assert.equal(await width(panel), narrowed);
      assert(!(await placing(page)));

      await page.getByRole('button',{name:'Add comment',exact:true}).click();
      await page.getByRole('button', {name:'+ About the whole file', exact:true}).click();
      const draft = page.getByRole('textbox', {name:'Comment',exact:true});
      await draft.fill('Keep this draft while folded');
      await handle.click();
      assert(!(await panel.isVisible()));
      assert(!(await placing(page)));
      await handle.click();
      assert.equal(await draft.inputValue(), 'Keep this draft while folded');
      assert.equal(await width(panel), narrowed);
      assert(!(await placing(page)));

      await page.setViewportSize({width:899,height:800});
      await handle.waitFor({state:'hidden'});
      assert(await panel.isVisible());
      await page.setViewportSize({width:1366,height:800});
      await handle.waitFor();
      assert.equal(await width(panel), narrowed);
      assert.equal(await draft.inputValue(), 'Keep this draft while folded');
      assert.deepEqual(errors, []);
      await context.close();
      console.log(`${theme}: resize, cancel, edge fold, keyboard restore and preserved draft OK`);
    }
    const touch = await browser.newPage({viewport:{width:390,height:760},hasTouch:true,isMobile:true});
    await touch.goto(url);
    assert(!(await touch.locator('#panel-handle').isVisible()));
    await touch.locator('#sheet-strip').tap();
    assert(await touch.locator('#comments').isVisible());
    assert(!(await touch.locator('#panel-handle').isVisible()));
    console.log('Narrow touch sheet has no side handle');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
