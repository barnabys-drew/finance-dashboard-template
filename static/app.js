let data = null;
let subsData = null;
let activeInsight = null;
let INSIGHT_LABELS = {};
let viewData = null;
let activeCategory = null;
let selectedMonth = null;
let selectedDay = null;
let filterMonth = null;
let catChart = null;
let timeChart = null;
let monthlyChart = null;
let merchantChart = null;
let drilldownChart = null;
let monthDrilldownChart = null;

const fmt = n => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});

// Animate a number element up to `to` (easeOutCubic). Skips re-animating an
// unchanged value, and skips entirely when Conceal is on (so a blurred figure
// never flashes un-wrapped mid-animation). Default formatter = fmt (currency).
function countUp(el, to, format = fmt) {
  if (!el) return;
  to = Number(to) || 0;
  if (typeof CONCEAL_MONEY !== 'undefined' && CONCEAL_MONEY) { el._countTo = to; el.textContent = format(to); return; }
  if (el._countTo === to) { el.textContent = format(to); return; }
  if (el._raf) cancelAnimationFrame(el._raf);
  const from = el._countTo ?? 0;
  el._countTo = to;
  const dur = 650, t0 = performance.now(), ease = p => 1 - Math.pow(1 - p, 3);
  const step = now => {
    const p = Math.min((now - t0) / dur, 1);
    el.textContent = format(from + (to - from) * ease(p));
    if (p < 1) el._raf = requestAnimationFrame(step);
  };
  el._raf = requestAnimationFrame(step);
}

// Polish every chart at once: Inter font, token colors, and a refined tooltip.
if (window.Chart) {
  Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = '#8b949e';
  Chart.defaults.borderColor = '#21262d';
  Chart.defaults.animation = { duration: 600, easing: 'easeOutCubic' };
  Object.assign(Chart.defaults.plugins.tooltip, {
    backgroundColor: '#1c1f26', borderColor: '#2d3139', borderWidth: 1,
    titleColor: '#e1e4e8', bodyColor: '#c9d1d9', padding: 10, cornerRadius: 8,
    displayColors: false, titleFont: { weight: '600' },
  });
}

// Conceal state shared with the chart configs. Charts draw to <canvas>, so we
// can't blur individual labels — instead, when concealed we simply don't print
// the dollar numbers (blank axis ticks, masked tooltips). Bars/shapes stay crisp.
let CONCEAL_MONEY = (localStorage.getItem('concealMoney') ?? '1') === '1';
const axisMoney = v => CONCEAL_MONEY ? '' : '$' + Number(v).toLocaleString();
// Hover tooltips always reveal the real amount — even when Conceal is on. The
// passive display (axis ticks, .money text) stays blurred; a deliberate hover by
// the owner shows the actual number. (Note: anyone hovering can see it.)
const tipMoney  = n => fmt(n);

function getViewData() {
  if (!filterMonth) return data;
  const txns = data.transactions.filter(t => t.date.startsWith(filterMonth));
  const expenses = txns.filter(t => !t.is_income && !t.is_savings);
  const income = txns.filter(t => t.is_income);
  const savings_transfers = txns.filter(t => t.is_savings).reduce((s, t) => s + t.amount, 0);
  const by_category = {};
  expenses.forEach(t => { by_category[t.category] = (by_category[t.category] || 0) + t.amount; });
  const by_date = {};
  expenses.forEach(t => { by_date[t.date] = (by_date[t.date] || 0) + t.amount; });
  const total_expenses = expenses.reduce((s, t) => s + t.amount, 0);
  const total_income = income.reduce((s, t) => s + t.amount, 0);
  // "Saved" is the period cash-flow surplus: income − spend. Transfers to Ally
  // HYSA are internal money moves (often pre-existing cash), NOT income earned and
  // saved this period — counting them here inflated the rate past 100%. They're
  // surfaced separately as context, not folded into the headline.
  const savings_rate = total_income > 0 ? +((total_income - total_expenses) / total_income * 100).toFixed(1) : 0;
  const merchants = {};
  expenses.forEach(t => { merchants[t.description] = (merchants[t.description] || 0) + t.amount; });
  const top_merchants = Object.entries(merchants).sort((a, b) => b[1] - a[1]).slice(0, 15);
  // Single-bucket assignment (most specific wins) so tiles never double-count.
  const _bt = {};
  expenses.forEach(t => { const k = insightBucket(t); if (k) _bt[k] = (_bt[k] || 0) + t.amount; });
  const doordash_total = _bt.food || 0;
  const sub_total = _bt.subs || 0;
  const shopping_total = _bt.shopping || 0;
  const baby_total = _bt.baby || 0;
  const ai_total = _bt.ai || 0;
  const atm_cash = _bt.cash || 0;
  const electricity = _bt.electricity || 0;
  const health_total = _bt.health || 0;
  const pet_total = _bt.pet || 0;
  const household_total = _bt.household || 0;
  const dining_total = _bt.dining || 0;
  const rent_total = _bt.rent || 0;
  const insurance_total = _bt.insurance || 0;
  const transport_total = _bt.transport || 0;
  const entertainment_total = _bt.entertainment || 0;
  const personal_total = _bt.personal || 0;
  const other_total = _bt.other || 0;
  const debit_instore = expenses.filter(t => t.payment_channel === 'in store' && !/(WEB ID|PPD ID|ACH )/i.test(t.description)).reduce((s, t) => s + t.amount, 0);
  return {
    ...data,
    transactions: txns,
    by_category,
    by_date,
    top_merchants,
    insights: { total_expenses, total_income, savings_transfers, savings_rate, doordash_total, sub_total, shopping_total, baby_total, ai_total, atm_cash, electricity, health_total, pet_total, household_total, dining_total, rent_total, insurance_total, transport_total, entertainment_total, personal_total, other_total, debit_instore },
  };
}

// The page is long-lived (start.sh opens it once and the tab stays open for
// days), so data can NOT be fetch-once: refresh.py rewrites the JSON behind
// the APIs while the page sits there. loadData() is re-runnable, and
// refreshIfStale() re-pulls everything when the window regains focus after
// the data has had a chance to change.
let lastDataFetch = 0;
const STALE_MS = 60_000;

async function loadData() {
  const resp = await fetch('/api/data');
  data = await resp.json();
  lastDataFetch = Date.now();
  viewData = data;
  render();
  // Recurring-charge analysis loads independently so the dashboard paints first.
  fetch('/api/subscriptions')
    .then(r => r.json())
    .then(s => { subsData = s; renderInsights(viewData.insights); })
    .catch(() => {});
  // Checking balance badge loads independently — lets you gauge spend against
  // what's actually in the account without opening Chase.
  fetch('/api/networth')
    .then(r => r.json())
    .then(renderCheckingBadge)
    .catch(() => {});
}

async function init() {
  await loadData();
  document.getElementById('tx-search').addEventListener('input', renderTable);
}

// Re-pull when the user comes back to a page whose data may have changed
// underneath it (e.g. a morning refresh.py run while the tab sat open).
// The 60s TTL keeps rapid alt-tabbing free.
function refreshIfStale() {
  if (document.visibilityState !== 'visible') return;
  if (Date.now() - lastDataFetch < STALE_MS) return;
  loadData();
  if (nwData) loadNetWorth();
  if (cfData) loadCashflow();
}
document.addEventListener('visibilitychange', refreshIfStale);
window.addEventListener('focus', refreshIfStale);

