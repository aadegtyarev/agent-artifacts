// Run against a disposable fixture: BASE_URL, FIXTURE_JSON and CHROME are required.
// NODE_PATH must contain Playwright. This changes the fixture's one.html and claims two invites.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const f = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
const base = process.env.BASE_URL;
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  try {
    const mobile = await browser.newContext({ viewport: { width: 320, height: 568 }, isMobile: true, hasTouch: true });
    await mobile.addInitScript(() => localStorage.setItem('agent-artifacts:text-size', 'normal'));
    const page = await mobile.newPage();
    await page.clock.setFixedTime(new Date('2026-11-23T12:00:00Z'));
    await page.goto(`${base}/a/${f.ids['one.html']}`);
    await page.waitForTimeout(1000); // Let the initial poll establish the file version.
    const response = await page.request.put(`${base}/api/v1/artifacts/${f.ids['one.html']}`, {
      headers: { Authorization: `Bearer ${f.key}` },
      multipart: { file: { name: 'one.html', mimeType: 'text/html', buffer: Buffer.from('<p>Updated</p>') } },
    });
    assert.equal(response.status(), 200);
    const message = page.locator('.banner span').first();
    await message.waitFor({ state: 'visible', timeout: 12000 });
    assert.equal(await message.textContent(), 'The file was updated');
    const font = await message.evaluate(e => parseFloat(getComputedStyle(e).fontSize) * parseFloat(getComputedStyle(e).zoom));
    assert.ok(font >= 15, `visible banner font ${font}`);
    console.log('visible mobile update banner font:', font);
    await mobile.close();

    const desktop = await browser.newContext({ viewport: { width: 1000, height: 800 } });
    const p = await desktop.newPage();
    await p.clock.setFixedTime(new Date('2026-11-23T12:00:00Z'));
    const buttonSizes = {};
    for (const [i, size] of ['normal', 'large', 'larger'].entries()) {
      if (i === 0) await p.goto(`${base}/join/${f.invites[20 + i]}`);
      await p.evaluate(size => localStorage.setItem('agent-artifacts:text-size', size), size);
      await p.goto(`${base}/join/${f.invites[20 + i]}`);
      for (const phase of ['invite', 'key']) {
        if (phase === 'key') await p.locator('#setup').waitFor({ state: 'visible' });
        const metrics = await p.locator('.join').evaluate(e => {
          const s = getComputedStyle(e), r = e.getBoundingClientRect();
          return { width: r.width, padding: [s.paddingTop, s.paddingRight], zoom: s.zoom };
        });
        assert.equal(metrics.width, 560);
        assert.deepEqual(metrics.padding, ['24px', '16px']);
        assert.equal(metrics.zoom, '1');
        const button = await p.locator('.join button').first().boundingBox();
        assert.ok(button.width > 0 && button.height > 0);
        const dimensions = [button.width, button.height];
        if (buttonSizes[phase]) assert.deepEqual(dimensions, buttonSizes[phase]);
        else buttonSizes[phase] = dimensions;
        console.log('desktop', size, phase, metrics, 'button', button);
        if (phase === 'invite') await p.locator('.join button').click();
      }
    }
    await desktop.close();
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
