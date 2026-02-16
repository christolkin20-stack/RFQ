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
    _dump: () => Object.fromEntries(map.entries()),
  };
}

function makeWindow() {
  const listeners = new Map();
  return {
    __RFQ_AUTH_INVALID__: false,
    __RFQ_SESSION_SCOPE__: 'u1:user:company:buyer',
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

function makeFetch(serverProjectsRef) {
  return async (url, opts = {}) => {
    if (url === '/api/projects' && (!opts.method || opts.method === 'GET')) {
      return {
        ok: true,
        clone() { return { json: async () => ({ projects: serverProjectsRef.value }) }; },
        json: async () => ({ projects: serverProjectsRef.value }),
      };
    }
    if (url === '/api/projects/bulk' && opts.method === 'POST') {
      return {
        ok: true,
        clone() { return { json: async () => ({ ok: true }) }; },
        json: async () => ({ ok: true }),
      };
    }
    if (url === '/api/projects/reset' && opts.method === 'POST') {
      return {
        ok: true,
        clone() { return { json: async () => ({ ok: true }) }; },
        json: async () => ({ ok: true }),
      };
    }
    return { ok: false, status: 404, clone() { return { json: async () => ({ error: 'not found' }) }; }, text: async () => 'not found' };
  };
}

async function mountRFQData(localStorage, serverProjectsRef) {
  const file = path.resolve(__dirname, '../static/rfq/rfq_data.js');
  const code = fs.readFileSync(file, 'utf8');
  const window = makeWindow();
  const context = {
    window,
    localStorage,
    fetch: makeFetch(serverProjectsRef),
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
  return window.RFQData;
}

async function run() {
  const serverProjectsRef = {
    value: [{
      id: 'uat_c1_proj1',
      name: 'UAT project',
      server_updated_at: '2026-02-16T11:00:00Z',
      updated_at: '2026-02-16T11:00:00Z',
      items: [{ id: '111222', qty_1: 2 }],
    }],
  };

  // Tab A has stale local qty_1=1 and stale pending draft
  const lsA = makeLocalStorage({
    rfq_projects_v1: JSON.stringify([{ id: 'uat_c1_proj1', items: [{ id: '111222', qty_1: 1 }] }]),
    rfq_sync_queue_v1: '[]',
    rfq_project_draft_v1_uat_c1_proj1: JSON.stringify({
      pending: true,
      sessionScope: 'u1:user:company:buyer',
      baseVersion: '2026-02-16T10:00:00Z',
      project: { items: [{ id: '111222', qty_1: 1 }] },
    }),
  });

  // Tab B has stale local qty_1=2 and stale pending draft
  const lsB = makeLocalStorage({
    rfq_projects_v1: JSON.stringify([{ id: 'uat_c1_proj1', items: [{ id: '111222', qty_1: 2 }] }]),
    rfq_sync_queue_v1: '[]',
    rfq_project_draft_v1_uat_c1_proj1: JSON.stringify({
      pending: true,
      sessionScope: 'u1:user:company:buyer',
      baseVersion: '2026-02-16T10:00:00Z',
      project: { items: [{ id: '111222', qty_1: 2 }] },
    }),
  });

  const rfqA = await mountRFQData(lsA, serverProjectsRef);
  const rfqB = await mountRFQData(lsB, serverProjectsRef);

  // Simulate F5/bootstrap in both tabs => both must converge to canonical server qty_1=2
  await rfqA.bootstrapFromServer();
  await rfqB.bootstrapFromServer();

  const a = rfqA.getProjects()[0];
  const b = rfqB.getProjects()[0];

  assert.strictEqual(a.items[0].qty_1, 2, 'Tab A must converge to canonical server qty_1');
  assert.strictEqual(b.items[0].qty_1, 2, 'Tab B must converge to canonical server qty_1');
  assert.strictEqual(lsA.getItem('rfq_project_draft_v1_uat_c1_proj1'), null, 'Tab A stale draft key should be cleared when queue empty');
  assert.strictEqual(lsB.getItem('rfq_project_draft_v1_uat_c1_proj1'), null, 'Tab B stale draft key should be cleared when queue empty');

  // Strict guard positive check: only same session + same version + pending queue can apply
  lsA.setItem('rfq_sync_queue_v1', JSON.stringify([{ at: Date.now() }]));
  lsA.setItem('rfq_project_draft_v1_uat_c1_proj1', JSON.stringify({
    pending: true,
    sessionScope: 'u1:user:company:buyer',
    baseVersion: '2026-02-16T11:00:00Z',
    project: { items: [{ id: '111222', qty_1: 9 }] },
  }));
  await rfqA.bootstrapFromServer();
  assert.strictEqual(rfqA.getProjects()[0].items[0].qty_1, 9, 'Strict-guarded pending draft may apply only when session/version match and queue non-empty');

  console.log('PASS test_rfq_stale_overlay_regression');
}

run().catch((err) => {
  console.error('FAIL test_rfq_stale_overlay_regression', err);
  process.exit(1);
});
