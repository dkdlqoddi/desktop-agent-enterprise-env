// The context menu of the app's window, as an Electron menu template: Electron shows none of its own, so without it the
// mouse could not cut, copy or paste in a field (a link, a password, a token) or copy selected text. Editing only:
// nothing that opens DevTools. null where there is nothing to edit or copy.
function editMenu({ isEditable, selectionText = '', editFlags = {} }) {
  if (isEditable)
    return [
      { role: 'cut', label: '잘라내기', enabled: !!editFlags.canCut },
      { role: 'copy', label: '복사', enabled: !!editFlags.canCopy },
      { role: 'paste', label: '붙여넣기', enabled: !!editFlags.canPaste },
      { type: 'separator' },
      { role: 'selectAll', label: '모두 선택', enabled: !!editFlags.canSelectAll },
    ];
  if (selectionText.trim()) return [{ role: 'copy', label: '복사', enabled: !!editFlags.canCopy }];
  return null;
}

module.exports = { editMenu };
