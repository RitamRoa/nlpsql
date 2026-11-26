// ── State ─────────────────────────────────────────────────────────────────────
let curSchema   = '__SCHEMA__';
let allSchemas  = [];
let uploadedSet = new Set();   // display names of custom uploaded schemas
let pendingFile = null;        // File object awaiting upload
let resultState = null;
let voiceRecognition = null;
let voiceRecorder = null;
let voiceStream = null;
let voiceChunks = [];
let preferLocalVoice = false;
let voiceStarting = false;
let voiceStopTimer = null;
let resultPage = 0;
let resultPageSize = 10;
let resultFilter = '';
let resultSort = {column: -1, direction: 1};
let recentQueries = [];
let favoriteQueries = [];
let pinnedResults = [];
let currentSchemaTables = [];
let conversationTurns = [];

const SUGGESTIONS = __SUGGESTIONS__;

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
    await ensureAuthenticated();
    applyTheme(localStorage.getItem('nlpsql-theme-v2') || 'light');
    recentQueries = loadStoredList('nlpsql-history');
    conversationTurns = loadStoredList('nlpsql-turns');
    favoriteQueries = loadStoredList('nlpsql-favorites');
    pinnedResults = loadStoredList('nlpsql-pins');
    renderSavedQueries();
    renderPins();
    const d = await fetch('/api/schemas').then(r => r.json());
    if (d.error) throw new Error(d.error);
    allSchemas  = d.available_schemas || [];
    curSchema   = d.current_schema   || 'company';
    uploadedSet = new Set(d.uploaded_schemas || []);
    renderTabs();
    renderSuggestions(curSchema);
    await refreshSchemaPanel();
    await loadFeedbackStats();
    const shared = new URLSearchParams(location.hash.slice(1));
    const sharedQuestion = shared.get('q');
    if (sharedQuestion) {
        const sharedSchema = shared.get('schema');
        if (sharedSchema && sharedSchema !== curSchema && allSchemas.includes(sharedSchema)) await switchSchema(sharedSchema);
        document.getElementById('prompt').value = sharedQuestion;
        runQuery();
    }

    async function ensureAuthenticated() {
        const response = await fetch('/api/auth/status');
        const status = await response.json();
        if (!status.enabled) return;
        const token = window.prompt('Enter your NLPSQL access token:');
        if (!token) throw new Error('An access token is required.');
        const login = await fetch('/api/auth/login', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({token})
        });
        const result = await login.json();
        if (!login.ok) throw new Error(result.error || 'Authentication failed.');
    }
}

// ── Tabs ──────────────────────────────────────────────────────────────────────
function renderTabs() {
    const el = document.getElementById('schemaTabs');
    el.innerHTML = allSchemas.map(s => {
        const isCustom = uploadedSet.has(s);
        return `<button class="stab${s===curSchema?' active':''} ${isCustom?'custom':''}"
                    id="tab-${CSS.escape(s)}" onclick="switchSchema('${s}')">${s}</button>`;
    }).join('');
}

async function switchSchema(name) {
    if (name === curSchema) return;
    setStatus('busy', 'Switching…');
    document.getElementById('schemaContent').innerHTML = '<div class="loading-msg">Switching…</div>';
    document.querySelectorAll('.stab').forEach(t => t.classList.remove('active'));
    document.getElementById('tab-' + CSS.escape(name))?.classList.add('active');

    const d = await fetch('/api/switch_schema', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({schema: name})
    }).then(r => r.json());

    if (d.status === 'ok') {
        curSchema = name;
        renderSuggestions(name);
        await refreshSchemaPanel();
        document.getElementById('results').style.display = 'none';
        document.getElementById('prompt').value = '';
        setStatus('ok', 'Connected');
    } else {
        setStatus('ok', 'Error');
    }
}

// ── Schema Panel ──────────────────────────────────────────────────────────────
async function refreshSchemaPanel() {
    const d = await fetch('/api/schema_detail').then(r => r.json());
    document.getElementById('dbBadge').textContent = d.engine || 'SQLite';
    const tables = d.tables || [];
    currentSchemaTables = tables;
    renderAutocomplete();
    if (!tables.length) {
        document.getElementById('schemaContent').innerHTML = '<div class="loading-msg">No tables found.</div>';
        return;
    }
    document.getElementById('schemaContent').innerHTML = tables.map(t => `
        <div class="tbl-block">
            <div class="tbl-name" role="button" tabindex="0" aria-expanded="false" onclick="toggleTable(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleTable(this)}">${esc(t.name)}</div>
            <ul class="col-list">${t.columns.map(c => `
                <li>
                    <span class="cn">${esc(c.name)}</span>
                    <span class="ct">${esc(c.type)}</span>
                    ${c.is_pk ? '<span class="pk">PK</span>' : ''}
                    ${c.is_fk ? '<span class="fk">FK</span>' : ''}
                </li>`).join('')}
                <li><button class="tool-btn" type="button" onclick="previewTable(decodeURIComponent('${encodeURIComponent(t.name).replace(/'/g, '%27')}'))">Preview rows</button></li>
            </ul>
        </div>`).join('');
    const first = document.querySelector('#schemaContent .tbl-name');
    if (first) toggleTable(first);
}

function toggleTable(nameElement) {
    const block = nameElement.closest('.tbl-block');
    const open = block.classList.toggle('open');
    nameElement.setAttribute('aria-expanded', String(open));
}

// ── Suggestions ───────────────────────────────────────────────────────────────
function renderSuggestions(schema) {
    const list = SUGGESTIONS[schema] || [];
    if (!list.length) {
        document.getElementById('suggestions').innerHTML = '<span style="color:var(--tm);font-size:.82rem;">No suggestions for this schema yet.</span>';
        return;
    }
    const groups = [
        {title:'Simple', values:list.filter((_, index) => index < 2)},
        {title:'Moderate', values:list.filter((_, index) => index >= 2 && index < 4)},
        {title:'Complex', values:list.filter((_, index) => index >= 4)},
    ];
    document.getElementById('suggestions').innerHTML = groups.filter(group => group.values.length).map(group =>
        `<div class="suggestion-group"><span class="suggestion-title">${group.title}</span><div class="suggestions">${group.values.map(s =>
            `<button class="sug-btn" onclick="setPrompt(this.innerText)">${esc(s)}</button>`
        ).join('')}</div></div>`
    ).join('');
}

function setPrompt(t) {
    document.getElementById('prompt').value = t;
    document.getElementById('prompt').focus();
}

// ── Query ─────────────────────────────────────────────────────────────────────
async function runQuery() {
    const q = document.getElementById('prompt').value.trim();
    if (!q) return;
    const btn = document.getElementById('runBtn');
    btn.disabled = true;
    document.getElementById('spin').style.display = 'block';
    document.getElementById('btnTxt').style.display = 'none';

    document.getElementById('emptyState').hidden = true;
    showProgress('Starting', 'translate');
    try {
        const response = await fetch('/api/query_stream', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({
                prompt: q,
                conversation_context: conversationTurns.slice(-4).map(turn =>
                    `Previous question: ${turn.question}\nPrevious validated SQL: ${turn.sql}`
                ).join('\n\n')
            })
        });
        if ((response.headers.get('Content-Type') || '').includes('application/json')) {
            const data = await response.json();
            if (data.clarification) {
                showClarification(data.clarification);
                return;
            }
            throw new Error(data.error || 'Query request failed.');
        }
        if (!response.ok || !response.body) throw new Error(`Query request failed (${response.status})`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const {value, done} = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, {stream:true});
            const events = buffer.split('\n\n');
            buffer = events.pop();
            events.forEach(event => {
                const line = event.split('\n').find(item => item.startsWith('data: '));
                if (!line) return;
                const message = JSON.parse(line.slice(6));
                if (message.type === 'progress') showProgress(message.message, message.stage);
                else if (message.type === 'result') renderQueryResult(message.data, q);
                else if (message.type === 'error') throw new Error(message.message);
            });
        }
        hideProgress();
    } catch(e) {
        document.getElementById('results').style.display = 'flex';
        resetAnswerView(q);
        document.getElementById('tableOut').innerHTML = `<div class="err-box"><strong>The request did not complete.</strong><br>${esc(e.message)}<br>Check that the server is still running, then ask again.</div>`;
    } finally {
        hideProgress();
        btn.disabled = false;
        document.getElementById('spin').style.display = 'none';
        document.getElementById('btnTxt').style.display = 'block';
    }
}