function renderCheckingBadge(nw) {
  if (!nw || !Array.isArray(nw.accounts)) return;
  const chk = nw.accounts.find(a => /checking/i.test(a.account_name || ''));
  if (!chk) return;
  document.getElementById('checking-badge-val').textContent =
    (chk.current_value || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' });
  if (chk.snapshot_date)
    document.getElementById('checking-badge-asof').textContent = 'as of ' + chk.snapshot_date;
  document.getElementById('checking-badge').style.display = '';
}

function render() {
  viewData = getViewData();
  const vd = viewData;
  const expenses = vd.transactions.filter(t => !t.is_income && !t.is_savings);
  const ins = vd.insights;

  // Header period label
  if (filterMonth) {
    const [y, mo] = filterMonth.split('-');
    document.getElementById('period-label').textContent =
      new Date(y, mo - 1).toLocaleString('en-US', { month: 'long', year: 'numeric' });
  } else if (expenses.length) {
    const dates = expenses.map(t => t.date).sort();
    document.getElementById('period-label').textContent =
      `${dates[0]} → ${dates[dates.length-1]}  ·  Chase Checking + Sapphire`;
  }

  // Income bar
  if (ins.total_income > 0) {
    document.getElementById('income-bar').style.display = '';
    document.getElementById('income-val').textContent = fmt(ins.total_income);
  }

  // Summary cards — two distinct figures so neither double-counts the other:
  //  • Net cash flow = income − spend (what actually grew/shrank your wealth)
  //  • Into savings  = money moved to Ally HYSA (set aside, but its own number,
  //    not folded into cash flow — a transfer doesn't itself create savings)
  const savingsTransfers = ins.savings_transfers || 0;
  const savings = ins.total_income - ins.total_expenses;
  const uniqueDates = [...new Set(expenses.map(t => t.date))];
  const topCat = Object.entries(vd.by_category).sort((a, b) => b[1] - a[1])[0];

  countUp(document.getElementById('stat-total'), ins.total_expenses);
  const savingsEl = document.getElementById('stat-savings');
  savingsEl.textContent = (savings >= 0 ? '+' : '') + fmt(savings);
  savingsEl.style.color = savings >= 0 ? '#3fb950' : '#f8a5a5';
  if (ins.savings_rate !== undefined && ins.total_income > 0) {
    document.getElementById('stat-savings-label').textContent =
      `Net cash flow — ${ins.savings_rate >= 0 ? '+' : ''}${ins.savings_rate}% of income`;
  }
  const intoEl = document.getElementById('stat-savings-into');
  const totalEl = document.getElementById('stat-savings-total');
  if (savingsTransfers > 0) {
    document.getElementById('stat-savings-into-val').textContent = '+' + fmt(savingsTransfers);
    intoEl.style.display = '';
    // Third value: overall position change = net cash flow + money set aside.
    const total = savings + savingsTransfers;
    const totalValEl = document.getElementById('stat-savings-total-val');
    totalValEl.textContent = (total >= 0 ? '+' : '') + fmt(total);
    totalValEl.style.color = total >= 0 ? '#3fb950' : '#f8a5a5';
    totalEl.style.display = '';
  } else {
    intoEl.style.display = 'none';
    totalEl.style.display = 'none';
  }
  document.getElementById('stat-top-cat').textContent = topCat ? topCat[0] : '—';
  document.getElementById('stat-avg-day').textContent =
    uniqueDates.length ? fmt(ins.total_expenses / uniqueDates.length) : '—';

  renderMonthPicker();
  renderCategoryChart(vd);
  renderTimeChart(vd);
  renderMonthlyChart();
  renderMerchantChart(vd);
  renderInsights(ins);
  renderBudgetSummary();  // after renderInsights — needs INSIGHT_LABELS
  renderFilters(expenses);
  renderTable();
}

function renderMonthPicker() {
  // Show every month that has data (no gating). Partial edge months are marked
  // with a "·" and a tooltip so it's clear they're not a full month to compare —
  // but they're still selectable and their totals still count everywhere else.
  const bounds = dataDateBounds();
  const months = Object.keys(data.by_month).sort();

  // Drop a filter pointing at a month that no longer has data (e.g. it rolled off
  // after a refresh) so the view doesn't get stuck on an empty month.
  if (filterMonth && !months.includes(filterMonth)) filterMonth = null;

  const el = document.getElementById('month-picker');
  el.innerHTML = `
    <button class="month-btn ${!filterMonth ? 'active' : ''}" onclick="setMonthFilter(null)">All time</button>
    ${months.map(m => {
      const [y, mo] = m.split('-');
      const partial = !isMonthComplete(m, bounds);
      const label = new Date(y, mo - 1).toLocaleString('en-US', { month: 'short', year: '2-digit' });
      const title = partial ? ' title="Partial month — not a full month to compare"' : '';
      return `<button class="month-btn ${filterMonth === m ? 'active' : ''}"${title} onclick="setMonthFilter('${m}')">${label}${partial ? ' ·' : ''}</button>`;
    }).join('')}
  `;
}

// Budget summary strip: total budgeted vs spent for budgetMonth(), plus one
// chip per budgeted bucket colored by pace. Hidden entirely when config.json
// has no budgets. Chips reuse the insight accordion for drill-down.
function renderBudgetSummary() {
  const panel = document.getElementById('budget-summary');
  const budgets = data.budgets || {};
  const bm = budgetMonth();
  const keys = Object.keys(budgets);
  if (!keys.length || !bm) { panel.style.display = 'none'; return; }

  const bounds = dataDateBounds();
  const { covered, daysInMonth } = monthCoverage(bm, bounds);
  const { months: sparkMonths, series: sparks } = bucketMonthlySeries();
  const mi = sparkMonths.indexOf(bm);

  const rows = keys.map(k => {
    const spent = mi >= 0 ? ((sparks[k] || [])[mi] || 0) : 0;
    const s = budgetStatus(spent, budgets[k], covered, daysInMonth);
    return { key: k, label: INSIGHT_LABELS[k] || k, spent, budget: budgets[k], ...s };
  }).sort((a, b) => b.pct - a.pct);

  const totBudget = rows.reduce((s, r) => s + r.budget, 0);
  const totSpent = rows.reduce((s, r) => s + r.spent, 0);
  const overCount = rows.filter(r => r.status === 'red').length;
  const [y, mo] = bm.split('-');
  const mLabel = new Date(y, mo - 1).toLocaleString('en-US', { month: 'long', year: 'numeric' });
  const totPct = Math.min(100, totSpent / totBudget * 100);

  panel.style.display = '';
  panel.innerHTML = `
    <div class="flex justify-between items-center flex-wrap gap-2 mb-2">
      <div class="section-title mb-0">Budgets — ${mLabel}</div>
      <div style="font-size:0.75rem;color:${overCount ? '#f8a5a5' : '#3fb950'}">
        ${overCount ? `${overCount} of ${rows.length} bucket${rows.length === 1 ? '' : 's'} over pace` : 'All buckets on track'}
      </div>
    </div>
    <div style="height:6px;border-radius:3px;background:#21262d;overflow:hidden;margin-bottom:6px">
      <div style="height:100%;width:${totPct.toFixed(1)}%;background:${totSpent > totBudget ? '#f8a5a5' : '#3fb950'};border-radius:3px"></div>
    </div>
    <div style="font-size:0.75rem;color:#8b949e;margin-bottom:10px">${fmt(totSpent)} of ${fmt(totBudget)} budgeted</div>
    <div style="display:flex;flex-wrap:wrap;gap:6px">
      ${rows.map(r => `
        <button onclick="toggleInsight('${r.key}')" title="${fmt(r.spent)} of ${fmt(r.budget)}"
          style="background:none;border:1px solid ${BUDGET_COLORS[r.status]};color:${BUDGET_COLORS[r.status]};
                 border-radius:12px;padding:2px 10px;font-size:0.72rem;cursor:pointer">
          ${r.label} · ${Math.round(r.spent / r.budget * 100)}%
        </button>`).join('')}
    </div>`;
}

function setMonthFilter(m) {
  filterMonth = m;
  activeCategory = null;
  selectedDay = null;
  if (catChart) { catChart.destroy(); catChart = null; }
  if (timeChart) { timeChart.destroy(); timeChart = null; }
  if (merchantChart) { merchantChart.destroy(); merchantChart = null; }
  if (drilldownChart) { drilldownChart.destroy(); drilldownChart = null; }
  document.getElementById('drilldown-panel').style.display = 'none';
  document.getElementById('day-drilldown-panel').style.display = 'none';
  render();
}

function renderCategoryChart(vd) {
  const labels = Object.keys(vd.by_category);
  const values = Object.values(vd.by_category);
  const colors = labels.map(c => data.colors[c] || '#95A5A6');

  if (catChart) catChart.destroy();
  catChart = new Chart(document.getElementById('cat-chart'), {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors, borderWidth: 2, borderColor: '#1c1f26', hoverBorderColor: '#fff' }]
    },
    options: {
      cutout: '62%',
      plugins: {
        legend: { position: 'bottom', labels: { color: '#c9d1d9', font: { size: 10.5 }, boxWidth: 11, padding: 10 } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${tipMoney(ctx.raw)} (${((ctx.raw / vd.insights.total_expenses) * 100).toFixed(1)}%)` } }
      },
      onClick: (e, els) => {
        if (!els.length) { activeCategory = null; }
        else {
          const cat = Object.keys(vd.by_category)[els[0].index];
          activeCategory = activeCategory === cat ? null : cat;
        }
        updateFilterBtns();
        renderTable();
        renderCategoryDrilldown();
      }
    }
  });
}

function renderTimeChart(vd) {
  const dates = Object.keys(vd.by_date).sort();
  const values = dates.map(d => Math.round((vd.by_date[d] || 0) * 100) / 100);

  const baseColor = (v, date) => {
    if (date === selectedDay) return '#fff';
    if (v > 500) return '#FF6B6B';
    if (v > 200) return '#FF9F43';
    return '#58a6ff99';
  };

  if (timeChart) timeChart.destroy();
  timeChart = new Chart(document.getElementById('time-chart'), {
    type: 'bar',
    data: {
      labels: dates.map(d => d.slice(5)),
      datasets: [{
        data: values,
        backgroundColor: dates.map((d, i) => baseColor(values[i], d)),
        borderRadius: 3,
        borderSkipped: false,
      }]
    },
    options: {
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { title: ctx => ctx[0].label, label: ctx => ` ${tipMoney(ctx.raw)}` } }
      },
      scales: {
        x: { ticks: { color: '#6e7681', font: { size: 10 }, maxRotation: 45 }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', callback: axisMoney }, grid: { color: '#21262d' } }
      },
      onClick: (e, els) => {
        if (!els.length) return;
        const clicked = dates[els[0].index];
        selectedDay = selectedDay === clicked ? null : clicked;
        // Re-highlight by updating colors in place — destroying/recreating the
        // chart from inside its own onClick blanks the canvas.
        timeChart.data.datasets[0].backgroundColor =
          dates.map((d, i) => baseColor(values[i], d));
        timeChart.update('none');
        renderDayDrilldown(vd);
      }
    }
  });
}

function renderDayDrilldown(vd) {
  const panel = document.getElementById('day-drilldown-panel');
  if (!selectedDay) { panel.style.display = 'none'; return; }

  const dayTxns = vd.transactions
    .filter(t => t.date === selectedDay)
    .sort((a, b) => b.amount - a.amount);

  if (!dayTxns.length) { panel.style.display = 'none'; return; }

  panel.style.display = '';
  const [, mo, dy] = selectedDay.split('-');
  const label = new Date(selectedDay + 'T12:00:00').toLocaleString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
  const total = dayTxns.filter(t => !t.is_income && !t.is_savings).reduce((s, t) => s + t.amount, 0);
  document.getElementById('day-drilldown-title').textContent = `${label}  ·  ${fmt(total)} spent`;

  const rows = dayTxns.map(t => {
    const inflow = t.is_income || t.is_savings;
    const color = inflow ? (t.is_savings ? '#00B894' : '#3fb950') : (data.colors[t.category] || '#95A5A6');
    const sign = inflow ? '+' : '-';
    const amtClass = inflow ? 'amount-inc' : 'amount-exp';
    return `
      <div class="flex justify-between items-center py-2" style="border-bottom:1px solid #21262d">
        <div class="flex items-center gap-4" style="min-width:0">
          <span class="cat-badge flex-shrink-0" style="background:${color}22;color:${color}">${t.category}</span>
          <span style="font-size:0.85rem;color:#e1e4e8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${t.description}">${t.description}</span>
          <span style="font-size:0.72rem;color:#6e7681;flex-shrink:0">${t.account}</span>
        </div>
        <span class="${amtClass}" style="font-size:0.9rem;font-weight:600;flex-shrink:0;margin-left:16px">${sign}${fmt(t.amount)}</span>
      </div>
    `;
  });

  document.getElementById('day-drilldown-body').innerHTML = rows.join('');
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function clearDayDrilldown() {
  selectedDay = null;
  if (viewData) renderTimeChart(viewData);
  document.getElementById('day-drilldown-panel').style.display = 'none';
}

function renderMonthlyChart() {
  const months = Object.keys(data.by_month).sort();
  const values = months.map(m => Math.round(data.by_month[m] * 100) / 100);
  const incomeVals = months.map(m => Math.round((data.income_by_month[m] || 0) * 100) / 100);
  // Partial edge months are still plotted (the data is real) but drawn faded so
  // they don't read as a real dip when eyeballing the month-to-month trend.
  const bounds = dataDateBounds();
  const partial = months.map(m => !isMonthComplete(m, bounds));
  const spendColor = (m, i) => (m === selectedMonth || m === filterMonth) ? '#FF6B6B' : (partial[i] ? '#FF6B6B22' : '#FF6B6B66');
  const incomeColor = (m, i) => partial[i] ? '#3fb95022' : '#3fb95066';

  if (monthlyChart) monthlyChart.destroy();
  monthlyChart = new Chart(document.getElementById('monthly-chart'), {
    type: 'bar',
    data: {
      labels: months.map(m => {
        const [y, mo] = m.split('-');
        return new Date(y, mo - 1).toLocaleString('en-US', { month: 'short', year: '2-digit' });
      }),
      datasets: [
        { label: 'Spending', data: values, backgroundColor: months.map(spendColor), borderRadius: 4, order: 1 },
        { label: 'Income', data: incomeVals, backgroundColor: months.map(incomeColor), borderRadius: 4, order: 2 },
      ]
    },
    options: {
      plugins: {
        legend: { labels: { color: '#c9d1d9', font: { size: 10.5 }, boxWidth: 11 } },
        tooltip: { callbacks: {
          label: ctx => ` ${ctx.dataset.label}: ${tipMoney(ctx.raw)}`,
          afterBody: items => partial[items[0].dataIndex] ? ['(partial month — not a full-month comparison)'] : [],
        } }
      },
      scales: {
        x: { ticks: { color: '#6e7681', font: { size: 11 } }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', callback: axisMoney }, grid: { color: '#21262d' } }
      },
      onClick: (e, els) => {
        if (!els.length) return;
        const clicked = months[els[0].index];
        selectedMonth = selectedMonth === clicked ? null : clicked;
        // Re-highlight by updating colors in place — destroying/recreating the
        // chart from inside its own onClick blanks the canvas.
        monthlyChart.data.datasets[0].backgroundColor = months.map(spendColor);
        monthlyChart.update('none');
        renderMonthDrilldown();
      }
    }
  });
}

function renderMonthDrilldown() {
  const panel = document.getElementById('month-drilldown-panel');
  if (!selectedMonth) { panel.style.display = 'none'; return; }

  panel.style.display = '';
  const [y, mo] = selectedMonth.split('-');
  const label = new Date(y, mo - 1).toLocaleString('en-US', { month: 'long', year: 'numeric' });
  document.getElementById('month-drilldown-title').textContent = `${label} — by Category`;

  const entries = Object.entries(data.by_month_category)
    .map(([cat, months]) => [cat, months[selectedMonth] || 0])
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);

  const cats = entries.map(e => e[0]);
  const vals = entries.map(e => Math.round(e[1] * 100) / 100);
  const colors = cats.map(c => (data.colors[c] || '#95A5A6') + 'cc');

  if (monthDrilldownChart) monthDrilldownChart.destroy();
  monthDrilldownChart = new Chart(document.getElementById('month-drilldown-chart'), {
    type: 'bar',
    data: {
      labels: cats,
      datasets: [{ data: vals, backgroundColor: colors, borderRadius: 4 }]
    },
    options: {
      indexAxis: 'y',
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${tipMoney(ctx.raw)} (${((ctx.raw / data.by_month[selectedMonth]) * 100).toFixed(1)}%)` } }
      },
      scales: {
        x: { ticks: { color: '#8b949e', callback: axisMoney }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#c9d1d9', font: { size: 11 } }, grid: { display: false } }
      },
      onClick: (e, els) => {
        if (!els.length) return;
        activeCategory = cats[els[0].index];
        updateFilterBtns();
        renderTable();
        renderCategoryDrilldown();
        document.getElementById('tx-body').scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  });
}

function clearMonthDrilldown() {
  selectedMonth = null;
  renderMonthlyChart();
  renderMonthDrilldown();
}

