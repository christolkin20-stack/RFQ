(function () {
  "use strict";

  // =========================================================
  // RFQData
  // - single source of truth for storage + shared constants
  // - safe defaults so rfq.js never crashes on first load
  // =========================================================

  const LS_KEYS = {
    PROJECTS: "rfq_projects_v1",
    ACTIVE_PROJECT: "rfq_active_project_id",
    PROJECTS_VERSION: 'rfq_projects_version_v1',
    SYNC_SIGNAL: 'rfq_sync_signal_v1',
  };

  const safeJsonParse = (value, fallback) => {
    try {
      if (!value) return fallback;
      return JSON.parse(value);
    } catch (e) {
      return fallback;
    }
  };

    const PROJECT_STATUS_KEY = "rfq_project_status_options_v1";

  const getProjectStatusOptions = () => {
    const def = ["Created", "In process", "Done"];
    const arr = safeJsonParse(localStorage.getItem(PROJECT_STATUS_KEY), null);
    if (!Array.isArray(arr) || arr.length === 0) return def;
    // sanitize + unique
    const out = [];
    const seen = new Set();
    arr.forEach(x => {
      const s = String(x || "").trim();
      if (!s) return;
      const key = s.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push(s);
    });
    return out.length ? out : def;
  };

  const addProjectStatusOption = (name) => {
    const s = String(name || "").trim();
    if (!s) return false;
    const arr = getProjectStatusOptions();
    const exists = arr.some(x => String(x).toLowerCase() === s.toLowerCase());
    if (exists) return true;
    arr.push(s);
    localStorage.setItem(PROJECT_STATUS_KEY, JSON.stringify(arr));
    return true;
  };

const nowIso = () => new Date().toISOString();

  const _thisTabId = `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;

  const uid = () => {
    // good enough unique id for localStorage
    const r = Math.random().toString(16).slice(2);
    return `${Date.now()}_${r}`;
  };

  const saveProjects = (projects) => {
    localStorage.setItem(LS_KEYS.PROJECTS, JSON.stringify(Array.isArray(projects) ? projects : []));
  };

  const _emitMutationSignal = (type, projectId) => {
    const at = Date.now();
    try { localStorage.setItem(LS_KEYS.PROJECTS_VERSION, String(at)); } catch (e) {}
    try {
      localStorage.setItem(LS_KEYS.SYNC_SIGNAL, JSON.stringify({
        tab: _thisTabId,
        at,
        type: String(type || 'mutation'),
        projectId: projectId ? String(projectId) : '',
      }));
    } catch (e) {}
  };


  // =========================================================
  // Server Sync (Django)
  // - keeps LocalStorage as cache, but persists to /api/projects
  // - designed to be non-blocking (does not change rfq.js flow)
  // =========================================================

  const API = {
    PROJECTS: '/api/projects',
    BULK: '/api/projects/bulk',
    RESET: '/api/projects/reset',
  };

  const _fetchJson = (url, opts) => {
    try {
      return fetch(url, {
        method: (opts && opts.method) || 'GET',
        headers: {
          'Content-Type': 'application/json',
          ...(opts && opts.headers ? opts.headers : {}),
        },
        credentials: 'same-origin',
        body: opts && opts.body !== undefined ? opts.body : undefined,
      }).then(async (r) => {
        if (r.ok) return r.json();
        let text = '';
        let body = null;
        try {
          text = await r.text();
          body = text ? JSON.parse(text) : null;
        } catch (e) {
          body = null;
        }
        const err = new Error((body && body.error) || text || ('HTTP ' + r.status));
        err.status = r.status;
        err.body = body;
        throw err;
      });
    } catch (e) {
      return Promise.reject(e);
    }
  };

  const mergeProjectsById = (localArr, serverArr) => {
    const out = new Map();
    (Array.isArray(localArr) ? localArr : []).forEach(p => { if (p && p.id) out.set(String(p.id), p); });
    (Array.isArray(serverArr) ? serverArr : []).forEach(p => {
      if (!p || !p.id) return;
      const key = String(p.id);
      const existing = out.get(key);
      if (!existing) { out.set(key, p); return; }
      const ta = Date.parse(existing.server_updated_at || existing.data_version || existing.updated_at || existing.created_at || '') || 0;
      const tb = Date.parse(p.server_updated_at || p.data_version || p.updated_at || p.created_at || '') || 0;
      out.set(key, tb >= ta ? p : existing);
    });
    return Array.from(out.values());
  };

  let _syncTimer = null;
  let _syncInFlight = false;
  const _dirtyProjectIds = new Set();

  const LOCK = {
    ACQUIRE: '/api/locks/acquire',
    HEARTBEAT: '/api/locks/heartbeat',
    RELEASE: '/api/locks/release',
    TTL_SEC: 180,
  };

  const _projectLockTimers = new Map();

  const _lockResourceKey = (projectId) => `project:${String(projectId)}:edit`;

  const _currentVersion = (project) => String(project?.server_updated_at || project?.data_version || '');

  const _withBaseVersion = (project) => ({ ...project, base_version: _currentVersion(project) });

  const _ensureProjectLock = async (projectId) => {
    if (!projectId) return false;
    const resource_key = _lockResourceKey(projectId);
    const res = await _fetchJson(LOCK.ACQUIRE, {
      method: 'POST',
      body: JSON.stringify({ resource_key, project_id: String(projectId), context: 'project-data', ttl_sec: LOCK.TTL_SEC }),
    });
    if (!res || !res.acquired) return false;

    if (!_projectLockTimers.has(resource_key)) {
      const id = setInterval(async () => {
        try {
          await _fetchJson(LOCK.HEARTBEAT, {
            method: 'POST',
            body: JSON.stringify({ resource_key, ttl_sec: LOCK.TTL_SEC }),
          });
        } catch (e) {}
      }, Math.max(30000, (LOCK.TTL_SEC * 1000) / 2));
      _projectLockTimers.set(resource_key, id);
    }
    return true;
  };

  const ensureProjectLock = async (projectId) => {
    try {
      return await _ensureProjectLock(projectId);
    } catch (e) {
      return false;
    }
  };

  const _handleVersionConflict = (body) => {
    const canonical = body && body.project && body.project.id ? body.project : null;
    if (!canonical) return;
    const projects = getProjects();
    const idx = projects.findIndex(p => String(p?.id) === String(canonical.id));
    if (idx >= 0) projects[idx] = canonical;
    else projects.unshift(canonical);
    saveProjects(projects);

    try {
      window.dispatchEvent(new CustomEvent('rfq:project-conflict', {
        detail: {
          projectId: canonical.id,
          message: 'Your copy was stale. Reloaded latest server data.',
          project: canonical,
          serverVersion: body?.server_version || canonical.server_updated_at || '',
        },
      }));
    } catch (e) {}
  };

  const _syncPayload = async () => {
    const allProjects = getProjects();
    const projects = allProjects.filter(p => _dirtyProjectIds.has(String(p && p.id)));
    if (!projects.length) return { projects: [], blocked: [] };

    const lockResults = await Promise.all(projects.map(async (p) => {
      try {
        const ok = await _ensureProjectLock(p?.id);
        return { p, ok };
      } catch (e) {
        return { p, ok: false };
      }
    }));

    const writable = lockResults.filter(x => x.ok).map(x => _withBaseVersion(x.p));
    const blocked = lockResults.filter(x => !x.ok).map(x => x.p?.id).filter(Boolean);
    if (blocked.length) {
      try {
        window.dispatchEvent(new CustomEvent('rfq:project-lock-required', { detail: { blockedProjectIds: blocked } }));
      } catch (e) {}
    }
    return { projects: writable, blocked };
  };

  const _syncNowCore = async () => {
    const payload = await _syncPayload();
    if (!payload.projects.length) return { ok: true, skipped: true, reason: 'lock_required' };
    try {
      const res = await _fetchJson(API.BULK, { method: 'POST', body: JSON.stringify({ projects: payload.projects }) });
      payload.projects.forEach(p => { if (p && p.id) _dirtyProjectIds.delete(String(p.id)); });
      return res;
    } catch (err) {
      if (err && err.status === 409 && err.body && err.body.code === 'version_conflict') {
        _handleVersionConflict(err.body);
      }
      throw err;
    }
  };

  const syncNow = () => {
    if (_syncInFlight) return;
    _syncInFlight = true;
    _syncNowCore()
      .then(res => console.log('[RFQ Sync] Success:', res))
      .catch(err => console.error('[RFQ Sync] Error:', err.message || err))
      .finally(() => { _syncInFlight = false; });
  };

  // Awaitable version of syncNow — resolves when sync completes
  const syncNowAsync = () => {
    return new Promise((resolve, reject) => {
      if (_syncInFlight) {
        const waitInterval = setInterval(() => {
          if (!_syncInFlight) {
            clearInterval(waitInterval);
            syncNowAsync().then(resolve).catch(reject);
          }
        }, 100);
        return;
      }
      _syncInFlight = true;
      _syncNowCore()
        .then(res => { resolve(res); })
        .catch(err => { reject(err); })
        .finally(() => { _syncInFlight = false; });
    });
  };

  const queueSync = (delayMs) => {
    const d = Number(delayMs) || 800;
    if (_syncTimer) clearTimeout(_syncTimer);
    _syncTimer = setTimeout(() => {
      _syncTimer = null;
      syncNow();
    }, d);
  };

  const _cleanupStaleDrafts = () => {
    const queue = safeJsonParse(localStorage.getItem('rfq_sync_queue_v1'), []);
    if (Array.isArray(queue) && queue.length > 0) return;
    try {
      for (let i = localStorage.length - 1; i >= 0; i -= 1) {
        const k = localStorage.key(i);
        if (k && String(k).startsWith('rfq_project_draft_v1_')) localStorage.removeItem(k);
      }
    } catch (e) {}
  };

  const bootstrapFromServer = (preferServer = false) => {
    // 1) pull server projects
    return _fetchJson(API.PROJECTS)
      .then(data => {
        const serverProjects = data && Array.isArray(data.projects) ? data.projects : [];
        const localProjects = getProjects();

        if (serverProjects.length === 0 && localProjects.length > 0) {
          // Server empty -> push local up.
          queueSync(100);
          return;
        }

        // Merge server -> local (choose newer by updated_at)
        let merged = mergeProjectsById(localProjects, serverProjects);
        if (preferServer) {
          const map = new Map((Array.isArray(localProjects) ? localProjects : []).filter(p => p && p.id).map(p => [String(p.id), p]));
          (Array.isArray(serverProjects) ? serverProjects : []).forEach(p => { if (p && p.id) map.set(String(p.id), p); });
          merged = Array.from(map.values());
        }

        const queue = safeJsonParse(localStorage.getItem('rfq_sync_queue_v1'), []);
        const queueHasPending = Array.isArray(queue) && queue.length > 0;
        const sessionScope = String(window.__RFQ_SESSION_SCOPE__ || '');
        if (queueHasPending) {
          merged.forEach((p, idx) => {
            if (!p || !p.id) return;
            const k = `rfq_project_draft_v1_${String(p.id)}`;
            const draft = safeJsonParse(localStorage.getItem(k), null);
            if (!draft || !draft.pending) return;
            if (String(draft.sessionScope || '') !== sessionScope) return;
            const base = String(draft.baseVersion || '');
            const serverVersion = String(p.server_updated_at || p.data_version || p.updated_at || '');
            if (!base || base !== serverVersion) return;
            if (draft.project && typeof draft.project === 'object') {
              merged[idx] = { ...p, ...draft.project };
            }
          });
        }

        saveProjects(merged);
        _cleanupStaleDrafts();
      })
      .catch(() => {
        // offline / server not reachable -> keep local only
      });
  };

  // Periodic sync (covers imports that bypass RFQData helpers)
  try {
    setInterval(() => queueSync(500), 30000);
    window.addEventListener('beforeunload', () => {
      try { syncNow(); } catch (e) {}
      try {
        _projectLockTimers.forEach((timerId, resource_key) => {
          clearInterval(timerId);
          _fetchJson(LOCK.RELEASE, { method: 'POST', body: JSON.stringify({ resource_key }) }).catch(() => {});
        });
        _projectLockTimers.clear();
      } catch (e) {}
    });
  } catch (e) {}


  const getProjects = () => {
    const projects = safeJsonParse(localStorage.getItem(LS_KEYS.PROJECTS), []);
    // minimal migration hardening
    if (!Array.isArray(projects)) return [];
    return projects.map(p => {
      const proj = p && typeof p === "object" ? p : {};
      if (!proj.id) proj.id = uid();
      if (!proj.name) proj.name = "Untitled";
      // Project-level status (dashboard + project details)
      if (!proj.project_status || !String(proj.project_status).trim()) proj.project_status = "Created";
      if (!Array.isArray(proj.items)) proj.items = [];
      if (!proj.dates || typeof proj.dates !== "object") proj.dates = {};
      if (Array.isArray(proj.rfq_batches) && !Array.isArray(proj.rfqBatches)) proj.rfqBatches = proj.rfq_batches;
      if (!Array.isArray(proj.rfqBatches)) proj.rfqBatches = [];
      proj.rfq_batches = proj.rfqBatches;
      return proj;
    });
  };

  const createProject = (name, extraDates) => {
    const projects = getProjects();
    const proj = {
      id: uid(),
      name: String(name || "Untitled").trim() || "Untitled",
      created_at: nowIso(),
      updated_at: nowIso(),
      project_status: "Created",
      dates: extraDates && typeof extraDates === "object" ? { ...extraDates } : {},
      items: [],
      supplierData: {},
      rfqBatches: [],
      rfq_batches: [],
      // supplier master DB (NDA + custom fields)
      supplierMaster: [],
      notes: [],
    };
    projects.unshift(proj);
    saveProjects(projects);
    _dirtyProjectIds.add(String(proj.id));
    _emitMutationSignal('create_project', proj.id);
    try { queueSync(); } catch (e) {}
    localStorage.setItem(LS_KEYS.ACTIVE_PROJECT, proj.id);
    return proj;
  };

  const updateProject = (project) => {
    if (!project || typeof project !== "object") return false;
    const projects = getProjects();
    const idx = projects.findIndex(p => String(p.id) === String(project.id));
    if (idx >= 0) {
      projects[idx] = project;
    } else {
      projects.unshift(project);
    }
    project.updated_at = nowIso();
    saveProjects(projects);
    _dirtyProjectIds.add(String(project.id));
    _emitMutationSignal('update_project', project.id);
    try { queueSync(); } catch (e) {}
    return true;
  };

  const deleteProject = (projectId) => {
    const projects = getProjects().filter(p => String(p.id) !== String(projectId));
    saveProjects(projects);
    _dirtyProjectIds.add(String(projectId));
    _emitMutationSignal('delete_project', projectId);
    try { queueSync(); } catch (e) {}
    const active = localStorage.getItem(LS_KEYS.ACTIVE_PROJECT);
    if (String(active) === String(projectId)) {
      localStorage.removeItem(LS_KEYS.ACTIVE_PROJECT);
    }
    return true;
  };

  // =========================================================
  // RFQ Batches (Bundles)
  // Stored on project.rfqBatches
  // =========================================================

  const getRFQBatches = (projectId) => {
    const proj = getProjects().find(p => String(p.id) === String(projectId));
    return proj && Array.isArray(proj.rfqBatches) ? proj.rfqBatches : [];
  };

  const saveRFQBatch = (projectId, batch) => {
    if (!projectId) return null;
    const projects = getProjects();
    const proj = projects.find(p => String(p.id) === String(projectId));
    if (!proj) return null;

    if (!Array.isArray(proj.rfqBatches)) proj.rfqBatches = [];

    const b = { ...(batch || {}) };
    if (!b.id) b.id = uid();
    if (!b.created_at) b.created_at = nowIso();
    b.updated_at = nowIso();

    const idx = proj.rfqBatches.findIndex(x => String(x.id) === String(b.id));
    if (idx >= 0) proj.rfqBatches[idx] = b;
    else proj.rfqBatches.unshift(b);

    updateProject(proj);
    return b;
  };

  const deleteRFQBatch = (projectId, batchId) => {
    const projects = getProjects();
    const proj = projects.find(p => String(p.id) === String(projectId));
    if (!proj) return false;
    proj.rfqBatches = (proj.rfqBatches || []).filter(b => String(b.id) !== String(batchId));
    updateProject(proj);
    return true;
  };

  // =========================================================
  // Quote Validity Helpers
  // =========================================================

  const parseDate = (dateStr) => {
    if (!dateStr) return null;
    // Accept yyyy-mm-dd
    const d = new Date(String(dateStr));
    return isNaN(d.getTime()) ? null : d;
  };

  const getDaysUntilExpiry = (validUntilStr) => {
    const d = parseDate(validUntilStr);
    if (!d) return null;
    const today = new Date();
    // normalize to midnight
    const t0 = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    const t1 = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const diff = Math.ceil((t1 - t0) / (24 * 3600 * 1000));
    return diff;
  };

  const getQuoteValidityStatus = (validUntilStr) => {
    const days = getDaysUntilExpiry(validUntilStr);
    if (days === null) return "";
    if (days < 0) return "Expired";
    if (days <= 7) return `Expiring (${days}d)`;
    if (days <= 30) return `Valid (${days}d)`;
    return `Valid (${days}d)`;
  };

  // =========================================================
  // Shared constants
  // =========================================================

  const INCOTERMS = [
    "EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DPU", "DAP", "DDP",
  ];

  // Rates are only a helper for quick dashboards.
  // If you need real-time FX, integrate an API later.
  // Top/most common currencies shown across the UI (ordered).
    const CURRENCIES = [
      'EUR', 'USD', 'CZK', 'GBP', 'CHF', 'PLN', 'HUF', 'CNY', 'JPY', 'SEK'
    ];

    // Default EUR-based helper rates (static placeholders).
    // Meaning: EUR_value = value_in_currency * CURRENCY_RATES[currency]
    const DEFAULT_CURRENCY_RATES = {
      EUR: 1,
      USD: 0.92,
      CZK: 0.041,
      GBP: 1.17,
      CHF: 1.05,
      PLN: 0.23,
      HUF: 0.0026,
      CNY: 0.13,
      JPY: 0.0062,
      SEK: 0.09,
    };

    // Settings storage key
    const SETTINGS_KEY = 'rfq_settings_v1';

    // Get settings from localStorage
    const getSettings = () => {
      const def = {
        currencyRates: { ...DEFAULT_CURRENCY_RATES },
        currencyDecimals: 4
      };
      const saved = safeJsonParse(localStorage.getItem(SETTINGS_KEY), null);
      if (!saved || typeof saved !== 'object') return def;
      // Merge with defaults to ensure all keys exist
      return {
        currencyRates: { ...DEFAULT_CURRENCY_RATES, ...(saved.currencyRates || {}) },
        currencyDecimals: typeof saved.currencyDecimals === 'number' ? saved.currencyDecimals : 4
      };
    };

    // Save settings to localStorage
    const saveSettings = (settings) => {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    };

    // Get current currency rates (from settings or defaults)
    const getCurrencyRates = () => {
      return getSettings().currencyRates;
    };

    // Get currency decimal places setting
    const getCurrencyDecimals = () => {
      return getSettings().currencyDecimals;
    };

    // Live CURRENCY_RATES object that gets updated from settings
    const CURRENCY_RATES = { ...DEFAULT_CURRENCY_RATES };

    // Function to refresh CURRENCY_RATES from settings
    const refreshCurrencyRates = () => {
      const rates = getCurrencyRates();
      Object.keys(rates).forEach(k => {
        CURRENCY_RATES[k] = rates[k];
      });
    };

    // Initialize rates from settings on load
    try { refreshCurrencyRates(); } catch (e) {}

  // Unit options for items
  const UNITS = ['pcs', 'ea', 'set', 'kit', 'box', 'pack', 'roll', 'sheet', 'm', 'cm', 'mm', 'l', 'ml', 'kg', 'g'];

  const FIELDS = [
    { key: "line", label: "Line" },
    { key: "item_drawing_no", label: "Drawing No" },
    { key: "description", label: "Description" },
    { key: "manufacturer", label: "Manufacturer" },
    { key: "mpn", label: "MPN" },
    { key: "unit", label: "Unit" },
    { key: "status", label: "Status" },
    { key: "target_price", label: "Target Price", type: "number" },

    { key: "qty_1", label: "QTY 1" },
    { key: "qty_2", label: "QTY 2" },
    { key: "qty_3", label: "QTY 3" },
    { key: "qty_4", label: "QTY 4" },
    { key: "qty_5", label: "QTY 5" },

    // Legacy main supplier fields (kept for backward compatibility)
    { key: "supplier", label: "Main Supplier" },
    { key: "currency", label: "Currency" },
    { key: "incoterms", label: "Incoterms" },
    { key: "moq", label: "MOQ" },
    { key: "mov", label: "MOV" },
    { key: "lead_time", label: "Lead Time" },
    { key: "shipping_cost", label: "Shipping Cost" },
    { key: "quote_valid_until", label: "Valid Until" },

    // Price columns (legacy)
    { key: "price_1", label: "Price 1" },
    { key: "price_2", label: "Price 2" },
    { key: "price_3", label: "Price 3" },
    { key: "price_4", label: "Price 4" },
    { key: "price_5", label: "Price 5" },

    { key: "notes", label: "Notes" },
  ];

  // =========================================================
  // Public API
  // =========================================================


  // =========================================================
  // Cross-project duplicate detection (used in Item Detail)
  // =========================================================

  const norm = (v) => String(v ?? '').trim().toLowerCase();

  const findDuplicateParts = (drawingNo, mpn, currentProjectId) => {
    const dn = norm(drawingNo);
    const m = norm(mpn);
    if (!dn && !m) return [];

    const matches = [];
    const projects = getProjects();

    projects.forEach(p => {
      if (!p || typeof p !== "object") return;
      // compare against OTHER projects only
      if (currentProjectId && String(p.id) === String(currentProjectId)) return;

      const items = Array.isArray(p.items) ? p.items : [];
      items.forEach(it => {
        if (!it || typeof it !== "object") return;

        const itDn = norm(it.item_drawing_no);
        const itMpn = norm(it.mpn);

        if (dn && itDn && itDn === dn) {
          matches.push({ projectId: p.id, projectName: p.name || "Untitled", item: it, matchType: "drawing_no" });
          return;
        }
        if (m && itMpn && itMpn === m) {
          matches.push({ projectId: p.id, projectName: p.name || "Untitled", item: it, matchType: "mpn" });
        }
      });
    });

    return matches;
  };

  try {
    window.addEventListener('storage', (ev) => {
      const key = ev && ev.key ? String(ev.key) : '';
      if (key !== LS_KEYS.SYNC_SIGNAL && key !== LS_KEYS.PROJECTS_VERSION) return;
      const payload = safeJsonParse(ev && ev.newValue ? ev.newValue : '', null);
      if (payload && payload.tab && payload.tab === _thisTabId) return;
      setTimeout(() => {
        try { bootstrapFromServer(true); } catch (e) {}
      }, 200);
    });
  } catch (e) {}

  try { bootstrapFromServer(); } catch (e) {}

  window.RFQData = {
    uid,
    FIELDS,
    UNITS,
    INCOTERMS,
    CURRENCIES,
    CURRENCY_RATES,
    DEFAULT_CURRENCY_RATES,
    getSettings,
    saveSettings,
    getCurrencyRates,
    getCurrencyDecimals,
    refreshCurrencyRates,
    getProjects,
    createProject,
    updateProject,
    deleteProject,
    getRFQBatches,
    saveRFQBatch,
    deleteRFQBatch,
    getDaysUntilExpiry,
    getQuoteValidityStatus,
    findDuplicateParts,
    getProjectStatusOptions,
    addProjectStatusOption,
    syncNow,
    syncNowAsync,
    queueSync,
    ensureProjectLock,
    resetServer: function(){ return _fetchJson(API.RESET, { method: 'POST', body: '{}' }); },
    bootstrapFromServer,
  };

  // Backward-compatible global helpers (older rfq.js versions referenced these)
  window.getProjects = window.RFQData.getProjects;
  window.createProject = window.RFQData.createProject;
  window.updateProject = window.RFQData.updateProject;
  window.deleteProject = window.RFQData.deleteProject;
  window.getRFQBatches = window.RFQData.getRFQBatches;
  window.saveRFQBatch = window.RFQData.saveRFQBatch;
  window.deleteRFQBatch = window.RFQData.deleteRFQBatch;
  window.findDuplicateParts = window.RFQData.findDuplicateParts;
  window.uid = window.RFQData.uid;

  // Global currency rates (updated from Settings)
  window.CURRENCY_RATES = window.RFQData.CURRENCY_RATES;

})();
