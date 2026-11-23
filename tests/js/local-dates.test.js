const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const source = path.resolve(__dirname, '../../src/agent_artifacts/static/local-dates.js');
function format(tz, method, instant, now) {
  const code = `const d = require(${JSON.stringify(source)}); console.log(d.${method}(${JSON.stringify(instant)}${now ? `, new Date(${JSON.stringify(now)})` : ''}));`;
  return execFileSync(process.execPath, ['-e', code], { env: { ...process.env, TZ: tz }, encoding: 'utf8' }).trim();
}
test('deadline uses the offset at the instant including DST and date boundary', () => {
  assert.equal(format('America/Los_Angeles', 'deadline', '2026-11-24T01:00:00Z'), 'Nov 23, 2026 at 17:00 UTC-08:00');
  assert.equal(format('America/Los_Angeles', 'deadline', '2026-07-24T01:00:00Z'), 'Jul 23, 2026 at 18:00 UTC-07:00');
  assert.equal(format('Asia/Tokyo', 'deadline', '2026-11-24T01:00:00Z'), 'Nov 24, 2026 at 10:00 UTC+09:00');
  assert.equal(format('UTC', 'deadline', '2026-11-24T01:00:00Z'), 'Nov 24, 2026 at 01:00 UTC');
});
test('older dates use local year across New Year', () => {
  assert.equal(format('America/Los_Angeles', 'date', '2026-01-01T01:00:00Z', '2026-01-03T00:00:00Z'), 'Dec 31, 2025');
  assert.equal(format('Asia/Tokyo', 'date', '2026-01-01T01:00:00Z', '2026-01-03T00:00:00Z'), 'Jan 1');
});

test('comment and project dates agree in each browser zone', () => {
  const model = path.resolve(__dirname, '../../src/agent_artifacts/static/comments-model.js');
  for (const tz of ['America/Los_Angeles', 'Asia/Tokyo']) {
    const code = `const date = require(${JSON.stringify(source)}); const model = require(${JSON.stringify(model)}); const now = Date.parse("2026-01-03T00:00:00Z"); const instant = "2026-01-01T01:00:00Z"; console.log(date.date(instant, new Date(now)) === model({comments: [], drafts: []}, {now: () => now}).when(instant));`;
    assert.equal(execFileSync(process.execPath, ['-e', code], {env: {...process.env, TZ: tz}, encoding: 'utf8'}).trim(), 'true');
  }
});