function renderCategoryDrilldown() {
  const panel = document.getElementById('drilldown-panel');
  if (!activeCategory || !data.by_month_category[activeCategory]) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';
  document.getElementById('drilldown-title').textContent = `${activeCategory} — Monthly Breakdown`;

  const months = Object.keys(data.by_month).sort();
  const catData = data.by_month_category[activeCategory] || {};
  const values = months.map(m => Math.round((catData[m] || 0) * 100) / 100);
  const color = data.colors[activeCategory] || '#58a6ff';

  if (drilldownChart) drilldownChart.destroy();
  drilldownChart = new Chart(document.getElementById('drilldown-chart'), {
    type: 'bar',
    data: {
      labels: months.map(m => {
        const [y, mo] = m.split('-');
        return new Date(y, mo - 1).toLocaleString('en-US', { month: 'short', year: '2-digit' });
      }),
      datasets: [{ data: values, backgroundColor: color + '99', borderColor: color, borderWidth: 1, borderRadius: 4 }]
    },
    options: {
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${tipMoney(ctx.raw)}` } }
      },
      scales: {
        x: { ticks: { color: '#6e7681', font: { size: 11 } }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', callback: axisMoney }, grid: { color: '#21262d' } }
      }
    }
  });
}

function renderMerchantChart(vd) {
  const names = vd.top_merchants.map(m => m[0]);
  const amounts = vd.top_merchants.map(m => m[1]);
  const colors = names.map(n => {
    const expense = vd.transactions.find(t =>
      n === 'DoorDash' ? t.description.toLowerCase().includes('doordash') :
      n === 'Target'   ? t.description.toLowerCase().includes('target') :
      t.description.toLowerCase().startsWith(n.toLowerCase().slice(0, 6))
    );
    return expense ? (data.colors[expense.category] || '#58a6ff') + 'bb' : '#58a6ffbb';
  });

  if (merchantChart) merchantChart.destroy();
  merchantChart = new Chart(document.getElementById('merchant-chart'), {
    type: 'bar',
    data: {
      labels: names,
      datasets: [{ data: amounts, backgroundColor: colors, borderRadius: 4 }]
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ` ${tipMoney(ctx.raw)}` } } },
      scales: {
        x: { ticks: { color: '#8b949e', callback: axisMoney }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#c9d1d9', font: { size: 11 } }, grid: { display: false } }
      }
    }
  });
}

// First / last transaction date in the loaded data (YYYY-MM-DD), used to judge
// which calendar months the data fully covers.
function dataDateBounds() {
  const ds = (data.transactions || []).map(t => t.date.slice(0, 10)).sort();
  return ds.length ? { first: ds[0], last: ds[ds.length - 1] } : { first: null, last: null };
}

// A month "YYYY-MM" is "complete" only when the loaded data spans its entire
// calendar range — the window starts on/before the 1st and ends on/after the last
// day. The first and last months of a Plaid pull are usually partial (e.g. a pull
// starting 03-27 or the current month on the 1st), and a 1-day month would drag
// down per-month averages and read as a dip in the trend, so partial months are
// excluded from averages/sparklines and only marked (not gated) elsewhere.
function isMonthComplete(m, bounds = dataDateBounds()) {
  if (!bounds.first) return false;
  const [y, mo] = m.split('-').map(Number);
  const lastDay = new Date(y, mo, 0).getDate();
  const start = `${m}-01`;
  const end = `${m}-${String(lastDay).padStart(2, '0')}`;
  return bounds.first <= start && bounds.last >= end;
}

// The month all budget UI keys off: the filtered month, else the latest month
// with data ("All time" pins budgets to the current/latest month — a budget is
// inherently a per-month number, so it always needs a concrete month).
function budgetMonth() {
  const b = dataDateBounds();
  return filterMonth || (b.last ? b.last.slice(0, 7) : null);
}

// Budget pace for one bucket-month. coveredDays is DATA coverage of the month
// (not wall-clock day-of-month) so a partial first month or a stale refresh
// doesn't skew the projection; for a fully past month coveredDays equals
// daysInMonth and projected === spent (plain over/under, no proration).
//   red   — already over, or projected to end the month over
//   amber — projected to land within 10% of the budget
//   green — comfortably under
function budgetStatus(spent, budget, coveredDays, daysInMonth) {
  const projected = coveredDays > 0 ? spent / coveredDays * daysInMonth : spent;
  const overNow = spent > budget;
  const status = (overNow || projected > budget) ? 'red'
    : projected > 0.9 * budget ? 'amber' : 'green';
  return { pct: Math.min(100, spent / budget * 100), projected, status, overNow };
}

// Days of the month `m` ("YYYY-MM") that the loaded data actually covers, and
// the month's calendar length — the two denominators budgetStatus needs.
function monthCoverage(m, bounds = dataDateBounds()) {
  const [y, mo] = m.split('-').map(Number);
  const daysInMonth = new Date(y, mo, 0).getDate();
  const start = `${m}-01`, end = `${m}-${String(daysInMonth).padStart(2, '0')}`;
  const lo = bounds.first > start ? bounds.first : start;
  const hi = bounds.last < end ? bounds.last : end;
  const covered = Math.max(1, Math.round((new Date(hi) - new Date(lo)) / 86400000) + 1);
  return { covered, daysInMonth };
}

const BUDGET_COLORS = { green: '#3fb950', amber: '#FFC312', red: '#f8a5a5' };

// Per-bucket monthly spend series (for the tile sparklines). Groups every expense
// by insightBucket × month — reuses the same bucketing the tiles use, so the
// sparkline shape matches the tile total. Always spans ALL months (a single month
// can't form a trend line) so the line stays as context when a month is filtered.
// Returns the aligned month axis plus per-bucket arrays (0 for months with no
// spend in that bucket); callers use `months` to locate the selected month.
function bucketMonthlySeries() {
  const expenses = (data.transactions || []).filter(t => !t.is_income && !t.is_savings);
  const months = [...new Set(expenses.map(t => t.date.slice(0, 7)))].sort();
  const byBucket = {};
  for (const t of expenses) {
    const b = insightBucket(t);
    if (!b) continue;
    (byBucket[b] = byBucket[b] || {});
    const m = t.date.slice(0, 7);
    byBucket[b][m] = (byBucket[b][m] || 0) + t.amount;
  }
  const series = {};
  for (const b in byBucket) series[b] = months.map(m => byBucket[b][m] || 0);
  return { months, series };
}

// Tiny inline-SVG sparkline: faint area fill + line + a dot marking one month.
// highlightIdx points the dot at the selected month (with a faint vertical guide);
// -1 (no month filter) falls back to marking the latest month.
function sparklineSVG(values, color, highlightIdx = -1, w = 88, h = 24) {
  if (!values || values.length < 2) return `<div style="height:${h}px"></div>`;
  const max = Math.max(...values), min = Math.min(...values), range = (max - min) || 1;
  const n = values.length;
  const pts = values.map((v, i) => [
    (i / (n - 1)) * (w - 2) + 1,
    h - 2 - ((v - min) / range) * (h - 5),
  ]);
  const line = pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area = `1,${h - 1} ${line} ${w - 1},${h - 1}`;
  const hasFilter = highlightIdx >= 0 && highlightIdx < n;
  const dot = pts[hasFilter ? highlightIdx : n - 1];
  const guide = hasFilter
    ? `<line x1="${dot[0].toFixed(1)}" y1="1" x2="${dot[0].toFixed(1)}" y2="${h - 1}" stroke="${color}" stroke-width="1" opacity="0.35"/>`
    : '';
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block;margin-left:auto;margin-top:6px">
    <polygon points="${area}" fill="${color}" opacity="0.12"/>
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>
    ${guide}
    <circle cx="${dot[0].toFixed(1)}" cy="${dot[1].toFixed(1)}" r="${hasFilter ? 2.6 : 2}" fill="${color}"/>
  </svg>`;
}

// Lucide icon per spending bucket (replaces the emoji for a crisp, consistent set).
const LUCIDE_ICONS = {
  food: 'bike', dining: 'utensils', subs: 'repeat', shopping: 'shopping-bag',
  baby: 'baby', ai: 'sparkles', cash: 'banknote', electricity: 'zap', health: 'heart-pulse',
  pet: 'paw-print', household: 'house', rent: 'building-2', insurance: 'shield',
  transport: 'car', entertainment: 'gamepad-2', personal: 'scissors', other: 'package',
};

function renderInsights(ins) {
  const { months: sparkMonths, series: sparks } = bucketMonthlySeries();

  // Restrict averages and the sparkline trend to COMPLETE months so a partial edge
  // month (e.g. the current month on the 1st) doesn't drag the average down or read
  // as a dip. bucketMonthlySeries keeps every month, so a filtered partial month's
  // own total is still available — we just leave it out of the multi-month math.
  const bounds = dataDateBounds();
  const completeIdx = sparkMonths.map((m, i) => i).filter(i => isMonthComplete(sparkMonths[i], bounds));
  const nComplete = completeIdx.length || 1;

  // Avg monthly spend for a bucket: the selected month's own total when a month is
  // filtered, otherwise the mean over complete months only.
  const monthlyAvg = key => {
    const s = sparks[key] || [];
    if (filterMonth) return s[sparkMonths.indexOf(filterMonth)] || 0;
    return completeIdx.reduce((a, i) => a + (s[i] || 0), 0) / nComplete;
  };

  // Sparkline trend uses complete months only. The dot marks the filtered month
  // when it's complete, else falls back to the latest complete month.
  const sparkMonthsC = completeIdx.map(i => sparkMonths[i]);
  const sparksC = {};
  for (const k in sparks) sparksC[k] = completeIdx.map(i => sparks[k][i]);
  const sparkIdx = filterMonth ? sparkMonthsC.indexOf(filterMonth) : -1;
  const items = [
    {
      key: 'food',
      color: '#FF9F43',
      icon: '🛵',
      label: 'Food Delivery',
      sublabel: 'DoorDash orders',
      value: fmt(ins.doordash_total),
      tip: `That's ~${Math.round(ins.doordash_total / 40)} orders this period. Cooking 2x/week could save $200+/mo.`
    },
    {
      key: 'subs',
      color: '#9B59B6',
      icon: '📱',
      label: 'Subscriptions',
      sublabel: subsSublabel(),
      value: fmt(ins.sub_total),
      tip: subsTip(),
    },
    {
      key: 'shopping',
      color: '#F9CA24',
      icon: '🛍️',
      label: 'Shopping',
      sublabel: 'General merchandise not in another tile',
      value: fmt(ins.shopping_total),
      tip: `Catch-all retail (general merchandise) not claimed by a more specific tile. Avg ${fmt(Math.round(monthlyAvg('shopping')))}/mo.`
    },
    {
      key: 'baby',
      color: '#00B894',
      icon: '👶',
      label: 'Baby',
      sublabel: (data.baby_keywords || []).join(', '),
      value: fmt(ins.baby_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('baby')))}/mo. Add keywords to baby_keywords in ~/.finance-viz/config.json to track more.`
    },
    {
      key: 'ai',
      color: '#A29BFE',
      icon: '🤖',
      label: 'AI',
      sublabel: (data.ai_keywords || []).join(', '),
      value: fmt(ins.ai_total || 0),
      tip: `All AI spend in one place — recurring subscriptions and ad-hoc, usage-based charges alike (Claude/Anthropic, ChatGPT/OpenAI, Cursor, Sakana). The Chase charge doesn't distinguish a subscription from usage billing, so both roll up here rather than into Subscriptions. Avg ${fmt(Math.round(monthlyAvg('ai')))}/mo. Edit ai_keywords in ~/.finance-viz/config.json.`
    },
    {
      key: 'cash',
      color: '#778CA3',
      icon: '🏧',
      label: 'Cash Withdrawn',
      sublabel: 'ATM withdrawals',
      value: fmt(ins.atm_cash || 0),
      tip: `${ins.atm_cash > 0 ? `~${fmt(Math.round(monthlyAvg('cash')))}/mo in cash. Each withdrawal also costs $3 in ATM fees.` : 'No ATM withdrawals this period.'}`
    },
    {
      key: 'electricity',
      color: '#4ECDC4',
      icon: '⚡',
      label: 'Utilities',
      sublabel: (data.utility_keywords || []).join(', ') || 'Electric, gas, internet & phone',
      value: fmt(ins.electricity || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('electricity')))}/mo across electric, gas, internet & phone (utility_keywords in ~/.finance-viz/config.json). Watch electric in summer — AC usage spikes.`
    },
    {
      key: 'health',
      color: '#00D2D3',
      icon: '🩺',
      label: 'Health',
      sublabel: ['Medical / Rx', ...(data.health_keywords || [])].join(', '),
      value: fmt(ins.health_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('health')))}/mo. Charges Plaid tags MEDICAL plus health_keywords in ~/.finance-viz/config.json.`
    },
    {
      key: 'pet',
      color: '#6C5CE7',
      icon: '🐾',
      label: 'Pet',
      sublabel: (data.pet_keywords || []).join(', '),
      value: fmt(ins.pet_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('pet')))}/mo on pets. Add/remove merchants via pet_keywords in ~/.finance-viz/config.json.`
    },
    {
      key: 'household',
      color: '#A8E063',
      icon: '🏠',
      label: 'Household',
      sublabel: (data.household_keywords || []).join(', '),
      value: fmt(ins.household_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('household')))}/mo on groceries + household runs, matched via household_keywords in ~/.finance-viz/config.json.`
    },
    {
      key: 'dining',
      color: '#FD79A8',
      icon: '🍽️',
      label: 'Dining',
      sublabel: 'Restaurants & fast food (not delivery)',
      value: fmt(ins.dining_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('dining')))}/mo at restaurants/fast food (Plaid FOOD_AND_DRINK). DoorDash delivery is its own Food Delivery tile.`
    },
    {
      key: 'rent',
      color: '#FF6B6B',
      icon: '🏘️',
      label: 'Rent',
      sublabel: 'Rent / housing',
      value: fmt(ins.rent_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('rent')))}/mo (Plaid RENT_AND_UTILITIES, after utility providers are peeled off).`
    },
    {
      key: 'insurance',
      color: '#E17055',
      icon: '🛡️',
      label: 'Insurance',
      sublabel: 'Premiums (auto, home, life)',
      value: fmt(ins.insurance_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('insurance')))}/mo on insurance premiums (base category Insurance).`
    },
    {
      key: 'transport',
      color: '#54A0FF',
      icon: '🚗',
      label: 'Transportation',
      sublabel: 'Gas, transit, rideshare, parking',
      value: fmt(ins.transport_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('transport')))}/mo on gas, transit & rides (Plaid TRANSPORTATION/TRAVEL).`
    },
    {
      key: 'entertainment',
      color: '#FFC312',
      icon: '🎬',
      label: 'Entertainment',
      sublabel: 'Games, streaming, events',
      value: fmt(ins.entertainment_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('entertainment')))}/mo on games/entertainment (Plaid ENTERTAINMENT).`
    },
    {
      key: 'personal',
      color: '#E84393',
      icon: '💅',
      label: 'Personal Care',
      sublabel: 'Salon, grooming, personal care',
      value: fmt(ins.personal_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('personal')))}/mo on salon/grooming (Plaid PERSONAL_CARE).`
    },
    {
      key: 'other',
      color: '#95A5A6',
      icon: '📦',
      label: 'Other',
      sublabel: 'Uncategorized — services, fees, gov’t',
      value: fmt(ins.other_total || 0),
      tip: `Avg ${fmt(Math.round(monthlyAvg('other')))}/mo not yet categorized (misc services, government, fees). Reclassify specific charges via bucket_overrides in ~/.finance-viz/config.json.`
    },
  ];
  INSIGHT_LABELS = Object.fromEntries(items.map(i => [i.key, i.label]));

  // Budget progress bar per tile (only for buckets with a config.json budget).
  // Spent comes from the same per-bucket series the sparklines use, pinned to
  // budgetMonth(); pace prorates by the data coverage of that month.
  const bm = budgetMonth();
  const budgetBar = key => {
    const budget = (data.budgets || {})[key];
    if (!budget || !bm) return '';
    const spent = (sparks[key] || [])[sparkMonths.indexOf(bm)] || 0;
    const { covered, daysInMonth } = monthCoverage(bm, bounds);
    const s = budgetStatus(spent, budget, covered, daysInMonth);
    const color = BUDGET_COLORS[s.status];
    const [y, mo] = bm.split('-');
    const mLabel = new Date(y, mo - 1).toLocaleString('en-US', { month: 'short', year: '2-digit' });
    const projNote = (covered < daysInMonth && s.status === 'red' && !s.overNow)
      ? ` · projected ${fmt(Math.round(s.projected))} — over` : (s.overNow ? ' · over' : '');
    return `
      <div style="margin-top:8px">
        <div style="height:4px;border-radius:2px;background:#21262d;overflow:hidden">
          <div style="height:100%;width:${s.pct.toFixed(1)}%;background:${color};border-radius:2px"></div>
        </div>
        <div style="font-size:0.68rem;color:#8b949e;margin-top:3px">
          ${fmt(spent)} of ${fmt(budget)} — ${mLabel}<span style="color:${color}">${projNote}</span>
        </div>
      </div>`;
  };

  document.getElementById('insights-content').innerHTML = items.map(item => `
    <div class="col-span-12 sm:col-span-6 md:col-span-4">
      <div class="insight-block" data-insight-key="${item.key}" onclick="toggleInsight('${item.key}')" style="cursor:pointer">
        <div class="insight-bar" style="background:${item.color}"></div>
        <div class="flex justify-between items-start mb-1">
          <div>
            <i data-lucide="${LUCIDE_ICONS[item.key] || 'circle'}" style="width:17px;height:17px;color:${item.color};vertical-align:-3px"></i>
            <strong style="font-size:0.9rem;margin-left:6px">${item.label}</strong>
            <span class="insight-toggle-hint" style="font-size:0.65rem;color:${item.color};margin-left:6px">view ▸</span>
            <div style="font-size:0.72rem;color:#8b949e;margin-top:2px">${item.sublabel}</div>
          </div>
          <div style="text-align:right;min-width:90px">
            <div class="tnum" style="font-size:1.3rem;font-weight:700;color:${item.color};line-height:1.1;letter-spacing:-0.01em">${item.value}</div>
            ${sparklineSVG(sparksC[item.key], item.color, sparkIdx)}
          </div>
        </div>
        <div style="font-size:0.75rem;color:#8b949e;margin-top:8px;line-height:1.4">${item.tip}</div>
        ${budgetBar(item.key)}
      </div>
    </div>
  `).join('');

  if (window.lucide) lucide.createIcons();
  // Re-open the active drill-down with fresh data after a re-render.
  if (activeInsight) openInsight(activeInsight, false);
}

