// Run with Playwright against a disposable loopback HTML artifact:
// AA_TEST_ARTIFACT=http://127.0.0.1:PORT/a/ID node tests/browser/mobile-review-caret.cjs
const assert = require('node:assert/strict');
const {randomBytes} = require('node:crypto');
const {chromium} = require('playwright-core');

const artifact = new URL(process.env.AA_TEST_ARTIFACT);
assert.equal(artifact.hostname, '127.0.0.1', 'Use a disposable loopback fixture');
assert.match(artifact.pathname, /^\/a\/[A-Za-z0-9_-]{22}$/);
const author = randomBytes(16).toString('base64url');
const general = 'Хочу размеры шрифтов в настройке. Но не давать настраивать каждый.\n'
  + 'Можно просто сделать в настройках пункт: размер шрифта и 4 варианта.\n'
  + 'Текущий пусть будет вторым, тот что был до этого первым. И два остальных крупнее.\n'
  + 'Менять сразу все шрифты при ';

async function geometry(area) {
  return area.evaluate(e => {
    const body = e.closest('.sbody');
    const rect = e.getBoundingClientRect();
    return {scroll: body.scrollTop, height: rect.height, bottom: rect.bottom,
      clip: body.getBoundingClientRect().bottom, focused: document.activeElement === e,
      selection: e.selectionEnd, value: e.value};
  });
}

(async () => {
  for (let n = 0; n < 5; n++) {
    const response = await fetch(`${artifact.href}/drafts`, {method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-Author-Key': author,
    }, body: JSON.stringify({author: 'Fixture reviewer', text: `Draft ${n + 1}`})});
    assert.equal(response.status, 201, await response.text());
  }
  const browser = await chromium.launch({executablePath: process.env.AA_CHROME || '/usr/bin/google-chrome'});
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 850},
      isMobile: true, hasTouch: true, colorScheme: 'dark'});
    await context.addInitScript(key => {
      if (window !== top) return;
      localStorage.setItem('agent-artifacts:author-key', key);
      localStorage.setItem('agent-artifacts:text-size', 'larger');
    }, author);
    await context.route('**/favicon.ico', route => route.fulfill({status: 204}));
    const page = await context.newPage();
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(artifact.href);
    await page.locator('#sheet-strip').getByRole('button', {name: 'Finish review'}).click();
    const area = page.getByRole('textbox', {name: 'General comment, optional'});
    await area.fill(general);
    // Approximate the keyboard's resized content area. CDP supplies actual
    // Chromium composition/commit events, not a physical Gboard session.
    await page.setViewportSize({width: 390, height: 452});
    await area.evaluate(e => { e.focus(); e.setSelectionRange(e.value.length, e.value.length); });
    const devtools = await context.newCDPSession(page);
    const word = 'изменении';
    await devtools.send('Input.imeSetComposition', {text: word, selectionStart: word.length, selectionEnd: word.length});
    await area.evaluate(e => { const body = e.closest('.sbody'); body.scrollTop = body.scrollHeight; });
    const before = await geometry(area);
    assert(before.focused && before.bottom <= before.clip + 1);
    await devtools.send('Input.insertText', {text: word + ' '});
    await page.waitForTimeout(350);
    const after = await geometry(area);
    assert(after.focused && Math.abs(after.scroll - before.scroll) < 2 && after.bottom <= after.clip + 1,
      `The completed word is clipped (scroll ${before.scroll} -> ${after.scroll})`);
    assert.equal(after.value, general + word + ' ');
    assert.equal(after.selection, after.value.length);

    const next = 'и после этого проверить перенос на следующую строку. '.repeat(2);
    await devtools.send('Input.insertText', {text: next});
    const wrapped = await geometry(area);
    assert(wrapped.height > after.height && wrapped.bottom <= wrapped.clip + 1);
    await page.waitForTimeout(8200);
    assert.deepEqual(await geometry(area), wrapped, 'A pause must not move or blur the editor');

    const early = await area.evaluate(e => {
      e.setSelectionRange(12, 12);
      const body = e.closest('.sbody');
      body.scrollTop += e.getBoundingClientRect().top - body.getBoundingClientRect().top - 44;
      return body.scrollTop;
    });
    await devtools.send('Input.insertText', {text: 'новое '});
    const edited = await geometry(area);
    assert.equal(edited.selection, 18);
    assert(edited.focused && Math.abs(edited.scroll - early) < 2, 'Editing earlier text must not jump to the end');
    assert.deepEqual(errors, []);
    console.log('Mobile Finish review: IME commit, wrapped line, idle pause and mid-text selection stayed visible');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
