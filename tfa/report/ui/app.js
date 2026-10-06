/* The multi-year analysis screen.
 *
 * Every number shown is measured, not simulated. The only presentation liberty
 * is CADENCE: real events are queued and drained at a readable pace, because a
 * two-second run is not something a person can watch. Nothing is invented, and
 * a figure with no cell behind it is shown as a gap rather than as a zero.
 */
'use strict';

const CADENCE = { read: 60, assess: 5, anomaly: 260, other: 120 };
const REVEAL_BUDGET_MS = 3500;

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (t, c, h) => { const n = document.createElement(t);
  if (c) n.className = c; if (h != null) n.innerHTML = h; return n; };
const esc = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const ms = n => (n >= 1000 ? (n / 1000).toFixed(2) + ' s' : Math.round(n) + ' ms');
const usd = n => '$' + Number(n || 0).toFixed(4);

/* Indian grouping: the reader of this is reading a return. */
function money(v) {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (!isFinite(n)) return esc(String(v));
  const neg = n < 0;
  let whole = String(Math.abs(Math.round(n)));
  if (whole.length > 3) {
    let head = whole.slice(0, -3); const tail = whole.slice(-3); const parts = [];
    while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2); }
    if (head) parts.unshift(head);
    whole = parts.concat([tail]).join(',');
  }
  return (neg ? '−' : '') + whole;
}
const pct = (v, p = 1) => v == null ? '—' : (Number(v) * 100).toFixed(p) + '%';
const ay = s => String(s || '').replace('AY ', '');

const S = { files: [], findings: [], anomalies: [], matrix: null, trends: null,
            attendance: null, report: null, preflight: null, running: false,
            hasRun: false, client: null, status: null };

/* ============================================================== status ==== */
async function refreshStatus() {
  let st;
  try { st = await (await fetch('/api/status')).json(); } catch (e) { return; }
  S.status = st;
  S.client = st.client;
  if (st.session_charge) renderCharges(null, st.session_charge);
  renderClients(st.clients || [], st.client);

  const n = $('#aislot');
  if (!n) return;
  n.className = 'sect';
  n.innerHTML = '<div class="sh">Model judgment</div><div class="ai ' +
    (st.ai_available ? 'on' : 'off') + '" id="aibody"></div>';
  $('#aibody', n).innerHTML = st.ai_available
    ? `<span class="badge">on</span><br><code>${esc(st.model)}</code><br>
       key ${esc(st.key)} from ${esc(st.key_source)}<br>
       ceiling ${st.max_model_calls} calls per analysis`
    : `<span class="badge">off</span><br>${esc(st.ai_reason)}.
       Every deterministic rule still runs; the judgment rules and the open
       comparison report as not assessed. Put the key in the <code>env</code>
       file and press Run.`;
}

function renderClients(clients, current) {
  const box = $('#clients');
  if (!box) return;
  if (!clients.length) {
    box.innerHTML = '<div class="ai off" style="font-size:11.5px">No client folder yet.'
      + ' Create <code>filings/&lt;CLIENT-CODE&gt;/</code> and drop the ITR and 3CD'
      + ' JSON files in.</div>';
    return;
  }
  box.innerHTML = '';
  clients.forEach(c => {
    const b = el('button', '', `<span>${esc(c.client_id)}</span>`
      + `<span class="n">${c.files} file${c.files === 1 ? '' : 's'}`
      + `${c.declared ? '' : ' · no context'}</span>`);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(c.client_id === current));
    b.onclick = async () => {
      if (S.running) return;
      await fetch('/api/client', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: c.client_id }) });
      location.reload();
    };
    box.appendChild(b);
  });
}

/* ==================================================== the canonical rules ==
 * Rendered from /api/rules — the catalogue the engine actually loaded — so this
 * tab cannot drift from what runs. */
let RULES = null;

async function loadRules() {
  try { RULES = await (await fetch('/api/rules')).json(); }
  catch (e) {
    $('#tab-rules').innerHTML = '<div class="empty">Could not load the rules.</div>';
    return;
  }
  renderRules();
}

function ruleStats() {
  const by = {};
  (S.findings || []).forEach(f => {
    const row = by[f.rule_id] || (by[f.rule_id] = { instances: 0, FAIL: 0, PASS: 0,
      NOT_APPLICABLE: 0, INSUFFICIENT_EVIDENCE: 0 });
    row.instances++;
    if (row[f.verdict] != null) row[f.verdict]++;
  });
  return by;
}

let ruleFilter = { text: '', method: 'ALL' };

