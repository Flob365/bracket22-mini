'use strict';

const $ = id => document.getElementById(id);
const fmt = {
  money: new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}),
  percent: new Intl.NumberFormat('fr-FR', {style: 'percent', maximumFractionDigits: 1}),
  signed: new Intl.NumberFormat('fr-FR', {style: 'percent', maximumFractionDigits: 1, signDisplay: 'exceptZero'}),
  number: new Intl.NumberFormat('fr-FR', {maximumFractionDigits: 2}),
  date: new Intl.DateTimeFormat('fr-FR', {day: 'numeric', month: 'long', year: 'numeric'}),
  stamp: new Intl.DateTimeFormat('fr-FR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}),
};
const money = v => v == null ? '—' : fmt.money.format(v);
const percent = v => v == null ? '—' : fmt.percent.format(v);
const signed = v => v == null ? '—' : fmt.signed.format(v);
const number = v => v == null ? '—' : fmt.number.format(v);
const price = v => v == null ? '—' : `${fmt.number.format(v)} USD`;
const tone = v => v == null || v === 0 ? '' : v > 0 ? 'pos' : 'neg';
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

const NAMES = {QQQ: 'Nasdaq 100', SPY: 'S&P 500', AAPL: 'Apple', MSFT: 'Microsoft', NVDA: 'Nvidia', AMZN: 'Amazon', META: 'Meta', GOOGL: 'Alphabet', AVGO: 'Broadcom', TSLA: 'Tesla', AMD: 'AMD', NFLX: 'Netflix', COST: 'Costco', PLTR: 'Palantir', LRCX: 'Lam Research', SPCX: 'SpaceX', WMT: 'Walmart', GLD: 'Or', TLT: 'Obligations 20 ans+', IWM: 'Russell 2000'};
const RISK = {APPROVED: 'Admissible', WATCH: 'À suivre', REJECTED: 'Rejeté'};
const REVIEW = {pending: 'À examiner', approve: 'Approuvé', reject: 'Refusé'};
const REASONS = {invalid_numbers: 'Valeurs invalides', position_limit: 'Limite par position', sector_limit: 'Limite sectorielle', crypto_limit: 'Limite crypto', daily_limit: 'Limite quotidienne atteinte', score_below_minimum: 'Score sous le minimum', confidence_below_minimum: 'Confiance sous le minimum', missing_data: 'Données manquantes', stale_data: 'Données périmées', position_already_open: 'Position déjà ouverte', insufficient_cash: 'Liquidités insuffisantes', red_team_veto: 'Veto de la Red Team', original_proposal_not_approved: 'Proposition initiale non admissible'};
const EVENTS = {SOURCE_BOUND: 'Source de données liée', AGENT_RUN: 'Avis d’agent', ANALYSIS: 'Analyse', HUMAN_REVIEW: 'Décision humaine', PAPER_BUY: 'Achat paper', PAPER_CLOSE: 'Clôture paper', RISK_RECHECK_APPROVED: 'Contrôle de risque validé', RISK_RECHECK_REJECTED: 'Contrôle de risque refusé', FORWARD_MEASURED: 'Mesure différée', DAILY_RUN: 'Traitement quotidien'};
const EVENT_TONE = {PAPER_BUY: 'pos', PAPER_CLOSE: 'warn', RISK_RECHECK_REJECTED: 'neg', HUMAN_REVIEW: 'you', DAILY_RUN: 'you'};
const ALERTS = {stop_reached: 'Stop atteint à la clôture', horizon_reached: 'Horizon atteint'};
const METRICS = {
  Steffi: [['Tendance', m => ({bullish: 'Haussière', bearish: 'Baissière', neutral: 'Neutre'}[m.trend])], ['RSI 14', m => number(m.rsi14)], ['SMA 50 / 200', m => `${number(m.sma50)} / ${number(m.sma200)}`], ['Momentum 20 s.', m => signed(m.momentum20)], ['Support / résistance', m => `${number(m.support)} / ${number(m.resistance)}`], ['ATR 14', m => number(m.atr14)]],
  Desmond: [['Volatilité annualisée', m => percent(m.volatility)], ['Sharpe / Sortino', m => `${number(m.sharpe)} / ${number(m.sortino)}`], ['Perte maximale', m => percent(m.max_drawdown)], ['Bêta / corrélation SPY', m => `${number(m.beta_spy)} / ${number(m.correlation_spy)}`], ['Observations', m => m.observations], ['Rendement moyen 20 s.', m => signed(m.forward_return_20d)], ['Part de résultats positifs', m => percent(m.positive_probability)]],
};
const reasons = list => list.map(r => REASONS[r] || r).join(' · ');

