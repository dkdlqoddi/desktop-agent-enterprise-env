const { test } = require('node:test');
const assert = require('node:assert/strict');
const { editMenu } = require('../electron/context-menu.cjs');

const flags = (value) => ({
  canCut: value,
  canCopy: value,
  canPaste: value,
  canSelectAll: value,
});
const items = (template) =>
  template.map((item) => (item.type === 'separator' ? '-' : `${item.label}:${item.enabled}`));

test('a field offers cut, copy, paste and select all as Chromium allows them', () => {
  assert.deepEqual(items(editMenu({ isEditable: true, editFlags: flags(true) })), [
    '잘라내기:true',
    '복사:true',
    '붙여넣기:true',
    '-',
    '모두 선택:true',
  ]);
  // A password field: Chromium allows only pasting and selecting into it.
  const password = editMenu({
    isEditable: true,
    selectionText: '',
    editFlags: { canCut: false, canCopy: false, canPaste: true, canSelectAll: true },
  });
  assert.deepEqual(items(password), [
    '잘라내기:false',
    '복사:false',
    '붙여넣기:true',
    '-',
    '모두 선택:true',
  ]);
  assert.deepEqual(
    password.filter((item) => item.role).map((item) => item.role),
    ['cut', 'copy', 'paste', 'selectAll'],
  );
});

test('selected text offers copy; elsewhere there is no menu, and never DevTools', () => {
  assert.deepEqual(
    items(editMenu({ isEditable: false, selectionText: '회의록', editFlags: flags(true) })),
    ['복사:true'],
  );
  assert.equal(
    editMenu({ isEditable: false, selectionText: '  \n', editFlags: flags(true) }),
    null,
  );
  assert.equal(editMenu({ isEditable: false, editFlags: {} }), null);
  for (const template of [
    editMenu({ isEditable: true, editFlags: flags(true) }),
    editMenu({ isEditable: false, selectionText: 'x', editFlags: flags(true) }),
  ])
    assert.ok(
      template.every((item) => !['toggleDevTools', 'reload', 'forceReload'].includes(item.role)),
    );
});