function renderRules() {
  const d = RULES;
  if (!d) return;
  if (d.error) { $('#tab-rules').innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
  const stats = ruleStats();
  const c = d.counts;

  let html = `
  <div class="confid"><b>Internal — this is the method.</b> The questions, the
  thresholds, the fact each rule reads and the provision each one tests. None of
  it goes to a client: the client sees observations and published sections only.
  A rule that is not in this file cannot produce a finding.</div>

  <div class="tiles" style="margin:0 0 20px">
    <div class="tile"><div class="n">${c.declared}</div><div class="l">Canonical rules</div></div>
    <div class="tile"><div class="n">${c.deterministic}</div><div class="l">Decided by Python</div></div>
    <div class="tile"><div class="n">${c.judgment}</div><div class="l">Model judgment</div></div>
    <div class="tile"><div class="n">${c.per_year + c.per_pair}</div><div class="l">Instantiated per year</div></div>
    <div class="tile"><div class="n negt">${c.high}</div><div class="l">High severity</div></div>
    <div class="tile"><div class="n">${d.fact_map.facts}</div><div class="l">Facts in the dictionary</div></div>
  </div>

  <p class="lede">Catalogue <b>v${esc(d.version)}</b> ·
    <span class="mono">${esc(d.path)}</span> ·
    digest <span class="mono">${esc((d.digest || '').split(':').pop().slice(0, 12))}</span>
    · authority pack ${esc(d.pack)} (<span class="${d.pack_status === 'verified' ? '' : 'warnt'}">${
      esc(d.pack_status)}</span>, ${d.provisions_unverified} of ${d.provisions}
    provisions not yet checked against the bare Act by a person)
    · fact dictionary v${esc(d.fact_map.version)}.
    ${Object.keys(stats).length ? 'Instance counts below are from the last run.'
      : 'Run an analysis to see how each rule behaved.'}</p>

  <div class="rulebar">
    <input id="rulesearch" type="search" placeholder="Filter by rule, question, fact or section…"
      value="${esc(ruleFilter.text)}">
    ${['ALL', 'rule', 'llm'].map(m =>
      `<button class="fbtn" data-m="${m}" aria-pressed="${ruleFilter.method === m}"
        >${m === 'ALL' ? 'All' : m === 'rule' ? 'Deterministic' : 'Judgment'}</button>`).join('')}
  </div>
  <div id="rulelist"></div>

  <h2 class="h" style="margin-top:30px">The doctrine these rules run under</h2>
  <p class="lede">Thresholds live here rather than inside a rule: a number buried in
  code is a policy nobody agreed to. Changing one changes every finding that leans
  on it, and the manifest records which values produced the run.</p>
  <div class="card">
    ${(d.doctrine.articles || []).map(a => `<details><summary><b>${esc(a.id)}</b> —
      ${esc(a.title)}</summary><div class="body lede" style="max-width:76ch">${
      esc(a.text)}</div></details>`).join('')}
  </div>
  <div class="card scroll" style="margin-top:12px"><table><thead><tr>
    <th>Threshold</th><th class="right">Value</th></tr></thead><tbody>
    ${Object.entries(d.doctrine.parameters || {}).map(([k, v]) =>
      `<tr><td class="mono">${esc(k)}</td><td class="right nums">${esc(v)}</td></tr>`)
      .join('')}
  </tbody></table></div>

  <h2 class="h" style="margin-top:30px">The fact dictionary</h2>
  <p class="lede">What the rules are allowed to read, and where each fact is looked
  for inside the JSON. A fact found by searching for a field name rather than at a
  known path is marked as such wherever it is used — real returns move fields
  between schedules from one year to the next, and the honest answer to that is a
  list of candidate paths a person can extend.</p>
  <div class="card scroll"><table><thead><tr><th>Fact</th><th>Group</th><th>From</th>
    <th>Kind</th><th>Clause</th><th>Where it is looked for</th></tr></thead><tbody>
    ${d.fact_map.specs.map(s => `<tr><td class="mono">${esc(s.id)}
      <div class="faint" style="font-size:11px">${esc(s.label)}</div></td>
      <td class="muted">${esc(s.group)}</td>
      <td><span class="tag ${s.scope === 'derived' ? 'mute' : s.scope === 'itr' ? 'rule' : 'llm'}">${
        s.scope === '3cd' ? 'audit report' : s.scope === 'itr' ? 'return' : 'computed'}</span></td>
      <td class="muted">${esc(s.kind)}${s.aggregate && s.aggregate !== 'first'
        ? ` <span class="faint">(${esc(s.aggregate)})</span>` : ''}</td>
      <td class="muted">${esc(s.clause || '—')}</td>
      <td class="mono faint" style="font-size:10.5px;word-break:break-all">${
        s.expr ? esc(s.expr) : esc((s.paths || []).join('  ·  ') || (s.keys || []).join(', '))}</td>
      </tr>`).join('')}
  </tbody></table></div>`;

  $('#tab-rules').innerHTML = html;
  $('#rulesearch').oninput = e => { ruleFilter.text = e.target.value; renderRuleList(); };
  $$('#tab-rules .fbtn').forEach(b => b.onclick = () => {
    ruleFilter.method = b.dataset.m;
    $$('#tab-rules .fbtn').forEach(x => x.setAttribute('aria-pressed',
      String(x === b)));
    renderRuleList();
  });
  renderRuleList();
}

function renderRuleList() {
  const d = RULES;
  if (!d) return;
  const stats = ruleStats();
  const q = ruleFilter.text.trim().toLowerCase();
  let html = '';
  d.themes.forEach(theme => {
    let rules = d.rules.filter(r => r.theme === theme.id);
    if (ruleFilter.method !== 'ALL')
      rules = rules.filter(r => r.method === ruleFilter.method);
    if (q) rules = rules.filter(r => (
      r.id + ' ' + r.question + ' ' + r.rule + ' ' + (r.facts || []).join(' ') + ' ' +
      r.authority.map(a => a.citation + ' ' + a.title).join(' ') + ' ' + r.vulnerability
    ).toLowerCase().includes(q));
    if (!rules.length) return;
    html += `<div class="ckgroup"><h3>${esc(theme.label)}
      <span class="n">${rules.length} rule${rules.length === 1 ? '' : 's'}</span></h3>`;
    rules.forEach(r => {
      const st = stats[r.id];
      const badges = [
        `<span class="tag ${r.method === 'rule' ? 'rule' : 'llm'}">${
          r.method === 'rule' ? 'rule' : 'judgment'}</span>`,
        r.for_each === 'year' ? '<span class="tag year">per year</span>' :
        r.for_each === 'year_pair' ? '<span class="tag pair">per year pair</span>' : '',
        `<span class="tag ${r.severity === 'high' ? 'neg' : 'mute'}">${esc(r.severity)}</span>`,
        st ? `<span class="tag ${st.FAIL ? 'neg' : 'pos'}">${st.instances} run${
          st.FAIL ? ` · ${st.FAIL} raised` : ''}</span>` : '',
      ].filter(Boolean).join('');

      html += `<details class="ck">
        <summary><span class="cid">${esc(r.id)}</span>
          <span class="cq">${esc(r.question)}</span>
          <span class="cmeta">${badges}</span></summary>
        <div class="cbody"><dl>
          <dt>Authority</dt><dd>${r.authority.length ? r.authority.map(a =>
            `<div style="margin-bottom:6px">${esc(a.citation)} — ${esc(a.title)}
             <span class="faint">(${esc(a.instrument)})</span>${
             a.verified ? '' : ' <span class="tag warn">unverified</span>'}${
             a.effective_from ? ` <span class="faint">in force from ${
               esc(a.effective_from)}</span>` : ''}
             <div class="lede" style="margin:3px 0 0">${esc(a.summary)}</div></div>`)
            .join('') : '<span class="faint">—</span>'}</dd>
          <dt>Decided by</dt><dd>${r.method === 'rule'
            ? `the deterministic rule <span class="mono">${esc(r.rule)}</span>`
            : 'model judgment against the rubric below, with the provision quoted from the pack'}</dd>
          ${(r.facts || []).length ? `<dt>Facts read</dt><dd>${
            r.facts.map(f => `<span class="mono">${esc(f)}</span>`).join(', ')}</dd>` : ''}
          ${Object.keys(r.params || {}).filter(k => !['enquiry', 'left_label', 'right_label'].includes(k)).length
            ? `<dt>Parameters</dt><dd class="mono" style="font-size:11.5px">${
              esc(JSON.stringify(r.params, null, 0))}</dd>` : ''}
          ${r.requires.length ? `<dt>Depends on</dt><dd>${r.requires.map(x =>
            `<span class="mono">${esc(x)}</span>`).join(', ')} — skipped if unsatisfied</dd>` : ''}
          ${r.applicability.length ? `<dt>Applies when</dt><dd>${r.applicability.map(x =>
            `<span class="mono">${esc(x)}</span>`).join(', ')}</dd>` : ''}
          ${r.rubric ? `<dt>Rubric</dt><dd><div class="rubric">${esc(r.rubric)}</div></dd>` : ''}
          ${r.vulnerability ? `<dt>What it exposes</dt><dd>${esc(r.vulnerability)}</dd>` : ''}
          ${r.params && r.params.enquiry ? `<dt>Standing enquiry</dt><dd>${
            esc(r.params.enquiry)}</dd>` : ''}
        </dl></div></details>`;
    });
    html += '</div>';
  });
  $('#rulelist').innerHTML = html || '<div class="empty">Nothing matches that filter.</div>';
}



/* ================================================================ charts ==
 * The SVG comes from the server, drawn by Python from the same cells the rules
 * read. Nothing here decides what to plot; it lays out what was declared, and
 * says which parts of a declaration had no data. */
let CHARTS = null;

function chartCard(c, compact) {
  const legend = [...c.series, ...c.overlay].map(s =>
    `<span><i class="${c.overlay.includes(s) ? 'dash' : ''}"
      style="${c.overlay.includes(s)
        ? 'border-color:' + s.colour : 'background:' + s.colour}"></i>${
      esc(s.label)}${c.overlay.includes(s) ? ' — right-hand scale' : ''}</span>`
  ).join('');
  const gaps = c.series.concat(c.overlay).flatMap(s =>
    s.points.filter(p => p.value == null).map(p => ay(p.ay)));
  const missing = (c.missing || []).length
    ? `<div class="gapnote">Not plotted, no figure in any year: ${
        c.missing.map(esc).join(', ')}.</div>` : '';
  const gapnote = gaps.length
    ? `<div class="gapnote">No figure for ${[...new Set(gaps)].join(', ')}; the line
       breaks rather than joining across it.</div>` : '';
  return `<div class="chartcard">
    <span class="cid">${esc(c.id)}</span>
    <h4>${esc(c.title)}</h4>
    ${compact ? '' : `<div class="note">${esc(c.note)}</div>`}
    ${c.svg || '<div class="empty">Not enough figures to draw this.</div>'}
    <div class="legend">${legend}</div>
    ${gapnote}${missing}
    ${compact ? `<div class="note" style="margin:10px 0 4px">${esc(c.note)}</div>` : ''}
  </div>`;
}

function renderCharts(d) {
  CHARTS = d;
  const groups = new Map((d.groups || []).map(g => [g.id, g.label]));
  const byGroup = new Map();
  (d.charts || []).forEach(c => {
    if (!byGroup.has(c.group)) byGroup.set(c.group, []);
    byGroup.get(c.group).push(c);
  });
  let html = `
    <h2 class="h" style="margin-top:18px">The factual picture</h2>
    <p class="lede">Drawn by program from the fact matrix — ${d.drawable} of
    ${(d.charts || []).length} declared charts had enough figures. Every point
    carries the cell it came from: hover it for the year and the value. These are
    the same drawings the report carries and the same series the inference layer
    is given in writing, so a conclusion can always be checked against the picture
    a reader is looking at.</p>`;
  byGroup.forEach((charts, group) => {
    html += `<h3 class="h" style="margin:24px 0 8px">${
      esc(groups.get(group) || group)}</h3>`;
    html += charts.map(c => chartCard(c, false)).join('');
  });
  $('#tab-charts').innerHTML = html;

  const counter = $('.lede', block('charts'));
  if (counter) counter.innerHTML =
    `${d.drawable} chart(s) drawn from the matrix. The <b>Charts</b> tab has all of
     them; the report carries the ${(d.in_report || []).length} the catalogue marks
     as standard.`;
  const h = holder('charts');
  (d.charts || []).filter(c => c.report && c.drawable).slice(0, 2)
    .forEach(c => h.insertAdjacentHTML('beforeend', chartCard(c, true)));
}

/* ============================================================= inference ==
 * The only place a model is used. Each card shows what was put to it — the
 * brief — beside what came back, because an inference nobody can audit is an
 * opinion. */
const INFERENCES = [];

function inferenceCard(i) {
  const tag = { REPORTABLE: 'neg', EXPLAINED: 'pos', NOTHING_ARISING: 'mute',
                INSUFFICIENT_EVIDENCE: 'warn' }[i.verdict] || 'mute';
  const label = { REPORTABLE: 'to report', EXPLAINED: 'explained',
                  NOTHING_ARISING: 'nothing arising',
                  INSUFFICIENT_EVIDENCE: 'cannot tell' }[i.verdict] || i.verdict;
  const aberrations = (i.aberrations || []).map(a => `<div class="abr">
      <div><div class="ay">${esc(ay(a.ay))}</div>
        <span class="tag ${a.corroborated === 'contradicted' ? 'neg'
          : a.corroborated === 'corroborated' ? 'pos' : 'mute'}">${
          esc(a.corroborated || 'neither')}</span></div>
      <div><div class="what">${esc(a.what)}</div>
        ${a.corroboration_detail
          ? `<div class="corr">${esc(a.corroboration_detail)}</div>` : ''}
        ${(a.evidence || []).length
          ? `<div class="ev">${a.evidence.slice(0, 4).map(esc).join('<br>')}</div>` : ''}
      </div></div>`).join('');
  const correlations = (i.correlations || []).map(c => `<div class="corrline">
      <span class="f ${c.factor === 'none' ? 'none' : ''}">${
        esc(c.factor === 'none' ? 'nothing on file explains it'
          : (c.factor_label || c.factor))}</span>${
        c.ay ? ` · ${esc(ay(c.ay))}` : ''} — ${esc(c.effect)}</div>`).join('');
  const hypotheses = (i.hypotheses || []).map(h => `<div class="hyp">
      <div class="ex">${esc(h.explanation)}</div>
      <div class="test"><b>Confirmed if</b> ${esc(h.confirms_if || '—')}</div>
      ${h.refutes_if ? `<div class="test"><b>Refuted if</b> ${esc(h.refutes_if)}</div>` : ''}
      ${h.document ? `<div class="doc"><b>Call for:</b> ${esc(h.document)}</div>` : ''}
    </div>`).join('');

  return `<div class="infcard ${esc(i.verdict)}">
    <div class="top">
      <span class="tag ${tag}">${esc(label)}</span>
      <span class="rid">${esc(i.id)}</span>
      <span class="tag mute">${esc(i.topic.replace(/_/g, ' '))}</span>
      ${i.ran ? `<span class="tag mute">confidence ${Number(i.confidence).toFixed(2)}</span>` : ''}
      ${i.years_reasoned_over ? `<span class="tag year">${i.years_reasoned_over} years</span>` : ''}
      <span class="q">${esc(i.title)}</span>
    </div>
    ${i.ran ? `<p class="head">${esc(i.headline)}</p>` : `
      <div class="note" style="margin:0">Not assessed. ${esc(i.reason)}</div>`}
    ${aberrations ? `<h5>What departs from its own trend</h5>${aberrations}` : ''}
    ${correlations ? `<h5>Correlated against what else was true in those years</h5>${correlations}` : ''}
    ${hypotheses ? `<h5>Candidate explanations, and what would settle each</h5>${hypotheses}` : ''}
    ${i.vulnerability ? `<h5>What it exposes</h5><div class="vuln" style="border:0;padding:0">${esc(i.vulnerability)}</div>` : ''}
    ${(i.charts || []).length ? `<div class="gapnote" style="margin-top:12px">Read
      alongside ${i.charts.map(esc).join(', ')} on the Charts tab.</div>` : ''}
    ${(i.notes || []).length ? `<div class="notes">${i.notes.map(esc).join(' · ')}</div>` : ''}
    ${i.brief ? `<details class="briefbox"><summary>The brief this was drawn from —
      what the model was given, and nothing else</summary>
      <div class="body"><pre>${esc(i.brief.text)}</pre></div></details>` : ''}
  </div>`;
}

function renderInference() {
  const counts = INFERENCES.reduce((a, i) => {
    a[i.verdict] = (a[i.verdict] || 0) + 1; return a;
  }, {});
  $('#tab-inference').innerHTML = `
    <h2 class="h" style="margin-top:18px">What the years suggest when read together</h2>
    <p class="lede">Every use of a model in this system is a declared entry in the
    inference catalogue, put to it over a brief that Python wrote: the measured
    series, the findings arithmetic had already settled, and the declared background
    of those years. The model gets no raw JSON and no free run of the matrix — which
    is why each card can be opened to show exactly what it was given.</p>
    <div class="tiles" style="margin:0 0 18px">
      <div class="tile neg"><div class="n negt">${counts.REPORTABLE || 0}</div>
        <div class="l">To report</div></div>
      <div class="tile"><div class="n">${counts.EXPLAINED || 0}</div>
        <div class="l">Explained by the years</div></div>
      <div class="tile"><div class="n faint">${counts.NOTHING_ARISING || 0}</div>
        <div class="l">Nothing arising</div></div>
      <div class="tile"><div class="n warnt">${counts.INSUFFICIENT_EVIDENCE || 0}</div>
        <div class="l">Cannot tell</div></div>
    </div>
    ${INFERENCES.map(inferenceCard).join('')}`;
}

/* ============================================================ audit plan == */
function renderPlan(d) {
  const c = d.counts;
  $('#tab-plan').innerHTML = `
    <h2 class="h" style="margin-top:18px">Audit programme for these years</h2>
    <p class="lede">${esc(d.note)} Assembled by program from what the rules found,
    what the series did and what the inference layer named — ${c.areas} area(s),
    ${c.procedures} procedures. Ordered by what this analysis found rather than by
    convention.</p>
    <div class="tiles" style="margin:0 0 18px">
      <div class="tile neg"><div class="n negt">${c.high}</div>
        <div class="l">High priority</div></div>
      <div class="tile"><div class="n warnt">${c.medium}</div>
        <div class="l">Medium</div></div>
      <div class="tile"><div class="n faint">${c.low}</div>
        <div class="l">Low</div></div>
      <div class="tile"><div class="n">${c.procedures}</div>
        <div class="l">Procedures</div></div>
      <div class="tile"><div class="n">${d.amount ? money(d.amount) : '—'}</div>
        <div class="l">Amounts in issue</div></div>
    </div>
    ${(d.areas || []).map(a => `<div class="parea">
      <div class="hd"><span class="pri ${esc(a.priority)}">${esc(a.priority)}</span>
        <span class="lbl">${esc(a.label)}</span>
        <span class="amt">${a.amount ? money(a.amount) : ''}${
          a.years.length ? ' · ' + a.years.map(ay).join(', ') : ''}</span></div>
      <div class="why">${esc(a.why)}</div>
      <div class="because"><b>On the plan because:</b>
        ${a.reasons.map(r => `<span class="mono">${esc(r.ref)}</span>`).join(' ')}</div>
      <ol>${a.procedures.map(p => `<li class="${p.from_inference ? 'fromai' : ''}"
        ${p.from_inference ? `data-src="${esc(p.from_inference)}"` : ''}>${esc(p.text)}
        ${(p.authority || []).length
          ? `<span class="auth">${p.authority.map(esc).join(', ')}</span>` : ''}</li>`)
        .join('')}</ol>
    </div>`).join('')}
    ${(d.omitted || []).length ? `<h2 class="h" style="margin-top:26px">Areas not on
      the plan</h2><p class="lede">Listed so the programme can be reviewed for what
      it left out as well as what it selected.</p>
      <div class="card">${d.omitted.map(o =>
        `<div class="omitted"><b>${esc(o.label)}</b> — ${esc(o.reason)}</div>`)
        .join('')}</div>` : ''}`;

  const counter = $('.lede', block('plan'));
  if (counter) counter.innerHTML =
    `${c.areas} area(s) and ${c.procedures} procedures, ${c.high} of them high
     priority. The <b>Audit plan</b> tab has the programme.`;
}

/* ============================================================== register ==
 * The durable side. Everything above is a function of the filings and is
 * recomputed on every run; this is what people decided about it, and it has to
 * still be here next week. */
let REGISTER = null;
let registerFilter = 'live';
const ACTOR_KEY = 'tfa.actor';

async function loadRegister() {
  try { REGISTER = await (await fetch('/api/register')).json(); }
  catch (e) {
    $('#tab-register').innerHTML =
      '<div class="empty">Could not reach the register.</div>';
    return;
  }
  renderRegister();
}

function actor() {
  let who = '';
  try { who = localStorage.getItem(ACTOR_KEY) || ''; } catch (e) { who = ''; }
  if (!who) {
    who = (prompt('Your initials, for the record against this decision:') || '').trim();
    if (!who) return '';
    try { localStorage.setItem(ACTOR_KEY, who); } catch (e) { /* not essential */ }
  }
  return who;
}

const LIVE_STATES = ['open', 'accepted', 'amended'];

function renderRegister() {
  const d = REGISTER;
  if (!d) return;
  const store = d.store || {};
  if (!store.enabled) {
    $('#tab-register').innerHTML = `
      <div class="dbline off"><b>Nothing is being recorded.</b>
      ${esc(store.reason || 'no database is configured')}.
      The analysis runs exactly as it does now — what is lost without a database
      is this register: which matters were accepted or rejected, by whom, and
      which enquiries have been answered. That is the one part a re-run cannot
      reconstruct.
      <div style="margin-top:7px">For one machine:
      <code>python3 -m tfa.store.local start</code>, then put the URL it prints
      into <code>TFA_DB_URL</code> in the <code>env</code> file. For anything
      shared, point it at a Postgres of TFA's own.</div></div>`;
    return;
  }

  const counts = d.counts || {};
  const matters = d.matters || [];
  const shown = registerFilter === 'all' ? matters
    : registerFilter === 'live' ? matters.filter(m => LIVE_STATES.includes(m.state))
    : matters.filter(m => m.state === registerFilter);
  const open = counts.open || 0;
  const exposure = matters.filter(m => LIVE_STATES.includes(m.state))
    .reduce((a, m) => a + Number(m.amount_inr || 0), 0);

  $('#tab-register').innerHTML = `
    <div class="dbline">Recorded in ${esc(store.server || 'Postgres')} ·
      <code>${esc(store.url)}</code> ·
      ${(d.runs || []).length} run(s) on record for this client.
      A decision here survives the next run: a matter is identified by the
      figures that produced it, so the same figures keep your note and changed
      figures raise a new matter rather than inheriting it.</div>

    <div class="tiles" style="margin:0 0 18px">
      <div class="tile neg"><div class="n negt">${open}</div>
        <div class="l">Open, undecided</div></div>
      <div class="tile"><div class="n">${counts.accepted || 0}</div>
        <div class="l">Accepted</div></div>
      <div class="tile"><div class="n">${counts.rejected || 0}</div>
        <div class="l">Rejected</div></div>
      <div class="tile"><div class="n faint">${counts.resolved || 0}</div>
        <div class="l">No longer arising</div></div>
      <div class="tile"><div class="n faint">${counts.superseded || 0}</div>
        <div class="l">Superseded</div></div>
      <div class="tile"><div class="n">${exposure ? money(exposure) : '—'}</div>
        <div class="l">Amounts still open</div></div>
    </div>

    <div class="filters" style="margin:0 0 12px">
      ${[['live', 'Live ' + shownCount(matters, 'live')],
         ['open', 'Open ' + (counts.open || 0)],
         ['accepted', 'Accepted ' + (counts.accepted || 0)],
         ['rejected', 'Rejected ' + (counts.rejected || 0)],
         ['resolved', 'No longer arising ' + (counts.resolved || 0)],
         ['superseded', 'Superseded ' + (counts.superseded || 0)],
         ['all', 'All ' + matters.length]].map(([k, label]) =>
        `<button data-s="${k}" aria-pressed="${registerFilter === k}">${esc(label)}</button>`
      ).join('')}
    </div>

    <div id="mlist">${shown.map(matterRow).join('')
      || '<div class="empty">Nothing on the register under that filter.</div>'}</div>

    ${(d.enquiries || []).length ? `
      <h2 class="h" style="margin-top:30px">Enquiries outstanding</h2>
      <p class="lede">Raised by a rule, queued once per matter, and closed when the
      answer is recorded. A matter is never asked about twice.</p>
      <div class="card">${d.enquiries.map(enquiryRow).join('')}</div>` : ''}

    <h2 class="h" style="margin-top:30px">Runs on record</h2>
    <div class="card scroll"><table><thead><tr><th>When</th><th>Years</th>
      <th class="right">Raised</th><th class="right">Amounts</th>
      <th class="right">AI cost</th><th>Rules</th><th>Manifest</th>
      </tr></thead><tbody>
      ${(d.runs || []).map(r => `<tr>
        <td>${esc(String(r.finished_at).slice(0, 16).replace('T', ' '))}</td>
        <td class="muted">${esc((r.years || []).map(ay).join(', '))}</td>
        <td class="right nums">${r.findings_raised}</td>
        <td class="right nums">${money(r.exposure_inr)}</td>
        <td class="right nums faint">${usd(r.ai_usd)}</td>
        <td class="mono faint">v${esc(r.rules_version)}</td>
        <td class="mono faint" style="font-size:10.5px">${
          esc(String(r.manifest_digest || '').split(':').pop().slice(0, 12))}</td>
      </tr>`).join('')}
    </tbody></table></div>`;

  $$('#tab-register .filters button').forEach(b => b.onclick = () => {
    registerFilter = b.dataset.s;
    renderRegister();
  });
  wireRegisterActions();
}

function shownCount(matters, which) {
  return which === 'live'
    ? matters.filter(m => LIVE_STATES.includes(m.state)).length : matters.length;
}

function matterRow(m) {
  const closed = ['resolved', 'superseded'].includes(m.state);
  return `<div class="mrow ${esc(m.severity || 'low')}${closed ? ' closed' : ''}"
      data-key="${esc(m.matter_key)}">
    <div class="top">
      <span class="state ${esc(m.state)}">${esc(m.state.replace('_', ' '))}</span>
      <span class="rid">${esc(m.rule_id)}</span>
      ${m.ay ? `<span class="tag year">${esc(ay(m.ay))}</span>` : ''}
      <span class="tag ${m.severity === 'high' ? 'neg' : 'mute'}">${esc(m.severity)}</span>
      ${m.amount_inr ? `<span class="amt ${m.state === 'open' ? 'negt' : 'muted'}">${
        money(m.amount_inr)}</span>` : ''}
    </div>
    <p class="st">${esc(m.statement || m.question)}</p>
    ${m.enquiry_text ? `<div class="enq"><b>What to ask, or where to look</b>${
      esc(m.enquiry_text)}</div>` : ''}
    ${m.last_action ? `<div class="decided"><b>${esc(m.last_action)}</b> by
      ${esc(m.last_actor)} · ${esc(String(m.last_action_at).slice(0, 16).replace('T', ' '))}
      ${m.last_note ? '<br>' + esc(m.last_note) : ''}</div>` : ''}
    ${m.closed_reason ? `<div class="decided">${esc(m.closed_reason)}</div>` : ''}
    ${closed ? '' : `<div class="acts">
      <input type="text" placeholder="A note, for the record — what was agreed, or why not"
        data-note="${esc(m.matter_key)}">
      <button class="yes" data-a="accept">Accept</button>
      <button class="no" data-a="reject">Reject</button>
      <button data-a="note">Note only</button>
    </div>`}
    <div class="meta">raised ${m.times_raised} time(s) · first seen ${
      esc(String(m.first_seen).slice(0, 10))} · ${m.decisions} decision(s) ·
      ${m.open_enquiries} enquiry(ies) outstanding
      <span class="mono"> · ${esc(m.matter_key.slice(0, 10))}</span></div>
  </div>`;
}

function enquiryRow(e) {
  return `<div class="enqrow" data-eid="${e.id}">
    <div><div class="ay">${esc(ay(e.ay) || '')}</div>
      <span class="tag ${e.severity === 'high' ? 'neg' : 'mute'}">${esc(e.status.replace('_', ' '))}</span>
      <div class="faint mono" style="font-size:10.5px;margin-top:4px">${esc(e.rule_id)}</div></div>
    <div><p class="m">${esc(e.question)}</p>
      <div class="acts" style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap">
        <input type="text" placeholder="What came back, or the document received"
          data-answer="${e.id}"
          style="flex:1;min-width:200px;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:5px 10px;font:inherit;font-size:11.5px;color:var(--ink)">
        ${e.status === 'to_ask'
          ? '<button data-eaction="asked" style="background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:4px 11px;font:inherit;font-size:11.5px;cursor:pointer">Mark as asked</button>'
          : ''}
        <button data-eaction="answered" style="background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:4px 11px;font:inherit;font-size:11.5px;cursor:pointer">Record the answer</button>
      </div>
      ${e.answer ? `<div class="a" style="margin-top:6px">${esc(e.answer)}</div>` : ''}
    </div>
  </div>`;
}

function wireRegisterActions() {
  $$('#tab-register .mrow .acts button').forEach(b => b.onclick = async () => {
    const row = b.closest('.mrow');
    const key = row.dataset.key;
    const who = actor();
    if (!who) return;
    const note = ($(`input[data-note="${key}"]`) || {}).value || '';
    b.disabled = true;
    const r = await fetch('/api/disposition', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ matter_key: key, action: b.dataset.a, actor: who, note }) })
      .then(x => x.json()).catch(() => ({ ok: false, reason: 'the service could not be reached' }));
    if (!r.ok) { alert('Not recorded: ' + (r.reason || 'unknown reason')); b.disabled = false; return; }
    await loadRegister();
  });

  $$('#tab-register .enqrow button').forEach(b => b.onclick = async () => {
    const row = b.closest('.enqrow');
    const id = row.dataset.eid;
    const who = actor();
    if (!who) return;
    const answer = ($(`input[data-answer="${id}"]`) || {}).value || '';
    b.disabled = true;
    const r = await fetch('/api/enquiry', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: Number(id), status: b.dataset.eaction,
                             actor: who, answer }) })
      .then(x => x.json()).catch(() => ({ ok: false, reason: 'the service could not be reached' }));
    if (!r.ok) { alert('Not recorded: ' + (r.reason || 'unknown reason')); b.disabled = false; return; }
    await loadRegister();
  });
}

