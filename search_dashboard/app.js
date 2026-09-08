(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const geoLabels = { in: 'India', us: 'USA', ca: 'Canada' };
  const state = { data: null, geo: 'in', month: null, sort: 'latest', direction: -1, page: 1, pageSize: 25, lastTrigger: null, selectedSlug: null, refreshJobId: null, refreshStartedAt: null, refreshPromise: null, csrfToken: null, metaPromise: null, meta: null, favoriteSlugs: new Set(), favoritesLoaded: false, favoritePending: new Set(), favoriteMutations: new Map(), favoriteNextMutation: 0, favoriteLastResponse: 0, favoritesOnly: false, artistStatusPending: new Set(), eligibilityAvailable: false };
  const refreshStorageKey = 'artist-search-refresh-job';
  const refreshPollingTimeout = 40 * 60 * 1000;

  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const fmt = value => value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toLocaleString('en-US', { maximumFractionDigits: 0 });
  const pct = value => value == null || Number.isNaN(Number(value)) ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(Math.abs(value) >= 1 ? 0 : 1)}%`;
  const yoyLabel = value => value == null || Number.isNaN(Number(value)) ? 'Unavailable' : pct(value);
  const selectedValue = value => value == null || Number.isNaN(Number(value)) ? 'Unavailable' : `${fmt(value)} measured`;
  const averageLabel = value => value == null || Number.isNaN(Number(value)) ? 'Unavailable' : fmt(value);
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
    if (['directory_candidate', 'operator_candidate', 'public_identity_review'].includes(row.research_state)) return true;
    if (row.research_state) return false;
    const kind = row.identity_evidence?.kind;
    return Boolean(kind && !['legacy_candidate', 'legacy'].includes(kind));
  }
  function scopedRosterRows() {
    const scope = $('#status-filter')?.value || 'research';
    if (scope === 'inactive') return state.data.artists.filter(row => row.status === 'inactive');
    const eligible = state.data.artists.filter(row => !excludedStates.has(row.status));
    if (scope === 'verified') return eligible.filter(row => row.status === 'verified');
    if (scope === 'candidate') return eligible.filter(row => row.status === 'candidate');
    return eligible.filter(isResearched);
  }
  function bookingEligibility(row) {
    const value = row.booking_eligibility;
    if (!value || typeof value !== 'object') return { status: 'unchecked' };
    const status = ['eligible', 'review', 'excluded', 'exception'].includes(value.status) ? value.status : 'unchecked';
    return { ...value, status };
  }
  function eligibilityLabel(status) {
    return ({ eligible: 'Eligible', exception: 'Eligible exception', review: 'Needs review', excluded: 'Excluded', unchecked: 'Not assessed' }[status] || 'Not assessed');
  }
  function eligibilityTone(status) {
    return ({ eligible: 'good', exception: 'warn', review: 'warn', excluded: 'alert', unchecked: 'neutral' }[status] || 'neutral');
  }
  function safeHttpHref(value) {
    try {
      const url = new URL(String(value || ''), window.location.origin);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch { return ''; }
  }
  function bookingEvidenceMarkup(value) {
    if (!Array.isArray(value) || !value.length) return 'None recorded';
    const entries = value.map(item => {
      if (typeof item === 'string') return escapeHtml(item);
      if (!item || typeof item !== 'object') return '';
      const label = item.summary || [item.date, item.name, item.venue, item.city, item.evidence_kind, item.source].filter(Boolean).join(' · ') || 'Open booking evidence';
      const href = safeHttpHref(item.url);
      return href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>` : escapeHtml(label);
    }).filter(Boolean);
    return entries.length ? entries.join('<br>') : 'None recorded';
  }
  function mappingInfo(source) {
    const value = source?.mapping_quality;
    if (value && typeof value === 'object') {
      return { label: value.state || value.mapping_mode || source.mapping_mode || 'not recorded', mode: value.mapping_mode || source.mapping_mode };
    }
    return { label: value || source?.mapping_mode || 'not recorded', mode: source?.mapping_mode || value };
  }
  function eligibilityRows(rows) {
    const filter = $('#eligibility-filter')?.value || 'all';
    if (!state.eligibilityAvailable || filter === 'all') return rows;
    if (filter === 'shortlist') return rows.filter(row => ['eligible', 'exception'].includes(bookingEligibility(row).status));
    return rows.filter(row => bookingEligibility(row).status === filter);
  }
  function activeCategoryRows() {
    const genre = $('#genre-filter')?.value || '';
    return eligibilityRows(scopedRosterRows()).filter(row => !genre || row.primary_genre === genre);
  }
  function verifiedMetricRows(geo = state.geo) {
    const genre = $('#genre-filter')?.value || '';
    return state.data.artists.filter(row => row.status === 'verified'
      && row.keyword_review_state === 'approved'
      && row.summary_eligible === true
      && row.summary_eligible_geos?.[geo] === true
      && (!genre || row.primary_genre === genre));
  }
  function isFavorite(row) { return state.favoriteSlugs.has(row.slug); }
  function favoriteButton(row, extraClass = '') {
    const active = isFavorite(row);
    const pending = state.favoritePending.has(row.slug);
    const action = pending ? 'Saving Favorite' : active ? 'Remove from Favorites' : 'Add to Favorites';
    return `<button type="button" class="favorite-toggle ${extraClass} ${active ? 'active' : ''} ${pending ? 'pending' : ''}" data-favorite="${escapeHtml(row.slug)}" aria-pressed="${active}" aria-busy="${pending}" aria-label="${action}: ${escapeHtml(row.name)}" title="${action}"${pending ? ' disabled' : ''}><span aria-hidden="true">${pending ? '…' : active ? '★' : '☆'}</span></button>`;
  }
  function rowStatusButton(row) {
    const restore = row.status === 'inactive';
    const pending = state.artistStatusPending.has(row.slug);
    const action = restore ? 'Restore' : 'Archive';
    return `<button type="button" class="row-status-toggle ${restore ? 'restore' : 'archive'} ${pending ? 'pending' : ''}" data-row-status="${escapeHtml(row.slug)}" data-active="${restore}" aria-busy="${pending}" aria-label="${pending ? 'Saving' : action} artist: ${escapeHtml(row.name)}" title="${action} artist"${pending ? ' disabled' : ''}>${pending ? 'Saving…' : action}</button>`;
  }

  function metric(row, geo = state.geo, selected = state.month) {
    const series = row.geos?.[geo]?.series || [];
    const values = new Map(series.map(item => [item.month, Number(item.searches)]));
    const latest = values.has(selected) ? values.get(selected) : null;
    const current = monthIndex(selected);
    const exactAverage = count => {
      if (latest == null) return null;
      const range = Array.from({ length: count }, (_, index) => values.get(indexMonth(current - index)));
      return range.every(value => value != null) ? range.reduce((sum, value) => sum + value, 0) / count : null;
    };
    const previous = values.has(indexMonth(current - 1)) ? values.get(indexMonth(current - 1)) : null;
    const absolute = latest == null || previous == null ? null : latest - previous;
    const mom = absolute == null || !previous ? null : absolute / previous;
    const priorYear = values.has(indexMonth(current - 12)) ? values.get(indexMonth(current - 12)) : null;
    const yoy = latest == null || !priorYear ? null : (latest - priorYear) / priorYear;
    return { latest, previous, absolute, mom, average3: exactAverage(3), average6: exactAverage(6), average12: exactAverage(12), yoy, series };
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
    const scope = $('#status-filter').value;
    // Review and archive are deliberate operator work queues. The normal
    // research scope shows every retained researched artist so the operator
    // can favorite or remove any of them; eligibility remains an optional filter.
    const rows = state.favoritesOnly ? state.data.artists.filter(isFavorite)
      : ['candidate', 'inactive'].includes(scope) ? scopedRosterRows()
      : eligibilityRows(scopedRosterRows());
    const ordered = state.data.niches.filter(niche => rows.some(row => row.primary_genre === niche.id));
    const selected = $('#genre-filter').value;
    if (selected && !ordered.some(niche => niche.id === selected)) $('#genre-filter').value = '';
    $('#genre-filter').innerHTML = `<option value="">All combined</option>${ordered.map(niche => `<option value="${escapeHtml(niche.id)}">${escapeHtml(niche.name)}</option>`).join('')}`;
    $('#genre-filter').value = selected && ordered.some(niche => niche.id === selected) ? selected : '';
    const current = $('#genre-filter').value;
    $('#category-nav').innerHTML = `<button type="button" class="${current ? '' : 'active'}" data-genre="" role="listitem"><span>All combined</span><span>${fmt(rows.length)}</span></button>${ordered.map(niche => `<button type="button" class="${current === niche.id ? 'active' : ''}" data-genre="${escapeHtml(niche.id)}" role="listitem"><span>${escapeHtml(niche.name)}</span><span>${fmt(rows.filter(row => row.primary_genre === niche.id).length)}</span></button>`).join('')}`;
    const favorites = $('#favorites-nav');
    favorites.classList.toggle('active', state.favoritesOnly); favorites.setAttribute('aria-pressed', String(state.favoritesOnly));
    $('#favorites-count').textContent = fmt(state.favoriteSlugs.size);
    $('#review-nav').classList.toggle('active', !state.favoritesOnly && $('#status-filter').value === 'candidate');
    $('#archive-nav').classList.toggle('active', !state.favoritesOnly && $('#status-filter').value === 'inactive');
  }
  function renderEligibilityControl() {
    const select = $('#eligibility-filter');
    if (!select) return;
    const wasAvailable = state.eligibilityAvailable;
    state.eligibilityAvailable = state.data.artists.some(row => row.booking_eligibility && typeof row.booking_eligibility === 'object');
    if (!state.eligibilityAvailable) {
      select.disabled = true;
      select.innerHTML = '<option value="all">Not assessed in this data</option>';
      select.value = 'all';
      return;
    }
    const selected = wasAvailable ? select.value : 'all';
    select.disabled = false;
    select.innerHTML = '<option value="shortlist">Shortlist: eligible + exceptions</option><option value="review">Needs review</option><option value="excluded">Excluded</option><option value="all">All booking checks</option>';
    select.value = ['shortlist', 'review', 'excluded', 'all'].includes(selected) ? selected : 'all';
  }
  function totalsFor(geo, rows = null) {
    rows = rows || verifiedMetricRows(geo);
    const metrics = rows.map(row => metric(row, geo)).filter(m => m.latest != null);
    const total = metrics.reduce((sum, m) => sum + m.latest, 0);
    const comparable = metrics.filter(m => m.previous != null);
    const previous = comparable.reduce((sum, m) => sum + m.previous, 0);
    const current = comparable.reduce((sum, m) => sum + m.latest, 0);
    return { total: metrics.length ? total : null, coverage: metrics.length, mom: previous ? (current - previous) / previous : null };
  }

  function aggregateSeries(geo, rows = null) {
    rows = rows || verifiedMetricRows(geo);
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
    $('#market-total').textContent = selectedValue(selected.total);
    $('#market-total-note').textContent = `${geoLabels[state.geo]} · ${fmt(selected.coverage)} booking-eligible verified artists with data (${fmt(verified)} verified identities total)`;
    $('#market-mom').textContent = pct(selected.mom);
    $('#market-mom').className = selected.mom == null ? 'unknown' : selected.mom >= 0 ? 'positive' : 'negative';
    $('#market-mom-note').textContent = `${geoLabels[state.geo]} · comparable artists in current scope`;
    $('#verified-count').textContent = fmt(verified);
    $('#review-count').textContent = fmt(candidates);
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
      return `<article class="market-card ${geo}"><h3>${geoLabels[geo]}</h3><strong>${selectedValue(summary.total)}</strong><p>Selected month · ${monthLabel(state.month)} · ${fmt(summary.coverage)} included artists</p><p class="${summary.mom == null ? 'unknown' : summary.mom >= 0 ? 'positive' : 'negative'}">Comparable MoM: ${pct(summary.mom)}</p><span class="trend-label">12-month aggregate trend · ${escapeHtml(category)}</span>${chartCanvas(series, false, 'aggregate-chart', `${geoLabels[geo]} 12-month aggregate search trend`)}</article>`;
    }).join('');
    const usa = totalsFor('us'), canada = totalsFor('ca');
    const stronger = usa.total == null && canada.total == null ? null : (canada.total ?? -Infinity) > (usa.total ?? -Infinity) ? { geo: 'ca', summary: canada } : { geo: 'us', summary: usa };
    $('#north-america-comparison').innerHTML = `<article class="north-card us"><span>USA selected-month searches</span><strong>${selectedValue(usa.total)}</strong><small>${fmt(usa.coverage)} included artists · ${monthLabel(state.month)}</small></article><article class="north-card ca"><span>Canada selected-month searches</span><strong>${selectedValue(canada.total)}</strong><small>${fmt(canada.coverage)} included artists · ${monthLabel(state.month)}</small></article><article class="north-card stronger"><span>Stronger geography</span><strong>${stronger ? geoLabels[stronger.geo] : 'Unavailable'}</strong><small>${stronger ? `${selectedValue(stronger.summary.total)} in that single market` : 'No comparable market value'} · never a sum</small></article>`;
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
    // Favorites are an operator decision, not a measurement or identity claim.
    // Do not hide an existing favorite because a later review archived it or
    // changed its booking/search eligibility.
    const base = state.favoritesOnly ? state.data.artists.filter(isFavorite) : activeCategoryRows();
    const rows = base.filter(row => {
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
      const mapping = mappingInfo(source);
      const fetched = source.last_updated || source.fetched_at || row.fetched_at || 'Not recorded';
      const yoyTone = m.yoy == null ? 'unknown' : m.yoy >= 0 ? 'positive' : 'negative';
      const eligibility = bookingEligibility(row);
      const eligibilityCell = state.eligibilityAvailable ? badge(eligibilityLabel(eligibility.status), eligibilityTone(eligibility.status)) : '<span class="unknown">Not assessed</span>';
      return `<tr tabindex="0" data-open="${escapeHtml(row.slug)}" aria-label="Open details for ${escapeHtml(row.name)}"><td class="artist-actions-cell"><div class="row-actions">${favoriteButton(row)}${rowStatusButton(row)}</div></td><td><span class="artist-name"><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.measurement_keyword || 'Keyword pending')}${source.mapping_mode === 'close_variant' ? ' · close variant' : ''}</small></span></td><td>${escapeHtml(genreLabel(row.primary_genre))}</td><td>${badge(title(row.status), toneForStatus(row.status))}</td><td>${eligibilityCell}</td><td><strong>${selectedValue(m.latest)}</strong><small class="table-subline">${monthLabel(state.month)}</small></td><td class="${changeTone}">${m.absolute == null ? '—' : `${m.absolute >= 0 ? '+' : ''}${fmt(m.absolute)}`}</td><td class="${changeTone}">${pct(m.mom)}</td><td>${averageLabel(m.average3)}</td><td>${averageLabel(m.average6)}</td><td>${averageLabel(m.average12)}</td><td class="${yoyTone}">${yoyLabel(m.yoy)}</td><td>${badge(title(mapping.label), mapping.mode === 'exact' ? 'good' : mapping.mode === 'close_variant' ? 'warn' : 'neutral')}</td><td>${escapeHtml(String(fetched).slice(0, 10))}</td><td>${chartCanvas(m.series)}</td></tr>`;
    }).join('') : `<tr class="empty-row"><td colspan="15"><div class="empty">${state.favoritesOnly ? 'No favorite artists match these filters. Use the star button to save an artist, or adjust the existing filters.' : 'No artists match these filters. Try All combined, another booking check, or a different roster scope.'}</div></td></tr>`;
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
    const health = state.data.data_health || {};
    const selectedAvailable = health.research_scope_selected_month_available_by_geo?.[state.geo];
    const apiMapped = health.research_scope_api_mapped_by_geo?.[state.geo];
    const scopeTotal = health.research_scope_total;
    const selectedMonth = health.research_scope_selected_month || state.month;
    const cards = [
      ['Network', health.network_label || 'Google Search + Search partners', health.network_note || health.network_state],
      ['Selected-month availability', scopeTotal == null || selectedAvailable == null ? 'Unavailable' : `${fmt(selectedAvailable)} / ${fmt(scopeTotal)}`, `Research scope · ${monthLabel(selectedMonth)} · selected market`],
      ['API-mapped in selected geo', apiMapped == null ? 'Unavailable' : `${fmt(apiMapped)} mapped`, `${geoLabels[state.geo]} only; not an all-registry fallback.`],
      ['Research scope', scopeTotal == null ? 'Unavailable' : `${fmt(scopeTotal)} artists`, `${health.verified ?? 0} verified · ${health.researched_candidates ?? 0} evidence-researched candidates.`],
      ['Idea inbox', health.idea_review_backlog == null ? 'Unavailable' : fmt(health.idea_review_backlog), `${state.data.candidate_runs?.length || 0} retained discovery runs.`]
    ];
    $('#health-grid').innerHTML = cards.map(([label, value, note]) => `<article class="health-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><p>${escapeHtml(note || '')}</p></article>`).join('');
    const job = state.data.operations?.monthly;
    $('#automation').innerHTML = job ? `<strong>Monthly job: ${escapeHtml(job.result || job.state || 'Unknown')}</strong><br>Last run: ${escapeHtml(job.finished_at || job.started_at || 'Unknown')} · Next run: ${escapeHtml(job.next_run || 'Unknown')}<br>Log: ${escapeHtml(job.log_path || 'Not recorded')} · Credentials: ${escapeHtml(job.credentials_available || 'Presence not recorded')}` : '<strong>Monthly job status has not been recorded in this checkout.</strong><br>The dashboard does not inspect or display credential values.';
    $('#network-warning').classList.toggle('hidden', health.network_state === 'verified');
    if (health.network_state !== 'verified') $('#network-warning').innerHTML = `<div><h2>Refresh required before treating retained legacy values as new Search + Search partners MoM</h2><p>${escapeHtml(health.network_note || 'Network state has not been verified.')}</p></div>`;
  }
  function openDetail(slug, trigger) {
    const row = state.data.artists.find(item => item.slug === slug);
    if (!row) return;
    state.selectedSlug = slug;
    state.lastTrigger = trigger || document.activeElement;
    const cards = ['in', 'us', 'ca'].map(geo => {
      const m = metric(row, geo), source = row.geos?.[geo] || {};
      const mapping = mappingInfo(source);
      return `<article class="geo-card"><h3>${geoLabels[geo]}</h3><div class="big">${selectedValue(m.latest)}</div><p>Selected month: ${monthLabel(state.month)}<br>MoM ${pct(m.mom)} · 3-month avg ${averageLabel(m.average3)} · 6-month avg ${averageLabel(m.average6)} · 12-month avg ${averageLabel(m.average12)} · YoY ${yoyLabel(m.yoy)}</p>${chartCanvas(m.series, true, '', `${geoLabels[geo]} full available monthly search history`)}<p>${row.keyword_review_state === 'approved' ? 'Approved keyword' : 'Measurement query (pending review)'}: ${escapeHtml(row.measurement_keyword || source.keyword || 'Pending')}<br>Mapping quality: ${escapeHtml(mapping.label)}<br>Last fetched: ${escapeHtml(source.last_updated || source.fetched_at || row.fetched_at || 'Not recorded')}</p></article>`;
    }).join('');
    const evidence = row.identity_evidence || {};
    const selectedSource = row.geos?.[state.geo] || {};
    const variants = selectedSource.close_variants || row.close_variants || [];
    const statusAction = row.status === 'inactive' ? 'reactivate' : 'deactivate';
    const statusLabel = row.status === 'inactive' ? 'Restore artist' : 'Remove artist';
    const eligibility = bookingEligibility(row);
    const selectedMapping = mappingInfo(selectedSource);
    const eligibilityDetail = state.eligibilityAvailable
      ? `Status: ${escapeHtml(eligibilityLabel(eligibility.status))}<br>Reason: ${escapeHtml(eligibility.reason || 'Not recorded')}<br>Last India show: ${escapeHtml(eligibility.last_india_show || 'Not recorded')}<br>Recent India events: ${escapeHtml(eligibility.recent_event_count ?? 'Not recorded')}<br>Search quality: ${escapeHtml(eligibility.search_quality || 'Not recorded')}<br>Evidence:<br>${bookingEvidenceMarkup(eligibility.evidence)}<br>${escapeHtml(eligibility.note || 'No additional eligibility note recorded.')}`
      : 'Booking eligibility has not been calculated in this dashboard payload.';
    $('#detail-content').innerHTML = `<header class="detail-header"><p class="eyebrow">${escapeHtml(genreLabel(row.primary_genre))}</p><h2 id="detail-name">${escapeHtml(row.name)}</h2><p>${badge(title(row.status), toneForStatus(row.status))} ${badge(row.keyword_review_state === 'approved' ? 'Keyword approved' : 'Keyword review pending', row.keyword_review_state === 'approved' ? 'good' : 'warn')}</p><p>${row.keyword_review_state === 'approved' ? 'Approved measurement keyword' : 'Measurement query pending review'}: <strong>${escapeHtml(row.measurement_keyword || selectedSource.keyword || 'Pending')}</strong></p><div class="detail-actions">${favoriteButton(row, 'detail-favorite')}<button type="button" class="secondary-action" data-artist-action="edit">Edit artist</button><button type="button" class="secondary-action" data-artist-action="reassign">Reassign category</button><button type="button" class="secondary-action" data-artist-action="keyword">Add or replace keyword</button><button type="button" class="secondary-action" data-artist-action="${statusAction}">${statusLabel}</button></div></header><section class="geo-detail-grid">${cards}</section><section class="detail-grid"><article class="detail-block"><h3>Booking eligibility</h3><p>${eligibilityDetail}</p></article><article class="detail-block"><h3>Measurement provenance</h3><p>Mapping quality: ${escapeHtml(selectedMapping.label)}<br>Mapping result: ${escapeHtml(selectedSource.result_text || selectedSource.mapping_quality?.result_text || 'Not recorded')}<br>Close variants: ${escapeHtml(variants.join(', ') || 'None recorded')}<br>Last fetched: ${escapeHtml(selectedSource.last_updated || selectedSource.fetched_at || row.fetched_at || 'Not recorded')}</p></article><article class="detail-block"><h3>Contamination review</h3><p>Status: ${escapeHtml(row.contamination_status || 'Not recorded')}<br>${escapeHtml(row.contamination_note || 'No contamination note recorded.')}</p></article><article class="detail-block"><h3>Identity evidence</h3><p>Type: ${escapeHtml(evidence.kind || 'Unknown')}<br>Research state: ${escapeHtml(row.research_state || 'Not recorded')}<br>Curated: ${row.curated === true ? 'Yes' : 'No'}<br>Reviewed: ${escapeHtml(evidence.reviewed_at || row.last_reviewed || 'Not reviewed')}<br>${escapeHtml(evidence.note || 'No evidence note recorded.')}${evidence.url ? `<br><a href="${escapeHtml(evidence.url)}" target="_blank" rel="noopener noreferrer">Open evidence</a>` : ''}</p></article><article class="detail-block"><h3>Audit trail and aliases</h3><p>First seen: ${escapeHtml(row.first_seen || 'Not recorded')}<br>Aliases: ${escapeHtml((row.aliases || []).join(', ') || 'None recorded')}<br>Niche tags: ${escapeHtml((row.niche_tags || []).map(genreLabel).join(', ') || 'None recorded')}<br>${escapeHtml((row.needs_attention || []).join(' ') || 'No active review warning.')}</p></article></section>`;
    const dialog = $('#artist-detail');
    if (!dialog.open) dialog.showModal();
    drawCharts($('#detail-content')); $('#close-detail').focus();
  }
  function showRefreshStatus(message, tone = 'neutral', href = '') {
    const node = $('#refresh-status');
    node.className = `refresh-status ${tone}`;
    node.textContent = message;
    const safeHref = safeHttpHref(href);
    if (safeHref) {
      const link = document.createElement('a');
      link.href = safeHref; link.target = '_blank'; link.rel = 'noopener noreferrer';
      link.textContent = ' Open GitHub Actions.';
      node.append(link);
    }
  }

  async function ensureMeta(force = false) {
    if (state.csrfToken && !force) return { csrf_token: state.csrfToken };
    if (!state.metaPromise || force) {
      state.metaPromise = fetch('/api/v1/meta', { cache: 'no-store' })
        .then(response => response.ok ? response.json() : Promise.reject(new Error(`Metadata request failed: ${response.status}`)))
        .then(meta => {
          if (!meta.csrf_token) throw new Error('Metadata response did not include a CSRF token.');
          state.csrfToken = meta.csrf_token; state.meta = meta; return meta;
        })
        .finally(() => { state.metaPromise = null; });
    }
    return state.metaPromise;
  }

  async function apiRequest(path, options = {}, retried = false) {
    const method = (options.method || 'GET').toUpperCase();
    const mutation = ['POST', 'PATCH', 'PUT', 'DELETE'].includes(method);
    const headers = { ...(options.headers || {}) };
    if (mutation) {
      const meta = await ensureMeta();
      headers['Content-Type'] = 'application/json'; headers['X-CSRF-Token'] = meta.csrf_token;
    }
    const response = await fetch(path, { ...options, method, headers });
    const contentType = response.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await response.json() : {};
    if (!response.ok) {
      if (mutation && response.status === 403 && !retried) { state.csrfToken = null; return apiRequest(path, options, true); }
      throw new Error(body.message || body.error || `Request failed: ${response.status}`);
    }
    return body;
  }
  async function loadFavorites() {
    try {
      const result = await apiRequest('/api/v1/favorites', { method: 'GET' });
      state.favoriteSlugs = new Set((result.favorites || []).filter(slug => state.data.artists.some(row => row.slug === slug)));
      state.favoritesLoaded = true;
    } catch {
      // A transient read failure must not visually erase favorites that were
      // already confirmed in this session.
      if (state.favoritesLoaded) showRefreshStatus('Favorites could not load; saved state was not changed.', 'error');
    }
  }
  async function toggleFavorite(slug, trigger) {
    const row = state.data.artists.find(item => item.slug === slug);
    if (!row || state.favoritePending.has(slug)) return;
    const favorite = !isFavorite(row);
    const mutation = ++state.favoriteNextMutation;
    state.favoritePending.add(slug);
    state.favoriteMutations.set(slug, { favorite, mutation });
    if (favorite) state.favoriteSlugs.add(slug); else state.favoriteSlugs.delete(slug);
    state.page = 1; renderAll();
    if ($('#artist-detail').open && state.selectedSlug === slug) openDetail(slug, state.lastTrigger);
    showRefreshStatus(`Saving ${row.name} in Favorites…`, 'running');
    try {
      const result = await apiRequest(`/api/artists/${encodeURIComponent(slug)}/favorite`, { method: 'POST', body: JSON.stringify({ favorite }) });
      // Responses can arrive out of order when two stars are clicked quickly.
      // Apply only the newest complete server set, then reapply all still
      // pending optimistic choices. A final GET below reconciles the durable
      // set after the last request settles.
      if (Array.isArray(result.favorites) && mutation >= state.favoriteLastResponse) {
        state.favoriteSlugs = new Set(result.favorites);
        state.favoriteLastResponse = mutation;
        state.favoriteMutations.forEach((pending, pendingSlug) => {
          if (pending.favorite) state.favoriteSlugs.add(pendingSlug);
          else state.favoriteSlugs.delete(pendingSlug);
        });
      }
      state.page = 1; renderAll();
      if ($('#artist-detail').open && state.selectedSlug === slug) openDetail(slug, state.lastTrigger);
      showRefreshStatus(`${row.name} ${favorite ? 'added to' : 'removed from'} Favorites.`, 'success');
    } catch (error) {
      if (favorite) state.favoriteSlugs.delete(slug); else state.favoriteSlugs.add(slug);
      state.page = 1; renderAll();
      if ($('#artist-detail').open && state.selectedSlug === slug) openDetail(slug, state.lastTrigger);
      showRefreshStatus(`Favorite was not saved: ${error.message}`, 'error');
    } finally {
      state.favoritePending.delete(slug);
      state.favoriteMutations.delete(slug);
      if (!state.favoritePending.size) await loadFavorites();
      renderAll();
      if ($('#artist-detail').open && state.selectedSlug === slug) openDetail(slug, state.lastTrigger);
      if (trigger?.isConnected) trigger.disabled = false;
    }
  }
  function mergeArtist(response, render = true) {
    const artist = response.artist || response;
    if (!artist?.slug) return artist || null;
    const index = state.data.artists.findIndex(row => row.slug === artist.slug);
    if (index >= 0) state.data.artists[index] = { ...state.data.artists[index], ...artist };
    else state.data.artists.push(artist);
    state.data.generated_at = response.generated_at || state.data.generated_at;
    if (render) renderAll();
    return artist;
  }
  function setFormBusy(form, busy) {
    const submit = $('button[type="submit"]', form);
    if (!submit) return;
    if (busy) {
      form.dataset.pending = 'true';
      submit.dataset.idleLabel = submit.textContent;
      submit.textContent = 'Saving…';
      submit.disabled = true;
    } else {
      delete form.dataset.pending;
      submit.textContent = submit.dataset.idleLabel || 'Save';
      submit.disabled = false;
    }
  }
  async function updateArtistStatus(slug, active, trigger) {
    const row = state.data.artists.find(item => item.slug === slug);
    if (!row || state.artistStatusPending.has(slug)) return;
    state.artistStatusPending.add(slug);
    renderArtists();
    showRefreshStatus(`${active ? 'Restoring' : 'Archiving'} ${row.name}…`, 'running');
    try {
      const result = await apiRequest(`/api/artists/${encodeURIComponent(slug)}/status`, { method: 'POST', body: JSON.stringify({ active }) });
      const updated = mergeArtist(result);
      await refreshDashboardData(updated);
      showRefreshStatus(active ? 'Artist restored to the active list.' : 'Artist archived. Restore it anytime from Archived.', 'success');
    } catch (error) {
      showRefreshStatus(`Artist status could not be updated: ${error.message}`, 'error');
    } finally {
      state.artistStatusPending.delete(slug);
      renderAll();
      if (trigger?.isConnected) trigger.disabled = false;
    }
  }
  function genreOptions(select, selected = '') {
    select.innerHTML = state.data.niches.map(niche => `<option value="${escapeHtml(niche.id)}">${escapeHtml(niche.name)}</option>`).join('');
    select.value = selected || state.data.niches[0]?.id || '';
  }

  function selectedArtist() { return state.data.artists.find(row => row.slug === state.selectedSlug); }

  function openArtistEditor(slug = null) {
    const row = slug ? state.data.artists.find(item => item.slug === slug) : null;
    const edit = Boolean(row);
    $('#artist-editor-title').textContent = edit ? 'Edit artist' : 'Add artist';
    $('#artist-editor-slug').value = row?.slug || '';
    $('#artist-editor-name').value = row?.name || '';
    $('#artist-editor-aliases').value = (row?.aliases || []).join(', ');
    $('#artist-editor-keyword').value = row?.measurement_keyword || '';
    $('#artist-editor-evidence').value = row?.identity_evidence?.url || '';
    $('#artist-editor-name').disabled = false; $('#artist-editor-keyword').disabled = edit; $('#artist-editor-evidence').disabled = false;
    genreOptions($('#artist-editor-genre'), row?.primary_genre);
    $('#artist-editor').showModal(); $('#artist-editor-genre').focus();
  }
  function openReassignDialog() {
    const row = selectedArtist(); if (!row) return;
    $('#reassign-slug').value = row.slug; genreOptions($('#reassign-genre'), row.primary_genre);
    $('#reassign-dialog').showModal(); $('#reassign-genre').focus();
  }

  function openKeywordDialog() {
    const row = selectedArtist(); if (!row) return;
    $('#keyword-slug').value = row.slug; $('#keyword-action').value = 'add';
    $('#keyword-value').value = ''; $('#keyword-previous').value = row.measurement_keyword || '';
    $('#keyword-dialog').showModal(); $('#keyword-value').focus();
  }

  function openStatusDialog(action) {
    const row = selectedArtist(); if (!row) return;
    const active = action !== 'deactivate';
    $('#status-slug').value = row.slug; $('#status-value').value = String(active);
    $('#status-title').textContent = active ? 'Restore artist' : 'Remove artist';
    $('#status-message').textContent = active ? `Restore ${row.name} to the active artist list.` : `Remove ${row.name} from the active artist list? This does not delete history. You can restore the artist from Archived.`;
    $('#status-form button[type="submit"]').textContent = active ? 'Restore artist' : 'Remove artist';
    $('#status-dialog').showModal();
  }
  function updateSourceMeta(data) {
    $('#network-badge').textContent = data.network === 'GOOGLE_SEARCH_AND_PARTNERS' ? 'Google Search + Search partners (not YouTube views)' : data.network || 'Network unknown';
    $('#generated-at').textContent = latestGoogleFetch(data) || 'No Google fetch timestamp recorded';
    $('#caveat').textContent = [data.caveat, data.network_note].filter(Boolean).join(' ') || $('#caveat').textContent;
  }
  function latestGoogleFetch(data) {
    const timestamps = [data?.data_updated_at];
    (data?.artists || []).forEach(artist => Object.values(artist.geos || {}).forEach(geo => {
      timestamps.push(geo?.last_updated, geo?.fetched_at);
    }));
    return timestamps.filter(value => typeof value === 'string' && value).sort().at(-1) || null;
  }
  function payloadIncludesRefresh(data, startedAt) {
    const fetched = Date.parse(latestGoogleFetch(data) || '');
    const started = Date.parse(startedAt || '');
    return Number.isFinite(fetched) && Number.isFinite(started) && fetched >= started;
  }
  async function refreshDashboardData(updatedArtist = null) {
    try {
      let response = await fetch('/api/dashboard', { cache: 'no-store' });
      if (!response.ok) response = await fetch('dashboard-data.json', { cache: 'no-store' });
      if (!response.ok) throw new Error(`Dashboard data reload failed: ${response.status}`);
      const data = await response.json();
      const selectedMonth = state.month;
      state.data = data;
      state.month = data.months?.includes(selectedMonth) ? selectedMonth : data.default_month || selectedMonth;
      await loadFavorites();
      if (updatedArtist) mergeArtist({ artist: updatedArtist }, false);
      updateSourceMeta(data);
      renderControls(); renderAll();
      return data;
    } catch (error) { showRefreshStatus(`Current dashboard payload could not be reloaded: ${error.message}`, 'error'); return null; }
  }
  const pause = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds));
  function setRefreshControl(running) {
    $('#refresh-data').disabled = running;
    $('#refresh-data').textContent = running ? 'Refreshing…' : 'Refresh data';
  }
  async function pollRefresh(jobId) {
    let failures = 0;
    let deploymentWaitStarted = 0;
    while (state.refreshJobId === jobId) {
      try {
        const job = await apiRequest(`/api/refresh/${encodeURIComponent(jobId)}`, { method: 'GET' });
        failures = 0;
        const stateName = job.state || 'running';
        const progress = job.progress == null ? '' : ` ${job.progress}%`;
        const failed = stateName === 'failed' || stateName === 'error';
        const unobservable = stateName === 'monitoring_unavailable';
        const startedAt = Date.parse(job.started_at || state.refreshStartedAt || '') || Date.now();
        state.refreshStartedAt = new Date(startedAt).toISOString();
        if (stateName === 'success') {
          const payload = await refreshDashboardData();
          if (payloadIncludesRefresh(payload, job.started_at)) {
            sessionStorage.removeItem(refreshStorageKey);
            state.refreshJobId = null;
            showRefreshStatus(job.message || 'Search measurements refreshed and are now displayed.', 'success');
            return;
          }
          deploymentWaitStarted ||= Date.now();
          if (Date.now() - deploymentWaitStarted >= 120_000) {
            sessionStorage.removeItem(refreshStorageKey);
            state.refreshJobId = null;
            showRefreshStatus('Search measurements were saved, but this hosted dashboard still has the previous Google fetch. Deployment is pending; reload after Vercel publishes the new bundle.', 'neutral');
            return;
          }
          showRefreshStatus('Search measurements were saved. Waiting for the hosted deployment to publish the newer Google fetch…', 'running');
        } else {
          showRefreshStatus(`${title(stateName)}${progress}${job.message ? ` — ${job.message}` : ''}`, failed ? 'error' : unobservable ? 'neutral' : 'running');
        }
        if (!failed && !unobservable && stateName !== 'success' && Date.now() - startedAt >= refreshPollingTimeout) {
          sessionStorage.removeItem(refreshStorageKey);
          state.refreshJobId = null;
          state.refreshStartedAt = null;
          showRefreshStatus('Refresh monitoring timed out after 40 minutes. The workflow may still be running; check GitHub Actions before starting another refresh.', 'neutral', job.actions_url);
          return;
        }
        if (failed || unobservable) {
          sessionStorage.removeItem(refreshStorageKey);
          state.refreshJobId = null;
          state.refreshStartedAt = null;
          return;
        }
      } catch (error) {
        failures += 1;
        if (failures >= 3) {
          showRefreshStatus(`Refresh status could not be read after three attempts: ${error.message}`, 'error');
          return;
        }
        showRefreshStatus(`Refresh status check failed; retrying (${failures}/3)…`, 'running');
      }
      await pause(10_000);
    }
  }
  async function startRefresh() {
    if (state.refreshPromise) return state.refreshPromise;
    if (state.refreshJobId) {
      setRefreshControl(true);
      state.refreshPromise = pollRefresh(state.refreshJobId).finally(() => { state.refreshPromise = null; setRefreshControl(false); });
      return state.refreshPromise;
    }
    setRefreshControl(true);
    showRefreshStatus('Refresh queued. The retained dashboard remains visible while data updates.', 'running');
    state.refreshPromise = (async () => {
      try {
      const job = await apiRequest('/api/refresh', { method: 'POST', body: '{}' });
      state.refreshJobId = job.job_id;
      state.refreshStartedAt = new Date().toISOString();
      if (!job.job_id) throw new Error('Refresh response did not include a job_id.');
      sessionStorage.setItem(refreshStorageKey, JSON.stringify({ jobId: job.job_id, startedAt: state.refreshStartedAt }));
      await pollRefresh(job.job_id);
      } catch (error) { showRefreshStatus(`Refresh could not start: ${error.message}`, 'error'); }
      finally { state.refreshPromise = null; setRefreshControl(false); }
    })();
    return state.refreshPromise;
  }

  async function loadDashboardStatus() {
    const savedRefresh = sessionStorage.getItem(refreshStorageKey);
    if (savedRefresh) {
      let savedJobId = savedRefresh;
      try {
        const parsed = JSON.parse(savedRefresh);
        savedJobId = parsed.jobId || savedRefresh;
        state.refreshStartedAt = parsed.startedAt || null;
      } catch { state.refreshStartedAt = null; }
      state.refreshJobId = savedJobId;
      setRefreshControl(true);
      state.refreshPromise = pollRefresh(savedJobId).finally(() => { state.refreshPromise = null; setRefreshControl(false); });
      return;
    }
    try {
      const result = await apiRequest('/api/dashboard/status', { method: 'GET' });
      const job = result?.refresh || result;
      const failed = job?.state === 'failed' || job?.state === 'error';
      if (job?.state && job.state !== 'idle') showRefreshStatus(`Server refresh state: ${title(job.state)}${job.message ? ` — ${job.message}` : ''}`, failed ? 'error' : job.state === 'success' ? 'success' : 'neutral');
    } catch { /* Static dashboard mode has no API status endpoint. */ }
  }
  function renderAll() { renderEligibilityControl(); renderCategoryControls(); renderOverview(); renderComparison(); renderGenres(); renderRisers(); renderArtists(); renderCandidates(); renderHealth(); }

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
    $('#eligibility-filter').addEventListener('change', () => { state.page = 1; renderAll(); });
    $('#category-nav').addEventListener('click', event => {
      const button = event.target.closest('[data-genre]'); if (!button) return;
      $('#genre-filter').value = button.dataset.genre; state.page = 1; renderAll(); $('#artists').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    $('#favorites-nav').addEventListener('click', () => {
      state.favoritesOnly = !state.favoritesOnly; state.page = 1; renderAll(); $('#artists').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    $('#review-nav').addEventListener('click', () => {
      state.favoritesOnly = false; $('#status-filter').value = 'candidate'; $('#eligibility-filter').value = 'all'; state.page = 1; renderAll(); $('#artists').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    $('#archive-nav').addEventListener('click', () => {
      state.favoritesOnly = false; $('#status-filter').value = 'inactive'; $('#eligibility-filter').value = 'all'; state.page = 1; renderAll(); $('#artists').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    $$('.sort').forEach(button => button.addEventListener('click', () => {
      const next = button.dataset.sort; state.direction = state.sort === next ? -state.direction : (next === 'name' ? 1 : -1); state.sort = next; resetAndRender();
    }));
    $('#prev-page').addEventListener('click', () => { state.page -= 1; renderArtists(); });
    $('#next-page').addEventListener('click', () => { state.page += 1; renderArtists(); });
    $('#page-buttons').addEventListener('click', event => { const button = event.target.closest('[data-page]'); if (button) { state.page = Number(button.dataset.page); renderArtists(); } });
    $('#page-size').addEventListener('change', event => { state.pageSize = Number(event.target.value); state.page = 1; renderArtists(); });
    document.addEventListener('click', event => {
      const favorite = event.target.closest('[data-favorite]');
      if (favorite) { event.preventDefault(); toggleFavorite(favorite.dataset.favorite, favorite); return; }
      const rowStatus = event.target.closest('[data-row-status]');
      if (rowStatus) { event.preventDefault(); updateArtistStatus(rowStatus.dataset.rowStatus, rowStatus.dataset.active === 'true', rowStatus); return; }
      const opener = event.target.closest('[data-open]'); if (opener) openDetail(opener.dataset.open, opener);
    });
    $('#artist-body').addEventListener('keydown', event => {
      const row = event.target.closest('[data-open]');
      if (row && !event.target.closest('[data-favorite], [data-row-status]') && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openDetail(row.dataset.open, row); }
    });
    $('#refresh-data').addEventListener('click', startRefresh);
    $('#add-artist').addEventListener('click', () => openArtistEditor());
    $('#detail-content').addEventListener('click', event => {
      const action = event.target.closest('[data-artist-action]')?.dataset.artistAction;
      if (!action) return;
      if (action === 'edit') openArtistEditor(state.selectedSlug);
      if (action === 'reassign') openReassignDialog();
      if (action === 'keyword') openKeywordDialog();
      if (action === 'deactivate' || action === 'reactivate') openStatusDialog(action);
    });
    $$('[data-close-dialog]').forEach(button => button.addEventListener('click', () => $(`#${button.dataset.closeDialog}`).close()));
    $('#artist-editor-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      if (form.dataset.pending) return;
      setFormBusy(form, true);
      const slug = $('#artist-editor-slug').value;
      const aliases = $('#artist-editor-aliases').value.split(',').map(value => value.trim()).filter(Boolean);
      const payload = slug ? { name: $('#artist-editor-name').value.trim(), aliases, category: $('#artist-editor-genre').value, evidence_url: $('#artist-editor-evidence').value.trim() || null } : { name: $('#artist-editor-name').value.trim(), category: $('#artist-editor-genre').value, measurement_keyword: $('#artist-editor-keyword').value.trim() || null, evidence_url: $('#artist-editor-evidence').value.trim() || null };
      try {
        const result = await apiRequest(slug ? `/api/artists/${encodeURIComponent(slug)}` : '/api/artists', { method: slug ? 'PATCH' : 'POST', body: JSON.stringify(payload) });
        const updated = mergeArtist(result); await refreshDashboardData(updated); $('#artist-editor').close();
        if (!slug && updated?.slug) {
          state.favoritesOnly = false; $('#status-filter').value = 'candidate'; $('#eligibility-filter').value = 'all'; state.page = 1; renderAll(); openDetail(updated.slug);
          showRefreshStatus('Artist added to the Review backlog. Confirm identity and eligibility before it enters the shortlist.', 'success');
        } else showRefreshStatus('Artist updated successfully.', 'success');
      } catch (error) { showRefreshStatus(`Artist could not be saved: ${error.message}`, 'error'); }
      finally { setFormBusy(form, false); }
    });
    $('#reassign-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      if (form.dataset.pending) return;
      setFormBusy(form, true);
      const slug = $('#reassign-slug').value;
      try {
        const result = await apiRequest(`/api/artists/${encodeURIComponent(slug)}/category`, { method: 'POST', body: JSON.stringify({ category: $('#reassign-genre').value }) });
        const updated = mergeArtist(result); await refreshDashboardData(updated); $('#reassign-dialog').close(); showRefreshStatus('Artist category reassigned successfully.', 'success');
      } catch (error) { showRefreshStatus(`Category could not be reassigned: ${error.message}`, 'error'); }
      finally { setFormBusy(form, false); }
    });
    $('#keyword-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      if (form.dataset.pending) return;
      setFormBusy(form, true);
      const slug = $('#keyword-slug').value;
      const payload = { keyword: $('#keyword-value').value.trim(), action: $('#keyword-action').value };
      if ($('#keyword-previous').value.trim()) payload.previous_keyword = $('#keyword-previous').value.trim();
      try {
        const result = await apiRequest(`/api/artists/${encodeURIComponent(slug)}/keywords`, { method: 'POST', body: JSON.stringify(payload) });
        const updated = mergeArtist(result); await refreshDashboardData(updated); $('#keyword-dialog').close(); showRefreshStatus('Keyword update recorded successfully.', 'success');
      } catch (error) { showRefreshStatus(`Keyword could not be saved: ${error.message}`, 'error'); }
      finally { setFormBusy(form, false); }
    });
    $('#status-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      if (form.dataset.pending) return;
      setFormBusy(form, true);
      const slug = $('#status-slug').value;
      try {
        const result = await apiRequest(`/api/artists/${encodeURIComponent(slug)}/status`, { method: 'POST', body: JSON.stringify({ active: $('#status-value').value === 'true' }) });
        const updated = mergeArtist(result); await refreshDashboardData(updated); $('#status-dialog').close();
        if ($('#artist-detail').open) $('#artist-detail').close();
        showRefreshStatus(updated?.status === 'inactive' ? 'Artist removed from the active list. Restore it anytime from Archived.' : 'Artist restored to the active list.', 'success');
      } catch (error) { showRefreshStatus(`Artist status could not be updated: ${error.message}`, 'error'); }
      finally { setFormBusy(form, false); }
    });    $('#close-detail').addEventListener('click', () => $('#artist-detail').close());
    $('#artist-detail').addEventListener('close', () => { if (state.lastTrigger?.focus) state.lastTrigger.focus(); });
    window.addEventListener('resize', () => drawCharts());
  }

  async function render(data) {
    state.data = data; state.month = data.default_month || data.months?.[0] || null;
    if (!state.month) throw new Error('No monthly search values were supplied.');
    updateSourceMeta(data);
    await loadFavorites();
    renderControls(); bind(); renderAll(); loadDashboardStatus(); $('#loading').classList.add('done');
  }

  fetch('dashboard-data.json', { cache: 'no-store' })
    .then(response => response.ok ? response.json() : Promise.reject(new Error(`Data load failed: ${response.status}`)))
    .then(render)
    .catch(error => {
      const loading = $('#loading'); loading.classList.add('error'); loading.innerHTML = `<span>Dashboard could not load: ${escapeHtml(error.message)}. Use the local launcher rather than opening the HTML file directly.</span>`;
    });
})();
