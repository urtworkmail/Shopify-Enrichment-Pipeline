
const PAGE_SIZE = 500;
let currentRunId = null;
let currentProductStatus = 'all';
let currentPage = 0;
let totalProducts = 0;
let autoRefreshInterval = null;
let currentSearch = '';

function badgeHtml(s) {
  const m = {success:'badge-success',failed:'badge-failed',pending:'badge-pending',running:'badge-running'};
  return `<span class="${m[s]||'badge-pending'}">${s}</span>`;
}
function tierHtml(t) { if(!t) return ''; return `<span class="tier-${t.replace('T','')}">${t}</span>`; }
async function fetchJson(url, opts={}) { const r = await fetch(url, opts); return r.json(); }

async function loadRuns() {
  try {
    const runs = await fetchJson('/api/runs');
    const sel = document.getElementById('run-select');
    if (!runs || runs.length === 0) { sel.innerHTML = '<option>No runs</option>'; return; }
    const selectedId = currentRunId || runs[0].id;
    sel.innerHTML = runs.map(r =>
      `<option value="${r.id}" ${r.id == selectedId ? 'selected' : ''}>Run ${r.id} — ${r.status} (${r.total_products} products)</option>`
    ).join('');
    if (!currentRunId) { currentRunId = runs[0].id; sel.value = currentRunId; }
  } catch(e) { console.error(e); }
}

async function loadRunStats() {
  if (!currentRunId) return;
  try {
    const r = await fetchJson(`/api/runs/${currentRunId}`);
    document.getElementById('stat-progress').textContent = (r.progress_pct||0)+'%';
    document.getElementById('progress-fill').style.width = (r.progress_pct||0)+'%';
    document.getElementById('stat-counts').textContent = `${(r.enriched_count||0)+(r.failed_count||0)} / ${r.total_products||0}`;
    document.getElementById('stat-success').textContent = r.enriched_count||0;
    document.getElementById('stat-failed').textContent = r.failed_count||0;
    document.getElementById('stat-cost').textContent = '$'+(r.estimated_cost_usd||0).toFixed(2);
    document.getElementById('stat-tokens').textContent = `${(r.total_input_tokens||0).toLocaleString()} in / ${(r.total_output_tokens||0).toLocaleString()} out`;
    document.getElementById('stat-writeback').textContent = r.writeback_status||'pending';
    const badge = document.getElementById('run-status-badge');
    badge.textContent = r.status||'?'; badge.className = 'badge';
  } catch(e) { console.error(e); }
}

