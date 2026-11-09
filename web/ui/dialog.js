// Shared dialog primitive; no navigation, transport or application bootstrap.
export function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
let dialog;
export function ask({
  title = '确认操作',
  message = '',
  input,
  inputLabel = '名称',
  confirmLabel = '确认',
  danger = false,
}) {
  if (dialog?.open) return Promise.resolve(input === undefined ? false : null);
  if (!dialog) {
    dialog = element('dialog', null, 'ui-dialog');
    dialog.id = 'ui-dialog';
    document.body.append(dialog);
  }
  const previous = document.activeElement,
    form = element('form');
  form.method = 'dialog';
  const heading = element('h2', title);
  heading.id = 'ui-dialog-title';
  dialog.setAttribute('aria-labelledby', heading.id);
  const content = element('p', message),
    actions = element('div', null, 'ui-dialog-actions');
  const cancel = element('button', '取消'),
    confirm = element('button', confirmLabel);
  cancel.value = 'cancel';
  cancel.formNoValidate = true;
  confirm.value = 'confirm';
  confirm.className = danger ? 'ui-danger' : 'ui-primary';
  form.append(heading, content);
  let field;
  if (input !== undefined) {
    const label = element('label', inputLabel);
    field = element('input');
    field.value = input;
    field.required = true;
    field.maxLength = 60;
    label.append(field);
    form.append(label);
  }
  actions.append(cancel, confirm);
  form.append(actions);
  dialog.replaceChildren(form);
  dialog.returnValue = '';
  return new Promise((resolve) => {
    dialog.addEventListener(
      'close',
      () => {
        const accepted = dialog.returnValue === 'confirm';
        resolve(input === undefined ? accepted : accepted ? field.value.trim() : null);
        previous?.focus?.();
      },
      { once: true },
    );
    dialog.showModal();
    (field || cancel).focus();
  });
}
