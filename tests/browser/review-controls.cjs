// Loopback PNG fixture: BASE_URL, ARTIFACT_ID, CHROME and NODE_PATH.
const assert = require('node:assert/strict');
const { chromium } = require('playwright-core');
const url = `${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`;
const placing = page => page.locator('body').evaluate(el => el.classList.contains('commenting'));
async function dismissTip(page) {
  const tip = page.locator('#float-tip');
  if (await tip.isVisible()) await tip.getByRole('button', { name: 'OK', exact: true }).tap();
}
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  try {
    for (const colorScheme of ['light', 'dark']) {
      const desktop = await browser.newContext({ viewport: { width: 1366, height: 800 }, colorScheme });
      const p = await desktop.newPage(), errors = [];
      p.setDefaultTimeout(7000);
      p.on('pageerror', e => errors.push(e.message));
      await p.goto(url);
      const pane = p.locator('#comments'), toggle = p.locator('#comments-toggle'), float = p.locator('#float-button');
      const add = p.getByRole('button', { name: 'Add comment', exact: true });
      await p.waitForFunction(() => document.querySelectorAll('#pins .pin').length === 3);
      assert(!(await pane.isVisible()));
      await toggle.click();
      assert(await pane.isVisible()); assert(!(await placing(p)));
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      await p.keyboard.press('c'); assert.equal(await add.getAttribute('aria-pressed'), 'true');
      await p.keyboard.press('c'); assert(await placing(p));
      await p.keyboard.press('Escape'); assert(await pane.isVisible()); assert(!(await placing(p)));
      await p.keyboard.press('Shift+c'); assert(!(await pane.isVisible()));
      await p.keyboard.press('c'); assert(await pane.isVisible()); assert(await placing(p));
      await p.getByRole('button', { name: '+ About the whole file', exact: true }).click();
      const draft = p.getByRole('textbox', { name: 'Comment', exact: true });
      await draft.fill('Keep this draft');
      await p.keyboard.press('f'); assert(!(await float.isVisible()));
      assert.equal(await draft.inputValue(), 'Keep this draftf');
      await p.keyboard.press('Escape'); assert(!(await placing(p)));
      assert.equal(await draft.inputValue(), 'Keep this draftf');
      await p.keyboard.press('Control+Enter');
      const finish = p.locator('#sheet-foot').getByRole('button', { name: /Finish review/ });
      await finish.waitFor(); assert(!(await placing(p)));
      await finish.click(); assert(!(await placing(p)));
      await p.getByRole('button', { name: 'Back', exact: true }).click();
      await toggle.focus(); await p.keyboard.press('f');
      assert(await float.isVisible()); assert(!(await pane.isVisible()));
      assert(!(await p.locator('#float-tip').isVisible()));
      await float.dblclick(); assert(await float.isVisible());
      if (!await p.locator('#fan').isVisible()) await float.click();
      assert.deepEqual(await p.locator('#fan-list kbd:visible').allTextContents(), ['Shift+C', 'C', 'F']);
      await p.locator('[data-act="comments"]').click();
      assert(await pane.isVisible()); assert(!(await placing(p)));
      await p.keyboard.press('f'); assert(!(await float.isVisible()));
      assert.deepEqual(errors, []);
      await desktop.close();

      const mobile = await browser.newContext({ viewport: { width: 380, height: 800 }, colorScheme, isMobile: true, hasTouch: true });
      const m = await mobile.newPage(), mobileErrors = [];
      m.setDefaultTimeout(7000);
      m.on('pageerror', e => mobileErrors.push(e.message));
      await m.goto(url);
      const strip = m.locator('#sheet-strip'), mPane = m.locator('#comments'), mAdd = m.getByRole('button', { name: 'Add comment', exact: true });
      assert.equal(await m.locator('header .actions .sq:visible').count(), 2);
      await strip.tap(); assert(!(await placing(m)));
      await mAdd.tap(); assert(await placing(m));
      assert(await strip.getByText('Tap a spot to comment.', { exact: true }).isVisible());
      await strip.getByRole('button', { name: 'Add comment', exact: true }).tap();
      assert(!(await placing(m))); assert(await mAdd.isVisible());
      await m.locator('#full-button').tap(); assert(!(await mPane.isVisible()));
      await dismissTip(m);
      const menu = m.locator('#float-button');
      await menu.tap(); await m.locator('[data-act="comments"]').tap();
      assert(await mPane.isVisible()); assert(!(await placing(m)));
      const cdp = await mobile.newCDPSession(m);
      const box = await menu.boundingBox();
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }] });
      await m.waitForFunction(() => document.body.classList.contains('commenting'));
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      assert(await placing(m)); assert(await mPane.isVisible());
      assert.deepEqual(mobileErrors, []);
      await mobile.close();
      console.log(`${colorScheme}: desktop shortcuts and review; phone controls, fullscreen gate and long press OK`);
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
