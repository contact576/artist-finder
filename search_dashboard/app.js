(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const geoLabels = { in: 'India', us: 'USA', ca: 'Canada' };
  const state = { data: null, geo: 'in', month: null, sort: 'latest', direction: -1, page: 1, pageSize: 25, lastTrigger: null };

  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const fmt = value => value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toLocaleString('en-US', { maximumFractionDigits: 0 });
  const pct = value => value == null || Number.isNaN(Number(value)) ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(Math.abs(value) >= 1 ? 0 : 1)}%`;
  const title = value => String(value || 'unknown').replaceAll('_', ' ').replace(/\b\w/g, x => x.toUpperCase());
  const monthIndex = value => { const [year, month] = String(value || '').split('-').map(Number); return year * 12 + month - 1; };
  const indexMonth = value => `${Math.floor(value / 12).toString().padStart(4, '0')}-${(value % 12 + 1).toString().padStart(2, '0')}`;
  const monthLabel = value => {
    if (!/^\d{4}-\d{2}$/.test(value || '')) return 'Unknown';
    const [year, month] = value.split('-').map(Number);
    return new Intl.DateTimeFormat('en', { month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(year, month - 1, 1)));
  };
  const badge = (text, tone = 'neutral') => `<span class="badge ${tone}">${escapeHtml(text)}</span>`;
  const toneForStatus = status => ({ verified: 'good', candidate: 'warn', inactive: 'neutral', rejected: 'alert' }[status] || 'neutral');
  const excludedStates = new Set(['rejected', 'merged', 'inactive']);
  function isResearched(row) {
    if (row.status === 'verified') return true;
    if (row.research_state) return row.curated === true && row.research_state === 'directory_candidate';
    const kind = row.identity_evidence?.kind;
    return Boolean(kind && !['legacy_candidate', 'legacy'].includes(kind));
  }
  function scopedRosterRows() {
    const scope = $('#status-filter')?.value || 'research';
    const eligible = state.data.artists.filter(row => !excludedStates.has(row.status));
    if (scope === 'verified') return eligible.filter(row => row.status === 'verified');
    if (scope === 'candidate') return eligible.filter(row => row.status === 'candidate');
    return eligible.filter(isResearched);
  }
  function activeCategoryRows() {
    const genre = $('#genre-filter')?.value || '';
    return scopedRosterRows().filter(row => !genre || row.primary_genre === genre);
  }
  function verifiedMetricRows() {
    const genre = $('#genre-filter')?.value || '';
    return state.data.artists.filter(row => row.status === 'verified'
      && row.keyword_review_state === 'approved' && (!genre || row.primary_genre === genre));
  }

  function metric(row, geo = state.geo, selected = state.month) {
    const series = row.geos?.[geo]?.series || [];
    const values = new Map(series.map(item => [item.month, Number(item.searches)]));
    const latest = values.has(selected) ? values.get(selected) : null;
    const current = monthIndex(selected);
    const previous = values.has(indexMonth(current - 1)) ? values.get(indexMonth(current - 1)) : null;
    const absolute = latest == null || previous == null ? null : latest - previous;
    const mom = absolute == null || !previous ? null : absolute / previous;
    const priorYear = values.has(indexMonth(current - 12)) ? values.get(indexMonth(current - 12)) : null;
    const yoy = latest == null || !priorYear ? null : (latest - priorYear) / priorYear;
    const historical = series.filter(item => item.month <= selected).map(item => Number(item.searches));
    const mean = values => values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
    return { latest, previous, absolute, mom, average3: mean(historical.slice(-3)), average12: mean(historical.slice(-12)), yoy, series };
  }

  function genreLabel(id) {
    return state.data.niches.find(item => item.id === id)?.name || title(id);
  }

  function drawChart(canvas) {
    const packed = canvas.dataset.series;
    if (!packed) return;
    let series;
    try { series = JSON.parse(decodeURIComponent(packed)); } catch { return; }
    const selected = canvas.dataset.full === 'true' ? series : series.filter(item => item.month <= state.month).slice(-12);
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width * (window.devicePixelRatio || 1)));
    const height = Math.max(1, Math.round(rect.height * (window.devicePixelRatio || 1)));
    canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext('2d');
    const scale = window.devicePixelRatio || 1;
    ctx.scale(scale, scale);
    const w = rect.width, h = rect.height, pad = 5;
    const values = selected.map(item => Number(item.searches)).filter(Number.isFinite);
    if (!values.length) return;
    const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
    ctx.strokeStyle = '#dfe4ec'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, h - pad); ctx.lineTo(w, h - pad); ctx.stroke();
    ctx.strokeStyle = '#1f5fd1'; ctx.lineWidth = canvas.classList.contains('detail-chart') ? 2.25 : 1.75;
    ctx.beginPath();
    selected.forEach((item, index) => {
      const x = selected.length === 1 ? w / 2 : pad + index * (w - pad * 2) / (selected.length - 1);
      const y = h - pad - ((Number(item.searches) - min) / range) * (h - pad * 2);
      if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
  }

  function drawCharts(root = document) { $$('canvas[data-series]', root).forEach(drawChart); }

  function renderControls() {
    $('#month-select').innerHTML = state.data.months.map(month => `<option value="${month}">${monthLabel(month)}</option>`).join('');
    $('#month-select').value = state.month;
  }

  function renderCategoryControls() {
    const rows = scopedRosterRows();
    const ordered = state.data.niches.filter(niche => rows.some(row => row.primary_genre === niche.id));
    const selected = $('#genre-filter').value;
    if (selected && !ordered.some(niche => niche.id === selected)) $('#genre-filter').value = '';
    $('#genre-filter').innerHTML = `<option value="">All combined</option>${ordered.map(niche => `<option value="${escapeHtml(niche.id)}">${escapeHtml(niche.name)}</option>`).join('')}`;
    $('#genre-filter').value = selected && ordered.some(niche => niche.id === selected) ? selected : '';
    const current = $('#genre-filter').value;
    $('#category-nav').innerHTML = `<button type="button" class="${current ? '' : 'active'}" data-genre="" role="listitem"><span>All combined</span><span>${fmt(rows.length)}</span></button>${ordered.map(niche => `<button type="button" class="${current === niche.id ? 'active' : ''}" data-genre="${escapeHtml(niche.id)}" role="listitem"><span>${escapeHtml(niche.name)}</span><span>${fmt(rows.filter(row => row.primary_genre === niche.id).length)}</span></button>`).join('')}`;
  }
  function totalsFor(geo, rows = verifiedMetricRows()) {
    const metrics = rows.map(row => metric(row, geo)).filter(m => m.latest != null);
    const total = metrics.reduce((sum, m) => sum + m.latest, 0);
    const comparable = metrics.filter(m => m.previous != null);
    const previous = comparable.reduce((sum, m) => sum + m.previous, 0);
    const current = comparable.reduce((sum, m) => sum + m.latest, 0);
    return { total: metrics.length ? total : null, coverage: metrics.length, mom: previous ? (current - previous) / previous : null };
  }

  function aggregateSeries(geo, rows = verifiedMetricRows()) {
    const totals = new Map();
    rows.forEach(row => (row.geos?.[geo]?.series || []).filter(item => item.month <= state.month).forEach(item => {
      totals.set(item.month, (totals.get(item.month) || 0) + Number(item.searches || 0));
    }));
    return [...totals.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([month, searches]) => ({ month, searches }));
  }
  function renderOverview() {
    const selected = totalsFor(state.geo);
    const verified = state.data.artists.filter(row => row.status === 'verified').length;
    const candidates = state.data.artists.filter(row => row.status === 'candidate').length;
    const ideaBacklog = state.data.candidates.filter(row => row.state === 'needs_review').length;
    $('#market-total').textContent = fmt(selected.total);
    $('#market-total-note').textContent = `${geoLabels[state.geo]} · ${fmt(selected.coverage)} artists in current scope`;
    $('#market-mom').textContent = pct(selected.mom);
    $('#market-mom').className = selected.mom == null ? 'unknown' : selected.mom >= 0 ? 'positive' : 'negative';
    $('#market-mom-note').textContent = `${geoLabels[state.geo]} · comparable artists in current scope`;
    $('#verified-count').textContent = fmt(verified);
    $('#review-count').textContent = fmt(candidates + ideaBacklog);
    $('#freshness-badge').textContent = `${monthLabel(state.month)} data · ${geoLabels[state.geo]}`;
    $$('.market-name').forEach(node => { node.textContent = geoLabels[state.geo]; });
    $$('#geo-toggle button').forEach(button => {
      const active = button.dataset.geo === state.geo;
      button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    });
  }
  function renderComparison() {
    const category = $('#genre-filter').value ? genreLabel($('#genre-filter').value) : 'All included categories';
    $('#market-comparison').innerHTML = ['in', 'us', 'ca'].map(geo => {
      const summary = totalsFor(geo), series = aggregateSeries(geo);
      return `<article class="market-card ${geo}"><h3>${geoLabels[geo]}</h3><strong>${fmt(summary.total)}</strong><p>Monthly searches · ${fmt(summary.coverage)} included artists</p><p class="${summary.mom == null ? 'unknown' : summary.mom >= 0 ? 'positive' : 'negative'}">Comparable MoM: ${pct(summary.mom)}</p><span class="trend-label">12-month aggregate trend · ${escapeHtml(category)}</span>${chartCanvas(series, false, 'aggregate-chart', `${geoLabels[geo]} 12-month aggregate search trend`)}</article>`;
    }).join('');
    drawCharts($('#market-comparison'));
  }
  function genreRows() {
    const groups = new Map();
    verifiedMetricRows().forEach(row => {
      const m = metric(row);
      if (m.latest == null) return;
      const group = groups.get(row.primary_genre) || { id: row.primary_genre, items: [] };
      group.items.push({ row, metric: m }); groups.set(row.primary_genre, group);
    });
    return [...groups.values()].map(group => {
      const ordered = [...group.items].sort((a, b) => b.metric.latest - a.metric.latest);
      const values = ordered.map(item => item.metric.latest).sort((a, b) => a - b);
      const midpoint = Math.floor(values.length / 2);
      const median = values.length % 2 ? values[midpoint] : (values[midpoint - 1] + values[midpoint]) / 2;
      const comparable = ordered.filter(item => item.metric.previous != null);
      const previous = comparable.reduce((sum, item) => sum + item.metric.previous, 0);
      const current = comparable.reduce((sum, item) => sum + item.metric.latest, 0);
      const total = values.reduce((sum, value) => sum + value, 0);
      const topFive = ordered.slice(0, 5).reduce((sum, item) => sum + item.metric.latest, 0);
      const backlog = state.data.artists.filter(row => row.status === 'candidate'
        && isResearched(row) && row.primary_genre === group.id).length;
      return {
        id: group.id, total, median, coverage: ordered.length,
        mom: previous ? (current - previous) / previous : null,
        topArtist: ordered[0]?.row.name || '-',
        topFiveShare: total ? topFive / total : null,
        backlog,
        series: aggregateSeries(state.geo, ordered.map(item => item.row))
      };
    }).sort((a, b) => b.total - a.total);
  }

  function renderGenres() {
    const rows = genreRows();
    $('#genre-chart').innerHTML = rows.length ? `<div class="table-wrap"><table class="genre-table"><thead><tr><th>Category</th><th>Tracked-roster volume</th><th>Median artist</th><th>MoM</th><th>Top artist</th><th>Top-five share</th><th>Verified</th><th>Research backlog</th><th>12-month trend</th></tr></thead><tbody>${rows.map(row => `<tr><td><strong>${escapeHtml(genreLabel(row.id))}</strong></td><td>${fmt(row.total)}</td><td>${fmt(row.median)}</td><td class="${row.mom == null ? 'unknown' : row.mom >= 0 ? 'positive' : 'negative'}">${pct(row.mom)}</td><td>${escapeHtml(row.topArtist)}</td><td>${row.topFiveShare == null ? '-' : `${(row.topFiveShare * 100).toFixed(1)}%`}</td><td>${fmt(row.coverage)}</td><td>${fmt(row.backlog)}</td><td>${chartCanvas(row.series, false, '', `${genreLabel(row.id)} 12-month tracked-roster trend`)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No verified artists have a measurement for this market and month.</div>';
    drawCharts($('#genre-chart'));
  }

  function rankMarkup(items, kind) {
    if (!items.length) return '<div class="empty">Need comparable monthly values to rank this list.</div>';
    return items.slice(0, 5).map((item, index) => {
      const value = kind === 'percent' ? item.metric.mom : item.metric.absolute;
      const text = kind === 'percent' ? pct(value) : `${value >= 0 ? '+' : ''}${fmt(value)}`;
      return `<button type="button" class="rank-row" data-open="${escapeHtml(item.row.slug)}"><span class="rank-number">${index + 1}</span><span><strong>${escapeHtml(item.row.name)}</strong><small>${escapeHtml(genreLabel(item.row.primary_genre))} · ${fmt(item.metric.previous)} → ${fmt(item.metric.latest)}</small></span><span class="${value >= 0 ? 'gain' : 'loss'}">${text}</span></button>`;
    }).join('');
  }

  function renderRisers() {
    const items = verifiedMetricRows().map(row => ({ row, metric: metric(row) })).filter(item => item.metric.absolute != null);
    const absolute = [...items].sort((a, b) => b.metric.absolute - a.metric.absolute);
    const minimum = Number($('#minimum-select').value || 100);
    const percentage = items.filter(item => item.metric.previous >= minimum && item.metric.mom != null).sort((a, b) => b.metric.mom - a.metric.mom);
    const underdogs = items.filter(item => item.metric.latest <= minimum * 10 && item.metric.absolute > 0).sort((a, b) => b.metric.mom - a.metric.mom);
    $('#absolute-risers').innerHTML = rankMarkup(absolute, 'absolute');
    $('#percentage-risers').innerHTML = rankMarkup(percentage, 'percent');
    $('#underdogs').innerHTML = rankMarkup(underdogs, 'percent');
  }

  function filteredArtists() {
    const query = $('#artist-search').value.trim().toLowerCase();
    const rows = activeCategoryRows().filter(row => {
      const haystack = [row.name, row.measurement_keyword, ...(row.aliases || []), ...(row.niche_tags || [])].join(' ').toLowerCase();
      return !query || haystack.includes(query);
    });
    const sortable = row => {
      const m = metric(row);
      if (state.sort === 'name') return row.name.toLowerCase();
      return m[state.sort] ?? -Infinity;
    };
    rows.sort((a, b) => {
      const av = sortable(a), bv = sortable(b);
      const order = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv));
      return order * state.direction || a.name.localeCompare(b.name);
    });
    return rows;
  }
  function chartCanvas(series, full = false, extraClass = '', label = '') {
    if (!series?.length) return '<span class="unknown">No series</span>';
    return `<canvas class="${full ? 'detail-chart' : 'spark'} ${extraClass}" data-series="${encodeURIComponent(JSON.stringify(series))}"${full ? ' data-full="true"' : ''} role="img" aria-label="${escapeHtml(label || (full ? 'Full available monthly search history' : '12-month search trend'))}"></canvas>`;
  }
  function renderArtists() {
    const rows = filteredArtists();
    const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * state.pageSize;
    const visible = rows.slice(start, start + state.pageSize);
    $('#artist-result-count').textContent = rows.length ? `${fmt(start + 1)}–${fmt(Math.min(start + state.pageSize, rows.length))} of ${fmt(rows.length)} matching` : '0 matching';
    $('#artist-body').innerHTML = visible.length ? visible.map(row => {
      const m = metric(row), changeTone = m.absolute == null ? 'unknown' : m.absolute >= 0 ? 'positive' : 'negative';
      const source = row.geos?.[state.geo] || {};
      const mapping = source.mapping_mode || 'not recorded';
      const fetched = source.fetched_at || row.fetched_at || 'Not recorded';
      return `<tr tabindex="0" data-open="${escapeHtml(row.slug)}" aria-label="Open details for ${escapeHtml(row.name)}"><td><span class="artist-name"><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.measurement_keyword || 'Keyword pending')}${mapping === 'close_variant' ? ' · close variant' : ''}</small></span></td><td>${escapeHtml(genreLabel(row.primary_genre))}</td><td>${badge(title(row.status), toneForStatus(row.status))}</td><td><strong>${fmt(m.latest)}</strong></td><td class="${changeTone}">${m.absolute == null ? '—' : `${m.absolute >= 0 ? '+' : ''}${fmt(m.absolute)}`}</td><td class="${changeTone}">${pct(m.mom)}</td><td>${fmt(m.average3)}</td><td>${fmt(m.average12)}</td><td class="${m.yoy == null ? 'unknown' : m.yoy >= 0 ? 'positive' : 'negative'}">${pct(m.yoy)}</td><td>${badge(title(mapping), mapping === 'exact' ? 'good' : mapping === 'close_variant' ? 'warn' : 'neutral')}</td><td>${escapeHtml(String(fetched).slice(0, 10))}</td><td>${chartCanvas(m.series)}</td></tr>`;
    }).join('') : '<tr class="empty-row"><td colspan="12"><div class="empty">No artists match these filters. Try All combined or change roster scope.</div></td></tr>';
    $$('.sort').forEach(button => button.setAttribute('aria-sort', button.dataset.sort === state.sort ? (state.direction === 1 ? 'ascending' : 'descending') : 'none'));
    renderPagination(pages); drawCharts($('#artist-body'));
  }
  function renderPagination(pages) {
    const button = page => `<button type="button" data-page="${page}" class="${page === state.page ? 'active' : ''}" aria-label="Page ${page}"${page === state.page ? ' aria-current="page"' : ''}>${page}</button>`;
    const candidates = [...new Set([1, state.page - 1, state.page, state.page + 1, pages].filter(page => page >= 1 && page <= pages))];
    $('#page-buttons').innerHTML = candidates.map(button).join('');
    $('#prev-page').disabled = state.page === 1; $('#next-page').disabled = state.page === pages;
  }

  function renderCandidates() {
    const candidates = state.data.candidates.filter(row => row.state === 'needs_review');
    const visible = candidates.slice(0, 50);
    $('#candidate-count').textContent = candidates.length > visible.length ? `${fmt(candidates.length)} to review · showing 50` : `${fmt(candidates.length)} to review`;
    $('#candidate-body').innerHTML = visible.length ? visible.map(row => `<tr><td><strong>${escapeHtml(row.text)}</strong></td><td>${escapeHtml(genreLabel(row.niche_id))}</td><td>${fmt(row.avg_monthly_searches)}</td><td>${badge(title(row.state), 'warn')}</td><td>${escapeHtml(String(row.last_seen || 'Unknown').slice(0, 10))}</td></tr>`).join('') : '<tr class="empty-row"><td colspan="5"><div class="empty">No candidate discovery rows are available.</div></td></tr>';
  }

  function renderHealth() {
    const health = state.data.data_health || {}, latest = health.latest_run, geo = latest?.geos?.[state.geo] || {};
    const cards = [
      ['Network', health.network_state === 'verified' ? 'Search + Partners' : 'Refresh required', health.network_note],
      ['Mapped in selected geo', geo.mapped ?? health.measured_by_geo?.[state.geo] ?? 0, latest ? `${geo.exact ?? 0} exact · ${geo.close_variant ?? 0} close variant · ${geo.ambiguous ?? 0} ambiguous` : 'No new-format monthly run recorded.'],
      ['Registry', `${health.verified ?? 0} verified`, `${health.researched_candidates ?? 0} researched candidates · ${health.legacy_unreviewed ?? 0} legacy rows excluded by default.`],
      ['Idea inbox', health.idea_review_backlog ?? 0, `${state.data.candidate_runs?.length || 0} retained discovery runs.`]
    ];
    $('#health-grid').innerHTML = cards.map(([label, value, note]) => `<article class="health-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><p>${escapeHtml(note || '')}</p></article>`).join('');
    const job = state.data.operations?.monthly;
    $('#automation').innerHTML = job ? `<strong>Monthly job: ${escapeHtml(job.result || job.state || 'Unknown')}</strong><br>Last run: ${escapeHtml(job.finished_at || job.started_at || 'Unknown')} · Next run: ${escapeHtml(job.next_run || 'Unknown')}<br>Log: ${escapeHtml(job.log_path || 'Not recorded')} · Credentials: ${escapeHtml(job.credentials_available || 'Presence not recorded')}` : '<strong>Monthly job status has not been recorded in this checkout.</strong><br>The dashboard does not inspect or display credential values.';
    $('#network-warning').classList.toggle('hidden', health.network_state === 'verified');
    if (health.network_state !== 'verified') $('#network-warning').innerHTML = `<div><h2>Refresh required before treating retained legacy values as new Search + Partners MoM</h2><p>${escapeHtml(health.network_note || 'Network state has not been verified.')}</p></div>`;
  }

  function openDetail(slug, trigger) {
    const row = state.data.artists.find(item => item.slug === slug);
    if (!row) return;
    state.lastTrigger = trigger || document.activeElement;
    const cards = ['in', 'us', 'ca'].map(geo => {
      const m = metric(row, geo), source = row.geos?.[geo] || {};
      return `<article class="geo-card"><h3>${geoLabels[geo]}</h3><div class="big">${fmt(m.latest)}</div><p>${monthLabel(state.month)} · MoM ${pct(m.mom)} · 3-month avg ${fmt(m.average3)} · 12-month avg ${fmt(m.average12)} · YoY ${pct(m.yoy)}</p>${chartCanvas(m.series, true, '', `${geoLabels[geo]} full available monthly search history`)}<p>${row.keyword_review_state === 'approved' ? 'Approved keyword' : 'Measurement query (pending review)'}: ${escapeHtml(row.measurement_keyword || source.keyword || 'Pending')}<br>Mapping quality: ${escapeHtml(source.mapping_mode || 'Not recorded')}<br>Last fetched: ${escapeHtml(source.fetched_at || row.fetched_at || 'Not recorded')}</p></article>`;
    }).join('');
    const evidence = row.identity_evidence || {};
    const selectedSource = row.geos?.[state.geo] || {};
    const variants = selectedSource.close_variants || row.close_variants || [];
    $('#detail-content').innerHTML = `<header class="detail-header"><p class="eyebrow">${escapeHtml(genreLabel(row.primary_genre))}</p><h2 id="detail-name">${escapeHtml(row.name)}</h2><p>${badge(title(row.status), toneForStatus(row.status))} ${badge(row.keyword_review_state === 'approved' ? 'Keyword approved' : 'Keyword review pending', row.keyword_review_state === 'approved' ? 'good' : 'warn')}</p><p>Approved measurement keyword: <strong>${escapeHtml(row.measurement_keyword || selectedSource.keyword || 'Pending')}</strong></p></header><section class="geo-detail-grid">${cards}</section><section class="detail-grid"><article class="detail-block"><h3>Measurement provenance</h3><p>Mapping quality: ${escapeHtml(selectedSource.mapping_mode || 'Not recorded')}<br>Mapping result: ${escapeHtml(selectedSource.result_text || 'Not recorded')}<br>Close variants: ${escapeHtml(variants.join(', ') || 'None recorded')}<br>Last fetched: ${escapeHtml(selectedSource.fetched_at || row.fetched_at || 'Not recorded')}</p></article><article class="detail-block"><h3>Contamination review</h3><p>Status: ${escapeHtml(row.contamination_status || 'Not recorded')}<br>${escapeHtml(row.contamination_note || 'No contamination note recorded.')}</p></article><article class="detail-block"><h3>Identity evidence</h3><p>Type: ${escapeHtml(evidence.kind || 'Unknown')}<br>Reviewed: ${escapeHtml(evidence.reviewed_at || row.last_reviewed || 'Not reviewed')}<br>${escapeHtml(evidence.note || 'No evidence note recorded.')}${evidence.url ? `<br><a href="${escapeHtml(evidence.url)}" target="_blank" rel="noopener noreferrer">Open evidence</a>` : ''}</p></article><article class="detail-block"><h3>Review and aliases</h3><p>Aliases: ${escapeHtml((row.aliases || []).join(', ') || 'None recorded')}<br>Niche tags: ${escapeHtml((row.niche_tags || []).map(genreLabel).join(', ') || 'None recorded')}<br>${escapeHtml((row.needs_attention || []).join(' ') || 'No active review warning.')}</p></article></section>`;
    if (row.keyword_review_state !== 'approved') {
      const keywordCopy = $('#detail-content .detail-header p:last-child');
      if (keywordCopy?.firstChild) keywordCopy.firstChild.textContent = 'Measurement query pending review: ';
    }
    const dialog = $('#artist-detail');
    if (!dialog.open) dialog.showModal();
    drawCharts($('#detail-content')); $('#close-detail').focus();
  }
  function renderAll() { renderCategoryControls(); renderOverview(); renderComparison(); renderGenres(); renderRisers(); renderArtists(); renderCandidates(); renderHealth(); }

  function resetAndRender() { state.page = 1; renderArtists(); }

  function bind() {
    $('#geo-toggle').addEventListener('click', event => {
      const button = event.target.closest('[data-geo]'); if (!button) return;
      state.geo = button.dataset.geo; state.page = 1; renderAll();
    });
    $('#month-select').addEventListener('change', event => { state.month = event.target.value; state.page = 1; renderAll(); });
    $('#minimum-select').addEventListener('change', renderRisers);
    $('#artist-search').addEventListener('input', resetAndRender);
    $('#genre-filter').addEventListener('change', () => { state.page = 1; renderAll(); });
    $('#status-filter').addEventListener('change', () => { state.page = 1; renderAll(); });
    $('#category-nav').addEventListener('click', event => {
      const button = event.target.closest('[data-genre]'); if (!button) return;
      $('#genre-filter').value = button.dataset.genre; state.page = 1; renderAll(); $('#artists').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    $$('.sort').forEach(button => button.addEventListener('click', () => {
      const next = button.dataset.sort; state.direction = state.sort === next ? -state.direction : (next === 'name' ? 1 : -1); state.sort = next; resetAndRender();
    }));
    $('#prev-page').addEventListener('click', () => { state.page -= 1; renderArtists(); });
    $('#next-page').addEventListener('click', () => { state.page += 1; renderArtists(); });
    $('#page-buttons').addEventListener('click', event => { const button = event.target.closest('[data-page]'); if (button) { state.page = Number(button.dataset.page); renderArtists(); } });
    $('#page-size').addEventListener('change', event => { state.pageSize = Number(event.target.value); state.page = 1; renderArtists(); });
    document.addEventListener('click', event => { const opener = event.target.closest('[data-open]'); if (opener) openDetail(opener.dataset.open, opener); });
    $('#artist-body').addEventListener('keydown', event => {
      const row = event.target.closest('[data-open]');
      if (row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openDetail(row.dataset.open, row); }
    });
    $('#close-detail').addEventListener('click', () => $('#artist-detail').close());
    $('#artist-detail').addEventListener('close', () => { if (state.lastTrigger?.focus) state.lastTrigger.focus(); });
    window.addEventListener('resize', () => drawCharts());
  }

  function render(data) {
    state.data = data; state.month = data.default_month || data.months?.[0] || null;
    if (!state.month) throw new Error('No monthly search values were supplied.');
    $('#network-badge').textContent = data.network === 'GOOGLE_SEARCH_AND_PARTNERS' ? 'Google Search + Partners' : data.network || 'Network unknown';
    $('#generated-at').textContent = data.generated_at || 'Not recorded';
    $('#caveat').textContent = data.caveat || 'Google-estimated monthly search demand. This is not ticket sales, audience size, or a ticket forecast.';
    renderControls(); bind(); renderAll(); $('#loading').classList.add('done');
  }

  fetch('dashboard-data.json', { cache: 'no-store' })
    .then(response => response.ok ? response.json() : Promise.reject(new Error(`Data load failed: ${response.status}`)))
    .then(render)
    .catch(error => {
      const loading = $('#loading'); loading.classList.add('error'); loading.innerHTML = `<span>Dashboard could not load: ${escapeHtml(error.message)}. Use the local launcher rather than opening the HTML file directly.</span>`;
    });
})();