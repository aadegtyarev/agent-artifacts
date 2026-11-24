const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../src/agent_artifacts/static/comments.js'), 'utf8');
const section = source.slice(source.indexOf('  class ApiError'), source.indexOf('  // --- Items'));
function setup(fetch) {
  const c = { fetch, artifact: 'fixture', asAuthor: {} };
  vm.createContext(c); vm.runInContext(section, c); return c;
}
const response = (status, data) => ({ status, ok: status >= 200 && status < 300, headers: {}, json: async () => { if (data instanceof Error) throw data; return data; } });
test('exchange uses only explicit safe copy and narrowly recognizes name refusals', async () => {
  for (const [status, data, expected, name] of [
    [400, { error: 'bad_request', message: 'X-Author-Key must be 22 URL-safe base64 characters' }, 'Try again.', false],
    [400, { error: 'bad_request', message: "anchor must be null or ['type']" }, 'Try again.', false],
    [400, { error: 'bad_request', message: 'author must be one line' }, 'Try again.', true],
    [400, { error: 'bad_request', message: 'author is longer than 64 characters' }, 'Try again.', true],
    [400, { error: 'bad_request', message: 'author secret internal failure' }, 'Try again.', false],
    [429, { message: 'secret' }, 'Wait a minute and try again.', false],
    [404, { message: 'secret' }, 'Refresh and try again.', false],
    [409, { error: 'too_many_drafts', message: 'secret' }, 'Delete a draft and try again.', false],
    [409, { error: 'too_many_comments' }, 'Remove a new comment and try again.', false],
    [409, { error: 'too_many_replies' }, 'Remove a reply and try again.', false],
    [409, { error: 'too_many_secret', message: 'secret' }, 'Try again.', false],
    [409, { error: { toString: null } }, 'Try again.', false],
    [409, { error: ['too_many_drafts'] }, 'Try again.', false],
    [500, { message: 'stack secret' }, 'Try again.', false],
    [502, new Error('bad JSON'), 'Try again.', false],
    [200, new Error('bad JSON'), 'Try again.', false],
  ]) {
    const c = setup(async () => response(status, data));
    await assert.rejects(c.exchange('POST', '/drafts', {}), e => {
      const copy = c.errorCopy(e, 'Could not save the draft.');
      assert.ok(copy.endsWith(expected), copy); assert.equal(e.nameRejected, name);
      if (status === 409 && typeof data.error !== 'string') assert.equal(copy, 'Could not save the draft. Try again.');
      assert.doesNotMatch(copy, /secret|author|anchor|502|stack/i); return true;
    });
  }
  const c = setup(async () => { throw new Error('offline secret'); });
  await assert.rejects(c.exchange('GET', '/comments'), e => {
    assert.equal(c.errorCopy(e, 'Could not load comments.'), 'Could not load comments. Check the connection and try again.'); return true;
  });
});

test('load, save and review handlers use local action copy, retaining inputs until success', () => {
  assert.match(source, /errorCopy\(e, "Could not load comments\."\)/);
  assert.match(source, /errorCopy\(e, "Could not save the draft\."\)/);
  assert.match(source, /errorCopy\(e, "Could not finish the review\."\)/);
  assert.doesNotMatch(source, /toast\([^\n]*e\.message/);
  assert.match(source, /await call\("POST", "\/drafts"[\s\S]*?fields\.compose\.value = ""[\s\S]*?catch \(e\)/);
  assert.match(source, /await call\("POST", "\/review"[\s\S]*?fields\.general\.value = ""[\s\S]*?catch \(e\)/);
});

test('draft failure preserves input and re-enables retry; name refusal retains correction path', async () => {
  const part = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
  const field = { value: 'unsaved words' }, notices = [], removed = [];
  const c = { S: { compose: { anchor: null }, drafts: [] }, fields: { compose: field },
    sendButton: { disabled: false }, savingDraft: false, name: 'Old', nameInput: { value: '' }, NAME_KEY: 'name',
    localStorage: { removeItem: x => removed.push(x) }, toast: x => notices.push(x),
    errorCopy: (e, action) => action + ' Try again.',
    call: async () => { throw { status: 400, nameRejected: true }; } };
  vm.createContext(c); vm.runInContext(part('  function nameRefused(e)', '  // --- Decisions and replies'), c);
  assert.equal(await c.addDraft(), false); assert.equal(field.value, 'unsaved words');
  assert.equal(c.sendButton.disabled, false); assert.equal(c.name, null);
  assert.equal(c.nameInput.value, 'Old'); assert.deepEqual(removed, ['name']);
  assert.equal(notices[0], 'That name cannot be used. Give another one.');
});

test('load failure shows safe copy and a later load recovers', async () => {
  let failing = true;
  const c = setup(async (url) => url.endsWith('/comments') && failing
    ? response(500, { message: 'SECRET' })
    : { ...response(200, url.endsWith('/comments') ? [{ n: 1 }] : [{ id: 'saved' }]), headers: { get: () => null } });
  Object.assign(c, { generation: 0, S: {}, noteFile() {}, toast: (text) => notices.push(text), render: () => renders++ });
  const notices = []; let renders = 0;
  await c.load();
  assert.equal(notices[0], 'Could not load comments. Try again.');
  assert.equal(renders, 0);
  failing = false;
  await c.load();
  assert.equal(c.S.comments[0].n, 1); assert.equal(c.S.drafts[0].id, 'saved'); assert.equal(renders, 1);
});

test('finish failure retains saved draft and text, enables retry, then publishes', async () => {
  let failing = true; const notices = [], payloads = [];
  const saved = { id: 'saved', kind: 'new', text: 'saved draft' };
  const c = setup(async (url, init) => {
    if (url.endsWith('/review')) {
      payloads.push(JSON.parse(init.body));
      return failing ? response(409, { error: 'too_many_comments', message: 'SECRET' }) : response(200, [{ n: 1 }]);
    }
    return { ...response(200, url.endsWith('/drafts') && failing ? [saved] : []), headers: { get: () => null } };
  });
  Object.assign(c, { generation: 0, S: { toAgent: false, comments: [], drafts: [saved] },
    fields: { general: { value: 'typed text' } }, sending: false, deciding: false, name: 'Tester',
    finishButton: { disabled: false, isConnected: true }, flushDeletes: async () => true,
    reviewCount: () => c.S.drafts.length, liveDrafts: () => c.S.drafts, noteFile() {}, render() {},
    toast: text => notices.push(text), nameRefused: () => false });
  vm.runInContext(source.slice(source.indexOf('  async function finish()'), source.indexOf('  // --- Fields')), c);
  await c.finish(); await new Promise(setImmediate);
  assert.equal(notices[0], 'Could not finish the review. This file cannot hold more comments. Remove a new comment and try again.');
  assert.equal(c.fields.general.value, 'typed text'); assert.equal(c.S.drafts[0].text, 'saved draft');
  assert.equal(c.finishButton.disabled, false); assert.equal(payloads[0].agent, undefined);
  failing = false; await c.finish(); await new Promise(setImmediate);
  assert.equal(notices.at(-1), 'Review published'); assert.equal(c.fields.general.value, '');
  assert.equal(c.S.drafts.length, 0); assert.equal(payloads.length, 2); assert.equal(payloads[1].text, 'typed text');
});