function renderQueryResult(d, question) {
    document.getElementById('results').style.display = 'flex';
    document.getElementById('answerQuestion').textContent = question;
    document.getElementById('mainInner').classList.add('has-result');
    document.getElementById('exDetails').open = false;
    const banner = document.getElementById('transBanner');
    banner.style.display = d.is_translated && d.translated_query ? 'block' : 'none';
    if (banner.style.display === 'block') banner.innerHTML = `<strong>Translated to English:</strong> “${esc(d.translated_query)}”`;
    document.getElementById('sqlOut').value = d.sql || '';
    updateSqlHighlight();
    document.getElementById('tInf').textContent = d.inference_time != null ? d.inference_time.toFixed(3)+'s' : '-';
    document.getElementById('tQ').textContent = d.query_time != null ? d.query_time.toFixed(4)+'s' : '-';
    document.getElementById('tAtt').textContent = d.attempts ?? '-';
    renderTrust(d);
    renderCandidates(d);
    document.getElementById('clarificationPanel').hidden = true;
    const featureParts = [];
    if (d.explanation) {
        const chips = items => items.length ? `<div class="chips">${items.map(item => `<span class="chip">${esc(item)}</span>`).join('')}</div>` : 'none';
        featureParts.push(`<h3>What this query does</h3><p>${esc(d.explanation.summary)}</p><h3 style="margin-top:.8rem">Tables used</h3>${chips(d.explanation.tables)}<h3 style="margin-top:.8rem">Columns used</h3>${chips(d.explanation.columns)}`);
    }
    if (d.narration) {
        featureParts.push(`<h3>Summary computed from the returned rows</h3><p>${esc(d.narration)}</p>`);
    }
    if (d.cost_guard && d.cost_guard.warnings.length) {
        featureParts.push(`<h3>Cost warnings</h3><p>${d.cost_guard.warnings.map(esc).join(' ')}</p>`);
    }
    if (d.feature_errors && Object.keys(d.feature_errors).length) {
        featureParts.push(`<p style="color:var(--danger)">Some optional checks did not run: ${esc(Object.values(d.feature_errors).join('; '))}</p>`);
    }
    document.getElementById('featurePanel').innerHTML = featureParts.join('') || '<p>No explanation is available for this query.</p>';
    const out = document.getElementById('tableOut');
    const diff = document.getElementById('diffPanel');
    if (Array.isArray(d.sql_history) && d.sql_history.length > 1) {
        diff.style.display = 'block';
        diff.innerHTML = `<strong>The first query failed and was repaired automatically</strong><div class="sql-diff"><div><span style="color:var(--danger)">First attempt</span><pre>${esc(d.sql_history[0])}</pre></div><div><span style="color:var(--verified)">Repaired</span><pre>${esc(d.sql_history[d.sql_history.length - 1])}</pre></div></div>`;
    } else {
        diff.style.display = 'none';
        diff.textContent = '';
    }
    if (d.error) {
        resultState = null;
        document.getElementById('summaryOut').textContent = '';
        document.getElementById('chartOut').innerHTML = '';
        document.getElementById('followups').innerHTML = '';
        document.getElementById('chartPanel').style.display = 'none';
        document.getElementById('resultToolbar').style.display = 'none';
        document.getElementById('rowCount').textContent = '0';
        document.getElementById('summaryOut').textContent = d.abstained ? 'No answer shown: the drafts disagreed.' : 'This question could not be answered.';
        document.getElementById('followups').innerHTML = '';
        out.innerHTML = d.abstained
            ? `<div class="withheld"><strong>No answer shown</strong><p>The model's draft queries returned different results, so showing any one of them could mislead you. Try rephrasing, or name the table and columns you mean. The drafts are listed under Explain.</p></div>`
            : `<div class="err-box"><strong>This question could not be answered.</strong><br>${esc(d.error)}<br>Try rephrasing it, or name the table or column you mean.</div>`;
        selectResultTab(d.abstained ? 'table' : 'table');
        recordQuery(question, false);
        return;
    }
    if (!Array.isArray(d.columns) || !Array.isArray(d.rows)) {
        throw new Error('Query response did not contain result columns and rows.');
    }
    resultState = {question, sql: d.sql || '', columns: d.columns, rows: d.rows, truncated: d.truncated};
    document.getElementById('rowCount').textContent = String(d.rows.length) + (d.truncated ? '+' : '');
    selectResultTab('table');
    resultPage = 0;
    resultFilter = '';
    resultSort = {column: -1, direction: 1};
    document.getElementById('chartType').value = 'auto';
    document.getElementById('resultToolbar').style.display = 'flex';
    document.getElementById('chartPanel').style.display = d.rows.length ? 'block' : 'none';
    renderSummary();
    applyChartEdit(question);
    renderResultsTable();
    renderChart();
    renderFollowups();
    recordQuery(question, true);
    conversationTurns = [...conversationTurns, {question, sql:d.sql || ''}].slice(-20);
    saveStoredList('nlpsql-turns', conversationTurns);
    loadFeedbackStats();
    updateShareLink(question, curSchema);
    if (window.matchMedia('(max-width: 900px)').matches) {
        document.getElementById('results').scrollIntoView({behavior: 'smooth', block: 'start'});
    }
}

function resetAnswerView(question) {
    document.getElementById('answerQuestion').textContent = question || '';
    document.getElementById('summaryOut').textContent = '';
    document.getElementById('followups').innerHTML = '';
    document.getElementById('rowCount').textContent = '0';
    const verdict = document.getElementById('trustVerdict');
    verdict.className = 'verdict bad';
    verdict.textContent = 'No result';
    document.getElementById('trustBody').innerHTML = '<p class="trust-note">The question did not reach the database, so nothing was checked.</p>';
    ['tInf', 'tQ', 'tAtt'].forEach(id => { document.getElementById(id).textContent = '-'; });
}

function renderTrust(d) {
    const verdict = document.getElementById('trustVerdict');
    const body = document.getElementById('trustBody');
    const drafts = (d.confidence_candidates || []).length;
    const hasConfidence = Number.isFinite(d.confidence) && drafts > 0;
    const agree = hasConfidence ? Math.round(d.confidence * drafts) : 0;
    let tone = 'idle', label = 'Not cross-checked', text = 'Cross-checking was off for this run, so only one query was written.';
    if (d.error && !d.abstained) {
        tone = 'bad'; label = 'Did not run'; text = 'The query failed, so there is no result to check.';
    } else if (d.abstained) {
        tone = 'warn'; label = 'Withheld';
        text = `Only ${agree} of ${drafts} drafts returned the same result, so no answer is shown.`;
    } else if (hasConfidence && d.confidence >= 1) {
        tone = 'ok'; label = 'Verified'; text = `All ${drafts} drafts returned the same result.`;
    } else if (hasConfidence) {
        tone = 'warn'; label = 'Review suggested';
        text = `${agree} of ${drafts} drafts returned this result. Check the SQL before relying on it.`;
    }
    verdict.className = 'verdict ' + tone;
    verdict.textContent = label;

    const dots = hasConfidence
        ? `<div class="dots" aria-hidden="true">${Array.from({length: drafts}, (_, index) => `<span class="dot ${index < agree ? 'on' : 'off'}"></span>`).join('')}</div>`
        : '';
    const checks = [];
    const sec = d.security || {};
    if (!d.error || d.abstained) checks.push(['', '<b>Read-only</b> connection. Nothing was changed.']);
    if (!d.error) checks.push(['', '<b>Validated</b> against the schema before it ran.']);
    if (sec.enforced) {
        const parts = [];
        if (sec.masked_columns && sec.masked_columns.length) parts.push(`${sec.masked_columns.length} masked column${sec.masked_columns.length === 1 ? '' : 's'}`);
        if (sec.row_filtered_tables && sec.row_filtered_tables.length) parts.push(`rows filtered on ${sec.row_filtered_tables.map(esc).join(', ')}`);
        checks.push(['', `<b>Access policy</b> applied for role ${esc(sec.role)}${parts.length ? ': ' + parts.join('; ') : ''}.`]);
    } else if (sec.role) {
        checks.push(['warn', '<b>Full access.</b> No access policy is configured for this session.']);
    }
    if ((d.attempts || 1) > 1 && !d.error) checks.push(['warn', `<b>Repaired automatically</b> after an error (${d.attempts} attempts).`]);
    if (d.is_translated) checks.push(['', '<b>Translated</b> to English before processing.']);
    ((d.cost_guard && d.cost_guard.warnings) || []).forEach(warning => checks.push(['warn', esc(warning)]));

    body.innerHTML = `${hasConfidence ? `<div class="agree">${dots}<div class="agree-text">${esc(text)}</div></div>` : `<p class="trust-note">${esc(text)}</p>`}` +
        (checks.length ? `<ul class="checks">${checks.map(([cls, html]) => `<li class="${cls}"><span>${html}</span></li>`).join('')}</ul>` : '');
    document.getElementById('qualityBadge').textContent = `${label}. ${text}`;
}

function renderCandidates(d) {
    const list = (d.confidence_candidates || []);
    const card = document.getElementById('candidatesCard');
    card.hidden = list.length < 2;
    document.getElementById('candidatesOut').innerHTML = list.map((sql, index) =>
        `<div class="cand"><i>${index === 0 ? 'Main' : 'Draft ' + (index + 1)}</i><span>${sql ? esc(sql) : 'This draft did not produce a valid query.'}</span></div>`
    ).join('');
}

function showClarification(item) {
    const panel = document.getElementById('clarificationPanel');
    panel.hidden = false;
    panel.innerHTML = `<h3>${esc(item.question)}</h3><p>More than one column matches. Pick the one you mean and the question will run again.</p><div class="suggestions" style="margin-top:.6rem">${item.choices.map(choice =>
        `<button class="sug-btn" type="button" data-choice="${esc(choice)}" onclick="resolveClarification(this.dataset.choice)">${esc(choice)}</button>`
    ).join('')}</div>`;
}

function resolveClarification(choice) {
    const prompt = document.getElementById('prompt');
    prompt.value = `${prompt.value} (use ${choice})`;
    document.getElementById('clarificationPanel').hidden = true;
    runQuery();
}

async function submitFeedback(rating) {
    if (!resultState) return;
    try {
        const response = await fetch('/api/feedback', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({question:resultState.question, sql:resultState.sql, rating})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Feedback could not be saved.');
        loadFeedbackStats();
        setStatus('ok', 'Feedback saved');
    } catch (error) {
        setStatus('ok', error.message);
    }
}

async function saveCorrection() {
    if (!resultState) return;
    const correctedSql = document.getElementById('sqlOut').value.trim();
    try {
        const response = await fetch('/api/feedback', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
                question:resultState.question,
                sql:resultState.sql,
                rating:-1,
                corrected_sql:correctedSql
            })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Correction was rejected.');
        loadFeedbackStats();
        setStatus('ok', 'Correction validated and learned');
    } catch (error) {
        setStatus('ok', error.message);
    }
}

