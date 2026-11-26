// HTTPS loopback Workspace fixture, with a disposable artifact for each case.
// FIXTURE_JSON provides the signed browser cookie and Drive-backed test items.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON, 'utf8'));
const widths = [320, 380, 899, 900, 1280];
const themes = ['light', 'dark'];
const sizes = ['normal', 'large', 'larger'];

async function rect(action) {
  await action.waitFor();
  const box = await action.boundingBox();
  assert(Math.abs(box.height - 48) < 0.25, JSON.stringify(box));
  return box;
}

function same(a, b) {
  for (const key of ['x', 'y', 'width', 'height']) {
    assert(Math.abs(a[key] - b[key]) <= 1, `${key}: ${a[key]} vs ${b[key]}`);
  }
}

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  try {
    let i = 0;
    for (const width of widths) for (const colorScheme of themes) for (const text of sizes) {
      const item = fixture.items[i++];
      const desktop = width >= 900;
      const context = await browser.newContext({viewport: {width, height: 800}, colorScheme,
        ignoreHTTPSErrors: true, hasTouch: !desktop, isMobile: !desktop});
      try {
        await context.addCookies([{name: fixture.cookie_name, value: fixture.cookie,
          url: fixture.main, secure: true, httpOnly: true, sameSite: 'Lax'}]);
        await context.addInitScript(({colorScheme, text}) => {
          if (window === window.top) {
            localStorage.setItem('aa-theme', colorScheme);
            localStorage.setItem('agent-artifacts:text-size', text);
          }
        }, {colorScheme, text});
        const page = await context.newPage();
        page.setDefaultTimeout(7000);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('requestfailed', request => {
          if (request.resourceType() === 'stylesheet') errors.push(request.failure().errorText);
        });
        page.on('response', response => {
          if (response.request().resourceType() === 'stylesheet' && response.status() >= 400) {
            errors.push(`Stylesheet HTTP ${response.status()}`);
          }
        });
        assert.equal((await page.goto(item.url)).status(), 200);
        assert.deepEqual(errors, []);
        assert(await page.locator('link[href*="/site.css"]').evaluate(el => !!el.sheet));
        const foot = page.locator('#sheet-foot');
        const panel = page.locator('#comments');
        const open = () => (desktop ? page.locator('#comments-toggle') : page.locator('#strip-text')).click();
        const finish = foot.getByRole('button', {name: 'Finish review…', exact: true});
        const confirm = foot.getByRole('button', {name: 'Finish review', exact: true});
        const preview = foot.getByRole('button', {name: /^Review drafts · 1$/});
        const back = foot.getByRole('button', {name: 'Back', exact: true});
        const send = foot.getByRole('button', {name: 'Send comments', exact: true});
        const publish = foot.getByRole('button', {name: 'Publish comments', exact: true});
        const finalNote = panel.getByRole('textbox', {name: 'Final note, optional'});
        const general = panel.getByRole('textbox', {name: 'General comment, optional'});
        const publishOnly = foot.getByRole('radio', {name: 'Only publish'});
        const sendToAgent = foot.getByRole('radio', {name: 'Send to agent'});
        const action = async (button) => {
          const [res] = await Promise.all([page.waitForResponse(r => r.url() === item.url + '/review' &&
            r.request().method() === 'POST'), button.click()]);
          assert.equal(res.status(), 201);
          const data = res.request().postDataJSON();
          assert.equal(data.submission.version, item.version_url.split('/').at(-1));
          return data;
        };
        const add = async (text) => {
          await panel.getByRole('button', {name: 'Add comment', exact: true}).click();
          if (!desktop) await page.locator('#strip-text').click();
          const whole = panel.getByRole('button', {name: '+ About the whole file', exact: true});
          await whole.click();
          await panel.getByRole('textbox', {name: 'Comment', exact: true}).fill(text);
          await panel.getByRole('button', {name: 'Add', exact: true}).click();
          if (!desktop && !await preview.isVisible()) await page.locator('#strip-text').click();
          await preview.waitFor();
        };
        await open();
        assert.equal(await preview.count(), 0);
        const left = await rect(finish);
        await finish.click();
        await finalNote.fill('Final note');
        await back.click();
        assert(await finish.evaluate(el => el === document.activeElement));
        same(left, await rect(finish));
        await add('Draft content');
        same(left, await rect(finish));
        const right = await rect(preview);
        await preview.click();
        assert.equal(await panel.getByRole('link', {name: 'Reviewed version'}).getAttribute('href'), new URL(item.version_url).pathname);
        same(left, await rect(back));
        same(right, await rect(send));
        await general.fill('Round note');
        await general.blur(); // The recipient stays out of the way while typing.
        await publishOnly.click();
        assert(await publishOnly.evaluate(el => el === document.activeElement));
        same(right, await rect(publish));
        await back.click();
        assert(await preview.evaluate(el => el === document.activeElement));
        await finish.click();
        assert.equal(await finalNote.inputValue(), 'Final note');
        await panel.getByText('1 draft will be published.', {exact: true}).waitFor();
        assert.equal(await general.count(), 0);
        assert.equal(await publishOnly.count(), 0);
        same(left, await rect(back));
        same(right, await rect(confirm));
        await back.click();
        await preview.click();
        assert.equal(await publishOnly.getAttribute('aria-checked'), 'true');
        assert.equal(await general.inputValue(), 'Round note');
        const requestsEdits = text === 'large';
        if (requestsEdits) {
          await sendToAgent.click();
          same(right, await rect(send));
        }
        const published = await action(requestsEdits ? send : publish);
        assert.equal(published.submission.close, false);
        assert.equal(published.agent, requestsEdits ? true : undefined);
        assert.equal(published.text, 'Round note');
        if (!desktop) await open();
        await panel.getByText('Review notes update pending.', {exact: true}).waitFor();
        assert.equal(await panel.getByRole('link', {name: 'Read review notes'}).count(), 0);
        await finish.click();
        await panel.getByText(/^Open comments · 2/).waitFor();
        const finished = await action(confirm);
        assert.equal(finished.submission.close, true);
        assert.equal(finished.agent, undefined);
        assert.equal(finished.text, 'Final note');
        assert.notEqual(finished.submission.id, published.submission.id);
        assert.deepEqual(errors, []);
        console.log(`${width} ${colorScheme} ${text}: stable 48px slots, ${requestsEdits ? 'send' : 'publish-only'} and explicit close passed`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