let token = '';
let data = null;
let selected = null;
let filter = 'all';
let toastTimer = 0;

async function api(path, body) {
  const headers = token ? {Authorization: `Bearer ${token}`} : {};
  const options = body === undefined ? {headers} : {method: 'POST', headers: {...headers, 'Content-Type': 'application/json'}, body: JSON.stringify(body)};
  const response = await fetch(path, options);
  if (response.status === 401) {
    $('auth').hidden = false;
    $('token').focus();
    throw new Error('Saisissez le jeton pour ouvrir le desk.');
  }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? reasons(result.detail.split(', ')) : JSON.stringify(result.detail));
  return result;
}

function message(text, error = false) {
  const box = $('message');
  box.textContent = text;
  // Re-entering the top layer keeps the toast above an open dossier.
  if (box.showPopover) { if (box.matches(':popover-open')) box.hidePopover(); box.showPopover(); }
  box.className = `show${error ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.className = ''; }, error ? 12000 : 5000);
}

async function action(button, fn) {
  button.disabled = true;
  button.classList.add('busy');
  try { await fn(); } catch (error) { message(error.message, true); } finally { button.disabled = false; button.classList.remove('busy'); }
}

// Irreversible journal entries need a second click within four seconds.
function confirmed(button) {
  if (button.dataset.armed) return true;
  const label = button.textContent;
  button.dataset.armed = '1';
  button.textContent = 'Confirmer ?';
  setTimeout(() => { delete button.dataset.armed; if (button.isConnected) button.textContent = label; }, 4000);
  return false;
}

function sparkline(values, width = 96, height = 28, area = false) {
  if (!values || values.length < 2) return '<span class="muted">—</span>';
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1, pad = 2;
  const points = values.map((v, i) => `${(i / (values.length - 1) * width).toFixed(1)},${(pad + (1 - (v - min) / span) * (height - 2 * pad)).toFixed(1)}`);
  const change = values[values.length - 1] / values[0] - 1;
  const fill = area ? `<polygon class="area" points="0,${height} ${points.join(' ')} ${width},${height}"/>` : '';
  return `<svg class="spark ${tone(change) || 'pos'}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Variation sur ${values.length} séances : ${signed(change)}">${fill}<polyline points="${points.join(' ')}"/></svg>`;
}

const gauge = (ratio, cls = '') => `<svg class="gauge ${cls}" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true"><rect class="track" width="100" height="6" rx="3"/><rect class="fill" width="${Math.max(0, Math.min(1, ratio)) * 100}" height="6" rx="3"/></svg>`;

function renderHeader(d) {
  const real = d.provider === 'yahoo-daily-v1';
  const llm = d.agent_mode === 'llm';
  const stale = d.reports.filter(r => r.risk.reasons.includes('stale_data') || r.risk.reasons.includes('missing_data')).map(r => r.symbol);
  $('source-chip').textContent = real ? 'Données réelles' : 'Démonstration';
  $('source-chip').className = `chip ${real ? 'live' : ''}`;
  $('source-title').textContent = real ? 'Données réelles · Yahoo Finance' : 'Environnement de démonstration';
  const parts = [real ? 'Clôtures journalières · FX réel' : 'Données synthétiques', llm ? 'Agents LLM activés' : 'Agents déterministes'];
  if (llm) parts.push(`${d.reports.filter(r => r.mode === 'llm' && !r.data_error).length}/${d.reports.length} derniers rapports LLM`);
  if (stale.length) parts.push(`Données à vérifier : ${stale.join(', ')}`);
  $('source-description').textContent = parts.join(' · ');
  document.querySelectorAll('[data-model]').forEach(node => {
    const model = d.agent_models?.[node.dataset.model];
    node.textContent = llm && model ? `Modèle : ${model[0]} · effort ${model[1]}` : 'Mode déterministe · aucun LLM appelé';
  });
}

function renderStats(d) {
  const p = d.portfolio;
  for (const id of ['equity', 'cash', 'regime']) $(id).classList.remove('skeleton');
  $('equity').textContent = money(p.equity_eur);
  $('cash').textContent = money(p.cash_eur);
  $('pnl').innerHTML = `<b class="${tone(p.pnl_eur)}">${signed(p.return)}</b> · ${money(p.pnl_eur)} depuis le lancement`;
  $('invested').textContent = `${percent(1 - p.cash_eur / p.equity_eur)} investi · ${p.positions.length} position(s)`;
  const regime = d.reports.find(r => r.symbol === 'SPY')?.agents.Steffi?.metrics.regime;
  $('regime').innerHTML = regime ? `<span class="dot ${regime === 'risk-on' ? 'pos' : 'neg'}"></span>${regime === 'risk-on' ? 'Risk-on' : 'Risk-off'}` : 'Indisponible';
  $('universe').textContent = d.assets.length;
  $('count').textContent = `${d.reports.length} analysé(s) · ${d.reports.filter(r => r.risk.status === 'APPROVED' && r.human_review === 'pending').length} à examiner`;
}

function renderRadar(d) {
  const pending = d.assets.filter(a => !d.reports.some(r => r.symbol === a.symbol));
  $('pending-assets').innerHTML = pending.map(a => `<span class="pending-item">${escapeHtml(NAMES[a.symbol] || a.symbol)} <small>${escapeHtml(a.symbol)}</small><button class="link" data-analyze="${escapeHtml(a.symbol)}">Analyser</button></span>`).join('');
  const sectors = Object.fromEntries(d.assets.map(a => [a.symbol, a.sector]));
  const rows = d.reports.filter(r => filter === 'all' || r.risk.status === filter);
  document.querySelectorAll('#filters button').forEach(b => {
    const n = b.dataset.filter === 'all' ? d.reports.length : d.reports.filter(r => r.risk.status === b.dataset.filter).length;
    b.setAttribute('aria-pressed', String(b.dataset.filter === filter));
    b.dataset.count = n;
  });
  $('reports').innerHTML = rows.length ? rows.map(r => {
    const h = r.agents.Houston;
    return `<tr data-open="${escapeHtml(r.symbol)}" tabindex="0" class="${r.symbol === selected ? 'selected' : ''}">
<td><b>${escapeHtml(r.symbol)}</b><small>${escapeHtml(NAMES[r.symbol] || sectors[r.symbol] || '')}</small></td>
<td class="hide-sm">${sparkline(r.sparkline)}</td>
<td><span class="score">${h.score.toFixed(1)}<small>/10</small></span>${gauge(h.score / 10, h.score >= 7 ? 'pos' : h.score >= 4 ? 'warn' : 'neg')}</td>
<td>${percent(h.confidence)}</td>
<td><span class="pill ${r.risk.status}">${RISK[r.risk.status]}</span></td>
<td><span class="review ${r.human_review}">${REVIEW[r.human_review]}</span></td></tr>`;
  }).join('') : `<tr><td colspan="6" class="empty">${d.reports.length ? 'Aucun actif dans ce filtre.' : 'Lancez une analyse pour découvrir le radar.'}</td></tr>`;
}

function renderPortfolio(d) {
  const p = d.portfolio;
  const summary = `<div class="pnl-row"><span>Gains réalisés <b class="${tone(p.realized_pnl_eur)}">${money(p.realized_pnl_eur)}</b></span><span>Gains latents <b class="${tone(p.unrealized_pnl_eur)}">${money(p.unrealized_pnl_eur)}</b></span>${p.valuation_estimated ? '<span class="pill WATCH">Valorisation indicative : données à vérifier</span>' : ''}</div>`;
  $('positions').className = '';
  if (!p.positions.length) {
    $('positions').innerHTML = `${summary}<p class="empty">Aucune position ouverte. Les analyses ne passent aucun ordre.</p>`;
    return;
  }
  const status = {fresh: 'À jour', stale: 'Périmé', missing: 'Indisponible'};
  $('positions').innerHTML = `${summary}<div class="table-wrap"><table><thead><tr><th>Actif</th><th>Entrée → cours</th><th>Valeur</th><th>Gain latent</th><th>Suivi</th><th></th></tr></thead><tbody>${p.positions.map(x => `<tr>
<td><b>${escapeHtml(x.symbol)}</b><small>Entrée le ${escapeHtml(x.day)}</small></td>
<td>${price(x.entry_price)} → ${price(x.current_price)}<small>Cours du ${escapeHtml(x.mark_day)} · ${status[x.data_status]}</small></td>
<td>${money(x.value_eur)}<small>${percent(x.weight)} du portefeuille</small></td>
<td><b class="${tone(x.pnl_eur)}">${signed(x.return)}</b><small>${money(x.pnl_eur)}</small></td>
<td><span class="progress">${x.holding_sessions ?? '—'} / ${x.horizon_sessions} séances${gauge((x.holding_sessions || 0) / x.horizon_sessions)}</span><small>Stop ${price(x.stop_price)} · ${x.holding_days} j</small>${x.exit_alerts.map(a => `<span class="pill WATCH">${escapeHtml(ALERTS[a] || a)}</span>`).join(' ')}</td>
<td><button class="secondary" data-close="${escapeHtml(x.symbol)}" ${x.data_status !== 'fresh' ? 'disabled' : ''}>Clôturer</button></td></tr>`).join('')}</tbody></table></div><p class="caption foot">Entrée et stop ajustés des divisions d’actions. Alertes calculées sur la dernière clôture, sans surveillance intrajournalière. Chaque sortie reste manuelle.</p>`;
}

function renderOperations(d) {
  const run = d.operations.last_run, success = d.operations.last_success;
  const badge = $('operations-status');
  if (!run) {
    badge.textContent = 'Jamais lancé';
    badge.className = 'pill';
    $('operations').textContent = 'Aucun traitement quotidien enregistré.';
    return;
  }
  badge.textContent = run.errors.length ? `${run.errors.length} erreur(s)` : 'Succès';
  badge.className = `pill ${run.errors.length ? 'REJECTED' : 'APPROVED'}`;
  $('operations').textContent = `Dernier traitement le ${fmt.stamp.format(new Date(run.completed_at))} · ${run.measurements_added} nouvelle(s) mesure(s). Dernier succès : ${success ? fmt.stamp.format(new Date(success.completed_at)) : 'aucun'}.`;
}

function renderAgentStats() {
  if (!data) return;
  const horizon = $('horizon').value;
  const table = (title, groups) => Object.keys(groups).length ? `<table class="mini"><thead><tr><th>${title}</th><th>Obs.</th><th>Réussite</th><th>Rendement</th></tr></thead><tbody>${Object.entries(groups).map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${v.count}</td><td>${percent(v.win_rate)}</td><td class="${tone(v.mean_return)}">${signed(v.mean_return)}</td></tr>`).join('')}</tbody></table>` : '';
  $('agent-stats').innerHTML = Object.entries(data.agents).map(([name, value]) => {
    const h = value.horizons[horizon];
    const details = table('Confiance', h.calibration) + table('Régime', h.by_regime);
    return `<article class="agent"><header><h3>${escapeHtml(name)}</h3><small>${value.signals} signal(aux)</small></header>