async function loadFeedbackStats() {
    try {
        const response = await fetch('/api/feedback_stats');
        const stats = await response.json();
        if (!response.ok) throw new Error(stats.error || 'Could not load feedback stats.');
        const before = Math.round((stats.before_accuracy || 0) * 100);
        const after = Math.round((stats.after_accuracy || 0) * 100);
        document.getElementById('feedbackStats').innerHTML =
            `<small>Feedback learning · ${stats.measured || 0} corrected-query replays · not a held-out benchmark</small>` +
            `<div>Before ${before}% <progress max="100" value="${before}"></progress> After ${after}% <progress max="100" value="${after}"></progress></div>`;
    } catch (error) {
        document.getElementById('feedbackStats').textContent = error.message;
    }
}

async function runModelArena() {
    const question = resultState?.question || document.getElementById('prompt').value.trim();
    if (!question) return;
    const referenceSql = window.prompt('Optional reference SQL for execution-accuracy scoring (leave blank for execution-success ranking):') || '';
    try {
        const response = await fetch('/api/model_arena', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({question, reference_sql:referenceSql || null})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Model arena failed.');
        document.getElementById('featurePanel').innerHTML =
            `<strong>Model arena</strong><br>${esc(data.score_definition || data.message || '')}<ol>` +
            (data.leaderboard || []).map(item =>
                `<li>${esc(item.model)} · ${item.execution_accuracy === null ? (item.execution_success ? 'executable' : 'failed') : `${Math.round(item.execution_accuracy * 100)}% exact result match`} · ${item.elapsed_seconds ?? '-'}s${item.error ? ` · ${esc(item.error)}` : ''}<pre>${esc(item.sql || '')}</pre></li>`
            ).join('') + '</ol>';
    } catch (error) {
        document.getElementById('featurePanel').textContent = error.message;
    }
}

async function transpileCurrentSql() {
    try {
        const response = await fetch('/api/transpile', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
                sql:document.getElementById('sqlOut').value,
                dialect:document.getElementById('targetDialect').value
            })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'SQL transpilation failed.');
        document.getElementById('featurePanel').innerHTML =
            `<strong>${esc(data.dialect)} SQL</strong><pre>${esc(data.sql)}</pre>`;
    } catch (error) {
        document.getElementById('featurePanel').textContent = error.message;
    }
}

async function showSecurityReport() {
    try {
        const response = await fetch('/api/security_report');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Security report failed.');
        document.getElementById('featurePanel').innerHTML =
            `<strong>Safety regression suite</strong><br>${data.blocked}/${data.total} blocked (${Math.round(data.block_rate * 100)}%)<ul>` +
            data.cases.map(item => `<li>${esc(item.case)}: ${item.blocked ? 'blocked' : 'NOT BLOCKED'}</li>`).join('') +
            '</ul>';
    } catch (error) {
        document.getElementById('featurePanel').textContent = error.message;
    }
}

async function previewWrite() {
    const sql = document.getElementById('sqlOut').value.trim();
    try {
        const response = await fetch('/api/preview_write', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({sql})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Write preview was rejected.');
        document.getElementById('featurePanel').innerHTML =
            `<strong>Rolled-back write preview</strong><pre>${esc(JSON.stringify(data.preview, null, 2))}</pre>`;
        if (!window.confirm(`Preview affects ${data.preview.affected_rows} row(s). Commit this change now?`)) return;
        const approval = await fetch('/api/approve_write', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({approval_token:data.approval_token})
        });
        const committed = await approval.json();
        if (!approval.ok) throw new Error(committed.error || 'Write approval failed.');
        document.getElementById('featurePanel').innerHTML += '<br><strong>Approved write committed.</strong>';
    } catch (error) {
        document.getElementById('featurePanel').textContent = error.message;
    }
}

const PIPELINE_STEPS = [
    {label: 'Understand the question', stages: ['translate', 'schema', 'classify']},
    {label: 'Write SQL', stages: ['generate', 'repair']},
    {label: 'Check safety', stages: ['validate']},
    {label: 'Run read-only', stages: ['execute']},
    {label: 'Cross-check', stages: ['confidence', 'complete']},
];

function showProgress(message, stage) {
    document.getElementById('progressMsg').textContent = message;
    const el = document.getElementById('stepper');
    let current = PIPELINE_STEPS.findIndex(step => step.stages.includes(stage));
    if (current < 0) current = 0;
    el.innerHTML = PIPELINE_STEPS.map((step, index) =>
        `<span class="step ${index < current ? 'done' : index === current ? 'current' : ''}">${esc(step.label)}</span>`
    ).join('<span class="step-sep" aria-hidden="true"></span>') +
        `<span class="step-note">${esc(message)}</span>`;
    el.classList.add('active');
}

function hideProgress() {
    document.getElementById('stepper').classList.remove('active');
    document.getElementById('progressMsg').textContent = '';
}

function loadStoredList(key) {
    try {
        const value = JSON.parse(localStorage.getItem(key) || '[]');
        return Array.isArray(value) ? value : [];
    } catch (error) {
        console.error(`Could not read ${key} from local storage`, error);
        return [];
    }
}

function saveStoredList(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
        console.error(`Could not save ${key} to local storage`, error);
        setStatus('ok', 'Storage unavailable');
    }
}

function recordQuery(question, successful) {
    recentQueries = [{question, schema:curSchema, successful, at:Date.now()}, ...recentQueries.filter(item => item.question !== question || item.schema !== curSchema)].slice(0, 40);
    saveStoredList('nlpsql-history', recentQueries);
    renderSavedQueries();
}

function renderSavedQueries() {
    const out = document.getElementById('historyOut');
    if (!out) return;
    if (!recentQueries.length) {
        out.innerHTML = '<div class="chart-empty">Your recent questions stay in this browser.</div>';
    } else {
        out.innerHTML = recentQueries.slice(0, 12).map((item, index) => `
            <div class="saved-item"><button type="button" onclick="replayQuery(${index})">${esc(item.question)} <small>· ${esc(item.schema)}</small></button>
            <button type="button" aria-label="Favorite query" onclick="toggleFavorite(${index})">${isFavorite(item) ? '★' : '☆'}</button></div>`).join('');
    }
    const favorites = document.getElementById('favoritesOut');
    favorites.innerHTML = favoriteQueries.length
        ? favoriteQueries.map((item, index) => `<div class="saved-item"><button type="button" onclick="replayFavorite(${index})">${esc(item.question)} <small>· ${esc(item.schema)}</small></button><button type="button" onclick="removeFavorite(${index})" aria-label="Remove favorite">×</button></div>`).join('')
        : '<div class="chart-empty">Favorite a recent query to keep it here.</div>';
}

function renderPins() {
    const out = document.getElementById('pinsOut');
    if (!out) return;
    if (!pinnedResults.length) {
        out.innerHTML = '<div class="chart-empty">Pin a result to keep a local snapshot here.</div>';
        return;
    }
    out.innerHTML = pinnedResults.map((item, index) => `
        <div class="saved-item"><button type="button" onclick="openPin(${index})">${esc(item.question)} <small>· ${esc(item.schema)} · ${item.rows.length} rows</small></button>
        <button type="button" aria-label="Remove pin" onclick="removePin(${index})">×</button></div>`).join('');
}

function isFavorite(item) {
    return favoriteQueries.some(saved => saved.question === item.question && saved.schema === item.schema);
}

function toggleFavorite(index) {
    const item = recentQueries[index];
    if (!item) return;
    favoriteQueries = isFavorite(item)
        ? favoriteQueries.filter(saved => saved.question !== item.question || saved.schema !== item.schema)
        : [{question:item.question, schema:item.schema}, ...favoriteQueries].slice(0, 40);
    saveStoredList('nlpsql-favorites', favoriteQueries);
    renderSavedQueries();
}

function replayQuery(index) {
    const item = recentQueries[index];
    if (!item) return;
    if (item.schema !== curSchema && allSchemas.includes(item.schema)) {
        switchSchema(item.schema).then(() => { setPrompt(item.question); runQuery(); });
        return;
    }
    setPrompt(item.question);
    runQuery();
}

function replayFavorite(index) {
    const item = favoriteQueries[index];
    if (!item) return;
    const historyIndex = recentQueries.findIndex(saved => saved.question === item.question && saved.schema === item.schema);
    if (historyIndex >= 0) replayQuery(historyIndex);
    else {
        setPrompt(item.question);
        runQuery();
    }
}

function removeFavorite(index) {
    favoriteQueries.splice(index, 1);
    saveStoredList('nlpsql-favorites', favoriteQueries);
    renderSavedQueries();
}

function clearHistory() {
    recentQueries = [];
    saveStoredList('nlpsql-history', recentQueries);
    renderSavedQueries();
}

function pinResults() {
    if (!resultState) return;
    pinnedResults = [{
        question: resultState.question,
        schema: curSchema,
        sql: resultState.sql,
        columns: resultState.columns,
        rows: resultState.rows,
        at: Date.now(),
    }, ...pinnedResults].slice(0, 12);
    saveStoredList('nlpsql-pins', pinnedResults);
    renderPins();
    toast('Result pinned. Find it under Pinned in the side panel.');
}

function removePin(index) {
    pinnedResults.splice(index, 1);
    saveStoredList('nlpsql-pins', pinnedResults);
    renderPins();
}