// --- Subscriptions: sublabel, tip, and drill-down (backed by /api/subscriptions) ---
function subsSublabel() {
  if (!subsData || !subsData.subscriptions || !subsData.subscriptions.length)
    return 'Recurring charges';
  const names = subsData.subscriptions.slice(0, 4).map(s => s.merchant);
  const extra = subsData.subscriptions.length - names.length;
  return names.join(', ') + (extra > 0 ? `, +${extra} more` : '');
}

function subsTip() {
  if (!subsData) return 'Loading recurring-charge analysis…';
  const n = (subsData.subscriptions || []).length;
  const changes = (subsData.price_changes || []).length;
  const lapsed = (subsData.lapsed || []).length;
  if (!n) return 'No recurring subscriptions detected yet.';
  let t = `${n} recurring sub${n === 1 ? '' : 's'} ≈ ${fmt(subsData.monthly_total)}/mo (${fmt(subsData.monthly_total * 12)}/yr).`;
  if (changes) t += ` ${changes} price change${changes === 1 ? '' : 's'}.`;
  if (lapsed) t += ` ${lapsed} possibly lapsed.`;
  return t + ' Click to view all.';
}

// --- Insight accordion: one drill-down open at a time -----------------------
function toggleInsight(key) {
  if (activeInsight === key) { closeInsight(); return; }
  openInsight(key, true);
}

function closeInsight() {
  activeInsight = null;
  document.getElementById('insight-drilldown-panel').style.display = 'none';
  highlightActiveTile(null);
}

function highlightActiveTile(key) {
  document.querySelectorAll('[data-insight-key]').forEach(el => {
    const on = el.getAttribute('data-insight-key') === key;
    el.style.outline = on ? '2px solid #58a6ff' : 'none';
    el.style.outlineOffset = on ? '1px' : '0';
  });
}

function openInsight(key, doScroll) {
  activeInsight = key;
  const [title, html] = txnDrilldownContent(key);
  document.getElementById('insight-drilldown-title').textContent = title;
  document.getElementById('insight-drilldown-body').innerHTML = html;
  const panel = document.getElementById('insight-drilldown-panel');
  panel.style.display = '';
  highlightActiveTile(key);
  if (doScroll) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Mirror of app.py _is_walgreens_pharmacy: a Walgreens charge is "pharmacy"
// (→ Health) if it matches a scraped Rx fill by amount (±$0.01) within ±2 days,
// or by date alone when the scraper couldn't capture amounts. No fills → false,
// so every Walgreens charge falls to Household.
function isWalgreensPharmacy(t) {
  const fills = (data && data.walgreens_fills) || [];
  if (!fills.length) return false;
  const matchDays = (data && data.walgreens_match_days) || 10;
  const td = new Date(t.date.slice(0, 10) + 'T00:00:00');
  const tamt = Math.round(t.amount * 100) / 100;
  for (const f of fills) {
    if (!f.date) continue;
    const fd = new Date(String(f.date).slice(0, 10) + 'T00:00:00');
    if (Math.abs((fd - td) / 86400000) > matchDays) continue;
    if (f.amount == null) return true;
    if (Math.abs(Math.round(f.amount * 100) / 100 - tamt) <= 0.01) return true;
  }
  return false;
}

// The spending bucket is assigned server-side (app.py's _bucket loop) and stamped
// onto each expense as t.bucket — the single source of truth, including per-charge
// overrides from /finance-recategorize. The frontend just reads it so the
// classification can never drift between Python and JS.
function insightBucket(t) {
  return t.bucket;
}

function insightFilter(key) {
  return t => insightBucket(t) === key;
}

function txnDrilldownContent(key) {
  const expenses = (viewData?.transactions || []).filter(t => !t.is_income && !t.is_savings);
  const matched = expenses.filter(insightFilter(key)).sort((a, b) => b.date.localeCompare(a.date));
  const total = matched.reduce((s, t) => s + t.amount, 0);
  const label = INSIGHT_LABELS[key] || key;
  if (!matched.length) {
    return [label, '<div class="nw-small" style="padding:6px 0;color:#8b949e">No matching transactions in this period.</div>'];
  }
  const rows = matched.map(t => `
    <div class="flex justify-between items-center" style="padding:5px 0;border-bottom:1px solid #21262d">
      <div><span style="color:#8b949e;font-size:0.75rem">${t.date.slice(5)}</span>
        <span style="font-size:0.82rem;margin-left:8px">${t.description}</span>
        <span class="cat-badge" style="font-size:0.62rem;margin-left:6px;color:#8b949e">${t.category}</span></div>
      <span class="amount-exp" style="font-size:0.82rem;white-space:nowrap">-${fmt(t.amount)}</span>
    </div>`).join('');
  const html = `<div style="max-height:340px;overflow-y:auto">${rows}</div>`;
  return [`${label} — ${matched.length} txns · ${fmt(total)}`, html];
}

function renderFilters(expenses) {
  const cats = [...new Set(expenses.map(t => t.category))].sort();
  const el = document.getElementById('cat-filters');
  el.innerHTML = `
    <button class="filter-btn active" style="border-color:#8b949e;color:#8b949e" onclick="setFilter(null)">All</button>
    ${cats.map(c => {
      const color = data.colors[c] || '#95A5A6';
      return `<button class="filter-btn" style="border-color:${color};color:${color}" onclick="setFilter('${c}')">${c}</button>`;
    }).join('')}
  `;
}

function setFilter(cat) {
  activeCategory = cat;
  updateFilterBtns();
  renderTable();
  renderCategoryDrilldown();
}

function updateFilterBtns() {
  document.querySelectorAll('.filter-btn').forEach(btn => {
    const isAll = btn.textContent === 'All';
    const isActive = isAll ? !activeCategory : btn.textContent === activeCategory;
    btn.classList.toggle('active', isActive);
    if (isActive) {
      const color = isAll ? '#8b949e' : (data.colors[btn.textContent] || '#95A5A6');
      btn.style.background = color;
      btn.style.color = '#0d1117';
    } else {
      btn.style.background = 'transparent';
      const color = isAll ? '#8b949e' : (data.colors[btn.textContent] || '#95A5A6');
      btn.style.color = color;
    }
  });
}

function renderTable() {
  if (!viewData) return;
  const search = document.getElementById('tx-search').value.toLowerCase();
  const all = [...viewData.transactions].reverse();

  const filtered = all.filter(t => {
    if (activeCategory && t.category !== activeCategory && !(t.is_income && activeCategory === 'Income')) return false;
    if (search && !t.description.toLowerCase().includes(search) && !t.category.toLowerCase().includes(search)) return false;
    return true;
  });

  const rows = filtered.map(t => {
    const inflow = t.is_income || t.is_savings;
    const color = inflow ? (t.is_savings ? '#00B894' : '#3fb950') : (data.colors[t.category] || '#95A5A6');
    const amtClass = inflow ? 'amount-inc' : 'amount-exp';
    const sign = inflow ? '+' : '-';
    const pendingBadge = t.pending
      ? ` <span class="pending-badge" title="Pending — not yet posted; amount may change">pending</span>`
      : '';
    return `
      <tr${t.pending ? ' style="opacity:0.72"' : ''}>
        <td style="color:#8b949e;white-space:nowrap">${t.date.slice(5)}</td>
        <td><div class="tx-desc" title="${t.description}">${t.description}${pendingBadge}</div></td>
        <td><span class="cat-badge" style="background:${color}22;color:${color}">${t.category}</span></td>
        <td style="color:#6e7681;font-size:0.75rem;white-space:nowrap">${t.account}</td>
        <td class="text-right ${amtClass}">${sign}${fmt(t.amount)}</td>
      </tr>
    `;
  });

  document.getElementById('tx-body').innerHTML = rows.join('');
  document.getElementById('tx-count-label').textContent =
    `Showing ${filtered.length} of ${viewData.transactions.length} transactions`;

  // Totals row: sum the Amount column for exactly the rows shown, using the same
  // sign convention as the rows (inflows +, outflows −) so it reconciles with
  // what's on screen. Filter to a category (e.g. Subscriptions) to total it.
  const outflow = filtered.filter(t => !t.is_income && !t.is_savings)
    .reduce((s, t) => s + t.amount, 0);
  const inflow = filtered.filter(t => t.is_income || t.is_savings)
    .reduce((s, t) => s + t.amount, 0);
  const net = inflow - outflow;
  const scope = activeCategory ? `${activeCategory}` : (search ? `“${search}”` : 'All transactions');
  const footLabel = document.getElementById('tx-foot-label');
  const footTotal = document.getElementById('tx-foot-total');
  if (inflow > 0 && outflow > 0) {
    // Mixed view — show the spend total (what reconciles with category amounts),
    // and note the net so income isn't hidden.
    footLabel.textContent = `${scope} — ${filtered.length} txns · net ${net >= 0 ? '+' : '−'}${fmt(Math.abs(net))}`;
    footTotal.innerHTML = `<span class="amount-exp">−${fmt(outflow)}</span>`;
  } else if (inflow > 0) {
    footLabel.textContent = `${scope} — ${filtered.length} txns`;
    footTotal.innerHTML = `<span class="amount-inc">+${fmt(inflow)}</span>`;
  } else {
    footLabel.textContent = `${scope} — ${filtered.length} txns`;
    footTotal.innerHTML = `<span class="amount-exp">−${fmt(outflow)}</span>`;
  }
}

init();

// ---- Conceal / reveal dollar values ----------------------------------------
// Default = concealed, so the dashboard is safe to show to friends out of the box.
// fmt() output lands in the DOM as plain text via many code paths (and is also
// reused for Chart.js tooltips), so instead of wrapping at the source we tag any
// "$N" text in the DOM with a .money span and blur it via CSS. A MutationObserver
// re-tags after every render automatically.
(function setupConceal() {
  const MONEY_RE = /\$\s?-?[\d,]+(?:\.\d+)?/g;
  const root = document.querySelector('.container-fluid') || document.body;
  const cb = document.getElementById('conceal-check');
  const label = document.getElementById('conceal-label');

  function tagMoney(node) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (!n.nodeValue || n.nodeValue.indexOf('$') === -1) return NodeFilter.FILTER_REJECT;
        const p = n.parentNode;
        if (!p || p.nodeName === 'SCRIPT' || p.nodeName === 'STYLE') return NodeFilter.FILTER_REJECT;
        if (p.classList && p.classList.contains('money')) return NodeFilter.FILTER_REJECT;
        MONEY_RE.lastIndex = 0;
        return MONEY_RE.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    const targets = [];
    while (walker.nextNode()) targets.push(walker.currentNode);
    for (const n of targets) {
      const s = n.nodeValue, frag = document.createDocumentFragment();
      let last = 0, m; MONEY_RE.lastIndex = 0;
      while ((m = MONEY_RE.exec(s))) {
        if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
        const span = document.createElement('span');
        span.className = 'money'; span.textContent = m[0];
        frag.appendChild(span); last = m.index + m[0].length;
      }
      if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
      n.parentNode.replaceChild(frag, n);
    }
  }

  const observer = new MutationObserver(() => {
    observer.disconnect();
    tagMoney(root);
    observer.observe(root, { childList: true, subtree: true });
  });

  function apply() {
    document.body.classList.toggle('conceal-money', CONCEAL_MONEY);
    if (cb) cb.checked = CONCEAL_MONEY;
    if (label) label.textContent = CONCEAL_MONEY ? 'Concealed' : 'Conceal $';
  }

  if (cb) cb.addEventListener('change', () => {
    CONCEAL_MONEY = cb.checked;
    localStorage.setItem('concealMoney', CONCEAL_MONEY ? '1' : '0');
    apply();
    // Re-render charts so axis ticks / tooltips reflect the new state.
    if (typeof render === 'function') render();
    if (typeof renderNetWorth === 'function' && typeof nwData !== 'undefined' && nwData) renderNetWorth();
    if (typeof renderCashflow === 'function' && typeof cfData !== 'undefined' && cfData) renderCashflow();
  });

  tagMoney(root);
  apply();
  observer.observe(root, { childList: true, subtree: true });
})();

