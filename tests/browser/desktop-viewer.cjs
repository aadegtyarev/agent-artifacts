// Disposable fixture only: BASE_URL, FIXTURE_JSON, CHROME and NODE_PATH are required.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 800 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${process.env.BASE_URL}/a/${fixture.ids['empty.html']}`);
    await page.locator('#comments-toggle').click();
    await page.getByRole('button', { name: 'Add comment', exact:true }).click();
    await page.getByText('Click a spot to comment.').first().waitFor();
    await page.getByRole('button', { name: /whole file/i }).click();
    const field = page.getByRole('textbox', { name: 'Comment', exact: true });
    await field.fill('one\ntwo\nthree\nfour\nfive\nsix\nseven');
    await field.press('Shift+Enter');
    assert.equal(await field.inputValue(), 'one\ntwo\nthree\nfour\nfive\nsix\nseven\n');
    assert.ok((await field.boundingBox()).height > 140);
    await page.setViewportSize({ width: 899, height: 800 });
    await page.setViewportSize({ width: 900, height: 800 });
    assert.equal(await field.inputValue(), 'one\ntwo\nthree\nfour\nfive\nsix\nseven\n');
    await page.setViewportSize({ width: 1000, height: 400 });
    await field.fill(Array.from({ length: 40 }, (_, i) => `Line ${i + 1}`).join('\n'));
    const overflow = await field.evaluate(e => ({
      scroll: e.scrollHeight, client: e.clientHeight, bottom: e.getBoundingClientRect().bottom,
      resize: getComputedStyle(e).resize,
    }));
    const send = await page.locator('#sheet-foot .send').boundingBox();
    assert.ok(overflow.scroll > overflow.client && overflow.bottom <= 400);
    assert.equal(overflow.resize, 'none');
    assert.ok(send.y + send.height <= 400);
    await field.fill('one\ntwo\nthree\nfour\nfive\nsix\nseven\n');
    await field.press('Enter');
    await page.locator('#sheet-body').getByText('one', { exact: false }).first().waitFor();
    await page.locator('#menu-button').click();
    const active = await page.evaluate(() => document.activeElement.dataset.act);
    await page.setViewportSize({ width: 899, height: 800 });
    await page.setViewportSize({ width: 900, height: 800 });
    assert.equal(await page.evaluate(() => document.activeElement.dataset.act), active);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