function openPin(index) {
    const item = pinnedResults[index];
    if (!item) return;
    resultState = {...item, truncated:false};
    document.getElementById('prompt').value = item.question;
    document.getElementById('sqlOut').value = item.sql;
    updateSqlHighlight();
    document.getElementById('results').style.display = 'flex';
    document.getElementById('resultToolbar').style.display = 'flex';
    document.getElementById('chartPanel').style.display = item.rows.length ? 'block' : 'none';
    document.getElementById('chartType').value = 'auto';
    renderSummary();
    renderResultsTable();
    renderChart();
}

function updateShareLink(question, schema) {
    const params = new URLSearchParams({q:question, schema});
    history.replaceState(null, '', `${location.pathname}${location.search}#${params}`);
}

async function shareQuery() {
    if (!resultState) return;
    updateShareLink(resultState.question, curSchema);
    const url = location.href;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(url);
            setStatus('ok', 'Link copied');
        } else {
            window.prompt('Copy this shareable query link:', url);
        }
    } catch (error) {
        console.error('Could not copy query link', error);
        window.prompt('Copy this shareable query link:', url);
    }
}

function printReport() {
    if (!resultState) return;
    const report = window.open('', '_blank');
    if (!report) {
        setStatus('ok', 'Allow pop-ups to create a PDF report');
        return;
    }
    report.opener = null;
    const tableRows = [resultState.columns, ...resultState.rows].map(row =>
        `<tr>${row.map(value => `<td>${esc(value ?? '')}</td>`).join('')}</tr>`
    ).join('');
    const chart = document.getElementById('chartOut').innerHTML;
    report.document.write(`<!doctype html><html><head><title>NLPSQL report</title><style>body{font:14px Arial;margin:32px;color:#182230}pre{white-space:pre-wrap;background:#f3f6fb;padding:12px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd4df;padding:6px;text-align:left}svg{max-width:100%;height:auto}.summary{padding:10px;background:#eff6ff}</style></head><body><h1>${esc(resultState.question)}</h1><p>${esc(curSchema)} · ${new Date().toLocaleString()}</p><div class="summary">${esc(document.getElementById('summaryOut').textContent)}</div><h2>Visualization</h2>${chart}<h2>SQL</h2><pre>${esc(resultState.sql)}</pre><h2>Results</h2><table>${tableRows}</table><script>window.onload=()=>window.print();<\/script></body></html>`);
    report.document.close();
}

async function runEditedSql() {
    const sql = document.getElementById('sqlOut').value.trim();
    if (!sql) return;
    showProgress('Validating edited SQL');
    try {
        const response = await fetch('/api/execute_sql', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sql})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `Execution failed (${response.status})`);
        if (!resultState) resultState = {question:document.getElementById('prompt').value, columns:[], rows:[], truncated:false};
        resultState = {...resultState, sql, columns:data.columns, rows:data.rows, truncated:data.truncated};
        document.getElementById('sqlOut').value = sql;
        updateSqlHighlight();
        document.getElementById('resultToolbar').style.display = 'flex';
        document.getElementById('chartPanel').style.display = data.rows.length ? 'block' : 'none';
        renderSummary();
        renderResultsTable();
        renderChart();
    } catch (error) {
        document.getElementById('tableOut').innerHTML = `<div class="err-box">${esc(error.message)}</div>`;
    } finally {
        hideProgress();
    }
}

function renderFollowups() {
    if (!resultState) return;
    const questions = [];
    if (resultState.columns.some(column => /date|month|year|time|hired|created/i.test(String(column)))) {
        questions.push(`Break this down by month: ${resultState.question}`);
    }
    if (resultState.rows.length > 10) questions.push(`Show the top 10 results for: ${resultState.question}`);
    if (resultState.rows.length > 1) questions.push(`Sort the results descending: ${resultState.question}`);
    document.getElementById('followups').innerHTML = questions.map(question =>
        `<button class="sug-btn" onclick="runFollowup('${encodeURIComponent(question).replace(/'/g, '%27')}')">↗ ${esc(question.split(':')[0])}</button>`
    ).join('');
}

function runFollowup(encodedQuestion) {
    setPrompt(decodeURIComponent(encodedQuestion));
    runQuery();
}

function renderAutocomplete() {
    const hints = new Set(recentQueries.map(item => item.question));
    currentSchemaTables.forEach(table => {
        hints.add(`Show all rows from ${table.name}`);
        table.columns.forEach(column => hints.add(`Show ${column.name} from ${table.name}`));
    });
    document.getElementById('queryHints').innerHTML = [...hints].slice(0, 100).map(text => `<option value="${esc(text)}"></option>`).join('');
}

function applyChartEdit(question) {
    const lower = question.toLowerCase();
    const chartTypes = ['bar', 'line', 'pie', 'scatter', 'map'];
    const requested = chartTypes.find(type => lower.includes(`${type} chart`) || lower.includes(`make it ${type}`));
    if (requested) document.getElementById('chartType').value = requested;
    if (/sort .*desc|descending|highest first/.test(lower)) {
        const index = numericColumnIndexes()[0];
        if (index !== undefined) resultSort = {column:index, direction:-1};
    } else if (/sort .*asc|ascending|lowest first/.test(lower)) {
        const index = numericColumnIndexes()[0];
        if (index !== undefined) resultSort = {column:index, direction:1};
    }
}

function startVoiceInput() {
    if (voiceRecorder?.state === 'recording') {
        voiceRecorder.stop();
        return;
    }
    if (preferLocalVoice) {
        startLocalVoiceRecording();
        return;
    }
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        preferLocalVoice = true;
        startLocalVoiceRecording();
        return;
    }
    const recognition = new SpeechRecognition();
    voiceRecognition = recognition;
    recognition.lang = document.getElementById('speechLanguage').value;
    recognition.interimResults = false;
    recognition.onstart = () => setStatus('busy', 'Listening… speak now');
    recognition.onresult = event => {
        setPrompt(event.results[0][0].transcript);
        document.getElementById('prompt').focus();
        setStatus('ok', 'Voice captured');
    };
    recognition.onerror = event => {
        voiceRecognition = null;
        if (event.error === 'network') {
            preferLocalVoice = true;
            setStatus('busy', 'Browser speech service unavailable; checking local voice…');
            startLocalVoiceRecording();
            return;
        }
        const messages = {
            'not-allowed': 'Microphone access is blocked. Allow microphone access for this app.',
            'audio-capture': 'No microphone was found. Connect a microphone and try again.',
            'no-speech': 'No speech detected. Click the microphone and try again.',
            'service-not-allowed': 'Browser speech service is blocked. Trying local voice…',
        };
        if (event.error === 'service-not-allowed') {
            preferLocalVoice = true;
            startLocalVoiceRecording();
            return;
        }
        setStatus('ok', messages[event.error] || `Voice input failed: ${event.error}`);
    };
    recognition.onend = () => {
        if (voiceRecognition === recognition) voiceRecognition = null;
    };
    try {
        recognition.start();
    } catch (error) {
        voiceRecognition = null;
        setStatus('ok', `Could not start voice input: ${error.message}`);
    }
}