// ── Net Worth ──────────────────────────────────────────────────────────────

let nwData = null;
let cfData = null;
let cashflowChart = null;
let nwDonutChart = null;
let nwVestingChart = null;
let nwBarsChart = null;
let nwAssetClassChart = null;
let nwTopPositionsChart = null;

function switchTab(tab) {
  const tabs = ['spending', 'trends', 'networth', 'cashflow'];
  if (!tabs.includes(tab)) tab = 'spending';
  tabs.forEach(t => {
    document.getElementById('view-' + t).style.display = (t === tab) ? '' : 'none';
    const active = (t === tab);
    document.getElementById('tab-' + t).style.cssText =
      `background:none;border:none;border-bottom:2px solid ${active ? '#58a6ff' : 'transparent'};color:${active ? '#e1e4e8' : '#8b949e'};font-size:0.82rem;font-weight:600;padding:8px 16px 10px;margin-bottom:-1px;cursor:pointer`;
  });
  // Fade/slide the newly shown view in.
  const shown = document.getElementById('view-' + tab);
  shown.classList.remove('view-anim'); void shown.offsetWidth; shown.classList.add('view-anim');
  // Always refetch on switch — these are cheap local endpoints, and the
  // fetch-once `!nwData` guards were why Net Worth / Cash Flow never updated
  // after the first view without a full page reload.
  if (tab === 'networth') loadNetWorth();
  if (tab === 'trends') renderTrends();
  if (tab === 'cashflow') loadCashflow();
}

async function loadNetWorth() {
  const resp = await fetch('/api/networth');
  nwData = await resp.json();
  renderNetWorth();
}

// ── Cash Flow forecast ──────────────────────────────────────────────────────

async function loadCashflow() {
  try {
    cfData = await fetch('/api/cashflow').then(r => r.json());
  } catch {
    cfData = { available: false };
  }
  renderCashflow();
}

function renderCashflow() {
  const empty = document.getElementById('cashflow-empty');
  const content = document.getElementById('cashflow-content');
  if (!cfData || !cfData.available) {
    empty.textContent = 'Forecast unavailable — needs a Chase Checking balance and transaction history. Run refresh.py and reload.';
    empty.style.display = '';
    content.style.display = 'none';
    return;
  }
  empty.style.display = 'none';
  content.style.display = '';
  const cf = cfData;

  // Stat cards
  countUp(document.getElementById('cf-stat-balance'), cf.start_balance);
  const minEl = document.getElementById('cf-stat-min');
  countUp(minEl, cf.min_balance);
  minEl.style.color = cf.shortfall ? '#f8a5a5' : '#3fb950';
  document.getElementById('cf-stat-min-label').textContent =
    `Minimum projected — ${cf.min_date}`;
  const safeEl = document.getElementById('cf-stat-safe');
  countUp(safeEl, cf.safe_to_save);
  safeEl.style.color = cf.safe_to_save > 0 ? '#58a6ff' : '#8b949e';
  document.getElementById('cf-stat-safe-label').textContent =
    cf.shortfall
      ? `Below the ${fmt(cf.buffer)} buffer — nothing safe to move`
      : `Safe to move to savings (keeps ${fmt(cf.buffer)} buffer)`;
  document.getElementById('cf-stat-bills').textContent = cf.upcoming_bills.length;
  document.getElementById('cashflow-chart-title').textContent =
    `Projected Checking Balance — next ${cf.horizon_days} days`;

  // Projection chart: balance line with payday/bill markers + buffer line.
  const labels = cf.daily.map(d => d.date.slice(5));
  const balances = cf.daily.map(d => d.balance);
  const ptColor = cf.daily.map(d =>
    d.events.some(e => e.type === 'paycheck') ? '#3fb950'
    : d.events.some(e => e.type === 'bill') ? '#f8a5a5' : 'transparent');
  const ptRadius = cf.daily.map(d => d.events.length ? 3 : 0);

  if (cashflowChart) cashflowChart.destroy();
  cashflowChart = new Chart(document.getElementById('cashflow-chart'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Projected balance', data: balances, borderColor: '#58a6ff',
          backgroundColor: '#58a6ff22', fill: true, tension: 0.25, borderWidth: 2,
          pointRadius: ptRadius, pointBackgroundColor: ptColor, pointBorderWidth: 0 },
        { label: 'Buffer', data: balances.map(() => cf.buffer), borderColor: '#FFC312',
          borderDash: [6, 4], borderWidth: 1, pointRadius: 0, fill: false },
      ]
    },
    options: {
      plugins: {
        legend: { labels: { color: '#c9d1d9', font: { size: 10.5 }, boxWidth: 11 } },
        tooltip: { callbacks: {
          label: ctx => ` ${ctx.dataset.label}: ${tipMoney(ctx.raw)}`,
          afterBody: items => (cf.daily[items[0].dataIndex].events || [])
            .map(e => `${e.type === 'paycheck' ? '+' : '−'}${tipMoney(e.amount)} ${e.name}`),
        } }
      },
      scales: {
        x: { ticks: { color: '#6e7681', font: { size: 10 }, maxTicksLimit: 15 }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', callback: axisMoney }, grid: { color: '#21262d' } }
      }
    }
  });

  // Upcoming bills table
  document.getElementById('cashflow-bills-title').textContent =
    `Upcoming Bills — next ${cf.horizon_days} days`;
  const body = document.getElementById('cashflow-bills-body');
  const billsEmpty = document.getElementById('cashflow-bills-empty');
  billsEmpty.style.display = cf.upcoming_bills.length ? 'none' : '';
  body.innerHTML = cf.upcoming_bills.map(b => `
    <tr style="border-bottom:1px solid #21262d">
      <td style="padding:6px 8px 6px 0;color:#8b949e;white-space:nowrap">${b.date}</td>
      <td style="padding:6px 8px"><strong>${b.merchant}</strong></td>
      <td style="padding:6px 8px"><span class="cat-badge" style="font-size:0.65rem;color:#8b949e">${b.cadence}</span></td>
      <td style="padding:6px 8px;color:#8b949e;font-size:0.72rem">${b.category}</td>
      <td style="padding:6px 0 6px 8px;text-align:right" class="amount-exp">-${fmt(b.amount)}</td>
    </tr>`).join('');

  // Assumptions footnote
  const a = cf.assumptions;
  const pay = (a.payroll_streams || []).length
    ? (a.payroll_streams || []).map(s => `${fmt(s.amount)} from ${s.name} every ${s.cadence_days}d`).join(', ')
    : 'no payroll history detected (income-free forecast)';
  const burn = a.burn_months.length
    ? `${fmt(a.daily_burn)}/day discretionary (avg of ${a.burn_months.length} complete months)`
    : `${fmt(a.daily_burn)}/day discretionary (estimate from partial data)`;
  document.getElementById('cashflow-assumptions').textContent =
    `Assumes ${pay}; ${burn}. Recurring bills modeled individually from detected cadences. As of ${cf.as_of}.`;
}

// ── Trends / Monthly Report ─────────────────────────────────────────────────

function monthLabel(mo) {
  const [y, m] = mo.split('-').map(Number);
  return new Date(y, m - 1).toLocaleString('en-US', { month: 'short', year: 'numeric' });
}

function trendsMetrics() {
  // Per-month income / spend / saved / Ally transfers / category breakdown,
  // plus which months are complete. A month counts as complete only when the
  // data spans its whole calendar range — partial first/current months are
  // excluded from comparisons so a 4-day month can't masquerade as a spike.
  const txns = (data && data.transactions) || [];
  if (!txns.length) return { full: [], partial: [], metric: {} };
  const bounds = dataDateBounds();
  const months = [...new Set(txns.map(t => t.date.slice(0, 7)))].sort();
  const isComplete = mo => isMonthComplete(mo, bounds);

  const metric = {};
  for (const mo of months) {
    const mt = txns.filter(t => t.date.slice(0, 7) === mo);
    const income = mt.filter(t => t.is_income).reduce((s, t) => s + t.amount, 0);
    const exp = mt.filter(t => !t.is_income && !t.is_savings);
    const spend = exp.reduce((s, t) => s + t.amount, 0);
    const transfers = mt.filter(t => t.is_savings).reduce((s, t) => s + t.amount, 0);
    const byCat = {};
    exp.forEach(t => { byCat[t.category] = (byCat[t.category] || 0) + t.amount; });
    metric[mo] = { income, spend, saved: income - spend, transfers,
                   rate: income > 0 ? (income - spend) / income * 100 : 0, byCat };
  }
  return { full: months.filter(isComplete), partial: months.filter(m => !isComplete(m)), metric };
}

// Findings — anomaly-detector output (/api/anomalies), rendered as cards atop Trends.
const FINDING_STYLE = {
  hot_category: { icon: 'flame', color: '#f8a5a5', title: c => `${c} running hot` },
  large:        { icon: 'trending-up', color: '#FFC312', title: f => `Large charge at ${f.merchant}` },
  new_merchant: { icon: 'sparkles', color: '#58a6ff', title: f => `New merchant: ${f.merchant}` },
  budget_pace:  { icon: 'gauge', color: '#f8a5a5', title: f => `${f.category} over budget pace` },
};

