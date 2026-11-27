// Loopback HTML fixture: FIXTURE_JSON (base and id, with a seeded comment), CHROME and NODE_PATH.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_JSON));
const base = new URL(fixture.base);
assert(['localhost', '127.0.0.1'].includes(base.hostname));
assert.match(fixture.id, /^[\w-]{22}$/);

async function intact(action, sheet) {
  await action.waitFor();
  const outer = await sheet.boundingBox();
  const r = await action.boundingBox();
  assert(r.width > 0 && r.height >= 32);
  assert(r.x >= outer.x && r.x + r.width <= outer.x + outer.width + 1);
  assert(await action.evaluate(b => {
    const box = b.getBoundingClientRect();
    return Array.from(b.children).filter(e => e.getClientRects().length).every(e => {
      const ink = e.getBoundingClientRect();
      return ink.left >= box.left && ink.right <= box.right;
    });
  }), 'label and shortcut fit inside the visual button');
  assert.equal(await sheet.getByRole('button', { name: 'Stop adding comments', exact: true }).count(), 1);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME });
  try {
    for (const width of [320, 390, 900, 1280]) for (const colorScheme of ['light', 'dark']) for (const text of ['large', 'larger']) {
      const touch = width < 900;
      const context = await browser.newContext({ viewport: { width, height: 800 }, colorScheme,
        hasTouch: touch, isMobile: touch });
      try {
        await context.addInitScript(size => { if (window === window.top) localStorage.setItem('agent-artifacts:text-size', size); }, text);
        const p = await context.newPage(), errors = [], writes = [];
        p.setDefaultTimeout(7000);
        p.on('pageerror', e => errors.push(e.message));
        p.on('request', req => { if (req.method() !== 'GET') writes.push(req.method()); });
        assert.equal((await p.goto(new URL(`/a/${fixture.id}`, base).href)).status(), 200);
        const panel = p.locator('#comments');
        const strip = p.locator('#strip-text');
        const start = panel.getByRole('button', { name: 'Add comment', exact: true });
        const stop = panel.getByRole('button', { name: 'Stop adding comments', exact: true });
        const press = control => touch ? control.tap() : control.press('Enter');
        const mode = enabled => p.waitForFunction(want => document.body.classList.contains('commenting') === want, enabled);
        const focus = control => control.evaluate(e => e === document.activeElement);
        await (touch ? strip.tap() : p.locator('#comments-toggle').click());
        if (width === 900) {
          for (let i = 0; i < 10; i++) await p.locator('#panel-handle').press('ArrowRight');
          assert.equal(Math.round((await panel.boundingBox()).width), 300);
        }
        await press(start);
        await mode(true);
        await intact(stop, panel);
        assert.equal(await stop.locator('kbd').isVisible(), !touch);
        assert.equal(await stop.locator('kbd').textContent(), 'Esc');
        assert.equal(await p.locator('[data-act="comment"]').getAttribute('aria-checked'), 'true');
        const shot = async label => {
          if (process.env.SHOTS) await panel.screenshot({ path: path.join(process.env.SHOTS, `${width}-${colorScheme}-${text}-${label}.png`) });
        };
        if (touch) { await shot('strip'); await strip.tap(); }
        await intact(stop, panel);
        await shot('list');

        await press(panel.getByRole('button', {name:/Check the placement control/}));
        await intact(stop, panel);
        await press(stop);
        await mode(false);
        assert(await panel.getByText('Check the placement control.', { exact: true }).isVisible());
        assert(await panel.isVisible());
        if (!touch) assert(await focus(panel.locator('.nav .lbl')));
        if (touch) await panel.locator('.nav .lbl').tap();
        else await panel.getByRole('button', {name:'‹ All',exact:true}).click();
        await press(start);
        if (touch) await strip.tap();
        await press(panel.getByRole('button', { name: '+ About the whole file', exact: true }));
        const field = panel.getByRole('textbox', { name: 'Comment', exact: true });
        await field.fill('Unsent text');
        await intact(stop, panel);
        await shot('compose');
        if (touch) await stop.tap();
        else {
          await field.focus();
          await field.evaluate(el => el.setSelectionRange(2, 4));
          await field.press('Escape');
          assert(await focus(field));
          assert.deepEqual(await field.evaluate(el => [el.selectionStart, el.selectionEnd]), [2, 4]);
        }
        await mode(false);
        assert.equal(await field.inputValue(), 'Unsent text');
        assert(await field.isVisible());
        await shot('stopped');
        assert.deepEqual(writes, []);
        assert.deepEqual(errors, []);
        console.log(`${width} ${colorScheme} ${text}: visible stop, intact thread, panel and unsent text, no writes`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
