// Loopback HTML fixture: FIXTURE_JSON, CHROME, NODE_PATH; optional SHOTS.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
const base = new URL(fixture.base);
assert.equal(base.protocol, 'https:');
assert(['localhost', '127.0.0.1'].includes(base.hostname));
assert.match(fixture.id, /^[\w-]{22}$/);

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME});
  try {
    const profiles = [
      ...[1280,380].flatMap(width=>['light','dark'].flatMap(colorScheme=>
        ['normal','larger'].map(size=>({width,colorScheme,size,touch:width<900})))),
      {width:380,colorScheme:'light',size:'larger',touch:false},
      {width:1280,colorScheme:'dark',size:'normal',touch:true},
      {width:900,height:400,colorScheme:'dark',size:'larger',touch:false},
    ];
    for (const {width,height=820,colorScheme,size,touch} of profiles) {
      const desktop = width >= 900 && !touch;
      const label = `${fixture.session?'workspace':'public'}-${width}-${colorScheme}-${size}-${touch?'touch':'keyboard'}`;
      const context = await browser.newContext({viewport:{width,height}, colorScheme,
        hasTouch:touch, isMobile:touch && width<900, ignoreHTTPSErrors:true});
      if (fixture.session) await context.addCookies([{name:'__Host-aa-workspace-session',value:fixture.session,
        url:base.origin,httpOnly:true,secure:true,sameSite:'Lax'}]);
      await context.addInitScript(({origin,size})=>{
        if (window !== window.top || location.origin !== origin) return;
        localStorage.setItem('agent-artifacts:text-size',size);
        localStorage.setItem('agent-artifacts:name','Example Reviewer');
      },{origin:base.origin,size});
      const p = await context.newPage(), errors=[], writes=[];
      p.setDefaultTimeout(6500);
      p.on('pageerror',e=>errors.push(e.message));
      p.on('request',r=>{if (r.method()==='POST') writes.push(new URL(r.url()).pathname);});
      await p.goto(new URL(`/a/${fixture.id}`,base).href);
      if (width >= 900) await p.locator('#comments-toggle').click();
      else await p.locator('#sheet-strip').click();
      if (width === 900) {
        const handle = p.locator('#panel-handle');
        for (let i=0;i<10;i++) await handle.press('ArrowRight');
        assert.equal(Math.round((await p.locator('#comments').boundingBox()).width),300);
      }
      const add = p.getByRole('button',{name:'Add comment',exact:true});
      await add.click();
      if (width < 900 && await p.locator('#comments').getAttribute('data-pos') === 'strip') {
        await p.locator('#sheet-strip').click();
      }
      await p.getByRole('button',{name:'+ About the whole file',exact:true}).click();
      const field = p.getByRole('textbox',{name:'Comment',exact:true});
      await field.waitFor();
      assert.equal(await field.getAttribute('placeholder'),'Comment');
      const hint = p.getByText('Enter: save draft. Shift+Enter: new line.',{exact:true});
      assert.equal(await field.getAttribute('aria-describedby'),desktop?'compose-key-hint':null);
      if (desktop) {
        assert(await hint.isVisible());
        assert(await hint.evaluate(el=>{
          const footer = el.closest('.sfoot').getBoundingClientRect(), range = new Range();
          const composer = el.previousElementSibling.getBoundingClientRect();
          if (el.getBoundingClientRect().top < composer.bottom) return false;
          range.selectNodeContents(el);
          if (![...range.getClientRects()].every(r=>r.x>=footer.x && r.right<=footer.right
            && r.top>=footer.top && r.bottom<=Math.min(footer.bottom,innerHeight))) return false;
          return [...el.children].every(child=>{
            range.selectNodeContents(child);
            return range.getClientRects().length===1;
          });
        }), 'the hint should fit even in the narrow desktop panel with larger Text');
      } else assert.equal(await hint.count(),0);
      if (process.env.SHOTS && desktop) await p.screenshot({path:path.join(process.env.SHOTS,label+'-compose.png')});
      // Native Shift+Enter and the application's modified keys preserve selection and caret.
      await field.fill('First lineXYZSecond line');
      const select = () => field.evaluate(el=>el.setSelectionRange(10,13));
      await select();
      await field.press('Shift+Enter');
      assert.equal(await field.inputValue(),'First line\nSecond line');
      if (desktop) {
        assert(await hint.isVisible());
        for (const key of ['Control+Enter','Meta+Enter']) {
          await field.fill('First lineXYZSecond line');
          await select();
          await field.press(key);
          assert.equal(await field.inputValue(),'First line\nSecond line');
          assert.equal(await field.evaluate(el=>el.selectionStart),11);
          if (key === 'Control+Enter') {
            await field.press('Control+z');
            assert.equal(await field.inputValue(),'First lineXYZSecond line');
          }
        }
        await field.fill('x'.repeat(2000));
        await field.press('Control+Enter');
        assert.equal((await field.inputValue()).length,2000);
        await field.fill('First lineXYZSecond line');
        await select();
        await field.evaluate(el=>{el.ownerDocument.execCommand = ()=>false;});
        await field.press('Control+Enter');
        await field.evaluate(el=>{delete el.ownerDocument.execCommand;});
        assert.equal(await field.inputValue(),'First line\nSecond line');
        // Repeated key-down and IME acceptance must not save a draft.
        await field.evaluate(el=>{
          for (const flags of [{isComposing:true},{repeat:true}]) {
            el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true,...flags}));
          }
        });
      }
      if (touch) {
        await field.press('End');
        await field.press('Enter');
        assert.equal(await field.inputValue(),'First line\nSecond line\n');
        await p.keyboard.type('Third line');
      }
      assert.deepEqual(writes,[]);
      const input = await field.inputValue();
      const sent = p.waitForResponse(r=>r.request().method()==='POST' && r.url().endsWith('/drafts'));
      if (touch) await p.locator('#sheet-foot .send').click();
      else await field.press('Enter');
      const saved = await sent;
      assert.equal(saved.status(),201);
      assert.equal(saved.request().postDataJSON().text,input);
      assert.deepEqual(writes,[`/a/${fixture.id}/drafts`]);
      if (width >= 900) {
        await p.getByRole('button',{name:/^Review drafts/}).first().waitFor();
        if (process.env.SHOTS && desktop) await p.screenshot({path:path.join(process.env.SHOTS,label+'-draft.png')});
      }
      assert.deepEqual(errors,[]);
      console.log(label, ': Enter, line breaks and draft-only save OK');
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
