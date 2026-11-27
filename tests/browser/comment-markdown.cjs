// Disposable local disk instance only. FIXTURE_JSON contains its base URL and
// a temporary agent key. CHROME and NODE_PATH select Chromium and playwright-core.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');
const { base, key } = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
assert.match(base, /^http:\/\/127\.0\.0\.1:\d+$/);
const headers = { Authorization: `Bearer ${key}` };
const docs = 'https://docs.google.com/document/d/example/edit?mode=read&tab=t.0';
const unsafe = '[Other scheme](data:text/plain,example)';
const plain = 'A normal line.\nA second line.';
const comment = `**New wireframes**: ${base}/example/versions/exact.\n${plain}\n\n- First option\n- Second option`;
const reply = `[Review notes](${docs})\n\n${unsafe}\n` +
  '![Preview](https://images.example/preview.png)\n`https://example.org/code`\n\n<div>Raw HTML</div>';

async function publish(page) {
  await page.getByRole('button', { name: 'Review drafts · 1', exact: true }).click();
  const action = page.getByRole('button', { name: 'Send comments', exact: true });
  const [response] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/review') && r.request().method() === 'POST'), action.click(),
  ]);
  assert.equal(response.status(), 201);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME });
  try {
    for (const width of [320, 1280]) for (const colorScheme of ['light', 'dark']) for (const size of ['large', 'larger']) {
      const mobile = width < 900;
      const context = await browser.newContext({ viewport: { width, height: 820 }, colorScheme,
        hasTouch: mobile, isMobile: mobile });
      try {
        await context.addInitScript(textSize => {
          if (window !== window.top || location.hostname !== '127.0.0.1') return;
          localStorage.setItem('agent-artifacts:text-size', textSize);
          localStorage.setItem('agent-artifacts:name', 'Example Reviewer');
        }, size);
        const api = context.request;
        const upload = await api.post(`${base}/api/v1/artifacts`, { headers, multipart: {
          file: { name: 'example.html', mimeType: 'text/html', buffer: Buffer.from('<p>Review sample</p>') },
          title: 'Comment formatting · preview',
        }});
        assert.equal(upload.status(), 201);
        const { id } = await upload.json();
        const agent = `${base}/api/v1/artifacts/${id}`;
        const url = `${base}/a/${id}`;
        await context.route('**/example/versions/exact', route => route.fulfill({ body: 'Exact version example' }));
        await context.route(docs, route => route.fulfill({ body: 'Google Doc example' }));
        const page = await context.newPage();
        const errors = [], unexpected = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('request', request => { if (!request.url().startsWith(base + '/')) unexpected.push(request.url()); });
        page.setDefaultTimeout(7500);
        const panel = page.locator('#comments');
        const open = () => (mobile ? page.locator('#strip-text') : page.locator('#comments-toggle')).click();
        const openThread = () => page.getByRole('button', { name: /\*\*New wireframes\*\*/ }).first().click();
        assert.equal((await page.goto(url)).status(), 200);
        await open();
        await panel.getByRole('button', { name: 'Add comment', exact: true }).click();
        if (mobile && await panel.getAttribute('data-pos') === 'strip') await open();
        await panel.getByRole('button', { name: '+ About the whole file', exact: true }).click();
        const input = panel.getByRole('textbox', { name: 'Comment', exact: true });
        await input.fill(comment);
        assert.equal(await input.inputValue(), comment);
        const [saved] = await Promise.all([
          page.waitForResponse(r => r.url().endsWith('/drafts') && r.request().method() === 'POST'),
          panel.getByRole('button', { name: 'Add', exact: true }).click(),
        ]);
        assert.equal(saved.status(), 201);
        if (mobile) {
          await page.locator('#comments[data-pos="strip"]').waitFor();
          await open();
        }
        await openThread();
        const draft = panel.locator('.msg.unsent .text');
        assert.equal(await draft.textContent(), comment);
        assert.equal(await draft.getByRole('link').count(), 0);
        await publish(page);
        assert.equal((await api.post(agent + '/take', { headers })).status(), 200);
        assert.equal((await api.post(agent + '/comments/1/reply', { headers, data: { text: reply, state: 'wrote' } })).status(), 201);
        await page.reload();
        await open();
        await openThread();

        const rendered = panel.locator('.comment-markdown');
        const raw = rendered.first().getByRole('link');
        const named = rendered.nth(1).getByRole('link', { name: 'Review notes' });
        await named.waitFor();
        assert.equal(await rendered.first().locator('strong').textContent(), 'New wireframes');
        assert.equal(await rendered.first().locator('li').count(), 2);
        assert((await rendered.first().locator('p').textContent()).includes(plain));
        assert.equal(await rendered.nth(1).getByRole('link').count(), 1);
        assert.equal(await rendered.nth(1).locator('code').textContent(), 'https://example.org/code');
        const renderedReply = await rendered.nth(1).textContent();
        assert(renderedReply.includes(unsafe));
        assert(renderedReply.includes('<div>Raw HTML</div>'));
        assert(renderedReply.includes('![Preview](https://images.example/preview.png)'));
        assert.equal(await rendered.nth(1).locator('img, iframe, div, [style]').count(), 0);
        const [published] = await (await api.get(agent + '/comments', { headers })).json();
        assert.equal(published.text, comment);
        assert.equal(published.replies[0].text, reply);

        for (const [link, destination] of [[raw, `${base}/example/versions/exact`], [named, docs]]) {
          assert.equal(await link.getAttribute('href'), destination);
          assert.equal(await link.getAttribute('target'), '_blank');
          assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
          assert(await link.evaluate(a => {
            const msg = a.closest('.bd');
            const bounds = msg.getBoundingClientRect();
            return getComputedStyle(a).textDecorationLine.includes('underline') &&
              getComputedStyle(a).fontSize === getComputedStyle(msg).fontSize &&
              [...a.getClientRects()].every(rect => rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1);
          }));
          if (!mobile) {
            await link.focus();
            await page.keyboard.press('Tab');
            await page.keyboard.press('Shift+Tab');
            assert(await link.evaluate(a => a === document.activeElement &&
              getComputedStyle(a).outlineStyle === 'solid'));
          }
          const [popup] = await Promise.all([page.waitForEvent('popup'), mobile ? link.tap() : page.keyboard.press('Enter')]);
          await popup.waitForLoadState();
          assert.equal(popup.url(), destination);
          assert.equal(await popup.evaluate(() => window.opener), null);
          await popup.close();
          assert.equal(page.url(), url);
          assert(await named.isVisible());
        }
        if (!mobile) {
          await raw.focus();
          // The viewer polls every eight seconds. Check the UI, not a response
          // that may have started just before this reply was stored.

          assert.equal((await api.post(agent + '/comments/1/reply', { headers, data: {
            text: 'One more reply.', state: 'wrote',
          }})).status(), 201);
          await panel.getByText('One more reply.', { exact: true }).waitFor({ timeout: 18000 });
          assert(await raw.evaluate(a => a === document.activeElement));
        }
        assert.deepEqual(errors, []);
        assert.deepEqual(unexpected, []);
        if (process.env.SHOTS) {
          await raw.scrollIntoViewIfNeeded();
          await panel.screenshot({ path: path.join(process.env.SHOTS, `${width}-${colorScheme}-${size}.png`) });
        }
        console.log(`${width} ${colorScheme} ${size}: literal draft, two live links, inert HTML/images and unchanged source`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