async function loadProducts() {
  if (!currentRunId) return;
  try {
    const statusParam = currentProductStatus === 'all' ? '' : `&status=${currentProductStatus}`;
    const limit = currentSearch ? 10000 : PAGE_SIZE;
    const offset = currentSearch ? 0 : currentPage * PAGE_SIZE;
    const data = await fetchJson(`/api/runs/${currentRunId}/products?limit=${limit}&offset=${offset}${statusParam}`);

    let items = data.items || [];
    if (currentSearch) {
      const s = currentSearch.toLowerCase();
      items = items.filter(p => p.sku.toLowerCase().includes(s));
    }

    totalProducts = items.length;
    const tbody = document.getElementById('products-tbody');
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="11" class="empty-state">${currentSearch ? 'No matching SKUs' : 'No products'}</td></tr>`;
      document.getElementById('pagination').innerHTML = '';
      return;
    }

    const pageStart = currentPage * PAGE_SIZE;
    const pageItems = items.slice(pageStart, pageStart + PAGE_SIZE);

    tbody.innerHTML = pageItems.map(p => `
      <tr onclick="openPanel('${p.sku}')">
        <td style="font-family:monospace;font-size:12px;">${p.sku}</td>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${p.title||''}">${p.title||''}</td>
        <td>${tierHtml(p.tier)}</td><td>${badgeHtml(p.status)}</td>
        <td style="font-size:11px;color:#888;">${p.scrape_status||''}</td>
        <td>${badgeHtml(p.writeback_status)}</td>
        <td style="font-size:12px;color:#888;">${(p.input_tokens||0).toLocaleString()}</td>
        <td style="font-size:12px;color:#888;">${(p.output_tokens||0).toLocaleString()}</td>
        <td style="font-size:12px;color:#ccc;">$${(p.cost_usd||0).toFixed(4)}</td>
        <td style="font-size:12px;color:#888;">${p.retry_count||0}</td>
        <td onclick="event.stopPropagation()">
          <button class="action-btn scrape" onclick="rescrape('${p.sku}')">&#128269;</button>
          <button class="action-btn enrich" onclick="reenrich('${p.sku}')">&#128260;</button>
          <button class="action-btn writeback" onclick="writeback('${p.sku}')">&#128228;</button>
        </td>
      </tr>`).join('');
    renderPagination();
  } catch(e) {
    document.getElementById('products-tbody').innerHTML = '<tr><td colspan="11" class="empty-state">Error loading products</td></tr>';
  }
}

function renderPagination() {
  const totalPages = Math.ceil(totalProducts/PAGE_SIZE);
  const pag = document.getElementById('pagination');
  if (totalPages<=1) { pag.innerHTML = ''; return; }
  pag.innerHTML = `
    <button onclick="goToPage(${currentPage-1})" ${currentPage===0?'disabled':''}>&laquo; Prev</button>
    <span class="page-info">Page ${currentPage+1} of ${totalPages} (${totalProducts.toLocaleString()} products)</span>
    <button onclick="goToPage(${currentPage+1})" ${currentPage>=totalPages-1?'disabled':''}>Next &raquo;</button>`;
}

function goToPage(p) {
  if (p<0||p>=Math.ceil(totalProducts/PAGE_SIZE)) return;
  currentPage = p;
  loadProducts();
}

async function loadLogs() {
  if (!currentRunId) return;
  try {
    const data = await fetchJson(`/api/runs/${currentRunId}/logs?limit=200`);
    const c = document.getElementById('logs-container');
    c.innerHTML = data.length ? data.map(l => `
      <div class="log-entry">
        <span class="log-time">${l.timestamp.slice(0,19).replace('T',' ')}</span>
        <span class="log-level-${l.level}">${l.level}</span>
        <span style="color:#555;min-width:120px;">${l.module||''}</span>
        <span style="color:#888;min-width:100px;">${l.sku||''}</span>
        <span>${l.message}</span>
      </div>`).join('') : '<div class="empty-state">No logs</div>';
  } catch(e) { console.error(e); }
}

function switchTab(panel, status) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('products-panel').style.display = panel==='products'?'':'none';
  document.getElementById('logs-panel').style.display = panel==='logs'?'':'none';
  if (panel==='products') { currentProductStatus = status; currentPage = 0; loadProducts(); }
  else loadLogs();
}

function onRunChange() {
  const newId = parseInt(document.getElementById('run-select').value);
  if (newId && newId !== currentRunId) {
    currentRunId = newId;
    currentPage = 0;
    currentSearch = '';
    document.getElementById('sku-search').value = '';
    loadRunStats();
    loadProducts();
  }
}

async function openPanel(sku) {
  document.getElementById('overlay').classList.add('show');
  document.getElementById('side-panel').classList.add('open');
  document.getElementById('panel-title').textContent = sku+' – Details';
  document.getElementById('panel-content').innerHTML = '<p style="color:#888;">Loading...</p>';
  try {
    const data = await fetchJson(`/api/products/${sku}/details`);
    if (data.error) { document.getElementById('panel-content').innerHTML = `<p style="color:#f87171;">${data.error}</p>`; return; }
    const shopify = data.shopify_data||{}, scraped = data.scraped_content||{}, enriched = data.enriched_data||{};
    document.getElementById('panel-status-bar').className = 'panel-status-bar '+(data.enrichment_status==='success'?'success':data.enrichment_status==='failed'?'failed':'pending');
    let enrichedHtml = '';
    if (Array.isArray(enriched.image_alt_texts)) {
      let list = '<ul class="alt-list">';
      enriched.image_alt_texts.forEach((a,i) => list += `<li><span class="alt-num">#${i+1}</span>${a}</li>`);
      list += '</ul>';
      const copy = {...enriched}; delete copy.image_alt_texts;
      enrichedHtml = `<strong style="color:#aaa;">Image Alt Texts</strong>${list}<pre>${JSON.stringify(copy,null,2)}</pre>`;
    } else enrichedHtml = `<pre>${JSON.stringify(enriched,null,2)}</pre>`;
    document.getElementById('panel-content').innerHTML = `
      <div style="margin-bottom:10px;"><span class="${data.enrichment_status==='success'?'badge-success':'badge-failed'}">${data.enrichment_status}</span> <span style="font-size:13px;color:#888;">Tier: ${data.tier||'?'} &middot; Cost: $${data.cost_usd.toFixed(4)}</span></div>
      <div class="accordion open"><div class="accordion-header" onclick="this.parentElement.classList.toggle('open')"><span>Shopify Data</span><span>&#9662;</span></div><div class="accordion-body">
        <div class="field-row"><span class="field-label">Title</span><span class="field-value">${shopify.title||''}</span></div>
        <div class="field-row"><span class="field-label">Vendor</span><span class="field-value">${shopify.vendor||''}</span></div>
        <pre>${JSON.stringify(shopify.existing_content||{},null,2)}</pre>
      </div></div>
      <div class="accordion"><div class="accordion-header" onclick="this.parentElement.classList.toggle('open')"><span>Scraped Content</span><span>&#9662;</span></div><div class="accordion-body">
        <pre>${JSON.stringify(scraped,null,2)}</pre>
      </div></div>
      <div class="accordion"><div class="accordion-header" onclick="this.parentElement.classList.toggle('open')"><span>Enriched Content</span><span>&#9662;</span></div><div class="accordion-body">${enrichedHtml}</div></div>
      <div class="accordion"><div class="accordion-header" onclick="this.parentElement.classList.toggle('open')"><span>Writeback</span><span>&#9662;</span></div><div class="accordion-body">
        <div class="field-row"><span class="field-label">Status</span><span class="field-value">${data.writeback.status||'pending'}</span></div>
        ${data.writeback.error?`<div class="field-row"><span class="field-label">Error</span><span class="field-value" style="color:#f87171;">${data.writeback.error}</span></div>`:''}
      </div></div>`;
  } catch(e) { document.getElementById('panel-content').innerHTML = `<p style="color:#f87171;">Failed: ${e.message}</p>`; }
}

