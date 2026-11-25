// Disposable HTTPS loopback fixtures only: FIXTURE_JSON, CHROME, NODE_PATH;
// optional SHOTS directory. Google is simulated; no real account is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
const base = new URL(fixture.base);
assert.equal(base.protocol, 'https:');
assert(['localhost', '127.0.0.1'].includes(base.hostname));
assert(['code', 'google'].includes(fixture.mode));
const google = fixture.mode === 'google';
const at = route => new URL(route, base).href;

async function shot(p, label) {
  assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (process.env.SHOTS) await p.screenshot({fullPage:true, path:path.join(process.env.SHOTS, `${fixture.mode}-${label}.png`)});
}

// Design tokens are in CSS px before Text scaling. Probe real hit testing
// outside the visible box too, not just the pseudo-element's declaration.
async function compact(button, scale, visualHeight = 32) {
  await button.scrollIntoViewIfNeeded();
  const geometry = await button.evaluate((el, scale) => {
    const r = el.getBoundingClientRect(), before = getComputedStyle(el, '::before');
    const x = r.left+r.width/2, y = r.top+r.height/2;
    const points = [[x,r.top-6*scale],[x,r.bottom+6*scale],[r.left-2*scale,y],[r.right+2*scale,y]];
    return {h:r.height,w:r.width,top:before.top,bottom:before.bottom,
      center:(r.left+r.right)/2, hit:points.every(([px,py])=>el.contains(document.elementFromPoint(px,py)))};
  }, scale);
  assert(Math.abs(geometry.h - visualHeight*scale) < 1, JSON.stringify(geometry));
  assert(geometry.w + 1 >= 38*scale || visualHeight === 30, JSON.stringify(geometry));
  assert.equal(geometry.top, '-7px'); assert.equal(geometry.bottom, '-7px');
  assert(geometry.hit, `hit target must extend outside ${JSON.stringify(geometry)}`);
}

async function shortField(field, scale) {
  const geometry = await field.evaluate(el=>({h:el.getBoundingClientRect().height,
    font:getComputedStyle(el).fontSize, radius:getComputedStyle(el).borderTopLeftRadius}));
  assert(Math.abs(geometry.h - 40*scale) < 1, JSON.stringify(geometry));
  assert.equal(geometry.font,'16px'); assert.equal(geometry.radius,'10px');
}

async function pageFocus(page, button) {
  await page.waitForFunction(el=>el===document.activeElement,await button.elementHandle());
}

async function topUndo(page, title, scale, verifyFocus = true) {
  const button = page.getByRole('button',{name:`Undo deletion of ${title}`,exact:true});
  await button.waitFor();
  if (verifyFocus) await pageFocus(page,button);
  const box = await button.evaluate(el=>{
    const box = el.closest('.toast-box').getBoundingClientRect();
    return {center:(box.left+box.right)/2, width:innerWidth, top:box.top, bottom:box.bottom,
      headingTop:document.querySelector('.account-page h1').getBoundingClientRect().top,
      focused:el===document.activeElement};
  });
  assert(Math.abs(box.center - box.width/2) < 2, JSON.stringify(box));
  assert(box.top>=10 && box.top<=30 && box.bottom<=box.headingTop, JSON.stringify(box));
  if (verifyFocus) assert(box.focused);
  await compact(button,scale,30);
  return button;
}

