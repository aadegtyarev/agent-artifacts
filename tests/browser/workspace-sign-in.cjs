// Local TLS Workspace fixture only. Intercepts the synthetic OAuth provider;
// never visits Google or uses real Workspace credentials.
const assert = require('node:assert/strict');
const { chromium } = require('playwright-core');
const path = require('node:path');
const base = new URL(process.env.BASE_URL);
assert.equal(base.protocol, 'https:');
assert(['localhost', '127.0.0.1'].includes(base.hostname));
const artifact = `/a/${process.env.ARTIFACT_ID}`;
assert.match(artifact, /^\/a\/[\w-]{22}$/);
const screenshot = (page, label) => process.env.SHOTS &&
  page.screenshot({ path: path.join(process.env.SHOTS, `${label}.png`) });

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME });
  try {
    for (const width of [380, 1280]) for (const colorScheme of ['light', 'dark']) {
      for (const size of ['normal', 'larger']) {
        const label = `${width}-${colorScheme}-${size}`;
        const context = await browser.newContext({ viewport: { width, height: 720 },
          colorScheme, hasTouch: width === 380, isMobile: width === 380, ignoreHTTPSErrors: true });
        await context.addInitScript(({ size, origin }) => {
          if (window === window.top && location.origin === origin) {
            localStorage.setItem('agent-artifacts:text-size', size);
          }
        }, { size, origin: base.origin });
        let requests = 0, cancel = true;
        const p = await context.newPage(), errors = [];
        p.setDefaultTimeout(6000);
        // CDP also intercepts the target of a navigation redirect.
        const cdp = await context.newCDPSession(p);
        await cdp.send('Fetch.enable', { patterns: [{ urlPattern: 'https://accounts.google.com/*' }] });
        cdp.on('Fetch.requestPaused', async ({ requestId, request }) => {
          try {
            assert(!Object.keys(request.headers).some(key => key.toLowerCase() === 'referer'));
            const provider = new URL(request.url);
            assert.equal(provider.pathname, '/fixture-auth');
            const callback = new URL(provider.searchParams.get('redirect_uri'));
            assert.equal(callback.href, new URL('/auth/callback', base).href);
            callback.searchParams.set('state', provider.searchParams.get('state'));
            if (cancel) callback.searchParams.set('error', 'access_denied');
            else callback.searchParams.set('code', 'test-only');
            requests++;
            await cdp.send('Fetch.fulfillRequest', { requestId, responseCode: 303,
              responseHeaders: [{ name: 'Location', value: callback.href }] });
          } catch (error) {
            errors.push(error.message);
            await cdp.send('Fetch.failRequest', { requestId, errorReason: 'Aborted' }).catch(() => {});
          }
        });
        p.on('pageerror', e => errors.push(e.message));
        p.on('console', m => { if (m.type() === 'error' && /Content Security Policy/.test(m.text())) errors.push(m.text()); });
        assert.equal((await p.goto(new URL(artifact, base).href)).status(), 200);
        assert.equal(new URL(p.url()).pathname, '/auth/login');
        assert.equal(await p.locator('h1').textContent(), 'Sign in');
        assert.equal(requests, 0);
        const button = p.getByRole('button', { name: 'Sign in with Google', exact: true });
        const box = await button.boundingBox();
        assert(box.width > 44 && (width > 380 || box.height >= 44));
        assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await screenshot(p, `${label}-entry`);
        await p.keyboard.press('Tab');
        assert(await button.evaluate(el => el === document.activeElement && el.matches(':focus-visible')));
        await screenshot(p, `${label}-focus`);
        await p.keyboard.press('Enter');
        await p.getByRole('heading', { name: 'Could not sign in' }).waitFor();
        assert.equal(requests, 1);
        await screenshot(p, `${label}-retry`);
        cancel = false;
        await button.click();
        await p.waitForURL(new URL(artifact, base).href);
        await p.locator('#comments[data-workspace-author="Example Reviewer"]').waitFor({state:'attached'});
        assert.equal(requests, 2);
        await p.goto(base.href);
        assert.equal(await p.locator('h1').textContent(), 'Signed in');
        assert(await p.getByText('reviewer@wirenboard.com', {exact:true}).isVisible());
        await screenshot(p, `${label}-signed-in`);
        const logout = p.waitForResponse(r => new URL(r.url()).pathname === '/auth/logout');
        await p.getByRole('button', { name:'Sign out', exact:true }).click();
        assert.equal((await logout).status(), 303);
        await p.getByRole('heading', { name: 'Signed out' }).waitFor();
        const raw = await context.request.get(new URL(`${artifact}/download`, base).href);
        assert.equal(raw.status(), 401);
        assert.deepEqual(errors, []);
        await context.close();
        console.log(`${label}: focus, cancelled login, retry, return and sign out OK`);
      }
    }
  } finally { await browser.close(); }
})().catch(err => { console.error(err); process.exitCode = 1; });