// Budget findings for the latest data month: one per bucket that's over budget
// or projected over (red status). Computed from the same budgetStatus math the
// tiles use, so a green tile can never coexist with a "projected over" finding.
function budgetFindings() {
  if (!data) return [];
  const budgets = data.budgets || {};
  const bm = budgetMonth();
  if (!bm || !Object.keys(budgets).length) return [];
  const bounds = dataDateBounds();
  const { covered, daysInMonth } = monthCoverage(bm, bounds);
  const { months: sparkMonths, series: sparks } = bucketMonthlySeries();
  const mi = sparkMonths.indexOf(bm);
  const out = [];
  for (const k of Object.keys(budgets)) {
    const spent = mi >= 0 ? ((sparks[k] || [])[mi] || 0) : 0;
    const s = budgetStatus(spent, budgets[k], covered, daysInMonth);
    if (s.status !== 'red') continue;
    const label = INSIGHT_LABELS[k] || BUCKET_FALLBACK_LABELS[k] || k;
    const detail = s.overNow
      ? `${label} has already exceeded its budget (${fmt(spent)} spent vs ${fmt(budgets[k])}).`
      : `${label} is projected to exceed its budget (${fmt(Math.round(s.projected))} projected vs ${fmt(budgets[k])}).`;
    out.push({ kind: 'budget_pace', bucket: k, category: label,
               amount: spent, budget: budgets[k], projected: s.projected, detail });
  }
  return out.sort((a, b) => (b.amount / b.budget) - (a.amount / a.budget));
}

// INSIGHT_LABELS is only populated once the Spending tiles render; Trends can
// render first on a hard refresh landing there, so keep a static fallback.
const BUCKET_FALLBACK_LABELS = {
  food: 'Food Delivery', subs: 'Subscriptions', shopping: 'Shopping', baby: 'Baby',
  ai: 'AI', cash: 'Cash Withdrawn', electricity: 'Utilities', health: 'Health',
  pet: 'Pet', household: 'Household', dining: 'Dining', rent: 'Rent',
  insurance: 'Insurance', transport: 'Transportation', entertainment: 'Entertainment',
  personal: 'Personal Care', other: 'Other',
};

async function renderFindings() {
  const cards = document.getElementById('findings-cards');
  const empty = document.getElementById('findings-empty');
  const meta = document.getElementById('findings-meta');
  let res;
  try {
    res = await fetch('/api/anomalies').then(r => r.json());
  } catch {
    empty.style.display = ''; empty.textContent = 'Findings unavailable right now.';
    return;
  }
  const findings = [...budgetFindings(), ...((res && res.findings) || [])];
  meta.textContent = res && res.month
    ? `${monthLabel(res.month)} · ${findings.length} finding${findings.length === 1 ? '' : 's'}`
    : '';
  if (!findings.length) {
    cards.innerHTML = '';
    empty.style.display = '';
    empty.textContent = res && res.month
      ? `Nothing unusual in ${monthLabel(res.month)}. 👍`
      : 'No findings yet.';
    return;
  }
  empty.style.display = 'none';
  cards.innerHTML = findings.map(f => {
    const s = FINDING_STYLE[f.kind] || { icon: 'circle', color: '#8b949e', title: () => 'Finding' };
    const title = s.title(f.kind === 'hot_category' ? f.category : f);
    const right = f.kind === 'hot_category'
      ? `<span style="font-weight:700;color:${s.color}">${f.ratio}×</span>`
      : f.kind === 'budget_pace'
      ? `<span style="font-weight:700;color:${s.color}">${fmt(f.amount)}</span><span style="font-size:0.72rem;color:#8b949e"> / ${fmt(f.budget)}</span>`
      : `<span style="font-weight:700">${fmt(f.amount)}</span>`;
    const when = f.date ? `<span style="color:#6e7681;font-size:0.7rem">${f.date.slice(5)}</span>` : '';
    return `
      <div class="col-span-12 sm:col-span-6 md:col-span-4">
        <div class="card-panel p-4 h-full" style="border-left:3px solid ${s.color}">
          <div class="flex justify-between items-start mb-1">
            <div style="font-weight:700;font-size:0.9rem;display:flex;align-items:center;gap:6px"><i data-lucide="${s.icon}" style="width:15px;height:15px;color:${s.color}"></i>${title}</div>
            ${right}
          </div>
          <div style="font-size:0.76rem;color:#adbac7;line-height:1.4">${f.detail}</div>
          <div style="margin-top:6px">${when}</div>
        </div>
      </div>`;
  }).join('');
  if (window.lucide) lucide.createIcons();
}

function renderTrends() {
  renderFindings();
  const { full, partial, metric } = trendsMetrics();
  const cards = document.getElementById('trends-cards');
  const empty = document.getElementById('trends-empty');
  const table = document.getElementById('trends-table');
  const coverage = document.getElementById('trends-coverage');

  coverage.textContent = full.length
    ? `Complete: ${full.map(monthLabel).join(', ')}` +
      (partial.length ? `  ·  Excluded (partial): ${partial.map(monthLabel).join(', ')}` : '')
    : '';

  if (!full.length) {
    cards.innerHTML = '';
    table.style.display = 'none';
    empty.style.display = '';
    empty.textContent = partial.length
      ? 'No complete months yet — comparisons appear once this month closes. '
        + `(In progress: ${partial.map(monthLabel).join(', ')}.)`
      : 'No data to report yet.';
    return;
  }
  empty.style.display = 'none';
  table.style.display = '';

  const signed = v => (v >= 0 ? '+' : '−') + fmt(Math.abs(v));
  const arrow = v => v > 0 ? '▲' : (v < 0 ? '▼' : '');
  // For spend/categories, up is bad (red); for income/saved, up is good (green).
  const colorUp = (v, goodUp) => v === 0 ? '#8b949e' : ((v > 0) === goodUp ? '#3fb950' : '#f8a5a5');

  // ── Narrative cards (most recent first), each vs the prior complete month ──
  const ordered = [...full].reverse();
  cards.innerHTML = ordered.map(mo => {
    const i = full.indexOf(mo);
    const cur = metric[mo];
    const prev = i > 0 ? metric[full[i - 1]] : null;
    const prevLabel = i > 0 ? monthLabel(full[i - 1]) : null;

    const line = (label, val, delta, goodUp) => `
      <div style="display:flex;justify-content:space-between;align-items:baseline;padding:3px 0">
        <span style="color:#8b949e;font-size:0.78rem">${label}</span>
        <span style="font-weight:700">${val}
          ${delta !== null ? `<span style="font-size:0.72rem;font-weight:600;color:${colorUp(delta, goodUp)};margin-left:6px">${signed(delta)} ${arrow(delta)}</span>` : ''}
        </span>
      </div>`;

    // Top category movers vs prior month
    let moversHtml = '', summary = '';
    if (prev) {
      const catKeys = [...new Set([...Object.keys(cur.byCat), ...Object.keys(prev.byCat)])];
      const movers = catKeys
        .map(c => ({ c, d: (cur.byCat[c] || 0) - (prev.byCat[c] || 0) }))
        .filter(m => Math.abs(m.d) >= 1)
        .sort((a, b) => Math.abs(b.d) - Math.abs(a.d))
        .slice(0, 3);
      moversHtml = movers.map(m => `
        <div style="display:flex;justify-content:space-between;font-size:0.74rem;padding:1px 0">
          <span style="color:#c9d1d9">${m.c}</span>
          <span style="color:${colorUp(m.d, false)};font-weight:600">${signed(m.d)} ${arrow(m.d)}</span>
        </div>`).join('');

      const spendD = cur.spend - prev.spend;
      const savedD = cur.saved - prev.saved;
      const up = movers.filter(m => m.d > 0)[0];
      const down = movers.filter(m => m.d < 0).sort((a, b) => a.d - b.d)[0];
      const parts = [];
      parts.push(`Spend ${spendD >= 0 ? 'rose' : 'fell'} ${fmt(Math.abs(spendD))} vs ${prevLabel}`);
      if (up) parts.push(`${up.c} led (${signed(up.d)})`);
      if (down) parts.push(`${down.c} eased (${signed(down.d)})`);
      summary = parts.join('; ') + '. ' +
        `Saved ${savedD >= 0 ? 'improved' : 'slipped'} by ${fmt(Math.abs(savedD))}.`;
    } else {
      summary = 'First complete month — no prior month to compare against yet.';
    }

    const transferNote = cur.transfers > 0
      ? `<div style="display:flex;justify-content:space-between;font-size:0.74rem;margin-top:8px;padding-top:6px;border-top:1px solid #21262d">
           <span style="color:#8b949e">Into savings → Ally HYSA</span>
           <span style="color:#58a6ff;font-weight:600">+${fmt(cur.transfers)}</span>
         </div>`
      : '';

    return `
      <div class="col-span-12 lg:col-span-6">
        <div class="card-panel p-4 h-full">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
            <div style="font-weight:700;font-size:1.05rem">${monthLabel(mo)}</div>
            <div style="font-size:0.72rem;color:${colorUp(cur.saved, true)};font-weight:600">
              ${cur.saved >= 0 ? 'Saved' : 'Over'} ${fmt(Math.abs(cur.saved))} · ${cur.rate.toFixed(1)}%
            </div>
          </div>
          ${line('Income', fmt(cur.income), prev ? cur.income - prev.income : null, true)}
          ${line('Spend', fmt(cur.spend), prev ? cur.spend - prev.spend : null, false)}
          ${line('Saved', signed(cur.saved), prev ? cur.saved - prev.saved : null, true)}
          <div style="font-size:0.78rem;color:#adbac7;margin:10px 0 6px;line-height:1.4">${summary}</div>
          ${moversHtml ? `<div style="border-top:1px solid #21262d;padding-top:6px;margin-top:6px"><div style="font-size:0.66rem;color:#6e7681;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:3px">Top movers vs ${prevLabel}</div>${moversHtml}</div>` : ''}
          ${transferNote}
        </div>
      </div>`;
  }).join('');

  // ── Detail table: categories × complete months, Δ = latest vs prior ────────
  const head = document.getElementById('trends-table-head');
  const body = document.getElementById('trends-table-body');
  const hasDelta = full.length >= 2;
  const latest = metric[full[full.length - 1]];
  const prior = hasDelta ? metric[full[full.length - 2]] : null;

  head.innerHTML = `<tr>
    <th>Category</th>
    ${full.map(m => `<th class="text-right">${monthLabel(m)}</th>`).join('')}
    ${hasDelta ? `<th class="text-right">Δ latest</th>` : ''}
  </tr>`;

  const catKeys = [...new Set(full.flatMap(m => Object.keys(metric[m].byCat)))]
    .sort((a, b) => (latest.byCat[b] || 0) - (latest.byCat[a] || 0));

  const summaryRow = (label, pick, goodUp, bold) => {
    const d = hasDelta ? pick(latest) - pick(prior) : null;
    return `<tr style="${bold ? 'font-weight:700;background:#161b22' : ''}">
      <td>${label}</td>
      ${full.map(m => `<td class="text-right">${fmt(pick(metric[m]))}</td>`).join('')}
      ${hasDelta ? `<td class="text-right" style="color:${colorUp(d, goodUp)};font-weight:600">${signed(d)}</td>` : ''}
    </tr>`;
  };

  body.innerHTML =
    summaryRow('Income', m => m.income, true, true) +
    summaryRow('Spend', m => m.spend, false, true) +
    summaryRow('Saved', m => m.saved, true, true) +
    `<tr><td colspan="${full.length + (hasDelta ? 2 : 1)}" style="padding-top:10px;color:#6e7681;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.06em">Spending by category</td></tr>` +
    catKeys.map(c => {
      const d = hasDelta ? (latest.byCat[c] || 0) - (prior.byCat[c] || 0) : null;
      return `<tr>
        <td>${c}</td>
        ${full.map(m => `<td class="text-right">${metric[m].byCat[c] ? fmt(metric[m].byCat[c]) : '—'}</td>`).join('')}
        ${hasDelta ? `<td class="text-right" style="color:${colorUp(d, false)};font-weight:600">${d === 0 ? '—' : signed(d)}</td>` : ''}
      </tr>`;
    }).join('');
}

function pctFmt(v) {
  return ((Number(v || 0)) * 100).toFixed(1) + '%';
}

// The account holding employer RSUs — identified by having unvested positions,
// never by a hardcoded account name.
function employerEquityAccount(accounts) {
  return (accounts || []).find(a =>
    (a.positions || []).some(p => p.vested === false || /unvested/i.test(p.description || '')));
}

const NW_CLASS_COLORS = {
  'Cash': '#00B894', 'Retirement': '#FFC312', 'Employer Equity': '#00D2D3',
  'Marketable Securities': '#58a6ff', 'Unclassified': '#95A5A6', 'Liability': '#EA2027',
};
const NW_ACCENTS = ['#4ECDC4', '#FF9F43', '#9B59B6', '#54A0FF', '#00B894', '#FFC312', '#00D2D3', '#F9CA24', '#FD79A8', '#EA2027'];
let NW_ACCT_COLORS = {};
const nwColor = n => NW_CLASS_COLORS[n] || NW_ACCT_COLORS[n] || '#95A5A6';

