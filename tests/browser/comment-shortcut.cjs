// Disposable loopback HTML fixture only. FIXTURE_JSON contains its entry URL,
// artifact ID and optional test Workspace cookie; NODE_PATH supplies Playwright.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
const base = new URL(fixture.base);
assert(['localhost', '127.0.0.1'].includes(base.hostname));
assert.match(fixture.id, /^[\w-]{22}$/);

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME });
  try {
    const context = await browser.newContext({ viewport:{width:1280,height:800}, ignoreHTTPSErrors:true });
    if (fixture.session) await context.addCookies([{name:'__Host-aa-workspace-session', value:fixture.session,
      url:base.origin, httpOnly:true, secure:true, sameSite:'Lax'}]);
    const p = await context.newPage(), errors = [];
    p.on('pageerror', e => errors.push(e.message));
    p.setDefaultTimeout(6000);
    const cdp = await context.newCDPSession(p);
    async function russian(shift = false) {
      const key = shift ? '\u0421' : '\u0441';
      const props = {key, code:'KeyC', windowsVirtualKeyCode:67, modifiers:shift ? 8 : 0};
      await cdp.send('Input.dispatchKeyEvent', {type:'keyDown', text:key, ...props});
      await cdp.send('Input.dispatchKeyEvent', {type:'keyUp', ...props});
    }
    const url = new URL(`/a/${fixture.id}`, base).href;
    const response = await p.goto(url);
    assert.equal(response.status(), 200);
    assert.equal(p.url(), url);
    const frame = p.frameLocator('#frame'), surface = frame.locator('#surface');
    const toggle = p.locator('#comments-toggle');
    const modes = (want) => p.waitForFunction(v => document.body.classList.contains('commenting') === v, want);
    const quiet = async () => {
      await p.waitForTimeout(100);
      assert.equal(await p.locator('body').evaluate(el=>el.classList.contains('commenting')),false);
    };
    await toggle.focus();
    if (process.env.PROBE_BASELINE === '1') {
      await russian(); await quiet();
      await surface.click(); await p.keyboard.press('c'); await quiet();
      console.log('Baseline reproduces both failures: Cyrillic in the viewer, Latin inside the file');
      return;
    }
    await russian(); await modes(true);
    await p.keyboard.press('Escape'); await modes(false);
    await russian(true);
    assert.equal(await toggle.getAttribute('aria-expanded'),'false'); // reading panel was open
    await russian(true);
    assert.equal(await toggle.getAttribute('aria-expanded'),'true'); await modes(false);
    await p.keyboard.press('c'); await modes(true);
    await p.keyboard.press('Escape'); await modes(false);

    // The sandbox uses the parent's width, not the width left beside the panel.
    await p.setViewportSize({width:960,height:800});
    assert((await p.locator('#frame').boundingBox()).width < 900);
    await toggle.focus();
    await surface.evaluate(() => window.parent.postMessage({type:'start-comment'}, '*'));
    await quiet(); // the file does not have the viewer's keyboard focus
    await surface.click();
    await p.waitForFunction(() => document.activeElement?.id === 'frame');
    await p.waitForTimeout(120);
    await p.keyboard.press('c'); await modes(true);
    await russian(); await modes(true); // start is idempotent, not a toggle
    await p.keyboard.press('Escape'); await modes(false);
    await surface.focus();
    await p.waitForFunction(() => document.activeElement?.id === 'frame');
    await russian(); await modes(true);
    await p.keyboard.press('Escape'); await modes(false);
    await surface.evaluate(el=>el.dispatchEvent(new KeyboardEvent('keydown',{key:'c',code:'KeyC',bubbles:true}))); await quiet();
    await p.keyboard.press('Control+c'); await quiet();
    const editor = frame.locator('#entry');
    await editor.click(); await p.keyboard.press('c'); await russian(); await quiet();
    assert.equal(await editor.inputValue(),'c\u0441');
    const editable = frame.locator('#editable');
    await editable.click(); await russian(); await quiet();
    assert.match(await editable.textContent(),/\u0441/);
    await frame.locator('#claimed-key').focus(); await p.keyboard.press('c'); await quiet();

    await p.setViewportSize({width:390,height:800});
    await surface.click();
    await p.keyboard.press('c'); await russian(); await quiet();
    assert.deepEqual(errors,[]);
    console.log(`${fixture.session?'Workspace':'Public'}: Latin/Cyrillic start in viewer and focused file; typing, modifiers, page handlers and narrow layout keep their keys`);
  } finally { await browser.close(); }
})().catch(e=>{ console.error(e); process.exitCode=1; });