async function startLocalVoiceRecording() {
    if (voiceStarting) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        setStatus('ok', 'Local recording requires a browser with microphone recording support.');
        return;
    }
    voiceStarting = true;
    try {
        const statusResponse = await fetch('/api/voice/status');
        if (!statusResponse.ok) throw new Error('Could not check local voice availability.');
        const voiceStatus = await statusResponse.json();
        if (!voiceStatus.local_whisper_available) {
            setStatus('ok', 'Browser speech needs network access. For offline fallback, install faster-whisper with requirements-voice.txt and restart.');
            voiceStarting = false;
            return;
        }
        voiceStream = await navigator.mediaDevices.getUserMedia({audio:true});
        const formats = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
        const mimeType = formats.find(type => MediaRecorder.isTypeSupported(type));
        voiceRecorder = new MediaRecorder(voiceStream, mimeType ? {mimeType} : undefined);
        voiceChunks = [];
        const button = document.getElementById('voiceBtn');
        button.classList.add('voice-recording');
        button.setAttribute('aria-label', 'Stop local voice recording');
        button.title = 'Recording locally — click to stop';
        voiceRecorder.ondataavailable = event => {
            if (event.data.size) voiceChunks.push(event.data);
        };
        voiceRecorder.onerror = event => {
            clearTimeout(voiceStopTimer);
            button.classList.remove('voice-recording');
            button.setAttribute('aria-label', 'Start voice input');
            button.title = 'Voice input; local Whisper is used if browser recognition cannot connect';
            voiceStream?.getTracks().forEach(track => track.stop());
            voiceStream = null;
            voiceStarting = false;
            setStatus('ok', `Local recording failed: ${event.error?.message || 'recording error'}`);
        };
        voiceRecorder.onstop = async () => {
            clearTimeout(voiceStopTimer);
            button.classList.remove('voice-recording');
            button.setAttribute('aria-label', 'Start voice input');
            button.title = 'Voice input; local Whisper is used if browser recognition cannot connect';
            const recording = new Blob(voiceChunks, {type:voiceRecorder.mimeType || 'audio/webm'});
            voiceChunks = [];
            voiceStream.getTracks().forEach(track => track.stop());
            voiceStream = null;
            if (!recording.size) {
                setStatus('ok', 'No audio was recorded. Try again and speak after recording starts.');
                return;
            }
            setStatus('busy', 'Transcribing locally… first use may download the Whisper model.');
            try {
                const language = encodeURIComponent(document.getElementById('speechLanguage').value);
                const response = await fetch(`/api/voice/transcribe?language=${language}`, {
                    method:'POST',
                    headers:{'Content-Type':recording.type || 'audio/webm'},
                    body:recording,
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Local transcription failed.');
                if (!data.text) throw new Error('No speech detected. Try again and speak clearly.');
                setPrompt(data.text);
                document.getElementById('prompt').focus();
                setStatus('ok', 'Voice transcribed locally');
            } catch (error) {
                setStatus('ok', error.message);
            }
        };
        voiceRecorder.start();
        voiceStarting = false;
        setStatus('busy', 'Recording locally — click the microphone to stop');
        voiceStopTimer = setTimeout(() => {
            if (voiceRecorder?.state === 'recording') voiceRecorder.stop();
        }, 12000);
    } catch (error) {
        voiceStarting = false;
        if (voiceStream) voiceStream.getTracks().forEach(track => track.stop());
        voiceStream = null;
        setStatus('ok', error.name === 'NotAllowedError'
            ? 'Microphone access is blocked. Allow microphone access and try again.'
            : `Local voice input failed: ${error.message}`);
    }
}

function speakSummary() {
    if (!('speechSynthesis' in window)) {
        setStatus('ok', 'Text-to-speech is not supported in this browser');
        return;
    }
    const text = document.getElementById('summaryOut').textContent;
    if (!text) {
        setStatus('ok', 'Run a query before reading its summary');
        return;
    }
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

function updateSqlHighlight() {
    const textarea = document.getElementById('sqlOut');
    const highlight = document.getElementById('sqlHighlight');
    if (!textarea || !highlight) return;
    const sql = textarea.value;
    const escaped = esc(sql);
    const tokens = /(--[^\n]*|'(?:''|[^'])*'|\b(?:SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|DISTINCT|COUNT|SUM|AVG|MIN|MAX|CASE|WHEN|THEN|ELSE|END|AND|OR|NOT|NULL|IS|IN|LIKE|BETWEEN|UNION|ALL|ASC|DESC|WITH|OVER|PARTITION|INTO|VALUES|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER)\b|\b\d+(?:\.\d+)?\b)/gi;
    highlight.innerHTML = escaped.replace(tokens, token => {
        const type = token.startsWith('--') ? 'comment' : token.startsWith("'") ? 'string' : /^\d/.test(token) ? 'number' : 'keyword';
        return `<span class="sql-${type}">${token}</span>`;
    }) + '\n';
    syncSqlHighlight();
}

function syncSqlHighlight() {
    const textarea = document.getElementById('sqlOut');
    const highlight = document.getElementById('sqlHighlight');
    if (textarea && highlight) {
        highlight.scrollTop = textarea.scrollTop;
        highlight.scrollLeft = textarea.scrollLeft;
    }
}

function openCommandPalette() {
    document.getElementById('commandOverlay').classList.add('open');
    document.getElementById('commandSearch').focus();
}

function closeCommandPalette() {
    document.getElementById('commandOverlay').classList.remove('open');
}

function filterCommands(value) {
    document.querySelectorAll('.command-list button').forEach(button => {
        button.hidden = !button.textContent.toLowerCase().includes(value.toLowerCase());
    });
}

document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openCommandPalette();
    } else if (event.key === 'Escape') closeCommandPalette();
    else if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') runQuery();
});

function renderErDiagram(relationships) {
    const host = document.getElementById('erCanvas');
    const tables = currentSchemaTables;
    const width = Math.max(420, tables.length * 190);
    const positions = new Map(tables.map((table, index) => [table.name, {x:25 + index * 190, y:35 + (index % 2) * 145}]));
    let svg = `<svg viewBox="0 0 ${width} 360" role="img" aria-label="Database entity relationship diagram">`;
    (relationships || []).forEach(relation => {
        const from = positions.get(relation.table), to = positions.get(relation.ref_table);
        if (from && to) svg += `<line x1="${from.x + 145}" y1="${from.y + 22}" x2="${to.x}" y2="${to.y + 22}" stroke="var(--purple)" stroke-width="2"><title>${esc(relation.table)}.${esc(relation.column)} → ${esc(relation.ref_table)}.${esc(relation.ref_column)}</title></line>`;
    });
    tables.forEach(table => {
        const point = positions.get(table.name);
        svg += `<rect x="${point.x}" y="${point.y}" width="145" height="90" rx="8" fill="var(--card)" stroke="var(--border-a)"></rect><text x="${point.x + 10}" y="${point.y + 20}" class="chart-label">${esc(table.name)}</text>`;
        table.columns.slice(0, 4).forEach((column, index) => {
            svg += `<text x="${point.x + 10}" y="${point.y + 40 + index * 13}" class="chart-label">${esc(column.name)} · ${esc(column.type)}</text>`;
        });
    });
    host.innerHTML = svg + '</svg>';
    host.hidden = false;
}

async function showErDiagram() {
    const response = await fetch('/api/schema_detail');
    const data = await response.json();
    renderErDiagram(data.relationships || []);
}

