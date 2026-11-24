const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../src/agent_artifacts/static/project.js'), 'utf8');

function project() {
  const docEvents = new Map(), windowEvents = new Map(), timers = new Map();
  const requests = [];
  const row = {
    dataset: {id: 'file'}, child: null,
    append(dot) { assert.equal(this.child, null); this.child = dot; },
    querySelector() { return this.child; },
  };
  const list = {dataset: {project: 'project'}, querySelectorAll: () => [row]};
  const document = {
    hidden: false,
    getElementById: id => id === 'files' ? list : null,
    addEventListener: (name, fn) => docEvents.set(name, fn),
    createElement: () => ({setAttribute() {}, remove() { row.child = null; }}),
  };
  let nextTimer = 0, summary = {file: {waiting_you: 0, drafts: 0}};
  vm.runInNewContext(source, {
    document,
    window: {addEventListener: (name, fn) => windowEvents.set(name, fn)},
    localStorage: {getItem: () => 'a'.repeat(22)},
    fetch: async (url, options) => {
      requests.push({url, options});
      const value = summary;
      return {ok: !!value, json: async () => value};
    },
    setTimeout: (fn, delay) => {
      assert.equal(delay, 8000);
      const id = ++nextTimer;
      timers.set(id, fn);
      return id;
    },
    clearTimeout: id => timers.delete(id),
  });
  return {
    row, document, requests, timers, docEvents, windowEvents,
    set: value => { summary = value; },
    tick: () => {
      assert.equal(timers.size, 1);
      const [id, timer] = timers.entries().next().value;
      timers.delete(id);
      return timer();
    },
  };
}

const turn = () => new Promise(resolve => setImmediate(resolve));

test('project dots appear and clear in place, and survive a failed refresh', async () => {
  const p = project();
  await turn();
  assert.equal(p.requests.length, 1);
  assert.equal(p.requests[0].url, '/p/project/summary');
  assert.equal(p.requests[0].options.headers['X-Author-Key'], 'a'.repeat(22));
  assert.equal(p.row.child, null);
  p.set({file: {waiting_you: 1, drafts: 0}});
  await p.tick();
  const dot = p.row.child;
  assert(dot);
  await p.tick();
  assert.equal(p.row.child, dot);
  p.set(null);
  await p.tick();
  assert.equal(p.row.child, dot);
  p.set({file: {waiting_you: 0, drafts: 0}});
  await p.tick();
  assert.equal(p.row.child, null);
});

test('the hidden project stops polling and refreshes on return', async () => {
  const p = project();
  await turn();
  p.document.hidden = true;
  await p.docEvents.get('visibilitychange')();
  assert.equal(p.timers.size, 0);
  assert.equal(p.requests.length, 1);
  p.set({file: {waiting_you: 0, drafts: 1}});
  p.document.hidden = false;
  await p.docEvents.get('visibilitychange')();
  assert.equal(p.requests.length, 2);
  assert(p.row.child);
  p.windowEvents.get('pageshow')({persisted: true});
  await turn();
  assert.equal(p.requests.length, 3);
  assert.equal(p.timers.size, 1);
});
