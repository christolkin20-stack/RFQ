const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

function makeLocalStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(String(k), String(v)); },
    removeItem: (k) => { map.delete(String(k)); },
    key: (i) => Array.from(map.keys())[i] || null,
    get length() { return map.size; },
  };
}

function makeWindow() {
  const listeners = new Map();
  return {
    __RFQ_AUTH_INVALID__: false,
    addEventListener(type, cb) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(cb);
    },
    dispatchEvent(ev) {
      const arr = listeners.get(ev.type) || [];
      arr.forEach((cb) => cb(ev));
      return true;
    },
  };
}

async function run() {
  const file = path.resolve(__dirname, '../static/rfq/rfq_data.js');
  const code = fs.readFileSync(file, 'utf8');

  const localStorage = makeLocalStorage({
    rfq_projects_v1: JSON.stringify([{ id: 'p1', name: 'P1', items: [] }]),
  });
  const window = makeWindow();

  const fetch = async (url, opts = {}) => {
    if (url.startsWith('/api/locks/status?resource_key=project%3Ap1%3Aedit')) {
      return {
        ok: true,
        json: async () => ({
          ok: true,
          locked: true,
          is_owner: false,
          owner: { user_id: 99, display: 'alice' },
          expires_at: '2026-02-16T13:30:00Z',
        }),
      };
    }
    if (url === '/api/projects' && (!opts.method || opts.method === 'GET')) {
      return { ok: true, json: async () => ({ projects: [] }) };
    }
    if (url === '/api/projects/bulk' && opts.method === 'POST') {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (url === '/api/projects/reset' && opts.method === 'POST') {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    return { ok: false, status: 404, text: async () => 'not found' };
  };

  const context = {
    window,
    localStorage,
    fetch,
    console,
    setTimeout,
    clearTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    Date,
    Math,
    JSON,
    CustomEvent: function(type, init) { this.type = type; this.detail = (init && init.detail) || {}; },
  };

  vm.createContext(context);
  vm.runInContext(code, context);

  await new Promise(r => setTimeout(r, 20));

  const status = await window.RFQData.getProjectLockStatus('p1');
  assert.strictEqual(status.locked, true);
  assert.strictEqual(status.is_owner, false);
  assert.strictEqual(status.owner.display, 'alice');
  assert.strictEqual(status.expires_at, '2026-02-16T13:30:00Z');

  console.log('PASS test_rfq_lock_status_owner');
}

run().catch((err) => {
  console.error('FAIL test_rfq_lock_status_owner', err);
  process.exit(1);
});