async function previewTable(table) {
    const response = await fetch(`/api/sample?table=${encodeURIComponent(table)}`);
    const data = await response.json();
    const panel = document.getElementById('samplePanel');
    panel.hidden = false;
    panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    if (!response.ok) {
        panel.innerHTML = `<div class="err-box">${esc(data.error || 'Could not load sample rows.')}</div>`;
        return;
    }
    panel.innerHTML = `<div class="panel-hdr">${esc(table)} · first ${data.rows.length} rows</div><div class="tbl-wrap"><table><thead><tr>${data.columns.map(column => `<th>${esc(column)}</th>`).join('')}</tr></thead><tbody>${data.rows.map(row => `<tr>${row.map(value => `<td>${esc(value ?? 'NULL')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

async function connectDatabase() {
    const engine = window.prompt('Database engine (sqlite, duckdb, postgresql, or mysql):', 'sqlite');
    if (!engine) return;
    const normalizedEngine = engine.toLowerCase();
    if (!['sqlite','duckdb','postgresql','mysql'].includes(normalizedEngine)) {
        setStatus('ok', 'Choose SQLite, DuckDB, PostgreSQL, or MySQL.');
        return;
    }
    const localFile = ['sqlite','duckdb'].includes(normalizedEngine);
    const connection = window.prompt(localFile ? 'Local database file path (opened read-only):' : `${normalizedEngine} connection string (use a read-only database account):`);
    if (!connection) return;
    const body = localFile ? {engine:normalizedEngine, path:connection} : {engine:normalizedEngine, connection_string:connection};
    const response = await fetch('/api/connect_database', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) {
        setStatus('ok', data.error || 'Database connection failed');
        return;
    }
    curSchema = data.schema_name;
    if (!allSchemas.includes(curSchema)) allSchemas.push(curSchema);
    renderTabs();
    await refreshSchemaPanel();
    setStatus('ok', 'Connected read-only');
}

function handleChartEdit(event) {
    if (!resultState) return;
    const request = event.currentTarget.value;
    applyChartEdit(request);
    renderResultsTable();
    renderChart();
}

function toggleTheme() {
    const theme = document.body.classList.contains('theme-light') ? 'dark' : 'light';
    localStorage.setItem('nlpsql-theme-v2', theme);
    applyTheme(theme);
}

function applyTheme(theme) {
    const light = theme === 'light';
    document.body.classList.toggle('theme-light', light);
    document.body.classList.toggle('theme-dark', !light);
    const themeBtn = document.getElementById('themeBtn');
    const label = light ? 'Switch to dark theme' : 'Switch to light theme';
    themeBtn.setAttribute('aria-label', label);
    themeBtn.title = label;
    themeBtn.innerHTML = light
        ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/></svg>'
        : '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
}

function isNumericValue(value) {
    return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function numericColumnIndexes() {
    if (!resultState || !resultState.rows.length) return [];
    return resultState.columns.map((_, index) => index).filter(index => {
        const values = resultState.rows.map(row => row[index]).filter(value => value !== null && value !== undefined && value !== '');
        return values.length > 0 && values.every(isNumericValue);
    });
}

function formatValue(value) {
    return isNumericValue(value) ? Number(value).toLocaleString(undefined, {maximumFractionDigits: 2}) : String(value);
}

function renderSummary() {
    const summary = document.getElementById('summaryOut');
    const {columns, rows, truncated} = resultState;
    if (!rows.length) {
        summary.textContent = 'The query ran but no rows matched.';
        return;
    }
    if (rows.length === 1 && columns.length === 1) {
        summary.textContent = `${columns[0]}: ${formatValue(rows[0][0])}`;
        return;
    }
    let text = `${rows.length}${truncated ? '+' : ''} row${rows.length === 1 ? '' : 's'} across ${columns.length} column${columns.length === 1 ? '' : 's'}.`;
    // Skip id-like columns: averaging them says nothing.
    const numericIndex = numericColumnIndexes().find(index => !/(^|_)id$/i.test(String(columns[index])));
    if (numericIndex !== undefined && rows.length > 1) {
        const values = rows.map(row => row[numericIndex]).filter(isNumericValue).map(Number);
        const average = values.reduce((total, value) => total + value, 0) / values.length;
        text += ` Average ${columns[numericIndex]}: ${formatValue(average)} (range ${formatValue(Math.min(...values))} to ${formatValue(Math.max(...values))}).`;
    }
    if (truncated) text += ' The result was capped, so more rows exist.';
    summary.textContent = text;
}

function renderResultsTable() {
    if (!resultState) return;
    const {columns, rows, truncated} = resultState;
    const filteredRows = rows.map((row, index) => ({row, index})).filter(({row}) =>
        !resultFilter || row.some(value => String(value ?? '').toLocaleLowerCase().includes(resultFilter.toLocaleLowerCase()))
    );
    if (resultSort.column >= 0) {
        const {column, direction} = resultSort;
        filteredRows.sort((left, right) => {
            const a = left.row[column];
            const b = right.row[column];
            if (isNumericValue(a) && isNumericValue(b)) return (Number(a) - Number(b)) * direction;
            return String(a ?? '').localeCompare(String(b ?? ''), undefined, {numeric: true, sensitivity: 'base'}) * direction;
        });
    }
    const pageCount = Math.max(1, Math.ceil(filteredRows.length / resultPageSize));
    resultPage = Math.min(resultPage, pageCount - 1);
    const start = resultPage * resultPageSize;
    const pageRows = filteredRows.slice(start, start + resultPageSize);
    const out = document.getElementById('tableOut');
    if (!pageRows.length) {
        out.innerHTML = `<div class="empty-res">${rows.length ? 'No rows match this filter.' : 'The query ran but matched no rows.'}</div>`;
        if (rows.length) out.innerHTML += renderTableControls(0, pageCount);
        return;
    }
    let html = `<div class="table-toolbar">
        <input class="table-control table-filter" type="search" aria-label="Filter results" placeholder="Filter results…" value="${esc(resultFilter)}" oninput="filterResults(this.value)">
        <label>Rows per page <select class="table-control" onchange="changePageSize(this.value)">
            ${[10, 25, 50].map(size => `<option value="${size}"${size === resultPageSize ? ' selected' : ''}>${size}</option>`).join('')}
        </select></label>
    </div><div class="tbl-wrap"><table><thead><tr>`;
    const numericColumns = new Set(numericColumnIndexes());
    columns.forEach((column, index) => {
        const indicator = resultSort.column === index ? (resultSort.direction === 1 ? ' ▲' : ' ▼') : '';
        html += `<th${numericColumns.has(index) ? ' class="num"' : ''} aria-sort="${resultSort.column === index ? (resultSort.direction === 1 ? 'ascending' : 'descending') : 'none'}" role="button" tabindex="0" onclick="sortResults(${index})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortResults(${index})}">${esc(String(column))}${indicator}</th>`;
    });
    html += '</tr></thead><tbody>';
    pageRows.forEach(({row}) => {
        html += '<tr>' + row.map((value, index) =>
            `<td${numericColumns.has(index) ? ' class="num"' : ''}>${value !== null && value !== undefined ? esc(String(value)) : '<em>empty</em>'}</td>`
        ).join('') + '</tr>';
    });
    html += '</tbody></table></div>';
    html += `<div class="row-ct">${truncated ? 'First ' : ''}${rows.length} row${rows.length !== 1 ? 's' : ''}${resultFilter ? ` · ${filteredRows.length} matching` : ''}</div>`;
    html += renderTableControls(filteredRows.length, pageCount);
    out.innerHTML = html;
}

function renderTableControls(rowCount, pageCount) {
    const first = rowCount ? resultPage * resultPageSize + 1 : 0;
    const last = Math.min((resultPage + 1) * resultPageSize, rowCount);
    return `<div class="pagination"><span>${first}–${last} of ${rowCount}</span>
        <button class="tool-btn" type="button" onclick="changePage(-1)" ${resultPage === 0 ? 'disabled' : ''}>Previous</button>
        <span>Page ${resultPage + 1} of ${pageCount}</span>
        <button class="tool-btn" type="button" onclick="changePage(1)" ${resultPage >= pageCount - 1 ? 'disabled' : ''}>Next</button></div>`;
}

function filterResults(value) {
    const input = document.querySelector('.table-filter');
    const selection = input ? input.selectionStart : value.length;
    resultFilter = value;
    resultPage = 0;
    renderResultsTable();
    const nextInput = document.querySelector('.table-filter');
    if (nextInput) {
        nextInput.focus();
        nextInput.setSelectionRange(selection, selection);
    }
}

function changePage(delta) {
    if (delta === -1 || delta === 1) resultPage += delta;
    else if ([10, 25, 50].includes(Number(delta))) {
        resultPageSize = Number(delta);
        resultPage = 0;
    }
    renderResultsTable();
}

function changePageSize(value) {
    changePage(Number(value));
}

function sortResults(column) {
    if (resultSort.column === column) resultSort.direction *= -1;
    else resultSort = {column, direction: 1};
    renderResultsTable();
}

function exportResults(format) {
    if (!resultState) return;
    let contents;
    let mimeType;
    let extension;
    if (format === 'csv') {
        const quote = value => {
            let text = String(value ?? '');
            if (typeof value === 'string' && /^[=+\-@\t\r]/.test(text)) text = "'" + text;
            return `"${text.replace(/"/g, '""')}"`;
        };
        contents = [resultState.columns, ...resultState.rows].map(row => row.map(quote).join(',')).join('\r\n');
        mimeType = 'text/csv;charset=utf-8';
        extension = 'csv';
    } else if (format === 'excel') {
        const xmlEscape = value => String(value ?? '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const rows = [resultState.columns, ...resultState.rows].map(row =>
            `<Row>${row.map(value => {
                const numeric = isNumericValue(value);
                return `<Cell><Data ss:Type="${numeric ? 'Number' : 'String'}">${xmlEscape(value)}</Data></Cell>`;
            }).join('')}</Row>`
        ).join('');
        contents = `<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Results"><Table>${rows}</Table></Worksheet></Workbook>`;
        mimeType = 'application/vnd.ms-excel;charset=utf-8';
        extension = 'xls';
    } else {
        contents = JSON.stringify({
            question: resultState.question,
            sql: resultState.sql,
            columns: resultState.columns,
            rows: resultState.rows,
        }, null, 2);
        mimeType = 'application/json;charset=utf-8';
        extension = 'json';
    }
    const blob = new Blob([contents], {type: mimeType});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `nlpsql-results.${extension}`;
    link.click();
    URL.revokeObjectURL(url);
}

function renderChart() {
    const chart = document.getElementById('chartOut');
    if (!resultState || !resultState.rows.length) {
        chart.innerHTML = '<div class="chart-empty">No rows available to visualize.</div>';
        return;
    }
    const numericIndexes = numericColumnIndexes();
    const categoryIndex = resultState.columns.findIndex((_, index) => !numericIndexes.includes(index));
    const dateLike = categoryIndex >= 0 && /date|time|month|year/i.test(String(resultState.columns[categoryIndex]));
    const hasLocation = resultState.columns.some(column => /city|state|location/i.test(String(column)));
    const requestedType = document.getElementById('chartType').value;
    const type = requestedType === 'auto' ? (hasLocation ? 'map' : dateLike ? 'line' : numericIndexes.length > 1 && categoryIndex < 0 ? 'scatter' : 'bar') : requestedType;
    const rows = resultState.rows.slice(0, 20);
    if (type === 'map') {
        chart.innerHTML = renderGeoMap(rows, numericIndexes[0]);
        return;
    }
    const categoryLabel = categoryIndex >= 0 ? String(resultState.columns[categoryIndex]) : 'Row';
    if (type === 'scatter') {
        if (numericIndexes.length < 2) {
            chart.innerHTML = '<div class="chart-empty">Scatter plots need at least two numeric columns.</div>';
            return;
        }
        chart.innerHTML = renderScatter(rows, numericIndexes[0], numericIndexes[1]);
        return;
    }
    const measureIndex = numericIndexes.find(index => index !== categoryIndex);
    if (measureIndex === undefined) {
        chart.innerHTML = '<div class="chart-empty">No numeric column is available for a chart.</div>';
        return;
    }
    const points = rows.map((row, index) => ({
        label: categoryIndex >= 0 ? String(row[categoryIndex] ?? '') : `Row ${index + 1}`,
        rawValue: row[measureIndex],
    })).filter(point => isNumericValue(point.rawValue)).map(point => ({
        label: point.label,
        value: Number(point.rawValue),
    }));
    const title = `${resultState.columns[measureIndex]} by ${categoryLabel}`;
    if (type === 'pie') {
        chart.innerHTML = renderPie(points, title);
        return;
    }
    chart.innerHTML = renderCartesian(points, type, title);
}

