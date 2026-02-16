const fs = require('fs');
const path = require('path');
const assert = require('assert');

function run() {
  const file = path.resolve(__dirname, '../static/rfq/rfq.js');
  const code = fs.readFileSync(file, 'utf8');

  assert(code.includes('item-detail-lock-overlay'), 'Lock overlay element id must exist');
  assert(code.includes('Read-only mode (lock active)'), 'Overlay must show explicit read-only message');
  assert(code.includes('btn-item-detail-save') && code.includes("b.style.display = 'none'"), 'Save buttons must be hidden in read-only mode');
  assert(code.includes('getProjectLockStatus'), 'UI must query lock status');
  assert(code.includes('refreshItemDetailLockState') && code.includes('ensureItemDetailLockWatcher'), 'UI must refresh lock state automatically');

  console.log('PASS test_rfq_lock_readonly_ui_contract');
}

try {
  run();
} catch (err) {
  console.error('FAIL test_rfq_lock_readonly_ui_contract', err);
  process.exit(1);
}
