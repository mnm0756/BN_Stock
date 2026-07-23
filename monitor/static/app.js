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
    title: "美股 Stock Trading",
    status: "已计入",
    detail: "0佣金；<= $350 每单 $0.35；> $350 按 0.1% spread，向上取到 2 位小数。",
    href: "https://www.binance.com/en/support/faq/detail/a7469c7703524024b5bc2d492b03639d",
  },
  {
    title: "TradFi Perps 交易费",
    status: "已计入",
    detail: "Regular/VIP1 默认 Maker 0%，Taker 0.0400%；BNB 抵扣 Taker 为 0.0360%。",
    href: "https://www.binance.com/en/support/announcement/detail/a4c3f1957f2b4e69902985154235c3b1",
  },
  {
    title: "bStocks 转换",
    status: "未默认计入",
    detail: "股票与 bStocks 1:1 转换无费用；bStocks 提现有 BSC 网络费。",
    href: "https://www.binance.com/en/support/faq/detail/f0d41139fadc4790bf9a4c0c7bce2e88",
  },
  {
    title: "bStocks 现货交易",
    status: "未默认计入",
    detail: "新上市 bStocks 对在活动期享 0 Maker；Taker/点差需按实际成交和账户费率核对。",
    href: "https://www.binance.com/en/support/announcement/detail/ea42cf41150f4e9ab7f1631fa21bba1e",
  },
  {
    title: "ADR/税费/换汇等",
    status: "需手动预留",
    detail: "ADR 通常 $0.01-$0.05/股、每年一到两次；税费、换汇、监管、分红相关费用可能另算。",
    href: "https://www.binance.com/en/support/faq/detail/a7469c7703524024b5bc2d492b03639d",
  },
];

function binanceStockUrl(item) {
  return `https://www.binance.com/en/stocks/EQ_${encodeURIComponent(item.ticker)}`;
}

function binanceFuturesUrl(item) {
  return `https://www.binance.com/en/futures/${encodeURIComponent(item.symbol)}`;
}