function renderNetWorth() {
  const { accounts, total, total_gain_loss, by_account, analytics } = nwData;
  const totals = analytics?.totals || {};
  const exposures = analytics?.exposures || {};
  // Unvested employer RSUs aren't owned yet (vest over ~3 yrs), so the headline
  // "Owned Assets" excludes them and the By-Account / Employer-Equity views use
  // vested-only equity. The unvested figure is shown separately (pre-tax estimate).
  const unvested = totals.unvested_equity || 0;
  const owned = total - unvested;
  const denom = owned || totals.gross_investable_assets || totals.gross_assets || Math.abs(total) || 0;

  countUp(document.getElementById('nw-total'), owned);
  const unvestedNote = document.getElementById('nw-unvested-note');
  if (unvested > 0) {
    const yrs = unvestedVestingYears(accounts);
    unvestedNote.textContent = `+ ${fmt(unvested)} unvested RSUs (pre-tax, vests over ~${yrs} yrs)`;
  } else {
    unvestedNote.textContent = '';
  }

  // Liquid/Retirement/Vested come from the backend's asset-class analytics, so
  // they work for any account names (no personal labels in code).
  const byClass = exposures.by_asset_class || {};
  const liquid = byClass['Cash'] || 0;
  const retirement = byClass['Retirement'] || 0;
  const vested = totals.vested_equity || 0;

  countUp(document.getElementById('nw-liquid'), liquid);
  countUp(document.getElementById('nw-retirement'), retirement);
  countUp(document.getElementById('nw-vested'), vested);

  NW_ACCT_COLORS = {};
  Object.keys(by_account || {}).sort().forEach((n, i) => { NW_ACCT_COLORS[n] = NW_ACCENTS[i % NW_ACCENTS.length]; });
  // Employer Equity reflects only vested (owned) RSUs — unvested equity isn't owned
  // yet, so it's excluded here just as it is from the headline net worth and the
  // account-level donut. Reduce the asset-class map before the cards and donut.
  const byAssetClass = { ...(exposures.by_asset_class || {}) };
  if (byAssetClass['Employer Equity'] != null) {
    byAssetClass['Employer Equity'] = Math.max(byAssetClass['Employer Equity'] - unvested, 0);
  }
  // Asset-class concentration cards mirror the donut slices: one card per positive
  // asset class, % of the same class total the donut sums to, accent-colored to its
  // slice. Laid out in balanced near-square rows (4→2×2, 6→3×2, 9→3×3, 5→3+2) with
  // each card flex-filling its row, so the block stays uniform with no empty gaps.
  const classEntries = Object.entries(byAssetClass)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  const classTotal = classEntries.reduce((s, [, v]) => s + v, 0) || 1;
  const nClasses = classEntries.length;
  let classRows = Math.max(1, Math.round(Math.sqrt(nClasses)));
  if (classRows > 1 && nClasses / classRows < 2) classRows--;   // no lonely single-card row
  let classIdx = 0;
  document.getElementById('nw-class-cards').innerHTML =
    Array.from({ length: classRows }, (_, r) => {
      const count = Math.floor(nClasses / classRows) + (r < nClasses % classRows ? 1 : 0);
      const cards = classEntries.slice(classIdx, classIdx + count).map(([name, val]) => `
        <div class="card-panel stat-card" style="flex:1 1 0;border-top:3px solid ${nwColor(name)}">
          <div class="stat-value">${(val / classTotal * 100).toFixed(1)}%</div>
          <div class="stat-label">${name}</div>
        </div>`).join('');
      classIdx += count;
      return `<div class="flex gap-4">${cards}</div>`;
    }).join('');

  // Reduce employer equity to its vested-only value so the donut/bars reflect owned assets.
  const eqAcct = employerEquityAccount(accounts);
  const byAccountVested = { ...by_account };
  if (eqAcct && byAccountVested[eqAcct.account_name] != null) {
    byAccountVested[eqAcct.account_name] = Math.max(byAccountVested[eqAcct.account_name] - unvested, 0);
  }
  // Only positive balances on the By-Account donut and balance bars — a liability
  // (e.g. CC Liability) graphs as a confusing negative slice/bar. It's still in the
  // All Positions table and already nets into the headline net worth.
  const acctNames = Object.keys(byAccountVested).filter(n => byAccountVested[n] > 0);
  const acctVals = acctNames.map(n => byAccountVested[n]);
  const colors = acctNames.map(n => nwColor(n));

  // % of total assets (positive balances) — drawn on the donut slices and at the
  // end of each balance bar. Ratios reveal no dollar amounts, so they show even
  // when Conceal is on. Liabilities read as a negative share.
  const acctTotal = acctVals.reduce((s, v) => s + (v > 0 ? v : 0), 0) || 1;
  const donutPctPlugin = {
    id: 'donutPct',
    afterDatasetsDraw(chart) {
      const { ctx } = chart, meta = chart.getDatasetMeta(0), data = chart.data.datasets[0].data;
      ctx.save();
      ctx.font = "600 10px Inter, system-ui, sans-serif";
      ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      meta.data.forEach((arc, i) => {
        const pct = data[i] / acctTotal * 100;
        if (pct < 4) return;  // skip slivers to avoid clutter
        const p = arc.tooltipPosition();
        ctx.fillText(Math.round(pct) + '%', p.x, p.y);
      });
      ctx.restore();
    }
  };
  const barPctPlugin = {
    id: 'barPct',
    afterDatasetsDraw(chart) {
      const { ctx } = chart, meta = chart.getDatasetMeta(0), data = chart.data.datasets[0].data;
      ctx.save();
      ctx.font = "600 10px Inter, system-ui, sans-serif";
      ctx.fillStyle = "#c9d1d9"; ctx.textBaseline = "middle";
      meta.data.forEach((bar, i) => {
        const v = data[i], pos = v >= 0;
        ctx.textAlign = pos ? 'left' : 'right';
        ctx.fillText((v / acctTotal * 100).toFixed(1) + '%', bar.x + (pos ? 6 : -6), bar.y);
      });
      ctx.restore();
    }
  };

  if (nwDonutChart) nwDonutChart.destroy();
  nwDonutChart = new Chart(document.getElementById('nw-donut'), {
    type: 'doughnut',
    data: { labels: acctNames, datasets: [{ data: acctVals, backgroundColor: colors, borderWidth: 2, borderColor: '#1c1f26' }] },
    plugins: [donutPctPlugin],
    options: {
      cutout: '62%',
      plugins: {
        legend: { position: 'bottom', labels: { color: '#c9d1d9', font: { size: 10.5 }, boxWidth: 11, padding: 10 } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${tipMoney(ctx.raw)} (${((ctx.raw/owned)*100).toFixed(1)}%)` } }
      }
    }
  });

  if (nwBarsChart) nwBarsChart.destroy();
  nwBarsChart = new Chart(document.getElementById('nw-bars'), {
    type: 'bar',
    data: { labels: acctNames, datasets: [{ data: acctVals, backgroundColor: colors.map(c => c + 'bb'), borderRadius: 4 }] },
    plugins: [barPctPlugin],
    options: {
      indexAxis: 'y',
      layout: { padding: { right: 44 } },  // room for the % labels past the bar ends
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ` ${tipMoney(ctx.raw)}` } } },
      scales: {
        x: { ticks: { color: '#8b949e', callback: axisMoney }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#c9d1d9', font: { size: 11 } }, grid: { display: false } }
      }
    }
  });

  const classNames = Object.keys(byAssetClass).filter(k => byAssetClass[k] > 0);
  const classVals = classNames.map(k => byAssetClass[k]);
  if (nwAssetClassChart) nwAssetClassChart.destroy();
  nwAssetClassChart = new Chart(document.getElementById('nw-asset-class'), {
    type: 'doughnut',
    data: { labels: classNames, datasets: [{ data: classVals, backgroundColor: classNames.map(n => nwColor(n)), borderWidth: 2, borderColor: '#1c1f26' }] },
    options: {
      cutout: '62%',
      plugins: {
        legend: { position: 'bottom', labels: { color: '#c9d1d9', font: { size: 10.5 }, boxWidth: 11, padding: 10 } },
        // % matches the concentration cards above — share of the class total the donut sums to.
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${tipMoney(ctx.raw)} (${((ctx.raw/classTotal)*100).toFixed(1)}%)` } }
      }
    }
  });

  const top = (analytics?.top_positions || []).slice(0, 10).reverse();
  if (nwTopPositionsChart) nwTopPositionsChart.destroy();
  nwTopPositionsChart = new Chart(document.getElementById('nw-top-positions'), {
    type: 'bar',
    data: {
      labels: top.map(p => p.symbol),
      datasets: [{ data: top.map(p => p.value), backgroundColor: top.map(p => (NW_CLASS_COLORS[p.asset_class] || '#58a6ff') + 'bb'), borderRadius: 4 }]
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => {
        const p = top[ctx.dataIndex];
        return ` ${tipMoney(ctx.raw)} · ${pctFmt(p.pct)} · ${p.asset_class}${p.is_account_proxy ? ' · account-level' : ''}`;
      } } } },
      scales: {
        x: { ticks: { color: '#8b949e', callback: axisMoney }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#c9d1d9', font: { size: 11 } }, grid: { display: false } }
      }
    }
  });

  renderVestingSchedule(accounts);
  renderNetWorthPositions(accounts, analytics);
  renderFidelityPositions(accounts);
  renderPrincipalPositions(accounts);
  loadPositionNews();
  renderDataQuality(analytics?.data_quality || {});
}

// Years until the last unvested tranche fully vests (fallback 3 if no schedule).
function unvestedVestingYears(accounts) {
  const sw = employerEquityAccount(accounts);
  const sched = (sw?.vesting_schedule || []).filter(t => t.date);
  if (!sched.length) return 3;
  const last = sched.map(t => new Date(t.date)).sort((a, b) => b - a)[0];
  const yrs = (last - new Date()) / (365.25 * 24 * 3600 * 1000);
  return Math.max(1, Math.round(yrs));
}

function renderVestingSchedule(accounts) {
  const panel = document.getElementById('nw-vesting-panel');
  const body = document.getElementById('nw-vesting-body');
  const subtitle = document.getElementById('nw-vesting-subtitle');
  const totalEl = document.getElementById('nw-vesting-total');
  const sw = employerEquityAccount(accounts);
  const unvested = (sw?.positions || []).filter(p => /unvested/i.test(p.description || ''))
    .reduce((s, p) => s + Number(p.value || 0), 0);

  // No employer equity at all → hide the section entirely.
  if (!sw || unvested <= 0) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const sched = (sw.vesting_schedule || []).filter(t => t.date).sort((a, b) => new Date(a.date) - new Date(b.date));

  // Fallback before the scraper ships per-tranche data: single summary line.
  if (!sched.length) {
    document.getElementById('nw-vesting-chart-wrap').style.display = 'none';
    subtitle.textContent = 'Employer RSUs — pre-tax estimate; not yet owned.';
    totalEl.innerHTML = `<strong style="color:#e1e4e8">${fmt(unvested)}</strong><br>unvested`;
    body.innerHTML = `
      <div class="nw-small" style="padding:8px 0">
        ${fmt(unvested)} of unvested RSUs vesting over ~${unvestedVestingYears(accounts)} years.
        Per-tranche dates aren't available yet — they populate automatically once the
        Shareworks scrape reads your grant pages.
      </div>`;
    return;
  }

  let cumulative = 0;
  const rows = sched.map(t => {
    const val = Number(t.value || 0);
    cumulative += val;
    return `
      <tr>
        <td style="color:#c9d1d9;font-size:0.82rem;white-space:nowrap">${t.date}</td>
        <td class="text-right" style="color:#8b949e;font-size:0.8rem">${t.shares == null ? '—' : Number(t.shares).toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
        <td class="text-right" style="color:#e1e4e8;font-weight:600">${fmt(val)}</td>
        <td class="text-right" style="color:#6e7681;font-size:0.8rem">${fmt(cumulative)}</td>
      </tr>`;
  }).join('');

  // Monthly bar chart — shares vesting per month, biggest months highlighted.
  const byMonth = {};
  sched.forEach(t => {
    const m = t.date.slice(0, 7);
    (byMonth[m] = byMonth[m] || { shares: 0, value: 0 });
    byMonth[m].shares += Number(t.shares || 0);
    byMonth[m].value += Number(t.value || 0);
  });
  const months = Object.keys(byMonth).sort();
  const monthShares = months.map(m => byMonth[m].shares);
  const maxShares = Math.max(...monthShares, 0);
  const labels = months.map(m => { const [y, mo] = m.split('-'); return new Date(y, mo - 1).toLocaleString('en-US', { month: 'short' }) + " '" + y.slice(2); });
  const barColors = monthShares.map(s => s >= maxShares * 0.999 ? '#00D2D3' : (s >= maxShares * 0.8 ? '#1b8e8e' : '#2c4a4a'));

  document.getElementById('nw-vesting-chart-wrap').style.display = '';
  if (nwVestingChart) nwVestingChart.destroy();
  nwVestingChart = new Chart(document.getElementById('nw-vesting-chart'), {
    type: 'bar',
    data: { labels, datasets: [{ data: monthShares, backgroundColor: barColors, borderRadius: 3 }] },
    options: {
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { title: items => { const [y, mo] = months[items[0].dataIndex].split('-'); return new Date(y, mo - 1).toLocaleString('en-US', { month: 'long', year: 'numeric' }); },
          label: ctx => { const v = byMonth[months[ctx.dataIndex]]; return ` ${v.shares.toLocaleString()} shares · ${tipMoney(v.value)}`; } } }
      },
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: 10 }, maxRotation: 0, autoSkip: true }, grid: { display: false } },
        y: { ticks: { color: '#8b949e', precision: 0 }, grid: { color: '#21262d' }, title: { display: true, text: 'shares vesting', color: '#6e7681', font: { size: 10 } } }
      }
    }
  });
  const fmtM = m => { const [y, mo] = m.split('-'); return new Date(y, mo - 1).toLocaleString('en-US', { month: 'short', year: 'numeric' }); };
  const ranked = months.map(m => [m, byMonth[m].shares]).sort((a, b) => b[1] - a[1]).slice(0, 3);
  document.getElementById('nw-vesting-highlight').innerHTML =
    'Biggest months: ' + ranked.map(([m, s]) => `<strong style="color:#c9d1d9">${fmtM(m)}</strong> (${s.toLocaleString()} sh)`).join('  ·  ');

  subtitle.textContent = `Employer RSUs — pre-tax estimate; ${sched.length} tranches through ${sched[sched.length - 1].date}.`;
  totalEl.innerHTML = `<strong style="color:#e1e4e8">${fmt(cumulative)}</strong><br>${sched.length} tranches`;
  body.innerHTML = `
    <div style="max-height:340px;overflow-y:auto">
      <table class="table table-dark mb-0" style="border-color:#21262d">
        <thead><tr>
          <th>Vest date</th>
          <th class="text-right">Shares</th>
          <th class="text-right">Value (pre-tax)</th>
          <th class="text-right">Cumulative</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderDataQuality(dq) {
  const notes = [...(dq.granularity_notes || [])];
  if ((dq.unknown_positions || []).length) {
    notes.push(`${fmt(dq.unknown_value || 0)} (${pctFmt(dq.unknown_pct || 0)}) is unclassified.`);
  }
  const el = document.getElementById('nw-data-quality-footnote');
  if (!el) return;
  if (!notes.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<span style="color:#6e7681">Notes:</span> ` + notes.join(' · ');
}

