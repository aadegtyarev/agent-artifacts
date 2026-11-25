const { test } = require('node:test');
const assert = require('node:assert/strict');
const keys = require('../../src/agent_artifacts/static/review-keys.js');

for (const key of ['c', 'C', '\u0441', '\u0421']) {
  test(`comment shortcuts work for ${key}`, () => {
    assert.equal(keys.command({ key }), 'start-comment');
    assert.equal(keys.command({ key, shiftKey: true }), 'toggle-comments');
  });
}

test('a normal full-screen key is unchanged; modifiers and composition win', () => {
  assert.equal(keys.command({ key: 'f' }), 'fullscreen');
  assert.equal(keys.command({ key: 'F', shiftKey: true }), null);
  assert.equal(keys.command({ key: 'x', code: 'KeyC' }), null);
  for (const guard of ['isComposing', 'defaultPrevented', 'repeat', 'altKey', 'ctrlKey', 'metaKey']) {
    assert.equal(keys.command({ key: 'c', [guard]: true }), null);
  }
  assert.equal(keys.command({ key: 'c', keyCode: 229 }), null);
  assert.equal(keys.command({ key: 'c', getModifierState: key => key === 'AltGraph' }), null);
});

test('editable controls and dialogs, including input under a shadow host, keep their keys', () => {
  const host = { closest: () => null };
  const input = { closest: selector => selector.includes('input') && input };
  const editor = { isContentEditable: true };
  assert.equal(keys.command({ key:'c', target:input }), null);
  assert.equal(keys.command({ key:'\u0441', target:editor }), null);
  assert.equal(keys.command({ key:'c', target:host, composedPath:() => [input, host] }), null);
  for (const role of ['dialog', 'textbox', 'combobox']) {
    assert.equal(keys.command({ key:'c', target:{ closest: selector => selector.includes(`role="${role}"`) } }), null);
  }
});