<strong class="${tone(h.mean_alpha)}">${signed(h.mean_alpha)}</strong><p>Alpha moyen vs SPY · J+${horizon}</p>
<dl><div><dt>Observations</dt><dd>${h.count}</dd></div><div><dt>Réussite</dt><dd>${percent(h.win_rate)}</dd></div><div><dt>Rendement</dt><dd class="${tone(h.mean_return)}">${signed(h.mean_return)}</dd></div></dl>
${details ? `<details><summary>Calibration et régimes</summary>${details}</details>` : '<p class="muted">En attente de mesures à maturité.</p>'}</article>`;
  }).join('');
}

function renderJournal(d) {
  const j = d.journal;
  $('integrity').textContent = j.valid ? `Intégrité vérifiée · ${j.count} événements` : `Échec d’intégrité à l’événement ${j.broken_at}`;
  $('integrity').className = `pill ${j.valid ? 'APPROVED' : 'REJECTED'}`;
  $('events').innerHTML = j.events.map(e => {
    const label = /^[0-9a-f]{8}-/.test(e.label) ? `Rapport ${e.label.slice(0, 8)}` : e.label;
    return `<div class="event ${EVENT_TONE[e.kind] || ''}"><span class="dot"></span><b>${escapeHtml(EVENTS[e.kind] || e.kind)}</b><span>${escapeHtml(label)}</span><time datetime="${escapeHtml(e.at)}">${escapeHtml(fmt.stamp.format(new Date(e.at)))}</time></div>`;
  }).join('') || '<p class="empty">Le journal est vide.</p>';
}

function agentCard(name, a, report) {
  const metrics = a.metrics && METRICS[name] ? `<dl class="metrics">${METRICS[name].map(([label, get]) => `<div><dt>${label}</dt><dd>${escapeHtml(get(a.metrics))}</dd></div>`).join('')}</dl>` : '';
  const scenarios = name === 'Houston' && a.scenarios ? `<dl class="metrics scenarios"><div><dt>Scénario bas</dt><dd class="neg">${signed(a.scenarios.bear)}</dd></div><div><dt>Central</dt><dd>${signed(a.scenarios.base)}</dd></div><div><dt>Haut</dt><dd class="pos">${signed(a.scenarios.bull)}</dd></div></dl>` : '';
  const verdict = a.recommendation ? `<span class="pill ${{candidate: 'APPROVED', continue: 'APPROVED', watch: 'WATCH', reject: 'REJECTED'}[a.recommendation] || ''}">${{candidate: 'Idée à examiner', continue: 'Sans veto', watch: 'Surveillance', reject: 'Veto'}[a.recommendation] || escapeHtml(a.recommendation)}</span>` : '';
  return `<article class="agent-card"><header><h3>${escapeHtml(name)}</h3>${a.score != null ? `<span class="score">${number(a.score)}<small>/10</small></span>` : ''}${verdict}</header>