/* ================================================== attendance (on load) == */
async function loadAttendance() {
  let d;
  try { d = await (await fetch('/api/attendance')).json(); } catch (e) { return; }
  if (d.error) {
    $('#tab-attendance').innerHTML = `<div class="empty">${esc(d.error)}</div>`;
    return;
  }
  S.attendance = d;
  const ent = $('#entity');
  if (ent) ent.innerHTML = `${esc(d.client.name)}<br>${esc(d.client.pan || 'PAN not read')}
    · ${esc(d.client.entity_type || 'constitution not declared')}`;
  renderAttendance(d);
}

function renderAttendance(d) {
  const a = d.attendance;
  const rows = a.rows || [];
  const chips = rows.map(r => {
    const cls = r.state === 'both' ? 'both' : r.state === 'neither' ? 'none' : 'half';
    return `<div class="yearchip ${cls}"><div class="y">${esc(ay(r.ay))}</div>
      <div class="s">${esc(r.fy)}</div>
      <div class="s">${r.itr ? esc(r.itr.form || 'return') : '<span class="negt">no return</span>'}</div>
      <div class="s">${r['3cd'] ? esc(r['3cd'].form || '3CD') : '<span class="negt">no report</span>'}</div>
      </div>`;
  }).join('');

  $('#tab-attendance').innerHTML = `
    ${d.client.declared ? '' : `<div class="note">${esc(d.client.notes[0] || '')}</div>`}
    ${(d.warnings || []).map(w => `<div class="note">${esc(w)}</div>`).join('')}
    <h2 class="h" style="margin-top:18px">What is on the record</h2>
    <p class="lede">${esc(a.statement)}. Folder <span class="mono">${esc(d.folder)}</span>.
      ${a.absence_safe
        ? 'Every file that could have been a filing was read, so this analysis may state that something was not filed.'
        : '<span class="warnt">A file that could have been a filing was not read, so no statement that something was never filed is made from this record.</span>'}</p>
    <div class="years">${chips}</div>

    <h2 class="h" style="margin-top:26px">Every file in the folder</h2>
    <div class="card scroll"><table><thead><tr><th>File</th><th>Read as</th>
      <th>Year</th><th>PAN</th><th class="right">Fields</th><th>Note</th>
      </tr></thead><tbody>
      ${(d.filings || []).map(f => `<tr class="${f.readable && f.kind !== 'unknown' ? '' : 'neg'}">
        <td>${esc(f.rel)}${f.superseded_by
          ? `<div class="faint" style="font-size:11px">superseded by ${esc(f.superseded_by)}</div>` : ''}</td>
        <td>${f.readable
          ? (f.kind === 'unknown' ? '<span class="negt">not recognised</span>'
            : esc(f.form || f.kind))
          : '<span class="negt">not read</span>'}</td>
        <td class="mono">${esc(ay(f.ay) || '—')}${f.folder_hint && f.ay && f.folder_hint !== f.ay
          ? `<div class="warnt" style="font-size:11px">folder says ${esc(ay(f.folder_hint))}</div>` : ''}</td>
        <td class="mono faint">${esc(f.pan || '—')}</td>
        <td class="right nums faint">${f.populated ? f.populated.toLocaleString() : '—'}</td>
        <td class="faint" style="font-size:11.5px">${esc(f.error || (f.notes || []).join('; ') || '')}</td>
        </tr>`).join('')}
    </tbody></table></div>

    ${(a.duplicates || []).length ? `<h2 class="h" style="margin-top:26px">More than one filing for a year</h2>
      <div class="card pad lede">${a.duplicates.map(x =>
        `${esc(x.ay)} ${esc(x.kind.toUpperCase())} — <span class="mono">${esc(x.file)}</span>
         superseded by <span class="mono">${esc(x.superseded_by)}</span>`).join('<br>')}
      <div style="margin-top:8px">The later filing is used. A revised return supersedes
      the original, and the reason for revision is itself worth reading.</div></div>` : ''}`;
}

