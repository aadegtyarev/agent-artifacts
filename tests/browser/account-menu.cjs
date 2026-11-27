// Disposable HTTPS loopback account fixture: FIXTURE_JSON (base, id, project),
// CHROME, NODE_PATH for playwright-core.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright-core');
const {base,id,project} = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
assert.match(base,/^https:\/\/127\.0\.0\.1:\d+$/);
assert.match(id,/^[\w-]{22}$/); assert.match(project,/^[\w-]{22}$/);

(async () => {
  const browser = await chromium.launch({ executablePath:process.env.CHROME });
  try {
    for (const width of [320, 390, 900, 1280]) for (const colorScheme of ['light','dark']) for (const size of ['large','larger']) {
      const touch = width < 900;
      const c = await browser.newContext({ viewport:{width,height:width === 320 ? 420 : 810}, colorScheme,
        isMobile:touch, hasTouch:touch, ignoreHTTPSErrors:true, reducedMotion:'reduce' });
      try {
        await c.addInitScript(text => { if (window === top) localStorage.setItem('agent-artifacts:text-size',text); },size);
        const p = await c.newPage(), errors=[], writes=[];
        p.setDefaultTimeout(7000);
        p.on('pageerror',e=>errors.push(e.message));
        p.on('request',r=>{ if (r.method() !== 'GET') writes.push(r.method()); });
        const click = b => touch ? b.tap() : b.click();
        const restore = b => b.evaluate(e=>e===document.activeElement);
        for (const surface of ['file','project']) {
          const url = `${base}/${surface === 'file' ? `a/${id}` : `p/${project}`}`;
          assert.equal((await p.goto(url)).status(),200);
          let text;
          if (surface === 'file') {
            await click(p.locator(touch ? '#strip-text' : '#comments-toggle'));
            await click(p.getByRole('button',{name:/A demo discussion/}));
            text=p.getByRole('textbox',{name:'Reply to the agent'});
            await text.fill('Unsent reply stays here');
          }
          const openAccount = async (trigger, isFull = false) => {
            if (touch) await trigger.tap();
            else await trigger.press('Enter');
            const account=p.getByRole('menuitem',{name:'Account',exact:true});
            assert.equal(await account.getAttribute('href'),'/account');
            assert.equal(await account.getAttribute('target'),'_blank');
            assert.equal(await account.getAttribute('rel'),'noopener noreferrer');
            assert.equal(await account.evaluate(el=>el.previousElementSibling.dataset.act),'settings');
            if (!touch) {
              await p.keyboard.press('End');
              assert(await restore(account));
              assert.equal(await account.locator('.fan-sq').evaluate(e=>getComputedStyle(e).outlineStyle),'solid');
            } else await account.scrollIntoViewIfNeeded();
            const box = await account.boundingBox();
            assert(Math.abs(box.height - 44 * (size === 'larger' ? 1.3 : 1.15)) < 0.5);
            assert(box.x >= 0 && box.x+box.width <= width+1);
            const [tab] = await Promise.all([p.waitForEvent('popup'), touch ? account.tap() : p.keyboard.press('Enter')]);
            await tab.waitForLoadState();
            assert.equal(tab.url(),`${base}/`); // unpaired browser is sent to its existing entry screen
            assert.equal(await tab.evaluate(()=>window.opener),null);
            await tab.getByRole('heading',{name:'Connect a device'}).waitFor();
            await tab.close();
            assert.equal(p.url(),url);
            assert.equal(await trigger.getAttribute('aria-expanded'),'false');
            assert(await restore(trigger));
            if (text && !isFull) assert.equal(await text.inputValue(),'Unsent reply stays here');
          };
          await openAccount(p.locator('#menu-button'));
          if (surface === 'project' && width !== 320) {
            await click(p.locator('#full-button'));
            const tip=p.locator('#float-tip-ok');
            if (await tip.isVisible()) await click(tip);
            await openAccount(p.locator('#float-button'), true);
            assert(await p.locator('body').evaluate(e=>e.classList.contains('full')));
          }
        }
        assert.deepEqual(errors,[]); assert.deepEqual(writes,[]);
        console.log(`${width} ${colorScheme} ${size}: Account opens from file and project without leaving the page or losing text`);
      } finally { await c.close(); }
    }
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
