// Disposable server and fixture: BASE_URL and FIXTURE_JSON; NODE_PATH supplies Playwright.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const f = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
const base = process.env.BASE_URL;
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  try {
    for (const [zone, expected, old] of [
      ['America/Los_Angeles', 'Nov 23, 2026 at 17:00 UTC-08:00', 'Dec 31, 2025'],
      ['Asia/Tokyo', 'Nov 24, 2026 at 10:00 UTC+09:00', 'Jan 1'],
    ]) {
      const context = await browser.newContext({ timezoneId: zone, viewport: { width: 320, height: 568 }, isMobile: true, hasTouch: true });
      await context.addInitScript(() => localStorage.setItem('agent-artifacts:text-size', 'larger'));
      const page = await context.newPage();
      await page.clock.setFixedTime(new Date('2026-11-23T12:00:00Z'));
      for (const path of [`/a/${f.standalone}`, `/join/${f.token}`]) {
        await page.goto(base + path);
        const time = page.locator('time[data-local-deadline]');
        assert.equal(await time.textContent(), expected);
        assert.equal(await time.getAttribute('datetime'), '2026-11-24T01:00:00Z');
        const boxes = await time.evaluate(e => { const r = document.createRange(); r.selectNodeContents(e); return { rects: [...r.getClientRects()].map(x => x.toJSON()), width: innerWidth, height: innerHeight, scroll: document.documentElement.scrollWidth }; });
        assert.ok(boxes.rects.every(r => r.left >= 0 && r.right <= boxes.width && r.bottom <= boxes.height), JSON.stringify(boxes));
        assert.ok(boxes.scroll <= boxes.width, JSON.stringify(boxes));
      }
      await page.goto(base + `/a/${f.noexpiry}`);
      assert.match(await page.locator('header .deadline-meta').textContent(), /kept until deleted/);
      assert.equal(await page.locator('time[data-local-deadline]').count(), 0);
      await page.goto(base + `/p/${f.project}`);
      assert.equal(await page.locator('time[data-local-date]').textContent(), old);
      await page.goto(base + `/a/${f.projectfile}`);
      await page.locator('#files-button').click();
      assert.equal(await page.locator('time[data-local-date]').textContent(), old);
      assert.equal(await page.locator('time[data-local-deadline]').count(), 0);
      assert.equal(await page.locator('[data-act="files"]').count(), 1);
      console.log(zone, expected, old, '320/Larger visible');
      await context.close();
    }
    const nojs = await browser.newContext({ javaScriptEnabled: false });
    const page = await nojs.newPage();
    for (const path of [`/a/${f.standalone}`, `/join/${f.token}`]) {
      await page.goto(base + path);
      assert.equal(await page.locator('time[data-local-deadline]').textContent(), '2026-11-24 01:00 UTC');
    }
    await nojs.close();
    const noIntl = await browser.newContext();
    await noIntl.addInitScript(() => { window.Intl = undefined; });
    const fallback = await noIntl.newPage();
    for (const path of [`/a/${f.standalone}`, `/join/${f.token}`]) {
      await fallback.goto(base + path);
      assert.equal(await fallback.locator('time[data-local-deadline]').textContent(), '2026-11-24 01:00 UTC');
    }
    await noIntl.close();
    const context = await browser.newContext({ timezoneId: 'Asia/Tokyo', viewport: { width: 320, height: 568 }, isMobile: true, hasTouch: true });
    await context.addInitScript(() => localStorage.setItem('agent-artifacts:text-size', 'larger'));
    const p = await context.newPage();
    await p.clock.setFixedTime(new Date('2026-11-23T12:00:00Z'));
    await p.goto(base + `/a/${f.standalone}`);
    await p.evaluate(() => window.originalDocument = document);
    await p.waitForTimeout(1000);
    const result = await context.request.put(base + `/api/v1/artifacts/${f.standalone}`, {
      headers: { Authorization: `Bearer ${f.key}` }, multipart: { file: { name: 'test.html', mimeType: 'text/html', buffer: Buffer.from('<p>new</p>') } },
    });
    assert.equal(result.status(), 200);
    const api = await result.json();
    assert.ok(api.expires_at.endsWith('Z'));
    const banner = p.locator('.banner');
    await banner.waitFor({ state: 'visible', timeout: 20000 });
    const placement = await p.evaluate(() => Object.fromEntries(['header', 'header time', '.banner'].map(s => [s, document.querySelector(s).getBoundingClientRect().toJSON()])));
    assert.ok(placement['header time'].height > 20, JSON.stringify(placement));
    assert.ok(placement['.banner'].top >= placement.header.bottom && placement['.banner'].top >= placement['header time'].bottom, JSON.stringify(placement));
    console.log('pending placement', JSON.stringify(placement));
    await p.locator('#full-button').click();
    await p.waitForFunction(() => document.body.classList.contains('full'));
    const full = await banner.boundingBox();
    assert.ok(Math.abs(full.y - 73.5) < 1, JSON.stringify(full));
    console.log('fullscreen placement', JSON.stringify(full));
    // Hold subsequent polls so the Settings change must reposition the pending banner itself.
    await p.route(`**/a/${f.standalone}/comments**`, route => route.abort());
    await p.locator('#float-button').click();
    await p.locator('[data-act="settings"]').click();
    await p.locator('input[name="set-text"][value="normal"]').check({ force: true });
    const normal = await banner.boundingBox();
    assert.ok(full.y > normal.y && Math.abs(normal.y - 60) < 1, JSON.stringify({ full, normal }));
    assert.equal(await banner.isVisible(), true);
    console.log('fullscreen Settings Larger to Normal without poll', JSON.stringify({ full, normal }));
    await p.locator('#settings-form').getByRole('button', { name: 'Done' }).click();
    await p.getByRole('button', { name: 'Show', exact: true }).click();
    await p.waitForFunction(expected => document.querySelector('time[data-local-deadline]')?.dateTime === expected, api.expires_at);
    assert.equal(await p.evaluate(() => document === window.originalDocument), true);
    assert.equal(await p.locator('time[data-local-deadline]').getAttribute('datetime'), api.expires_at);
    assert.notEqual(await p.locator('time[data-local-deadline]').textContent(), 'Nov 24, 2026 at 10:00 UTC+09:00');
    console.log('Show updated same document and datetime', api.expires_at, await p.locator('time[data-local-deadline]').textContent());
    await context.close();
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