/* ================================================================= tabs === */
$$('#tabs button').forEach(b => b.onclick = () => {
  $$('#tabs button').forEach(x => x.setAttribute('aria-selected', String(x === b)));
  $$('section.tab').forEach(s => s.classList.toggle('on', s.id === 'tab-' + b.dataset.t));
  const area = $('#scrollarea');
  if (area) area.scrollTop = 0;
});

function followContent() {
  const area = $('#scrollarea');
  if (!area || !S.running) return;
  area.scrollTop = area.scrollHeight;
}

/* ========================================================== stage rail === */
const STAGE_LABEL = { preflight: 'Preflight', reading: 'Reading', matrix: 'Matrix',
  trends: 'Trends', assessing: 'Applying rules', comparing: 'Comparing', report: 'Drafting' };
function stage(key, state) {
  const n = document.querySelector(`.stg[data-k="${key}"]`);
  if (n) n.dataset.s = state;
  if (state === 'active') progress(STAGE_LABEL[key] || key);
}
function stageDone(key) { stage(key, 'done'); }
function progress(text, index, of) {
  const label = $('#runlabel');
  if (!label || !S.running) return;
  label.textContent = (index && of) ? `${text} ${index}/${of}…` : `${text}…`;
}

/* ======================================================= block helpers === */
const stream = $('#stream');
function block(id, heading, lede) {
  let b = document.getElementById('b-' + id);
  if (b) return b;
  b = el('div', 'blk');
  b.id = 'b-' + id;
  if (heading) b.appendChild(el('h2', 'h', esc(heading)));
  if (lede) b.appendChild(el('p', 'lede', lede));
  b.appendChild(el('div', 'holder'));
  stream.appendChild(b);
  followContent();
  return b;
}
const holder = id => $('.holder', block(id));