function renderGeoMap(rows, measureIndex) {
    const locationIndex = resultState.columns.findIndex(column => /city|state|location/i.test(String(column)));
    if (locationIndex < 0) return '<div class="chart-empty">A city, state, location, or region column is required for the geo map.</div>';
    const locations = {
        'andhra pradesh':[16.5,80.6], 'arunachal pradesh':[28.2,94.7], assam:[26.2,92.9], bihar:[25.6,85.1],
        chhattisgarh:[21.3,81.6], goa:[15.5,73.8], gujarat:[22.3,71.2], haryana:[29.1,76.1],
        'himachal pradesh':[31.1,77.2], jharkhand:[23.6,85.3], karnataka:[15.3,75.7], kerala:[10.8,76.3],
        'madhya pradesh':[23.5,77.4], maharashtra:[19.7,75.7], manipur:[24.8,93.9], meghalaya:[25.5,91.3],
        mizoram:[23.2,92.8], nagaland:[26.2,94.1], odisha:[20.9,85.1], punjab:[31.1,75.3],
        rajasthan:[26.9,73.8], sikkim:[27.5,88.5], tamilnadu:[11.1,78.7], 'tamil nadu':[11.1,78.7],
        telangana:[17.9,79.0], tripura:[23.8,91.3], 'uttar pradesh':[26.8,80.9], uttarakhand:[30.1,79.0],
        'west bengal':[22.9,87.8], delhi:[28.6,77.2], mumbai:[19.1,72.9], bengaluru:[12.9,77.6],
        bangalore:[12.9,77.6], chennai:[13.1,80.3], hyderabad:[17.4,78.5], kolkata:[22.6,88.4],
        pune:[18.5,73.9], ahmedabad:[23.0,72.6], jaipur:[26.9,75.8], lucknow:[26.8,80.9],
    };
    const recognized = rows.map(row => {
        const name = String(row[locationIndex] ?? '').trim().toLowerCase();
        return {name:row[locationIndex], point:locations[name], value:measureIndex !== undefined && isNumericValue(row[measureIndex]) ? Number(row[measureIndex]) : 1};
    }).filter(item => item.point);
    if (!recognized.length) return '<div class="chart-empty">No recognized Indian state/city names in the first 20 rows; other regions need coordinates.</div>';
    let svg = '<svg viewBox="0 0 640 430" role="img" aria-label="India geo bubble map"><title>India data locations</title><rect class="geo-bg" x="30" y="25" width="580" height="360" rx="14"></rect>';
    [0,1,2,3,4].forEach(index => {
        const x = 70 + index * 130, y = 55 + index * 70;
        svg += `<line class="geo-grid" x1="${x}" y1="35" x2="${x}" y2="375"></line><line class="geo-grid" x1="40" y1="${y}" x2="600" y2="${y}"></line>`;
    });
    recognized.forEach(item => {
        const [lat, lon] = item.point;
        const x = 70 + (lon - 68) / 30 * 520;
        const y = 45 + (37 - lat) / 31 * 320;
        svg += `<circle class="geo-mark" cx="${x}" cy="${y}" r="8"><title>${esc(item.name)}${Number.isFinite(item.value) ? `: ${esc(item.value)}` : ''}</title></circle><text class="chart-label" x="${x + 8}" y="${y - 7}">${esc(item.name)}</text>`;
    });
    return svg + '</svg>';
}

function renderCartesian(points, type, title) {
    if (!points.length) return '<div class="chart-empty">No numeric values available for a chart.</div>';
    const width = 720, height = 300, left = 48, right = 18, top = 26, bottom = 54;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const min = Math.min(0, ...points.map(point => point.value));
    const max = Math.max(0, ...points.map(point => point.value));
    const range = max - min || 1;
    const y = value => top + (max - value) / range * plotHeight;
    const baseline = y(0);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)} ${type} chart"><title>${esc(title)}</title>`;
    [0, .5, 1].forEach(fraction => {
        const value = min + range * fraction;
        const yPos = y(value);
        svg += `<line class="chart-grid" x1="${left}" y1="${yPos}" x2="${width - right}" y2="${yPos}"></line><text class="chart-label" x="${left - 7}" y="${yPos + 4}" text-anchor="end">${esc(value.toLocaleString(undefined, {maximumFractionDigits: 2}))}</text>`;
    });
    const step = plotWidth / points.length;
    if (type === 'line') {
        const coords = points.map((point, index) => `${left + step * (index + .5)},${y(point.value)}`);
        svg += `<polyline class="chart-line" points="${coords.join(' ')}"></polyline>`;
        points.forEach((point, index) => {
            const x = left + step * (index + .5), yPos = y(point.value);
            svg += `<circle class="chart-mark" cx="${x}" cy="${yPos}" r="4"><title>${esc(point.label)}: ${esc(point.value.toLocaleString())}</title></circle>`;
        });
    } else {
        const barWidth = Math.max(2, step * .68);
        points.forEach((point, index) => {
            const x = left + step * index + (step - barWidth) / 2;
            const yPos = y(point.value);
            svg += `<rect class="chart-mark" x="${x}" y="${Math.min(yPos, baseline)}" width="${barWidth}" height="${Math.max(1, Math.abs(baseline - yPos))}" rx="3"><title>${esc(point.label)}: ${esc(point.value.toLocaleString())}</title></rect>`;
        });
    }
    points.forEach((point, index) => {
        const x = left + step * (index + .5);
        svg += `<text class="chart-label" x="${x}" y="${height - 24}" text-anchor="middle">${esc(point.label.slice(0, 12))}</text>`;
    });
    return svg + '</svg>';
}

function renderScatter(rows, xIndex, yIndex) {
    const pairs = rows.filter(row => isNumericValue(row[xIndex]) && isNumericValue(row[yIndex]))
        .map(row => ({x: Number(row[xIndex]), y: Number(row[yIndex])}))
        .filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
    if (!pairs.length) return '<div class="chart-empty">No numeric pairs available for a scatter plot.</div>';
    const width = 720, height = 300, left = 54, right = 20, top = 24, bottom = 42;
    const xMin = Math.min(...pairs.map(point => point.x)), xMax = Math.max(...pairs.map(point => point.x));
    const yMin = Math.min(...pairs.map(point => point.y)), yMax = Math.max(...pairs.map(point => point.y));
    const x = value => left + (value - xMin) / (xMax - xMin || 1) * (width - left - right);
    const y = value => top + (yMax - value) / (yMax - yMin || 1) * (height - top - bottom);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Scatter plot of ${esc(resultState.columns[xIndex])} and ${esc(resultState.columns[yIndex])}"><title>${esc(resultState.columns[yIndex])} by ${esc(resultState.columns[xIndex])}</title>`;
    pairs.forEach(point => {
        svg += `<circle class="chart-mark" cx="${x(point.x)}" cy="${y(point.y)}" r="5"><title>${esc(point.x.toLocaleString())}, ${esc(point.y.toLocaleString())}</title></circle>`;
    });
    svg += `<text class="chart-label" x="${width / 2}" y="${height - 8}" text-anchor="middle">${esc(resultState.columns[xIndex])}</text>`;
    svg += `<text class="chart-label" x="14" y="${height / 2}" transform="rotate(-90 14 ${height / 2})" text-anchor="middle">${esc(resultState.columns[yIndex])}</text>`;
    return svg + '</svg>';
}

function renderPie(points, title) {
    const positivePoints = points.filter(point => point.value > 0);
    const total = positivePoints.reduce((sum, point) => sum + point.value, 0);
    if (!positivePoints.length || !Number.isFinite(total)) return '<div class="chart-empty">Pie charts need positive numeric values.</div>';
    const colors = ['#4f8ef7', '#a78bfa', '#10b981', '#f59e0b', '#f472b6', '#22d3ee'];
    const cx = 180, cy = 145, radius = 112;
    let angle = -Math.PI / 2;
    let svg = `<svg viewBox="0 0 720 300" role="img" aria-label="${esc(title)} pie chart"><title>${esc(title)}</title>`;
    positivePoints.forEach((point, index) => {
        const nextAngle = angle + point.value / total * Math.PI * 2;
        const x1 = cx + radius * Math.cos(angle), y1 = cy + radius * Math.sin(angle);
        const x2 = cx + radius * Math.cos(nextAngle), y2 = cy + radius * Math.sin(nextAngle);
        const largeArc = nextAngle - angle > Math.PI ? 1 : 0;
        svg += `<path d="M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z" fill="${colors[index % colors.length]}"><title>${esc(point.label)}: ${esc(point.value.toLocaleString())}</title></path>`;
        const legendY = 30 + index * 23;
        if (legendY < 286) svg += `<rect x="365" y="${legendY - 10}" width="10" height="10" rx="2" fill="${colors[index % colors.length]}"></rect><text class="chart-label" x="382" y="${legendY}">${esc(point.label.slice(0, 28))}: ${esc(point.value.toLocaleString())}</text>`;
        angle = nextAngle;
    });
    return svg + '</svg>';
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('prompt').addEventListener('keypress', e => {
        if (e.key === 'Enter') runQuery();
    });
});

// ── Upload Modal ──────────────────────────────────────────────────────────────
const FILE_ICONS = {sql:'🗃', csv:'📊', json:'📋', pdf:'📄', png:'🖼', jpg:'🖼', jpeg:'🖼'};

function openUpload()  { document.getElementById('overlay').classList.add('open'); }
function closeUpload() { document.getElementById('overlay').classList.remove('open'); clearFile(); setUploadStatus(''); }
function overlayClick(e) { if (e.target === document.getElementById('overlay')) closeUpload(); }

function onDragOver(e)  { e.preventDefault(); document.getElementById('dropZone').classList.add('drag-over'); }
function onDragLeave(e) { document.getElementById('dropZone').classList.remove('drag-over'); }
function onDrop(e) {
    e.preventDefault();
    document.getElementById('dropZone').classList.remove('drag-over');
    const files = [...e.dataTransfer.files];
    if (files.length) setFile(files);
}
function onFileSelect(e) { if (e.target.files.length) setFile([...e.target.files]); }

function setFile(files) {
    pendingFile = Array.isArray(files) ? files : [files];
    if (pendingFile.length > 8) {
        setUploadStatus('Select no more than 8 files at a time.', 'error');
        pendingFile = null;
        document.getElementById('uploadBtn').disabled = true;
        return;
    }
    const imageFiles = pendingFile.filter(file => /\.(png|jpe?g)$/i.test(file.name));
    if (imageFiles.length && (imageFiles.length > 1 || pendingFile.length > 1)) {
        setUploadStatus('Process one image at a time, separately from data files.', 'error');
        pendingFile = null;
        document.getElementById('uploadBtn').disabled = true;
        return;
    }
    const first = pendingFile[0];
    const ext = first.name.split('.').pop().toLowerCase();
    const totalSize = pendingFile.reduce((total, file) => total + file.size, 0);
    if (totalSize > 20 * 1024 * 1024 || pendingFile.some(file => file.size > 8 * 1024 * 1024)) {
        setUploadStatus('Uploads are limited to 8 MB per file and 20 MB combined.', 'error');
        pendingFile = null;
        document.getElementById('uploadBtn').disabled = true;
        return;
    }
    document.getElementById('fileIcon').textContent = FILE_ICONS[ext] || '📄';
    document.getElementById('fileName').textContent = pendingFile.length === 1 ? first.name : `${pendingFile.length} files`;
    document.getElementById('fileSize').textContent = formatBytes(totalSize);
    document.getElementById('filePreview').classList.add('show');
    document.getElementById('uploadBtn').disabled = false;
    document.getElementById('uploadPreview').innerHTML = '';
    setUploadStatus('');
    if (!imageFiles.length) previewUploadFiles();
}

