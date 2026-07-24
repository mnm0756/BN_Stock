const state = {
  snapshot: null,
  selectedSymbol: null,
  activeView: "scanner",
  notifications: localStorage.getItem("carry-notifications") === "on",
  lastAlerted: new Set(),
  saveTimer: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const money = (value, digits = 2) => {
  const number = Number(value || 0);
  return `${number < 0 ? "-" : ""}$${Math.abs(number).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
};

const price = (value) => {
  const number = Number(value || 0);
  const digits = number >= 100 ? 2 : number >= 1 ? 4 : 6;
  return number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: digits });
};

const percent = (value, digits = 2) => `${(Number(value || 0) * 100).toFixed(digits)}%`;
const bps = (value) => `${Number(value || 0) >= 0 ? "+" : ""}${Number(value || 0).toFixed(1)} bps`;
const valueClass = (value) => (Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "neutral");
const signedMoney = (value) => `${Number(value || 0) >= 0 ? "+" : "-"} ${money(Math.abs(Number(value || 0)))}`;
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));

const FEE_SOURCES = [
  {
    title: "Binance USD-M 永续",
    status: "已计入",
    detail: "默认按普通用户 USDT 永续 Maker 0.0200%、Taker 0.0500%，可在设置中改为你的账户费率。",
    href: "https://www.binance.com/en/fee/futureFee",
  },
  {
    title: "OKX USDT 永续",
    status: "已计入",
    detail: "默认按普通用户永续 Maker 0.0200%、Taker 0.0500%，可在设置中改为你的账户费率。",
    href: "https://www.okx.com/fees",
  },
  {
    title: "资金费数据",
    status: "实时读取",
    detail: "Binance 使用 premiumIndex/fundingRate；OKX 使用 funding-rate/funding-rate-history。",
    href: "https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data",
  },
  {
    title: "划转/资金占用",
    status: "需手动预留",
    detail: "跨所转账、借币、资金闲置、强平缓冲和账户等级差异不会自动读取，请用额外成本预留。",
    href: "https://www.okx.com/help/fee-details",
  },
];

function binanceFuturesUrl(item) {
  return `https://www.binance.com/en/futures/${encodeURIComponent(item.symbol)}`;
}

function okxSwapUrl(item) {
  return `https://www.okx.com/trade-swap/${encodeURIComponent((item.okx_inst_id || `${item.ticker}-USDT-SWAP`).toLowerCase())}`;
}

function marketLinks(item, tone = "compact") {
  const labelClass = tone === "full" ? " full" : "";
  return `
    <div class="market-links${labelClass}">
      <a class="market-link futures" href="${binanceFuturesUrl(item)}" target="_blank" rel="noreferrer" title="打开 Binance 合约 ${escapeHtml(item.symbol)}">
        <i data-lucide="external-link"></i><span>Binance</span>
      </a>
      <a class="market-link stock" href="${okxSwapUrl(item)}" target="_blank" rel="noreferrer" title="打开 OKX 合约 ${escapeHtml(item.okx_inst_id || item.symbol)}">
        <i data-lucide="external-link"></i><span>OKX</span>
      </a>
    </div>
  `;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function initIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
}

function toast(message, type = "normal") {
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "error" : ""}`;
  node.textContent = message;
  $("#toast-region").appendChild(node);
  setTimeout(() => node.remove(), 3600);
}

function formatLocalTime(value, withSeconds = true) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hour12: false,
  }).format(new Date(value));
}

function summarizeSourceError(error) {
  const message = String(error || "");
  if (message.includes("451") || message.toLowerCase().includes("restricted location")) {
    return "交易所公共接口返回 451，当前网络或地区不可用";
  }
  if (message.toLowerCase().includes("timeout")) {
    return "连接交易所公共接口超时";
  }
  const firstLine = message.split("\n")[0].trim();
  return firstLine.length > 150 ? `${firstLine.slice(0, 147)}...` : firstLine;
}

function applySnapshot(snapshot) {
  state.snapshot = snapshot;
  const opportunities = snapshot.opportunities || [];
  if (!state.selectedSymbol || !opportunities.some((item) => item.symbol === state.selectedSymbol)) {
    state.selectedSymbol = opportunities[0]?.symbol || null;
  }
  syncControlsFromSettings(snapshot.settings || {});
  renderStatus();
  renderKpis();
  renderOpportunities();
  renderDetail();
  renderPositions();
  maybeNotify();
  initIcons();
}

function renderStatus() {
  const { source, updated_at: updatedAt, error } = state.snapshot;
  const labels = { live: "实时行情", stale: "实时延迟", demo: "演示数据", error: "连接失败", none: "正在连接" };
  $("#source-label").textContent = labels[source] || "正在连接";
  $("#updated-label").textContent = updatedAt ? `${formatLocalTime(updatedAt)} 更新` : "--";
  $("#source-dot").className = `status-dot ${source === "live" ? "live" : source === "stale" ? "stale" : source === "demo" ? "demo" : "error"}`;
  const banner = $("#data-banner");
  if (source === "demo" || source === "error" || source === "stale") {
    banner.classList.remove("hidden");
    if (source === "stale") {
      $("#data-banner-text").textContent = `实时刷新暂时失败，当前保留上一份真实行情，请核对 Binance 和 OKX 页面后再交易。原因：${summarizeSourceError(error) || "未知错误"}`;
    } else if (source === "demo") {
      $("#data-banner-text").textContent = `当前显示演示数据，不可用于交易判断。${error ? `实时接口原因：${summarizeSourceError(error)}` : "已手动启用演示模式。"}`;
    } else {
      $("#data-banner-text").textContent = `实时行情不可用：${summarizeSourceError(error) || "未知错误"}`;
    }
  } else {
    banner.classList.add("hidden");
  }
}

function renderKpis() {
  const summary = state.snapshot.summary || {};
  const settings = state.snapshot.settings || {};
  $("#kpi-market-count").textContent = summary.market_count ?? 0;
  $("#kpi-profitable-count").textContent = `${summary.profitable_count ?? 0} 个费用后为正`;
  $("#kpi-best-annualized").textContent = percent(summary.best_annualized);
  $("#kpi-best-annualized").className = valueClass(summary.best_annualized);
  $("#kpi-best-symbol").textContent = summary.best_symbol || "--";
  $("#kpi-best-profit").textContent = money(summary.best_projected_profit);
  $("#kpi-best-profit").className = valueClass(summary.best_projected_profit);
  $("#kpi-holding-days").textContent = `${settings.holding_days || 0} 天，已扣预估成本`;
  $("#kpi-position-pnl").textContent = money(summary.position_net_pnl);
  $("#kpi-position-pnl").className = valueClass(summary.position_net_pnl);
  $("#kpi-position-count").textContent = `${summary.position_count || 0} 个组合`;
  $("#position-count-badge").textContent = summary.position_count || 0;
  updateCountdown();
}

function updateCountdown() {
  if (!state.snapshot) return;
  const timestamp = Number(state.snapshot.summary?.next_funding_time || 0);
  if (!timestamp) {
    $("#kpi-countdown").textContent = "--:--:--";
    $("#kpi-funding-time").textContent = "--";
    return;
  }
  const remaining = Math.max(0, Math.floor((timestamp - Date.now()) / 1000));
  const hours = String(Math.floor(remaining / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((remaining % 3600) / 60)).padStart(2, "0");
  const seconds = String(remaining % 60).padStart(2, "0");
  $("#kpi-countdown").textContent = `${hours}:${minutes}:${seconds}`;
  $("#kpi-funding-time").textContent = formatLocalTime(timestamp, false);
}

function filteredOpportunities() {
  if (!state.snapshot) return [];
  const query = $("#search-input").value.trim().toLowerCase();
  const minAnnualized = Number($("#min-annualized").value) / 100;
  return state.snapshot.opportunities.filter((item) => {
    const matchesQuery = !query || item.symbol.toLowerCase().includes(query) || item.name.toLowerCase().includes(query);
    return matchesQuery && item.annualized_current >= minAnnualized;
  });
}

function riskTag(item) {
  const flags = item.risk_flags || [];
  const main = flags[0] || "常规";
  const tone = main === "常规" ? "normal" : main === "负资金费" ? "danger" : "warning";
  return `<span class="risk-tag ${tone}">${escapeHtml(main)}</span>`;
}

function projectionCost(projection) {
  return Number(projection?.total_cost ?? (Number(projection?.opening_cost || 0) + Number(projection?.closing_cost || 0)));
}

function costBreakdown(projection) {
  const total = projectionCost(projection);
  const hasCrossDetail = projection && Object.hasOwn(projection, "binance_open_fee");
  const hasDetail = projection && Object.hasOwn(projection, "spot_open_fee");
  const binance = hasCrossDetail ? Number(projection.binance_open_fee || 0) + Number(projection.binance_close_fee || 0) : 0;
  const okx = hasCrossDetail ? Number(projection.okx_open_fee || 0) + Number(projection.okx_close_fee || 0) : 0;
  const spot = hasCrossDetail ? binance : hasDetail ? Number(projection.spot_open_fee || 0) + Number(projection.spot_close_fee || 0) : 0;
  const perp = hasCrossDetail ? okx : hasDetail ? Number(projection.perp_open_fee || 0) + Number(projection.perp_close_fee || 0) : 0;
  const detail = hasCrossDetail || hasDetail;
  const slippage = detail ? Number(projection.slippage_open || 0) + Number(projection.slippage_close || 0) : 0;
  const extra = detail ? Number(projection.extra_open_fee || 0) + Number(projection.extra_close_fee || 0) : 0;
  return {
    total,
    binance,
    okx,
    spot,
    perp,
    slippage,
    extra,
    costPctOfHedge: Number(projection?.cost_pct_of_hedge || 0),
    costPctOfCapital: Number(projection?.cost_pct_of_capital || 0),
    hasDetail: detail,
  };
}

function renderFeeAudit() {
  return FEE_SOURCES.map((item) => `
    <div class="fee-audit-row">
      <span>${escapeHtml(item.title)}</span>
      <strong>${escapeHtml(item.status)}</strong>
      <em>${escapeHtml(item.detail)}</em>
      <a href="${escapeHtml(item.href)}" target="_blank" rel="noreferrer">官方来源</a>
    </div>
  `).join("");
}

function renderOpportunities() {
  const rows = filteredOpportunities();
  $("#visible-count").textContent = `${rows.length} 个结果`;
  $("#empty-opportunities").classList.toggle("hidden", rows.length > 0);
  $(".table-wrap").classList.toggle("hidden", rows.length === 0);
  if (rows.length === 0) {
    const allRows = state.snapshot?.opportunities || [];
    const minAnnualized = Number($("#min-annualized").value) / 100;
    const bestAnnualized = allRows.reduce((best, item) => Math.max(best, Number(item.annualized_current || 0)), 0);
    $("#empty-opportunities span").textContent = allRows.length
      ? `当前筛选为不低于 ${percent(minAnnualized, 0)}，最高只有 ${percent(bestAnnualized)}`
      : "等待行情更新";
  }
  $("#opportunity-body").innerHTML = rows.map((item) => {
    const costs = costBreakdown(item.projection);
    return `
    <tr class="opportunity-row ${item.symbol === state.selectedSymbol ? "selected" : ""}" data-symbol="${escapeHtml(item.symbol)}">
      <td>
        <div class="symbol-cell">
          <span class="ticker-mark">${escapeHtml(item.ticker.slice(0, 4))}</span>
          <span><strong>${escapeHtml(item.ticker)}</strong><small>${escapeHtml(item.name)}</small>${marketLinks(item)}</span>
        </div>
      </td>
      <td><div class="value-stack"><strong class="${valueClass(item.funding_rate)}">${percent(item.funding_rate, 4)}</strong><small>当前年化 ${percent(item.annualized_current)}</small></div></td>
      <td><div class="value-stack"><strong>${percent(item.binance_funding_rate, 4)} / ${percent(item.okx_funding_rate, 4)}</strong><small>近7日差 ${percent(item.annualized_7d)}</small></div></td>
      <td><div class="value-stack"><strong>${escapeHtml(item.short_exchange)} 空 / ${escapeHtml(item.long_exchange)} 多</strong><small>收 ${percent(item.short_rate, 4)}，付 ${percent(item.long_rate, 4)}</small></div></td>
      <td><div class="value-stack"><strong>${price(item.short_bid)} / ${price(item.long_ask)}</strong><small>空方 Bid / 多方 Ask，${bps(item.entry_basis_bps)}</small></div></td>
      <td><div class="value-stack"><strong class="negative">-${money(costs.total)}</strong><small>BN ${money(costs.binance)} / OKX ${money(costs.okx)} / 滑 ${money(costs.slippage)}</small><small>其 ${money(costs.extra)} / ${percent(costs.costPctOfHedge)} 名义</small></div></td>
      <td><div class="value-stack"><strong class="${valueClass(item.projection.net_profit)}">${money(item.projection.net_profit)}</strong><small>${percent(item.projection.return_pct)} / 持有期</small></div></td>
      <td>${riskTag(item)}</td>
    </tr>
  `;
  }).join("");
  $$(".market-link").forEach((link) => link.addEventListener("click", (event) => event.stopPropagation()));
  $$(".opportunity-row").forEach((row) => row.addEventListener("click", () => {
    state.selectedSymbol = row.dataset.symbol;
    renderOpportunities();
    renderDetail();
    initIcons();
  }));
}

function renderDetail() {
  const item = state.snapshot?.opportunities.find((row) => row.symbol === state.selectedSymbol);
  if (!item) {
    $("#detail-panel").innerHTML = `<div class="empty-detail"><i data-lucide="mouse-pointer-2"></i><strong>选择一条机会</strong><span>查看资金费、价差和费用拆解</span></div>`;
    return;
  }
  const projection = item.projection;
  const costs = costBreakdown(projection);
  const settings = state.snapshot?.settings || {};
  const binanceFeeRate = settings.execution_mode === "maker" ? settings.binance_maker_fee : settings.binance_taker_fee;
  const okxFeeRate = settings.execution_mode === "maker" ? settings.okx_maker_fee : settings.okx_taker_fee;
  const halfCapital = Number(settings.total_capital || 0) / 2;
  const leverage = Number(settings.leverage || 1);
  const maxRate = Math.max(...item.history_rates.map((value) => Math.abs(value)), 0.000001);
  const bars = item.history_rates.map((value) => {
    const height = Math.max(3, Math.abs(value) / maxRate * 52);
    return `<span class="history-bar ${value < 0 ? "negative" : ""}" style="height:${height}px" title="${percent(value, 4)}"></span>`;
  }).join("");
  $("#detail-panel").innerHTML = `
    <div class="detail-content">
      <div class="detail-title">
        <div class="detail-title-main">
          <span class="ticker-mark">${escapeHtml(item.ticker.slice(0, 4))}</span>
          <div><h2>${escapeHtml(item.ticker)}</h2><span class="eyebrow">${escapeHtml(item.name)}</span></div>
        </div>
        <div class="detail-title-actions">
          <span class="source-chip">跨所永续</span>
          ${marketLinks(item, "full")}
        </div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">资金费差</div>
        <div class="detail-rate">
          <div><span>当前费差年化</span><strong class="${valueClass(item.annualized_current)}">${percent(item.annualized_current)}</strong></div>
          <div><span>近7日费差年化</span><strong class="${valueClass(item.annualized_7d)}">${percent(item.annualized_7d)}</strong></div>
        </div>
        <div class="metric-row"><span>Binance 当前资金费</span><strong class="${valueClass(item.binance_funding_rate)}">${percent(item.binance_funding_rate, 4)}</strong></div>
        <div class="metric-row"><span>OKX 当前资金费</span><strong class="${valueClass(item.okx_funding_rate)}">${percent(item.okx_funding_rate, 4)}</strong></div>
        <div class="history-bars" aria-label="最近21次跨所资金费差">${bars}</div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">两条腿</div>
        <div class="leg-row"><span class="leg-side long">做多</span><span>${escapeHtml(item.long_exchange)} Ask</span><strong>${price(item.long_ask)}</strong></div>
        <div class="leg-row"><span class="leg-side short">做空</span><span>${escapeHtml(item.short_exchange)} Bid</span><strong>${price(item.short_bid)}</strong></div>
        <div class="metric-row"><span>可对冲名义本金</span><strong>${money(projection.hedge_notional)}</strong></div>
        <div class="metric-row"><span>入场价差</span><strong class="${valueClass(item.entry_basis_bps)}">${bps(item.entry_basis_bps)}</strong></div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">持有期收益拆解</div>
        <div class="metric-row"><span>按当前费差预估资金费</span><strong>${money(projection.gross_funding)}</strong></div>
        <div class="metric-row"><span>入场价差贡献</span><strong class="${valueClass(projection.entry_basis_pnl)}">${money(projection.entry_basis_pnl)}</strong></div>
        <div class="metric-row"><span>Binance 手续费 开+平</span><strong class="negative">-${money(costs.binance)}</strong></div>
        <div class="metric-row"><span>OKX 手续费 开+平</span><strong class="negative">-${money(costs.okx)}</strong></div>
        <div class="metric-row"><span>滑点预估 开+平</span><strong class="negative">-${money(costs.slippage)}</strong></div>
        <div class="metric-row"><span>额外预留成本 开+平</span><strong class="negative">-${money(costs.extra)}</strong></div>
        <div class="metric-row"><span>合计交易成本</span><strong class="negative">-${money(costs.total)}</strong></div>
        <div class="metric-row"><span>成本率</span><strong>${percent(costs.costPctOfCapital)} 账户 / ${percent(costs.costPctOfHedge)} 名义</strong></div>
        <div class="projection-total"><span>费用后预估净收益</span><strong>${money(projection.net_profit)}</strong></div>
        <button class="command-button primary detail-action" data-add-symbol="${escapeHtml(item.symbol)}"><i data-lucide="plus"></i><span>登记为持仓</span></button>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">费用参数</div>
        <div class="metric-row"><span>${settings.execution_mode === "maker" ? "Binance 挂单费率" : "Binance 吃单费率"}</span><strong>${percent(binanceFeeRate, 4)}</strong></div>
        <div class="metric-row"><span>${settings.execution_mode === "maker" ? "OKX 挂单费率" : "OKX 吃单费率"}</span><strong>${percent(okxFeeRate, 4)}</strong></div>
        <div class="metric-row"><span>单边滑点</span><strong>${Number(settings.slippage_bps || 0).toFixed(1)} bps</strong></div>
        <div class="metric-row"><span>额外单边成本</span><strong>${Number(settings.extra_cost_bps || 0).toFixed(1)} bps + ${money(settings.extra_fixed_fee)}</strong></div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">测算公式</div>
        <div class="formula-stack">
          <div class="formula-line"><span>对冲名义</span><strong>总资金 / 2 × 杠杆</strong><em>${money(halfCapital)} × ${leverage.toFixed(1)} = ${money(projection.hedge_notional)}</em></div>
          <div class="formula-line"><span>资金费</span><strong>对冲名义 × 当前费差年化 × 持有天数 / 365</strong><em>${money(projection.hedge_notional)} × ${percent(item.annualized_current)} × ${Number(settings.holding_days || 0)} / 365 = ${money(projection.gross_funding)}</em></div>
          <div class="formula-line"><span>价差贡献</span><strong>对冲名义 × 入场价差 bps / 10000</strong><em>${money(projection.hedge_notional)} × ${Number(item.entry_basis_bps || 0).toFixed(1)} / 10000 = ${money(projection.entry_basis_pnl)}</em></div>
          <div class="formula-line"><span>单边费用</span><strong>Binance 手续费 + OKX 手续费 + 双腿滑点 + 额外预留</strong><em>${money(projection.binance_open_fee)} + ${money(projection.okx_open_fee)} + ${money(projection.slippage_open)} + ${money(projection.extra_open_fee)} = ${money(projection.opening_cost)}</em></div>
          <div class="formula-line"><span>总费用</span><strong>2 × 单边费用</strong><em>2 × ${money(projection.opening_cost)} = ${money(costs.total)}</em></div>
          <div class="formula-line total"><span>净收益</span><strong>资金费 + 价差贡献 - 总费用</strong><em>${money(projection.gross_funding)} ${signedMoney(projection.entry_basis_pnl)} - ${money(costs.total)} = ${money(projection.net_profit)}</em></div>
        </div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">费用核验</div>
        <div class="fee-audit-list">${renderFeeAudit()}</div>
      </div>
      <div class="detail-block">
        <div class="risk-row"><i data-lucide="triangle-alert"></i><span>跨所价格和资金费会快速变化，开仓前需同时核对两边盘口和资金费倒计时。</span></div>
        <div class="risk-row"><i data-lucide="shield-alert"></i><span>两边账户保证金不能互相补充，单边急涨急跌时仍可能先触发强平。</span></div>
      </div>
    </div>`;
  $("[data-add-symbol]").addEventListener("click", () => openPositionDialog(item.symbol));
  $$("#detail-panel .market-link").forEach((link) => link.addEventListener("click", (event) => event.stopPropagation()));
}

function renderPositions() {
  const positions = state.snapshot?.positions || [];
  $("#empty-positions").classList.toggle("hidden", positions.length > 0);
  $("#positions-grid").innerHTML = positions.map((position) => {
    if (!position.quote_available || !position.pnl) {
      return `<article class="position-card">
        <div class="position-card-head"><div class="position-card-head-main"><span class="ticker-mark">${escapeHtml(position.symbol.slice(0, 4))}</span><strong>${escapeHtml(position.symbol)}</strong></div>${positionActions(position.id)}</div>
        <div class="empty-state"><strong>没有这个合约的行情</strong><span>请把代码加入监控列表</span></div>
      </article>`;
    }
    const pnl = position.pnl;
    return `<article class="position-card">
      <div class="position-card-head">
        <div class="position-card-head-main"><span class="ticker-mark">${escapeHtml(position.symbol.replace("USDT", "").slice(0, 4))}</span><div><strong>${escapeHtml(position.symbol.replace("USDT", ""))}</strong><span class="eyebrow">${position.quantity} 币</span></div></div>
        ${positionActions(position.id)}
      </div>
      <div class="position-metrics">
        <div class="position-metric"><span>多头盈亏</span><strong class="${valueClass(pnl.spot_pnl)}">${money(pnl.spot_pnl)}</strong></div>
        <div class="position-metric"><span>空单盈亏</span><strong class="${valueClass(pnl.perp_pnl)}">${money(pnl.perp_pnl)}</strong></div>
        <div class="position-metric"><span>已收资金费</span><strong class="${valueClass(pnl.funding)}">${money(pnl.funding)}</strong></div>
        <div class="position-metric"><span>费用 + 平仓成本</span><strong class="negative">-${money(pnl.fees + pnl.exit_cost).replace("-", "")}</strong></div>
        <div class="position-metric"><span>多头 ${price(position.spot_entry)} → ${price(position.current_spot)}</span><strong>${position.spot_price_kind === "manual" ? "手工覆盖" : "多头 Bid"}</strong></div>
        <div class="position-metric"><span>空头 ${price(position.perp_entry)} → ${price(position.current_perp)}</span><strong>空头 Ask</strong></div>
      </div>
      <div class="position-total"><span>立即平仓预估净利润</span><strong class="${valueClass(pnl.net_pnl)}">${money(pnl.net_pnl)}</strong></div>
      <span class="price-source">${escapeHtml(position.note || `开仓于 ${formatLocalTime(position.opened_at, false)}`)}</span>
    </article>`;
  }).join("");
  $$('[data-edit-position]').forEach((button) => button.addEventListener("click", () => openPositionDialog(null, Number(button.dataset.editPosition))));
  $$('[data-delete-position]').forEach((button) => button.addEventListener("click", () => deletePosition(Number(button.dataset.deletePosition))));
  initIcons();
}

function positionActions(id) {
  return `<div class="position-card-actions">
    <button class="icon-button" data-edit-position="${id}" title="编辑持仓" aria-label="编辑持仓"><i data-lucide="pencil"></i></button>
    <button class="icon-button" data-delete-position="${id}" title="删除持仓" aria-label="删除持仓"><i data-lucide="trash-2"></i></button>
  </div>`;
}

function syncControlsFromSettings(settings) {
  if (!settings || !Object.keys(settings).length) return;
  const active = document.activeElement;
  const setUnlessActive = (selector, value) => {
    const node = $(selector);
    if (node && active !== node) node.value = value;
  };
  setUnlessActive("#capital-input", settings.total_capital);
  setUnlessActive("#holding-days-input", settings.holding_days);
  setUnlessActive("#allocation-input", settings.leverage || 1);
  const minAnnualizedInput = $("#min-annualized");
  const minAnnualizedPercent = Math.round(settings.min_annualized * 100);
  setUnlessActive("#min-annualized", minAnnualizedPercent);
  if (active !== minAnnualizedInput) {
    $("#min-annualized-output").textContent = `${minAnnualizedPercent}%`;
  }
  updateAllocationLabels(settings.leverage || 1);
  $$('[data-execution]').forEach((button) => button.classList.toggle("active", button.dataset.execution === settings.execution_mode));
}

function updateAllocationLabels(leverageValue) {
  const leverage = Number(leverageValue || 1);
  $("#allocation-output").textContent = `${leverage.toFixed(leverage % 1 ? 1 : 0)}x`;
  $("#spot-allocation-label").textContent = "两所各放一半保证金";
  $("#perp-allocation-label").textContent = `名义约 ${leverage.toFixed(leverage % 1 ? 1 : 0)} 倍`;
}

function collectSettings() {
  const current = state.snapshot?.settings || {};
  return {
    ...current,
    total_capital: Number($("#capital-input").value),
    holding_days: Number($("#holding-days-input").value),
    leverage: Number($("#allocation-input").value),
    min_annualized: Number($("#min-annualized").value) / 100,
    execution_mode: $(".segment.active").dataset.execution,
  };
}

function scheduleSettingsSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveSettingsNow, 600);
}

async function saveSettingsNow() {
  clearTimeout(state.saveTimer);
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(collectSettings()) });
  } catch (error) {
    toast(error.message, "error");
  }
}

function openSettingsDialog() {
  const settings = state.snapshot.settings;
  $("#provider-mode-input").value = settings.provider_mode;
  $("#refresh-seconds-input").value = settings.refresh_seconds;
  $("#spot-fee-input").value = settings.binance_maker_fee;
  $("#spot-min-fee-input").value = settings.binance_taker_fee;
  $("#maker-fee-input").value = settings.okx_maker_fee;
  $("#taker-fee-input").value = settings.okx_taker_fee;
  $("#slippage-input").value = settings.slippage_bps;
  $("#extra-cost-bps-input").value = settings.extra_cost_bps;
  $("#extra-fixed-fee-input").value = settings.extra_fixed_fee;
  $("#alert-input").value = settings.alert_annualized;
  $("#symbols-input").value = settings.watch_symbols.join(", ");
  $("#settings-dialog").showModal();
}

async function saveSettingsDialog(event) {
  event.preventDefault();
  const payload = {
    ...collectSettings(),
    provider_mode: $("#provider-mode-input").value,
    refresh_seconds: Number($("#refresh-seconds-input").value),
    binance_maker_fee: Number($("#spot-fee-input").value),
    binance_taker_fee: Number($("#spot-min-fee-input").value),
    okx_maker_fee: Number($("#maker-fee-input").value),
    okx_taker_fee: Number($("#taker-fee-input").value),
    slippage_bps: Number($("#slippage-input").value),
    extra_cost_bps: Number($("#extra-cost-bps-input").value),
    extra_fixed_fee: Number($("#extra-fixed-fee-input").value),
    alert_annualized: Number($("#alert-input").value),
    watch_symbols: $("#symbols-input").value.split(/[,，\s]+/).filter(Boolean),
  };
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    $("#settings-dialog").close();
    toast("设置已保存");
  } catch (error) {
    toast(error.message, "error");
  }
}

function toDateTimeLocal(value = new Date()) {
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function openPositionDialog(symbol = null, positionId = null) {
  const form = $("#position-form");
  form.reset();
  $("#position-id-input").value = positionId || "";
  $("#position-funding-input").value = 0;
  $("#position-fees-input").value = 0;
  $("#position-opened-input").value = toDateTimeLocal();
  let opportunity = state.snapshot?.opportunities.find((item) => item.symbol === (symbol || state.selectedSymbol));
  if (positionId) {
    const position = state.snapshot.positions.find((item) => item.id === positionId);
    $("#position-dialog-title").textContent = "编辑持仓";
    $("#position-symbol-input").value = position.symbol;
    $("#position-quantity-input").value = position.quantity;
    $("#position-spot-entry-input").value = position.spot_entry;
    $("#position-perp-entry-input").value = position.perp_entry;
    $("#position-opened-input").value = toDateTimeLocal(position.opened_at);
    $("#position-funding-input").value = position.funding_received;
    $("#position-fees-input").value = position.opening_fees;
    $("#position-spot-override-input").value = position.spot_price_override || "";
    $("#position-note-input").value = position.note || "";
  } else {
    $("#position-dialog-title").textContent = "登记持仓";
    if (opportunity) {
      $("#position-symbol-input").value = opportunity.symbol;
      $("#position-spot-entry-input").value = opportunity.spot_ask;
      $("#position-perp-entry-input").value = opportunity.perp_bid;
      $("#position-quantity-input").value = (opportunity.projection.hedge_notional / opportunity.spot_ask).toFixed(6);
    }
  }
  $("#position-dialog").showModal();
}

async function savePosition(event) {
  event.preventDefault();
  const id = Number($("#position-id-input").value || 0);
  const override = $("#position-spot-override-input").value;
  const payload = {
    symbol: $("#position-symbol-input").value,
    quantity: Number($("#position-quantity-input").value),
    spot_entry: Number($("#position-spot-entry-input").value),
    perp_entry: Number($("#position-perp-entry-input").value),
    opened_at: new Date($("#position-opened-input").value).toISOString(),
    funding_received: Number($("#position-funding-input").value || 0),
    opening_fees: Number($("#position-fees-input").value || 0),
    spot_price_override: override ? Number(override) : null,
    note: $("#position-note-input").value,
  };
  try {
    await api(id ? `/api/positions/${id}` : "/api/positions", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    $("#position-dialog").close();
    toast(id ? "持仓已更新" : "持仓已登记");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deletePosition(id) {
  if (!window.confirm("确定删除这条持仓记录吗？")) return;
  try {
    await api(`/api/positions/${id}`, { method: "DELETE" });
    toast("持仓已删除");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function toggleNotifications(event) {
  if (!event.target.checked) {
    state.notifications = false;
    localStorage.setItem("carry-notifications", "off");
    return;
  }
  if (!("Notification" in window)) {
    event.target.checked = false;
    toast("当前浏览器不支持桌面通知", "error");
    return;
  }
  const permission = await Notification.requestPermission();
  state.notifications = permission === "granted";
  event.target.checked = state.notifications;
  localStorage.setItem("carry-notifications", state.notifications ? "on" : "off");
}

function maybeNotify() {
  if (!state.notifications || Notification.permission !== "granted" || state.snapshot.source !== "live") return;
  const threshold = state.snapshot.settings.alert_annualized;
  for (const item of state.snapshot.opportunities) {
    const key = `${item.symbol}:${Math.floor(item.annualized_current * 20)}`;
    if (item.annualized_current >= threshold && !state.lastAlerted.has(key)) {
      new Notification(`${item.ticker} 资金费机会`, {
        body: `当前费差年化 ${percent(item.annualized_current)}，持有期净收益预估 ${money(item.projection.net_profit)}`,
      });
      state.lastAlerted.add(key);
    }
  }
}

function bindEvents() {
  $$(".view-tab").forEach((button) => button.addEventListener("click", () => {
    state.activeView = button.dataset.view;
    $$(".view-tab").forEach((item) => item.classList.toggle("active", item === button));
    $$(".view-section").forEach((section) => section.classList.remove("active"));
    $(`#${state.activeView}-view`).classList.add("active");
  }));

  $("#search-input").addEventListener("input", renderOpportunities);
  $("#min-annualized").addEventListener("input", (event) => {
    $("#min-annualized-output").textContent = `${event.target.value}%`;
    renderOpportunities();
  });
  $("#min-annualized").addEventListener("change", scheduleSettingsSave);
  $("#allocation-input").addEventListener("input", (event) => updateAllocationLabels(event.target.value));
  ["#allocation-input", "#capital-input", "#holding-days-input"].forEach((selector) => $(selector).addEventListener("change", scheduleSettingsSave));
  $$(".segment").forEach((button) => button.addEventListener("click", () => {
    $$(".segment").forEach((item) => item.classList.toggle("active", item === button));
    scheduleSettingsSave();
  }));

  $("#reset-filter-btn").addEventListener("click", async () => {
    $("#search-input").value = "";
    $("#min-annualized").value = 0;
    $("#min-annualized-output").textContent = "0%";
    renderOpportunities();
    await saveSettingsNow();
  });

  $("#refresh-btn").addEventListener("click", async () => {
    const button = $("#refresh-btn");
    button.classList.add("spinning");
    try {
      const snapshot = await api("/api/refresh", { method: "POST" });
      applySnapshot({ ...snapshot, version: state.snapshot?.version || 0 });
      toast("行情已刷新");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.classList.remove("spinning");
    }
  });

  $("#settings-btn").addEventListener("click", openSettingsDialog);
  $("#settings-form").addEventListener("submit", saveSettingsDialog);
  $("#position-form").addEventListener("submit", savePosition);
  $("#add-position-btn").addEventListener("click", () => openPositionDialog());
  $("#add-position-top").addEventListener("click", () => openPositionDialog());
  $("#notification-toggle").checked = state.notifications;
  $("#notification-toggle").addEventListener("change", toggleNotifications);
  $$(".close-dialog").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
  $$("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  }));
}

function connectStream() {
  const source = new EventSource("/api/stream");
  source.addEventListener("snapshot", (event) => applySnapshot(JSON.parse(event.data)));
  source.onerror = () => {
    source.close();
    setTimeout(connectStream, 5000);
  };
}

async function boot() {
  bindEvents();
  initIcons();
  try {
    applySnapshot(await api("/api/snapshot"));
  } catch (error) {
    toast(error.message, "error");
  }
  connectStream();
  setInterval(updateCountdown, 1000);
}

document.addEventListener("DOMContentLoaded", boot);