<p class="mode">${escapeHtml([a.model || a.mode || report.mode, a.reasoning_effort, a.confidence != null ? `confiance ${percent(a.confidence)}` : ''].filter(Boolean).join(' · '))}</p>
${a.thesis ? `<p>${escapeHtml(a.thesis)}</p>` : ''}${a.invalidation ? `<p><b>Invalidation :</b> ${escapeHtml(a.invalidation)}</p>` : ''}
${a.arguments?.length ? `<ul>${a.arguments.map(v => `<li>${escapeHtml(v)}</li>`).join('')}</ul>` : ''}${metrics}${scenarios}
<details><summary>Sortie brute</summary><pre>${escapeHtml(JSON.stringify(a, null, 2))}</pre></details></article>`;
}

function renderDetail() {
  const r = data?.reports.find(x => x.symbol === selected);
  if (!r) return;
  const pending = r.human_review === 'pending';
  $('detail').innerHTML = `<header class="drawer-head"><div><p class="eyebrow">Le dossier · ${escapeHtml(r.day)}</p><h2 id="detail-title">${escapeHtml(r.symbol)} <small>${escapeHtml(NAMES[r.symbol] || '')}</small></h2></div><button class="ghost" data-dismiss aria-label="Fermer le dossier">✕</button></header>
