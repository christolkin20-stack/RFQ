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
    addEventListener(type, cb) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(cb);
    },
    dispatchEvent(ev) {
      const arr = listeners.get(ev.type) || [];
      arr.forEach((cb) => cb(ev));
      return true;
    },
    _emit(type, detail) {
      const arr = listeners.get(type) || [];
      arr.forEach((cb) => cb({ type, ...detail }));
    },
    _listeners: listeners,
  };
}

async function run() {
  const file = path.resolve(__dirname, '../static/rfq/rfq_data.js');
  const code = fs.readFileSync(file, 'utf8');

  const localStorage = makeLocalStorage({
    rfq_projects_v1: JSON.stringify([{ id: 'p1', name: 'Old', updated_at: '2026-01-01T00:00:00Z', items: [] }])
  });
  const window = makeWindow();

  let serverProjects = [{ id: 'p1', name: 'Server', updated_at: '2026-02-16T10:00:00Z', items: [] }];
  const fetch = async (url, opts = {}) => {
    if (url === '/api/projects' && (!opts.method || opts.method === 'GET')) {
      return {
        ok: true,
        clone() { return { json: async () => ({ projects: serverProjects }) }; },
        json: async () => ({ projects: serverProjects }),
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

  assert(window.RFQData, 'RFQData should be exposed on window');

  // wait for initial bootstrapFromServer()
  await new Promise(r => setTimeout(r, 20));
  let projects = window.RFQData.getProjects();
  assert.strictEqual(projects[0].name, 'Server', 'bootstrap should rehydrate from server');

  // mutation should touch version + signal keys
  const beforeVersion = localStorage.getItem('rfq_projects_version_v1');
  window.RFQData.updateProject({ ...projects[0], name: 'LocalEdit' });
  const afterVersion = localStorage.getItem('rfq_projects_version_v1');
  const signal = localStorage.getItem('rfq_sync_signal_v1');
  assert(afterVersion && afterVersion !== beforeVersion, 'project version stamp should change on update');
  assert(signal && signal.includes('update_project'), 'sync signal should include mutation action');

  // remote storage event should trigger refetch and update local project name
  serverProjects = [{ id: 'p1', name: 'RemoteTabEdit', updated_at: '2026-02-16T11:00:00Z', items: [] }];
  window._emit('storage', {
    key: 'rfq_sync_signal_v1',
    newValue: JSON.stringify({ tab: 'another-tab', at: Date.now(), type: 'mutation' }),
  });

  await new Promise(r => setTimeout(r, 260));
  projects = window.RFQData.getProjects();
  assert.strictEqual(projects[0].name, 'RemoteTabEdit', 'storage event should force server refetch');

  console.log('PASS test_rfq_live_sync');
}

run().catch((err) => {
  console.error('FAIL test_rfq_live_sync', err);
  process.exit(1);
});