function inferPositionMeta(a, p, analytics) {
  const found = (analytics?.top_positions || []).find(x =>
    x.account_name === a.account_name && x.symbol === String(p.symbol || 'UNKNOWN').toUpperCase().replaceAll(' ', '_') && Math.abs(x.value - p.value) < 0.01
  );
  if (found) return found;
  const symbol = String(p.symbol || 'UNKNOWN').toUpperCase().replaceAll(' ', '_');
  const desc = `${a.account_name} ${a.source || ''} ${symbol} ${p.description || ''}`.toLowerCase();
  let asset_class = 'Unclassified';
  if ((p.value || 0) < 0 || symbol === 'CC_DEBT' || desc.includes('credit card')) asset_class = 'Liability';
  else if (desc.includes('401') || desc.includes('ira') || desc.includes('principal')) asset_class = 'Retirement';
  else if (symbol.endsWith('RSU') || desc.includes('shareworks') || desc.includes('rsu')) asset_class = 'Employer Equity';
  else if (['CHECKING', 'SAVINGS', 'HYSA', 'CASH'].includes(symbol) || desc.includes('checking') || desc.includes('savings')) asset_class = 'Cash';
  else if (symbol !== 'UNKNOWN' && symbol !== 'ACCT') asset_class = 'Marketable Securities';
  return { symbol, asset_class, vested: !(desc.includes('unvested') || symbol.endsWith('RSU')), pct: (p.value || 0) / ((analytics?.totals?.gross_investable_assets || Math.abs(nwData.total) || 1)), is_account_proxy: false };
}

function renderNetWorthPositions(accounts, analytics) {
  const denom = analytics?.totals?.gross_investable_assets || analytics?.totals?.gross_assets || Math.abs(nwData.total) || 1;
  const rows = accounts.flatMap(a =>
    (a.positions || []).map(p => {
      const meta = inferPositionMeta(a, p, analytics);
      const staleNote = a.note ? `<span title="${a.note}" style="color:#778ca3;font-size:0.68rem;margin-left:4px">⚠</span>` : '';
      const proxyNote = meta.is_account_proxy ? `<span title="Account-level proxy; underlying holdings unavailable" style="color:#778ca3;font-size:0.68rem;margin-left:4px">proxy</span>` : '';
      const vesting = meta.asset_class === 'Liability' ? '—' : (meta.vested ? 'Vested/Available' : 'Unvested');
      return `
        <tr>
          <td style="color:#8b949e;font-size:0.75rem;white-space:nowrap">${a.account_name}${staleNote}</td>
          <td style="font-weight:600;color:#e1e4e8">${meta.symbol}${proxyNote}</td>
          <td style="color:#8b949e;font-size:0.8rem">${p.description || ''}</td>
          <td><span class="cat-badge" style="background:#58a6ff22;color:#58a6ff">${meta.asset_class}</span></td>
          <td style="color:#8b949e;font-size:0.75rem;white-space:nowrap">${vesting}</td>
          <td class="text-right" style="color:#e1e4e8;font-weight:500">${fmt(p.value)}</td>
          <td class="text-right" style="color:#6e7681;font-size:0.8rem">${pctFmt((p.value || 0) / denom)}</td>
        </tr>
      `;
    })
  );
  document.getElementById('nw-positions-body').innerHTML = rows.join('');
}

function isFidelityAccount(a) {
  const txt = `${a.account_number || ''} ${a.account_name || ''} ${a.source || ''}`.toLowerCase();
  return txt.includes('fidelity') || ['individual - tod', 'roth ira', 'rollover ira'].includes(String(a.account_name || '').toLowerCase());
}

function isPrincipalAccount(a) {
  const txt = `${a.account_number || ''} ${a.account_name || ''} ${a.source || ''}`.toLowerCase();
  return txt.includes('principal') || txt.includes('401');
}

function renderFidelityPositions(accounts) {
  renderHoldingsSection(accounts, {
    filterFn: isFidelityAccount,
    label: 'Fidelity',
    bodyId: 'nw-fidelity-positions-body',
    subtitleId: 'nw-fidelity-positions-subtitle',
    totalId: 'nw-fidelity-positions-total',
    relinkHint: 'To populate this automatically, relink Fidelity through Plaid with investment holdings permission, then start the app again. After that, <code style="color:#79c0ff">./start.sh</code> refreshes positions on startup.',
  });
}

function renderPrincipalPositions(accounts) {
  renderHoldingsSection(accounts, {
    filterFn: isPrincipalAccount,
    label: 'Principal',
    bodyId: 'nw-principal-positions-body',
    subtitleId: 'nw-principal-positions-subtitle',
    totalId: 'nw-principal-positions-total',
    relinkHint: 'To populate this automatically, relink Principal through Plaid with investment holdings permission, then start the app again. After that, <code style="color:#79c0ff">./start.sh</code> refreshes positions on startup.',
  });
}

function renderHoldingsSection(accounts, { filterFn, label, bodyId, subtitleId, totalId, relinkHint }) {
  const body = document.getElementById(bodyId);
  const subtitle = document.getElementById(subtitleId);
  const totalEl = document.getElementById(totalId);
  const matchedAccounts = (accounts || []).filter(filterFn);
  const liveAccounts = matchedAccounts.filter(a => a.positions_source === 'Plaid Investments' && (a.positions || []).length);

  if (!liveAccounts.length) {
    const proxyTotal = matchedAccounts.reduce((s, a) => s + Number(a.current_value || 0), 0);
    subtitle.textContent = `${label} is linked, but detailed holdings are not available yet.`;
    totalEl.textContent = proxyTotal ? `Account total ${fmt(proxyTotal)}` : '—';
    body.innerHTML = `
      <tr>
        <td colspan="8" style="color:#8b949e;font-size:0.82rem;padding:16px">${relinkHint}</td>
      </tr>
    `;
    return;
  }

  const rows = liveAccounts.flatMap(a =>
    (a.positions || []).map(p => ({
      account: a.account_name,
      accountValue: Number(a.current_value || 0),
      snapshot: a.snapshot_date,
      symbol: p.symbol || 'UNKNOWN',
      description: p.description || '',
      quantity: p.quantity,
      price: p.price,
      price_as_of: p.price_as_of,
      value: Number(p.value || 0),
      gain: p.total_gain_loss,
    }))
  ).sort((a, b) => String(a.account).localeCompare(String(b.account)) || Math.abs(b.value) - Math.abs(a.value));

  const total = liveAccounts.reduce((s, a) => s + Number(a.current_value || 0), 0);
  const latest = liveAccounts.map(a => a.snapshot_date).filter(Boolean).sort().slice(-1)[0];
  subtitle.textContent = latest ? `Automatically refreshed from Plaid Investments · as of ${latest}` : 'Automatically refreshed from Plaid Investments on startup.';
  totalEl.innerHTML = `<strong style="color:#e1e4e8">${fmt(total)}</strong><br>${rows.length} holdings`;

  body.innerHTML = rows.map(r => {
    const pctAccount = r.accountValue ? r.value / r.accountValue : 0;
    const gainColor = Number(r.gain || 0) >= 0 ? '#3fb950' : '#f8a5a5';
    const priceTitle = r.price_as_of ? ` title="Price as of ${r.price_as_of}"` : '';
    return `
      <tr>
        <td style="color:#8b949e;font-size:0.75rem;white-space:nowrap">${r.account}</td>
        <td style="font-weight:700;color:#e1e4e8">${r.symbol}</td>
        <td style="color:#8b949e;font-size:0.8rem;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.description}</td>
        <td class="text-right money" style="color:#8b949e;font-size:0.78rem">${r.quantity == null ? '—' : Number(r.quantity).toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
        <td class="text-right" style="color:#8b949e;font-size:0.78rem"${priceTitle}>${r.price == null ? '—' : fmt(r.price)}</td>
        <td class="text-right" style="color:#e1e4e8;font-weight:600">${fmt(r.value)}</td>
        <td class="text-right" style="color:${r.gain == null ? '#6e7681' : gainColor};font-size:0.8rem">${r.gain == null ? '—' : fmt(r.gain)}</td>
        <td class="text-right" style="color:#6e7681;font-size:0.8rem">${pctFmt(pctAccount)}</td>
      </tr>
    `;
  }).join('');
}

async function loadPositionNews() {
  const body = document.getElementById('nw-position-news-body');
  const subtitle = document.getElementById('nw-position-news-subtitle');
  const meta = document.getElementById('nw-position-news-meta');
  body.innerHTML = '<div class="nw-small">Loading headlines...</div>';
  try {
    const resp = await fetch('/api/position-news');
    const news = await resp.json();
    renderPositionNews(news);
  } catch (err) {
    subtitle.textContent = 'Could not load cached position news.';
    meta.textContent = '—';
    body.innerHTML = '<div class="nw-small">Run ./start.sh to refresh news.</div>';
  }
}

function newsHost(link) {
  try {
    return new URL(link).hostname.replace(/^www\\./, '');
  } catch {
    return '';
  }
}

function relativeTime(iso) {
  if (!iso) return '';
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return '';
  const diff = Date.now() - ts;
  const mins = Math.max(1, Math.round(diff / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

function escapeHtml(value) {
  return String(value || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function renderPositionNews(news) {
  const body = document.getElementById('nw-position-news-body');
  const subtitle = document.getElementById('nw-position-news-subtitle');
  const meta = document.getElementById('nw-position-news-meta');
  const stories = news?.stories || [];
  const symbols = news?.symbols || [];

  subtitle.textContent = news?.as_of
    ? `Yahoo Finance RSS · refreshed ${relativeTime(news.as_of)}`
    : 'Run ./start.sh to fetch headlines for current holdings.';
  meta.innerHTML = symbols.length
    ? `<strong style="color:#e1e4e8">${symbols.length}</strong> symbols<br>${stories.length} stories`
    : '—';

  if (!stories.length) {
    body.innerHTML = `
      <div class="nw-small" style="padding:12px 0">
        No position news cached yet. Once holdings access is granted, <code style="color:#79c0ff">./start.sh</code>
        will refresh headlines automatically.
      </div>
    `;
    return;
  }

  body.innerHTML = `
    <div class="grid grid-cols-12 gap-2">
      ${stories.slice(0, 12).map(story => `
        <div class="col-span-12 lg:col-span-6">
          <a href="${escapeHtml(story.link)}" target="_blank" rel="noopener noreferrer" style="display:block;text-decoration:none;background:#161b22;border:1px solid #21262d;border-radius:10px;padding:12px 14px;height:100%">
            <div class="flex justify-between items-start gap-2 mb-1">
              <span class="cat-badge" style="background:#58a6ff22;color:#58a6ff">${escapeHtml(story.symbol)}</span>
              <span style="color:#6e7681;font-size:0.72rem;white-space:nowrap">${relativeTime(story.published_at)}</span>
            </div>
            <div style="color:#e1e4e8;font-size:0.86rem;font-weight:650;line-height:1.35">${escapeHtml(story.title)}</div>
            <div style="color:#6e7681;font-size:0.72rem;margin-top:6px">${escapeHtml(story.source || newsHost(story.link) || 'Yahoo Finance')} · ${escapeHtml(newsHost(story.link))}</div>
          </a>
        </div>
      `).join('')}
    </div>
  `;
}