async function googleSignIn(context, page, account) {
  const cdp = await context.newCDPSession(page);
  await cdp.send('Fetch.enable', { patterns:[{urlPattern:'https://accounts.google.com/*'}] });
  cdp.on('Fetch.requestPaused', async ({requestId, request}) => {
    try {
      const provider = new URL(request.url);
      assert.equal(provider.pathname, '/fixture-auth');
      const callback = new URL(provider.searchParams.get('redirect_uri'));
      assert.equal(callback.href, at('/auth/callback'));
      callback.searchParams.set('state', provider.searchParams.get('state'));
      callback.searchParams.set('code', account);
      await cdp.send('Fetch.fulfillRequest', { requestId, responseCode:303,
        responseHeaders:[{name:'Location', value:callback.href}] });
    } catch {
      await cdp.send('Fetch.failRequest', {requestId, errorReason:'Aborted'}).catch(()=>{});
    }
  });
  await page.getByRole('button', {name:'Sign in with Google', exact:true}).click();
  await page.waitForURL(at('/account'));
}

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME});
  try {
    let i=0;
    for (const width of [380, 1280]) for (const colorScheme of ['light','dark']) {
      for (const size of ['normal','larger']) {
        const label = `${width}-${colorScheme}-${size}`;
        const scale = size === 'larger' ? 1.3 : 1;
        const agent = fixture.agents[i++];
        assert.match(agent.name, /^fixture-[\w-]+$/);
        async function open() {
          const ctx = await browser.newContext({viewport:{width,height:800}, colorScheme,
            isMobile:width===380, hasTouch:width===380, ignoreHTTPSErrors:true});
          await ctx.addInitScript(({origin,size}) => {
            if (window === window.top && location.origin === origin) localStorage.setItem('agent-artifacts:text-size', size);
          }, {origin:base.origin,size});
          const p = await ctx.newPage();
          p.setDefaultTimeout(6500);
          return [ctx,p];
        }
        const [owner,p] = await open();
        const errors = [];
        p.on('pageerror', e => errors.push(e.message));
        const setup = await owner.request.post(at('/api/v1/me/account-claim'),
          {headers:{Authorization:`Bearer ${agent.key}`}});
        assert.equal(setup.status(), 201);
        const {code} = await setup.json();
        assert.equal((await p.goto(base.href)).status(), 200);
        if (google) {
          await compact(p.getByRole('button',{name:'Sign in with Google',exact:true}),scale);
          await googleSignIn(owner,p,agent.name);
        } else {
          await p.getByRole('heading',{name:'Connect a device',exact:true}).waitFor();
          assert.equal(await p.getByLabel('Connection code').count(),0);
          await shot(p,label+'-no-code');
        }
        await p.getByRole('link',{name:google?'Link an agent':'Link an agent to get started',exact:true}).click();
        const field = p.getByLabel('Setup code');
        await shortField(field,scale);
        await field.fill(code);
        await p.keyboard.press('Tab');
        const go = p.getByRole('button',{name:'Continue',exact:true});
        assert(await go.evaluate(e=>e===document.activeElement && e.matches(':focus-visible')
          && getComputedStyle(e).outlineStyle === 'solid'));
        await compact(go,scale);
        await p.keyboard.press('Enter');
        await p.getByRole('button',{name:'Link agent',exact:true}).click();
        await p.waitForURL(at('/account'));
        await p.getByRole('link',{name:'Launch notes',exact:true}).waitFor();
        assert(await p.getByRole('link',{name:'Draft outline',exact:true}).isVisible());
        await shot(p,label+'-account');
        await owner.grantPermissions(['clipboard-read','clipboard-write']);
        const share = p.getByRole('button',{name:'Share Launch notes',exact:true});
        await compact(share,scale);
        await compact(p.getByRole('button',{name:'Delete Launch notes',exact:true}),scale);
        await share.click();
        const dialog = p.getByRole('dialog',{name:'Share',exact:true});
        const link = at('/a/'+agent.file);
        assert.equal(await dialog.getByLabel('Link',{exact:true}).inputValue(),link);
        assert.equal(await dialog.getByText('Only people who can sign in can open this link.',{exact:true}).count(),google?1:0);
        await dialog.getByRole('button',{name:'Copy link',exact:true}).click();
        await dialog.getByText('Link copied.',{exact:true}).waitFor();
        assert.equal(await p.evaluate(()=>navigator.clipboard.readText()),link);
        await p.keyboard.press('Escape');
        assert(await share.evaluate(e=>e===document.activeElement));
        await p.getByRole('button',{name:'Delete Launch notes',exact:true}).click();
        const undoFile = await topUndo(p,'Launch notes',scale);
        assert(await p.getByText('Deleted 1 file · Launch notes',{exact:true}).isVisible());
        assert.equal((await owner.request.get(link)).status(),404);
        await shot(p,label+'-undo-file');
        if (width===380 && colorScheme==='light' && size==='normal') {
          await p.getByRole('button',{name:'Delete Draft outline',exact:true}).click();
          assert.equal(await p.locator('#account-deletions .toast-box:visible').count(),2);
          await topUndo(p,'Draft outline',scale);
          const earlierId = await undoFile.evaluate(e=>e.closest('form').id);
          assert.equal((await p.goto(at(`/account?focus-check=1#${earlierId}`))).status(),200);
          await pageFocus(p, undoFile);
          await undoFile.click();
          assert(await p.getByRole('link',{name:'Launch notes',exact:true}).isVisible());
          assert.equal(await p.locator('#account-deletions .toast-box:visible').count(),1);
          await p.getByRole('button',{name:'Undo deletion of Draft outline',exact:true}).click();
        } else await undoFile.click();
        await p.getByRole('link',{name:'Launch notes',exact:true}).waitFor();
        assert.equal((await owner.request.get(link)).status(),200);
        const project = at(await p.getByRole('link',{name:'Product launch',exact:true}).getAttribute('href'));
        const pluralExample = !google && colorScheme==='dark' && size===(width===380?'larger':'normal');
        const fileCount = pluralExample ? '10 files' : '1 file';
        if (pluralExample) {
          for (let j=1; j<=9; j++) {
            const added = await owner.request.post(at('/api/v1/artifacts'), {
              headers:{Authorization:`Bearer ${agent.key}`}, multipart:{
                project:new URL(project).pathname.split('/').at(-1),
                file:{name:`note-${j}.md`,mimeType:'text/markdown',buffer:Buffer.from('# Example note')},
              }
            });
            assert.equal(added.status(),201);
          }
          await p.reload();
        }
        await p.getByRole('button',{name:'Delete Product launch',exact:true}).click();
        await p.getByRole('heading',{name:'Delete project?',exact:true}).waitFor();
        assert(await p.getByText(fileCount,{exact:true}).isVisible());
        assert.equal((await owner.request.get(project)).status(),200);
        await shot(p,label+'-confirm-project');
        const confirmDelete = p.getByRole('button',{name:'Delete project',exact:true});
        await compact(confirmDelete,scale);
        await confirmDelete.click();
        const undoProject = await topUndo(p,'Product launch',scale);
        const countText = `Deleted 1 project, ${fileCount}`;
        const deletionStatus = p.getByText(`${countText} · Product launch`,{exact:true});
        assert(await deletionStatus.evaluate((el,count) => {
          const bounds = el.getBoundingClientRect(), range = new Range();
          range.setStart(el.firstChild,0);
          range.setEnd(el.firstChild,count.length);
          return [...range.getClientRects()].every(rect=>rect.x>=bounds.x-1 && rect.right<=bounds.right+1
            && rect.top>=bounds.top && rect.bottom<=bounds.bottom);
        },countText), 'the two-line toast must not clip the counts');
        if (pluralExample) await shot(p,label+'-undo-project');
        assert.equal((await owner.request.get(project)).status(),404);
        assert.equal((await owner.request.get(link)).status(),404);
        await undoProject.click();
        await p.getByRole('link',{name:'Product launch',exact:true}).waitFor();
        assert.equal((await owner.request.get(link)).status(),200);
        if (!google && width===380 && colorScheme==='light' && size==='normal') {
          await p.clock.install();
          await p.getByRole('button',{name:'Delete Draft outline',exact:true}).click();
          await topUndo(p,'Draft outline',scale);
          const remaining = Number(await p.locator('[data-undo-ms]').getAttribute('data-undo-ms'));
          assert(remaining > 0 && remaining <= 60000);
          await p.clock.fastForward(remaining + 100);
          await p.locator('#account-deletions').waitFor({state:'hidden'});
          assert(await p.getByRole('heading',{name:'Your account',exact:true}).evaluate(e=>e===document.activeElement));
          // Only the browser clock advanced. Reloading uses the real remaining time from the server.
          await p.reload();
          await p.getByRole('button',{name:'Undo deletion of Draft outline',exact:true}).click();
        }
        const newAgent = `${agent.name}-worker`;
        await p.getByText('Issue an agent key',{exact:true}).click();
        const agentName = p.getByLabel('Agent name',{exact:true});
        await shortField(agentName,scale);
        await agentName.fill(newAgent);
        await p.getByRole('button',{name:'Issue key',exact:true}).click();
        await p.getByRole('heading',{name:`Your key, ${newAgent}`,exact:true}).waitFor();
        const issued = (await p.locator('#setup').inputValue()).split('AGENT_ARTIFACTS_KEY=')[1];
        assert.match(issued,/^aa_[\w-]{43}$/);
        const credential = {headers:{Authorization:`Bearer ${issued}`}};
        assert.equal((await owner.request.get(at('/api/v1/me'),credential)).status(),200);
        await p.getByRole('link',{name:'Back to your account',exact:true}).click();
        await p.getByRole('button',{name:`Revoke key for ${newAgent}`,exact:true}).click();
        await p.getByRole('heading',{name:'Revoke this key?',exact:true}).waitFor();
        await shot(p,label+'-revoke-key');
        assert.equal((await owner.request.get(at('/api/v1/me'),credential)).status(),200);
        await p.getByRole('button',{name:'Revoke key',exact:true}).click();
        await p.getByText('Key revoked',{exact:true}).waitFor();
        assert.equal((await owner.request.get(at('/api/v1/me'),credential)).status(),401);
        if (google) {
          assert.equal(await p.getByRole('button',{name:'Connect another device',exact:true}).count(),0);
          await p.getByRole('button',{name:'Sign out',exact:true}).click();
          await p.getByRole('heading',{name:'Signed out',exact:true}).waitFor();
        } else {
          await p.getByRole('button',{name:'Connect another device',exact:true}).click();
          const pair = await p.locator('.account-code').textContent();
          assert.match(pair,/^[A-Z2-9]{4}-[A-Z2-9]{4}$/);
          const [receiver,r] = await open();
          assert.equal((await r.goto(base.href)).status(),200);
          const target = r.getByLabel('Connection code');
          await target.waitFor();
          await shortField(target,scale);
          await compact(r.getByRole('button',{name:'Connect this device',exact:true}),scale);
          await shot(r,label+'-has-code');
          await target.fill(pair.toLowerCase());
          await r.keyboard.press('Enter');
          await r.waitForURL(at('/account'));
          assert(await r.getByRole('link',{name:'Launch notes',exact:true}).isVisible());
          await p.goto(at('/account'));
          await p.getByRole('button',{name:'Sign out device',exact:true}).click();
          await r.goto(at('/account'));
          await r.waitForURL(base.href);
          assert.equal(await r.getByLabel('Connection code').count(),0);
          assert.equal((await r.goto(at('/a/'+agent.file))).status(),200);
          await p.getByText('Account recovery',{exact:true}).click();
          await p.getByRole('button',{name:'Get a recovery code',exact:true}).click();
          const saved = await p.getByLabel('Recovery code',{exact:true}).inputValue();
          assert.match(saved,/^[\w-]{43}$/);
          await r.goto(base.href);
          await r.getByRole('link',{name:'Use a recovery code',exact:true}).click();
          await shot(r,label+'-recover');
          const recoveryField = r.getByLabel('Recovery code',{exact:true});
          await shortField(recoveryField,scale);
          await recoveryField.fill(saved);
          await r.getByRole('button',{name:'Restore account',exact:true}).click();
          await r.waitForURL(at('/account'));
          assert(await r.getByRole('link',{name:'Launch notes',exact:true}).isVisible());
          await p.goto(at('/account'));
          await p.waitForURL(base.href);
          await receiver.close();
        }
        assert.deepEqual(errors,[]);
        await owner.close();
        console.log(`${fixture.mode} ${label}: account, Delete/Undo, scoped key revocation and ${google?'sign-out':'pairing and recovery'} OK`);
      }
    }
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
