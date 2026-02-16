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
    PROJECTS_VERSION: "rfq_projects_version_v1",
    SYNC_SIGNAL: "rfq_sync_signal_v1",
    SYNC_QUEUE: "rfq_sync_queue_v1",
    DRAFT_PREFIX: "rfq_project_draft_v1_",
  };

  const TAB_ID = `${Date.now()}_${Math.random().toString(16).slice(2)}`;

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

  const uid = () => {
    // good enough unique id for localStorage
    const r = Math.random().toString(16).slice(2);
    return `${Date.now()}_${r}`;
  };

  const saveProjects = (projects) => {
    localStorage.setItem(LS_KEYS.PROJECTS, JSON.stringify(Array.isArray(projects) ? projects : []));
  };

  const getProjectVersionStamp = (project) => {
    if (!project || typeof project !== 'object') return '';
    const direct = project.server_updated_at || project.updated_at || project.version || '';
    return String(direct || '').trim();
  };

  const clearDraftKeyForProject = (projectId) => {
    if (!projectId) return;
    try { localStorage.removeItem(`${LS_KEYS.DRAFT_PREFIX}${String(projectId)}`); } catch (_) {}
  };

  const clearStaleDraftKeysForProjectIfQueueEmpty = (projectId) => {
    if (!projectId) return;
    const q = safeJsonParse(localStorage.getItem(LS_KEYS.SYNC_QUEUE), []);
    if (!Array.isArray(q) || q.length === 0) {
      clearDraftKeyForProject(projectId);
    }
  };

  const applyPendingDraftWithStrictGuard = (serverProject) => {
    if (!serverProject || !serverProject.id) return serverProject;
    const projectId = String(serverProject.id);
    const raw = localStorage.getItem(`${LS_KEYS.DRAFT_PREFIX}${projectId}`);
    if (!raw) return serverProject;
    const draft = safeJsonParse(raw, null);
    if (!draft || typeof draft !== 'object' || draft.pending !== true) return serverProject;

    const queue = safeJsonParse(localStorage.getItem(LS_KEYS.SYNC_QUEUE), []);
    if (!Array.isArray(queue) || queue.length === 0) {
      clearDraftKeyForProject(projectId);
      return serverProject;
    }

    const currentSession = String(window.__RFQ_SESSION_SCOPE__ || '');
    if (!currentSession || String(draft.sessionScope || '') !== currentSession) return serverProject;

    const serverStamp = getProjectVersionStamp(serverProject);
    const baseStamp = String(draft.baseVersion || '');
    if (!serverStamp || !baseStamp || baseStamp !== serverStamp) return serverProject;

    if (!draft.project || typeof draft.project !== 'object') return serverProject;
    return { ...serverProject, ...draft.project };
  };

  const stampProjectVersionMeta = (project) => {
    if (!project || typeof project !== 'object') return project;
    const stamp = getProjectVersionStamp(project) || nowIso();
    return {
      ...project,
      data_version_stamp: stamp,
    };
  };

  const bumpProjectsVersion = (reason) => {
    try {
      localStorage.setItem(LS_KEYS.PROJECTS_VERSION, JSON.stringify({
        tab: TAB_ID,
        at: Date.now(),
        reason: String(reason || 'mutation')
      }));
    } catch (_) {}
  };

  const emitSyncSignal = (type, detail) => {
    try {
      localStorage.setItem(LS_KEYS.SYNC_SIGNAL, JSON.stringify({
        tab: TAB_ID,
        at: Date.now(),
        type: String(type || 'update'),
        detail: detail || null,
      }));
    } catch (_) {}
  };

  const notifyDataUpdated = (detail) => {
    try {
      window.dispatchEvent(new CustomEvent('rfq:data-updated', {
        detail: {
          source: 'rfq_data',
          ...(detail || {}),
        }
      }));
    } catch (_) {}
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
        let data = null;
        try { data = await r.clone().json(); } catch (_) { data = null; }
        if (r.ok) return data || {};
        let txt = '';
        if (!data) {
          try { txt = await r.text(); } catch (_) { txt = ''; }
        }
        const err = new Error((data && (data.error || data.detail)) || txt || ('HTTP ' + r.status));
        err.status = r.status;
        err.data = data;
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
      const ta = Date.parse(existing.updated_at || existing.created_at || '') || 0;
      const tb = Date.parse(p.updated_at || p.created_at || '') || 0;
      out.set(key, tb >= ta ? p : existing);
    });
    return Array.from(out.values());
  };

  let _syncTimer = null;
  let _syncInFlight = false;

  const _markAuthInvalid = (reason) => {
    try {
      window.__RFQ_AUTH_INVALID__ = true;
      window.dispatchEvent(new CustomEvent('rfq:auth-invalidated', { detail: { reason: String(reason || 'auth_failed') } }));
    } catch (_) {}
  };

  const _mergeServerProject = (serverProject) => {
    if (!serverProject || !serverProject.id) return;
    const projects = getProjects();
    const idx = projects.findIndex(p => String(p.id) === String(serverProject.id));
    if (idx >= 0) projects[idx] = serverProject;
    else projects.unshift(serverProject);
    saveProjects(projects);
    try {
      window.dispatchEvent(new CustomEvent('rfq:project-reconciled', { detail: { projectId: String(serverProject.id) } }));
    } catch (_) {}
  };

  const _handleSyncFailure = (err) => {
    const status = Number(err && err.status);
    if (status === 401 || status === 403) {
      _markAuthInvalid(status === 401 ? 'session_expired' : 'forbidden');
      return;
    }
    if (status === 409 && err && err.data && err.data.code === 'lock_conflict') {
      if (err.data.project) _mergeServerProject(err.data.project);
    }
  };

  const syncNow = () => {
    if (_syncInFlight || window.__RFQ_AUTH_INVALID__) return;
    _syncInFlight = true;
    const projects = getProjects();
    const bodyStr = JSON.stringify({ projects });
    console.log(`[RFQ Sync] Sending ${projects.length} projects, body size: ${bodyStr.length} bytes`);
    _fetchJson(API.BULK, { method: 'POST', body: bodyStr })
      .then(async (res) => {
        console.log('[RFQ Sync] Success:', res);
        // Robustness: after successful write, always refresh from canonical server state.
        const fresh = await bootstrapFromServer();
        bumpProjectsVersion('post_write_refetch');
        emitSyncSignal('server_refetch', { count: Array.isArray(fresh) ? fresh.length : 0 });
      })
      .catch(err => {
        _handleSyncFailure(err);
        console.error('[RFQ Sync] Error:', err.message || err);
      })
      .finally(() => { _syncInFlight = false; });
  };

  // Awaitable version of syncNow — resolves when sync completes
  const syncNowAsync = () => {
    return new Promise((resolve, reject) => {
      if (window.__RFQ_AUTH_INVALID__) {
        reject(new Error('Session invalidated. Please log in again.'));
        return;
      }
      if (_syncInFlight) {
        // Wait for current sync to finish, then sync again
        const waitInterval = setInterval(() => {
          if (!_syncInFlight) {
            clearInterval(waitInterval);
            syncNowAsync().then(resolve).catch(reject);
          }
        }, 100);
        return;
      }
      _syncInFlight = true;
      const projects = getProjects();
      _fetchJson(API.BULK, { method: 'POST', body: JSON.stringify({ projects }) })
        .then(async (res) => {
          const fresh = await bootstrapFromServer();
          bumpProjectsVersion('post_write_refetch');
          emitSyncSignal('server_refetch', { count: Array.isArray(fresh) ? fresh.length : 0 });
          resolve(res);
        })
        .catch(err => {
          _handleSyncFailure(err);
          reject(err);
        })
        .finally(() => { _syncInFlight = false; });
    });
  };

  const queueSync = (delayMs) => {
    const d = Number(delayMs) || 800;
    if (_syncTimer) clearTimeout(_syncTimer);
    try {
      const q = safeJsonParse(localStorage.getItem(LS_KEYS.SYNC_QUEUE), []);
      const arr = Array.isArray(q) ? q : [];
      arr.push({ at: Date.now(), tab: TAB_ID, delay: d });
      localStorage.setItem(LS_KEYS.SYNC_QUEUE, JSON.stringify(arr.slice(-20)));
    } catch (_) {}
    _syncTimer = setTimeout(() => {
      _syncTimer = null;
      try { localStorage.setItem(LS_KEYS.SYNC_QUEUE, '[]'); } catch (_) {}
      syncNow();
    }, d);
  };

  const bootstrapFromServer = () => {
    // Pull server projects and treat server as source-of-truth
    // (critical for multi-user/multi-company isolation).
    return _fetchJson(API.PROJECTS)
      .then(data => {
        const serverProjects = data && Array.isArray(data.projects) ? data.projects : [];
        const merged = serverProjects
          .map(stampProjectVersionMeta)
          .map(applyPendingDraftWithStrictGuard)
          .map(stampProjectVersionMeta);
        merged.forEach(p => clearStaleDraftKeysForProjectIfQueueEmpty(p && p.id));
        saveProjects(merged);
        bumpProjectsVersion('bootstrap_from_server');
        notifyDataUpdated({ reason: 'bootstrap_from_server', count: merged.length });
        return merged;
      })
      .catch(() => {
        // offline / server not reachable -> keep local only
        return getProjects();
      });
  };

  // Periodic sync (covers imports that bypass RFQData helpers)
  try {
    setInterval(() => queueSync(500), 30000);
    window.addEventListener('beforeunload', () => { try { syncNow(); } catch (e) {} });
  } catch (e) {}

  // Cross-tab consistency: when another tab mutates data, re-fetch from server.
  let _remoteRefreshTimer = null;
  const queueRemoteRefresh = (delayMs, reason) => {
    if (_remoteRefreshTimer) clearTimeout(_remoteRefreshTimer);
    _remoteRefreshTimer = setTimeout(() => {
      _remoteRefreshTimer = null;
      bootstrapFromServer()
        .then(() => notifyDataUpdated({ reason: reason || 'remote_change' }))
        .catch(() => {});
    }, Number(delayMs) || 180);
  };

  try {
    window.addEventListener('storage', (ev) => {
      if (!ev || !ev.key) return;
      if (ev.key === LS_KEYS.PROJECTS_VERSION || ev.key === LS_KEYS.SYNC_SIGNAL || ev.key === LS_KEYS.PROJECTS) {
        // Ignore own sync signals when available
        if ((ev.key === LS_KEYS.PROJECTS_VERSION || ev.key === LS_KEYS.SYNC_SIGNAL) && ev.newValue) {
          try {
            const payload = JSON.parse(ev.newValue);
            if (payload && payload.tab && String(payload.tab) === String(TAB_ID)) return;
          } catch (_) {}
        }
        queueRemoteRefresh(180, `storage:${ev.key}`);
      }
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
    if (window.__RFQ_AUTH_INVALID__) return null;
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
    bumpProjectsVersion('create_project');
    emitSyncSignal('mutation', { action: 'create_project', projectId: String(proj.id) });
    notifyDataUpdated({ reason: 'create_project', projectId: String(proj.id) });
    try { queueSync(); } catch (e) {}
    localStorage.setItem(LS_KEYS.ACTIVE_PROJECT, proj.id);
    return proj;
  };

  const updateProject = (project) => {
    if (window.__RFQ_AUTH_INVALID__) return false;
    if (!project || typeof project !== "object") return false;
    const projects = getProjects();
    const nextProject = stampProjectVersionMeta(project);
    const idx = projects.findIndex(p => String(p.id) === String(nextProject.id));
    if (idx >= 0) {
      projects[idx] = nextProject;
    } else {
      projects.unshift(nextProject);
    }
    nextProject.updated_at = nowIso();
    saveProjects(projects);
    bumpProjectsVersion('update_project');
    emitSyncSignal('mutation', { action: 'update_project', projectId: String(nextProject.id || '') });
    notifyDataUpdated({ reason: 'update_project', projectId: String(nextProject.id || '') });
    try { queueSync(); } catch (e) {}
    return true;
  };

  const deleteProject = (projectId) => {
    if (window.__RFQ_AUTH_INVALID__) return false;
    const projects = getProjects().filter(p => String(p.id) !== String(projectId));
    saveProjects(projects);
    bumpProjectsVersion('delete_project');
    emitSyncSignal('mutation', { action: 'delete_project', projectId: String(projectId || '') });
    notifyDataUpdated({ reason: 'delete_project', projectId: String(projectId || '') });
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
    getProjectVersionStamp,
    syncNow,
    syncNowAsync,
    queueSync,
    resetServer: function(){
      return _fetchJson(API.RESET, { method: 'POST', body: '{}' }).then((res) => {
        saveProjects([]);
        localStorage.removeItem(LS_KEYS.ACTIVE_PROJECT);
        bumpProjectsVersion('reset_server');
        emitSyncSignal('mutation', { action: 'reset_server' });
        notifyDataUpdated({ reason: 'reset_server' });
        return res;
      });
    },
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
