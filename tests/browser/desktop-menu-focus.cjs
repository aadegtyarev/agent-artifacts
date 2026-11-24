// Disposable fixture: BASE_URL, FIXTURE_JSON, CHROME and NODE_PATH are required.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 250 } });
    await page.goto(`${process.env.BASE_URL}/a/${fixture.ids['empty.html']}`);
    await page.locator('#full-button').click();
    await page.locator('#float-button').click();
    await page.keyboard.press('End');
    async function verify(label) {
      // The mobile fan animates into its new geometry after crossing the breakpoint.
      await page.waitForTimeout(500);
      const state = await page.evaluate(() => {
        const list = document.getElementById('fan-list');
        const focused = document.activeElement;
        const row = focused.getBoundingClientRect();
        const box = list.getBoundingClientRect();
        return { action: focused.dataset.act, scroll: list.scrollTop,
          scrollHeight: list.scrollHeight, clientHeight: list.clientHeight,
          top: row.top, bottom: row.bottom, listTop: box.top, listBottom: box.bottom,
          height: innerHeight };
      });
      console.log(label, state);
      assert.equal(state.action, 'settings');
      assert.ok(state.scrollHeight > state.clientHeight && state.scroll > 0);
      assert.ok(state.top >= state.listTop - 1 && state.bottom <= state.listBottom + 1);
      assert.ok(state.top >= 0 && state.bottom <= state.height);
    }
    await verify('1000');
    await page.setViewportSize({ width: 899, height: 250 });
    await verify('899');
    await page.setViewportSize({ width: 900, height: 250 });
    await verify('900');
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'float-button');
    console.log('Escape restored the floating opener');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