function marketLinks(item, tone = "compact") {
  const labelClass = tone === "full" ? " full" : "";
  return `
    <div class="market-links${labelClass}">
      <a class="market-link stock" href="${binanceStockUrl(item)}" target="_blank" rel="noreferrer" title="打开 Binance 美股现货 ${escapeHtml(item.ticker)}">
        <i data-lucide="external-link"></i><span>现货</span>
      </a>
      <a class="market-link futures" href="${binanceFuturesUrl(item)}" target="_blank" rel="noreferrer" title="打开 Binance 合约 ${escapeHtml(item.symbol)}">
        <i data-lucide="external-link"></i><span>合约</span>
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
    return "Binance 公共接口返回 451，当前网络或地区不可用";
  }
  if (message.toLowerCase().includes("timeout")) {
    return "连接 Binance 公共接口超时";
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
      $("#data-banner-text").textContent = `实时刷新暂时失败，当前保留上一份真实行情，请核对 Binance 页面后再交易。原因：${summarizeSourceError(error) || "未知错误"}`;
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
    return matchesQuery && item.annualized_7d >= minAnnualized;
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
  const hasDetail = projection && Object.hasOwn(projection, "spot_open_fee");
  const spot = hasDetail ? Number(projection.spot_open_fee || 0) + Number(projection.spot_close_fee || 0) : 0;
  const perp = hasDetail ? Number(projection.perp_open_fee || 0) + Number(projection.perp_close_fee || 0) : 0;
  const slippage = hasDetail ? Number(projection.slippage_open || 0) + Number(projection.slippage_close || 0) : 0;
  const extra = hasDetail ? Number(projection.extra_open_fee || 0) + Number(projection.extra_close_fee || 0) : 0;
  return {
    total,
    spot,
    perp,
    slippage,
    extra,
    costPctOfHedge: Number(projection?.cost_pct_of_hedge || 0),
    costPctOfCapital: Number(projection?.cost_pct_of_capital || 0),
    hasDetail,
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
    const bestAnnualized = allRows.reduce((best, item) => Math.max(best, Number(item.annualized_7d || 0)), 0);
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
      <td><div class="value-stack"><strong class="${valueClass(item.funding_rate)}">${percent(item.funding_rate, 4)}</strong><small>每 ${item.funding_interval_hours} 小时</small></div></td>
      <td><div class="value-stack"><strong class="${valueClass(item.annualized_7d)}">${percent(item.annualized_7d)}</strong><small>当前 ${percent(item.annualized_current)}</small></div></td>
      <td><strong class="${valueClass(item.entry_basis_bps)}">${bps(item.entry_basis_bps)}</strong></td>
      <td><div class="value-stack"><strong>${price(item.spot_ask)} / ${price(item.perp_bid)}</strong><small>指数参考 / 合约 Bid</small></div></td>
      <td><div class="value-stack"><strong class="negative">-${money(costs.total)}</strong><small>股 ${money(costs.spot)} / 合 ${money(costs.perp)} / 滑 ${money(costs.slippage)}</small><small>其 ${money(costs.extra)} / ${percent(costs.costPctOfHedge)} 名义</small></div></td>
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
  const perpFeeRate = settings.execution_mode === "maker" ? settings.perp_maker_fee : settings.perp_taker_fee;
  const spotBudget = Number(settings.total_capital || 0) * (1 - Number(settings.perp_allocation || 0));
  const perpBudget = Number(settings.total_capital || 0) * Number(settings.perp_allocation || 0);
  const fundingEvents = Number(settings.holding_days || 0) * 24 / Math.max(Number(item.funding_interval_hours || 0), 0.001);
  const averageFundingRate = projection.hedge_notional && fundingEvents
    ? Number(projection.gross_funding || 0) / Number(projection.hedge_notional) / fundingEvents
    : 0;
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
          <span class="source-chip">指数参考现货</span>
          ${marketLinks(item, "full")}
        </div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">资金费</div>
        <div class="detail-rate">
          <div><span>当前年化</span><strong class="${valueClass(item.annualized_current)}">${percent(item.annualized_current)}</strong></div>
          <div><span>7 日年化</span><strong class="${valueClass(item.annualized_7d)}">${percent(item.annualized_7d)}</strong></div>
        </div>
        <div class="history-bars" aria-label="最近21次资金费">${bars}</div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">两条腿</div>
        <div class="leg-row"><span class="leg-side long">买入</span><span>现货参考 Ask</span><strong>${price(item.spot_ask)}</strong></div>
        <div class="leg-row"><span class="leg-side short">做空</span><span>合约 Bid</span><strong>${price(item.perp_bid)}</strong></div>
        <div class="metric-row"><span>可对冲名义本金</span><strong>${money(projection.hedge_notional)}</strong></div>
        <div class="metric-row"><span>入场价差</span><strong class="${valueClass(item.entry_basis_bps)}">${bps(item.entry_basis_bps)}</strong></div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">持有期收益拆解</div>
        <div class="metric-row"><span>预估资金费</span><strong>${money(projection.gross_funding)}</strong></div>
        <div class="metric-row"><span>入场价差贡献</span><strong class="${valueClass(projection.entry_basis_pnl)}">${money(projection.entry_basis_pnl)}</strong></div>
        <div class="metric-row"><span>美股平台费/价差 买+卖</span><strong class="negative">-${money(costs.spot)}</strong></div>
        <div class="metric-row"><span>合约手续费 开+平</span><strong class="negative">-${money(costs.perp)}</strong></div>
        <div class="metric-row"><span>滑点预估 开+平</span><strong class="negative">-${money(costs.slippage)}</strong></div>
        <div class="metric-row"><span>额外预留成本 开+平</span><strong class="negative">-${money(costs.extra)}</strong></div>
        <div class="metric-row"><span>合计交易成本</span><strong class="negative">-${money(costs.total)}</strong></div>
        <div class="metric-row"><span>成本率</span><strong>${percent(costs.costPctOfCapital)} 账户 / ${percent(costs.costPctOfHedge)} 名义</strong></div>
        <div class="projection-total"><span>费用后预估净收益</span><strong>${money(projection.net_profit)}</strong></div>
        <button class="command-button primary detail-action" data-add-symbol="${escapeHtml(item.symbol)}"><i data-lucide="plus"></i><span>登记为持仓</span></button>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">费用参数</div>
        <div class="metric-row"><span>美股价差费率 / 最低平台费</span><strong>${percent(settings.spot_fee_rate, 3)} / ${money(settings.spot_min_fee)}</strong></div>
        <div class="metric-row"><span>${settings.execution_mode === "maker" ? "合约挂单费率" : "合约吃单费率"}</span><strong>${percent(perpFeeRate, 4)}</strong></div>
        <div class="metric-row"><span>单边滑点</span><strong>${Number(settings.slippage_bps || 0).toFixed(1)} bps</strong></div>
        <div class="metric-row"><span>额外单边成本</span><strong>${Number(settings.extra_cost_bps || 0).toFixed(1)} bps + ${money(settings.extra_fixed_fee)}</strong></div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">测算公式</div>
        <div class="formula-stack">
          <div class="formula-line"><span>对冲名义</span><strong>min(总资金 × 合约占比, 总资金 × 现货占比)</strong><em>min(${money(perpBudget)}, ${money(spotBudget)}) = ${money(projection.hedge_notional)}</em></div>
          <div class="formula-line"><span>资金费</span><strong>对冲名义 × 平均资金费率 × 结算次数</strong><em>${money(projection.hedge_notional)} × ${percent(averageFundingRate, 4)} × ${fundingEvents.toFixed(1)} = ${money(projection.gross_funding)}</em></div>
          <div class="formula-line"><span>价差贡献</span><strong>对冲名义 × 入场价差 bps / 10000</strong><em>${money(projection.hedge_notional)} × ${Number(item.entry_basis_bps || 0).toFixed(1)} / 10000 = ${money(projection.entry_basis_pnl)}</em></div>
          <div class="formula-line"><span>单边费用</span><strong>美股平台费/价差 + 合约费 + 滑点 + 额外预留</strong><em>${money(projection.spot_open_fee)} + ${money(projection.perp_open_fee)} + ${money(projection.slippage_open)} + ${money(projection.extra_open_fee)} = ${money(projection.opening_cost)}</em></div>
          <div class="formula-line"><span>总费用</span><strong>2 × 单边费用</strong><em>2 × ${money(projection.opening_cost)} = ${money(costs.total)}</em></div>
          <div class="formula-line total"><span>净收益</span><strong>资金费 + 价差贡献 - 总费用</strong><em>${money(projection.gross_funding)} ${signedMoney(projection.entry_basis_pnl)} - ${money(costs.total)} = ${money(projection.net_profit)}</em></div>
        </div>
      </div>
      <div class="detail-block">
        <div class="detail-block-title">费用核验</div>
        <div class="fee-audit-list">${renderFeeAudit()}</div>
      </div>
      <div class="detail-block">
        <div class="risk-row"><i data-lucide="triangle-alert"></i><span>指数价不等于 Binance 美股现货可成交价，开仓前需核对真实盘口。</span></div>
        <div class="risk-row"><i data-lucide="shield-alert"></i><span>现货不能自动补充合约保证金，股票急涨时空单仍可能先被强平。</span></div>
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
        <div class="position-card-head-main"><span class="ticker-mark">${escapeHtml(position.symbol.replace("USDT", "").slice(0, 4))}</span><div><strong>${escapeHtml(position.symbol.replace("USDT", ""))}</strong><span class="eyebrow">${position.quantity} 股</span></div></div>
        ${positionActions(position.id)}
      </div>
      <div class="position-metrics">
        <div class="position-metric"><span>现货盈亏</span><strong class="${valueClass(pnl.spot_pnl)}">${money(pnl.spot_pnl)}</strong></div>
        <div class="position-metric"><span>空单盈亏</span><strong class="${valueClass(pnl.perp_pnl)}">${money(pnl.perp_pnl)}</strong></div>
        <div class="position-metric"><span>已收资金费</span><strong class="${valueClass(pnl.funding)}">${money(pnl.funding)}</strong></div>
        <div class="position-metric"><span>费用 + 平仓成本</span><strong class="negative">-${money(pnl.fees + pnl.exit_cost).replace("-", "")}</strong></div>
        <div class="position-metric"><span>现货 ${price(position.spot_entry)} → ${price(position.current_spot)}</span><strong>${position.spot_price_kind === "manual" ? "手工现货价" : "指数参考价"}</strong></div>
        <div class="position-metric"><span>合约 ${price(position.perp_entry)} → ${price(position.current_perp)}</span><strong>合约 Ask</strong></div>
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
  setUnlessActive("#allocation-input", Math.round(settings.perp_allocation * 100));
  const minAnnualizedInput = $("#min-annualized");
  const minAnnualizedPercent = Math.round(settings.min_annualized * 100);
  setUnlessActive("#min-annualized", minAnnualizedPercent);
  if (active !== minAnnualizedInput) {
    $("#min-annualized-output").textContent = `${minAnnualizedPercent}%`;
  }
  updateAllocationLabels(settings.perp_allocation * 100);
  $$('[data-execution]').forEach((button) => button.classList.toggle("active", button.dataset.execution === settings.execution_mode));
}

function updateAllocationLabels(perpPercent) {
  const perp = Number(perpPercent);
  $("#allocation-output").textContent = `${perp}%`;
  $("#spot-allocation-label").textContent = `现货 ${100 - perp}%`;
  $("#perp-allocation-label").textContent = `合约 ${perp}%`;
}

function collectSettings() {
  const current = state.snapshot?.settings || {};
  return {
    ...current,
    total_capital: Number($("#capital-input").value),
    holding_days: Number($("#holding-days-input").value),
    perp_allocation: Number($("#allocation-input").value) / 100,
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
  $("#spot-fee-input").value = settings.spot_fee_rate;
  $("#spot-min-fee-input").value = settings.spot_min_fee;
  $("#maker-fee-input").value = settings.perp_maker_fee;
  $("#taker-fee-input").value = settings.perp_taker_fee;
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
    spot_fee_rate: Number($("#spot-fee-input").value),
    spot_min_fee: Number($("#spot-min-fee-input").value),
    perp_maker_fee: Number($("#maker-fee-input").value),
    perp_taker_fee: Number($("#taker-fee-input").value),
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
    const key = `${item.symbol}:${Math.floor(item.annualized_7d * 20)}`;
    if (item.annualized_7d >= threshold && !state.lastAlerted.has(key)) {
      new Notification(`${item.ticker} 资金费机会`, {
        body: `7日年化 ${percent(item.annualized_7d)}，持有期净收益预估 ${money(item.projection.net_profit)}`,
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
