/* Artist Finder dashboard: local static rendering with no external dependencies. */
(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const stageOrder = ['Discovered', 'Major-platform confirmed', 'Diaspora validated', 'Forecast ready'];
  const state = { data: null, rows: [], sort: 'stage', direction: 1 };

  const escapeHtml = (value) => String(value ?? 'Unknown').replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;'
  })[char]);
  const label = (value) => String(value ?? 'Unknown').replaceAll('_', ' ');
  const titleCase = (value) => label(value).replace(/\b\w/g, char => char.toUpperCase());
  const fmt = (value) => typeof value === 'number' ? new Intl.NumberFormat().format(value) : 'Unknown';
  const compactDate = (value) => value ? String(value).slice(0, 10) : 'Unknown';
  const badge = (text, tone = '') => `<span class="badge ${tone}">${escapeHtml(text)}</span>`;
  const hasValue = value => typeof value === 'number' && Number.isFinite(value);
  const geoEvidence = (metric, labelText, searchUsable) => {
    if (!hasValue(metric?.avg_monthly)) return `No usable ${labelText} search evidence`;
    const status = searchUsable === true ? 'validated' : 'unvalidated';
    const note = metric.note ? ` — ${metric.note}` : '';
    return `${fmt(metric.avg_monthly)} average monthly searches (${status})${note}`;
  };

  function scoreMarkup(metric, kind) {
    if (!metric || !metric.observable || !hasValue(metric.value)) {
      return `<span class="score-value unknown">Unknown</span><span class="submetric">${escapeHtml(metric?.note || 'Not observable')}</span>`;
    }
    const suffix = kind === 'export_signal' ? ' / 100' : ' / 100';
    return `<span class="score-value ${kind === 'export_signal' ? 'separate' : ''}">${fmt(metric.value)}${suffix}</span><span class="submetric">${escapeHtml(metric.note || 'Observable')}</span>`;
  }

  function stateTone(value) {
    if (['clear', 'recent', 'retained_data', 'recorded', 'Success'].includes(value)) return 'good';
    if (['review_required', 'error', 'stale', 'disabled', 'Failed'].includes(value)) return 'alert';
    if (['unknown', 'not_recorded', 'requires_route', 'postponed', 'UNCLASSIFIED'].includes(value)) return 'warn';
    return 'info';
  }

  function values(key) {
    const out = new Set();
    state.data.artists.forEach(row => {
      const value = row[key];
      (Array.isArray(value) ? value : [value]).filter(Boolean).forEach(item => out.add(String(item)));
    });
    return [...out].sort((a, b) => a.localeCompare(b));
  }

  function populate(id, list) {
    const select = $(id);
    list.forEach(value => select.insertAdjacentHTML('beforeend', `<option value="${escapeHtml(value)}">${escapeHtml(titleCase(value))}</option>`));
  }

  function renderOverview() {
    const d = state.data;
    $('#title').textContent = d.title;
    $('#mode-pill').textContent = d.mode === 'fixture' ? 'Fixture / demo only' : 'Live local data';
    $('#mode-pill').className = `pill ${d.mode === 'fixture' ? 'fixture' : 'live'}`;
    $('#as-of').textContent = `As of ${compactDate(d.as_of)} · generated locally`;
    $('#caveat').textContent = d.caveat;
    const o = d.observability || {};
    const observability = $('#observability');
    observability.classList.toggle('good', Boolean(o.trajectory_observable));
    observability.innerHTML = `<div class="notice-mark" aria-hidden="true">${o.trajectory_observable ? '✓' : '!'}</div><div><h2>${o.trajectory_observable ? 'Trajectory is observable' : 'Trajectory is not observable yet'}</h2><p>${escapeHtml(o.trajectory_observable ? `Retained history: ${o.n_snapshots} crawls spanning ${o.span_days} days. Momentum remains a proxy, not a ticket-sales measure.` : `${o.why || 'Need retained crawl history.'} Momentum is intentionally shown as Unknown, not zero.`)}</p></div>`;
    $('#eligible-count').textContent = fmt(d.artists.length);
    $('#eligible-note').textContent = `${fmt(d.lifecycle?.counts?.Discovered || 0)} awaiting primary evidence`;
    const freshness = d.freshness || {};
    $('#freshness-count').textContent = freshness.snapshot_count ? `${freshness.snapshot_count} crawl${freshness.snapshot_count === 1 ? '' : 's'}` : 'No data';
    $('#freshness-note').textContent = freshness.last_snapshot ? `${freshness.age_days ?? 'Unknown'}d since retained snapshot (${compactDate(freshness.last_snapshot)})` : 'Run a safe fetch to create a baseline.';
    const issues = (d.source_health || []).filter(row => !['retained_data'].includes(row.state)).length;
    $('#source-count').textContent = `${fmt((d.source_health || []).length)} sources`;
    $('#source-note').textContent = issues ? `${issues} need a route, probe, or review` : 'All recorded sources have retained data';
    const attention = (d.action_queue || []).filter(row => row.review_state === 'review_required').length;
    $('#attention-count').textContent = fmt(attention || (d.action_queue || []).length);
    $('#attention-note').textContent = attention ? 'Rows need parsing/correction review' : 'Next evidence actions are queued';
  }

  function renderFunnel() {
    const count = state.data.lifecycle?.counts || {};
    $('#funnel').innerHTML = stageOrder.map((stage, index) => `<div class="funnel-step"><span>${index + 1}. ${escapeHtml(stage)}</span><strong>${fmt(count[stage] || 0)}</strong><span>${[ 'Credible discovery', 'Primary booking traceable', 'US/CA or qualifying foreign evidence', 'Dossier handoff is safe' ][index]}</span></div>`).join('');
  }

  function renderQueue() {
    const queue = state.data.action_queue || [];
    $('#action-queue').innerHTML = queue.length ? queue.slice(0, 10).map(row => `<button class="action-item" data-open="${escapeHtml(row.slug)}"><strong>${escapeHtml(row.name)}</strong><span>${escapeHtml(row.next_action)}</span>${badge(row.stage, row.review_state === 'review_required' ? 'alert' : 'info')}</button>`).join('') : `<div class="empty">No eligible records yet. Run the safe weekly discovery path, then return here to review retained evidence.</div>`;
  }

  function renderQuality() {
    const q = state.data.quality || {};
    const unresolved = q.unresolved_identity_evidence || [];
    const unresolvedNote = unresolved.length ? `Held out / review required: ${unresolved.slice(0, 3).map(item => `${item.name || 'Unnamed'} · ${item.source || 'Unknown source'}`).join('; ')}${unresolved.length > 3 ? ` +${unresolved.length - 3} more` : ''}.` : 'No unresolved-identity evidence is recorded.';
    const rows = [
      ['Records requiring parse review', q.review_rows, 'Review the underlying row; do not silently score it as zero.'],
      ['Unresolved identity rows (held out)', q.unresolved_identity_rows, unresolvedNote],
      ['Unknown genre', q.unknown_genre, 'Unknown genre is held out of the main signing ranking.'],
      ['Unclassified room-band evidence', q.unknown_venue_band, 'Unknown venues remain unknown; room capacity is not inferred.'],
      ['Traceable correction overlays', (q.corrections_event || 0) + (q.corrections_entity || 0), 'Corrections sit beside immutable snapshots.']
    ];
    $('#quality').innerHTML = rows.map(row => `<div class="quality-row"><span>${escapeHtml(row[0])}<br><small>${escapeHtml(row[2])}</small></span><strong>${fmt(row[1] || 0)}</strong></div>`).join('');
  }

  function rowMatches(row) {
    const search = $('#search').value.trim().toLowerCase();
    const filter = id => $(id).value;
    const includes = (items, value) => !value || (items || []).map(String).includes(value);
    const haystack = [row.name, row.slug, row.genre, row.kind, ...(row.sources || []), ...(row.cities || [])].join(' ').toLowerCase();
    if (search && !haystack.includes(search)) return false;
    if (filter('#stage-filter') && row.stage !== filter('#stage-filter')) return false;
    if (filter('#genre-filter') && row.genre !== filter('#genre-filter')) return false;
    if (filter('#kind-filter') && row.kind !== filter('#kind-filter')) return false;
    if (!includes(row.cities, filter('#city-filter'))) return false;
    if (!includes(row.sources, filter('#source-filter'))) return false;
    if (!includes(row.venue_bands, filter('#venue-filter'))) return false;
    if (filter('#primary-filter') && String(row.primary_confirmed ? 'yes' : 'no') !== filter('#primary-filter')) return false;
    if (filter('#review-filter') && row.review_state !== filter('#review-filter')) return false;
    if (filter('#freshness-filter') && row.freshness_state !== filter('#freshness-filter')) return false;
    return true;
  }

  function sortableValue(row, field) {
    if (field === 'stage') return stageOrder.indexOf(row.stage);
    if (['stature', 'momentum', 'export_signal'].includes(field)) return row.scores?.[field]?.value ?? -1;
    return String(row[field] ?? '').toLowerCase();
  }

  function renderArtists() {
    const rows = state.data.artists.filter(rowMatches).sort((a, b) => {
      const av = sortableValue(a, state.sort), bv = sortableValue(b, state.sort);
      const compare = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv));
      return compare * state.direction || a.name.localeCompare(b.name);
    });
    $('#result-count').textContent = `${fmt(rows.length)} of ${fmt(state.data.artists.length)} records`;
    $('#artist-body').innerHTML = rows.length ? rows.map(row => `<tr role="button" aria-label="Open details for ${escapeHtml(row.name)}" tabindex="0" data-open="${escapeHtml(row.slug)}"><td><span class="artist-name"><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(titleCase(row.kind))} · ${escapeHtml((row.cities || []).join(', ') || 'Unknown city')}</small></span></td><td>${badge(row.stage, row.primary_confirmed ? 'good' : 'info')}</td><td>${escapeHtml(titleCase(row.genre))}</td><td>${scoreMarkup(row.scores?.stature, 'stature')}</td><td>${scoreMarkup(row.scores?.momentum, 'momentum')}</td><td>${scoreMarkup(row.scores?.export_signal, 'export_signal')}</td><td>${badge(row.primary_confirmed ? 'Primary confirmed' : 'Discovery only', row.primary_confirmed ? 'good' : 'warn')}<br><span class="submetric">${escapeHtml((row.sources || []).join(', ') || 'Unknown')}</span></td><td>${badge(titleCase(row.review_state), stateTone(row.review_state))}<br><span class="submetric">${escapeHtml(row.freshness_state === 'stale' ? `Stale: ${row.freshness_days}d` : row.freshness_state)}</span></td></tr>`).join('') : `<tr><td colspan="8"><div class="empty">No records match these filters. Clear a filter or run the retained discovery workflow.</div></td></tr>`;
    $$('.sort-button').forEach(button => {
      const active = button.dataset.sort === state.sort;
      button.setAttribute('aria-sort', active ? (state.direction > 0 ? 'ascending' : 'descending') : 'none');
    });
  }

  function renderSources() {
    const sources = state.data.source_health || [];
    const metric = (value, text) => badge(value == null ? `${text}: Unknown` : `${text}: ${fmt(value)}`, value == null ? 'warn' : 'neutral');
    const rate = source => source.parser_rate == null ? 'Parse rate: Unknown' : `Parse rate: ${Math.round(source.parser_rate * 100)}%`;
    $('#source-health').innerHTML = sources.length ? sources.map(source => `<article class="source-card"><div class="source-card-top"><h3>${escapeHtml(source.name)}</h3>${badge(titleCase(source.state), stateTone(source.state))}</div><div class="source-meta">${badge(source.purpose, source.source_role === 'primary_validation' ? 'good' : 'info')}${badge(titleCase(source.access), 'neutral')}${metric(source.fetched_rows, 'Fetched')}${metric(source.accepted_rows, 'Accepted')}${metric(source.reviewed_rows, 'Review')}${badge(rate(source), source.parser_rate == null ? 'warn' : 'neutral')}</div><p><strong>Coverage:</strong> ${escapeHtml(source.coverage || 'Unknown')} · <strong>Retained date:</strong> ${escapeHtml(compactDate(source.retained_data_date))} · <strong>Crawled:</strong> ${escapeHtml(source.crawled_at || 'Unknown')}</p><p><strong>Last successful:</strong> ${escapeHtml(compactDate(source.last_successful))} · <strong>Attempt:</strong> ${escapeHtml(compactDate(source.last_attempted))}</p>${source.failure_reason ? `<p>${badge('Safe failure', 'alert')} ${escapeHtml(source.failure_reason)}</p>` : ''}<p>${escapeHtml(source.next_action || 'No safe action recorded.')}</p></article>`).join('') : `<div class="empty">No source configuration is available. Keep the dashboard local and inspect data/sources.json.</div>`;
  }

  function renderOperations() {
    const operations = state.data.operations || {};
    $('#operations-note').textContent = operations.note || '';
    $('#operations').innerHTML = (operations.jobs || []).map(job => `<article class="operation"><div>${badge(titleCase(job.name), stateTone(job.state))}</div><div><strong>${escapeHtml(job.result || 'Unknown')}</strong><p>Last run: ${escapeHtml(job.last_run || 'Not recorded')} · Artifact: ${escapeHtml(job.artifact_date || 'Unknown')}<br>Next run: ${escapeHtml(job.next_run || 'Not recorded')} · Credentials: ${escapeHtml(String(job.credentials_available ?? 'Not recorded'))}${job.log_path ? `<br>Log: ${escapeHtml(job.log_path)}` : ''}</p></div></article>`).join('') || `<div class="empty">No scheduled-job status has been recorded. The dashboard does not inspect credentials.</div>`;
  }

  function list(items, emptyText = 'None recorded.') { return items?.length ? `<ul>${items.map(item => `<li>${escapeHtml(typeof item === 'string' ? item : JSON.stringify(item))}</li>`).join('')}</ul>` : `<p>${escapeHtml(emptyText)}</p>`; }
  function evidenceRow(item) { return `<article class="ledger-row"><div class="ledger-row-top"><strong>${escapeHtml(item.title || 'Untitled listing')}</strong>${badge(item.source_role === 'primary_validation' ? 'Primary validation' : 'Discovery', item.source_role === 'primary_validation' ? 'good' : 'info')}</div><p>${escapeHtml(item.source || 'Unknown source')} · ${escapeHtml(compactDate(item.show_date))} · ${escapeHtml(item.city || 'Unknown city')} · ${escapeHtml(item.venue || 'Unknown venue')}</p><div class="ledger-meta">${badge(item.venue_band || 'Unknown room band', item.venue_band ? 'neutral' : 'warn')}${badge(item.status || 'Unknown status', 'neutral')}${badge(item.role || 'Unknown role', 'neutral')}${item.parse_confidence != null ? badge(`Parse ${item.parse_confidence}`, 'neutral') : badge('Parse confidence unknown', 'warn')}${item.primary_confirmation ? badge('Confirmed primary evidence', 'good') : ''}</div><p>First seen ${escapeHtml(compactDate(item.first_seen))} · Last seen ${escapeHtml(compactDate(item.last_seen))}${item.url ? ` · <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">Open listing</a>` : ''}</p>${item.correction_notes?.length ? `<p>${badge('Correction overlay', 'warn')} ${escapeHtml(item.correction_notes.join(' · '))}</p>` : ''}</article>`; }

  function openDetail(slug) {
    const row = state.data.artists.find(item => item.slug === slug);
    if (!row) return;
    const diaspora = row.diaspora || {};
    const us = diaspora.us || {}, ca = diaspora.ca || {}, representative = diaspora.representative;
    const momentumParts = row.scores?.momentum?.parts || [];
    const exportParts = row.scores?.export_signal?.parts || [];
    $('#detail-content').innerHTML = `<header class="detail-header"><p class="eyebrow">${escapeHtml(row.stage)}</p><h2 id="detail-name">${escapeHtml(row.name)}</h2><p>${escapeHtml(row.next_action)}</p><p>${badge(row.primary_confirmed ? 'Primary platform confirmed' : 'Discovery evidence only', row.primary_confirmed ? 'good' : 'warn')} ${badge(titleCase(row.review_state), stateTone(row.review_state))} ${badge(titleCase(row.quadrant), row.quadrant === 'RISING' ? 'good' : 'info')}</p></header><section class="detail-grid"><article class="detail-score"><h3>Stature</h3>${scoreMarkup(row.scores?.stature, 'stature')}</article><article class="detail-score"><h3>India momentum</h3>${scoreMarkup(row.scores?.momentum, 'momentum')}</article><article class="detail-score"><h3>Diaspora export signal</h3>${scoreMarkup(row.scores?.export_signal, 'export_signal')}</article></section><p class="muted">Momentum is a rate-of-rise proxy and ${row.scores?.calibrated === false ? 'uses uncalibrated judgement weights' : 'has the recorded calibration status'}. It is separate from diaspora research; neither is a ticket forecast.</p><section class="detail-section"><h3>Lifecycle &amp; data state</h3><div class="detail-cards"><article class="detail-block"><h3>Observability</h3><p>${escapeHtml(row.observability?.trajectory_observable ? `Observable: ${row.observability.n_snapshots} crawls / ${row.observability.span_days} days.` : row.observability?.why || 'Trajectory is not observable.')}</p></article><article class="detail-block"><h3>Unknowns &amp; review</h3>${list([...(row.review_notes || []), ...(row.unknowns || []), ...(row.corrections || [])], 'No recorded review or unknown-state note.')}</article><article class="detail-block"><h3>Watchlist fields</h3><p>Owner: ${escapeHtml(row.watchlist?.owner || 'Unassigned')}<br>First contacted: ${escapeHtml(row.watchlist?.first_contacted || 'Not recorded')}<br>Do not pursue: ${row.watchlist?.do_not_pursue ? 'Yes' : 'No'}<br>Local note count: ${fmt(row.watchlist?.contact_note_count || 0)}</p></article><article class="detail-block"><h3>Handoff boundary</h3><p>${escapeHtml(row.forecast_note || 'No forecast is present.')}</p></article></div></section><section class="detail-section"><h3>Diaspora research — separate geographies</h3><div class="detail-cards"><article class="detail-block"><h3>United States</h3><p>${hasValue(us.avg_monthly) ? `${fmt(us.avg_monthly)} average monthly searches` : 'No usable US search evidence'}<br>${escapeHtml(us.fetched_at || 'Not fetched')} · ${escapeHtml(us.source || 'Unknown source')}</p></article><article class="detail-block"><h3>Canada</h3><p>${hasValue(ca.avg_monthly) ? `${fmt(ca.avg_monthly)} average monthly searches` : 'No usable Canada search evidence'}<br>${escapeHtml(ca.fetched_at || 'Not fetched')} · ${escapeHtml(ca.source || 'Unknown source')}</p></article><article class="detail-block"><h3>Representative display</h3><p>${representative ? `${escapeHtml(String(representative.geo).toUpperCase())}: ${fmt(representative.avg_monthly)}. ${escapeHtml(representative.rule || 'Stronger geography only.')}` : 'No representative geography. US and Canada are never summed.'}</p></article><article class="detail-block"><h3>Foreign-date evidence</h3>${list((diaspora.qualifying_foreign_dates || []).map(item => `${item.country || 'Unknown'} · ${compactDate(item.date)} · ${item.city || 'Unknown city'} (${item.source || 'unknown source'})`), 'No qualifying foreign date recorded.')}</article></div></section><section class="detail-section"><h3>Score evidence</h3><div class="detail-cards"><article class="detail-block"><h3>Momentum inputs</h3>${list(momentumParts.map(item => `${item.signal}: ${item.observable ? 'observable' : 'unknown'} — ${item.note || ''}`), 'No momentum input is observable.')}</article><article class="detail-block"><h3>Export inputs</h3>${list(exportParts.map(item => `${item.signal}: ${item.observable ? 'observable' : 'unknown'} — ${item.note || ''}`), 'No export input is observable.')}</article></div></section><section class="detail-section"><h3>Chronological booking evidence</h3><div class="ledger">${row.evidence?.length ? row.evidence.map(evidenceRow).join('') : '<div class="empty">No ledger evidence is linked to this record.</div>'}</div></section>`;
    const diasporaCards = new Map(
      $$('.detail-block', $('#detail-content')).map(card => [card.querySelector('h3')?.textContent, card])
    );
    const setDiasporaText = (heading, text) => {
      const paragraph = diasporaCards.get(heading)?.querySelector('p');
      if (paragraph) paragraph.textContent = text;
    };
    setDiasporaText('United States',
      `${geoEvidence(us, 'US', diaspora.search_usable)} · ${us.fetched_at || 'Not fetched'} · ${us.source || 'Unknown source'}`);
    setDiasporaText('Canada',
      `${geoEvidence(ca, 'Canada', diaspora.search_usable)} · ${ca.fetched_at || 'Not fetched'} · ${ca.source || 'Unknown source'}`);
    setDiasporaText('Representative display', representative
      ? `${String(representative.geo).toUpperCase()}: ${fmt(representative.avg_monthly)}. ${representative.rule || 'Stronger geography only.'}${diaspora.search_usable ? '' : ' Display only: query identity is unvalidated.'}`
      : 'No representative geography. US and Canada are never summed.');
    const dialog = $('#artist-detail');
    if (!dialog.open) dialog.showModal();
  }

  function bind() {
    ['#search', '#stage-filter', '#genre-filter', '#kind-filter', '#city-filter', '#source-filter', '#primary-filter', '#venue-filter', '#review-filter', '#freshness-filter'].forEach(id => $(id).addEventListener('input', renderArtists));
    $('#artist-body').addEventListener('click', event => { const row = event.target.closest('[data-open]'); if (row) openDetail(row.dataset.open); });
    $('#artist-body').addEventListener('keydown', event => { if ((event.key === 'Enter' || event.key === ' ') && event.target.closest('[data-open]')) { event.preventDefault(); openDetail(event.target.closest('[data-open]').dataset.open); } });
    $('#action-queue').addEventListener('click', event => { const button = event.target.closest('[data-open]'); if (button) openDetail(button.dataset.open); });
    $$('.sort-button').forEach(button => button.addEventListener('click', () => { const next = button.dataset.sort; state.direction = state.sort === next ? state.direction * -1 : 1; state.sort = next; renderArtists(); }));
    $('#close-detail').addEventListener('click', () => $('#artist-detail').close());
    $('#artist-detail').addEventListener('click', event => { if (event.target === $('#artist-detail')) $('#artist-detail').close(); });
  }

  function render(data) {
    state.data = data;
    populate('#stage-filter', stageOrder);
    populate('#genre-filter', values('genre'));
    populate('#kind-filter', values('kind'));
    populate('#city-filter', values('cities'));
    populate('#source-filter', values('sources'));
    populate('#venue-filter', values('venue_bands'));
    renderOverview(); renderFunnel(); renderQueue(); renderQuality(); renderArtists(); renderSources(); renderOperations(); bind();
    $('#loading').classList.add('done');
  }

  fetch('dashboard-data.json', { cache: 'no-store' })
    .then(response => response.ok ? response.json() : Promise.reject(new Error(`Data load failed: ${response.status}`)))
    .then(render)
    .catch(error => { $('#loading').textContent = `Dashboard data could not load: ${error.message}. Start the local dashboard server rather than opening index.html directly.`; });
})();
