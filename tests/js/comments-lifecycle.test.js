const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../src/agent_artifacts/static/comments.js'), 'utf8');
const part = (from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));

test('completed poll does not acknowledge; explicit visible open redraws strip and pins together', async () => {
  let unread = 1, strip = 1, pin = 1, marked = 0;
  const c = { id: 1 };
  const context = { document: { visibilityState: 'visible', activeElement: null },
    S: { comments: [], sel: 'c1', view: 'item', pos: 'm', edit: null, compose: null, order: ['c1'] },
    pollStopped: false, sending: false, fields: { reply: { value: '' } }, generation: 0, pollSeq: 0,
    fetchComments: async () => [c], items: () => [{ key: 'c1' }], itemOf: () => ({ c }),
    markSeen: () => { unread = 0; marked++; }, ordered: () => [{ key: 'c1' }],
    go: () => { strip = unread; pin = unread; }, toChatEnd() {}, revealSoon() {}, protectRetainedCompose: () => false,
    render: () => { strip = unread; pin = unread; }, body: {}, desk: { matches: false },
  };
  vm.createContext(context);
  vm.runInContext(part('  async function poll()', '  setInterval(poll'), context);
  vm.runInContext(part('  function openItem(key)', '  // At middle height'), context);
  await context.poll();
  assert.deepEqual([marked, strip, pin], [0, 1, 1]);
  context.document.visibilityState = 'hidden'; context.openItem('c1');
  assert.equal(marked, 0);
  context.document.visibilityState = 'visible'; context.openItem('c1');
  assert.deepEqual([marked, strip, pin], [1, 0, 0]);
  assert.doesNotMatch(source, /acknowledgeVisibleReply/);
});

test('visual height override follows visual-only resize, zoom and desktop', () => {
  const properties = {}, classes = new Set();
  const context = { visual: { height: 240, scale: 1 }, desk: { matches: false },
    document: { body: { style: { setProperty: (k, v) => { properties[k] = v; }, removeProperty: k => { delete properties[k]; } },
      classList: { toggle: (k, on) => on ? classes.add(k) : classes.delete(k) } } } };
  vm.createContext(context);
  vm.runInContext(part('  function syncVisualHeight()', '  if (visual) visual.addEventListener'), context);
  context.syncVisualHeight(); assert.equal(properties['--visual-height'], '240px'); assert.ok(classes.has('visual-compact'));
  context.visual.scale = 2; context.syncVisualHeight(); assert.equal(properties['--visual-height'], undefined); assert.equal(classes.size, 0);
  context.visual.scale = 1; context.desk.matches = true; context.syncVisualHeight(); assert.equal(properties['--visual-height'], undefined);
});

test('keyboard and accessibility click activate once and focus current heading or grip', () => {
  for (const desktop of [false, true]) {
    const handlers = {}, heading = { focus() { focused = 'heading'; } }, grip = { addEventListener() {}, focus() { focused = 'grip'; } };
    let focused, opens = 0;
    const stripText = { setAttribute() {}, addEventListener(type, fn) { handlers[type] = fn; }, click() { handlers.click({ target: this, detail: 0 }); }, closest() { return null; } };
    const strip = { addEventListener(type, fn) { handlers[type] = fn; } };
    const context = { stripText, strip, grip, desk: { matches: desktop }, body: { querySelector: () => heading },
      drag: { dragged: () => false }, setPanel: open => { assert.equal(open, true); opens++; } };
    vm.runInNewContext(part('  stripText.setAttribute("role"', '  // --- Keys'), context);
    handlers.keydown({ key: 'Enter', preventDefault() {} });
    assert.equal(opens, 1); assert.equal(focused, desktop ? 'heading' : 'grip');
    stripText.click(); assert.equal(opens, 2);
  }
});

test('fold focuses visible strip or immersive floating menu', () => {
  for (const visible of [true, false]) {
    let focused = '';
    const stripText = { getClientRects: () => visible ? [1] : [], focus: () => { focused = 'strip'; } };
    const float = { getClientRects: () => [1], focus: () => { focused = 'float'; } };
    const context = { stripText, $: () => float };
    vm.runInNewContext(part('  function focusFoldTarget()', '  // A typed reply or a changed edit'), context);
    context.focusFoldTarget(); assert.equal(focused, visible ? 'strip' : 'float');
  }
});