function clearFile() {
    pendingFile = null;
    document.getElementById('filePreview').classList.remove('show');
    document.getElementById('uploadBtn').disabled = true;
    document.getElementById('fileInput').value = '';
    document.getElementById('uploadPreview').innerHTML = '';
    setUploadStatus('');
}

function setUploadStatus(msg, type='info') {
    const el = document.getElementById('uploadStatus');
    el.className = 'upload-status' + (msg ? ' ' + type : '');
    el.textContent = msg;
}

async function doUpload() {
    if (!pendingFile || !pendingFile.length) return;
    const btn = document.getElementById('uploadBtn');
    btn.disabled = true;
    btn.textContent = 'Importing…';
    setUploadStatus('Reading file…', 'info');

    try {
        const isImage = /\.(png|jpe?g)$/i.test(pendingFile[0].name);
        if (isImage) {
            setUploadStatus('Running local OCR…', 'info');
            const imageContent = await readFileAsBase64(pendingFile[0]);
            const ocrResponse = await fetch('/api/ocr', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filename:pendingFile[0].name, content_b64:imageContent})});
            const ocrResult = await ocrResponse.json();
            if (!ocrResponse.ok) throw new Error(ocrResult.error || 'OCR failed');
            document.getElementById('uploadPreview').innerHTML = `<p>Review and format OCR text as comma-separated values, including a header row:</p><textarea id="ocrData" class="sql-editor" style="min-height:130px">${esc(ocrResult.text)}</textarea><button class="tool-btn" type="button" onclick="useOcrData()">Use edited OCR text as CSV</button>`;
            setUploadStatus('OCR complete. Review the extracted text before importing it as CSV.', 'success');
            btn.disabled = true;
            btn.textContent = 'Import Schema';
            return;
        }
        const filePayloads = await Promise.all(pendingFile.map(async (file, index) => {
            const typeOverrides = {};
            document.querySelectorAll(`[data-file-index="${index}"][data-column]`).forEach(select => {
                typeOverrides[select.dataset.column] = select.value;
            });
            return {filename:file.name, content_b64:await readFileAsBase64(file), type_overrides:typeOverrides};
        }));
        setUploadStatus('Uploading to server…', 'info');

        const resp = await fetch('/api/upload_schema', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({files:filePayloads})
        }).then(r => r.json());

        if (resp.error) {
            setUploadStatus('❌ ' + resp.error, 'error');
            btn.disabled = false;
            btn.textContent = 'Import Schema';
            return;
        }

        // Success
        const tabName = resp.schema_name;
        uploadedSet.add(tabName);
        if (!allSchemas.includes(tabName)) allSchemas.push(tabName);

        // Add custom suggestions
        SUGGESTIONS[tabName] = resp.table_names.flatMap(t => [
            `Show all rows from ${t}`,
            `Count the rows in ${t}`,
        ]);

        setUploadStatus(`✓ Loaded "${tabName}" — ${resp.table_count} table(s), ${resp.row_count} row(s) total`, 'success');

        // Switch to the uploaded schema
        renderTabs();
        setTimeout(async () => {
            closeUpload();
            await switchSchema(tabName);
        }, 1200);
    } catch(e) {
        setUploadStatus('❌ ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Import Schema';
    }
}

function useOcrData() {
    const text = document.getElementById('ocrData').value;
    if (!text.trim()) {
        setUploadStatus('OCR returned no text. Try a clearer image.', 'error');
        return;
    }
    pendingFile = [new File([text], 'ocr_table.csv', {type:'text/csv'})];
    document.getElementById('fileName').textContent = 'ocr_table.csv';
    document.getElementById('fileSize').textContent = formatBytes(new Blob([text]).size);
    document.getElementById('uploadPreview').innerHTML = '';
    document.getElementById('uploadBtn').disabled = false;
    previewUploadFiles();
}

async function previewUploadFiles() {
    if (!pendingFile || !pendingFile.length) return;
    const unsupported = pendingFile.find(file => !/\.(sql|csv|json|pdf)$/i.test(file.name));
    if (unsupported) {
        setUploadStatus(`Unsupported file type: ${esc(unsupported.name)}`, 'error');
        return;
    }
    try {
        const files = await Promise.all(pendingFile.map(async file => ({
            filename:file.name,
            content_b64:await readFileAsBase64(file),
        })));
        const response = await fetch('/api/preview_upload', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({files})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not preview upload');
        const uploadPreviewData = data.tables || [];
        document.getElementById('uploadPreview').innerHTML = uploadPreviewData.map(table => `
            <section><strong>${esc(table.name)}</strong><div class="tbl-wrap"><table>
            <thead><tr>${table.columns.map(column => `<th>${esc(column.name)} ${table.type_editable ? `<select class="type-select" data-file-index="${table.file_index}" data-column="${esc(column.name)}"><option${column.type==='INTEGER'?' selected':''}>INTEGER</option><option${column.type==='REAL'?' selected':''}>REAL</option><option${column.type==='TEXT'?' selected':''}>TEXT</option></select>` : `<span class="ct">${esc(column.type)}</span>`}</th>`).join('')}</tr></thead>
            <tbody>${table.rows.map(row => `<tr>${row.map(value => `<td>${esc(value ?? 'NULL')}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`).join('');
        if (data.relationships?.length) {
            document.getElementById('uploadPreview').innerHTML += `<p>Detected links: ${data.relationships.map(item => `${esc(item.table)}.${esc(item.column)} → ${esc(item.ref_table)}.${esc(item.ref_column)}`).join(' · ')}</p>`;
        }
    } catch (error) {
        setUploadStatus(error.message, 'error');
    }
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            // result is "data:<mime>;base64,<data>"
            const b64 = reader.result.split(',')[1];
            resolve(b64);
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
    return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatBytes(n) {
    if (n < 1024) return n + ' B';
    if (n < 1024*1024) return (n/1024).toFixed(1) + ' KB';
    return (n/1024/1024).toFixed(1) + ' MB';
}

function setStatus(type, msg) {
    const el = document.getElementById('statusDot');
    if (type === 'busy') {
        el.textContent = msg;
        el.className = 'status-dot busy';
        return;
    }
    el.textContent = 'Connected';
    el.className = 'status-dot';
    if (msg && msg !== 'Connected') {
        toast(msg, /^error$|error|could not|not supported|allow pop-ups|choose |unavailable|failed/i.test(msg) ? 'bad' : 'ok');
    }
}

function toast(message, kind) {
    const host = document.getElementById('toasts');
    const item = document.createElement('div');
    item.className = 'toast' + (kind === 'bad' ? ' bad' : '');
    item.textContent = message;
    host.appendChild(item);
    setTimeout(() => item.remove(), kind === 'bad' ? 6000 : 3000);
}

// ── Layout helpers ────────────────────────────────────────────────────────────
function selectResultTab(name) {
    document.querySelectorAll('.tabs .tab').forEach(tab => {
        tab.setAttribute('aria-selected', String(tab.id === 'tab-view-' + name));
    });
    document.querySelectorAll('#results .pane').forEach(pane => { pane.hidden = pane.id !== 'pane-' + name; });
    if (name === 'sql') updateSqlHighlight();
    if (name === 'chart') renderChart();
}

function selectRailTab(name) {
    ['schema', 'history', 'pins'].forEach(key => {
        document.getElementById('railtab-' + key).setAttribute('aria-selected', String(key === name));
        document.getElementById('railpane-' + key).hidden = key !== name;
    });
    if (window.matchMedia('(max-width: 900px)').matches) setRail(true);
}

function setRail(open) {
    document.body.classList.toggle('rail-open', open);
    document.getElementById('railToggle').setAttribute('aria-expanded', String(open));
}
function toggleRail() { setRail(!document.body.classList.contains('rail-open')); }

async function copySql() {
    const sql = document.getElementById('sqlOut').value;
    if (!sql) return;
    try {
        await navigator.clipboard.writeText(sql);
    } catch (error) {
        const field = document.getElementById('sqlOut');
        field.select();
        document.execCommand('copy');
    }
    toast('SQL copied');
}

// Arrow keys move between tabs, as screen-reader and keyboard users expect.
document.addEventListener('keydown', event => {
    const tab = event.target.closest && event.target.closest('[role="tab"]');
    if (!tab || !['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...tab.parentElement.querySelectorAll('[role="tab"]')];
    let index = tabs.indexOf(tab);
    if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') index = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === 'Home') index = 0;
    else index = tabs.length - 1;
    event.preventDefault();
    tabs[index].focus();
    tabs[index].click();
});

function openShortcuts() { document.getElementById('shortcutsOverlay').classList.add('open'); }
function closeShortcuts() { document.getElementById('shortcutsOverlay').classList.remove('open'); }

document.addEventListener('keydown', event => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable;
    if (event.key === 'Escape') {
        closeShortcuts();
        setRail(false);
    } else if (!typing && !event.ctrlKey && !event.metaKey && event.key === '/') {
        event.preventDefault();
        document.getElementById('prompt').focus();
    } else if (!typing && event.key === '?') {
        event.preventDefault();
        openShortcuts();
    }
});

// Boot
init();
