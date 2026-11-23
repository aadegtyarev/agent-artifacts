// BASE_URL, FIXTURE_JSON, CHROME and BASELINE_CSS are required; NODE_PATH contains Playwright.
// Uses disposable fixture data and creates acceptance drafts under separate author keys.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
const baseline = fs.readFileSync(process.env.BASELINE_CSS, 'utf8');
const base = process.env.BASE_URL;

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  try {
    for (const [width, height, size] of [[320, 568, 'normal'], [390, 844, 'larger'], [320, 390, 'larger']]) {
      const context = await browser.newContext({ timezoneId: 'UTC', viewport: { width, height }, isMobile: true, hasTouch: true });
      await context.addInitScript(size => localStorage.setItem('agent-artifacts:text-size', size), size);
      const page = await context.newPage();
      await page.clock.setFixedTime(new Date('2026-11-23T12:00:00Z'));
      await page.goto(`${base}/join/${fixture.invites[18]}`);
      const expiry = await page.locator('.join .meta').evaluate(e => {
        const range = document.createRange();
        range.selectNodeContents(e);
        const box = e.getBoundingClientRect();
        return { text: e.textContent.trim(), box: box.toJSON(), lines: Array.from(range.getClientRects(), r => r.toJSON()),
          whiteSpace: getComputedStyle(e).whiteSpace, overflow: getComputedStyle(e).overflow };
      });
      assert.match(expiry.text, /^This invite works once, until Nov 23, 2026 at 13:00 UTC\.$/);
      assert.equal(expiry.whiteSpace, 'normal');
      assert.equal(expiry.overflow, 'visible');
      assert.ok(expiry.lines.length >= 2, JSON.stringify(expiry));
      for (const line of expiry.lines) {
        assert.ok(line.left >= expiry.box.left - 1 && line.right <= expiry.box.right + 1 &&
          line.top >= expiry.box.top - 1 && line.bottom <= expiry.box.bottom + 1, JSON.stringify(expiry));
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      console.log('expiry', width, height, size, JSON.stringify(expiry));
      await context.close();
    }
    const results = {};
    for (const width of [320, 1000]) for (const variant of ['pill', 'link']) for (const mode of ['baseline', 'patched']) {
      const context = await browser.newContext({ viewport: { width, height: width === 320 ? 568 : 800 }, hasTouch: true, isMobile: width === 320 });
      const key = `${width}${variant}${mode}`.padEnd(30, 'x');
      await context.addInitScript(key => {
        localStorage.setItem('agent-artifacts:text-size', 'normal');
        localStorage.setItem('agent-artifacts:author-key', key);
        localStorage.setItem('agent-artifacts:name', 'Tester');
      }, key);
      const page = await context.newPage();
      await page.clock.setFixedTime(new Date('2026-11-23T12:00:00Z'));
      if (mode === 'baseline') await page.route('**/static/site.css*', route => route.fulfill({ status: 200, contentType: 'text/css', body: baseline }));
      await page.goto(`${base}/a/${fixture.ids['tap.html']}`);
      await page.locator('#comments-toggle').click();
      await page.locator('#strip-text').click();
      if (variant === 'link') {
        await page.getByRole('button', { name: '+ About the whole file' }).click();
        await page.locator('.sheet textarea').fill('A draft for finish review');
        await page.locator('.send').click();
        await page.getByRole('button', { name: /Finish review/ }).first().click();
      }
      const target = page.locator(`.all-${variant}`);
      await target.waitFor({ state: 'visible' });
      const data = await target.evaluate(e => {
        const r = e.getBoundingClientRect(), s = getComputedStyle(e);
        const offsets = [[-3, .5], [r.width + 3, .5], [r.width / 2, -7], [r.width / 2, r.height + 7]];
        return { box: r.toJSON(), font: s.fontSize, inset: getComputedStyle(e, '::before').inset,
          hits: offsets.map(([x,y]) => { const el = document.elementFromPoint(r.left + x, r.top + (y === .5 ? r.height / 2 : y)); return { target: !!el && (el === e || e.contains(el)), element: el?.className || el?.tagName }; }) };
      });
      assert.equal(data.font, '13px');
      console.log('hit-test', width, variant, mode, JSON.stringify(data));
      results[`${width}-${variant}-${mode}`] = data;
      const request = page.waitForRequest(r => r.url().endsWith('/drafts/batch') && r.method() === 'POST');
      await target.tap();
      const batch = (await request).postDataJSON();
      assert.equal(batch.decisions.length, 2);
      await page.getByText('Accepted 2', { exact: false }).first().waitFor({ state: 'visible' });
      await context.close();
    }
    for (const width of [320, 1000]) for (const variant of ['pill', 'link']) {
      const a = results[`${width}-${variant}-baseline`], b = results[`${width}-${variant}-patched`];
      assert.equal(a.inset, b.inset);
      assert.equal(a.box.width, b.box.width);
      assert.equal(a.box.height, b.box.height);
      // Existing overlap can block an expanded hit region; never make a working point fail.
      a.hits.forEach((hit, i) => { if (hit.target) assert.ok(b.hits[i].target, `${width} ${variant} point ${i}`); });
      if (width === 1000) assert.deepEqual(a.hits, b.hits);
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
