// Disposable fixture only: BASE_URL, FIXTURE_JSON, CHROME and NODE_PATH are required.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  try {
    for (const kind of ['compose', 'reply']) {
      const context = await browser.newContext({ viewport: { width: 1000, height: 800 }, hasTouch: true });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(`${process.env.BASE_URL}/a/${fixture.ids[kind === 'compose' ? 'empty.html' : 'mixed.html']}`);
      await page.locator('#comments-toggle').tap();
      if (kind === 'compose') {
        await page.getByRole('button', { name: 'Add comment', exact: true }).tap();
        await page.getByRole('button', { name: '+ About the whole file', exact: true }).tap();
      } else {
        await page.locator('#sheet-body .it').first().click();
      }
      const field = kind === 'compose' ? page.getByRole('textbox', { name: 'Comment', exact: true })
        : page.locator('#sheet-foot textarea');
      const arrow = page.locator('#sheet-foot .send');
      const text = `Pending ${kind} proof`;
      await field.fill(text);
      let requests = 0;
      let release;
      let started;
      let releaseRetry;
      let startedRetry;
      const gate = new Promise(resolve => { release = resolve; });
      const first = new Promise(resolve => { started = resolve; });
      const retryGate = new Promise(resolve => { releaseRetry = resolve; });
      const retryStarted = new Promise(resolve => { startedRetry = resolve; });
      await page.route('**/a/*/drafts', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        requests++;
        if (requests === 1) {
          started();
          await gate;
          await route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
        } else {
          startedRetry();
          await retryGate;
          await route.continue();
        }
      });
      const shortcut = kind === 'compose' ? 'Control+Enter' : 'Meta+Enter';
      await field.press(shortcut);
      await first;
      assert.equal(await arrow.isDisabled(), true);
      const edited = `${text} edited while waiting`;
      await field.fill(edited);
      assert.equal(await arrow.isDisabled(), true);
      await page.setViewportSize({ width: 899, height: 800 });
      await page.setViewportSize({ width: 900, height: 800 });
      await field.waitFor({ state: 'visible' });
      assert.equal(await field.inputValue(), edited);
      assert.equal(await arrow.isDisabled(), true);
      await field.press(shortcut);
      await page.waitForTimeout(8500); // cross an actual poll while the request is held
      assert.equal(requests, 1);
      assert.equal(await field.inputValue(), edited);
      assert.equal(await arrow.isDisabled(), true);
      release();
      await page.waitForFunction(() => document.querySelector('#sheet-foot .send') && !document.querySelector('#sheet-foot .send').disabled);
      assert.equal(await field.inputValue(), edited);
      await field.press(shortcut);
      await retryStarted;
      const newest = `${edited} and more`;
      await field.fill(newest);
      assert.equal(await arrow.isDisabled(), true);
      const saved = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/drafts'));
      releaseRetry();
      assert.equal((await saved).status(), 201);
      await page.waitForFunction(() => document.querySelector('#sheet-foot .send') && !document.querySelector('#sheet-foot .send').disabled);
      assert.equal(await field.inputValue(), newest);
      assert.equal(requests, 2);
      assert.deepEqual(errors, []);
      console.log(`${kind}: pending lock, poll, redraw, failure and retry passed`);
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