<div class="drawer-body">
<div class="chart">${sparkline(r.sparkline, 600, 120, true)}</div>
<dl class="key-figures"><div><dt>Prix de référence</dt><dd>${price(r.reference_price)}</dd></div><div><dt>Stop théorique</dt><dd>${price(r.stop_theoretical)}</dd></div><div><dt>Horizon</dt><dd>${r.horizon_days} séances</dd></div><div><dt>Taille proposée</dt><dd>${percent(r.proposed_weight)}</dd></div></dl>
<p class="status-line"><span class="pill ${r.risk.status}">${RISK[r.risk.status]}</span> ${escapeHtml(r.data_error || reasons(r.risk.reasons) || 'Tous les contrôles initiaux sont passés.')}</p>
<div class="agent-cards">${['Steffi', 'Desmond', 'Red Team', 'Houston'].filter(name => r.agents[name]).map(name => agentCard(name, r.agents[name], r)).join('')}</div>
<p class="caption">Source : ${escapeHtml(r.provenance?.source || r.provider)} · cours du ${escapeHtml(r.reference_day || r.day)} · FX daté du ${escapeHtml(r.provenance?.fx_date || r.day)} · ${escapeHtml(r.mode)}</p>
</div>
<footer class="drawer-foot"><button class="secondary" data-analyze="${escapeHtml(r.symbol)}">Réanalyser</button><span class="spacer"></span>${pending ? `<button class="danger" data-review="reject" data-id="${escapeHtml(r.id)}">Refuser</button>${r.risk.status === 'APPROVED' ? `<button class="primary" data-review="approve" data-id="${escapeHtml(r.id)}">Approuver l’achat paper (5 %)</button>` : ''}` : `<span class="review ${r.human_review}">Décision humaine : ${REVIEW[r.human_review]}</span>`}</footer>`;
}

function showDetail(symbol) {
  selected = symbol;
  renderDetail();
  if (!$('detail').open) $('detail').showModal();
  $('detail').scrollTop = 0;
  renderRadar(data);
}

function render(d) {
  data = d;
  renderHeader(d);
  renderStats(d);
  renderRadar(d);
  renderPortfolio(d);
  renderOperations(d);
  renderAgentStats();
  renderJournal(d);
  if ($('detail').open) renderDetail();
}

async function load() {
  render(await api('/dashboard'));
}

$('date').textContent = fmt.date.format(new Date());
$('run').addEventListener('click', () => action($('run'), async () => {
  message(`Analyse des ${data?.assets.length ?? 20} actifs en cours…`);
  const result = await api('/analysis/run-daily', {});
  await load();
  message(result.errors.length ? `${result.errors.length} erreur(s) : ${result.errors.map(e => `${e.symbol} — ${e.error}`).join(' ; ')}` : 'Analyse terminée. Le portefeuille attend votre décision.', !!result.errors.length);
}));
$('refresh').addEventListener('click', () => action($('refresh'), async () => {
  const r = await api('/performance/refresh', {});
  await load();
  message(`${r.added} nouvelle(s) mesure(s) arrivée(s) à maturité.${r.errors.length ? ` Erreurs : ${r.errors.map(e => `${e.symbol} — ${e.error}`).join(' ; ')}` : ''}`, !!r.errors.length);
}));
$('horizon').addEventListener('change', renderAgentStats);
$('auth').addEventListener('submit', event => {
  event.preventDefault();
  token = $('token').value;
  $('token').value = '';
  action(event.submitter, async () => { await load(); $('auth').hidden = true; message('Desk ouvert.'); });
});
$('filters').addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button || !data) return;
  filter = button.dataset.filter;
  renderRadar(data);
});
$('detail').addEventListener('click', event => { if (event.target === $('detail')) $('detail').close(); });
$('detail').addEventListener('close', () => { selected = null; if (data) renderRadar(data); });

document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (button?.dataset.dismiss !== undefined) return $('detail').close();
  if (button?.dataset.analyze) return action(button, async () => {
    message('Analyse en cours…');
    const r = await api(`/analysis/${button.dataset.analyze}`, {});
    await load();
    message(r.data_error || 'Analyse terminée.', !!r.data_error);
  });
  if (button?.dataset.review) return confirmed(button) && action(button, async () => {
    await api(`/proposals/${button.dataset.id}/review`, {action: button.dataset.review});
    await load();
    message(button.dataset.review === 'approve' ? 'Achat paper enregistré dans le journal.' : 'Refus enregistré dans le journal.');
  });
  if (button?.dataset.close) return confirmed(button) && action(button, async () => {
    await api(`/portfolio/${button.dataset.close}/close`, {});
    await load();
    message('Position paper clôturée.');
  });
  const row = event.target.closest('tr[data-open]');
  if (row && data) showDetail(row.dataset.open);
});
document.addEventListener('keydown', event => {
  const row = event.target.closest?.('tr[data-open]');
  if (row && data && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); showDetail(row.dataset.open); }
});

// Theme: follows the system unless the visitor chose otherwise.
const stored = (() => { try { return localStorage.getItem('b22-theme'); } catch { return null; } })();
if (stored) document.documentElement.dataset.theme = stored;
$('theme').addEventListener('click', () => {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'light' : 'dark';
  try { localStorage.setItem('b22-theme', document.documentElement.dataset.theme); } catch { /* private mode */ }
});

// Highlight the section in view in the sidebar.
const links = [...document.querySelectorAll('nav a')].map(a => [a, $(a.hash.slice(1))]).filter(([, node]) => node);
let ticking = false;
function spy() {
  ticking = false;
  const line = innerHeight * 0.3;
  let current = links[0];
  for (const link of links) if (link[1].getBoundingClientRect().top <= line) current = link;
  for (const link of links) link[0].toggleAttribute('aria-current', link === current);
}
addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(spy); } }, {passive: true});
spy();

load().catch(error => message(error.message, true));