test('viewport token falls back to vh without dynamic viewport support or JS override', () => {
  const css = fs.readFileSync(path.join(__dirname, '../../src/agent_artifacts/static/site.css'), 'utf8');
  assert.match(css, /--viewport-height:\s*100vh/);
  assert.match(css, /@supports\s*\(height:\s*100dvh\)\s*\{\s*:root\s*\{\s*--viewport-height:\s*100dvh/);
  assert.match(css, /body\s*\{\s*height:\s*var\(--visual-height,\s*var\(--viewport-height\)\)/);
  assert.equal((css.match(/var\(--visual-height,\s*var\(--viewport-height\)\)/g) || []).length, 3);
  assert.doesNotMatch(css, /var\(--visual-height,\s*100dvh\)/);
});

test('seen counts survive two tabs and synchronize without acknowledging other threads', () => {
  const data = new Map([['aa-seen:art', JSON.stringify({ 3: 2 })]]);
  const storage = { get length() { return data.size; }, key: i => [...data.keys()][i],
    getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) };
  function tab() {
    let handler, renders = 0;
    const context = { artifact: 'art', localStorage: storage, S: {}, pending: new Set(),
      window: { addEventListener: (_, fn) => { handler = fn; } }, render: () => renders++,
      commentsModel: (_, { seen }) => ({ markSeen(c) { if ((seen[c.n] || 0) >= c.count) return false; seen[c.n] = c.count; return true; } }) };
    vm.runInNewContext(part('  const seenKey =', "  // An item's pin"), context);
    return { context, event: key => handler({ storageArea: storage, key, newValue: '1' }), get renders() { return renders; },
      seen: n => vm.runInNewContext(`seen[${n}] || 0`, context) };
  }
  const a = tab(), b = tab();
  assert.equal(a.seen(3), 2);
  a.context.markSeen({ n: 1, count: 2 });
  b.context.markSeen({ n: 2, count: 3 });
  b.event('aa-seen:art:1:2'); a.event('aa-seen:art:2:3');
  assert.deepEqual([a.seen(1), a.seen(2), b.seen(1), b.seen(2)], [2, 3, 2, 3]);
  assert.equal(a.seen(4), 0); assert.equal(a.renders, 1);
  // Stale acknowledgements of the same thread cannot lower a later count.
  a.context.markSeen({ n: 1, count: 5 }); b.context.markSeen({ n: 1, count: 4 });
  a.event('aa-seen:art:1:4'); b.event('aa-seen:art:1:5');
  const reloaded = tab(); assert.equal(reloaded.seen(1), 5); assert.equal(reloaded.seen(2), 3);
  data.set('aa-seen:art', '{invalid'); assert.equal(tab().seen(1), 5);
});

test('explicit close focuses the strip and preserves the draft; passive folding still respects a pending reply', () => {
  let click, focused = 0, rendered = 0;
  const context = { button: (_, fn) => { click = fn; return { append() {}, setAttribute() {} }; }, icon() {},
    S: { edit: null, sel: 'c1', view: 'item', pos: 'm', mode: true, compose: {} }, fields: { reply: { value: '' } },
    sheet: { contains: () => false }, document: { activeElement: null }, desk: { matches: false },
    render: () => rendered++, toast() {}, focusFoldTarget: () => focused++ };
  vm.createContext(context);
  vm.runInContext(part('  function foldButton()', '  function titled('), context);
  vm.runInContext(part('  function fold()', '  function focusFoldTarget()'), context);
  vm.runInContext(part('  function stopPlacement()', '  function setMode('), context);
  context.foldButton(); click(); assert.equal(focused, 1); assert.equal(context.S.pos, 'strip');
  assert.equal(context.S.mode, false); assert.equal(context.S.compose.retained, true);
  context.S.pos = 'm'; context.fields.reply.value = 'draft'; context.fold();
  assert.equal(context.S.pos, 'm'); assert.equal(focused, 1);
  context.fields.reply.value = ''; context.fold(); assert.equal(focused, 1); assert.equal(rendered, 3);
});

test('unavailable seen storage keeps acknowledgements in the current page', () => {
  const context = { artifact: 'art', S: {}, pending: new Set(), window: { addEventListener() {} },
    commentsModel: (_, { seen }) => ({ markSeen(c) { seen[c.n] = c.count; return true; } }) };
  Object.defineProperty(context, 'localStorage', { get() { throw new Error('denied'); } });
  vm.runInNewContext(part('  const seenKey =', "  // An item's pin"), context);
  context.markSeen({ n: 1, count: 2 });
  assert.equal(vm.runInNewContext('seen[1]', context), 2);
});

test('pending server count retains draft and Finish review in the strip', () => {
  for (const count of [1, 2]) for (const review of [0, 1]) {
    const stripText = { replaceChildren(...nodes) { this.textContent = nodes.join(''); } };
    const strip = { children: [stripText], append(node) { this.children.push(node); } };
    const context = { S: { loaded: false, comments: [], view: 'list' }, sheet: { dataset: { count: String(count) } },
      stripText, strip, desk: { matches: false }, reviewCount: () => review,
      groupCounts: () => ({}), button: text => text, el: (_, __, text) => text,
      toggle: { classList: { toggle() {} }, setAttribute() {} }, $: () => null, document: { querySelector: () => null } };
    vm.runInNewContext(part('  function renderStrip()', '  const views ='), context);
    context.renderStrip();
    assert.match(stripText.textContent, new RegExp(`${count} comment${count === 1 ? '' : 's'}`));
    if (review) { assert.match(stripText.textContent, /1 draft/); assert.ok(strip.children.includes('Finish review')); }
  }
});

test('comment attention follows Need you rather than draft or total counts', () => {
  for (const desktop of [false, true]) for (const need of [0, 2]) {
    const makeControl = () => ({ active: false, attributes: {}, classList: { toggle() {} } });
    const toggle = makeControl(), menu = makeControl(), floating = makeControl();
    for (const control of [toggle, menu, floating]) {
      control.classList.toggle = (_, value) => { control.active = value; };
      control.setAttribute = (key, value) => { control.attributes[key] = value; };
      control.removeAttribute = key => { delete control.attributes[key]; };
    }
    const stripText = { replaceChildren() {} };
    const context = { S: { loaded: true, comments: [], view: 'list' }, sheet: { dataset: {} },
      stripText, strip: { children: [stripText], append() {} }, desk: { matches: desktop },
      reviewCount: () => 3, groupCounts: () => ({ need, agent: 5 }), button: text => text,
      el: (_, __, text) => text, toggle, $: id => id === 'float-button' ? floating : null, document: { querySelector: () => menu } };
    vm.runInNewContext(part('  function renderStrip()', '  const views ='), context);
    context.renderStrip();
    const active = desktop && need > 0;
    for (const control of [toggle, menu, floating]) {
      assert.equal(control.active, active);
      assert.equal(control === toggle ? control.attributes['aria-label'].includes('needs your attention') : control.attributes['aria-describedby'] === 'comment-attention', active);
    }
  }
});

test('pending list keeps whole-file compose, local drafts and Finish review on both layouts', () => {
  for (const desktop of [false, true]) for (const review of [0, 1]) {
    const draft = { draft: true };
    const context = { S: { loaded: false, mode: true }, sheet: { dataset: { count: '1' } },
      desk: { matches: desktop }, stored: true, titled: text => text, addComment: {}, reviewCount: () => review,
      items: () => review ? [draft] : [], groups: () => review ? [{ id: 'drafts', title: 'Drafts', items: [draft] }] : [],
      FOLDED: new Set(), row: () => 'draft row', status: () => '', finishBar: () => 'Finish review',
      el: (_, __, text) => text === undefined ? { append(x) { this.child = x; } } : text,
      button: text => text };
    vm.createContext(context);
    vm.runInContext(part('  const views =', '  function toWhom()') + '\nthis.list = views.list;', context);
    const [content, footer] = context.list();
    assert.equal(content[0], 'Loading comments…');
    assert.ok(content.some(node => node && node.child === '+ About the whole file'));
    assert.equal(content.includes('draft row'), !!review);
    assert.equal(footer.includes('Finish review'), !!review);
  }
});
