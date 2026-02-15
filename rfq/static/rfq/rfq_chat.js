// =========================================================
// OPTIBRAIN V3.1 - SELF-LEARNING & MASS INGESTION
// + V4 ENTERPRISE LAYER (Dictionary, Anti-Hallucination)
// =========================================================

(function () {

    // --- HELPERS (MUST BE AVAILABLE BEFORE Brain METHODS USE THEM) ---
    const formatCurrency = (v, currency) => {
        const c = currency || 'EUR';
        try {
            return new Intl.NumberFormat('en-US', { style: 'currency', currency: c }).format(v);
        } catch (e) {
            return `${v} ${c}`;
        }
    };

    // V4: Typed Fact Formatter (safe + supports money object)
    function formatTypedFact(factObj) {
        if (!factObj) return "Error: Invalid Fact";

        const hasValue = Object.prototype.hasOwnProperty.call(factObj, 'value');
        if (!hasValue) return "Error: Invalid Fact";

        const v = factObj.value;

        if (factObj.type === 'money') {
            // supports both primitive and object {amount,currency,per}
            if (v && typeof v === 'object') {
                const amount = Object.prototype.hasOwnProperty.call(v, 'amount') ? v.amount : null;
                const currency = Object.prototype.hasOwnProperty.call(v, 'currency') ? v.currency : 'EUR';
                const per = Object.prototype.hasOwnProperty.call(v, 'per') ? v.per : null;

                if (amount === null || amount === undefined || amount === '') return "Error: Invalid Money Fact";
                const money = formatCurrency(Number(amount), currency);
                return per ? `<b>${money}</b> / <b>${per}</b>` : `<b>${money}</b>`;
            }

            // primitive money
            return `<b>${v}</b>`;
        }

        if (factObj.type === 'date') {
            if (!v) return "Error: Invalid Date Fact";
            return `<b>${v}</b>`;
        }

        if (factObj.type === 'status') {
            if (!v) return "Error: Invalid Status Fact";
            return `<b>${v}</b>`;
        }

        // default: allow HTML or plain
        return v === null || v === undefined ? "Error: Invalid Fact" : String(v);
    }

    // V4: Language Detection & Anti-Hallucination
    const isCzech = (text) =>
        /[áčďéěíňóřšťúůýž]/i.test(text) ||
        /\b(stav|cena|kdy|dodací|termín|dodání|uzávěrka|projektu|položka|dodavatel)\b/i.test(text);

    const missingField = (field, langCZ) => {
        if (langCZ) return `V systému to nemám (<b>${field}</b> chybí). Chcete ho doplnit?`;
        return `I don't have this in the system (<b>${field}</b> is missing). Do you want to add it?`;
    };

    const findProjectV4 = (name) => {
        const term = String(name || '').toLowerCase().trim();
        if (!term) return null;
        return window.RFQData.getProjects().find(p =>
            (p.name || '').toLowerCase().includes(term) || String(p.id) === term
        );
    };

    const findItemsV4 = (name) => {
        const term = String(name || '').toLowerCase().trim();
        if (!term) return [];
        const res = [];
        window.RFQData.getProjects().forEach(p => {
            if (p.items) p.items.forEach(i => {
                const desc = (i.description || '').toLowerCase();
                const mpn = (i.mpn || '').toLowerCase();
                if (desc.includes(term) || mpn.includes(term)) {
                    res.push({ item: i, project: p });
                }
            });
        });
        return res;
    };

    // --- 1. THE BRAIN (Storage & Memory) ---
    class Brain {
        constructor() {
            this.memoryKey = 'rfq_ai_memory_v3';
            this.knowledge = this.loadMemory();
            this.ingested = false;
        }

        loadMemory() {
            try {
                const raw = localStorage.getItem(this.memoryKey);
                const defaults = { intents: [], facts: {}, aliases: {} }; // V4: Added aliases

                // V3 INTENTS (Preserved)
                this.intents = [
                    { id: 'status_check', patterns: ['status of {project}', 'how is {project} going', 'state of {project}'], handler: 'handleProjectStatus' },
                    { id: 'value_check', patterns: ['value of {project}', 'cost of {project}', 'how much is {project}'], handler: 'handleProjectValue' },
                    { id: 'list_projects', patterns: ['show all projects', 'list projects', 'what projects do we have'], handler: 'handleListProjects' },
                    { id: 'price_check', patterns: ['price of {item}', 'cost of {item}', 'quote for {item}'], handler: 'handleItemPrice' },
                    { id: 'supplier_lookup', patterns: ['who supplies {item}', 'supplier for {item}'], handler: 'handleItemSupplier' },
                    { id: 'supplier_contact', patterns: ['contact for {supplier}', 'email of {supplier}', 'phone of {supplier}'], handler: 'handleSupplierContact' },
                    { id: 'generic_lookup', patterns: ['what is the {field} of {entity}', 'show {field} for {entity}', 'get {field} of {entity}'], handler: 'handleGenericLookup' },
                    { id: 'help', patterns: ['help', 'what can you do', 'show commands'], handler: 'handleHelp' }
                ];

                // V4 INTENTS (New Enterprise Rules - Regex Based)
                this.intentsV4 = [
                    { id: 'rfq_status', patterns: [/status (?:of|for| )(.+)/i, /stav (?:projektu| )(.+)/i], handler: 'handleProjectStatusV4' },
                    { id: 'rfq_deadline', patterns: [/deadline (?:of|for| )(.+)/i, /kdy (?:je|končí) (.+)/i, /termín (.+)/i], handler: 'handleProjectDeadlineV4' },
                    { id: 'rfq_scope', patterns: [/scope (?:of|for| )(.+)/i, /rozsah (.+)/i, /co (?:je|obsahuje) (.+)/i], handler: 'handleProjectScopeV4' },
                    { id: 'quote_price', patterns: [/priceOf (.+)/i, /cena(?: za)? (.+)/i, /price (?:of|for| )(.+)/i], handler: 'handleItemPriceV4' },
                    { id: 'contract_supplier', patterns: [/supplier (?:for|of| )(.+)/i, /dodavatel (.+)/i, /kdo dodává (.+)/i], handler: 'handleItemSupplierV4' },
                    { id: 'lead_time', patterns: [/lead time (?:of|for| )(.+)/i, /dodací (?:lhůta|doba) (.+)/i, /za jak dlouho (.+)/i], handler: 'handleGenericLookupV4', field: 'lead_time' },
                    { id: 'delivery_terms', patterns: [/incoterms (?:of|for| )(.+)/i, /podmínky (.+)/i, /doprava (.+)/i], handler: 'handleGenericLookupV4', field: 'incoterms' }
                ];

                if (!raw) return defaults;
                const data = JSON.parse(raw);
                return {
                    ...defaults,
                    facts: { ...(data.facts || {}) },
                    aliases: { ...(data.aliases || {}) }
                };
            } catch (e) {
                return { intents: [], facts: {}, aliases: {} };
            }
        }

        saveMemory() {
            localStorage.setItem(this.memoryKey, JSON.stringify({
                facts: this.knowledge.facts,
                aliases: this.knowledge.aliases
            }));
        }

        learnFact(question, answer) {
            const q = question.toLowerCase().trim();
            this.knowledge.facts[q] = answer;
            this.saveMemory();
            return true;
        }

        // MASS INGESTION (V3 Logic Preserved)
        ingestData() {
            if (this.ingested) return;
            const projects = window.RFQData.getProjects();

            projects.forEach(p => {
                this.knowledge.facts[`status of ${String(p.name || '').toLowerCase()}`] = `<b>${p.name}</b> is ${p.project_status}.`;
                this.knowledge.facts[`deadline of ${String(p.name || '').toLowerCase()}`] = p.dates?.deadline ? `Deadline: ${p.dates.deadline}` : "No deadline set.";

                (p.items || []).forEach(i => {
                    const desc = String(i.description || '').toLowerCase();
                    const mpn = String(i.mpn || '').toLowerCase();
                    const price = i.price_1 ? `${i.price_1} EUR` : (i.target_price ? `Target: ${i.target_price} EUR` : "No price");

                    if (desc) {
                        this.knowledge.facts[`price of ${desc}`] = `Price for <b>${i.description}</b>: ${price}`;
                        this.knowledge.facts[`cost of ${desc}`] = `Price for <b>${i.description}</b>: ${price}`;
                        this.knowledge.facts[`supplier of ${desc}`] = i.supplier ? `Supplier: <b>${i.supplier}</b>` : "No supplier assigned.";
                    }
                    if (mpn) this.knowledge.facts[`price of ${mpn}`] = `Price for <b>${i.mpn}</b>: ${price}`;
                });
            });
            this.ingested = true;
            console.log(`[OptiBrain] Ingested ${Object.keys(this.knowledge.facts).length} facts.`);
        }

        // --- V3 FIND MATCH (STRICTLY FIRST) ---
        findMatch(query) {
            const q = query.toLowerCase().trim();

            // 1. Facts
            if (this.knowledge.facts[q]) {
                const val = this.knowledge.facts[q];
                // V4: Support Typed Facts if found in V3 lookup
                if (typeof val === 'object' && val && Object.prototype.hasOwnProperty.call(val, 'value')) {
                    return { type: 'fact', answer: formatTypedFact(val) };
                }
                return { type: 'fact', answer: val };
            }

            // 2. V3 Intents
            for (const intent of this.intents) {
                for (const pattern of intent.patterns) {
                    const params = this.matchPattern(pattern, q);
                    if (params) return { type: 'intent', handler: intent.handler, params };
                }
            }
            return null;
        }

        matchPattern(pattern, query) {
            const regexStr = pattern
                .replace(/[.*+?^$()|[\]\\]/g, '\\$&')
                .replace(/\\\{(\w+)\\\}/g, '(?<$1>.+)');
            const match = query.match(new RegExp(`^${regexStr}$`, 'i'));
            return match ? (match.groups || {}) : null;
        }

        // --- V4 FIND MATCH (RUNS ONLY IF V3 FAILS) ---
        findMatchV4(query) {
            const qNorm = this.v4Normalize(query);

            // 1. Alias Lookup
            if (this.knowledge.aliases && this.knowledge.aliases[qNorm]) {
                const canonicalKey = this.knowledge.aliases[qNorm];
                if (this.knowledge.facts[canonicalKey]) {
                    const val = this.knowledge.facts[canonicalKey];
                    if (typeof val === 'object' && val && Object.prototype.hasOwnProperty.call(val, 'value')) {
                        return { type: 'fact', answer: formatTypedFact(val) };
                    }
                    return { type: 'fact', answer: val };
                }
            }

            // 2. V4 Intent Matching (Regex array)
            for (const intent of this.intentsV4) {
                for (const regex of intent.patterns) {
                    const match = query.match(regex);
                    if (match) {
                        return {
                            type: 'intentV4',
                            handler: intent.handler,
                            params: {
                                entity: match[1],
                                fullQuery: query,
                                field: intent.field
                            }
                        };
                    }
                }
            }

            return null;
        }

        v4Normalize(str) {
            return String(str || '').toLowerCase().trim()
                .replace(/[^\w\s\-_]/g, '') // Remove punctuation except - _
                .replace(/\s+/g, ' ');      // Collapse whitespace
        }
    }

    // --- 2a. HANDLERS (V3 - Preserved) ---
    const Handlers = {
        handleProjectStatus: (p) => {
            const proj = window.RFQData.getProjects().find(x => (x.name || '').toLowerCase().includes((p.project || '').toLowerCase()));
            if (!proj) return "Project not found.";
            const count = (proj.items || []).length;
            return `<b>${proj.name}</b> is <b>${proj.project_status}</b>. (<b>${count}</b> items)`;
        },
        handleProjectValue: (p) => {
            const proj = window.RFQData.getProjects().find(x => (x.name || '').toLowerCase().includes((p.project || '').toLowerCase()));
            if (!proj) return "Project not found.";
            let t = 0;
            (proj.items || []).forEach(i => t += (i.price_1 || i.target_price || 0) * (i.qty_1 || 1));
            return `Value: <b>${t.toFixed(2)} EUR</b>`;
        },
        handleListProjects: () => window.RFQData.getProjects().map(p => `• ${p.name}`).join('<br>') || "No projects.",
        handleItemPrice: (p) => `I checked my database for "${p.item}" but found no exact matches. Try the exact MPN or Description.`,
        handleItemSupplier: (p) => `I don't see a supplier for "${p.item}".`,
        handleSupplierContact: (p) => "Contact info not found.",
        handleGenericLookup: (p) => `Attribute "${p.field}" for "${p.entity}" is unknown.`,
        handleHelp: () => `
            <div style="text-align:left; font-size:13px; line-height:1.6;">
                <b>I can answer these questions (CZ/EN):</b><br><br>
                <b style="color:#2563eb;">Projects (Projekty)</b><br>
                • Status of [Project] <i>(Stav projektu...)</i><br>
                • Deadline of [Project] <i>(Kdy končí...)</i><br>
                • Scope of [Project] <i>(Co obsahuje...)</i><br><br>
                <b style="color:#10b981;">Items (Položky)</b><br>
                • Price of [MPN/Desc] <i>(Cena za...)</i><br>
                • Supplier for [Item] <i>(Kdo dodává...)</i><br>
                • Lead time for [Item] <i>(Dodací doba...)</i><br><br>
                <i>Try: "Stav projektu Alpha" or "Price of Resistor"</i>
            </div>`
    };

    // --- 2b. HANDLERS (V4 - New Enterprise Logic) ---
    const HandlersV4 = {
        handleProjectStatusV4: (ctx) => {
            const cz = isCzech(ctx.fullQuery);
            const p = findProjectV4(ctx.entity);
            if (!p) return cz ? `Projekt "${ctx.entity}" nebyl nalezen.` : `Project "${ctx.entity}" not found.`;

            const status = p.project_status || (cz ? "Neznámý" : "Unknown");
            return cz ?
                `Stav projektu <b>${p.name}</b> je: <b>${status}</b>.` :
                `Status of project <b>${p.name}</b> is: <b>${status}</b>.`;
        },

        handleProjectDeadlineV4: (ctx) => {
            const cz = isCzech(ctx.fullQuery);
            const p = findProjectV4(ctx.entity);
            if (!p) return cz ? `Projekt "${ctx.entity}" nebyl nalezen.` : `Project "${ctx.entity}" not found.`;

            if (!p.dates || !p.dates.deadline) return missingField('deadline', cz);

            return cz ?
                `Deadline projektu <b>${p.name}</b> je <b>${p.dates.deadline}</b>.` :
                `Deadline for project <b>${p.name}</b> is <b>${p.dates.deadline}</b>.`;
        },

        handleProjectScopeV4: (ctx) => {
            const cz = isCzech(ctx.fullQuery);
            const p = findProjectV4(ctx.entity);
            if (!p) return cz ? `Projekt "${ctx.entity}" nebyl nalezen.` : `Project "${ctx.entity}" not found.`;

            const count = (p.items || []).length;
            const examples = (p.items || []).slice(0, 3).map(i => i.description).filter(Boolean);
            const names = examples.length ? examples.join(', ') : (cz ? "bez položek" : "no items");

            return cz ?
                `Projekt <b>${p.name}</b> obsahuje <b>${count}</b> položek. (Např. ${names}...)` :
                `Project <b>${p.name}</b> contains <b>${count}</b> items. (E.g. ${names}...)`;
        },

        handleItemPriceV4: (ctx) => {
            const cz = isCzech(ctx.fullQuery);
            const hits = findItemsV4(ctx.entity);
            if (hits.length === 0) return cz ? `Položka "${ctx.entity}" nebyla nalezena.` : `Item "${ctx.entity}" not found.`;

            const { item } = hits[0];
            const price = item.price_1 || item.target_price;

            if (!price) return missingField('price', cz);

            const label = item.price_1 ? (cz ? "Cena" : "Price") : (cz ? "Target cena" : "Target Price");
            const val = formatCurrency(Number(price), item.currency || 'EUR');

            return cz ?
                `<b>${item.description}</b>: ${label} je <b>${val}</b>.` :
                `<b>${item.description}</b>: ${label} is <b>${val}</b>.`;
        },

        handleItemSupplierV4: (ctx) => {
            const cz = isCzech(ctx.fullQuery);
            const hits = findItemsV4(ctx.entity);
            if (hits.length === 0) return cz ? `Položka "${ctx.entity}" nebyla nalezena.` : `Item "${ctx.entity}" not found.`;

            const { item } = hits[0];
            if (!item.supplier) return missingField('supplier', cz);

            return cz ?
                `Dodavatel pro <b>${item.description}</b> je <b>${item.supplier}</b>.` :
                `Supplier for <b>${item.description}</b> is <b>${item.supplier}</b>.`;
        },

        handleGenericLookupV4: (ctx) => {
            const field = ctx.field;
            const cz = isCzech(ctx.fullQuery);

            const hits = findItemsV4(ctx.entity);
            if (hits.length > 0) {
                const val = hits[0].item[field];
                if (!val) return missingField(field, cz);
                return cz ?
                    `<b>${field}</b> pro <b>${hits[0].item.description}</b> je <b>${val}</b>.` :
                    `<b>${field}</b> for <b>${hits[0].item.description}</b> is <b>${val}</b>.`;
            }

            const proj = findProjectV4(ctx.entity);
            if (proj) {
                const val = proj[field] || (proj.dates ? proj.dates[field] : null);
                if (!val) return missingField(field, cz);
                return cz ?
                    `<b>${field}</b> pro projekt <b>${proj.name}</b> je <b>${val}</b>.` :
                    `<b>${field}</b> for project <b>${proj.name}</b> is <b>${val}</b>.`;
            }

            return cz ? `Omlouvám se, "${ctx.entity}" neznám.` : `Sorry, I don't know "${ctx.entity}".`;
        }
    };

    // --- 3. UI ---
    window.RFQChat = {
        brain: new Brain(),
        init: function () {
            if (document.getElementById('rfq-chat-root')) return;
            this.brain.ingestData();
            this.render();
            this.appendMsg('bot', `<b>Hello!</b> I am OptiBrain V4. 🧠<br>I know ${Object.keys(this.brain.knowledge.facts).length} facts about your projects.<br>Ask me anything!`);
            setTimeout(() => {
                const inp = document.getElementById('chat-inp');
                if (inp) { inp.focus(); }
            }, 500);
        },
        renderInterface: function () { this.render(); },
        render: function () {
            const el = document.getElementById('view-ai-chat');
            if (!el) return;
            el.innerHTML = `
                <div id="rfq-chat-root" style="display:flex; flex-direction:column; height:100%; width:50%; margin:0 auto; max-width:600px; background:#fff; border-left:1px solid #ddd; border-right:1px solid #ddd; box-shadow: 0 0 20px rgba(0,0,0,0.1);">
                    <div style="padding:15px; border-bottom:1px solid #eee; background:#f8fafc;">
                        <b style="font-size:16px;">🤖 OptiBrain V4 (Enterprise)</b>
                        <div style="font-size:11px; color:#10b981;">● ${Object.keys(this.brain.knowledge.facts).length} Knowledge Points + V4 Logic</div>
                    </div>
                    <div id="chat-history" style="flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:15px; scroll-behavior:smooth;"></div>
                    <div style="padding:20px; border-top:1px solid #eee; background:#fff;">
                        <div style="display:flex; gap:10px;">
                            <input id="chat-inp" type="text" placeholder="Type a question..."
                                   style="flex:1; padding:12px; border:1px solid #ccc; border-radius:8px; font-size:14px; 
                                          color:#000 !important; background:#fff !important; caret-color:#000 !important; 
                                          opacity:1 !important; user-select:text !important; cursor:text; z-index:9999; pointer-events:auto;"
                                   onclick="this.focus(); event.stopPropagation();"
                                   onmousedown="event.stopPropagation()"
                                   onkeydown="event.stopPropagation()"
                                   autocomplete="off">
                            <button id="chat-btn" style="padding:0 25px; background:#2563eb; color:white; border:none; border-radius:8px; font-weight:600; cursor:pointer;" onclick="event.stopPropagation()">Send</button>
                        </div>
                    </div>
                </div>
                `;
            const inp = document.getElementById('chat-inp');
            const btn = document.getElementById('chat-btn');
            const root = document.getElementById('rfq-chat-root');

            // Add global click handler to refocus input
            root.onclick = (e) => {
                if (e.target !== btn && e.target.tagName !== 'BUTTON') inp.focus();
            };

            const submit = () => {
                const txt = inp.value.trim();
                if (!txt) return;
                this.handleUser(txt);
                inp.value = '';
                inp.focus();
            };
            btn.onclick = submit;

            // Fix: Use addEventListener and stopPropagation to prevent global interference
            // preventing the return of 'false' which blocked typing
            inp.addEventListener('keydown', (e) => {
                e.stopPropagation(); // Stop global listeners
                if (e.key === 'Enter') submit();
            });
            inp.addEventListener('input', (e) => e.stopPropagation()); // Stop global listeners
        },
        appendMsg: function (role, html) {
            const h = document.getElementById('chat-history');
            const d = document.createElement('div');
            d.style.maxWidth = '85%';
            d.style.padding = '12px 18px';
            d.style.borderRadius = '12px';
            d.style.lineHeight = '1.5';
            d.style.fontSize = '14px';
            if (role === 'user') {
                d.style.alignSelf = 'flex-end';
                d.style.background = '#2563eb';
                d.style.color = 'white';
                d.style.borderRadius = '12px 2px 12px 12px';
            } else {
                d.style.alignSelf = 'flex-start';
                d.style.background = '#f1f5f9';
                d.style.color = '#1e293b';
                d.style.borderRadius = '2px 12px 12px 12px';
            }
            d.innerHTML = html;
            h.appendChild(d);
            h.scrollTop = h.scrollHeight;
        },

        // --- CONTROLLER Logic (V3 -> V4 Pipeline) ---
        handleUser: function (text) {
            this.appendMsg('user', text);

            // 1. Try V3 (Exact/Simple) Match FIRST
            let match = this.brain.findMatch(text);

            // 2. If no V3 match, Try V4 (Enterprise/Fuzzy) match
            if (!match) {
                match = this.brain.findMatchV4(text);
            }

            // 3. Dispatch
            if (match) {
                if (match.type === 'fact') this.appendMsg('bot', match.answer);
                else if (match.type === 'intent') this.appendMsg('bot', Handlers[match.handler](match.params));
                else if (match.type === 'intentV4') this.appendMsg('bot', HandlersV4[match.handler](match.params));
            } else {
                // 4. Teach Me Fallback (V3 Unchanged)
                const failHtml = `
                    I don't know the answer to that yet. 😔<br>
                < b > Teach me!</b > What should I have said ? <br><br>
                    <input id="teach-inp" placeholder="Type the correct answer..." style="width:100%; padding:8px; margin-top:5px; border:1px solid #ccc; border-radius:4px; user-select:text!important;">
                        <button onclick="window.RFQChat.teachLast('${text.replace(/'/g, "\\'")}')" style="margin-top:8px; padding:6px 12px; background:#10b981; color:white; border:none; border-radius:4px; cursor:pointer;">Save Answer</button>
                    <button onclick="window.RFQChat.cancelTeach()" style="margin-top:8px; padding:6px 12px; background:#ddd; border:none; border-radius:4px; cursor:pointer;">Skip</button>
                    `;
                this.appendMsg('bot', failHtml);
            }
        },

        teachLast: function (q) {
            const res = document.getElementById('teach-inp').value;
            this.brain.learnFact(q, res);
            this.appendMsg('bot', `Thanks! Learned: <i>${q}</i> -> <i>${res}</i>`);
        },

        cancelTeach: function () {
            this.appendMsg('bot', "Okay, I'll ignore that for now.");
        }
    };

    // Auto-init
    if (document.getElementById('view-ai-chat') && !document.getElementById('view-ai-chat').classList.contains('hidden')) {
        window.RFQChat.init();
    }
})();