/* ============================================================== charges == */
function renderCharges(run, session) {
  if (run) {
    $('#run-total').textContent = usd(run.total_usd);
    $('#run-ai').textContent = usd(run.ai_usd);
    $('#run-portal').textContent = usd(run.portal_usd);
  }
  if (session) {
    $('#ses-total').textContent = usd(session.total_usd);
    $('#ses-ai').textContent = usd(session.ai_usd);
    $('#ses-portal').textContent = usd(session.portal_usd);
    const n = session.runs || 0;
    $('#run-count').textContent = n ? `· ${n} run${n === 1 ? '' : 's'}` : '';
    const note = $('#costnote');
    if (note) {
      const free = session.cached_calls || 0;
      note.innerHTML = 'Portal charge matches the AI charge.'
        + (free ? ` ${free} model answer${free === 1 ? '' : 's'} served from cache at no cost.` : '');
    }
  }
}

/* ============================================================ renderers == */
function renderPreflight(d) {
  S.preflight = d;
  const ent = $('#entity');
  if (ent) ent.innerHTML = `${esc(d.name)}<br>${esc(d.pan || 'PAN not read')} ·
    ${esc(d.entity_type || 'constitution not declared')}`;
  const h = holder('pre');
  h.appendChild(el('div', 'card pad', `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px">
      <div><div class="h">Assessee</div><div>${esc(d.name)}</div>
        <div class="faint" style="font-size:11.5px">${esc(d.client_id)} ·
        ${d.declared ? 'declared in a client context' : 'taken from the filings'}</div></div>
      <div><div class="h">Law applied</div><div>${esc(d.pack)}</div>
        <div class="faint" style="font-size:11.5px">${d.provisions} provisions ·
          ${esc(d.pack_status)}</div></div>
      <div><div class="h">Doctrine</div><div>v${esc(d.doctrine)}</div>
        <div class="faint" style="font-size:11.5px">${d.doctrine_articles} standing rules</div></div>
      <div><div class="h">Folder</div><div class="mono" style="font-size:11.5px">${
        esc(String(d.folder).split('/').slice(-2).join('/'))}</div></div>
    </div>`));
  (d.warnings || []).forEach(w => h.appendChild(el('div', 'note', esc(w))));
}

function renderFileRow(e) {
  const h = holder('read');
  const bad = !e.readable || e.kind === 'unknown';
  progress('Reading', e.index, e.of);
  h.appendChild(el('div', 'frow' + (bad ? ' bad' : ''), `
    <span class="ic mono">${e.readable ? (e.kind === 'itr' ? 'ITR' : e.kind === '3cd' ? '3CD' : '···') : '···'}</span>
    <span class="nm">${esc(e.rel || e.file)}</span>
    <span class="meta">${esc(e.readable
      ? (e.kind === 'unknown' ? 'not a return or an audit report'
        : `${e.form || e.kind} · ${ay(e.ay) || 'year not stated'} · ${
          (e.populated || 0).toLocaleString()} fields`)
      : e.error)}</span>
    <span class="t">${ms(e.ms)}</span>`));
  const counter = $('.lede', block('read'));
  if (counter) counter.textContent = `Reading ${e.index} of ${e.of} files in the folder…`;
}

function renderAttendanceStage(d) {
  const counter = $('.lede', block('read'));
  if (counter) counter.innerHTML = esc(d.statement) + '.';
  const h = holder('read');
  const chips = (d.rows || []).map(r => {
    const cls = r.state === 'both' ? 'both' : r.state === 'neither' ? 'none' : 'half';
    return `<div class="yearchip ${cls}"><div class="y">${esc(ay(r.ay))}</div>
      <div class="s">${r.itr ? esc(r.itr.form || 'return') : '<span class="negt">no return</span>'}</div>
      <div class="s">${r['3cd'] ? esc(r['3cd'].form || '3CD') : '<span class="negt">no report</span>'}</div>
      </div>`;
  }).join('');
  h.appendChild(el('div', 'years', chips));
  if ((d.unreadable || []).length || (d.unrecognised || []).length) {
    h.appendChild(el('div', 'note', `<b class="negt">${
      d.unreadable.length + d.unrecognised.length} file(s) were not read as a filing.</b> `
      + [...d.unreadable, ...d.unrecognised].map(u =>
        `${esc(u.file)} — ${esc(u.reason)}`).join('; ')
      + (d.absence_safe ? '' : ' Because of this, nothing below states that a document '
        + 'was never filed.')));
  }
}