function closePanel() { document.getElementById('overlay').classList.remove('show'); document.getElementById('side-panel').classList.remove('open'); }

async function rescrape(sku) {
  const u = prompt('Custom URL (blank for auto-search):','');
  const body = u ? JSON.stringify({custom_url:u}) : '{}';
  try {
    const d = await fetchJson(`/api/products/${sku}/scrape`, {method:'POST', headers:{'Content-Type':'application/json'}, body});
    alert(`Scrape: ${d.scrape_status||d.error}`);
  } catch(e) { alert('Scrape failed: '+e.message); }
  loadProducts();
}

async function reenrich(sku) {
  try {
    const d = await fetchJson(`/api/products/${sku}/enrich`, {method:'POST'});
    alert(`Enrichment: ${d.status}\n${d.error||''}`);
  } catch(e) { alert('Enrichment failed: '+e.message); }
  loadProducts();
}

async function writeback(sku) {
  if (!confirm(`Write back ${sku}?`)) return;
  try {
    const d = await fetchJson(`/api/products/${sku}/writeback`, {method:'POST'});
    alert(d.status==='success'?`Writeback: success\n${d.passes_completed.join(', ')}`:`Writeback: failed\n${(d.errors||[]).join('\n')}`);
  } catch(e) { alert('Writeback failed: '+e.message); }
  loadProducts();
}

function filterBySku() {
  const search = document.getElementById('sku-search').value.trim();
  currentSearch = search;
  currentPage = 0;
  loadProducts();
}

function clearSkuFilter() {
  document.getElementById('sku-search').value = '';
  currentSearch = '';
  currentPage = 0;
  loadProducts();
}

async function loadAll() { await loadRuns(); await loadRunStats(); if (currentRunId) await loadProducts(); }
loadAll();
autoRefreshInterval = setInterval(loadAll, 5000);