function renderMatrix(d) {
  S.matrix = d;
  const counter = $('.lede', block('matrix'));
  const cov = d.coverage;
  if (counter) counter.innerHTML =
    `${cov.cells_populated} figures located across ${d.years.length} year(s) from
     ${cov.facts_declared} facts in the dictionary${cov.found_by_search
      ? `, of which <span class="warnt">${cov.found_by_search} were found by searching for a
         field name</span> rather than at a known path` : ''}. ${cov.computed} are computed
     from the others. Nothing is filled forward: a gap is a gap.`;

  const groups = new Map((d.groups || []).map(g => [g.id, g.label]));
  const rows = (d.rows || []);
  let html = `<table><thead><tr><th class="fact">Fact</th>
    ${d.years.map(y => `<th class="right">${esc(ay(y))}</th>`).join('')}</tr></thead><tbody>`;
  let lastGroup = null;
  rows.forEach(r => {
    if (r.group !== lastGroup) {
      lastGroup = r.group;
      html += `<tr class="groupline"><td colspan="${d.years.length + 1}">${
        esc(groups.get(r.group) || r.group)}</td></tr>`;
    }
    html += `<tr><td class="fact">${esc(r.label)}
      <span class="src mono">${esc(r.id)}${r.clause ? ' · clause ' + esc(r.clause) : ''}${
        r.expr ? ' · ' + esc(r.expr) : ''}</span></td>`;
    d.years.forEach(y => {
      const v = r.values[y];
      const by = r.by[y];
      const ptr = r.pointers[y];
      if (v == null) { html += '<td class="v gap">—</td>'; return; }
      const shown = r.kind === 'money' ? money(v)
        : r.kind === 'ratio' ? (Number(v).toFixed(2) + '%')
        : r.kind === 'flag' ? (v === true ? 'yes' : v === false ? 'no' : esc(String(v)))
        : r.kind === 'count' ? Number(v).toLocaleString()
        : esc(Array.isArray(v) ? v.join(', ') : String(v));
      html += `<td class="v" title="${esc(ptr || '')}">${shown}${
        by === 'key search' ? '<span class="fl searched" title="found by searching for a field name, not at a known path"> ?</span>'
        : by === 'computed' ? '<span class="fl" title="computed from other facts"> ƒ</span>' : ''}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table>';

  $('#tab-matrix').innerHTML = `
    <h2 class="h" style="margin-top:18px">The fact matrix</h2>
    <p class="lede">One canonical fact, one assessment year, one located value. This is
    the only thing the rules see — they never read the JSON, which is what makes a
    finding re-derivable. Hover any figure for the exact file and JSON pointer it came
    from. <span class="warnt">?</span> marks a figure found by searching for a field
    name; ƒ marks one computed from others.</p>
    <div class="card matrix">${html}</div>`;
  holder('matrix').appendChild(el('p', 'lede',
    `The matrix is on the <b>Fact matrix</b> tab — ${cov.cells_populated} located
     figures, each with the file and JSON pointer it was read from.`));
}

function sparkline(values, klass) {
  const nums = Object.values(values || {});
  if (nums.length < 2) return '';
  const lo = Math.min(...nums), hi = Math.max(...nums);
  const span = (hi - lo) || 1;
  const w = 120, h = 24;
  const pts = nums.map((v, i) => {
    const x = nums.length === 1 ? 0 : (i / (nums.length - 1)) * (w - 4) + 2;
    const y = h - 3 - ((v - lo) / span) * (h - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = pts[pts.length - 1].split(',');
  return `<svg class="spark ${klass || ''}" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">
    <polyline points="${pts.join(' ')}"></polyline>
    <circle cx="${last[0]}" cy="${last[1]}" r="2.2"></circle></svg>`;
}

function renderTrends(d) {
  S.trends = d;
  const sum = d.summary;
  const series = d.series.slice().sort((a, b) => {
    const A = a.largest_step && a.largest_step.pct != null ? Math.abs(a.largest_step.pct) : 0;
    const B = b.largest_step && b.largest_step.pct != null ? Math.abs(b.largest_step.pct) : 0;
    return B - A;
  });
  const shape = k => sum.shapes[k] || 0;

  $('#tab-trends').innerHTML = `
    <h2 class="h" style="margin-top:18px">What the numbers did</h2>
    <p class="lede">Measurement, not judgment. A series that triples is a fact about the
    series; whether it matters is decided by a rule with a threshold behind it. The
    material threshold for this run is ${pct(sum.material_threshold, 0)} year on year.
    Every statistic is computed on the years that actually carry a value — a gap is
    skipped, never interpolated.</p>
    <div class="tiles" style="margin:0 0 20px">
      <div class="tile"><div class="n">${sum.series}</div><div class="l">Series measured</div></div>
      <div class="tile"><div class="n warnt">${shape('step change')}</div><div class="l">Step changes</div></div>
      <div class="tile"><div class="n">${shape('volatile')}</div><div class="l">Volatile</div></div>
      <div class="tile"><div class="n">${shape('rising')}</div><div class="l">Rising</div></div>
      <div class="tile"><div class="n">${shape('falling')}</div><div class="l">Falling</div></div>
      <div class="tile"><div class="n faint">${shape('broadly flat')}</div><div class="l">Broadly flat</div></div>
    </div>
    <div class="card">${series.map(t => {
      const step = t.largest_step;
      const p = step && step.pct != null ? step.pct : null;
      const klass = t.direction === 'step change' ? 'neg'
        : t.direction === 'volatile' ? 'warn' : '';
      const dirClass = t.direction === 'step change' ? 'step'
        : t.direction === 'rising' ? 'up' : t.direction === 'falling' ? 'down' : 'flat';
      return `<div class="trow" title="${esc(Object.entries(t.values)
        .map(([y, v]) => `${ay(y)} ${t.kind === 'ratio' ? Number(v).toFixed(2) + '%' : money(v)}`)
        .join('   '))}">
        <div class="lab">${esc(t.label)}
          <span class="g">${esc(t.fact)} · ${t.observations} year${t.observations === 1 ? '' : 's'}${
            (t.gaps || []).length ? ` · ${t.gaps.length} gap` : ''}</span></div>
        <div>${sparkline(t.values, klass)}</div>
        <div class="num">${t.kind === 'ratio' ? Number(t.latest).toFixed(2) + '%' : money(t.latest)}
          <span class="faint" style="display:block;font-size:10.5px">${esc(ay(t.latest_year))}</span></div>
        <div class="dir ${dirClass}">${esc(t.direction)}${p != null
          ? `<span class="faint" style="display:block;font-size:10.5px">${
            (p * 100).toFixed(0)}% in ${esc(ay(step.ay))}</span>` : ''}</div>
      </div>`;
    }).join('')}</div>`;

  const counter = $('.lede', block('trends'));
  if (counter) counter.innerHTML =
    `${sum.series} series measured across the years. <b>${shape('step change')}</b>
     move sharply enough in a single year to be called a step change; ${
     sum.material_movements.length} movements cross the material threshold of ${
     pct(sum.material_threshold, 0)}. The <b>Trends</b> tab has all of them.`;
  const h = holder('trends');
  const top = sum.material_movements.slice(0, 6);
  if (top.length) {
    const card = el('div', 'card');
    card.innerHTML = top.map(m => `<div class="trow">
      <div class="lab">${esc(m.label)}<span class="g">${esc(m.fact)}</span></div>
      <div class="muted">${esc(ay(m.previous_ay))} → ${esc(ay(m.ay))}</div>
      <div class="num">${m.kind === 'ratio' ? Number(m.value).toFixed(2) + '%' : money(m.value)}</div>
      <div class="dir ${m.pct > 0 ? 'up' : 'down'}">${(m.pct * 100).toFixed(0)}%</div>
      </div>`).join('');
    h.appendChild(card);
  }
}

const VERDICT_TAG = { FAIL: 'neg', INSUFFICIENT_EVIDENCE: 'warn', PASS: 'pos',
  NOT_APPLICABLE: 'mute' };

function findingCard(f) {
  return `<div class="fcard ${esc(f.severity)}">
    <div class="top">
      <span class="tag ${VERDICT_TAG[f.verdict] || 'mute'}">${
        f.verdict === 'FAIL' ? 'to address' : esc(f.verdict.replace(/_/g, ' ').toLowerCase())}</span>
      <span class="rid">${esc(String(f.instance_id).split('@')[0])}</span>
      ${f.ay ? `<span class="tag year">${esc(ay(f.ay))}</span>` : ''}
      <span class="q">${esc(f.question)}</span>
      ${f.amount ? `<span class="amt">${money(f.amount)}</span>` : ''}
    </div>
    <p class="st">${esc(f.statement)}</p>
    ${f.enquiry ? `<div class="enq"><b>What to ask, or where to look</b>${esc(f.enquiry)}</div>` : ''}
    ${f.vulnerability ? `<div class="vuln">${esc(f.vulnerability)}</div>` : ''}
    ${(f.evidence || []).length ? `<div class="ev">${f.evidence.map(esc).join('<br>')}</div>` : ''}
    ${(f.authority || []).length ? `<div style="margin-top:7px">${f.authority.map(a =>
      `<span class="tag mute">${esc(a)}</span>`).join(' ')}</div>` : ''}
  </div>`;
}

let counters = null;
function renderAssessTiles() {
  const c = { FAIL: 0, INSUFFICIENT_EVIDENCE: 0, PASS: 0, NOT_APPLICABLE: 0 };
  S.findings.forEach(f => { if (c[f.verdict] != null) c[f.verdict]++; });
  if (!counters) {
    const t = el('div', 'tiles');
    t.innerHTML = `
      <div class="tile neg"><div class="n negt" id="c-fail">0</div><div class="l">Matters to address</div></div>
      <div class="tile"><div class="n warnt" id="c-ins">0</div><div class="l">Could not determine</div></div>
      <div class="tile"><div class="n" id="c-pass">0</div><div class="l">Found in order</div></div>
      <div class="tile"><div class="n faint" id="c-na">0</div><div class="l">Not applicable</div></div>
      <div class="tile"><div class="n" id="c-exp">—</div><div class="l">Amounts flagged</div></div>`;
    holder('assess').appendChild(t);
    counters = true;
  }
  $('#c-fail').textContent = c.FAIL;
  $('#c-ins').textContent = c.INSUFFICIENT_EVIDENCE;
  $('#c-pass').textContent = c.PASS;
  $('#c-na').textContent = c.NOT_APPLICABLE;
  const exposure = S.findings.filter(f => f.verdict === 'FAIL')
    .reduce((a, f) => a + (f.amount || 0), 0);
  $('#c-exp').textContent = exposure ? money(exposure) : '—';
  const counter = $('.lede', block('assess'));
  if (counter) counter.textContent =
    `${S.findings.length} propositions tested across the years.`;
}

let topHolder = null;
function renderTopFinding(f) {
  if (f.verdict !== 'FAIL' || f.severity !== 'high') return;
  if (!topHolder) topHolder = holder('assess');
  const shown = $$('.fcard', topHolder).length;
  if (shown >= 6) return;
  topHolder.appendChild(el('div', '', findingCard(f)));
}

function renderFindings() {
  const order = { FAIL: 0, INSUFFICIENT_EVIDENCE: 1, NOT_APPLICABLE: 2, PASS: 3 };
  const sev = { high: 0, medium: 1, low: 2 };
  const rows = S.findings.slice().sort((a, b) =>
    order[a.verdict] - order[b.verdict] ||
    (sev[a.severity] ?? 3) - (sev[b.severity] ?? 3) ||
    (b.amount || 0) - (a.amount || 0) ||
    a.instance_id.localeCompare(b.instance_id));
  const counts = v => rows.filter(r => r.verdict === v).length;

  $('#tab-findings').innerHTML = `
    <div class="filters" style="margin-top:18px">
      <button data-f="FAIL" aria-pressed="true">To address ${counts('FAIL')}</button>
      <button data-f="INSUFFICIENT_EVIDENCE">Undetermined ${counts('INSUFFICIENT_EVIDENCE')}</button>
      <button data-f="PASS">In order ${counts('PASS')}</button>
      <button data-f="NOT_APPLICABLE">Not applicable ${counts('NOT_APPLICABLE')}</button>
      <button data-f="ALL">All ${rows.length}</button>
    </div>
    <div id="flist">${rows.map(f =>
      `<div data-v="${esc(f.verdict)}">${findingCard(f)}</div>`).join('')}</div>`;
  const apply = which => $$('#flist > div').forEach(n => {
    n.hidden = which !== 'ALL' && n.dataset.v !== which;
  });
  $$('#tab-findings .filters button').forEach(b => b.onclick = () => {
    $$('#tab-findings .filters button').forEach(x =>
      x.setAttribute('aria-pressed', String(x === b)));
    apply(b.dataset.f);
  });
  apply('FAIL');
}

function renderEnquiries(d) {
  const list = d.enquiries || [];
  $('#tab-enquiries').innerHTML = `
    <h2 class="h" style="margin-top:18px">What to ask, and what to call for</h2>
    <p class="lede">One line per matter, in severity order. This is the working paper
    the analysis exists to produce: an anomaly with no next step is a complaint, and a
    question with the document named against it is work that can be finished.</p>
    ${list.length ? `<div class="card">${list.slice().sort((a, b) =>
      ({ high: 0, medium: 1, low: 2 }[a.severity] ?? 3) -
      ({ high: 0, medium: 1, low: 2 }[b.severity] ?? 3) ||
      (b.amount || 0) - (a.amount || 0)).map(e => `<div class="enqrow">
        <div><div class="ay">${esc(ay(e.ay) || 'all years')}</div>
          <span class="tag ${e.severity === 'high' ? 'neg' : 'mute'}">${esc(e.severity)}</span>
          ${e.amount ? `<div class="faint nums" style="font-size:11px;margin-top:4px">${
            money(e.amount)}</div>` : ''}</div>
        <div><p class="m">${esc(e.matter)}</p><div class="a">${esc(e.enquiry)}</div>
          <div class="faint" style="font-size:11px;margin-top:4px">${esc(e.heading)}</div></div>
      </div>`).join('')}</div>`
      : '<div class="empty">Nothing arising needs an enquiry.</div>'}`;
}

function renderReport(d) {
  S.report = d;
  const h = holder('report');
  const card = el('div', 'report');
  let html = `<div class="rt">Analysis of Income-tax Returns and Tax Audit Reports</div>
    <div class="rs">${esc(d.client)}${d.pan ? ' · ' + esc(d.pan) : ''} ·
      ${esc(d.years.join(', '))}</div>
    <p class="scope">${esc(d.scope)}</p>
    <p class="scope">${esc(d.coverage)}</p>`;
  if (!d.paragraphs.length)
    html += `<p style="margin-top:20px">Nothing arising from our analysis requires to be
      reported.</p>`;
  d.paragraphs.forEach(p => {
    const cls = p.theme === 'coverage' ? 'warnp' : 'neg';
    html += `<div class="para ${cls}"><h4>${esc(p.heading)}</h4><p>${esc(p.text)}</p>`;
    if (p.years && p.years.length)
      html += `<div class="refs"><b>Years:</b> ${esc(p.years.join('; '))}</div>`;
    if (p.authority && p.authority.length)
      html += `<div class="refs"><b>Provisions:</b> ${esc(p.authority.join('; '))}</div>`;
    html += `</div>`;
  });
  if ((d.charts || []).length) {
    html += `<h3 class="sec">The factual picture</h3>
      <p class="scope">The charts below are drawn by program from the figures as
      filed. They are stated as fact and carry no inference: each point is a figure
      from a return or an audit report, and the note under each says what a reader
      should take from it.</p>`;
    d.charts.forEach(c => { html += chartCard(c, true); });
  }
  const reportable = (d.inference || []).filter(i => i.verdict === 'REPORTABLE');
  if (reportable.length) {
    html += `<h3 class="sec">What the years suggest when read together</h3>
      <p class="scope">The paragraphs above state what the filings say. The
      following are inferences drawn from those same figures — each names the years
      it rests on, what would confirm it and what would refute it.</p>`;
    reportable.forEach(i => {
      html += `<div class="para neg"><h4>${esc(i.title)}</h4>
        <p>${esc(i.observation || i.headline)}</p>
        ${(i.hypotheses || []).length ? `<div class="refs"><b>To settle it:</b> ${
          esc(i.hypotheses.map(h => h.document || h.confirms_if)
            .filter(Boolean).slice(0, 3).join('; '))}</div>` : ''}
        ${(i.charts || []).length ? `<div class="refs"><b>See:</b> ${
          esc(i.charts.join(', '))}</div>` : ''}</div>`;
    });
  }
  if ((d.vulnerabilities || []).length) {
    html += `<h3 class="sec">Where these matters expose the assessee</h3>
      <p class="scope">Internal assessment of consequence, for the firm rather than
      for the client.</p>
      <div class="card" style="box-shadow:none">${d.vulnerabilities.slice(0, 12)
        .map(v => `<div class="omitted"><b>${esc(v.ref)}</b>${
          v.ay ? ' · ' + esc(ay(v.ay)) : ''}${
          v.amount ? ' · ' + money(v.amount) : ''} — ${esc(v.text)}</div>`)
        .join('')}</div>`;
  }
  if (d.programme && (d.programme.areas || []).length) {
    const p = d.programme;
    html += `<h3 class="sec">Audit programme indicated for these years</h3>
      <p class="scope">${p.counts.areas} area(s) and ${p.counts.procedures}
      procedures, ${p.counts.high} of them high priority. The full programme, with
      what put each area on it, is on the Audit plan tab.</p>
      <div class="card" style="box-shadow:none">${p.areas.map(a =>
        `<div class="omitted"><b>${esc(a.label)}</b> — ${esc(a.priority)} priority,
         ${a.procedures.length} procedures${a.amount ? ', ' + money(a.amount)
           + ' in issue' : ''}</div>`).join('')}</div>`;
  }
  card.innerHTML = html;
  h.appendChild(card);
  const counter = $('.lede', block('report'));
  if (counter) counter.innerHTML =
    `${d.paragraphs.length} theme${d.paragraphs.length === 1 ? '' : 's'} drafted from
     ${d.paragraphs.reduce((a, p) => a + p.sources, 0)} findings${d.exposure
      ? `, covering ${money(d.exposure)} of amounts flagged` : ''}. This is the only
     output that leaves the firm.`;
  S.hasRun = true;
  seedSuggestions();
  renderFindings();
  renderEnquiries(d);
  renderScore(d);
  renderManifest(d);
  renderRules();
}

function renderScore(d) {
  const sc = d.score;
  $('#tab-score').innerHTML = !sc
    ? `<div class="empty">No planted-defect manifest for this client. The scorecard
       exists for the synthetic client the generator writes, where we know what is
       wrong with the filings and can measure whether the rules found it.</div>`
    : `<div class="tiles" style="margin-top:18px">
      <div class="tile"><div class="n">${sc.detected}/${sc.planted}</div>
        <div class="l">Planted defects found</div></div>
      <div class="tile"><div class="n ${sc.recall === 1 ? '' : 'negt'}">${
        Math.round(sc.recall * 100)}%</div><div class="l">Recall</div></div>
      <div class="tile"><div class="n">${sc.additional.length}</div>
        <div class="l">Raised, not planted</div></div></div>
      <h2 class="h" style="margin-top:26px">Found</h2>
      <div class="card scroll"><table><thead><tr><th>Defect</th><th>Year</th>
        <th>Raised as</th><th>Verdict</th><th>What was planted</th></tr></thead><tbody>
        ${sc.matched.map(m => `<tr><td class="mono">${esc(m.defect_id)}</td>
          <td class="mono">${esc(ay(m.ay))}</td><td class="mono">${esc(m.instance)}</td>
          <td><span class="tag ${m.verdict === 'FAIL' ? 'neg' : 'warn'}">${
            esc(m.verdict.replace(/_/g, ' '))}</span></td>
          <td class="muted">${esc(m.description)}</td></tr>`).join('')}
      </tbody></table></div>
      <h2 class="h" style="margin-top:26px">Missed</h2>
      <div class="card">${sc.missed.length ? `<div class="scroll"><table><tbody>${
        sc.missed.map(m => `<tr class="neg"><td class="mono">${esc(m.defect_id)}</td>
          <td class="mono">${esc(ay(m.ay))}</td><td>${esc(m.where)}</td>
          <td class="muted">${esc(m.description)}</td>
          <td class="mono faint">expected ${esc(m.expected_rule)}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">None.</div>'}</div>
      <h2 class="h" style="margin-top:26px">Raised but not planted</h2>
      <p class="lede">Conditions the rules found that the generator did not plant.
      Each needs judging on its merits — a real condition the manifest omitted, or a
      rule firing too widely.</p>
      <div class="card scroll"><table><tbody>${sc.additional.map(a =>
        `<tr><td class="mono">${esc(a.instance)}</td>
         <td class="mono">${esc(ay(a.ay))}</td>
         <td class="muted">${esc(a.statement)}</td></tr>`).join('')}</tbody></table></div>`;
}

function renderManifest(d) {
  const g = d.gate;
  $('#tab-manifest').innerHTML = `
    <div class="gate ${g.passed ? '' : 'bad'}" style="margin-top:18px">
      <b>${g.passed ? 'Released' : 'Held'}</b>
      <span>Release gate — ${esc(g.summary)}</span></div>
    <p class="lede">Pinned inputs for this run. The same filings, doctrine, authority
    pack, fact dictionary and rule catalogue must produce the same findings; this is
    the record that makes that checkable.</p>
    <div class="card pad"><pre>${esc(JSON.stringify(d.manifest, null, 2))}</pre></div>`;
}

/* ================================================================ the run = */
const queue = [];
const failures = [];
let draining = false;
let assessPending = 0;

function delayFor(ev, backlog) {
  const base = CADENCE[ev.stage] != null ? CADENCE[ev.stage] : CADENCE.other;
  if (backlog <= 1) return base;
  return Math.max(3, Math.min(base, REVEAL_BUDGET_MS / backlog));
}

function safeApply(ev) {
  try { apply(ev); } catch (err) {
    console.error('render failed for', ev.stage, err);
    failures.push(ev.stage + ': ' + err.message);
  }
}

function drain() {
  if (draining) return;
  draining = true;
  const step = () => {
    if (document.hidden) {            // nobody is watching; catch up at once
      while (queue.length) safeApply(queue.shift());
      draining = false;
      return;
    }
    const ev = queue.shift();
    if (!ev) { draining = false; return; }
    safeApply(ev);
    setTimeout(step, delayFor(ev, queue.length));
  };
  step();
}
document.addEventListener('visibilitychange', () => { if (queue.length) drain(); });

function apply(ev) {
  switch (ev.stage) {
    case 'start':
      { const idle = $('#idle'); if (idle) idle.remove(); }
      stage('preflight', 'active');
      break;
    case 'preflight':
      block('pre', 'Before opening the folder',
        'The assessee, the law in force, the thresholds and the rules are fixed before '
        + 'a single filing is read.');
      renderPreflight(ev.detail);
      stageDone('preflight');
      break;
    case 'rules':
      block('rules', 'The method being applied',
        `${ev.detail.declared} canonical rules — ${ev.detail.deterministic} decided by
         arithmetic, ${ev.detail.judgment} put to model judgment — reading
         ${ev.detail.facts_declared} declared facts.`);
      break;
    case 'reading':
      stage('reading', 'active');
      block('read', 'Reading the filings',
        `Opening ${ev.detail.files_found} file(s). Nothing is written to them.`);
      break;
    case 'read': renderFileRow(ev); break;
    case 'attendance':
      renderAttendanceStage(ev.detail);
      stageDone('reading');
      stage('matrix', 'active');
      block('matrix', 'The fact matrix', 'Locating every declared fact in every year.');
      break;
    case 'matrix':
      renderMatrix(ev.detail);
      stageDone('matrix');
      stage('trends', 'active');
      block('trends', 'What the numbers did', 'Measuring each series across the years.');
      break;
    case 'trends':
      renderTrends(ev.detail);
      stageDone('trends');
      stage('charts', 'active');
      block('charts', 'The factual picture',
        'Charts declared in the catalogue, drawn from the same cells the rules read.');
      break;
    case 'charts':
      renderCharts(ev.detail);
      stageDone('charts');
      break;
    case 'assessing':
      stage('assessing', 'active');
      block('assess', 'Applying the canonical rules',
        `${ev.detail.rules} rules across ${ev.detail.years} year(s).`);
      break;
    case 'assess':
      progress('Applying rules', ev.index, ev.of);
      S.findings.push(ev);
      renderTopFinding(ev);
      if (++assessPending >= 8 || ev.index === ev.of) { assessPending = 0; renderAssessTiles(); }
      break;
    case 'comparing':
      stageDone('assessing');
      stage('comparing', 'active');
      block('anom', 'Comparing the years against each other',
        ev.detail.ai_available
          ? `Putting all ${ev.detail.cells} located figures to an open comparison — no
             question was written for this pass, and anything it finds that holds is a
             candidate for a new canonical rule.`
          : 'Not performed: ' + esc(ev.detail.reason) + '.');
      break;
    case 'anomaly':
      S.anomalies.push(ev);
      S.findings.push(ev);
      holder('anom').appendChild(el('div', '', findingCard(ev)));
      break;
    case 'compared': {
      const counter = $('.lede', block('anom'));
      if (counter && ev.detail.ran)
        counter.innerHTML = S.anomalies.length
          ? `<span class="negt">${S.anomalies.length} matter(s)</span> found by open
             comparison of ${ev.detail.cells_compared} figures, with no question having
             been written for them.`
          : `Compared ${ev.detail.cells_compared} figures across the years. Nothing was
             found that the rules had not already covered.`;
      if ((ev.detail.suppressed || []).length)
        holder('anom').appendChild(el('p', 'lede',
          `${ev.detail.suppressed.length} candidate(s) discarded before reporting: ` +
          esc(ev.detail.suppressed.map(x => x.reason).join('; ')) + '.'));
      stageDone('comparing');
      break;
    }
    case 'inferring':
      stage('inferring', 'active');
      block('inf', 'Inference over the factual briefs',
        ev.detail.ai_available
          ? `${ev.detail.declared} declared questions, each put to the model over a
             brief this program wrote — the measured series, the findings already
             settled by arithmetic, and the declared background of those years from
             context pack v${esc(ev.detail.context_version)}. The model sees no raw
             JSON and cannot choose its own evidence.`
          : 'Not performed: ' + esc(ev.detail.reason) + '.');
      break;
    case 'inference': {
      progress('Inferring', ev.index, ev.of);
      INFERENCES.push(ev.detail);
      if (ev.detail.verdict === 'REPORTABLE' || !ev.detail.ran)
        holder('inf').insertAdjacentHTML('beforeend', inferenceCard(ev.detail));
      renderInference();
      break;
    }
    case 'inferred': {
      const d = ev.detail;
      const counter = $('.lede', block('inf'));
      if (counter) counter.innerHTML =
        `${d.run} question(s) put to the inference layer:
         <b class="negt">${d.reportable} to report</b>, ${d.explained} explained by
         what else was true in those years, ${d.nothing_arising} with nothing
         arising${d.not_assessed ? `, ${d.not_assessed} not assessed` : ''}. Each card
         can be opened to show the brief it was given.`;
      stageDone('inferring');
      stage('programme', 'active');
      block('plan', 'The audit programme',
        'Assembled from what the rules found, what the series did, and what the '
        + 'inference layer named.');
      break;
    }
    case 'programme':
      renderPlan(ev.detail);
      stageDone('programme');
      break;
    case 'recorded': {
      const d = ev.detail;
      const b = block('recorded', 'The register',
        d.recorded
          ? 'What this run changed about the matters already on record.'
          : 'This run was not recorded.');
      const r = d.register || {};
      const c = r.counts || {};
      $('.holder', b).appendChild(el('div', d.recorded ? 'card pad' : 'note',
        d.recorded
          ? `<b>${c.new || 0}</b> new matter(s) ·
             <b>${c.again || 0}</b> raised again ·
             <b>${c.reopened || 0}</b> reopened ·
             <b>${c.superseded || 0}</b> superseded by changed figures ·
             <b>${c.resolved || 0}</b> no longer arising.
             <div class="lede" style="margin:7px 0 0">Decisions already recorded
             against a matter still hold where the figures behind it have not
             moved. See the <b>Register</b> tab.</div>`
          : `<b class="negt">Not recorded.</b> ${esc(d.reason)}
             The analysis above stands; what is missing is the register of
             matters and decisions.`));
      loadRegister();
      break;
    }
    case 'report':
      stage('report', 'active');
      block('report', 'The report', 'Drafting…');
      setTimeout(() => { renderReport(ev.detail); stageDone('report'); }, 240);
      break;
    case 'done':
      setTimeout(() => {
        $('#run').classList.remove('busy');
        $('#run').disabled = false;
        $('#runlabel').textContent = 'Run again';
        const b = block('foot');
        $('.holder', b).appendChild(el('p', 'divider',
          `Analysis completed in ${ms(ev.ms)} · ${ev.detail.findings} propositions
           tested · ${ev.detail.defects} matters to address`));
        if (ev.detail.run_charge)
          renderCharges(ev.detail.run_charge, ev.detail.session_charge);
        if (failures.length)
          $('.holder', b).appendChild(el('div', 'note',
            `<b class="negt">${failures.length} part(s) of this page failed to render.</b> `
            + esc(failures.join('; ')) + '. The run itself completed; reload and run again.'));
        S.running = false;
      }, 320);
      break;
    case 'error': {
      $('#run').classList.remove('busy');
      $('#run').disabled = false;
      $('#runlabel').textContent = 'Run analysis';
      const reached = $$('.stg').filter(n => n.dataset.s)
        .map(n => STAGE_LABEL[n.dataset.k] || n.dataset.k);
      const note = el('div', 'note blk',
        `<b class="negt">The analysis stopped before finishing.</b> ${esc(ev.detail)}
         ${reached.length ? `It had completed: ${esc(reached.join(', '))}.`
           : 'It had not started.'} Nothing shown above is lost — but the report was
         not drafted.
         <div style="margin-top:9px"><button type="button" class="retry">Run again</button></div>`);
      stream.appendChild(note);
      const retry = $('.retry', note);
      if (retry) retry.onclick = () => $('#run').click();
      S.running = false;
      break;
    }
  }
}

$('#run').onclick = () => {
  if (S.running) return;
  S.running = true;
  Object.assign(S, { files: [], findings: [], anomalies: [], report: null });
  INFERENCES.length = 0;
  counters = null; topHolder = null; assessPending = 0;
  queue.length = 0; failures.length = 0;
  stream.innerHTML = '';
  $$('.stg').forEach(s => delete s.dataset.s);
  ['matrix', 'trends', 'charts', 'inference', 'plan', 'findings', 'enquiries',
   'score', 'manifest'].forEach(t =>
    $('#tab-' + t).innerHTML = '<div class="empty">Running…</div>');
  const btn = $('#run');
  btn.classList.add('busy'); btn.disabled = true;
  $('#runlabel').textContent = 'Reading…';
  refreshStatus();

  const src = new EventSource('/api/run');
  src.onmessage = m => { queue.push(JSON.parse(m.data)); drain(); };
  src.addEventListener('end', () => src.close());
  src.onerror = () => {
    src.close();
    if (!S.running) return;
    queue.push({ stage: 'error', detail: 'The connection to the analysis service '
      + 'closed — the service was restarted, or is no longer running.' });
    drain();
  };
};

/* ==================================================================== chat */
function renderBlocks(blocks) {
  const cite = t => esc(t).replace(/\[([\d,\s]+)\]/g,
    (_, n) => `<span class="cite">[${n.replace(/\s+/g, '')}]</span>`);
  return (blocks || []).map(b => b.type === 'list'
    ? `<ol>${(b.items || []).map(i => `<li>${cite(i)}</li>`).join('')}</ol>`
    : `<p class="aa">${cite(b.text || '')}</p>`).join('');
}

function renderReferences(refs) {
  if (!refs || !refs.length) return '';
  return `<div class="refs"><b>References</b><br>` + refs.map(r =>
    `<span class="rn">[${r.n}]</span> ${esc(r.fact)} — ${esc(r.locator)}`)
    .join('<br>') + `</div>`;
}

const SUGGESTIONS = [
  'Which years show a fall in gross profit, and what moved with it?',
  'What is unpaid under section 43B across the years, and when was it paid?',
  'Where do the return and the audit report disagree?',
  'What would you ask the client first, and why?',
];

function seedSuggestions() {
  const box = $('#suggest');
  if (!box || box.dataset.done) return;
  box.dataset.done = '1';
  SUGGESTIONS.forEach(text => {
    const b = el('button', '', esc(text));
    b.type = 'button';
    b.onclick = () => { const q = $('#q'); q.value = text; q.focus(); };
    box.appendChild(b);
  });
}

$('#q').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $('#chatform').dispatchEvent(new Event('submit', { cancelable: true }));
  }
});

$('#chatform').onsubmit = async e => {
  e.preventDefault();
  const input = $('#q');
  const question = input.value.trim().replace(/\s+/g, ' ');
  if (!question) return;
  const send = $('#asksend');
  send.disabled = true; input.disabled = true;

  block('qa', 'Questions asked of the filings',
    'Answers are drawn from the fact matrix and cite it. They record no conclusion — '
    + 'conclusions are recorded by you.');
  const holderEl = holder('qa');
  const item = el('div', 'qa pending');
  item.innerHTML = `<p class="qq">${esc(question)}</p>
    <p class="aa"><span class="dots">Reading the matrix</span></p>`;
  holderEl.appendChild(item);
  const area = $('#scrollarea');
  if (area) area.scrollTop = area.scrollHeight;

  let r;
  try {
    r = await (await fetch('/api/chat', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }) })).json();
  } catch (err) {
    r = { ok: false, reason: 'the analysis service could not be reached' };
  }

  item.classList.remove('pending');
  let html = `<p class="qq">${esc(question)}</p>`;
  if (!r.ok) {
    html += `<p class="aa"><span class="negt">Not answered.</span> ${esc(r.reason)}</p>`;
  } else {
    html += renderBlocks(r.blocks);
    if (!r.sufficient)
      html += `<div class="warnbox">The matrix does not fully settle this.
        ${r.follow_up ? 'What would: ' + esc(r.follow_up) : ''}</div>`;
    if (r.directive_text)
      html += `<div class="warnbox"><b class="negt">Note.</b> One of the filings contains
        text addressed to the reader or to an automated system. It has been recorded and
        disregarded.</div>`;
    html += renderReferences(r.references);
    html += `<div class="refs" style="border-top:0;padding-top:6px">Read ${
      r.cells_searched} located figures across ${(r.years || []).length} year(s)${
      r.cached ? ', answered from cache' : ''}.</div>`;
  }
  item.innerHTML = html;
  if (r.run_charge) renderCharges(r.run_charge, r.session_charge);
  if (area) area.scrollTop = area.scrollHeight;
  send.disabled = false; input.disabled = false;
  input.value = ''; input.focus();
};

/* ================================================================== boot = */
refreshStatus();
loadRules();
loadAttendance();
loadRegister();
