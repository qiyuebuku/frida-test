const requestedView = new URLSearchParams(window.location.search).get("view");
const initialView = ["overview", "sectors", "stocks", "etf", "futures", "gold", "us", "runs", "watchlist", "assets"].includes(requestedView)
  ? requestedView
  : "overview";

function loadCollapsedTaskGroups() {
  try {
    return new Set(JSON.parse(localStorage.getItem("market-observability-collapsed-task-groups-v2") || "[]"));
  } catch (_) {
    return new Set();
  }
}

const state = {
  dashboard: null,
  collectionTasks: [],
  collapsedTaskGroups: loadCollapsedTaskGroups(),
  taskGroupsInitialized: localStorage.getItem("market-observability-collapsed-task-groups-v2") !== null,
  activeView: initialView,
  loading: false,
  refreshTimer: null,
  anomalyRefreshTimer: null,
  anomalyLoading: false,
  inventory: null,
  sectors: null,
  sectorLoading: false,
  sectorDetail: null,
  sectorDetailLoading: false,
  assetRecords: null,
  assetOffset: 0,
  assetLimit: 50,
  marketControls: {
    ranking: "rise",
    anomaly: "market_anomaly",
    flow: "etf",
    sentiment: "market",
    global: "a50",
    currency: "repo",
    valuation: "sh",
    valuationMetric: "pe",
    bond: "long",
  },
  rankingCache: {},
  dynamicGroups: null,
  dynamicGroupsLoading: false,
  activeDynamicGroup: null,
  etfMarket: null,
  etfMarketLoading: false,
  etfFlow: [],
  etfHot: "etf",
  etfRankingCategory: "all",
  etfRankingMetric: "change_pct",
  etfRankingFilter: "track",
  etfControls: {
    industry: { metric: "change_pct", direction: "desc" },
    index: { metric: "change_pct", direction: "desc" },
    t0: { metric: "change_pct", direction: "desc" },
  },
  futuresMarket: null,
  futuresMarketLoading: false,
  futuresControls: { ranking: "all", sort: "change_pct", direction: "desc", visible: 20 },
  usMarket: null,
  usMarketLoading: false,
  usControls: { breadth: "today", ranking: "all", rankingSession: "auto", industryPeriod: "current", conceptPeriod: "current" },
  goldMarket: null,
  goldMarketLoading: false,
  goldRealtimeLoading: false,
  goldRealtimeTimer: null,
  goldAiVisible: 10,
  goldNews: [],
  goldControls: { opportunity: "etf", opportunitySort: "recommend", spread: "spot", capital: "domestic", offline: "jewelry", reserve: "global", seasonality: "spot", ratio: "spot" },
  sectorControls: {
    hot: "concept",
    rankingType: "all",
    ranking: "change",
    rankingDirection: "desc",
    flow: "industry",
    rotationType: "concept",
    rotation: "change",
    commodity: "futures",
  },
};

const elements = {
  connection: document.querySelector("#connection-state"),
  refresh: document.querySelector("#refresh-button"),
  windowHours: document.querySelector("#window-hours"),
  autoRefresh: document.querySelector("#auto-refresh"),
  toast: document.querySelector("#toast"),
  dialog: document.querySelector("#record-dialog"),
  dialogKicker: document.querySelector("#dialog-kicker"),
  dialogTitle: document.querySelector("#dialog-title"),
  dialogContent: document.querySelector("#dialog-content"),
};

document.querySelectorAll(".view-tab").forEach((button) => {
  button.addEventListener("click", () => setActiveView(button.dataset.view));
});
document.querySelectorAll("[data-open-view]").forEach((button) => {
  button.addEventListener("click", () => setActiveView(button.dataset.openView));
});
elements.refresh.addEventListener("click", () => refreshActiveView(true));
elements.windowHours.addEventListener("change", () => loadDashboard(true));
elements.autoRefresh.addEventListener("change", configureAutoRefresh);
document.querySelector("#run-search").addEventListener("input", renderRuns);
document.querySelector("#run-source-filter").addEventListener("change", renderRuns);
document.querySelector("#run-module-filter").addEventListener("change", renderRuns);
document.querySelector("#run-channel-filter").addEventListener("change", renderRuns);
document.querySelector("#run-period-filter").addEventListener("change", renderRuns);
document.querySelector("#run-status-filter").addEventListener("change", renderRuns);
document.querySelector("#run-expand-all").addEventListener("click", () => {
  state.collapsedTaskGroups.clear();
  persistCollapsedTaskGroups();
  renderRuns();
});
document.querySelector("#run-collapse-all").addEventListener("click", () => {
  visibleTaskGroups().forEach((key) => state.collapsedTaskGroups.add(key));
  persistCollapsedTaskGroups();
  renderRuns();
});
document.querySelector("#run-table-body").addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-trigger-task]");
  if (trigger) {
    triggerCollectionTask(trigger.dataset.triggerTask, trigger);
    return;
  }
  const button = event.target.closest("[data-task-group-toggle]");
  if (!button) return;
  const key = decodeURIComponent(button.dataset.taskGroupToggle);
  if (state.collapsedTaskGroups.has(key)) state.collapsedTaskGroups.delete(key);
  else state.collapsedTaskGroups.add(key);
  persistCollapsedTaskGroups();
  renderRuns();
});
document.querySelector("#watchlist-search").addEventListener("input", renderWatchlist);
document.querySelector("#asset-domain-filter").addEventListener("change", () => {
  state.assetOffset = 0;
  renderAssetGroupOptions();
  loadAssetRecords();
});
document.querySelector("#asset-group-filter").addEventListener("change", () => {
  state.assetOffset = 0;
  loadAssetRecords();
});
document.querySelector("#asset-search").addEventListener("input", debounce(() => {
  state.assetOffset = 0;
  loadAssetRecords();
}, 250));
document.querySelector("#asset-prev").addEventListener("click", () => {
  state.assetOffset = Math.max(0, state.assetOffset - state.assetLimit);
  loadAssetRecords();
});
document.querySelector("#asset-next").addEventListener("click", () => {
  state.assetOffset += state.assetLimit;
  loadAssetRecords();
});
document.querySelector("#dialog-close").addEventListener("click", () => elements.dialog.close());
elements.dialog.addEventListener("click", (event) => {
  if (event.target === elements.dialog) {
    elements.dialog.close();
  }
});
window.addEventListener("resize", debounce(() => {
  renderMarketCharts();
}, 120));

document.querySelectorAll(".segmented-control").forEach((control) => {
  control.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    control.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    const name = control.dataset.control;
    if (name === "stock-ranking" || name === "stock-ranking-overview") {
      state.marketControls.ranking = button.dataset.value;
      document.querySelectorAll('[data-control="stock-ranking"], [data-control="stock-ranking-overview"]').forEach((rankingControl) => {
        rankingControl.querySelectorAll("button[data-value]").forEach((item) => {
          item.classList.toggle("active", item.dataset.value === button.dataset.value);
        });
      });
      loadStockRanking(button.dataset.value);
      return;
    }
    if (name === "stock-dynamic") {
      state.activeDynamicGroup = button.dataset.value;
      renderStockDynamicGroup();
      return;
    }
    const key = {
      anomaly: "anomaly",
      "capital-flow": "flow",
      sentiment: "sentiment",
      global: "global",
      currency: "currency",
      valuation: "valuation",
      "valuation-metric": "valuationMetric",
      bond: "bond",
    }[name];
    if (key) state.marketControls[key] = button.dataset.value;
    renderMarketCharts();
  });
});

document.querySelectorAll("[data-sector-control]").forEach((control) => {
  control.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    const key = control.dataset.sectorControl;
    if (key === "ranking" && state.sectorControls.ranking === button.dataset.value) {
      state.sectorControls.rankingDirection = state.sectorControls.rankingDirection === "desc" ? "asc" : "desc";
    } else if (key === "ranking") {
      state.sectorControls.rankingDirection = "desc";
    }
    control.querySelectorAll("button").forEach((item) => {
      item.classList.toggle("active", item === button);
      const icon = item.querySelector("i");
      if (icon) icon.textContent = item === button
        ? (state.sectorControls.rankingDirection === "desc" ? "▼" : "▲")
        : "◆";
    });
    state.sectorControls[key] = button.dataset.value;
    renderSectorMarket();
  });
});

document.querySelectorAll("[data-us-control]").forEach((control) => {
  control.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    control.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    state.usControls[control.dataset.usControl] = button.dataset.value;
    renderUsMarket();
  });
});

document.querySelectorAll("[data-etf-sort]").forEach((control) => {
  control.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    const category = control.dataset.etfSort;
    const current = state.etfControls[category];
    if (current.metric === button.dataset.value) {
      current.direction = current.direction === "desc" ? "asc" : "desc";
    } else {
      current.metric = button.dataset.value;
      current.direction = "desc";
    }
    control.querySelectorAll("button").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      const icon = item.querySelector("i");
      if (icon) icon.textContent = active ? (current.direction === "desc" ? "▼" : "▲") : "◆";
    });
    renderEtfCategory(category);
  });
});

document.querySelector("[data-etf-hot]")?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-value]");
  if (!button) return;
  state.etfHot = button.dataset.value;
  button.parentElement.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderEtfHot();
});

document.querySelector("[data-etf-ranking-category]")?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-value]");
  if (!button) return;
  state.etfRankingCategory = button.dataset.value;
  button.parentElement.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderEtfRanking();
});

document.querySelector("[data-etf-ranking-metric]")?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-value]");
  if (!button) return;
  state.etfRankingMetric = button.dataset.value;
  button.parentElement.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderEtfRanking();
});

document.querySelector("[data-etf-ranking-filter]")?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-value]");
  if (!button) return;
  state.etfRankingFilter = button.dataset.value;
  button.parentElement.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderEtfRanking();
});

document.querySelectorAll("[data-gold-control]").forEach((control) => {
  control.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    control.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    state.goldControls[control.dataset.goldControl] = button.dataset.value;
    if (control.dataset.goldControl === "opportunity") state.goldControls.opportunitySort = "recommend";
    renderGoldMarket();
  });
});

document.querySelector("[data-futures-ranking]")?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-value]");
  if (!button) return;
  state.futuresControls.ranking = button.dataset.value;
  state.futuresControls.visible = 20;
  button.parentElement.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderFuturesRanking();
});

document.querySelector(".futures-ranking-table thead")?.addEventListener("click", (event) => {
  const heading = event.target.closest("th[data-futures-sort]");
  if (!heading) return;
  const sort = heading.dataset.futuresSort;
  state.futuresControls.direction = state.futuresControls.sort === sort && state.futuresControls.direction === "desc" ? "asc" : "desc";
  state.futuresControls.sort = sort;
  state.futuresControls.visible = 20;
  renderFuturesRanking();
});

document.querySelector("#futures-load-more")?.addEventListener("click", () => {
  state.futuresControls.visible += 20;
  renderFuturesRanking();
});

document.querySelector("#gold-opportunity-sort").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-value]");
  if (!button) return;
  state.goldControls.opportunitySort = button.dataset.value;
  renderGoldOpportunity();
});

document.querySelector("#gold-reserve-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-value]");
  if (!button) return;
  state.goldControls.reserve = button.dataset.value;
  renderGoldReserve();
});

document.querySelector("#gold-ai-more").addEventListener("click", () => {
  const total = Number(document.querySelector("#gold-ai-more").dataset.total || 0);
  state.goldAiVisible = state.goldAiVisible >= total ? 10 : Math.min(total, state.goldAiVisible + 10);
  renderGoldNews();
});

document.querySelector("#view-sectors").addEventListener("click", (event) => {
  const target = event.target.closest("[data-sector-code]");
  if (!target) return;
  loadSectorDetail(target.dataset.sectorCode, target.dataset.sectorType || null);
});

setActiveView(state.activeView);
loadDashboard(false);
configureAutoRefresh();

function setActiveView(view) {
  state.activeView = view;
  document.querySelectorAll(".view-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `view-${view}`);
  });
  if (view === "overview") {
    requestAnimationFrame(renderMarketCharts);
  }
  if (view === "sectors" && !state.sectors) {
    loadSectorOverview();
  }
  if (view === "us" && !state.usMarket) {
    loadUsMarket();
  }
  if (view === "gold" && !state.goldMarket) {
    loadGoldMarket();
  }
  if (view === "etf" && !state.etfMarket) {
    loadEtfMarket();
  }
  if (view === "futures" && !state.futuresMarket) {
    loadFuturesMarket();
  }
  if (view === "assets" && !state.inventory) {
    loadInventory();
  }
  // ETF 全量快照采集周期为 15 秒；进入/离开 ETF 页时同步调整前端读取
  // 周期，避免页面继续沿用初始化时其他一级页的 30 秒定时器。
  configureAutoRefresh();
}

async function loadDashboard(showToast) {
  if (state.loading) return;
  state.loading = true;
  elements.refresh.classList.add("loading");
  setConnection("loading", "正在读取");
  try {
    const hours = Number(elements.windowHours.value || 24);
    const [response, taskResponse] = await Promise.all([
      fetch(`/api/market-observability/dashboard?hours=${hours}`, { headers: { Accept: "application/json" } }),
      fetch("/api/market-observability/collection-tasks", { headers: { Accept: "application/json" }, cache: "no-store" }),
    ]);
    if (!response.ok || !taskResponse.ok) {
      const failed = response.ok ? taskResponse : response;
      const body = await failed.text();
      throw new Error(`${failed.status} ${body}`);
    }
    state.dashboard = await response.json();
    state.collectionTasks = (await taskResponse.json()).items || [];
    state.rankingCache = {};
    state.dynamicGroups = null;
    state.activeDynamicGroup = null;
    renderDashboard();
    setConnection("ready", `已更新 ${formatClock(state.dashboard.generated_at)}`);
    if (showToast) showMessage("看板数据已刷新");
  } catch (error) {
    setConnection("error", "读取失败");
    showMessage(`读取失败：${error.message}`);
  } finally {
    state.loading = false;
    elements.refresh.classList.remove("loading");
  }
}

async function loadRealtimeMarketAnomaly() {
  if (state.anomalyLoading || !state.dashboard || document.hidden) return;
  state.anomalyLoading = true;
  try {
    const query = new URLSearchParams({
      subject_id: "cn:a_share:ths_anomaly",
      data_type: "market_anomaly",
      limit: "1",
    });
    const response = await fetch(`/api/market-observability/history?${query}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return;
    const payload = await response.json();
    const snapshot = (payload.items || [])[0];
    if (!snapshot) return;
    const block = state.dashboard.chart_series?.market_anomaly;
    if (!block || block.latest?.id === snapshot.id) return;
    block.latest = snapshot;
    const history = block.history || [];
    const lastIndex = history.length - 1;
    if (lastIndex >= 0 && history[lastIndex].trade_date === snapshot.trade_date) {
      history[lastIndex] = snapshot;
    } else {
      history.push(snapshot);
    }
    block.history = history;
    if (state.activeView === "overview" && state.marketControls.anomaly === "market_anomaly") {
      renderAnomalyChart();
    }
  } catch (_error) {
    // The 30-second full-dashboard refresh remains the recovery path.
  } finally {
    state.anomalyLoading = false;
  }
}

function refreshActiveView(showToast) {
  if (state.activeView === "sectors") {
    return loadSectorOverview(showToast);
  }
  if (state.activeView === "assets") {
    state.inventory = null;
    return loadInventory(showToast);
  }
  if (state.activeView === "us") return loadUsMarket(showToast);
  if (state.activeView === "gold") return loadGoldMarket(showToast);
  if (state.activeView === "etf") return loadEtfMarket(showToast);
  if (state.activeView === "futures") return loadFuturesMarket(showToast);
  return loadDashboard(showToast);
}

async function loadFuturesMarket(showToast = false) {
  if (state.futuresMarketLoading) return;
  state.futuresMarketLoading = true;
  setText("futures-market-status", "正在读取");
  try {
    const response = await fetch("/api/market-observability/snapshots?data_type=ths_futures_module&limit=100", { headers: { Accept: "application/json" }, cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    const payload = await response.json();
    const modules = {};
    (payload.items || []).forEach((item) => {
      if (!modules[item.subject_id] || dateValue(item.fetched_at) > dateValue(modules[item.subject_id].fetched_at)) modules[item.subject_id] = item;
    });
    if (!Object.keys(modules).length) throw new Error("尚无期货专区快照");
    state.futuresMarket = { modules, updatedAt: Object.values(modules).map((item) => item.fetched_at).filter(Boolean).sort().at(-1) };
    renderFuturesMarket();
    if (showToast) showMessage("期货数据已刷新");
  } catch (error) {
    setText("futures-market-status", "读取失败");
    showMessage(`期货数据读取失败：${error.message}`);
  } finally {
    state.futuresMarketLoading = false;
  }
}

function futuresModule(subject) {
  return state.futuresMarket?.modules?.[subject];
}

function futuresTable(subject) {
  return futuresModule(subject)?.data?.native_table || {};
}

function futuresTableRows(subject) {
  const table = futuresTable(subject);
  if (Array.isArray(table.rows)) return table.rows;
  const columns = table.dataDict || {};
  const length = Math.max(0, ...Object.values(columns).filter(Array.isArray).map((values) => values.length));
  return Array.from({ length }, (_, index) => Object.fromEntries(Object.entries(columns).map(([key, values]) => [key, Array.isArray(values) ? values[index] : null])));
}

function futuresNumber(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (value === null || value === undefined || value === "" || value === "--") return null;
  const parsed = Number.parseFloat(String(value).replaceAll(",", "").replace("%", ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function futuresCount(value) {
  const parsed = futuresNumber(value);
  if (parsed === null) return null;
  const text = String(value);
  if (text.includes("亿")) return parsed * 100000000;
  if (text.includes("万")) return parsed * 10000;
  return parsed;
}

function futuresAmountYi(value) {
  if (value === null || value === undefined || value === "--") return null;
  const text = String(value).replaceAll(",", "");
  const parsed = Number.parseFloat(text);
  if (!Number.isFinite(parsed)) return null;
  if (text.includes("万亿")) return parsed * 10000;
  if (text.includes("亿")) return parsed;
  if (text.includes("万")) return parsed / 10000;
  return parsed / 100000000;
}

function renderFuturesMarket() {
  if (!state.futuresMarket) return;
  const latest = state.futuresMarket.updatedAt;
  const elapsed = Date.now() - dateValue(latest);
  setText("futures-market-status", elapsed < 180000 ? futuresSessionLabel() : "数据延迟");
  document.querySelector("#futures-market-status")?.classList.toggle("neutral", elapsed >= 180000);
  setText("futures-market-updated", `采集 ${formatDateTime(latest)}`);
  const stateRow = futuresTableRows("market_state")[0] || {};
  const providerTimestamp = futuresNumber(stateRow["34320"]);
  setText("futures-market-source-time", `来源 ${providerTimestamp ? formatDateTime(new Date(providerTimestamp * 1000).toISOString()) : "—"}`);
  const flowRow = futuresTableRows("market_net_flow")[0] || {};
  const netFlow = futuresAmountYi(flowRow["68"]);
  const netFlowElement = document.querySelector("#futures-market-net-flow");
  if (netFlowElement) {
    netFlowElement.textContent = `市场资金净流入 ${netFlow === null ? "—" : `${netFlow > 0 ? "+" : ""}${formatNumber(netFlow, 2)} 亿`}`;
    netFlowElement.className = marketClass(netFlow);
  }
  renderFuturesHot();
  renderFuturesIndices();
  renderFuturesFlow();
  renderFuturesRanking();
}

function futuresSessionLabel() {
  const now = new Date();
  const minutes = now.getHours() * 60 + now.getMinutes();
  if ((minutes >= 9 * 60 && minutes <= 11 * 60 + 30) || (minutes >= 13 * 60 + 30 && minutes <= 15 * 60)) return "日盘中";
  if (minutes >= 21 * 60 || minutes <= 2 * 60 + 30) return "夜盘中";
  return "休市";
}

function futuresRankingRows(group = "all") {
  return futuresTableRows(`ranking:${group}`).map((row) => ({
    code: row["4"], name: row["55"] || row["4"], latest: futuresNumber(row["10"]), change_pct: futuresNumber(row["34818"]),
    change: futuresNumber(row["34821"]), volume: futuresCount(row["13"]), volume_text: row["13"], open_interest: futuresCount(row["65"]), open_interest_text: row["65"],
  }));
}

function renderFuturesHot() {
  const allRows = futuresRankingRows("all");
  const pushedSnapshot = futuresModule("hot_quotes_stream");
  const pushIsFresh = Date.now() - dateValue(pushedSnapshot?.fetched_at) <= 20000;
  const pushed = (pushIsFresh ? futuresTableRows("hot_quotes_stream") : []).map((item) => ({
      code: item["4"], name: item["55"], latest: futuresNumber(item["10"]),
      change_pct: futuresNumber(item["34818"]), change: futuresNumber(item["34821"]),
  }));
  const hotMembership = (futuresTable("hot").rows || []).slice(0, 6);
  document.querySelector("#futures-hot-grid").innerHTML = hotMembership.map((member) => {
    const code = String(member.code || "");
    const prefix = code.replace(/9999$/i, "");
    const row = pushed.find((item) => String(item.code).toLowerCase() === code.toLowerCase())
      || allRows.find((item) => String(item.code).toLowerCase().startsWith(prefix.toLowerCase()));
    const label = member.name || row?.name || code;
    return `<article class="futures-quote-card"><span>${escapeHtml(label)}</span><small>${escapeHtml(code || row?.code || "—")}</small><strong>${formatNumber(row?.latest, row?.latest && row.latest < 1000 ? 2 : 0)}</strong><em class="${marketClass(row?.change_pct)}">${signedNumber(row?.change)}　${signedPercent(row?.change_pct)}</em></article>`;
  }).join("");
}

function renderFuturesIndices() {
  const order = ["850001", "850103", "850300", "850104", "850101", "850102", "850100", "850200", "USDIND", "sc9999"];
  const indices = futuresTable("indices");
  document.querySelector("#futures-index-grid").innerHTML = order.map((code) => {
    const row = indices[code] || {};
    return `<article class="futures-index-card"><span>${escapeHtml(row.name || code)}</span><small>${escapeHtml(code)}</small><strong>${formatNumber(row.latest, code === "USDIND" ? 3 : 2)}</strong><em class="${marketClass(row.change_rate)}">${signedPercent(row.change_rate)}</em></article>`;
  }).join("");
}

function renderFuturesFlow() {
  const inflow = futuresTableRows("fund_inflow").slice(0, 6).map((row) => ({ name: row["55"], value: futuresAmountYi(row["68"]) }));
  const outflow = futuresTableRows("fund_outflow").slice(0, 6).map((row) => ({ name: row["55"], value: futuresAmountYi(row["68"]) }));
  const rows = [...inflow, ...outflow].filter((row) => row.name && row.value !== null);
  const max = Math.max(1, ...rows.map((row) => Math.abs(row.value)));
  const chart = document.querySelector("#futures-flow-chart");
  chart.innerHTML = rows.length ? `<div class="futures-flow-zero"></div>${rows.map((row) => `<article title="${escapeHtml(row.name)} ${signedNumber(row.value)} 亿"><strong class="${marketClass(row.value)}">${signedNumber(row.value, 2)}</strong><div class="futures-flow-bar-slot ${row.value >= 0 ? "up" : "down"}"><i style="height:${Math.max(3, Math.abs(row.value) / max * 100)}%"></i></div><span>${escapeHtml(row.name)}</span></article>`).join("")}` : emptyBlock("暂无品种资金流向数据");
  setText("futures-flow-updated", `更新于 ${formatDateTime(futuresModule("fund_inflow")?.fetched_at || futuresModule("fund_outflow")?.fetched_at)}`);
}

function renderFuturesRanking() {
  if (!state.futuresMarket) return;
  const controls = state.futuresControls;
  const labels = { all: "全部", night: "夜盘", energy_chemical: "能源化工", nonferrous: "有色金属", precious: "贵金属", ferrous: "黑色金属", agriculture: "农产品", financial: "金融", shfe: "上期所", dce: "大商所", czce: "郑商所", ine: "上期能源", gfex: "广期所", cffex: "中金所" };
  const fieldLabels = { latest: "最新", change_pct: "涨幅", change: "涨跌", volume: "成交量", open_interest: "持仓量" };
  const direction = controls.direction === "desc" ? -1 : 1;
  const rows = futuresRankingRows(controls.ranking).sort((a, b) => {
    const av = a[controls.sort]; const bv = b[controls.sort];
    if (av === null) return 1; if (bv === null) return -1;
    return (av - bv) * direction;
  });
  document.querySelectorAll(".futures-ranking-table th[data-futures-sort]").forEach((heading) => {
    const active = heading.dataset.futuresSort === controls.sort;
    heading.classList.toggle("active-sort", active);
    heading.textContent = `${fieldLabels[heading.dataset.futuresSort]} ${active ? (controls.direction === "desc" ? "▼" : "▲") : "↕"}`;
  });
  setText("futures-ranking-context", `${labels[controls.ranking]} · ${fieldLabels[controls.sort]}${controls.direction === "desc" ? "降序" : "升序"}`);
  setText("futures-ranking-count", `${rows.length} 个合约 · 来源 ${formatClock(futuresModule(`ranking:${controls.ranking}`)?.fetched_at)}`);
  document.querySelector("#futures-ranking-body").innerHTML = rows.length ? rows.slice(0, controls.visible).map((row) => `<tr><td><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.code)}</small></td><td>${formatNumber(row.latest, row.latest && row.latest < 1000 ? 2 : 0)}</td><td class="${marketClass(row.change_pct)}">${signedPercent(row.change_pct)}</td><td class="${marketClass(row.change)}">${signedNumber(row.change)}</td><td>${escapeHtml(row.volume_text || "—")}</td><td>${escapeHtml(row.open_interest_text || "—")}</td></tr>`).join("") : tableEmpty(6, "暂无该分组数据");
  const more = document.querySelector("#futures-load-more");
  more.hidden = controls.visible >= rows.length;
}

async function loadEtfMarket(showToast = false) {
  if (state.etfMarketLoading) return;
  state.etfMarketLoading = true;
  setText("etf-market-status", "正在读取");
  try {
    const [response, rankingResponse, flowResponse] = await Promise.all([
      fetch("/api/market-observability/snapshots?data_type=ths_etf_zone&limit=1", { headers: { Accept: "application/json" }, cache: "no-store" }),
      fetch("/api/market-observability/snapshots?data_type=ths_etf_home_ranking&limit=10", { headers: { Accept: "application/json" }, cache: "no-store" }),
      fetch("/api/market-observability/history?data_type=etf_estimated_net_inflow&subject_id=cn%3Aetf%3Aszse%3Aestimated_net_inflow&limit=500", { headers: { Accept: "application/json" }, cache: "no-store" }),
    ]);
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    const payload = await response.json();
    const snapshot = (payload.items || [])[0];
    if (!snapshot) throw new Error("尚无 ETF 专区快照");
    const rankingPayload = rankingResponse.ok ? await rankingResponse.json() : { items: [] };
    const flowPayload = flowResponse.ok ? await flowResponse.json() : { items: [] };
    const latestRankings = {};
    (rankingPayload.items || []).forEach((item) => {
      const category = item.subject_id || item.data?.category;
      if (category && !latestRankings[category]) latestRankings[category] = item;
    });
    state.etfMarket = { snapshot, rankings: latestRankings };
    state.etfFlow = flowPayload.items || [];
    renderEtfMarket();
    if (showToast) showMessage("ETF 数据已刷新");
  } catch (error) {
    setText("etf-market-status", "读取失败");
    showMessage(`ETF 数据读取失败：${error.message}`);
  } finally {
    state.etfMarketLoading = false;
  }
}

function renderEtfMarket() {
  const snapshot = state.etfMarket.snapshot;
  if (!snapshot) return;
  const rankings = state.etfMarket.rankings || {};
  const rankingRows = (key) => rankings[key]?.data?.rows
    || (key === "t0" ? snapshot.data?.t0_fallback_ranking?.rows : []) || [];
  const fullRows = snapshot.data?.full_ranking?.rows || [];
  const complete = ["industry", "index", "t0"].every((key) => rankingRows(key).length >= 6)
    && (rankingRows("all").length >= 100 || fullRows.length >= 1000);
  setText("etf-market-status", complete ? "原生榜单完整" : "等待原生榜单");
  const status = document.querySelector("#etf-market-status");
  if (status) status.className = `status-badge ${complete ? "success" : "partial_success"}`;
  setText("etf-market-updated", `更新于 ${formatDateTime(snapshot.fetched_at)}`);
  renderEtfOverview();
  renderEtfHot();
  renderEtfRanking();
  renderEtfFlow();
  ["industry", "index", "t0"].forEach(renderEtfCategory);
}

function etfOverviewValues() {
  const raw = state.etfMarket?.snapshot?.data?.market_overview;
  const payload = raw?.data?.indexes ? raw.data : raw;
  const indexes = payload?.indexes || [];
  const values = payload?.data?.[0]?.values || [];
  return Object.fromEntries(indexes.map((item, index) => [item.index_id, number(values[index]?.value)]));
}

function renderEtfOverview() {
  const values = etfOverviewValues();
  const up = values.etfUpCount || 0;
  const down = values.etfDownCount || 0;
  const flat = values.etfEqCount || 0;
  const total = Math.max(1, up + down + flat);
  setText("etf-up-count", formatInteger(up));
  setText("etf-down-count", formatInteger(down));
  setText("etf-flat-count", formatInteger(flat));
  setText("etf-total-turnover", formatEtfAmount(values.etfTotalTurnoverMoney));
  setText("etf-turnover-change", `${values.etfTotalTurnoverMoneyChgPreDay >= 0 ? "+" : ""}${formatEtfAmount(values.etfTotalTurnoverMoneyChgPreDay)}`);
  document.querySelector("#etf-up-bar").style.width = `${(up / total) * 100}%`;
  document.querySelector("#etf-down-bar").style.width = `${(down / total) * 100}%`;
  document.querySelector("#etf-flat-bar").style.width = `${(flat / total) * 100}%`;
}

function etfQuoteMap() {
  const result = new Map();
  (state.etfMarket?.snapshot?.data?.etf_quotes || []).forEach((payload) => {
    ((payload?.data || {}).quote_data || []).forEach((quote) => {
      const fields = quote.data_fields || [];
      const values = quote.value?.[0] || [];
      const cell = (field) => {
        const index = fields.indexOf(field);
        return index >= 0 ? number(values[index]) : null;
      };
      result.set(String(quote.code), {
        latest: cell("10"),
        change_pct: cell("199112"),
        turnover_yuan: cell("19"),
        change_speed_pct: cell("264648"),
      });
    });
  });
  return result;
}

function renderEtfHot() {
  const container = document.querySelector("#etf-hot-grid");
  if (!container || !state.etfMarket) return;
  const source = state.etfMarket.snapshot.data?.hot_rankings?.[state.etfHot]?.data;
  const rawRows = Array.isArray(source) ? source : (source?.list || []);
  const quotes = etfQuoteMap();
  const seen = new Set();
  const rows = rawRows.map((item) => {
    const code = String(item.etfCode || item.code || "");
    return { ...item, displayCode: code, displayName: item.etfName || item.name, quote: quotes.get(code) };
  }).filter((item) => item.displayCode && !seen.has(item.displayCode) && seen.add(item.displayCode)).slice(0, 6);
  container.innerHTML = rows.length ? rows.map((item, index) => `<article><small>热榜 NO.${index + 1}</small><strong title="${escapeHtml(item.displayName)}">${escapeHtml(item.displayName || "—")}</strong><b class="${(item.quote?.change_pct || 0) >= 0 ? "up" : "down"}">${formatSignedPercent(item.quote?.change_pct)}</b><span>热度值 ${formatEtfHeat(item.rate)}</span></article>`).join("") : `<div class="etf-empty">暂无该类热榜</div>`;
}

function formatEtfHeat(value) {
  const parsed = number(value);
  if (parsed === null) return "—";
  return parsed >= 10000 ? `${(parsed / 10000).toFixed(1)}万` : formatInteger(parsed);
}

function etfUniverseRows() {
  const data = state.etfMarket?.snapshot?.data || {};
  const universe = data.etf_universe || {};
  const types = (universe.indexes || []).map((item) => item.type);
  const quotes = etfQuoteMap();
  const scales = data.etf_scales || {};
  return (universe.itemList || []).map((row) => {
    const item = Object.fromEntries(types.map((type, index) => [type, row[index]]));
    const code = String(item.tradeCode || "");
    return { name: item.simpleName, code, market: item.subMarket, scale_yuan: parseEtfAmount(scales[code]), ...(quotes.get(code) || {}) };
  });
}

function parseEtfAmount(value) {
  if (typeof value === "number") return value;
  const text = String(value || "").replace(/,/g, "").trim();
  const parsed = Number.parseFloat(text);
  if (!Number.isFinite(parsed)) return null;
  if (text.includes("万亿")) return parsed * 1000000000000;
  if (text.includes("亿")) return parsed * 100000000;
  if (text.includes("万")) return parsed * 10000;
  return parsed;
}

function renderEtfRanking() {
  const body = document.querySelector("#etf-ranking-body");
  if (!body || !state.etfMarket) return;
  const nativeAll = state.etfMarket.rankings?.all?.data?.rows || [];
  const fullRows = state.etfMarket.snapshot.data?.full_ranking?.rows || [];
  const trackRows = state.etfMarket.snapshot.data?.track_filtered_ranking?.rows || [];
  const trackingIndexRows = state.etfMarket.snapshot.data?.tracking_index_filtered_ranking?.rows || [];
  // ETF 首页原生榜单推送包含 App 实际展示的短名称，以及更新更及时的
  // 涨速、成交额和规模。全量 HTTP 行情只负责补齐榜单覆盖不到的标的，
  // 不能反过来用基金资料全称或较慢的规模接口覆盖原生榜单字段。
  const nativeRowsByCode = new Map();
  Object.values(state.etfMarket.rankings || {}).forEach((snapshot) => {
    (snapshot?.data?.rows || []).forEach((row) => {
      if (row?.code) nativeRowsByCode.set(String(row.code), row);
    });
  });
  const mergeNativeRankingFields = (sourceRows, preserveSourceMetrics = false) => sourceRows.map((row) => {
    const nativeRow = nativeRowsByCode.get(String(row.code));
    if (!nativeRow) return { ...row };
    if (preserveSourceMetrics) {
      // Track/index filters are independent THS result sets.  A fund may also
      // appear in the six-row industry/index cards, but those card values must
      // never overwrite the selected filter's own quote and ordering fields.
      return {
        ...row,
        name: nativeRow.name || row.name,
        native_display_name: Boolean(nativeRow.name),
      };
    }
    return {
      ...row,
      name: nativeRow.name || row.name,
      change_pct: number(nativeRow.change_pct) ?? row.change_pct,
      change_speed_pct: number(nativeRow.change_speed_pct) ?? row.change_speed_pct,
      turnover_yuan: number(nativeRow.turnover_yuan) ?? row.turnover_yuan,
      scale_yuan: number(nativeRow.scale_yuan) ?? row.scale_yuan,
      native_ranking_fields: true,
    };
  });
  let rows;
  if (state.etfRankingFilter === "track" && trackRows.length) rows = mergeNativeRankingFields(trackRows, true);
  else if (state.etfRankingFilter === "index" && trackingIndexRows.length) rows = mergeNativeRankingFields(trackingIndexRows, true);
  else rows = nativeAll.length >= 100
    ? mergeNativeRankingFields(nativeAll)
    : mergeNativeRankingFields(fullRows.length ? fullRows : etfUniverseRows());
  if (state.etfRankingCategory === "t0") {
    const members = new Set(state.etfMarket.snapshot.data?.native_t0_codes || []);
    rows = rows.filter((item) => members.has(`${item.market}:${item.code}`));
  } else if (["industry", "index"].includes(state.etfRankingCategory)) {
    const members = new Set((state.etfMarket.rankings?.[state.etfRankingCategory]?.data?.rows || []).map((item) => item.code));
    rows = rows.filter((item) => members.has(item.code));
  }
  const metric = state.etfRankingMetric;
  rows.sort((a, b) => (number(b[metric]) ?? -Infinity) - (number(a[metric]) ?? -Infinity));
  const visible = rows.slice(0, 20);
  const categoryName = { all: "全部", t0: "T+0", industry: "行业主题", index: "宽基" }[state.etfRankingCategory];
  const metricName = { change_pct: "涨幅榜", turnover_yuan: "成交榜", scale_yuan: "规模榜", change_speed_pct: "涨速榜" }[metric];
  setText("etf-ranking-context", `${categoryName} · ${metricName}`);
  const sourceLabel = state.etfRankingFilter === "track" && trackRows.length
    ? "同花顺赛道过滤"
    : (state.etfRankingFilter === "index" && trackingIndexRows.length
      ? "同花顺跟踪指数过滤"
      : (nativeAll.length >= 100 ? "原生长连接" : (fullRows.length ? "同花顺全量行情" : "兜底池")));
  setText("etf-ranking-count", `${formatInteger(rows.length)} 只 · ${sourceLabel}`);
  body.innerHTML = visible.length ? visible.map((item) => `<tr><td><strong>${escapeHtml(item.name || "—")}</strong><small>${escapeHtml(item.code)}</small></td><td>${formatNumber(item.latest, 3)}</td><td class="${(item.change_pct || 0) >= 0 ? "up" : "down"}">${formatSignedPercent(item.change_pct)}</td><td>${formatSignedPercent(item.change_speed_pct)}</td><td>${formatEtfAmount(item.turnover_yuan)}</td><td>${formatEtfAmount(item.scale_yuan)}</td></tr>`).join("") : tableEmpty(6, "暂无该分类 ETF 数据");
}

function renderEtfFlow() {
  const container = document.querySelector("#etf-flow-chart");
  if (!container) return;
  const latestTradeDate = state.etfFlow.find((item) => item.trade_date)?.trade_date;
  const rows = state.etfFlow.filter((item) => item.trade_date === latestTradeDate).reverse();
  const newestSnapshot = state.etfFlow.find((item) => item.trade_date === latestTradeDate);
  const benchmarkMap = new Map((newestSnapshot?.data?.benchmark_trend || []).map((item) => [
    Number(item.timestamp), number(item.index_value),
  ]));
  const pointsData = rows.map((item) => ({
    flow: number(item.data?.net_inflow_yuan),
    index: number(item.data?.benchmark_index_value) ?? benchmarkMap.get(Math.floor(new Date(item.observed_at || item.fetched_at).getTime() / 1000)) ?? null,
  })).filter((item) => item.flow !== null);
  const values = pointsData.map((item) => item.flow / 100000000);
  const indexValues = pointsData.map((item) => item.index).filter((item) => item !== null);
  const latest = rows.at(-1);
  setText("etf-flow-updated", latest ? `更新于 ${formatClock(latest.observed_at || latest.fetched_at)}` : "—");
  const top = latest?.data?.top_inflow;
  setText("etf-flow-summary", top ? `当日流入较多：${top.name} ${formatSignedEtfAmount(top.net_inflow_yuan)}` : `当日预估净流入 ${formatSignedEtfAmount(pointsData.at(-1)?.flow)}`);
  if (values.length < 2) { container.innerHTML = `<div class="etf-empty">暂无 ETF 资金分时数据</div>`; return; }
  const width = 900, height = 270, left = 54, right = 62, topPad = 20, bottom = 24;
  const plotWidth = width - left - right, plotHeight = height - topPad - bottom;
  const rawFlowMin = Math.min(...values, 0), rawFlowMax = Math.max(...values, 0);
  const flowStep = Math.max(10, Math.ceil((rawFlowMax - rawFlowMin) / 4 / 10) * 10);
  const flowMin = Math.floor(rawFlowMin / flowStep) * flowStep;
  const flowMax = Math.max(0, Math.ceil(rawFlowMax / flowStep) * flowStep);
  const flowSpan = Math.max(flowStep, flowMax - flowMin);
  const indexMin = indexValues.length ? Math.min(...indexValues) : 0;
  const indexMax = indexValues.length ? Math.max(...indexValues) : 1;
  const indexPadding = Math.max(1, (indexMax - indexMin) * 0.08);
  const rightMin = indexMin - indexPadding, rightMax = indexMax + indexPadding;
  const x = (position) => left + position * plotWidth / Math.max(1, values.length - 1);
  const flowY = (value) => topPad + (flowMax - value) * plotHeight / flowSpan;
  const indexY = (value) => topPad + (rightMax - value) * plotHeight / Math.max(1, rightMax - rightMin);
  const flowPoints = values.map((value, position) => `${x(position)},${flowY(value)}`).join(" ");
  const indexPoints = pointsData.map((item, position) => item.index === null ? null : `${x(position)},${indexY(item.index)}`).filter(Boolean).join(" ");
  const grid = Array.from({ length: 5 }, (_, position) => {
    const y = topPad + position * plotHeight / 4;
    const leftValue = flowMax - position * flowSpan / 4;
    const rightValue = rightMax - position * (rightMax - rightMin) / 4;
    return `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}"/><text x="4" y="${y+4}">${leftValue.toFixed(2)}</text>${indexValues.length ? `<text class="right-axis" x="${width-4}" y="${y+4}">${rightValue.toFixed(2)}</text>` : ""}`;
  }).join("");
  const area = `${left},${flowY(0)} ${flowPoints} ${x(values.length - 1)},${flowY(0)}`;
  container.innerHTML = `<div class="etf-flow-legend"><span><i></i>预估申购净流入(亿)</span><span><i></i>上证指数</span><b>当日</b><em>历史</em></div><svg viewBox="0 0 ${width} ${height}" aria-label="ETF 预估申购净流入与上证指数分时图"><g class="grid">${grid}</g><polygon class="flow-area" points="${area}"/><polyline class="flow-line" points="${flowPoints}"/>${indexPoints ? `<polyline class="index-line" points="${indexPoints}"/>` : ""}</svg><div><span>9:30</span><span>11:30 / 13:00</span><span>15:00</span></div>`;
}

function formatSignedEtfAmount(value) {
  const parsed = number(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : ""}${(parsed / 100000000).toFixed(2)} 亿`;
}

function renderEtfCategory(category) {
  const container = document.querySelector(`#etf-${category}-grid`);
  if (!container) return;
  const rankingSnapshot = state.etfMarket?.rankings?.[category];
  const sourceRows = rankingSnapshot?.data?.rows
    || (category === "t0" ? state.etfMarket?.snapshot?.data?.t0_fallback_ranking?.rows : [])
    || [];
  const liveRows = new Map(
    (state.etfMarket?.snapshot?.data?.full_ranking?.rows || []).map((item) => [String(item.code), item]),
  );
  const rows = sourceRows.map((item) => {
    const live = liveRows.get(String(item.code));
    if (!live) return item;
    return {
      ...item,
      latest: live.latest,
      change_pct: live.change_pct,
      change_speed_pct: live.change_speed_pct,
      turnover_yuan: live.turnover_yuan,
      scale_yuan: live.scale_yuan,
      live_quote_at: state.etfMarket?.snapshot?.fetched_at,
    };
  });
  const control = state.etfControls[category];
  const direction = control.direction === "asc" ? 1 : -1;
  const sorted = [...rows].sort((left, right) => {
    const a = number(left?.[control.metric]);
    const b = number(right?.[control.metric]);
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    return (a - b) * direction;
  }).slice(0, 6);
  if (!sorted.length) {
    container.innerHTML = `<div class="etf-empty">当前快照尚未包含同花顺 ${escapeHtml(etfCategoryName(category))} 原生榜单</div>`;
    return;
  }
  const sourceTime = rankingSnapshot?.observed_at || rankingSnapshot?.fetched_at
    || state.etfMarket?.snapshot?.fetched_at;
  container.innerHTML = sorted.map((row, index) => {
    const change = number(row.change_pct);
    const speed = number(row.change_speed_pct);
    const tone = change === null ? "neutral" : (change >= 0 ? "up" : "down");
    return `<article class="etf-card" title="同花顺源时间：${escapeHtml(formatDateTime(sourceTime))}；采集时间：${escapeHtml(formatDateTime(rankingSnapshot?.fetched_at || state.etfMarket?.snapshot?.fetched_at))}">
      <div class="etf-card-rank">${index + 1}</div>
      <header><div><strong>${escapeHtml(row.name || "—")}</strong><small>${escapeHtml(row.code || "—")}</small></div><b class="${tone}">${formatSignedPercent(change)}</b></header>
      <dl><div><dt>涨速</dt><dd class="${speed === null ? "neutral" : (speed >= 0 ? "up" : "down")}">${formatSignedPercent(speed)}</dd></div><div><dt>成交额</dt><dd>${formatEtfAmount(row.turnover_yuan)}</dd></div><div><dt>规模</dt><dd>${formatEtfAmount(row.scale_yuan)}</dd></div></dl>
    </article>`;
  }).join("");
}

function etfCategoryName(category) {
  return { industry: "行业主题", index: "宽基指数", t0: "T+0 精选" }[category] || category;
}

function formatSignedPercent(value) {
  const parsed = number(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(2)}%`;
}

function formatEtfAmount(value) {
  const parsed = number(value);
  if (parsed === null) return "—";
  if (Math.abs(parsed) >= 100_000_000) return `${(parsed / 100_000_000).toFixed(2)} 亿`;
  if (Math.abs(parsed) >= 10_000) return `${(parsed / 10_000).toFixed(2)} 万`;
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(parsed);
}

async function loadGoldMarket(showToast = false) {
  if (state.goldMarketLoading) return;
  state.goldMarketLoading = true;
  setText("gold-market-status", "正在读取");
  try {
    const [response, newsResponse] = await Promise.all([
      fetch("/api/market-observability/snapshots?data_type=ths_gold_module&limit=100", { headers: { Accept: "application/json" } }),
      fetch("/api/market-observability/gold-news?hours=24&limit=100", { headers: { Accept: "application/json" } }),
    ]);
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    const payload = await response.json();
    if (newsResponse.ok) state.goldNews = (await newsResponse.json()).data || [];
    state.goldMarket = buildGoldMarketModel(payload);
    renderGoldMarket();
    if (showToast) showMessage("黄金数据已刷新");
  } catch (error) {
    setText("gold-market-status", "读取失败");
    showMessage(`黄金数据读取失败：${error.message}`);
  } finally {
    state.goldMarketLoading = false;
  }
}

async function loadGoldRealtime() {
  if (state.goldRealtimeLoading || !state.goldMarket || state.activeView !== "gold" || document.hidden) return;
  state.goldRealtimeLoading = true;
  try {
    const subjects = ["quotes", "analytics", "opportunities", "content", "futures_quotes_stream", "stock_period_performance_stream"];
    const [responses, newsResponse] = await Promise.all([
      Promise.all(subjects.map((subject) => fetch(`/api/market-observability/history?data_type=ths_gold_module&subject_id=${subject}&limit=1`, { headers: { Accept: "application/json" } }))),
      fetch("/api/market-observability/gold-news?hours=24&limit=100", { headers: { Accept: "application/json" } }),
    ]);
    if (newsResponse.ok) state.goldNews = (await newsResponse.json()).data || state.goldNews;
    const snapshots = (await Promise.all(responses.filter((response) => response.ok).map((response) => response.json())))
      .flatMap((payload) => payload.items || []);
    if (!snapshots.length) return;
    snapshots.forEach((snapshot) => { state.goldMarket.modules[snapshot.subject_id] = snapshot; });
    state.goldMarket.updatedAt = snapshots.map((item) => item.fetched_at).filter(Boolean).sort().at(-1) || state.goldMarket.updatedAt;
    renderGoldMarket();
  } catch (_error) {
    // Full-page refresh is the recovery path.
  } finally {
    state.goldRealtimeLoading = false;
  }
}

function buildGoldMarketModel(payload) {
  const items = payload.items || [];
  return {
    modules: Object.fromEntries(items.map((item) => [item.subject_id, item])),
    updatedAt: items.map((item) => item.fetched_at).filter(Boolean).sort().at(-1),
  };
}

function responseData(value) {
  let current = value;
  for (let depth = 0; depth < 4; depth += 1) {
    if (!current || typeof current !== "object" || Array.isArray(current) || !("data" in current)) break;
    current = current.data;
  }
  return current;
}

function goldQuoteRows(source) {
  const payload = responseData(source);
  const rows = payload?.quote_data || payload?.list || (Array.isArray(payload) ? payload : []);
  return rows.map((row) => {
    const fields = row.data_fields || [];
    const values = Array.isArray(row.value?.[0]) ? row.value[0] : (Array.isArray(row.value) ? row.value : []);
    return { ...row, fields: Object.fromEntries(fields.map((field, index) => [String(field), values[index]])) };
  });
}

function goldModule(name) {
  return state.goldMarket?.modules?.[name]?.data || {};
}

function goldNested(name, key) {
  return responseData(goldModule(name)?.[key]);
}

function renderGoldMarket() {
  if (!state.goldMarket) return;
  setText("gold-market-status", "实时更新");
  setText("gold-market-updated", `更新于 ${formatDateTime(state.goldMarket.updatedAt)}`);
  renderGoldQuotes();
  renderGoldOpportunity();
  renderGoldBank();
  renderGoldSpread();
  renderGoldCapital();
  renderGoldNews();
  renderGoldReserve();
  renderGoldOffline();
  renderGoldRatio();
  renderGoldAnalytics();
}

function renderGoldQuotes() {
  const quotes = goldModule("quotes");
  const rows = goldQuoteRows(quotes.gold_market_quotes);
  const byCode = new Map(rows.map((row) => [String(row.code), row]));
  const klineRow = (source) => {
    const payload = responseData(source);
    const quote = payload?.quote_data?.[0];
    const latest = quote?.value?.at(-1);
    const previous = quote?.value?.at(-2);
    if (!quote || !latest) return null;
    const fields = Object.fromEntries((quote.data_fields || []).map((field, index) => [String(field), index]));
    const close = number(latest[fields["11"]]);
    const previousClose = number(previous?.[fields["11"]]);
    const change = close !== null && previousClose !== null ? close - previousClose : null;
    return { code: quote.code, market: quote.market, fallback: true, fields: { "10": close, "264648": change, "199112": previousClose ? change / previousClose * 100 : null } };
  };
  [
    ["AU9999", klineRow(quotes.domestic_gold_kline)],
    ["AGTD", klineRow(goldModule("analytics").domestic_silver_kline)],
    ["GC0W", klineRow(goldModule("analytics").gold_future_kline)],
    ["AGUSDO", klineRow(goldModule("analytics").silver_spot_kline)],
    ["SI0W", klineRow(goldModule("analytics").silver_future_kline)],
    ["AUUSDO", klineRow(quotes.international_gold_kline)],
    ["BRN0W", klineRow(goldModule("analytics").brent_kline)],
  ].forEach(([code, row]) => { if (row && !byCode.has(code)) byCode.set(code, row); });
  // 同花顺黄金页把伦敦金同时暴露为 218:AUUSDO 与 97:XAUUSD；部分时段
  // 218 快照不返回最新价，此时使用同一资产的 97 行情，避免卡片显示为空。
  if (!byCode.has("AUUSDO") && byCode.has("XAUUSD")) {
    byCode.set("AUUSDO", { ...byCode.get("XAUUSD"), code: "AUUSDO" });
  }
  const names = { AU9999: "黄金 9999", XAUUSD: "伦敦金现", au9999: "沪金主连", GC0W: "纽约金主连" };
  const cards = ["AU9999", "XAUUSD", "au9999", "GC0W"].map((code) => {
    const row = byCode.get(code);
    const latest = number(row?.fields?.["10"] ?? row?.latest);
    const pct = number(row?.fields?.["199112"] ?? row?.change_pct);
    const change = number(row?.fields?.["264648"] ?? row?.change);
    const category = { AU9999: "国内现货", XAUUSD: "国际现货", au9999: "国内期货", GC0W: "国际期货" }[code];
    return `<article class="gold-quote-card"><small class="gold-quote-category">${category}</small><span>${names[code]}</span><small>${code}${row?.fallback ? " · 日线回补" : ""}</small><strong>${formatNumber(latest, latest && latest < 100 ? 3 : 2)}</strong><em class="${marketClass(pct)}">${signedNumber(change)} · ${signedPercent(pct)}</em></article>`;
  });
  document.querySelector("#gold-quote-grid").innerHTML = cards.join("");
  const definitions = [
    ["银行黄金", "GF001", "银行黄金"], ["黄金概念", "885530", "黄金概念"], ["美元", "USDIND", "美元指数"], ["外汇", "USDCNH", "美元/人民币(离)"],
    ["黄金股票", "931238", "SSH 黄金股票"], ["外汇", "AUUSDO", "伦敦金"], ["国内现货", "AGTD", "白银 T+D"], ["国际现货", "AGUSDO", "伦敦银现"],
    ["国内期货", "ag9999", "沪银主连"], ["国际期货", "SI0W", "纽约银主连"], ["国际期货", "BRN0W", "布伦特原油主连"], ["美股 ETF", "IBIT", "iShares Bitcoin Trust"],
  ];
  document.querySelector("#gold-secondary-strip").innerHTML = definitions.map(([category, code, name]) => {
    const row = byCode.get(code); const latest = row?.fields?.["10"]; const pct = row?.fields?.["199112"]; const change = row?.fields?.["264648"];
    return `<article class="gold-matrix-card ${row ? "" : "unavailable"}"><small>${escapeHtml(category)}</small><span>${escapeHtml(name)}</span><i>${escapeHtml(code)}${row?.fallback ? " · 日线回补" : ""}</i><strong>${formatNumber(latest, number(latest) !== null && Math.abs(number(latest)) < 100 ? 4 : 2)}</strong><em class="${marketClass(pct)}">${signedNumber(change, 4)} · ${signedPercent(pct)}</em></article>`;
  }).join("");
}

function renderGoldOpportunity() {
  const type = state.goldControls.opportunity;
  const opportunities = goldModule("opportunities");
  let rows = [];
  if (type === "etf") {
    const liveQuotes = new Map(goldQuoteRows(goldModule("quotes").gold_etf_flow).map((row) => [String(row.code), row]));
    rows = (responseData(opportunities.gold_etf_rank)?.list || []).map((row) => ({ ...row, live: liveQuotes.get(String(row.tradeCode)) }));
  }
  if (type === "fund") rows = responseData(opportunities.gold_fund_rank)?.list || [];
  if (type === "stock") rows = nativeTableRows(state.goldMarket.modules.stock_period_performance_stream);
  if (type === "futures") rows = nativeTableRows(state.goldMarket.modules.futures_quotes_stream);
  const labels = { etf: "黄金 ETF", fund: "黄金基金", stock: "黄金股票", futures: "黄金期货" };
  const descriptions = { etf: "A股账户直接买　百元起购", stock: "业务涉及黄金产业链的上市公司", fund: "起购0.1元起　长期投资", futures: "多空双向　T+0　夜盘交易" };
  const sortOptions = {
    etf: [["recommend", "推荐"], ["change", "涨幅"], ["amount", "成交额"], ["premium", "溢价率"]],
    stock: [["change", "涨幅"], ["latest", "最新价"], ["turnover", "换手率"]],
    fund: [["recommend", "推荐"], ["year", "近1年"], ["hyear", "近6月"], ["tmonth", "近3月"]],
    futures: [["recommend", "推荐"], ["change", "涨幅"], ["latest", "最新价"], ["volume", "成交量"]],
  }[type];
  if (!sortOptions.some(([value]) => value === state.goldControls.opportunitySort)) state.goldControls.opportunitySort = sortOptions[0][0];
  const sort = state.goldControls.opportunitySort;
  const numeric = (value) => number(String(value ?? "").replace("%", "")) ?? -Infinity;
  if (sort !== "recommend") rows.sort((a, b) => {
    const value = (row) => type === "stock" ? ({ change: row["33001"], latest: row["10"], turnover: row["1968584"] }[sort])
      : type === "futures" ? ({ change: row["34818"], latest: row["10"], volume: row["13"] }[sort])
        : type === "etf" ? ({ change: row.live?.fields?.["199112"], amount: row.live?.fields?.["134238"], premium: row.premium }[sort]) : row[sort];
    return numeric(value(b)) - numeric(value(a));
  });
  if (type === "stock" && sort === "recommend") rows.sort((a, b) => numeric(b["33001"]) - numeric(a["33001"]));
  setText("gold-opportunity-context", labels[type]);
  document.querySelector("#gold-opportunity-sort").innerHTML = `<p>${descriptions[type]}</p><div>${sortOptions.map(([value, label]) => `<button type="button" data-value="${value}" class="${sort === value ? "active" : ""}">${label}</button>`).join("")}</div>`;
  document.querySelector("#gold-opportunity-body").innerHTML = rows.length ? rows.slice(0, 6).map((row) => {
    if (type === "etf") {
      const appName = { "518600": "金ETF广发", "518680": "金ETF富国", "159934": "黄金ETF易方达", "159834": "金ETF南方", "159831": "金ETF嘉实", "518850": "黄金ETF华夏" }[row.tradeCode] || row.simpleName;
      return `<article><span>${escapeHtml(appName || "—")}</span><strong class="${marketClass(row.live?.fields?.["199112"])}">${signedPercent(row.live?.fields?.["199112"])}</strong><small>规模${formatMoney(row.fundScale)}</small></article>`;
    }
    if (type === "fund") return `<article><i>近1年</i><span>${escapeHtml(String(row.simpleName || "—").replace("ETF联接", "…"))}</span><strong class="${marketClass(row.year)}">${signedPercent(row.year)}</strong><small>规模${formatMoney(row.fundScale)}</small></article>`;
    if (type === "stock") {
      const totalMarketValue = row.total_market_value ?? row["34306"];
      return `<article><span>${escapeHtml(row["55"] || "—")}</span><strong class="${marketClass(row["33001"])}">${signedPercent(row["33001"])}</strong><small>总市值${totalMarketValue ? formatMoney(totalMarketValue) : "—"}</small></article>`;
    }
    return `<article><span>${escapeHtml(row["55"] || "—")}</span><strong class="${marketClass(row["34818"])}">${signedPercent(String(row["34818"] || "").replace("%", ""))}</strong><small>持仓量${escapeHtml(row["65"] || "—")}</small></article>`;
  }).join("") : emptyBlock("暂无投资机会数据");
}

function renderGoldBank() {
  const rows = goldQuoteRows(goldModule("quotes").jewelry_quotes);
  document.querySelector("#gold-bank-body").innerHTML = rows.length ? rows.map((row) => `<div><span><b>${escapeHtml({ ZS001: "浙商积存金", MS001: "民生积存金", GF001: "广发积存金" }[row.code] || row.code)}</b><small>${escapeHtml(row.code)}</small></span><strong>${formatNumber(row.fields["10"], 2)}</strong><em class="${marketClass(row.fields["199112"])}">${signedPercent(row.fields["199112"])}</em></div>`).join("") : emptyBlock("暂无银行黄金报价");
}

function renderGoldSpread() {
  const analytics = goldModule("analytics");
  const buildSeries = (sources, domesticCode, overseasCode, options = {}) => {
    const quotes = sources.flatMap((source) => responseData(source)?.quote_data || []);
    const points = (code) => {
      const quote = quotes.find((row) => String(row.code) === code);
      if (!quote) return new Map();
      const fields = Object.fromEntries((quote.data_fields || []).map((field, index) => [String(field), index]));
      // 同花顺 218 市场的 AUUSDO 分钟时间戳按海外行情时区编码，
      // 比 81 市场黄金 9999 与 97 市场 USDCNH 少一小时；统一到北京时间轴后再按分钟连接。
      const timestampOffset = String(quote.market) === "218" ? 60 * 60 * 1000 : 0;
      return new Map((quote.value || []).map((row) => [Number(row[fields["1"]]) + timestampOffset, number(row[fields["11"]])]));
    };
    const domestic = points(domesticCode); const overseasRaw = points(overseasCode); const fx = points("USDCNH"); const result = [];
    // 纽约商品交易所免费行情约延迟 15 分钟。同花顺将延迟曲线平移到当前轴，
    // 并用沪金与纽约金各自最新值计算展示价差，而不是拿旧时刻沪金相减。
    const latestDomesticTime = Math.max(...domestic.keys()); const latestOverseasTime = Math.max(...overseasRaw.keys());
    const observedDelay = options.alignDelayedOverseas && Number.isFinite(latestDomesticTime) && Number.isFinite(latestOverseasTime)
      ? Math.max(0, Math.min(30 * 60 * 1000, latestDomesticTime - latestOverseasTime)) : 0;
    if (!Number.isFinite(latestDomesticTime)) return result;
    const sessionStart = new Date(latestDomesticTime); sessionStart.setHours(6, 0, 0, 0); if (latestDomesticTime < sessionStart.getTime()) sessionStart.setDate(sessionStart.getDate() - 1);
    const latestBefore = (map, timestamp) => { let found = null; map.forEach((value, time) => { if (time <= timestamp && (found === null || time > found[0])) found = [time, value]; }); return found?.[1] ?? null; };
    let local = latestBefore(domestic, sessionStart.getTime()); let raw = latestBefore(overseasRaw, sessionStart.getTime() - observedDelay); let rate = latestBefore(fx, sessionStart.getTime());
    for (let timestamp = sessionStart.getTime(); timestamp <= latestDomesticTime; timestamp += 60 * 1000) {
      if (domestic.has(timestamp)) local = domestic.get(timestamp);
      if (overseasRaw.has(timestamp - observedDelay)) raw = overseasRaw.get(timestamp - observedDelay);
      if (fx.has(timestamp)) rate = fx.get(timestamp);
      if (local === null || raw === null || rate === null) continue;
      const overseas = raw * rate / 31.1035;
      result.push({ timestamp, local, overseas, spread: local - overseas });
    }
    return result;
  };
  const spot = buildSeries([analytics.gold_spot_intraday_kline, analytics.gold_ratio_gold_intraday_kline, analytics.gold_fx_intraday_kline], "AU9999", "AUUSDO");
  const futures = buildSeries([analytics.gold_futures_intraday_kline], "au9999", "GC0W", { alignDelayedOverseas: true });
  const historical = (responseData(analytics.history_spread) || []).slice().reverse().map((row) => ({ timestamp: row.date, local: number(row.local), overseas: number(row.overseas), spread: number(row.spread) })).filter((row) => row.spread !== null);
  const selectedType = state.goldControls.spread; const selected = selectedType === "spot" ? spot : futures;
  const latestTimestamp = selected.at(-1)?.timestamp; const sessionRows = latestTimestamp ? selected.filter((row) => row.timestamp > latestTimestamp - 24 * 60 * 60 * 1000) : selected;
  const displayRows = sessionRows.length ? sessionRows : (selectedType === "spot" ? historical : []); const latest = displayRows.at(-1) || {};
  const spreadText = (value) => number(value) === null ? "—" : `国内${number(value) >= 0 ? "贵" : "便宜"} ${formatNumber(Math.abs(number(value)), 2)}`;
  setText("gold-spot-spread-label", spreadText((spot.at(-1) || historical.at(-1) || {}).spread));
  setText("gold-futures-spread-label", spreadText(futures.at(-1)?.spread));
  document.querySelector("#gold-spread-summary").innerHTML = [["#3977ee", `价差(元/克) ${signedNumber(latest.spread, 2)}`], ["#e69a16", `${selectedType === "spot" ? "黄金9999" : "沪金主连"} ${formatNumber(latest.local, 2)}`], ["#8a8f91", `${selectedType === "spot" ? "伦敦金现" : "纽约金主连"} ${formatNumber(latest.overseas, 2)}`]].map(([color, label]) => `<span><i style="background:${color}"></i>${escapeHtml(label)}</span>`).join("");
  document.querySelector("#gold-spread-chart").innerHTML = spreadLineChart([{ name: "价差", values: displayRows.map((row) => row.spread) }, { name: "国内", values: displayRows.map((row) => row.local) }, { name: "海外折算", values: displayRows.map((row) => row.overseas) }], selected.length ? 1440 : null);
  setText("gold-spread-explanation", `${selectedType === "spot" ? "伦敦金现" : "纽约金主连"}：单位为美元/盎司，兑换为人民币/克。海外金价 × 美元兑离岸人民币汇率 ÷ 31.1035`);
}

function renderGoldCapital() {
  const capital = goldModule("capital");
  const domestic = responseData(capital.domestic);
  const international = responseData(capital.international);
  const entries = (data) => Object.entries(data?.indicData || {}).sort(([a], [b]) => a.localeCompare(b)).map(([date, value]) => ({ date, value: number(value) }));
  const domesticRows = entries(domestic); const internationalRows = entries(international);
  const unitValue = (value) => number(value) === null ? "—" : formatNumber(number(value) / 1e8, 2);
  const latestLabel = (rows, unit) => rows.length ? `${rows.at(-1).date.slice(5).replace("-", ".")}净流入　${unitValue(rows.at(-1).value)}${unit}` : "—";
  setText("gold-capital-domestic-latest", latestLabel(domesticRows, "亿元"));
  setText("gold-capital-international-latest", latestLabel(internationalRows, "亿美元"));
  const type = state.goldControls.capital; const data = type === "domestic" ? domestic : international; const rows = type === "domestic" ? domesticRows : internationalRows; const unit = type === "domestic" ? "亿元" : "亿美元";
  const intervalMap = new Map((data?.intervals || []).map((interval, index) => [Number(interval), number(data?.intervalData?.[index])]));
  document.querySelector("#gold-capital-summary").innerHTML = [20, 10, 5, 3].map((days) => `<div><span>${days}日净流入</span><strong class="${marketClass(intervalMap.get(days))}">${signedNumber(number(intervalMap.get(days)) / 1e8, 2)}${unit}</strong></div>`).join("");
  const priceSource = type === "domestic" ? goldModule("quotes").domestic_gold_kline : goldModule("quotes").international_gold_kline;
  const priceQuote = responseData(priceSource)?.quote_data?.[0]; const fieldIndex = Object.fromEntries((priceQuote?.data_fields || []).map((field, index) => [String(field), index]));
  const prices = (priceQuote?.value || []).slice(-rows.length).map((row) => number(row[fieldIndex["11"]]));
  document.querySelector("#gold-capital-legend").innerHTML = `<span><i class="gold-bar-key"></i>申购资金净流入(${unit})</span><span><i style="background:#3977ee"></i>${type === "domestic" ? "黄金9999" : "伦敦金现"}</span>`;
  document.querySelector("#gold-capital-chart").innerHTML = capitalFlowChart(rows.map((row) => number(row.value) / 1e8), prices);
  setText("gold-capital-explanation", type === "international" ? "美国市场：选取美股市场规模较大的黄金ETF，包括GLD、IAU、GLDM、SGOL和IAUM；申购资金大幅净流入表明买盘增强。" : "中国市场：统计境内主要黄金ETF申购资金净流入，并与黄金9999价格走势对照。" );
}

function renderGoldNews() {
  const content = goldModule("content");
  const persistedRows = state.goldNews.map((row) => ({ news_ai_summary: row.summary || row.title, news_create_time: row.published_at, news_source: row.source_name, news_pc_url: row.url }));
  const rows = persistedRows.length ? persistedRows : (responseData(content.gold_ai_summary_list) || []);
  const count = number(responseData(content.gold_ai_summary_count));
  const systemTime = number(responseData(content.gold_ai_system_time));
  const cutoff = systemTime ? new Date(systemTime * 1000) : null;
  const cutoffLabel = cutoff && !Number.isNaN(cutoff.getTime()) ? `${String(cutoff.getHours()).padStart(2, "0")}:${String(cutoff.getMinutes()).padStart(2, "0")}` : "—";
  document.querySelector("#gold-ai-overview").innerHTML = `<span class="gold-ai-icon">AI</span><b>近24小时·黄金相关事件共 <em>${formatInteger(count)}</em> 件</b><small>截止于${cutoffLabel}</small>`;
  const sorted = rows.slice().sort((a, b) => String(b.news_create_time || "").localeCompare(String(a.news_create_time || "")));
  const latest = sorted.slice(0, state.goldAiVisible);
  const moreButton = document.querySelector("#gold-ai-more");
  moreButton.dataset.total = String(sorted.length);
  moreButton.hidden = sorted.length <= 10;
  moreButton.textContent = state.goldAiVisible >= sorted.length ? "收起" : `查看更多（${Math.min(state.goldAiVisible, sorted.length)}/${sorted.length}）›`;
  document.querySelector("#gold-news-list").innerHTML = latest.length ? latest.map((row) => {
    const time = formatDateTime(row.news_create_time).slice(6, 11);
    const url = row.news_pc_url || row.news_url || "#";
    return `<article><time>${escapeHtml(time)}</time><div><p>${escapeHtml(row.news_ai_summary || "暂无事件摘要")}</p><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">🔗 来源：${escapeHtml(row.news_source || "同花顺")}<span>›</span></a></div></article>`;
  }).join("") : emptyBlock("暂无黄金AI要点事件");
}

function renderGoldReserve() {
  const reserve = goldModule("reserve");
  const nativeTabs = responseData(reserve.tabs) || [];
  const year = responseData(reserve.year_up) || [];
  const tabRows = (nativeTabs.length ? nativeTabs : year).filter((row, index, array) => row.index_id && array.findIndex((item) => String(item.index_id) === String(row.index_id)) === index).slice(0, 6);
  const tabs = document.querySelector("#gold-reserve-tabs");
  if (!tabRows.length) {
    tabs.innerHTML = "";
    document.querySelector("#gold-reserve-summary").innerHTML = "";
    document.querySelector("#gold-reserve-legend").innerHTML = "";
    document.querySelector("#gold-reserve-chart").innerHTML = emptyBlock("暂无官方黄金储备数据");
    setText("gold-reserve-explanation", "");
    return;
  }
  const requested = state.goldControls.reserve;
  const selected = tabRows.find((row) => String(row.index_id) === requested) || tabRows[0];
  state.goldControls.reserve = String(selected.index_id);
  tabs.innerHTML = tabRows.map((row) => `<button type="button" data-value="${escapeHtml(row.index_id)}" class="${String(selected.index_id) === String(row.index_id) ? "active" : ""}">${escapeHtml(row.name)}</button>`).join("");
  const allCurve = responseData(reserve.curves?.[`${selected.index_id}:all`]) || {};
  const upCurve = responseData(reserve.curves?.[`${selected.index_id}:up`]) || {};
  const allTimes = (allCurve.time_range || []).slice().reverse().slice(-24);
  const allValues = (allCurve.values || []).slice().reverse().slice(-24).map(number);
  const upTimes = (upCurve.time_range || []).slice().reverse().slice(-24);
  const upValues = (upCurve.values || []).slice().reverse().slice(-24).map(number);
  const incrementByMonth = new Map(upTimes.map((time, index) => [String(time), upValues[index]]));
  const increments = allTimes.map((time, index) => incrementByMonth.has(String(time)) ? incrementByMonth.get(String(time)) : upValues[index] ?? null);
  const latestTime = allTimes.at(-1) || upTimes.at(-1) || "";
  const updateTime = allCurve.update_time || upCurve.update_time || latestTime;
  const latestIncrement = increments.at(-1);
  const monthMatch = String(latestTime).match(/(?:^|[-/.])(\d{1,2})(?:$|[-/.])/);
  const monthLabel = monthMatch ? `${Number(monthMatch[1])}月` : "本月";
  const direction = latestIncrement !== null && latestIncrement < 0 ? "减持" : "增持";
  document.querySelector("#gold-reserve-summary").innerHTML = `<strong>${monthLabel}${direction}${latestIncrement === null ? "—" : formatNumber(Math.abs(latestIncrement), 2)}吨</strong><small>数据更新时间：${escapeHtml(String(updateTime).slice(0, 7).replace("-", "."))}</small>`;
  document.querySelector("#gold-reserve-legend").innerHTML = `<span><i style="background:#3977ee"></i>官方黄金储备(吨)</span><span><i class="gold-bar-key"></i>较上月增持(吨)</span>`;
  document.querySelector("#gold-reserve-chart").innerHTML = reserveFlowChart(allValues, increments, allTimes);
  setText("gold-reserve-explanation", selected.name === "全球" ? "官方增持黄金是金价上涨的重要支撑，近10年增持最多的国家是俄罗斯、中国、土耳其、波兰和印度，增持量约占全球官方总增持量的80%。" : `${selected.name}官方黄金储备与月度增减持变化。`);
}

function renderGoldOffline() {
  const type = state.goldControls.offline;
  const sourceKeys = { jewelry: "jewelry_prices", goldBar: "gold_bar_prices", bank: "bank_gold_prices", recycle: "recycle_gold_prices" };
  const data = responseData(goldModule("offline_price")[sourceKeys[type]]) || {};
  const labels = { jewelry: "金店首饰金", goldBar: "金店金条", bank: "银行金条", recycle: "回收" };
  setText("gold-offline-context", labels[type]);
  const refPrice = number(data.ref?.price);
  const refDate = String(data.ref?.date || "");
  const refDateLabel = refDate.length >= 12 ? `${refDate.slice(4, 6)}-${refDate.slice(6, 8)} ${refDate.slice(8, 10)}:${refDate.slice(10, 12)}` : refDate;
  document.querySelector("#gold-offline-ref").innerHTML = data.ref ? `<span>${escapeHtml(data.ref.name || "黄金9999")}价格参考(${escapeHtml(refDateLabel)})</span><strong>${formatNumber(refPrice, 2)}</strong>` : "";
  const rows = data.list || [];
  document.querySelector("#gold-offline-body").innerHTML = rows.length ? `<table class="gold-offline-table"><thead><tr><th>品牌名称(日期)</th><th>品牌金价</th><th><span title="品牌金价减去黄金9999参考价">ⓘ 溢价参考</span></th></tr></thead><tbody>${rows.map((row) => {
    const premium = refPrice === null || number(row.price) === null ? null : number(row.price) - refPrice;
    const date = String(row.date || ""); const dateLabel = date.length >= 10 ? date.slice(5) : date;
    return `<tr><td><strong>${escapeHtml(row.name || "—")}(${escapeHtml(dateLabel)})</strong></td><td>${formatNumber(row.price, 2)}</td><td class="${marketClass(premium)}">${signedNumber(premium, 2)}</td></tr>`;
  }).join("")}</tbody></table><p class="gold-offline-tip">提示：上述价格仅供参考，以门店实际报价为准</p>` : emptyBlock("暂无线下黄金价格");
}

function renderGoldAnalytics() {
  const analytics = goldModule("analytics");
  // 指标接口的业务对象本身也包含 data 字段，不能使用会递归拆壳的 responseData。
  const indicatorPayload = (source) => {
    let current = source;
    for (let depth = 0; depth < 3 && current && typeof current === "object" && !Array.isArray(current); depth += 1) {
      if (Array.isArray(current.data) && Array.isArray(current.time_range)) return current;
      current = current.data;
    }
    return {};
  };
  const silverData = indicatorPayload(analytics.gold_silver_correlation);
  const silverRows = silverData.data || [];
  const silverValues = silverRows[0]?.values || [];
  const performanceSimilarity = (silverValues.find((item) => Number(item.idx) === 0)?.values || []).map(number);
  const elasticity = (silverValues.find((item) => Number(item.idx) === 1)?.values || []).map(number);
  const klineValues = (source) => {
    const quote = responseData(source)?.quote_data?.[0];
    const fields = Object.fromEntries((quote?.data_fields || []).map((field, index) => [String(field), index]));
    return (quote?.value || []).map((row) => number(row[fields["11"]]));
  };
  const londonGold = klineValues(goldModule("quotes").international_gold_kline);
  const londonSilver = klineValues(analytics.silver_spot_kline);
  document.querySelector("#gold-silver-correlation-chart").innerHTML = goldCorrelationChart(elasticity, performanceSimilarity, londonGold, londonSilver, silverData.time_range || [], { leftName: "伦敦金现", rightName: "伦敦银现", leftColor: "#aa4eb4", rightColor: "#777", maxPoints: 264 });
  setText("gold-silver-correlation-explanation", "金银价格走势长期高度正相关，银价涨幅弹性更大，适合风险偏好较高的投资者。");
  const stockData = indicatorPayload(analytics.gold_stock_correlation);
  const stockValues = stockData.data?.[0]?.values || [];
  const stockSimilarity = (stockValues.find((item) => Number(item.idx) === 0)?.values || []).map(number);
  const stockElasticity = (stockValues.find((item) => Number(item.idx) === 1)?.values || []).map(number);
  const au9999 = klineValues(goldModule("quotes").domestic_gold_kline);
  const sshGoldStock = klineValues(analytics.ssh_gold_stock_kline);
  document.querySelector("#gold-stock-correlation-chart").innerHTML = goldCorrelationChart(stockElasticity, stockSimilarity, au9999, sshGoldStock, stockData.time_range || [], { leftName: "AU9999", rightName: "SSH黄金股票", leftColor: "#aa4eb4", rightColor: "#777", maxPoints: 246 });
  setText("gold-stock-correlation-explanation", "黄金股指数和AU9999价格变动方向长期高度正相关，同涨同跌；但前者涨跌弹性更大，适合风险偏好高的投资者。");
  const seasonality = responseData(analytics.seasonality_statistics)?.data || responseData(analytics.seasonality_statistics) || [];
  const seasonCode = state.goldControls.seasonality === "futures" ? "65:au9999" : "81:AU9999";
  const item = seasonality.find((row) => row.code === seasonCode) || seasonality[0] || {};
  const series = (item.values || []).map((entry) => { try { return JSON.parse(entry.value || entry); } catch (_error) { return {}; } });
  const probabilities = Array.from({ length: 12 }, (_, index) => number(series[0]?.[String(index + 1)]));
  const averages = Array.from({ length: 12 }, (_, index) => number(series[1]?.[String(index + 1)]));
  setText("gold-seasonality-spot-latest", `8月上涨概率 ${formatNumber(number(JSON.parse(seasonality.find((row) => row.code === "81:AU9999")?.values?.[0]?.value || "{}")["8"]), 1)}%`);
  setText("gold-seasonality-futures-latest", `8月上涨概率 ${formatNumber(number(JSON.parse(seasonality.find((row) => row.code === "65:au9999")?.values?.[0]?.value || "{}")["8"]), 1)}%`);
  document.querySelector("#gold-seasonality-legend").innerHTML = `<span><i style="background:#3977ee;height:9px"></i>上涨概率</span><span><i style="background:#d78b17"></i>月均涨幅</span>`;
  document.querySelector("#gold-seasonality-chart").innerHTML = seasonalityChart(probabilities, averages);
  const highest = probabilities.indexOf(Math.max(...probabilities.filter((value) => value !== null))) + 1;
  const lowest = probabilities.indexOf(Math.min(...probabilities.filter((value) => value !== null))) + 1;
  setText("gold-seasonality-explanation", `根据历史10年数据，${state.goldControls.seasonality === "futures" ? "国内黄金期货" : "国内黄金现货"}价格${highest}月上涨概率最高，${lowest}月下跌概率最高，呈现明显的季节性规律。`);
}

function renderGoldRatio() {
  const analytics = goldModule("analytics");
  const klineValues = (source) => {
    const quote = responseData(source)?.quote_data?.[0];
    const fields = Object.fromEntries((quote?.data_fields || []).map((field, index) => [String(field), index]));
    return (quote?.value || []).map((row) => number(row[fields["11"]]));
  };
  const intradaySeries = (source) => {
    const payload = responseData(source); const result = {};
    (payload?.quote_data || []).forEach((quote) => {
      const fields = Object.fromEntries((quote.data_fields || []).map((field, index) => [String(field), index]));
      result[quote.code] = (quote.value || []).map((row) => ({ time: number(row[fields["1"]]), value: number(row[fields["11"]]) })).filter((row) => row.time !== null && row.value !== null);
    });
    return result;
  };
  const spotIntraday = { ...intradaySeries(analytics.gold_ratio_gold_intraday_kline), ...intradaySeries(analytics.gold_ratio_silver_intraday_kline) }; const futuresIntraday = intradaySeries(analytics.gold_silver_futures_intraday_kline);
  const buildRatio = (source, goldCode, silverCode) => {
    const silverMap = new Map((source[silverCode] || []).map((row) => [row.time, row.value]));
    return (source[goldCode] || []).map((row) => ({ time: row.time, gold: row.value, silver: silverMap.get(row.time) })).filter((row) => row.silver).map((row) => ({ ...row, ratio: row.gold / row.silver }));
  };
  const spotRatio = buildRatio(spotIntraday, "AUUSDO", "AGUSDO"); let futuresRatio = buildRatio(futuresIntraday, "GC0W", "SI0W");
  if (!futuresRatio.length) {
    const futureGold = klineValues(analytics.gold_future_kline); const futureSilver = klineValues(analytics.silver_future_kline); const count = Math.min(futureGold.length, futureSilver.length);
    futuresRatio = futureGold.slice(-count).map((value, index) => ({ gold: value, silver: futureSilver.slice(-count)[index] })).filter((row) => row.gold !== null && row.silver).map((row) => ({ ...row, ratio: row.gold / row.silver }));
  }
  const quoteMap = new Map(goldQuoteRows(goldModule("quotes").gold_market_quotes).map((row) => [String(row.code), number(row.fields?.["10"])]));
  const quoteRatio = (goldCode, silverCode, fallback) => { const goldValue = quoteMap.get(goldCode); const silverValue = quoteMap.get(silverCode); return goldValue !== null && goldValue !== undefined && silverValue ? goldValue / silverValue : fallback; };
  const spotLatest = spotRatio.at(-1)?.ratio ?? quoteRatio("AUUSDO", "AGUSDO", null); const futuresLatest = quoteRatio("GC0W", "SI0W", futuresRatio.at(-1)?.ratio);
  setText("gold-ratio-spot-latest", Number.isFinite(spotLatest) ? formatNumber(spotLatest, 2) : "—");
  setText("gold-ratio-futures-latest", Number.isFinite(futuresLatest) ? formatNumber(futuresLatest, 2) : "—");
  const now = new Date(); const sessionStart = new Date(now); sessionStart.setHours(8, 0, 0, 0); if (now < sessionStart) sessionStart.setDate(sessionStart.getDate() - 1);
  const elapsedSessionMinutes = Math.min(1440, Math.max(1, Math.floor((now - sessionStart) / 60000) + 1));
  const ratioRows = state.goldControls.ratio === "futures" ? futuresRatio : spotRatio.slice(-elapsedSessionMinutes); const ratioNames = state.goldControls.ratio === "futures" ? ["纽约金主连", "纽约银主连"] : ["伦敦金现", "伦敦银现"];
  const latestRatio = ratioRows.at(-1)?.ratio;
  document.querySelector("#gold-ratio-legend").innerHTML = `<span><i style="background:#3977ee"></i>金银比 ${formatNumber(latestRatio, 2)}</span><span><i style="background:#d78b17"></i>${ratioNames[0]} ${formatNumber(ratioRows.at(-1)?.gold, 3)}</span><span><i style="background:#777"></i>${ratioNames[1]} ${formatNumber(ratioRows.at(-1)?.silver, 3)}</span>`;
  document.querySelector("#gold-ratio-chart").innerHTML = normalizedMultiLineChart([{ values: ratioRows.map((row) => row.ratio), color: "#3977ee" }, { values: ratioRows.map((row) => row.gold), color: "#d78b17" }, { values: ratioRows.map((row) => row.silver), color: "#777" }], state.goldControls.ratio === "spot" ? 1440 : null);
  setText("gold-ratio-explanation", `${state.goldControls.ratio === "futures" ? "期货" : "现货"}金银比：黄金价格/白银价格，表示1单位黄金可兑换的白银数量。`);
}

function metricCards(items) {
  return items.map(([name, value]) => `<div><span>${escapeHtml(name)}</span><strong>${typeof value === "string" ? escapeHtml(value) : formatNumber(value, 2)}</strong></div>`).join("");
}

function lineChart(series) {
  const usable = series.map((item) => ({ ...item, values: (item.values || []).filter((value) => value !== null) })).filter((item) => item.values.length > 1);
  if (!usable.length) return emptyBlock("暂无趋势数据");
  const all = usable.flatMap((item) => item.values);
  const min = Math.min(...all); const max = Math.max(...all); const range = max - min || 1;
  const colors = ["#b88720", "#2563a7", "#087a55"];
  const paths = usable.map((item, seriesIndex) => {
    const points = item.values.map((value, index) => `${10 + (index / (item.values.length - 1)) * 580},${105 - ((value - min) / range) * 90}`).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${colors[seriesIndex % colors.length]}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  }).join("");
  return `<div class="gold-chart-legend">${usable.map((item, index) => `<span><i style="background:${colors[index % colors.length]}"></i>${escapeHtml(item.name)}</span>`).join("")}</div><svg viewBox="0 0 600 120" preserveAspectRatio="none"><path d="M10 105H590" stroke="#d8dedb"/>${paths}</svg>`;
}

function spreadLineChart(series, totalPoints = null) {
  const colors = ["#3977ee", "#e69a16", "#8a8f91"];
  const usable = series.map((item) => ({ ...item, values: (item.values || []).map(number) })).filter((item) => item.values.filter((value) => value !== null).length > 1);
  if (!usable.length) return emptyBlock("等待同花顺分时价差数据");
  const scale = (values) => { const finite = values.filter((value) => value !== null); const min = Math.min(...finite); const max = Math.max(...finite); const padding = Math.max((max - min) * .08, .01); return { min: min - padding, max: max + padding, range: max - min + padding * 2 }; };
  const spreadScale = scale(usable[0]?.values || []);
  // 国内、海外折算价格必须共用右轴；分别归一化会伪造两条曲线的相对走势。
  const priceScale = scale(usable.slice(1).flatMap((item) => item.values));
  const paths = usable.map((item, seriesIndex) => {
    const axis = seriesIndex === 0 ? spreadScale : priceScale;
    const points = item.values.map((value, index) => value === null ? null : `${28 + index / Math.max(1, (totalPoints || item.values.length) - 1) * 544},${108 - (value - axis.min) / axis.range * 92}`).filter(Boolean).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${colors[seriesIndex]}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  }).join("");
  return `<svg viewBox="0 0 600 140" preserveAspectRatio="none"><rect x="153" y="16" width="45" height="92" fill="#7f8582" opacity=".06"/><rect x="243" y="16" width="125" height="92" fill="#7f8582" opacity=".06"/><rect x="493" y="16" width="79" height="92" fill="#7f8582" opacity=".06"/><path d="M28 16H572M28 62H572M28 108H572" stroke="#d8dedb" stroke-width=".7"/>${paths}<text x="28" y="136" fill="#8a918d" font-size="9">06:00</text><text x="300" y="136" text-anchor="middle" fill="#8a918d" font-size="9">17:59</text><text x="572" y="136" text-anchor="end" fill="#8a918d" font-size="9">05:59</text></svg>`;
}

function capitalFlowChart(flows, prices) {
  const cleanFlows = flows.map(number); const cleanPrices = prices.map(number);
  if (!cleanFlows.some((value) => value !== null)) return emptyBlock("暂无资金流向数据");
  const maxFlow = Math.max(...cleanFlows.filter((value) => value !== null).map(Math.abs), 1);
  const finitePrices = cleanPrices.filter((value) => value !== null); const minPrice = Math.min(...finitePrices); const maxPrice = Math.max(...finitePrices); const priceRange = maxPrice - minPrice || 1;
  const width = 600; const baseline = 63; const step = 580 / Math.max(1, cleanFlows.length); const barWidth = Math.min(18, step * .62);
  const bars = cleanFlows.map((value, index) => {
    if (value === null) return ""; const height = Math.abs(value) / maxFlow * 47; const x = 10 + index * step + (step - barWidth) / 2; const y = value >= 0 ? baseline - height : baseline;
    return `<rect x="${x}" y="${y}" width="${barWidth}" height="${height}" rx="1.5" fill="${value >= 0 ? "#ef334f" : "#0faf61"}"/>`;
  }).join("");
  const points = cleanPrices.map((value, index) => value === null ? null : `${10 + index * step + step / 2},${108 - (value - minPrice) / priceRange * 92}`).filter(Boolean).join(" ");
  return `<svg viewBox="0 0 600 120" preserveAspectRatio="none"><path d="M10 ${baseline}H590" stroke="#aeb8b3" stroke-width=".7"/>${bars}<polyline points="${points}" fill="none" stroke="#3977ee" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

function reserveFlowChart(reserves, increments, times) {
  const cleanReserves = reserves.map(number); const cleanIncrements = increments.map(number);
  const finiteReserves = cleanReserves.filter((value) => value !== null);
  if (finiteReserves.length < 2) return emptyBlock("暂无储备趋势数据");
  const rawMinReserve = Math.min(...finiteReserves); const rawMaxReserve = Math.max(...finiteReserves);
  const niceStep = (range) => { const rough = Math.max(range / 4, 1); const power = 10 ** Math.floor(Math.log10(rough)); const normalized = rough / power; return (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * power; };
  const reserveStep = niceStep(rawMaxReserve - rawMinReserve); const minReserve = Math.floor(rawMinReserve / reserveStep) * reserveStep; const maxReserve = Math.ceil(rawMaxReserve / reserveStep) * reserveStep; const reserveRange = maxReserve - minReserve || 1;
  const maxIncrement = Math.max(...cleanIncrements.filter((value) => value !== null).map(Math.abs), 1);
  const incrementLimit = Math.ceil(maxIncrement / niceStep(maxIncrement * 2)) * niceStep(maxIncrement * 2); const width = 600; const left = 46; const right = 554; const top = 14; const bottom = 174; const baseline = (top + bottom) / 2;
  const step = (right - left) / Math.max(1, cleanReserves.length - 1); const barWidth = Math.min(14, step * .58);
  const bars = cleanIncrements.map((value, index) => {
    if (value === null) return ""; const height = Math.abs(value) / incrementLimit * (bottom - top) / 2; const x = left + index * step - barWidth / 2; const y = value >= 0 ? baseline - height : baseline;
    return `<rect x="${x}" y="${y}" width="${barWidth}" height="${height}" rx="1" fill="${value >= 0 ? "#ef334f" : "#0faf61"}" opacity=".86"/>`;
  }).join("");
  const points = cleanReserves.map((value, index) => value === null ? null : `${left + index * step},${bottom - (value - minReserve) / reserveRange * (bottom - top)}`).filter(Boolean).join(" ");
  const labelIndexes = [...new Set([0, Math.floor((times.length - 1) / 2), times.length - 1])].filter((index) => index >= 0);
  const xLabels = labelIndexes.map((index) => `<span class="reserve-x-label${index === 0 ? " reserve-x-start" : index === times.length - 1 ? " reserve-x-end" : ""}" style="left:${(left + index * step) / width * 100}%">${escapeHtml(String(times[index] || "").slice(0, 7))}</span>`).join("");
  const axisTicks = [0, .25, .5, .75, 1];
  const leftLabels = axisTicks.map((ratio) => `<span class="reserve-y-label reserve-y-left" style="top:${top / 205 * 100 + ratio * (bottom - top) / 205 * 100}%">${formatNumber(maxReserve - ratio * reserveRange, 0)}</span>`).join("");
  const rightLabels = axisTicks.map((ratio) => `<span class="reserve-y-label reserve-y-right" style="top:${top / 205 * 100 + ratio * (bottom - top) / 205 * 100}%">${formatNumber(incrementLimit * (1 - ratio * 2), 0)}</span>`).join("");
  const grid = axisTicks.map((ratio) => { const y = top + ratio * (bottom - top); return `M${left} ${y}H${right}`; }).join("");
  return `<div class="reserve-chart-canvas"><svg viewBox="0 0 600 205" preserveAspectRatio="none"><path d="${grid}" stroke="#d8dedb" stroke-width=".7" vector-effect="non-scaling-stroke"/><path d="M${left} ${baseline}H${right}" stroke="#aeb8b3" stroke-width="1" vector-effect="non-scaling-stroke"/>${bars}<polyline points="${points}" fill="none" stroke="#3977ee" stroke-width="2.3" vector-effect="non-scaling-stroke"/></svg>${leftLabels}${rightLabels}${xLabels}</div>`;
}

function goldCorrelationChart(elasticity, similarity, gold, silver, times, options = {}) {
  const count = options.maxPoints || 264;
  const trim = (values) => values.slice(-count).map(number);
  const topLeft = trim(elasticity); const topRight = trim(similarity);
  const lowerLeft = trim(gold); const lowerRight = trim(silver);
  if (topLeft.filter((value) => value !== null).length < 2) return emptyBlock("暂无金银相关性数据");
  const panel = (leftValues, rightValues, yTop, yBottom, leftColor, rightColor) => {
    const scale = (values) => { const finite = values.filter((value) => value !== null); const min = Math.min(...finite); const max = Math.max(...finite); return { min, max, range: max - min || 1 }; };
    const leftScale = scale(leftValues); const rightScale = scale(rightValues); const width = 600; const x0 = 38; const x1 = 566;
    const path = (values, axis, color) => {
      const points = values.map((value, index) => value === null ? null : `${x0 + index / Math.max(1, values.length - 1) * (x1 - x0)},${yBottom - (value - axis.min) / axis.range * (yBottom - yTop)}`).filter(Boolean).join(" ");
      return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/>`;
    };
    return `<path d="M${x0} ${yTop}H${x1}M${x0} ${(yTop + yBottom) / 2}H${x1}M${x0} ${yBottom}H${x1}" stroke="#d8dedb" stroke-width=".6"/>${path(leftValues, leftScale, leftColor)}${path(rightValues, rightScale, rightColor)}<text x="${x0}" y="${yTop + 10}" fill="#8a918d" font-size="9">${formatNumber(leftScale.max, 0)}</text><text x="${x0}" y="${yBottom - 3}" fill="#8a918d" font-size="9">${formatNumber(leftScale.min, 0)}</text><text x="${x1}" y="${yTop + 10}" text-anchor="end" fill="#8a918d" font-size="9">${formatNumber(rightScale.max, 0)}</text><text x="${x1}" y="${yBottom - 3}" text-anchor="end" fill="#8a918d" font-size="9">${formatNumber(rightScale.min, 0)}</text>`;
  };
  const dateValues = times.slice(-topLeft.length); const dateLabel = (timestamp) => { const value = number(timestamp); return value === null ? "" : new Date(value * 1000).toISOString().slice(0, 10); };
  const leftColor = options.leftColor || "#aa4eb4"; const rightColor = options.rightColor || "#777";
  return `<div class="gold-chart-legend"><span><i style="background:#3977ee"></i>弹性系数</span><span><i style="background:#d78b17"></i>业绩相似度</span></div><svg viewBox="0 0 600 300" preserveAspectRatio="none">${panel(topLeft, topRight, 14, 135, "#3977ee", "#d78b17")}<text x="38" y="151" fill="#8a918d" font-size="9">${escapeHtml(dateLabel(dateValues[0]))}</text><text x="566" y="151" text-anchor="end" fill="#8a918d" font-size="9">${escapeHtml(dateLabel(dateValues.at(-1)))}</text><text x="38" y="174" fill="${leftColor}" font-size="10">— ${escapeHtml(options.leftName || "左轴")}</text><text x="125" y="174" fill="${rightColor}" font-size="10">— ${escapeHtml(options.rightName || "右轴")}</text>${panel(lowerLeft, lowerRight, 184, 292, leftColor, rightColor)}</svg>`;
}

function seasonalityChart(probabilities, averages) {
  if (!probabilities.some((value) => value !== null)) return emptyBlock("暂无季节性数据");
  const width = 600; const left = 34; const right = 574; const top = 12; const bottom = 132; const step = (right - left) / 12; const barWidth = Math.min(20, step * .55);
  const maxAverage = Math.max(...averages.filter((value) => value !== null).map(Math.abs), 1);
  const bars = probabilities.map((value, index) => value === null ? "" : `<rect x="${left + index * step + (step - barWidth) / 2}" y="${bottom - value / 100 * (bottom - top)}" width="${barWidth}" height="${value / 100 * (bottom - top)}" rx="1" fill="#3977ee"/>`).join("");
  const points = averages.map((value, index) => value === null ? null : `${left + index * step + step / 2},${(top + bottom) / 2 - value / maxAverage * (bottom - top) / 2}`).filter(Boolean).join(" ");
  const labels = probabilities.map((_value, index) => `<text x="${left + index * step + step / 2}" y="149" text-anchor="middle" fill="#8a918d" font-size="9">${index + 1}月</text>`).join("");
  return `<svg viewBox="0 0 600 155" preserveAspectRatio="none"><path d="M${left} ${top}H${right}M${left} ${(top + bottom) / 2}H${right}M${left} ${bottom}H${right}" stroke="#d8dedb" stroke-width=".6"/>${bars}<polyline points="${points}" fill="none" stroke="#d78b17" stroke-width="2" vector-effect="non-scaling-stroke"/>${labels}</svg>`;
}

function normalizedMultiLineChart(series, totalPoints = null) {
  const usable = series.filter((item) => item.values.filter((value) => value !== null).length > 1);
  if (!usable.length) return emptyBlock("等待同花顺金银比分时数据");
  const paths = usable.map((item) => {
    const values = item.values.map(number); const finite = values.filter((value) => value !== null); const min = Math.min(...finite); const max = Math.max(...finite); const range = max - min || 1;
    const points = values.map((value, index) => value === null ? null : `${12 + index / Math.max(1, (totalPoints || values.length) - 1) * 576},${112 - (value - min) / range * 96}`).filter(Boolean).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${item.color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/>`;
  }).join("");
  return `<svg viewBox="0 0 600 140" preserveAspectRatio="none"><path d="M12 16H588M12 64H588M12 112H588" stroke="#d8dedb" stroke-width=".6"/>${paths}<text x="12" y="136" fill="#8a918d" font-size="9">08:00</text><text x="300" y="136" text-anchor="middle" fill="#8a918d" font-size="9">19:59</text><text x="588" y="136" text-anchor="end" fill="#8a918d" font-size="9">07:59</text></svg>`;
}

function barChart(rows) {
  const usable = rows.filter((row) => number(row.value) !== null).slice(0, 14);
  if (!usable.length) return emptyBlock("暂无统计数据");
  const max = Math.max(...usable.map((row) => Math.abs(number(row.value))), 1);
  return `<div class="gold-bar-chart">${usable.map((row) => `<div><span>${escapeHtml(row.name)}</span><i><b class="${marketClass(row.value)}" style="width:${Math.max(2, Math.abs(number(row.value)) / max * 100)}%"></b></i><strong class="${marketClass(row.value)}">${signedNumber(row.value, 2)}</strong></div>`).join("")}</div>`;
}

async function loadUsMarket(showToast = false) {
  if (state.usMarketLoading) return;
  state.usMarketLoading = true;
  setText("us-market-status", "正在读取");
  try {
    const [moduleResponse, quoteResponse, sectorResponse] = await Promise.all([
      fetch("/api/market-observability/snapshots?data_type=ths_us_market_module&limit=100", { headers: { Accept: "application/json" } }),
      fetch("/api/market-observability/snapshots?data_type=ths_us_security_quote&subject_type=security&limit=5000", { headers: { Accept: "application/json" } }),
      fetch("/api/market-observability/history?subject_id=sectors&data_type=ths_us_market_module&limit=1", { headers: { Accept: "application/json" } }),
    ]);
    if (!moduleResponse.ok) throw new Error(`${moduleResponse.status} ${await moduleResponse.text()}`);
    if (!quoteResponse.ok) throw new Error(`${quoteResponse.status} ${await quoteResponse.text()}`);
    if (!sectorResponse.ok) throw new Error(`${sectorResponse.status} ${await sectorResponse.text()}`);
    state.usMarket = buildUsMarketModel(
      await moduleResponse.json(),
      await quoteResponse.json(),
      await sectorResponse.json(),
    );
    renderUsMarket();
    if (showToast) showMessage("美股数据已刷新");
  } catch (error) {
    setText("us-market-status", "读取失败");
    showMessage(`美股数据读取失败：${error.message}`);
  } finally {
    state.usMarketLoading = false;
  }
}

function nativeTableRows(snapshot) {
  const columns = snapshot?.data?.native_table?.dataDict || {};
  const length = Math.max(0, ...Object.values(columns).filter(Array.isArray).map((values) => values.length));
  return Array.from({ length }, (_, index) => Object.fromEntries(
    Object.entries(columns).map(([field, values]) => [field, Array.isArray(values) ? values[index] : null]),
  ));
}

function buildUsMarketModel(modulePayload, quotePayload, sectorPayload = null) {
  const modules = Object.fromEntries((modulePayload.items || []).map((item) => [item.subject_id, item]));
  const exactSectorSnapshot = sectorPayload?.items?.[0];
  if (exactSectorSnapshot) modules.sectors = exactSectorSnapshot;
  const quotes = new Map();
  (quotePayload.items || []).forEach((snapshot) => {
    const data = snapshot.data || {};
    quotes.set(`${data.market_id}:${data.code}`, { ...data, fetched_at: snapshot.fetched_at });
  });
  const configItems = modules.etf_config_stream?.data?.native_table?.items || [];
  const etfs = configItems.map((category) => {
    const snapshot = modules[`etf_sector_${category.BlockID}_stream`];
    return { category: category.Name, block_id: category.BlockID, row: nativeTableRows(snapshot)[0], fetched_at: snapshot?.fetched_at };
  }).filter((item) => item.row);
  const rankings = {};
  ["all", "us24hremen", "zhonggaigu", "djg", "redianmeigu", "ssxg", "redianetf"].forEach((tab) => {
    const snapshot = modules[`ranking_${tab}_stream`];
    rankings[tab] = { rows: nativeTableRows(snapshot), fetched_at: snapshot?.fetched_at };
  });
  ["pre_market", "regular", "after_hours"].forEach((session) => {
    const snapshot = modules[`ranking_all_${session}_stream`];
    rankings[`all_${session}`] = { rows: nativeTableRows(snapshot), fetched_at: snapshot?.fetched_at };
  });
  const times = (modulePayload.items || []).map((item) => item.fetched_at).filter(Boolean);
  return {
    modules,
    quotes,
    indices: nativeTableRows(modules.indices_stream).filter((row) => row["4"] !== "HXC"),
    industry: nativeTableRows(modules.industry_current_stream),
    concept: nativeTableRows(modules.concept_current_stream),
    sectorPeriods: modules.sectors?.data?.sectors || {},
    breadth: modules.overview?.data || modules.breadth?.data || {},
    etfs,
    rankings,
    updated_at: times.sort().at(-1) || null,
  };
}

function renderUsMarket() {
  const model = state.usMarket;
  if (!model) return;
  const indexGrid = document.querySelector("#us-index-grid");
  indexGrid.innerHTML = model.indices.map((row) => `
    <article class="us-index-card"><span>${escapeHtml(row["55"] || row["4"] || "—")}</span><strong>${formatNumber(row["10"], 3)}</strong><small class="${changeClass(row["34818"])}">${signedPercent(usNumber(row["34818"]))}</small></article>
  `).join("") || '<div class="empty-state">暂无指数数据</div>';
  renderUsBreadth(model.breadth);
  renderUsSectorCards("industry", model);
  renderUsSectorCards("concept", model);
  document.querySelector("#us-etf-grid").innerHTML = model.etfs.map((item) => `
    <article class="us-etf-card"><span>${escapeHtml(item.category)}</span><strong>${escapeHtml(item.row["55"] || item.row["4"] || "—")}</strong><small>${formatNumber(item.row["10"], 3)} <b class="${changeClass(item.row["34818"])}">${signedPercent(usNumber(item.row["34818"]))}</b></small></article>
  `).join("") || '<div class="empty-state">暂无 ETF 板块数据</div>';
  renderUsRanking();
  const age = relativeTime(model.updated_at);
  setText("us-market-updated", `来源 ${formatDateTime(model.updated_at)} · ${age}`);
  const badge = document.querySelector("#us-market-status");
  badge.className = "status-badge";
  badge.textContent = usTradingStatus();
}

function usMarketSession() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date()).map((part) => [part.type, part.value]));
  const minute = Number(parts.hour) * 60 + Number(parts.minute);
  if (["Sat", "Sun"].includes(parts.weekday)) return "closed";
  if (minute >= 240 && minute < 570) return "pre_market";
  if (minute >= 570 && minute < 960) return "regular";
  if (minute >= 960 && minute < 1200) return "after_hours";
  return "closed";
}

function usTradingStatus() {
  return { pre_market: "盘前交易", regular: "盘中交易", after_hours: "盘后交易", closed: "已闭市" }[usMarketSession()];
}

function renderUsSectorCards(type, model) {
  const period = state.usControls[`${type}Period`] || "current";
  const id = type === "industry" ? "us-industry-grid" : "us-concept-grid";
  const context = document.querySelector(`#us-${type}-context`);
  let rows;
  if (period === "current") {
    rows = model[type].map((row) => ({
      name: row["55"], sectorChange: row["34313"], leader: row["35284"],
      leaderPrice: row["35279"], leaderChange: row["35286"], rank: null,
    }));
    context.textContent = "当日板块涨幅";
  } else {
    const source = model.sectorPeriods[`${type}_${period}`]?.data?.sector_list || [];
    const prefix = { five_day: "five_day", one_month: "one_month", three_month: "three_month" }[period];
    const rankKey = `${prefix}_sector_rank`;
    rows = source.slice().sort((a, b) => (Number(a[rankKey]) || 9999) - (Number(b[rankKey]) || 9999)).slice(0, 3).map((row) => ({
      name: row.sector_name, sectorChange: row[`${prefix}_sector_uplift`] ?? null, rank: row[rankKey],
      leader: row[`${prefix}_head_stock_name`], leaderPrice: row[`${prefix}_head_stock_price`],
      leaderChange: row[`${prefix}_head_stock_uplift`],
    }));
    context.textContent = `${{ five_day: "近5日", one_month: "近1月", three_month: "近3月" }[period]}排行 · 板块涨幅`;
  }
  document.querySelector(`#${id}`).innerHTML = rows.map((row) => `
    <article><span>${row.rank ? `<i class="us-sector-rank">#${escapeHtml(row.rank)}</i>` : ""}${escapeHtml(row.name || "—")}</span><strong class="${changeClass(row.sectorChange ?? row.leaderChange)}">${row.sectorChange !== null ? signedPercent(usNumber(row.sectorChange)) : signedPercent(usNumber(row.leaderChange))}</strong><small>领涨：${escapeHtml(row.leader || "—")}</small><small>${formatNumber(row.leaderPrice, 3)} · <b class="${changeClass(row.leaderChange)}">${signedPercent(usNumber(row.leaderChange))}</b></small></article>
  `).join("") || '<div class="empty-state">暂无板块数据</div>';
}

function renderUsBreadth(data) {
  const chart = document.querySelector("#us-breadth-chart");
  const balance = document.querySelector("#us-breadth-balance");
  if (state.usControls.breadth === "month") {
    const rows = data.breadth_month?.quote_changes || data.month?.quote_changes || [];
    const max = Math.max(1, ...rows.map((row) => Math.max(number(row.rise_count) || 0, number(row.fall_count) || 0)));
    chart.innerHTML = rows.slice(-20).map((row) => `<div class="us-month-column" title="${escapeHtml(row.trade_date)}"><i class="down" style="height:${Math.max(3, (number(row.fall_count) || 0) / max * 70)}px"></i><i class="up" style="height:${Math.max(3, (number(row.rise_count) || 0) / max * 70)}px"></i><small>${escapeHtml(String(row.trade_date || "").slice(5))}</small></div>`).join("");
    const latest = rows.at(-1) || {};
    balance.innerHTML = `<span>跌 ${formatInteger(latest.fall_count)}</span><i><b style="width:${breadthRatio(latest.fall_count, latest.rise_count)}%"></b></i><span>涨 ${formatInteger(latest.rise_count)}</span>`;
    return;
  }
  const today = data.breadth_today || data.today || {};
  const ranges = [...(today.decline_ranges || []).slice().reverse(), { range: "0", count: today.zero_range }, ...(today.increase_ranges || [])];
  const max = Math.max(1, ...ranges.map((row) => number(row.count) || 0));
  chart.innerHTML = ranges.map((row, index) => `<div class="us-range-column"><strong>${formatInteger(row.count)}</strong><i class="${index < 5 ? "down" : index > 5 ? "up" : "flat"}" style="height:${Math.max(4, (number(row.count) || 0) / max * 82)}px"></i><small>${escapeHtml(row.range || "0")}</small></div>`).join("");
  const falls = (today.decline_ranges || []).reduce((sum, row) => sum + (number(row.count) || 0), 0);
  const rises = (today.increase_ranges || []).reduce((sum, row) => sum + (number(row.count) || 0), 0);
  balance.innerHTML = `<span>跌 ${formatInteger(falls)}</span><i><b style="width:${breadthRatio(falls, rises)}%"></b></i><span>涨 ${formatInteger(rises)}</span>`;
}

function breadthRatio(falls, rises) {
  const total = (number(falls) || 0) + (number(rises) || 0);
  return total ? (number(falls) || 0) / total * 100 : 50;
}

function renderUsRanking() {
  const model = state.usMarket;
  let session = state.usControls.rankingSession === "auto"
    ? usMarketSession()
    : state.usControls.rankingSession;
  if (state.usControls.rankingSession === "auto" && session === "closed") {
    // THS keeps the last completed pre/regular/after-hours tables available
    // after the session and on weekends.  The legacy generic `all` snapshot
    // may be several days old, so automatic mode must use the freshest real
    // session snapshot instead of silently falling back to it.
    session = ["pre_market", "regular", "after_hours"]
      .map((value) => ({ value, fetchedAt: Date.parse(model.rankings[`all_${value}`]?.fetched_at || "") || 0 }))
      .sort((a, b) => b.fetchedAt - a.fetchedAt)[0]?.value || "regular";
  }
  const sessionRanking = state.usControls.ranking === "all"
    ? model.rankings[`all_${session}`]
    : null;
  const ranking = sessionRanking?.rows?.length
    ? sessionRanking
    : (model.rankings[state.usControls.ranking] || { rows: [] });
  const extended = session === "pre_market" || session === "after_hours";
  const sessionLabel = session === "pre_market" ? "盘前" : "盘后";
  setText("us-ranking-latest-head", extended ? `${sessionLabel}最新` : "最新");
  setText("us-ranking-rate-head", extended ? `${sessionLabel}涨幅` : "涨幅");
  setText("us-ranking-change-head", extended ? `${sessionLabel}涨跌` : "涨跌");
  const projected = ranking.rows.map((row) => {
    const quote = model.quotes.get(`${row["36103"] || row["34338"]}:${row["4"]}`) || {};
    const extendedRateField = session === "after_hours" ? "34868" : "36065";
    const extendedChangeField = session === "after_hours" ? "34869" : "36066";
    const extendedChange = usNumber(row[extendedChangeField]);
    const regularLatest = usNumber(row["10"]);
    const latest = extended
      ? (regularLatest !== null && extendedChange !== null ? regularLatest + extendedChange : regularLatest)
      : (quote.latest ?? row["10"]);
    const changeRate = extended ? row[extendedRateField] : (quote.change_rate ?? row["34818"]);
    const changeAmount = extended ? row[extendedChangeField] : row["34387"];
    const speed = quote.speed ?? row["48"];
    return { row, quote, latest, changeRate, changeAmount, speed };
  });
  if (state.usControls.ranking === "all") {
    projected.sort((a, b) => (usNumber(b.changeRate) ?? -Infinity) - (usNumber(a.changeRate) ?? -Infinity));
  }
  document.querySelector("#us-ranking-body").innerHTML = projected.slice(0, 100).map(({ row, quote, latest, changeRate, changeAmount, speed }) => `<tr><td><strong>${escapeHtml(quote.name || row["55"] || "—")}</strong><small>${escapeHtml(row["4"] || "—")}</small></td><td>${formatNumber(latest, 3)}</td><td class="${changeClass(changeRate)}">${signedPercent(usNumber(changeRate))}</td><td class="${changeClass(changeAmount)}">${signedNumber(usNumber(changeAmount), 3)}</td><td class="${changeClass(speed)}">${signedPercent(usNumber(speed))}</td><td>${escapeHtml(row["19"] || "—")}</td></tr>`).join("") || tableEmpty(6, "该分类暂无排行数据");
  const modeLabel = extended ? sessionLabel : session === "regular" ? "盘中" : "闭市";
  const modePrefix = state.usControls.rankingSession === "auto" ? `自动（${modeLabel}）` : `手动（${modeLabel}）`;
  setText("us-ranking-updated", `${modePrefix} · 表格 ${formatDateTime(ranking.fetched_at)}`);
}

function changeClass(value) {
  const parsed = usNumber(value);
  return parsed === null || parsed === 0 ? "flat" : parsed > 0 ? "up" : "down";
}

function usNumber(value) {
  if (typeof value === "string") return number(value.replaceAll(",", "").replace("%", ""));
  return number(value);
}

async function loadSectorOverview(showToast = false) {
  if (state.sectorLoading) return;
  state.sectorLoading = true;
  try {
    const response = await fetch(
      "/api/market-observability/sectors/overview?limit_per_group=30",
      { headers: { Accept: "application/json" } },
    );
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    state.sectors = await response.json();
    renderSectorMarket();
    if (showToast) showMessage("板块数据已刷新");
  } catch (error) {
    showMessage(`板块数据读取失败：${error.message}`);
    const badge = document.querySelector("#sector-freshness");
    if (badge) {
      badge.className = "status-badge error";
      badge.textContent = "读取失败";
    }
  } finally {
    state.sectorLoading = false;
  }
}

async function loadSectorDetail(providerSectorCode, sectorType) {
  if (!providerSectorCode || state.sectorDetailLoading) return;
  state.sectorDetailLoading = true;
  const panel = document.querySelector("#sector-detail-panel");
  panel.hidden = false;
  document.querySelector("#sector-detail-title").textContent = "正在读取板块资料";
  document.querySelector("#sector-constituent-body").innerHTML =
    `<tr><td colspan="6">正在从数据库读取...</td></tr>`;
  try {
    const params = new URLSearchParams({
      provider_sector_code: providerSectorCode,
      history_limit: "20",
    });
    if (sectorType && sectorType !== "all") params.set("sector_type", sectorType);
    const response = await fetch(
      `/api/market-observability/sectors/detail?${params.toString()}`,
      { headers: { Accept: "application/json" } },
    );
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    state.sectorDetail = await response.json();
    renderSectorDetail();
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    document.querySelector("#sector-detail-title").textContent = "板块资料读取失败";
    document.querySelector("#sector-constituent-body").innerHTML =
      `<tr><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
  } finally {
    state.sectorDetailLoading = false;
  }
}

function renderSectorDetail() {
  const detail = state.sectorDetail || {};
  const latest = detail.latest || [];
  const identity = latest.find((item) => item.sector_name) || {};
  const etf = detail.representative_etf;
  document.querySelector("#sector-detail-title").textContent =
    identity.sector_name || detail.provider_sector_code || "板块资料";
  document.querySelector("#sector-detail-meta").textContent = [
    detail.provider_sector_code,
    detail.sector_type || identity.sector_type,
    `${formatInteger(detail.constituent_count || 0)} 只成分股`,
    etf?.name ? `关联 ETF：${etf.name} (${etf.code || "—"})` : null,
  ].filter(Boolean).join(" · ");
  const rows = detail.constituents || [];
  document.querySelector("#sector-constituent-body").innerHTML = rows.length
    ? rows.map((item) => `<tr>
        <td>${formatInteger(item.rank)}</td>
        <td><strong>${escapeHtml(item.security_name || "—")}</strong><small>${escapeHtml(item.security_code || "")}</small></td>
        <td>${formatNumber(item.latest, 2)}</td>
        <td class="${marketClass(number(item.change_pct))}">${signedPercent(number(item.change_pct))}</td>
        <td>${signedPercent(number(item.speed_pct))}</td>
        <td>${formatNumber(item.turnover_rate, 2)}%</td>
      </tr>`).join("")
    : `<tr><td colspan="6">成分股正在由后台任务补齐，页面不会直连上游数据源。</td></tr>`;
}

function renderSectorMarket() {
  const payload = state.sectors;
  if (!payload) return;
  const facts = payload.facts || {};
  const signals = payload.provider_signals || {};
  const controls = state.sectorControls;
  const freshness = payload.freshness || {};
  const badge = document.querySelector("#sector-freshness");
  if (badge) {
    badge.className = "status-badge ready";
    badge.textContent = `${formatInteger(payload.total)} 条最新状态 · ${formatClock(freshness.latest_bucket_at)}`;
  }

  const hot = facts.hot?.[controls.hot] || [];
  document.querySelector("#sector-hot-grid").innerHTML = hot.length
    ? hot.slice(0, 5).map((item, index) => {
      const heat = number(item.heat_score);
      return `
      <article class="sector-hot-row interactive" data-sector-code="${escapeHtml(item.provider_sector_code || "")}" data-sector-type="${escapeHtml(item.sector_type || controls.hot)}">
        <b class="sector-rank rank-${index + 1}">${index + 1}</b>
        <span><strong>${escapeHtml(item.sector_name || item.provider_sector_code || "板块")}</strong><small>${escapeHtml(item.provider_sector_code || "")}</small></span>
        <em class="${marketClass(number(item.change_pct))}">${signedPercent(number(item.change_pct))}</em>
        <strong>${heat === null ? "—" : `${formatNumber(heat / 10000, 2)}万`}</strong>
      </article>`;
    }).join("")
    : emptyBlock("暂无热门板块快照");

  const rankingSource = facts.rankings?.[controls.rankingType]?.[controls.ranking] || [];
  const rankings = [...rankingSource].sort((left, right) => {
    const leftValue = number(left.metric_value);
    const rightValue = number(right.metric_value);
    if (leftValue === null) return 1;
    if (rightValue === null) return -1;
    return controls.rankingDirection === "asc"
      ? leftValue - rightValue
      : rightValue - leftValue;
  });
  document.querySelector("#sector-ranking-grid").innerHTML = rankings.length
    ? rankings.slice(0, 9).map((item) => {
      const metric = number(item.metric_value);
      return `<article class="sector-stat-card interactive" data-sector-code="${escapeHtml(item.provider_sector_code || "")}" data-sector-type="${escapeHtml(item.sector_type || controls.rankingType)}">
        <strong>${escapeHtml(item.sector_name || item.provider_sector_code || "—")}</strong>
        <b class="${marketClass(metric)}">${escapeHtml(sectorMetricValue(item))}</b>
        ${item.lead_stock_name ? `<small>${escapeHtml(item.lead_stock_name)} <em class="${marketClass(number(item.lead_stock_change_pct))}">${signedPercent(number(item.lead_stock_change_pct))}</em></small>` : ""}
      </article>`;
    }).join("")
    : emptyBlock("暂无该指标快照");

  const availableFlows = facts.fund_flows?.[controls.flow] || [];
  const inflowFlows = availableFlows
      .filter((item) => number(item.main_net_inflow) !== null && number(item.main_net_inflow) >= 0)
      .sort((left, right) => (number(right.main_net_inflow) || 0) - (number(left.main_net_inflow) || 0))
      .slice(0, 3);
  const outflowFlows = availableFlows
      .filter((item) => number(item.main_net_inflow) !== null && number(item.main_net_inflow) < 0)
      .sort((left, right) => (number(left.main_net_inflow) || 0) - (number(right.main_net_inflow) || 0))
      .slice(0, 3)
      .sort((left, right) => (number(right.main_net_inflow) || 0) - (number(left.main_net_inflow) || 0));
  const flows = [...inflowFlows, ...outflowFlows];
  const maxFlow = Math.max(1, ...flows.map((item) => Math.abs(number(item.main_net_inflow) || 0)));
  document.querySelector("#sector-flow-bars").innerHTML = flows.length
    ? flows.map((item) => {
      const value = number(item.main_net_inflow) || 0;
      return `<div class="sector-bar-row interactive ${value >= 0 ? "positive" : "negative"}" data-sector-code="${escapeHtml(item.provider_sector_code || "")}" data-sector-type="${escapeHtml(item.sector_type || controls.flow)}">
        <span>${escapeHtml(item.sector_name || "—")}</span>
        <div class="sector-bar-track"><i style="width:${Math.max(2, Math.abs(value) / maxFlow * 100)}%"></i></div>
        <strong class="${marketClass(value)}">${value > 0 ? "+" : ""}${formatNumber(value, 2)} 亿</strong>
      </div>`;
    }).join("")
    : emptyBlock("暂无板块资金快照");

  const rotationTypeLabel = controls.rotationType === "industry" ? "行业板块" : "概念板块";
  const rotationMetricLabel = {
    change: "涨跌幅",
    main_net_inflow: "主力净流入",
    five_day_change: "五日涨幅",
    rise_rate: "上涨率",
    limit_up_count: "涨停家数",
  }[controls.rotation] || controls.rotation;
  const rotationContext = document.querySelector("#sector-rotation-context");
  if (rotationContext) rotationContext.textContent = `${rotationTypeLabel} · ${rotationMetricLabel}`;
  const rotationPeriods = signals.rotation?.[controls.rotationType]?.[controls.rotation] || [];
  renderSectorRotationMatrix(rotationPeriods);

  renderSectorSignalList(
    "sector-opportunity-list",
    Object.entries(signals.industry_opportunities || {}).flatMap(([category, rows]) =>
      rows.map((item) => ({
        name: item.sector_name || item.securityName,
        label: `${opportunityLabel(category)} · ${signalValue(item.indicator)}`,
      })),
    ),
  );
  renderSectorSignalList(
    "sector-prosperity-list",
    signals.prosperity || [],
    (item) => item.sector_name || item.name,
    (item) => `${formatNumber(item.prosperity_score, 1)} · ${formatNumber(item.prosperity_percentile, 1)}%`,
  );
  renderCommodityLinkage(
    signals.commodity_linkage?.[controls.commodity] || [],
  );
}

function renderCommodityLinkage(rows) {
  const body = document.querySelector("#sector-commodity-body");
  if (!body) return;
  body.innerHTML = rows.length
    ? rows.map((item) => {
      const assets = item.linked_assets || [];
      const assetNames = assets.map((asset) => `
        <span><strong>${escapeHtml(asset.security_name || asset.security_code || "—")}</strong><small>${asset.asset_type === "etf" ? "ETF" : "板块"}</small></span>
      `).join("");
      const assetChanges = assets.map((asset) => `
        <span class="${marketClass(number(asset.change_pct))}">${signedPercent(number(asset.change_pct))}</span>
      `).join("");
      return `<tr>
        <td><strong>${escapeHtml(item.source_name || item.sector_name || item.source_code || "—")}</strong><small>${escapeHtml(item.source_code || item.provider_sector_code || "")}</small></td>
        <td class="${marketClass(number(item.source_change_pct ?? item.change_pct))}">${signedPercent(number(item.source_change_pct ?? item.change_pct))}</td>
        <td><div class="commodity-asset-stack">${assetNames || "—"}</div></td>
        <td><div class="commodity-change-stack">${assetChanges || "—"}</div></td>
      </tr>`;
    }).join("")
    : `<tr><td colspan="4">暂无该类商品联动快照</td></tr>`;
}

function renderSectorSignalList(id, rows, nameOf = (item) => item.name, valueOf = (item) => item.label) {
  const element = document.querySelector(`#${id}`);
  element.innerHTML = rows.length
    ? rows.slice(0, 20).map((item) => `<div class="sector-signal-row"><span>${escapeHtml(nameOf(item) || "—")}</span><strong>${escapeHtml(valueOf(item) || "—")}</strong></div>`).join("")
    : emptyBlock("暂无来源信号");
}

function renderSectorRotationMatrix(periods) {
  const head = document.querySelector("#sector-rotation-head");
  const body = document.querySelector("#sector-rotation-body");
  const visiblePeriods = periods.slice(0, 10);
  if (!visiblePeriods.length) {
    head.innerHTML = "";
    body.innerHTML = `<tr><td>暂无热点轮动信号</td></tr>`;
    return;
  }
  head.innerHTML = `<tr><th>排名</th>${visiblePeriods.map((period) => `<th>${escapeHtml(String(period.source_date || "—").slice(5))}</th>`).join("")}</tr>`;
  const maxRank = Math.max(0, ...visiblePeriods.flatMap((period) => (period.items || []).map((item) => number(item.rank) || 0)));
  body.innerHTML = Array.from({ length: Math.min(maxRank, 10) }, (_, index) => {
    const rank = index + 1;
    const cells = visiblePeriods.map((period) => {
      const item = (period.items || []).find((candidate) => number(candidate.rank) === rank);
      if (!item) return "<td>—</td>";
      return `<td class="interactive" data-sector-code="${escapeHtml(item.provider_sector_code || "")}" data-sector-type="${escapeHtml(item.sector_type || "")}"><strong>${escapeHtml(item.sector_name || "—")}</strong><small class="${marketClass(rotationMetricNumber(item))}">${escapeHtml(rotationMetricValue(item))}</small></td>`;
    }).join("");
    return `<tr><td>${rank}</td>${cells}</tr>`;
  }).join("");
}

function rotationMetricNumber(item) {
  const info = item.source_signal || {};
  const key = {
    main_net_inflow: "zljlr",
    change: "zf",
    five_day_change: "zf5",
    rise_rate: "riseRate",
    limit_up_count: "riseLimCnt",
  }[item.metric];
  return key ? number(info[key]) : null;
}

function rotationMetricValue(item) {
  const info = item.source_signal || {};
  const rawValue = rotationMetricNumber(item);
  if (rawValue !== null) {
    if (item.metric === "main_net_inflow") {
      return `${rawValue >= 0 ? "+" : ""}${formatNumber(rawValue / 100000000, 2)}亿`;
    }
    if (item.metric === "limit_up_count") return `${formatInteger(rawValue)}家`;
    return signedPercent(rawValue);
  }
  const keys = {
    main_net_inflow: ["zljlr", "main_net_inflow"],
    change: ["zf", "change"],
    five_day_change: ["zf5", "five_day_change"],
    rise_rate: ["riseRate", "rise_rate"],
    limit_up_count: ["riseLimCnt", "limit_up_count"],
  }[item.metric] || [];
  for (const key of keys) {
    if (info[key] !== undefined && info[key] !== null) return String(info[key]);
  }
  return signalValue(info);
}

function sectorMetricValue(item) {
  const value = number(item.metric_value);
  if (value === null || value === undefined) return "—";
  if (item.metric === "limit_up_count") return `${formatInteger(value)} 家`;
  if (item.metric === "volume_ratio") return `${formatNumber(value, 2)}x`;
  return signedPercent(value);
}

function signalValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value !== "object") return String(value);
  const preferred = ["value", "score", "val", "name", "display"];
  for (const key of preferred) {
    if (value[key] !== undefined && value[key] !== null) return String(value[key]);
  }
  return Object.values(value).filter((item) => ["string", "number"].includes(typeof item)).slice(0, 2).join(" · ") || "—";
}

function opportunityLabel(value) {
  return { hotspot: "热点", lowLevel: "低位", revival: "复苏" }[value] || value;
}

function renderDashboard() {
  if (!state.dashboard) return;
  renderMetrics();
  renderBreadth();
  renderMarketHome();
  renderRuns();
  renderWatchlist();
}

function renderMetrics() {
  const summary = state.dashboard.summary || {};
  const snapshots = summary.snapshots || {};
  const runs = summary.runs || {};
  const freshness = snapshots.by_freshness || {};
  const etf = summary.etf_daily_shares || {};
  setText("metric-snapshots", formatInteger(snapshots.total));
  setText("metric-subjects", `${formatInteger(snapshots.subject_count)} 个对象`);
  setText("metric-latest", formatClock(snapshots.latest_bucket_at));
  setText("metric-latest-detail", relativeTime(snapshots.latest_bucket_at));
  setText("metric-realtime", formatInteger(freshness.realtime || 0));
  setText(
    "metric-delayed",
    `${formatInteger(freshness.delayed || 0)} 延时 · ${formatInteger(freshness.fetch_time || 0)} 仅采集时间 · ${formatInteger(freshness.unknown || 0)} 未知`,
  );
  setText("metric-runs", formatInteger(runs.total));
  setText(
    "metric-failures",
    `${formatInteger(runs.failed || 0)} 失败 · ${formatInteger(runs.running || 0)} 运行中`,
  );
  setText("metric-watchlist", formatInteger(summary.watchlist_count));
  setText("metric-etf-date", etf.trade_date || "—");
  setText("metric-etf-count", `${formatInteger(etf.fund_count)} 只基金`);
}

function renderBreadth() {
  const block = state.dashboard.market_breadth || {};
  const snapshot = block.latest;
  const data = snapshot?.data || {};
  const freshness = document.querySelector("#breadth-freshness");
  const displayStatus = block.display_status;
  const status = displayStatus?.status || snapshot?.freshness_status || "neutral";
  freshness.className = `status-badge ${status}`;
  freshness.textContent = snapshot
    ? `${displayStatus?.label || freshnessLabel(snapshot.freshness_status)} · ${formatClock(snapshot.observed_at || snapshot.fetched_at)}`
    : "暂无数据";

  const indices = Array.isArray(data.indices) ? data.indices : [];
  const indexStrip = document.querySelector("#index-strip");
  indexStrip.innerHTML = indices.length
    ? indices.map((item) => {
      const change = number(item.change_percent);
      return `
        <div class="index-item">
          <span>${escapeHtml(item.name || item.code || "指数")}</span>
          <strong>${formatNumber(item.close, 2)}</strong>
          <small class="${marketClass(change)}">${signedPercent(change)}</small>
        </div>`;
    }).join("")
    : emptyBlock("尚未采集主要指数");

  const up = number(data.up_count) || 0;
  const down = number(data.down_count) || 0;
  const flat = number(data.flat_count) || 0;
  const total = Math.max(1, up + down + flat);
  setText("breadth-up", formatInteger(up));
  setText("breadth-down", formatInteger(down));
  setText("breadth-flat", formatInteger(flat));
  document.querySelector("#breadth-up-bar").style.width = `${(up / total) * 100}%`;
  document.querySelector("#breadth-flat-bar").style.width = `${(flat / total) * 100}%`;
  document.querySelector("#breadth-down-bar").style.width = `${(down / total) * 100}%`;
  setText("breadth-turnover", formatMoney(data.turnover));
  const comparison = block.previous_same_time;
  const comparisonLabel = document.querySelector("#breadth-turnover-comparison-label");
  const comparisonValue = document.querySelector("#breadth-turnover-comparison");
  if (comparison) {
    const comparisonTime = comparison.comparison_time
      ? ` ${comparison.comparison_time}`
      : " 同时刻";
    comparisonLabel.textContent = `较 ${comparison.trade_date}${comparisonTime}`;
    const change = number(comparison.turnover_change);
    comparisonValue.textContent = `${change > 0 ? "+" : ""}${formatMoney(change)} · ${signedPercent(comparison.turnover_change_percent)}`;
    comparisonValue.className = marketClass(change);
  } else {
    comparisonLabel.textContent = "暂无上一交易日分时数据";
    comparisonValue.textContent = "—";
    comparisonValue.className = "";
  }
}

function renderBreadthChart() {
  const canvas = document.querySelector("#breadth-chart");
  const history = state.dashboard?.market_breadth?.history || [];
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);

  const style = getComputedStyle(document.documentElement);
  const line = style.getPropertyValue("--line").trim();
  const green = style.getPropertyValue("--green").trim();
  const red = style.getPropertyValue("--red").trim();
  const muted = style.getPropertyValue("--muted").trim();
  const left = 34;
  const right = 10;
  const top = 12;
  const bottom = 24;
  const width = rect.width - left - right;
  const height = rect.height - top - bottom;

  context.strokeStyle = line;
  context.lineWidth = 1;
  context.fillStyle = muted;
  context.font = "10px sans-serif";
  context.textAlign = "right";
  [0, 25, 50, 75, 100].forEach((tick) => {
    const y = top + height - (tick / 100) * height;
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(left + width, y);
    context.stroke();
    context.fillText(`${tick}%`, left - 6, y + 3);
  });

  const points = history
    .map((item) => {
      const up = number(item.up_count) || 0;
      const down = number(item.down_count) || 0;
      const flat = number(item.flat_count) || 0;
      const total = up + down + flat;
      return total > 0 ? (up / total) * 100 : null;
    })
    .filter((item) => item !== null);
  if (!points.length) {
    context.textAlign = "center";
    context.fillText("暂无市场广度历史", left + width / 2, top + height / 2);
    return;
  }

  context.beginPath();
  points.forEach((value, index) => {
    const x = left + (points.length === 1 ? width / 2 : (index / (points.length - 1)) * width);
    const y = top + height - (value / 100) * height;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.strokeStyle = points.at(-1) >= 50 ? red : green;
  context.lineWidth = 2;
  context.stroke();

  const latest = points.at(-1);
  const latestX = left + width;
  const latestY = top + height - (latest / 100) * height;
  context.beginPath();
  context.arc(latestX, latestY, 3.5, 0, Math.PI * 2);
  context.fillStyle = points.at(-1) >= 50 ? red : green;
  context.fill();
}

function renderEtfEstimatedFlow() {
  const block = state.dashboard.etf_estimated_flow || {};
  const snapshot = block.latest;
  const data = snapshot?.data || {};
  const freshness = document.querySelector("#etf-flow-freshness");
  const displayStatus = block.display_status;
  const status = displayStatus?.status || snapshot?.freshness_status || "neutral";
  freshness.className = `status-badge ${status}`;
  freshness.textContent = snapshot
    ? `${displayStatus?.label || freshnessLabel(snapshot.freshness_status)} · ${formatClock(snapshot.observed_at || snapshot.fetched_at)}`
    : "暂无数据";
  const total = number(data.net_inflow_yuan);
  const top = data.top_inflow || {};
  setText(
    "etf-flow-total",
    total === null ? "—" : `${total > 0 ? "+" : ""}${formatMoney(total)}`,
  );
  document.querySelector("#etf-flow-total").className = marketClass(total);
  setText("etf-flow-top", top.name || top.code || "—");
  setText(
    "etf-flow-top-detail",
    top.net_inflow_yuan === undefined
      ? "同花顺合作 ETF 池"
      : `${top.code || ""} · +${formatMoney(top.net_inflow_yuan)} · 同花顺合作 ETF 池`,
  );
  setText(
    "etf-flow-chart-range",
    `最近 ${(block.history || []).length} 个分钟点`,
  );
  requestAnimationFrame(renderEtfEstimatedFlowChart);
}

function renderEtfEstimatedFlowChart() {
  const canvas = document.querySelector("#etf-flow-chart");
  const history = state.dashboard?.etf_estimated_flow?.history || [];
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);

  const style = getComputedStyle(document.documentElement);
  const line = style.getPropertyValue("--line").trim();
  const green = style.getPropertyValue("--green").trim();
  const red = style.getPropertyValue("--red").trim();
  const muted = style.getPropertyValue("--muted").trim();
  const values = history
    .map((item) => number(item.net_inflow_yuan))
    .filter((item) => item !== null);
  const left = 58;
  const right = 12;
  const top = 12;
  const bottom = 24;
  const width = rect.width - left - right;
  const height = rect.height - top - bottom;
  if (!values.length) {
    context.fillStyle = muted;
    context.font = "10px sans-serif";
    context.textAlign = "center";
    context.fillText("暂无 ETF 预估净流入历史", left + width / 2, top + height / 2);
    return;
  }

  let minValue = Math.min(0, ...values);
  let maxValue = Math.max(0, ...values);
  if (minValue === maxValue) {
    const padding = Math.max(Math.abs(minValue) * 0.1, 1);
    minValue -= padding;
    maxValue += padding;
  }
  const valueRange = maxValue - minValue;
  const valueToY = (value) => top + height - ((value - minValue) / valueRange) * height;
  const zeroY = valueToY(0);
  context.strokeStyle = line;
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(left, zeroY);
  context.lineTo(left + width, zeroY);
  context.stroke();
  context.fillStyle = muted;
  context.font = "10px sans-serif";
  context.textAlign = "right";
  context.fillText(formatMoney(maxValue), left - 6, top + 4);
  context.fillText(formatMoney(minValue), left - 6, top + height);

  context.beginPath();
  values.forEach((value, index) => {
    const x = left + (values.length === 1 ? width / 2 : (index / (values.length - 1)) * width);
    const y = valueToY(value);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.strokeStyle = values.at(-1) >= 0 ? red : green;
  context.lineWidth = 2;
  context.stroke();
}

function renderMarketHome() {
  renderMarketComparisons();
  renderMarketCharts();
  loadStockDynamicGroups();
  loadStockRanking(state.marketControls.ranking);
}

function renderMarketComparisons() {
  const context = state.dashboard?.market_context || {};
  const todayUp = number(context.limit_up?.today?.num);
  const todayDown = number(context.limit_down?.today?.num);
  const yesterday = context.limit_up?.yesterday || {};
  const cap = context.cap_comparison || {};
  setText("compare-limit", `${formatInteger(todayUp)} : ${formatInteger(todayDown)}`);
  setText("compare-limit-detail", `涨停 / 跌停 · 炸板 ${formatInteger(context.limit_up?.today?.open_num)}`);
  const yesterdayRate = number(yesterday.rate);
  setText("compare-yesterday-limit", yesterdayRate === null ? "—" : signedPercent(yesterdayRate * 100));
  const leaderRate = number(yesterday.leader_change_rate);
  setText("compare-yesterday-detail", yesterday.leader_name
    ? `${yesterday.leader_name} ${leaderRate === null ? "—" : signedPercent(leaderRate)}`
    : `${formatInteger(yesterday.num)} 家封板 · ${formatInteger(yesterday.open_num)} 家开板`);
  const large = cap.largeCap || {};
  const small = cap.smallCap || {};
  setText("compare-cap", large.changeRate === undefined || small.changeRate === undefined
    ? "—"
    : `${signedPercent(large.changeRate, 1)} : ${signedPercent(small.changeRate, 1)}`);
  setText("compare-cap-detail", `${large.name || "大盘"} / ${small.name || "小盘"} · ${cap.stronger || "暂无结论"}`);
}

async function loadStockRanking(mode) {
  const bodies = [
    document.querySelector("#stock-ranking-body"),
    document.querySelector("#overview-stock-ranking-body"),
  ].filter(Boolean);
  if (!bodies.length) return;
  if (state.rankingCache[mode]) {
    renderStockRanking(state.rankingCache[mode], mode);
    return;
  }
  bodies.forEach((body) => { body.innerHTML = tableEmpty(4, "正在读取排行"); });
  try {
    const path = `/api/market-observability/stock-rankings?sort=${encodeURIComponent(mode)}&count=20`;
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json();
    const data = payload.data || {};
    const rows = Array.isArray(data.stocks) ? data.stocks : [];
    if (rows.length) {
      state.rankingCache[mode] = rows;
    } else {
      delete state.rankingCache[mode];
    }
    renderStockRanking(rows, mode);
  } catch (error) {
    bodies.forEach((body) => { body.innerHTML = tableEmpty(4, `排行读取失败：${error.message}`); });
  }
}

function renderStockRanking(rows, mode = state.marketControls.ranking) {
  const body = document.querySelector("#stock-ranking-body");
  const overviewBody = document.querySelector("#overview-stock-ranking-body");
  const metric = {
    rise: { label: "涨幅", keys: ["change_rate", "changeRate"], format: signedPercent },
    fall: { label: "涨幅", keys: ["change_rate", "changeRate"], format: signedPercent },
    quick: { label: "涨速", keys: ["speed", "changeSpeed"], format: signedPercent },
    turnover: { label: "成交额", keys: ["turnover"], format: (value) => value ?? "—" },
    large_order: { label: "大单净量", keys: ["large_order_ratio"], format: (value) => formatNumber(value, 2) },
    volume_ratio: { label: "量比", keys: ["volume_ratio"], format: (value) => formatNumber(value, 2) },
    turnover_rate: { label: "换手率", keys: ["turnover_rate"], format: signedPercent },
    main_net_inflow: { label: "主力净流入", keys: ["main_net_inflow"], format: (value) => value ?? "—" },
    amplitude: { label: "振幅", keys: ["amplitude"], format: signedPercent },
  }[mode] || { label: "涨幅", keys: ["change_rate", "changeRate"], format: signedPercent };
  setText("stock-ranking-metric", metric.label);
  setText("overview-stock-ranking-metric", metric.label);
  const limitReasons = new Map(
    (state.dashboard?.market_context?.limit_stocks || []).map((item) => [String(item.code), item.reason_type]),
  );
  const rowHtml = (limit) => rows.length
    ? rows.slice(0, limit).map((item) => {
      const price = firstNumber(item, ["close", "price", "latest"]);
      const value = ["turnover", "main_net_inflow"].includes(mode)
        ? item[metric.keys[0]]
        : firstNumber(item, metric.keys);
      const tone = mode === "turnover"
        ? null
        : (mode === "main_net_inflow" ? financialAmountNumber(value) : number(value));
      const industry = item.industry_name || item.industry || limitReasons.get(String(item.code)) || item.typeName || "—";
      return `<tr><td><strong>${escapeHtml(item.name || "—")}</strong><small>${escapeHtml(item.code || "")}</small></td><td>${escapeHtml(formatNumber(price, 2))}</td><td class="${tone === null ? "" : marketClass(tone)}">${escapeHtml(metric.format(value))}</td><td title="${escapeHtml(industry)}">${escapeHtml(industry)}</td></tr>`;
    }).join("")
    : tableEmpty(4, "暂无排行数据");
  if (body) body.innerHTML = rowHtml(20);
  if (overviewBody) overviewBody.innerHTML = rowHtml(8);
  setText("stock-ranking-updated", `更新于 ${formatClock(new Date().toISOString())}`);
  setText("overview-stock-ranking-updated", `更新于 ${formatClock(new Date().toISOString())}`);
}

async function loadStockDynamicGroups() {
  const body = document.querySelector("#stock-dynamic-body");
  if (!body || state.dynamicGroupsLoading) return;
  if (state.dynamicGroups) {
    renderStockDynamicGroup();
    return;
  }
  state.dynamicGroupsLoading = true;
  body.innerHTML = tableEmpty(5, "正在读取动态分组");
  try {
    const response = await fetch(
      "/api/market-observability/stock-dynamic-groups?count_per_group=100&scope=featured",
      { headers: { Accept: "application/json" } },
    );
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    const payload = await response.json();
    const groups = Array.isArray(payload?.data?.groups) ? payload.data.groups : [];
    state.dynamicGroups = groups;
    if (!groups.some((group) => group.data_code === state.activeDynamicGroup)) {
      state.activeDynamicGroup = groups[0]?.data_code || null;
    }
    renderStockDynamicGroup();
  } catch (error) {
    body.innerHTML = tableEmpty(5, `动态分组读取失败：${error.message}`);
    setText("stock-dynamic-updated", "读取失败");
    setStockPageFreshness("error", "读取失败");
  } finally {
    state.dynamicGroupsLoading = false;
  }
}

function renderStockDynamicGroup() {
  const groups = state.dynamicGroups || [];
  const tabs = document.querySelector("#stock-dynamic-tabs");
  const body = document.querySelector("#stock-dynamic-body");
  if (!tabs || !body) return;
  tabs.innerHTML = groups.map((group) => {
    const code = String(group.data_code || "");
    const active = code === state.activeDynamicGroup ? "active" : "";
    const count = Number(group.available_count ?? group.count ?? 0);
    return `<button class="${active}" data-value="${escapeHtml(code)}" type="button">${escapeHtml(group.title || code || "未命名分组")} <small>${count}</small></button>`;
  }).join("");

  const group = groups.find((item) => item.data_code === state.activeDynamicGroup);
  if (!group) {
    body.innerHTML = tableEmpty(5, "暂无动态分组数据");
    setText("stock-dynamic-subtitle", "等待后台采集动态选股结果");
    setText("stock-dynamic-context", "");
    setText("stock-dynamic-updated", "—");
    setStockPageFreshness("neutral", "暂无数据");
    return;
  }

  const stocks = Array.isArray(group.stocks) ? group.stocks : [];
  const description = group.subtitle || group.query || "同花顺动态选股结果";
  const contextParts = [
    group.highlight_tag ? `<span class="dynamic-highlight">${escapeHtml(group.highlight_tag)}</span>` : "",
    `<span>当前 ${Number(group.available_count ?? stocks.length)} 只</span>`,
    group.total !== undefined && group.total !== null ? `<span>上游总数 ${escapeHtml(group.total)}</span>` : "",
  ].filter(Boolean);
  setText("stock-dynamic-subtitle", description);
  document.querySelector("#stock-dynamic-context").innerHTML = contextParts.join("");
  setText("stock-dynamic-updated", `更新于 ${formatClock(group.snapshot?.bucket_at || group.snapshot?.fetched_at)}`);
  setStockPageFreshness(
    group.snapshot?.freshness_status || "neutral",
    `${freshnessLabel(group.snapshot?.freshness_status)} · ${formatClock(group.snapshot?.bucket_at || group.snapshot?.fetched_at)}`,
  );
  body.innerHTML = stocks.length
    ? stocks.map((item, index) => {
      const change = firstNumber(item, ["change_rate", "changeRate"]);
      const speed = firstNumber(item, ["speed", "changeSpeed"]);
      return `<tr><td>${escapeHtml(item.rank ?? index + 1)}</td><td><strong>${escapeHtml(item.name || "—")}</strong><small>${escapeHtml(item.code || "")}</small></td><td>${escapeHtml(formatNumber(firstNumber(item, ["latest", "close", "price"]), 2))}</td><td class="${marketClass(change)}">${escapeHtml(signedPercent(change))}</td><td class="${marketClass(speed)}">${escapeHtml(speed === null ? "—" : signedPercent(speed))}</td></tr>`;
    }).join("")
    : tableEmpty(5, "该分组当前没有符合条件的股票");
}

function setStockPageFreshness(status, label) {
  const badge = document.querySelector("#stock-page-freshness");
  if (!badge) return;
  badge.className = `status-badge ${status || "neutral"}`;
  badge.textContent = label || "—";
}

function renderMarketCharts() {
  if (!state.dashboard) return;
  renderAnomalyChart();
  renderCapitalFlowChart();
  renderSentimentChart();
  renderGlobalChart();
  renderCurrencyChart();
  renderValuationChart();
  renderBondChart();
}

function chartBlock(key) {
  return state.dashboard?.chart_series?.[key] || { latest: null, history: [] };
}

function historyData(key) {
  return (chartBlock(key).history || []).map((item) => ({
    ...item.data,
    bucket_at: item.bucket_at,
    trade_date: item.trade_date,
  }));
}

function renderAnomalyChart() {
  const mode = state.marketControls.anomaly;
  const rows = historyData(mode);
  const latest = rows.at(-1) || {};
  const curve = mode === "call_auction" ? (latest.line || []) : (latest.curve || []);
  const anomalyAxis = latest.axis || {};
  const values = curve.map((item) => number(mode === "call_auction" ? item.bidding_direction : item.index_value));
  const labels = curve.map((item, index) => anomalyTimeLabel(item.position ?? item.cas_position ?? index, mode));
  const marketCount = (latest.market_events || []).length;
  const marketEvents = latest.market_events || [];
  const stockEvents = latest.stock_events || [];
  const hotSectors = latest.hot_sectors || [];
  const eventCount = mode === "call_auction"
    ? hotSectors.length + (latest.limit_up_stocks || []).length + (latest.new_hot_stocks || []).length
    : marketCount + stockEvents.length;
  setText("anomaly-summary", rows.length
    ? `${latest.trade_date || rows.at(-1)?.trade_date || "最近交易日"} · ${eventCount} 条有效异动`
    : (mode === "call_auction" ? "暂无最近交易日竞价数据" : "暂无最近交易日异动数据"));
  setText("anomaly-updated", formatSeriesUpdated(chartBlock(mode)));
  drawSeriesChart("anomaly-chart", "anomaly-empty", [
    {
      label: mode === "call_auction" ? "上证竞价轨迹" : "上证指数",
      values,
      color: "#2563a7",
      fill: true,
      rangeMin: mode === "call_auction" ? undefined : anomalyAxis.min,
      rangeMax: mode === "call_auction" ? undefined : anomalyAxis.max,
    },
  ], labels, {
    symmetricAxis: mode === "call_auction" ? null : anomalyAxis,
    markers: mode === "call_auction" ? [] : marketEvents
      .filter((item) => number(item.position) !== null)
      .map((item) => ({
        index: number(item.position),
        label: item.title || item.sector_name || "大盘异动",
        reason: item.reason || "",
      })),
  });
  const facts = mode === "call_auction"
    ? [
      ...hotSectors.slice(0, 3).map((item) => ({ label: item.plateName || "竞价板块", value: signedPercent(item.callAuctionRise) })),
      ...(latest.limit_up_stocks || []).slice(0, 2).map((item) => ({ label: item.stockName || "竞价个股", value: signedPercent(item.callAuctionRise) })),
    ]
    : [...marketEvents]
      .sort((left, right) => Number(right.time || 0) - Number(left.time || 0))
      .slice(0, 4)
      .map((item) => ({
        label: `${formatEpochClock(item.time)} ${item.sector_name || "大盘异动"}`,
        value: item.title || "—",
      }));
  renderMiniFacts("anomaly-facts", facts);
}

function anomalyTimeLabel(position, mode) {
  const value = Math.max(0, Number(position) || 0);
  if (mode === "call_auction") {
    const minutes = 9 * 60 + 15 + Math.min(value, 10);
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  }
  const minutes = value <= 120 ? 9 * 60 + 30 + value : 13 * 60 + value - 121;
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function formatEpochClock(value) {
  const timestamp = Number(value);
  if (!Number.isFinite(timestamp)) return "";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(timestamp * 1000));
}

function stockEventDirection(item) {
  const color = String(item?.color || "");
  if (color === "-65536") return "特大主动买";
  if (color === "-16711936") return "特大主动卖";
  return "个股异动";
}

function stockEventPriority(item) {
  const direction = stockEventDirection(item);
  if (direction === "特大主动买") return 0;
  if (direction === "特大主动卖") return 1;
  return 2;
}

function renderMiniFacts(elementId, facts) {
  const element = document.querySelector(`#${elementId}`);
  if (!element) return;
  element.innerHTML = facts.map((item) => `<div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`).join("");
}

function renderCapitalFlowChart() {
  const mode = state.marketControls.flow;
  let rows;
  let primary;
  let benchmark;
  let summary;
  let facts = [];
  let updated;
  if (mode === "etf") {
    rows = state.dashboard?.etf_estimated_flow?.history || [];
    primary = rows.map((item) => scaleYuan(item.net_inflow_yuan));
    benchmark = alignBenchmark(rows, state.dashboard?.market_breadth?.history || []);
    const latest = state.dashboard?.etf_estimated_flow?.latest;
    const total = scaleYuan(latest?.data?.net_inflow_yuan);
    summary = total === null ? "预估申购净流入 —" : `预估申购净流入 ${signedNumber(total)} 亿元`;
    facts = [{ label: "口径", value: "申购量-赎回量 × IOPV" }];
    updated = latest;
  } else {
    const key = mode === "northbound" ? "northbound_capital" : "market_capital";
    const block = chartBlock(key);
    rows = historyData(key);
    primary = rows.map((item) => number(mode === "northbound" ? item.turnover : item.net_inflow));
    benchmark = rows.map((item) => number(item.szzz));
    const latest = block.latest?.data || {};
    if (mode === "northbound") {
      summary = latest.turnover === undefined ? "今日成交额 —" : `今日成交额 ${formatNumber(latest.turnover, 2)} 亿元`;
      facts = [
        { label: "沪股通", value: latest.turnover_sh === undefined ? "—" : `${formatNumber(latest.turnover_sh, 2)} 亿` },
        { label: "深股通", value: latest.turnover_sz === undefined ? "—" : `${formatNumber(latest.turnover_sz, 2)} 亿` },
      ];
    } else {
      summary = latest.net_inflow === undefined ? "大盘资金净流入 —" : `大盘资金净流入 ${signedNumber(latest.net_inflow)} 亿元`;
      facts = [{ label: "指标", value: "主力资金净流入" }];
    }
    updated = block.latest;
  }
  setText("capital-flow-summary", summary);
  document.querySelector("#capital-flow-summary").className = `chart-summary emphasis ${marketClass(primary.at(-1))}`;
  document.querySelector("#capital-flow-facts").innerHTML = facts.map((item) => `<div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`).join("");
  setText("flow-updated", formatSeriesUpdated({ latest: updated }));
  drawSeriesChart("capital-flow-chart", "capital-flow-empty", [
    { label: mode === "northbound" ? "成交额(亿)" : "净流入(亿)", values: primary, color: "#2563a7", fill: true },
    { label: "上证指数", values: benchmark, color: "#d98616", axis: "right" },
  ], rows.map(seriesLabel));
}

function renderSentimentChart() {
  const mode = state.marketControls.sentiment;
  const key = mode === "market" ? "market_sentiment" : mode === "sh50" ? "sentiment_sh50" : "sentiment_growth";
  const rows = historyData(key);
  const values = rows.map((item) => number(mode === "market" ? item.temperature : item.sentiment));
  const price = rows.map((item) => number(item.price));
  const label = mode === "market" ? "大盘温度" : mode === "sh50" ? "上证50情绪" : "创成长情绪";
  setText("sentiment-summary", `${label} ${values.at(-1) === null || values.at(-1) === undefined ? "—" : formatNumber(values.at(-1), 2)}`);
  setText("sentiment-updated", formatSeriesUpdated(chartBlock(key)));
  drawSeriesChart("sentiment-chart", "sentiment-empty", [
    {
      label,
      values,
      color: "#d6ddda",
      fill: false,
      rangeMin: 0,
      rangeMax: 100,
    },
    { label: "指数", values: price, color: "#7b8580", axis: "right" },
  ], rows.map(seriesLabel));
}

function renderGlobalChart() {
  const mode = state.marketControls.global;
  const key = mode === "a50" ? "futures_a50" : "futures_dow";
  const rows = historyData(key);
  const field = mode === "a50" ? "a50" : "dog";
  const rawValues = rows.map((item) => number(item[field]));
  const changes = rows.map((item) => number(item.zdf));
  const latest = rows.at(-1) || {};
  const latestValue = rawValues.at(-1);
  const latestChange = number(latest.zdf);
  const referenceValue = (
    latestValue !== null
    && latestChange !== null
    && latestChange !== -100
  ) ? latestValue / (1 + latestChange / 100) : null;
  const maxAbsChange = Math.max(
    ...changes.filter((value) => value !== null).map(Math.abs),
    0,
  );
  const frameSize = mode === "a50" ? 1426 : 1381;
  const leadingPadding = mode === "a50" ? 1 : 0;
  const values = frameSeries(rawValues, frameSize, leadingPadding);
  const rangeMin = referenceValue === null
    ? null
    : referenceValue * (1 - maxAbsChange / 100);
  const rangeMax = referenceValue === null
    ? null
    : referenceValue * (1 + maxAbsChange / 100);
  setText("global-summary", `${mode === "a50" ? "富时A50期指" : "道琼斯期指"} ${formatNumber(latestValue, 2)} · ${signedPercent(latest.zdf)}`);
  setText("global-updated", formatSeriesUpdated(chartBlock(key)));
  drawSeriesChart(
    "global-chart",
    "global-empty",
    [{
      label: mode === "a50" ? "富时A50" : "道琼斯30",
      values,
      color: "#2563a7",
      fill: true,
      rangeMin,
      rangeMax,
    }],
    [],
    {
      axisLabels: mode === "a50"
        ? ["16:45", "05:15", "16:30"]
        : ["06:00", "17:30", "05:00"],
    },
  );
}

function renderCurrencyChart() {
  const mode = state.marketControls.currency;
  const key = mode === "repo" ? "reverse_repo" : "usd_cny";
  const rows = historyData(key);
  const latest = rows.at(-1) || {};
  const values = rows.map((item) => number(mode === "repo" ? item.jtf : item.dollar_rmb));
  const benchmark = mode === "repo" ? rows.map((item) => number(item.szzz)) : [];
  const repoRange = mode === "repo" ? niceSymmetricLimit(values) : null;
  setText("currency-summary", mode === "repo" ? `逆回购净投放 ${formatNumber(values.at(-1), 2)} 亿元` : `美元/人民币 ${formatNumber(values.at(-1), 4)}`);
  setText(
    "currency-updated",
    mode === "repo" && rows.length
      ? `数据日期 ${formatValuationDate(latest?.date || latest?.trade_date)}`
      : formatSeriesUpdated(chartBlock(key)),
  );
  drawSeriesChart("currency-chart", "currency-empty", [
    {
      label: mode === "repo" ? "净投放(亿)" : "USD/CNY",
      values,
      color: mode === "repo" ? "#dc5a54" : "#7050a0",
      fill: mode !== "repo",
      type: mode === "repo" ? "bar" : "line",
      positiveColor: "#dc5a54",
      negativeColor: "#159b62",
      rangeMin: repoRange === null ? null : -repoRange,
      rangeMax: repoRange,
    },
    ...(mode === "repo" ? [{ label: "上证指数", values: benchmark, color: "#d98616", axis: "right" }] : []),
  ], rows.map(seriesLabel));
}

function renderValuationChart() {
  const market = state.marketControls.valuation;
  const metric = state.marketControls.valuationMetric;
  const key = `valuation_${market}`;
  const rows = historyData(key);
  const latest = rows.at(-1) || {};
  const risk = number(latest[`risk_${metric}`]);
  const chance = number(latest[`chance_${metric}`]);
  const indexPrice = number(latest.index_price);
  const marketName = market === "sh" ? "上证指数" : "深证成指";
  const metricName = metric === "pe" ? "市盈率危险/机会线" : "市净率危险/机会线";
  setText(
    "valuation-summary",
    `${metricName} ${formatNumber(risk, 2)} / ${formatNumber(chance, 2)} · ${marketName} ${formatNumber(indexPrice, 2)}`,
  );
  setText(
    "valuation-updated",
    rows.length
      ? `数据日期 ${formatValuationDate(latest.date || latest.trade_date)}`
      : "暂无数据",
  );
  drawValuationChart("valuation-chart", "valuation-empty", rows, metric);
}

function drawValuationChart(canvasId, emptyId, rows, metric) {
  const canvas = document.querySelector(`#${canvasId}`);
  const empty = document.querySelector(`#${emptyId}`);
  if (!canvas) return;
  const points = rows.map((item) => ({
    date: item.date || item.trade_date || "",
    index: number(item.index_price),
    risk: number(item[`risk_${metric}`]),
    chance: number(item[`chance_${metric}`]),
  })).filter((item) => item.index !== null && item.risk !== null && item.chance !== null);
  empty?.classList.toggle("visible", !points.length);
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!points.length) return;

  const left = 46, right = 48, top = 20, bottom = 28;
  const width = Math.max(1, rect.width - left - right);
  const height = Math.max(1, rect.height - top - bottom);
  const valuationValues = points.flatMap((point) => [point.risk, point.chance]);
  const valuationMin = Math.min(...valuationValues);
  const valuationMax = Math.max(...valuationValues);
  const valuationPadding = Math.max((valuationMax - valuationMin) * 0.10, metric === "pe" ? 0.25 : 0.03);
  const leftRange = { min: valuationMin - valuationPadding, max: valuationMax + valuationPadding };
  const indexValues = points.map((point) => point.index);
  const indexMin = Math.min(...indexValues);
  const indexMax = Math.max(...indexValues);
  const indexPadding = Math.max((indexMax - indexMin) * 0.12, 1);
  const rightRange = { min: indexMin - indexPadding, max: indexMax + indexPadding };
  const xFor = (index) => left + (index / Math.max(1, points.length - 1)) * width;
  const leftY = (value) => top + height - ((value - leftRange.min) / (leftRange.max - leftRange.min)) * height;
  const rightY = (value) => top + height - ((value - rightRange.min) / (rightRange.max - rightRange.min)) * height;

  ctx.font = "10px sans-serif";
  ctx.lineWidth = 1;
  for (let index = 0; index < 5; index += 1) {
    const ratioY = index / 4;
    const y = top + height * ratioY;
    ctx.strokeStyle = "#d8dedb";
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + width, y); ctx.stroke();
    ctx.fillStyle = "#66726c";
    ctx.textAlign = "right";
    ctx.fillText(formatNumber(leftRange.max - (leftRange.max - leftRange.min) * ratioY, 2), left - 5, y + 3);
    ctx.textAlign = "left";
    ctx.fillText(formatNumber(rightRange.max - (rightRange.max - rightRange.min) * ratioY, 0), left + width + 5, y + 3);
  }

  drawValuationBand(ctx, points, xFor, leftY, top, "risk", "above", "rgba(191, 63, 63, 0.19)");
  drawValuationBand(ctx, points, xFor, leftY, top + height, "chance", "below", "rgba(21, 155, 98, 0.20)");

  ctx.beginPath();
  points.forEach((point, index) => {
    const x = xFor(index);
    const y = rightY(point.index);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#397dc5";
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.fillStyle = "#ad3f3f";
  ctx.textAlign = "right";
  ctx.fillText("危险区", left + width - 5, Math.max(top + 12, leftY(points.at(-1).risk) - 5));
  ctx.fillStyle = "#087a55";
  ctx.fillText("机会区", left + width - 5, Math.min(top + height - 5, leftY(points.at(-1).chance) + 14));
  ctx.fillStyle = "#66726c";
  ctx.fillText("合理区", left + width - 5, (leftY(points.at(-1).risk) + leftY(points.at(-1).chance)) / 2 + 3);

  const labels = [points[0].date, points[Math.floor(points.length / 2)].date, points.at(-1).date].map(formatValuationDate);
  ctx.fillStyle = "#66726c";
  ctx.textAlign = "left"; ctx.fillText(labels[0], left, rect.height - 7);
  ctx.textAlign = "center"; ctx.fillText(labels[1], left + width / 2, rect.height - 7);
  ctx.textAlign = "right"; ctx.fillText(labels[2], left + width, rect.height - 7);
}

function drawValuationBand(ctx, points, xFor, yFor, edgeY, field, side, color) {
  ctx.beginPath();
  ctx.moveTo(xFor(0), edgeY);
  points.forEach((point, index) => ctx.lineTo(xFor(index), yFor(point[field])));
  ctx.lineTo(xFor(points.length - 1), edgeY);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = xFor(index), y = yFor(point[field]);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = side === "above" ? "rgba(173, 63, 63, 0.55)" : "rgba(8, 122, 85, 0.55)";
  ctx.lineWidth = 1;
  ctx.stroke();
}

function formatValuationDate(value) {
  const digits = String(value || "").replace(/\D/g, "");
  return digits.length >= 8
    ? `${digits.slice(0, 4)}/${digits.slice(4, 6)}/${digits.slice(6, 8)}`
    : String(value || "");
}

function renderBondChart() {
  const mode = state.marketControls.bond;
  const key = mode === "long" ? "bond_long" : "bond_short";
  const rows = trailingYearRows(historyData(key));
  const benchmarkRows = trailingYearRows(historyData("bond_benchmark"));
  const values = cumulativeReturnSeries(rows, "price");
  const benchmarkReturns = cumulativeReturnSeries(benchmarkRows, "price");
  const benchmarkByDate = new Map(
    benchmarkRows.map((item, index) => [
      String(item.date || item.trade_date || ""),
      benchmarkReturns[index],
    ]),
  );
  const benchmark = rows.map((item) => (
    benchmarkByDate.get(String(item.date || item.trade_date || "")) ?? null
  ));
  const title = mode === "long" ? "长期国债" : "短期国债";
  const instrument = mode === "long" ? "十年国债" : "两年国债";
  setText("bond-summary", `${title}近一年涨幅 ${signedPercent(values.at(-1))}`);
  const latestBond = rows.at(-1) || {};
  setText(
    "bond-updated",
    rows.length
      ? `数据日期 ${formatValuationDate(latestBond.date || latestBond.trade_date)}`
      : "暂无数据",
  );
  drawSeriesChart("bond-chart", "bond-empty", [
    { label: instrument, values, color: "#397dc5", fill: true },
    { label: "同花顺全A(沪深京)", values: benchmark, color: "#d56f42" },
  ], rows.map(seriesLabel));
}

function trailingYearRows(rows) {
  const datedRows = rows
    .map((item) => ({ item, timestamp: seriesDateTimestamp(item) }))
    .filter(({ timestamp }) => timestamp !== null);
  if (!datedRows.length) return rows;
  const today = new Date();
  const cutoff = Date.UTC(
    today.getFullYear() - 1,
    today.getMonth(),
    today.getDate(),
  );
  return datedRows
    .filter(({ timestamp }) => timestamp >= cutoff)
    .map(({ item }) => item);
}

function seriesDateTimestamp(item) {
  const raw = String(item.date || item.trade_date || "").replace(/\D/g, "");
  if (raw.length < 8) return null;
  const timestamp = Date.UTC(
    Number(raw.slice(0, 4)),
    Number(raw.slice(4, 6)) - 1,
    Number(raw.slice(6, 8)),
  );
  return Number.isFinite(timestamp) ? timestamp : null;
}

function cumulativeReturnSeries(rows, field) {
  const values = rows.map((item) => number(item[field]));
  const base = values.find((value) => value !== null && value !== 0);
  if (base === undefined) return values.map(() => null);
  return values.map((value) => (
    value === null ? null : ((value / base) - 1) * 100
  ));
}

function drawSeriesChart(canvasId, emptyId, series, labels, options = {}) {
  const canvas = document.querySelector(`#${canvasId}`);
  const empty = document.querySelector(`#${emptyId}`);
  if (!canvas) return;
  const usable = series.filter((item) => item.values.some((value) => number(value) !== null));
  empty?.classList.toggle("visible", !usable.length);
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!usable.length) return;
  const left = 52, right = options.symmetricAxis || usable.some((item) => item.axis === "right") ? 48 : 14, top = (options.markers || []).length ? 52 : 28, bottom = 26;
  const width = Math.max(1, rect.width - left - right);
  const height = Math.max(1, rect.height - top - bottom);
  ctx.strokeStyle = "#d8dedb";
  ctx.fillStyle = "#66726c";
  ctx.font = "10px sans-serif";
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) {
    const y = top + (height * i) / 3;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + width, y); ctx.stroke();
  }
  const ranges = {};
  ["left", "right"].forEach((axis) => {
    const axisSeries = usable.filter(
      (item) => (item.axis || "left") === axis,
    );
    const values = axisSeries.flatMap((item) =>
      item.values.map(number).filter((value) => value !== null),
    );
    if (!values.length) return;
    let min = Math.min(...values), max = Math.max(...values);
    const configuredMin = axisSeries
      .map((item) => number(item.rangeMin))
      .find((value) => value !== null);
    const configuredMax = axisSeries
      .map((item) => number(item.rangeMax))
      .find((value) => value !== null);
    if (configuredMin !== undefined) min = configuredMin;
    if (configuredMax !== undefined) max = configuredMax;
    if (min === max) { const pad = Math.max(Math.abs(min) * 0.05, 1); min -= pad; max += pad; }
    ranges[axis] = { min, max };
  });
  if (options.symmetricAxis && ranges.left) {
    const axis = options.symmetricAxis;
    const ticks = [
      [top, axis.max, axis.percent_max],
      [top + height / 2, axis.center, 0],
      [top + height, axis.min, axis.percent_min],
    ];
    ctx.font = "10px sans-serif";
    ticks.forEach(([y, point, percent]) => {
      ctx.fillStyle = Number(percent) > 0 ? "#d5443e" : Number(percent) < 0 ? "#159a69" : "#66726c";
      ctx.textAlign = "right";
      ctx.fillText(Number(point).toFixed(2), left - 5, y + 3);
      ctx.textAlign = "left";
      ctx.fillText(`${Number(percent) > 0 ? "+" : ""}${Number(percent).toFixed(2)}%`, left + width + 5, y + 3);
    });
  }
  usable.forEach((item, seriesIndex) => {
    const axis = item.axis || "left";
    const range = ranges[axis];
    const points = item.values.map((value, index) => ({ value: number(value), x: left + (item.values.length <= 1 ? width / 2 : (index / (item.values.length - 1)) * width) }));
    const yFor = (value) => top + height - ((value - range.min) / (range.max - range.min)) * height;
    if (item.type === "bar") {
      const baselineValue = Math.min(range.max, Math.max(range.min, 0));
      const baselineY = yFor(baselineValue);
      const barWidth = Math.max(
        2,
        Math.min(12, (width / Math.max(1, item.values.length)) * 0.64),
      );
      points.forEach((point) => {
        if (point.value === null) return;
        const valueY = yFor(point.value);
        ctx.fillStyle = point.value >= 0
          ? (item.positiveColor || item.color)
          : (item.negativeColor || item.color);
        ctx.fillRect(
          point.x - barWidth / 2,
          Math.min(valueY, baselineY),
          barWidth,
          Math.max(1, Math.abs(valueY - baselineY)),
        );
      });
      ctx.strokeStyle = "#87918c";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(left, baselineY);
      ctx.lineTo(left + width, baselineY);
      ctx.stroke();
    }
    if (item.fill) {
      const valid = points.filter((point) => point.value !== null);
      if (valid.length > 1) {
        ctx.beginPath(); ctx.moveTo(valid[0].x, top + height); valid.forEach((point) => ctx.lineTo(point.x, yFor(point.value))); ctx.lineTo(valid.at(-1).x, top + height); ctx.closePath();
        const gradient = ctx.createLinearGradient(0, top, 0, top + height); gradient.addColorStop(0, `${item.color}30`); gradient.addColorStop(1, `${item.color}03`); ctx.fillStyle = gradient; ctx.fill();
      }
    }
    if (item.type !== "bar") {
      ctx.beginPath(); let started = false;
      points.forEach((point) => { if (point.value === null) { started = false; return; } const y = yFor(point.value); if (!started) { ctx.moveTo(point.x, y); started = true; } else ctx.lineTo(point.x, y); });
      ctx.strokeStyle = item.color; ctx.lineWidth = 2; ctx.stroke();
    }
    const legendX = left + seriesIndex * 110;
    if (item.type === "bar") {
      ctx.fillStyle = item.positiveColor || item.color;
      ctx.fillRect(legendX, 6, 8, 8);
      ctx.fillStyle = item.negativeColor || item.color;
      ctx.fillRect(legendX + 11, 6, 8, 8);
      ctx.fillStyle = "#66726c";
      ctx.fillText(item.label, legendX + 24, 13);
    } else {
      ctx.fillStyle = item.color;
      ctx.fillRect(legendX, 7, 10, 3);
      ctx.fillStyle = "#66726c";
      ctx.fillText(item.label, legendX + 15, 12);
    }
  });
  const markers = options.markers || [];
  const primary = usable[0];
  const primaryRange = ranges[primary?.axis || "left"];
  if (primary && primaryRange && markers.length) {
    const yFor = (value) => top + height - ((value - primaryRange.min) / (primaryRange.max - primaryRange.min)) * height;
    markers.forEach((marker, markerIndex) => {
      const index = Math.max(0, Math.min(primary.values.length - 1, Number(marker.index) || 0));
      const value = number(primary.values[index]);
      if (value === null) return;
      const x = left + (primary.values.length <= 1 ? width / 2 : (index / (primary.values.length - 1)) * width);
      const y = yFor(value);
      ctx.strokeStyle = "#c47d16";
      ctx.fillStyle = "#c47d16";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, top + 16 + (markerIndex % 2) * 12); ctx.lineTo(x, y); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
      ctx.save();
      ctx.translate(x + 3, top + 13 + (markerIndex % 2) * 12);
      ctx.rotate(-0.2);
      ctx.font = "10px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(String(marker.label || "异动").slice(0, 12), 0, 0);
      ctx.restore();
    });
  }
  const axisLabels = options.axisLabels || [labels[0] || "", labels.at(-1) || ""];
  ctx.fillStyle = "#66726c";
  ctx.textAlign = "left";
  ctx.fillText(axisLabels[0] || "", left, rect.height - 7);
  if (axisLabels.length >= 3) {
    ctx.textAlign = "center";
    ctx.fillText(axisLabels[1] || "", left + width / 2, rect.height - 7);
  }
  ctx.textAlign = "right";
  ctx.fillText(axisLabels.at(-1) || "", left + width, rect.height - 7);
}

function frameSeries(values, frameSize, leadingPadding = 0) {
  const normalizedSize = Math.max(values.length + leadingPadding, frameSize);
  const framed = [
    ...Array(leadingPadding).fill(null),
    ...values,
  ];
  return [
    ...framed,
    ...Array(Math.max(0, normalizedSize - framed.length)).fill(null),
  ];
}

function niceSymmetricLimit(values) {
  const maxAbs = Math.max(
    ...values.filter((value) => value !== null).map((value) => Math.abs(value)),
    0,
  );
  if (!maxAbs) return null;
  const magnitude = 10 ** Math.floor(Math.log10(maxAbs));
  return Math.ceil(maxAbs / magnitude) * magnitude;
}

function alignBenchmark(primaryRows, benchmarkRows) {
  if (!primaryRows.length || !benchmarkRows.length) return [];
  return primaryRows.map((row) => {
    const target = dateValue(row.bucket_at || row.observed_at);
    const nearest = benchmarkRows.reduce((best, item) => {
      const delta = Math.abs(dateValue(item.bucket_at) - target);
      return !best || delta < best.delta ? { delta, value: number(item.sh_index) } : best;
    }, null);
    return nearest?.value ?? null;
  });
}

function scaleYuan(value) {
  const parsed = number(value);
  return parsed === null ? null : parsed / 100000000;
}

function seriesLabel(item) {
  const value = item.x_index || item.time || item.date || item.trade_date || item.bucket_at;
  if (!value) return "";
  if (String(value).includes("T")) return formatClock(value);
  return String(value).replace(/^\d{4}(\d{2})(\d{2})(\d{2})(\d{2})$/, "$3:$4");
}

function formatSeriesUpdated(block) {
  const latest = block?.latest;
  const value = latest?.observed_at || latest?.bucket_at || latest?.fetched_at;
  return value ? `更新于 ${formatDateTime(value)}` : "暂无数据";
}

function renderSourceHealth() {
  const runs = [...(state.dashboard.collection_sources || [])];
  const severity = { failed: 0, running: 1, partial_success: 2, success: 3, skipped: 4 };
  runs.sort((a, b) => {
    const statusDiff = (severity[a.status] ?? 5) - (severity[b.status] ?? 5);
    if (statusDiff) return statusDiff;
    return dateValue(b.started_at) - dateValue(a.started_at);
  });
  const visible = runs.slice(0, 10);
  const list = document.querySelector("#source-health-list");
  list.innerHTML = visible.length
    ? visible.map((run) => `
      <div class="source-health-item">
        <div>
          <strong title="${escapeHtml(run.task_name)}">${escapeHtml(displayTaskName(run.task_name))}</strong>
          <span title="${escapeHtml(run.source_name)}">${escapeHtml(run.source_name)} · ${relativeTime(run.started_at)}</span>
        </div>
        <span class="status-badge ${escapeHtml(run.status)}">${escapeHtml(statusLabel(run.status))}</span>
      </div>`).join("")
    : emptyBlock("尚无采集运行记录");
  const failures = runs.filter((item) => item.status === "failed").length;
  const summary = document.querySelector("#health-summary");
  summary.className = `status-badge ${failures ? "failed" : runs.length ? "success" : "neutral"}`;
  summary.textContent = failures ? `${failures} 个异常源` : runs.length ? "运行正常" : "等待运行";
}

function renderMarketContext() {
  const context = state.dashboard.market_context || {};
  const capitalFlow = context.capital_flow || {};
  const capComparison = context.cap_comparison || {};
  const environment = context.environment || {};
  const margin = environment.margin || {};
  const northbound = environment.northbound || {};
  const marketStatus = context.market_status || {};
  const limitUp = context.limit_up?.today?.num ?? context.limit_up?.total;
  const limitDown = context.limit_down?.today?.num ?? context.limit_down?.total;
  const mainFlow = number(capitalFlow.totalMainFlow);
  const largeCap = capComparison.largeCap || {};
  const smallCap = capComparison.smallCap || {};
  const pulse = [
    {
      label: "交易状态",
      value: marketStatus.name || "状态未知",
      detail: marketStatus.start_time && marketStatus.end_time
        ? `${marketStatus.start_time}–${marketStatus.end_time}`
        : "等待交易时段数据",
      tone: "neutral",
    },
    {
      label: "大盘主力净流入",
      value: mainFlow === null ? "—" : `${mainFlow > 0 ? "+" : ""}${formatNumber(mainFlow)} 亿`,
      detail: (capitalFlow.details || [])
        .map((item) => `${item.market} ${signedNumber(item.mainFlow)} 亿`)
        .join(" · ") || "暂无沪深分项",
      tone: marketClass(mainFlow),
    },
    {
      label: "涨跌停",
      value: `${formatInteger(limitUp)} : ${formatInteger(limitDown)}`,
      detail: `昨日 ${formatInteger(context.limit_up?.yesterday?.num)} : ${formatInteger(context.limit_down?.yesterday?.num)}`,
      tone: number(limitUp) >= number(limitDown) ? "positive" : "negative",
    },
    {
      label: "大小盘表现",
      value: largeCap.name && smallCap.name
        ? `${signedPercent(largeCap.changeRate, 1)} : ${signedPercent(smallCap.changeRate, 1)}`
        : "—",
      detail: largeCap.name && smallCap.name
        ? `${largeCap.name} : ${smallCap.name} · 差 ${formatNumber(capComparison.diff)} 个百分点`
        : "暂无风格对比",
      tone: "neutral",
    },
    {
      label: "融资余额",
      value: margin.latest?.rzye === undefined
        ? "—"
        : `${formatNumber(margin.latest.rzye)} 亿`,
      detail: margin.latest?.rzjme === undefined
        ? "暂无当日净买入"
        : `当日融资净买入 ${signedNumber(margin.latest.rzjme)} 亿 · ${margin.latest.date || ""}`,
      tone: marketClass(margin.latest?.rzjme),
    },
    {
      label: "北向成交",
      value: northbound.latest?.dealAmt === undefined
        ? "—"
        : `${formatNumber(northbound.latest.dealAmt)} 亿`,
      detail: northbound.avg5d === undefined
        ? "暂无历史均值"
        : `5 日均值 ${formatNumber(northbound.avg5d)} 亿 · 20 日 ${formatNumber(northbound.avg20d)} 亿`,
      tone: "neutral",
    },
  ];
  document.querySelector("#market-pulse-grid").innerHTML = pulse.map((item) => `
    <div class="market-pulse-item">
      <span>${escapeHtml(item.label)}</span>
      <strong class="${escapeHtml(item.tone)}">${escapeHtml(item.value)}</strong>
      <small title="${escapeHtml(item.detail)}">${escapeHtml(item.detail)}</small>
    </div>
  `).join("");
  setText(
    "market-context-updated-at",
    context.updated_at ? `更新于 ${formatDateTime(context.updated_at)}` : "暂无数据",
  );

  const hotStocks = context.hot_stocks || [];
  document.querySelector("#market-hot-stocks").innerHTML = hotStocks.length
    ? hotStocks.map((item) => `
      <div class="context-row quote">
        <div>
          <strong>${escapeHtml(item.name || item.code || "未知标的")}</strong>
          <small>${escapeHtml(item.code || "")} · ${escapeHtml(formatNumber(item.price, 2))}</small>
        </div>
        <strong class="${marketClass(item.percent)}">${escapeHtml(signedPercent(item.percent))}</strong>
      </div>
    `).join("")
    : emptyBlock("尚无热门股票");

  const limitStocks = context.limit_stocks || [];
  document.querySelector("#market-limit-stocks").innerHTML = limitStocks.length
    ? limitStocks.map((item) => `
      <div class="context-row quote">
        <div>
          <strong>${escapeHtml(item.name || item.code || "未知标的")}</strong>
          <small>${escapeHtml(item.code || "")} · ${escapeHtml(item.high_days || "—")}</small>
        </div>
        <strong class="${marketClass(item.change_rate)}">${escapeHtml(signedPercent(item.change_rate))}</strong>
      </div>
    `).join("")
    : emptyBlock("尚无涨停池数据");
}

function renderMacroMarket() {
  const context = state.dashboard.market_context || {};
  const environment = context.environment || {};
  const rows = [];
  if (environment.bond) {
    rows.push({
      label: environment.bond.etfName || "国债市场",
      value: formatNumber(environment.bond.close, 3),
      detail: `较 20 日均线 ${signedPercent(environment.bond.devMa20)} · ${environment.bond.date || ""}`,
      tone: marketClass(environment.bond.devMa20),
    });
  }
  (state.dashboard.macro_market || []).forEach((snapshot) => {
    const data = snapshot.data || {};
    if (snapshot.data_type === "government_bond_yield") {
      const market = data.market === "us" ? "美国" : data.market === "cn" ? "中国" : data.market;
      rows.push({
        label: `${market || ""}${data.tenor || ""}国债收益率`,
        value: data.yield === undefined ? "—" : `${formatNumber(data.yield, 4)}%`,
        detail: `${data.date || ""} · ${snapshot.provider || ""}`,
        tone: "neutral",
      });
      return;
    }
    if (snapshot.data_type === "bond_index") {
      rows.push({
        label: data.indicator_name || data.index_name || "中债指数",
        value: data.value === undefined ? "—" : formatNumber(data.value, 4),
        detail: `${data.index_name || ""} · ${data.date || ""} · 官方`,
        tone: "neutral",
      });
      return;
    }
    if (snapshot.data_type === "market_pe") {
      rows.push({
        label: `${data.market_name || "A股市场"} PE`,
        value: data.pe === undefined ? "—" : formatNumber(data.pe, 2),
        detail: `${data.date || ""} · 第三方市场口径`,
        tone: "neutral",
      });
      return;
    }
    if (snapshot.data_type === "market_pb") {
      rows.push({
        label: `${data.market_name || "A股市场"} PB`,
        value: data.pb === undefined ? "—" : formatNumber(data.pb, 2),
        detail: `${data.date || ""} · 第三方市场口径`,
        tone: "neutral",
      });
      return;
    }
    if (snapshot.data_type === "market_valuation_threshold") {
      rows.push({
        label: `${data.market_name || "A股市场"}估值阈值`,
        value: data.risk_pe === undefined
          ? "—"
          : `PE ${formatNumber(data.chance_pe, 2)}–${formatNumber(data.risk_pe, 2)}`,
        detail: data.risk_pb === undefined
          ? `${data.date || ""} · 同花顺页面阈值`
          : `PB ${formatNumber(data.chance_pb, 2)}–${formatNumber(data.risk_pb, 2)} · 非当前 PE/PB`,
        tone: "neutral",
      });
      return;
    }
    if (snapshot.data_type === "bond_market_price") {
      rows.push({
        label: data.name || (data.tenor === "long" ? "长期国债" : "短期国债"),
        value: data.price === undefined ? "—" : formatNumber(data.price, 3),
        detail: `${data.date || ""} · 同花顺国债期货主连`,
        tone: "neutral",
      });
      return;
    }
    if (snapshot.data_type === "index_sentiment") {
      rows.push({
        label: `${data.index_name || "指数"}情绪`,
        value: data.sentiment === undefined ? "—" : formatNumber(data.sentiment, 2),
        detail: `${data.date || ""} · 指数 ${formatNumber(data.price, 2)} · 同花顺黑盒指标`,
        tone: "neutral",
      });
      return;
    }
    if (snapshot.data_type === "reverse_repo") {
      rows.push({
        label: "央行逆回购净投放",
        value: data.jtf === undefined ? "—" : `${formatNumber(data.jtf, 2)} 亿`,
        detail: `${data.date || ""} · 上证 ${formatNumber(data.szzz, 2)}`,
        tone: marketClass(data.jtf),
      });
      return;
    }
    if (data.lpr1y !== undefined || data.lpr5y !== undefined) {
      rows.push({
        label: "LPR",
        value: `${formatNumber(data.lpr1y)}% / ${formatNumber(data.lpr5y)}%`,
        detail: `1 年期 / 5 年期 · ${data.date || ""}`,
        tone: "neutral",
      });
      return;
    }
    rows.push({
      label: snapshot.subject_id?.split(":").at(-1) || "市场利率",
      value: data.rate === undefined ? "—" : `${formatNumber(data.rate, 3)}%`,
      detail: data.change === undefined
        ? `${data.date || ""} · ${snapshot.provider || ""}`
        : `变动 ${signedNumber(data.change)} BP · ${data.date || ""}`,
      tone: marketClass(data.change),
    });
  });

  document.querySelector("#macro-market-grid").innerHTML = rows.length
    ? rows.map((item) => `
      <div class="macro-market-item">
        <span title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</span>
        <strong class="${escapeHtml(item.tone)}">${escapeHtml(item.value)}</strong>
        <small title="${escapeHtml(item.detail)}">${escapeHtml(item.detail)}</small>
      </div>
    `).join("")
    : emptyBlock("尚未采集利率与债券数据");
}

function renderThsMarketSignals() {
  const snapshots = state.dashboard.ths_market_signals || [];
  const snapshotsByType = new Map(
    snapshots.map((snapshot) => [snapshot.data_type, snapshot]),
  );
  const expectedSignals = [
    "market_capital",
    "northbound_capital",
    "market_sentiment",
    "market_anomaly",
    "call_auction",
  ];
  const rows = expectedSignals.map((dataType) => {
    const snapshot = snapshotsByType.get(dataType);
    if (!snapshot) {
      if (dataType === "market_anomaly") {
        return {
          label: "大盘与个股异动",
          value: "—",
          detail: "交易时段自动采集，当前尚无历史快照",
          tone: "neutral",
        };
      }
      if (dataType === "call_auction") {
        return {
          label: "集合竞价",
          value: "—",
          detail: "交易日 09:15-09:30 自动采集",
          tone: "neutral",
        };
      }
      return {
        label: dataType,
        value: "—",
        detail: "等待首次采集",
        tone: "neutral",
      };
    }
    const data = snapshot.data || {};
    if (snapshot.data_type === "market_capital") {
      return {
        label: "大盘主力净流入",
        value: data.net_inflow === undefined ? "—" : `${formatNumber(data.net_inflow, 2)} 亿`,
        detail: `${data.x_index || data.time || ""} · 上证 ${formatNumber(data.szzz, 2)}`,
        tone: marketClass(data.net_inflow),
      };
    }
    if (snapshot.data_type === "market_sentiment") {
      return {
        label: "大盘市场温度",
        value: data.temperature === undefined ? "—" : formatNumber(data.temperature, 0),
        detail: `${data.x_index || data.time || ""} · 同花顺综合指标`,
        tone: "neutral",
      };
    }
    if (snapshot.data_type === "northbound_capital") {
      const directionalAvailable = data.net_purchase !== null
        && data.net_purchase !== undefined;
      return {
        label: "北向资金",
        value: data.turnover === undefined || data.turnover === null
          ? "—"
          : `${formatNumber(data.turnover, 2)} 亿`,
        detail: directionalAvailable
          ? `净买额 ${signedNumber(data.net_purchase)} 亿 · ${data.x_index || data.time || ""}`
          : `沪 ${formatNumber(data.turnover_sh, 2)} 亿 · 深 ${formatNumber(data.turnover_sz, 2)} 亿 · 方向字段待更新`,
        tone: directionalAvailable ? marketClass(data.net_purchase) : "neutral",
      };
    }
    if (snapshot.data_type === "market_anomaly") {
      return {
        label: "大盘与个股异动",
        value: formatNumber(data.count || 0, 0),
        detail: `${(data.market_events || []).length} 大盘 · ${(data.stock_events || []).length} 个股`,
        tone: "neutral",
      };
    }
    return {
      label: "集合竞价",
      value: data.stage === undefined || data.stage === null ? "—" : `阶段 ${data.stage}`,
      detail: `${(data.hot_stocks || []).length} 热点 · ${(data.limit_up_stocks || []).length} 涨停 · ${(data.hot_sectors || []).length} 板块`,
      tone: "neutral",
    };
  });
  document.querySelector("#ths-market-signal-grid").innerHTML = rows.length
    ? rows.map((item) => `
      <div class="macro-market-item">
        <span title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</span>
        <strong class="${escapeHtml(item.tone)}">${escapeHtml(item.value)}</strong>
        <small title="${escapeHtml(item.detail)}">${escapeHtml(item.detail)}</small>
      </div>
    `).join("")
    : emptyBlock("尚未采集同花顺客观市场信号");

}

function renderSectors() {
  const quotes = state.dashboard.sector_quotes || [];
  const flows = state.dashboard.sector_flows || [];
  renderRanking(
    "sector-gainers",
    quotes.filter((item) => item.change_pct !== null).slice(0, 8),
    (item) => signedPercent(item.change_pct),
    (item) => marketClass(item.change_pct),
  );
  renderRanking(
    "sector-losers",
    [...quotes]
      .filter((item) => item.change_pct !== null)
      .sort((a, b) => a.change_pct - b.change_pct)
      .slice(0, 8),
    (item) => signedPercent(item.change_pct),
    (item) => marketClass(item.change_pct),
  );
  renderRanking(
    "sector-flows",
    flows.filter((item) => item.main_net_inflow !== null).slice(0, 8),
    (item) => formatMoney(item.main_net_inflow),
    (item) => marketClass(item.main_net_inflow),
  );
  const latest = [...quotes, ...flows]
    .map((item) => item.bucket_at)
    .filter(Boolean)
    .sort()
    .at(-1);
  setText("sector-updated-at", latest ? `更新于 ${formatDateTime(latest)}` : "暂无数据");
}

function renderRanking(id, items, valueFormatter, classFormatter) {
  const container = document.querySelector(`#${id}`);
  container.innerHTML = items.length
    ? items.map((item, index) => `
      <div class="ranking-row">
        <i>${index + 1}</i>
        <span title="${escapeHtml(item.sector_name)}">${escapeHtml(item.sector_name)}</span>
        <b class="${classFormatter(item)}">${escapeHtml(valueFormatter(item))}</b>
      </div>`).join("")
    : emptyBlock("暂无排名数据");
}

function renderCrossMarket() {
  const snapshots = state.dashboard.cross_market || [];
  const items = snapshots.flatMap(flattenMarketSnapshot);
  const grid = document.querySelector("#cross-market-grid");
  grid.innerHTML = items.length
    ? items.map((item) => `
      <div class="cross-item">
        <span title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
        <strong>${escapeHtml(formatNumber(item.value, item.valueDigits))}</strong>
        <small class="${marketClass(item.change)}">${escapeHtml(item.change === null ? item.detail : signedPercent(item.change))}</small>
      </div>`).join("")
    : emptyBlock("尚未采集跨市场行情");
}

function flattenMarketSnapshot(snapshot) {
  const data = snapshot.data || {};
  const arrayKeys = ["indices", "futures", "forex", "rates", "quotes", "items"];
  const values = arrayKeys.map((key) => data[key]).find(Array.isArray);
  if (!values) {
    return [{
      name: data.name || snapshot.subject_id || snapshot.data_type,
      value: firstNumber(data, ["price", "latest", "value", "close", "rate", "a50", "dog", "dollar_rmb"]),
      change: firstNumber(data, ["changeRate", "change_percent", "change_pct", "change_rate", "zdf"]),
      detail: data.x_index || data.time || snapshot.provider || "",
      valueDigits: 2,
    }];
  }
  return values.map((item) => ({
    name: item.name || item.symbol || item.code || item.contract || snapshot.subject_id,
    value: firstNumber(item, ["price", "latest", "current", "close", "rate", "value"]),
    change: firstNumber(item, ["changeRate", "change_percent", "change_pct", "change_rate"]),
    detail: item.currency || item.exchange || snapshot.provider || "",
    valueDigits: 2,
  }));
}

function renderSnapshotFilters() {
  const select = document.querySelector("#snapshot-type-filter");
  const selected = select.value;
  const types = [...new Set((state.dashboard.latest_records || []).map((item) => item.data_type))]
    .filter(Boolean)
    .sort();
  select.innerHTML = `<option value="">全部类型</option>${types.map((type) => (
    `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`
  )).join("")}`;
  if (types.includes(selected)) select.value = selected;
}

function renderSnapshots() {
  const rows = state.dashboard?.latest_records || [];
  const query = document.querySelector("#snapshot-search").value.trim().toLowerCase();
  const type = document.querySelector("#snapshot-type-filter").value;
  const filtered = rows.filter((row) => {
    if (type && row.data_type !== type) return false;
    if (!query) return true;
    return [
      row.data_type,
      row.subject_id,
      row.subject_type,
      row.market,
      row.provider,
    ].some((value) => String(value || "").toLowerCase().includes(query));
  });
  setText("snapshot-count", `${filtered.length} 条`);
  const body = document.querySelector("#snapshot-table-body");
  body.innerHTML = filtered.length
    ? filtered.map((row, index) => `
      <tr>
        <td title="${escapeHtml(row.data_type)}">${escapeHtml(row.data_type)}</td>
        <td title="${escapeHtml(row.subject_id)}">${escapeHtml(row.subject_id)}</td>
        <td>${escapeHtml(row.market || "—")}</td>
        <td>${escapeHtml(row.provider || "—")}</td>
        <td><span class="status-badge ${escapeHtml(row.freshness_status)}">${escapeHtml(freshnessLabel(row.freshness_status))}</span></td>
        <td title="${escapeHtml(formatDateTime(row.observed_at || row.fetched_at))}">${escapeHtml(formatDateTime(row.observed_at || row.fetched_at))}</td>
        <td title="${escapeHtml(formatDateTime(row.fetched_at))}">${escapeHtml(formatDateTime(row.fetched_at))}</td>
        <td><button class="data-button" type="button" data-record-index="${index}">查看</button></td>
      </tr>`).join("")
    : tableEmpty(8, "没有匹配的行情快照");
  body.querySelectorAll("[data-record-index]").forEach((button) => {
    button.addEventListener("click", () => {
      const row = filtered[Number(button.dataset.recordIndex)];
      openRecord("SNAPSHOT", `${row.data_type} · ${row.subject_id}`, row);
    });
  });
}

function renderRuns() {
  const rows = state.collectionTasks || [];
  const query = document.querySelector("#run-search").value.trim().toLowerCase();
  const source = document.querySelector("#run-source-filter").value;
  const module = document.querySelector("#run-module-filter").value;
  const channel = document.querySelector("#run-channel-filter").value;
  const period = document.querySelector("#run-period-filter").value;
  const statuses = selectedRunStatuses();
  setText("run-status-label", statuses.size ? `${statuses.size} 个状态` : "全部状态");
  populateTaskFilter("run-source-filter", rows.map((row) => row.source));
  populateTaskFilter("run-module-filter", rows.map((row) => row.module));
  populateTaskFilter("run-channel-filter", rows.map((row) => row.channel_label));
  populateTaskFilter("run-period-filter", rows.map((row) => row.period_label));
  const filtered = rows
    .filter((row) => (!source || row.source === source) && (!module || row.module === module) && (!channel || row.channel_label === channel) && (!period || row.period_label === period) && (!statuses.size || statuses.has(row.status)))
    .filter((row) => !query || [row.name, row.task_name, row.source, row.module, row.category, row.channel_label, row.error_message]
      .some((value) => String(value || "").toLowerCase().includes(query)))
    .sort((a, b) => a.source.localeCompare(b.source, "zh-CN") || a.module.localeCompare(b.module, "zh-CN") || a.name.localeCompare(b.name, "zh-CN"));
  setText("run-count", `${filtered.length} / ${rows.length} 个任务`);
  const healthy = rows.filter((row) => ["success", "partial_success", "running", "skipped"].includes(row.status)).length;
  const delayed = rows.filter((row) => row.status === "delayed").length;
  const failed = rows.filter((row) => row.status === "failed").length;
  const pending = rows.filter((row) => row.status === "pending").length;
  document.querySelector("#task-health-summary").innerHTML = `<span><b>${healthy}</b> 正常</span><span class="${delayed ? "warning" : ""}"><b>${delayed}</b> 延迟</span><span class="${failed ? "negative" : ""}"><b>${failed}</b> 失败</span><span><b>${pending}</b> 待首次执行</span>`;
  const body = document.querySelector("#run-table-body");
  const groups = new Map();
  filtered.forEach((row) => {
    const key = taskGroupKey(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  if (!state.taskGroupsInitialized && groups.size) {
    groups.forEach((_, key) => state.collapsedTaskGroups.add(key));
    state.taskGroupsInitialized = true;
    persistCollapsedTaskGroups();
  }
  body.innerHTML = filtered.length
    ? [...groups.entries()].map(([key, groupRows]) => {
      const first = groupRows[0];
      const collapsed = state.collapsedTaskGroups.has(key);
      const failedCount = groupRows.filter((row) => row.status === "failed").length;
      const delayedCount = groupRows.filter((row) => row.status === "delayed").length;
      const encodedKey = encodeURIComponent(key);
      const groupStatus = failedCount
        ? `<span class="task-group-alert negative">${failedCount} 失败</span>`
        : delayedCount
          ? `<span class="task-group-alert warning">${delayedCount} 延迟</span>`
          : "";
      const groupHeader = `<tr class="task-group-row"><td colspan="8"><button type="button" class="task-group-toggle" data-task-group-toggle="${encodedKey}" aria-expanded="${collapsed ? "false" : "true"}"><span class="task-group-chevron" aria-hidden="true">${collapsed ? "›" : "⌄"}</span><strong>${escapeHtml(first.source)}</strong><span>${escapeHtml(first.module)}</span><small>${groupRows.length} 个任务</small>${groupStatus}</button></td></tr>`;
      const taskRows = groupRows.map((row) => `
      <tr class="task-detail-row"${collapsed ? " hidden" : ""}>
        <td><strong>${escapeHtml(row.source)}</strong><br><small>${escapeHtml(row.module)} · ${escapeHtml(row.category)}</small></td>
        <td title="${escapeHtml(row.task_name)}"><strong>${escapeHtml(row.name)}</strong><br><small>${escapeHtml(row.task_name)}</small></td>
        <td><span class="task-channel ${escapeHtml(row.channel)}">${escapeHtml(row.channel_label)}</span></td>
        <td>${escapeHtml(row.period_label || "—")}</td>
        <td title="${escapeHtml(formatDateTime(row.last_data_at))}">${escapeHtml(formatDateTime(row.last_data_at))}<br><small>${escapeHtml(relativeTime(row.last_data_at))}</small></td>
        <td>${escapeHtml(formatDateTime(row.last_run_at))}</td>
        <td>${row.duration_ms == null ? (row.channel === "push" ? "事件驱动" : "—") : `${formatNumber(row.duration_ms / 1000, 2)} 秒`}</td>
        <td title="${escapeHtml(row.error_message || "")}"><span class="status-badge ${escapeHtml(row.status)}">${escapeHtml(statusLabel(row.status))}</span>${row.channel !== "push" && !String(row.id).startsWith("backfill:") ? `<button class="task-trigger-button" type="button" data-trigger-task="${escapeHtml(row.id)}">立即执行</button>` : ""}${row.error_message ? `<br><small class="negative">${escapeHtml(row.error_message)}</small>` : ""}</td>
      </tr>`).join("");
      return groupHeader + taskRows;
    }).join("")
    : tableEmpty(8, "没有匹配的采集任务");
}

function taskGroupKey(row) {
  return `${row.source || ""}\u0000${row.module || ""}`;
}

function visibleTaskGroups() {
  const rows = state.collectionTasks || [];
  const query = document.querySelector("#run-search").value.trim().toLowerCase();
  const source = document.querySelector("#run-source-filter").value;
  const module = document.querySelector("#run-module-filter").value;
  const channel = document.querySelector("#run-channel-filter").value;
  const period = document.querySelector("#run-period-filter").value;
  const statuses = selectedRunStatuses();
  return new Set(rows
    .filter((row) => (!source || row.source === source) && (!module || row.module === module) && (!channel || row.channel_label === channel) && (!period || row.period_label === period) && (!statuses.size || statuses.has(row.status)))
    .filter((row) => !query || [row.name, row.task_name, row.source, row.module, row.category, row.channel_label, row.error_message]
      .some((value) => String(value || "").toLowerCase().includes(query)))
    .map(taskGroupKey));
}

function selectedRunStatuses() {
  return new Set([...document.querySelectorAll("#run-status-filter input:checked")]
    .map((input) => input.value));
}

async function triggerCollectionTask(schedulerId, button) {
  if (!schedulerId || button.disabled) return;
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "投递中";
  try {
    const response = await fetch(`/api/market-observability/collection-tasks/${encodeURIComponent(schedulerId)}/trigger`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(await response.text());
    button.textContent = "已投递";
    showMessage("采集任务已立即投递");
    setTimeout(() => loadDashboard(false), 1500);
  } catch (error) {
    button.textContent = original;
    button.disabled = false;
    showMessage(`任务投递失败：${error.message}`);
  }
}

function persistCollapsedTaskGroups() {
  localStorage.setItem("market-observability-collapsed-task-groups-v2", JSON.stringify([...state.collapsedTaskGroups]));
}

function populateTaskFilter(id, values) {
  const select = document.querySelector(`#${id}`);
  const current = select.value;
  const first = select.options[0].outerHTML;
  select.innerHTML = first + [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"))
    .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  select.value = current;
}

function renderWatchlist() {
  const rows = state.dashboard?.watchlist || [];
  const query = document.querySelector("#watchlist-search").value.trim().toLowerCase();
  const filtered = rows.filter((row) => !query || [row.code, row.name, row.type, row.priority]
    .some((value) => String(value || "").toLowerCase().includes(query)));
  setText("watchlist-count", `${filtered.length} 个标的`);
  const body = document.querySelector("#watchlist-table-body");
  body.innerHTML = filtered.length
    ? filtered.map((row) => {
      const latestTypes = (row.latest || []).map((item) => item.data_type).join("、");
      return `
        <tr>
          <td title="${escapeHtml(row.code)}"><strong>${escapeHtml(row.name || row.code)}</strong><br><small>${escapeHtml(row.code)}</small></td>
          <td>${escapeHtml(row.type || "—")}</td>
          <td><span class="status-badge ${priorityClass(row.priority)}">${escapeHtml(priorityLabel(row.priority))}</span></td>
          <td>${formatInteger(row.realtime_interval_seconds)} 秒</td>
          <td title="${escapeHtml(latestTypes)}">${escapeHtml(latestTypes || "—")}</td>
          <td>${escapeHtml(formatDateTime(row.last_success_at))}</td>
          <td><span class="status-badge ${row.enabled ? "enabled" : "disabled"}">${row.enabled ? "启用" : "停用"}</span>${row.last_error ? ` <span class="negative" title="${escapeHtml(row.last_error)}">异常</span>` : ""}</td>
        </tr>`;
    }).join("")
    : tableEmpty(7, "尚未配置跟踪标的");
}

async function loadInventory(showToast = false) {
  try {
    const response = await fetch("/api/market-observability/inventory", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    state.inventory = await response.json();
    renderAssetInventory();
    renderAssetDomainOptions();
    renderAssetGroupOptions();
    await loadAssetRecords();
    if (showToast) showMessage("数据资产已刷新");
  } catch (error) {
    showMessage(`读取数据资产失败：${error.message}`);
  }
}

function renderAssetInventory() {
  const inventory = state.inventory || {};
  const domains = inventory.domains || [];
  setText("asset-domain-count", formatInteger(inventory.domain_count));
  setText("asset-available-count", formatInteger(inventory.available_count));
  setText("asset-total-records", formatInteger(inventory.total_records));
  const container = document.querySelector("#asset-inventory");
  container.innerHTML = domains.map((domain) => `
    <button class="asset-domain ${domain.available ? "" : "unavailable"}" type="button" data-asset-domain="${escapeHtml(domain.domain)}" ${domain.available ? "" : "disabled"}>
      <span>${escapeHtml(domain.title)}</span>
      <strong>${formatInteger(domain.total)}</strong>
      <small>${domain.available ? escapeHtml(formatDateTime(domain.latest_at)) : escapeHtml(domain.error || "不可用")}</small>
    </button>
  `).join("");
  container.querySelectorAll("[data-asset-domain]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("#asset-domain-filter").value = button.dataset.assetDomain;
      state.assetOffset = 0;
      renderAssetGroupOptions();
      loadAssetRecords();
    });
  });
}

function renderAssetDomainOptions() {
  const select = document.querySelector("#asset-domain-filter");
  const domains = (state.inventory?.domains || []).filter((item) => item.available);
  const selected = select.value || "market_snapshot";
  select.innerHTML = domains.map((domain) => (
    `<option value="${escapeHtml(domain.domain)}">${escapeHtml(domain.title)} · ${formatInteger(domain.total)}</option>`
  )).join("");
  select.value = domains.some((item) => item.domain === selected)
    ? selected
    : (domains[0]?.domain || "");
}

function renderAssetGroupOptions() {
  const domain = document.querySelector("#asset-domain-filter").value;
  const inventory = (state.inventory?.domains || []).find((item) => item.domain === domain);
  const select = document.querySelector("#asset-group-filter");
  select.innerHTML = `<option value="">全部分类</option>${(inventory?.groups || []).map((group) => (
    `<option value="${escapeHtml(group.name)}">${escapeHtml(group.name)} · ${formatInteger(group.count)}</option>`
  )).join("")}`;
}

async function loadAssetRecords() {
  const domain = document.querySelector("#asset-domain-filter").value;
  if (!domain) return;
  const params = new URLSearchParams({
    domain,
    limit: String(state.assetLimit),
    offset: String(state.assetOffset),
  });
  const group = document.querySelector("#asset-group-filter").value;
  const query = document.querySelector("#asset-search").value.trim();
  if (group) params.set("group", group);
  if (query) params.set("query", query);
  try {
    const response = await fetch(`/api/market-observability/records?${params}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    state.assetRecords = await response.json();
    renderAssetRecords();
  } catch (error) {
    showMessage(`读取采集记录失败：${error.message}`);
  }
}

function renderAssetRecords() {
  const payload = state.assetRecords || {};
  const rows = payload.items || [];
  const total = Number(payload.total || 0);
  setText("asset-record-count", `${formatInteger(total)} 条`);
  const body = document.querySelector("#asset-table-body");
  body.innerHTML = rows.length
    ? rows.map((row, index) => `
      <tr>
        <td>${escapeHtml(assetRecordGroup(row))}</td>
        <td title="${escapeHtml(assetRecordIdentity(row))}">${escapeHtml(assetRecordIdentity(row))}</td>
        <td>${escapeHtml(formatDateTime(assetRecordTime(row)))}</td>
        <td title="${escapeHtml(assetRecordSummary(row))}">${escapeHtml(assetRecordSummary(row))}</td>
        <td><button class="data-button" type="button" data-asset-record-index="${index}">查看</button></td>
      </tr>`).join("")
    : tableEmpty(5, payload.available === false ? payload.error : "没有匹配的采集记录");
  body.querySelectorAll("[data-asset-record-index]").forEach((button) => {
    button.addEventListener("click", () => {
      const row = rows[Number(button.dataset.assetRecordIndex)];
      openRecord("COLLECTION DATA", assetRecordIdentity(row), row);
    });
  });
  const start = total ? state.assetOffset + 1 : 0;
  const end = Math.min(total, state.assetOffset + rows.length);
  setText("asset-page-state", `${formatInteger(start)}–${formatInteger(end)} / ${formatInteger(total)}`);
  document.querySelector("#asset-prev").disabled = state.assetOffset <= 0;
  document.querySelector("#asset-next").disabled = state.assetOffset + rows.length >= total;
}

function assetRecordGroup(row) {
  return String(
    row.data_type || row.source || row.indicator || row.regime
    || row.aggregator || row.exchange || row.task_name || row.status || "未分类"
  );
}

function assetRecordIdentity(row) {
  return String(
    row.title || row.name || row.code || row.subject_id || row.source_name
    || row.indicator || row.snapshot_date || row.id || "记录"
  );
}

function assetRecordTime(row) {
  return row.published_at || row.bucket_at || row.trade_date || row.snapshot_date
    || row.updated_at || row.started_at || row.created_at || row.fetched_at;
}

function assetRecordSummary(row) {
  if (row.summary) return String(row.summary);
  if (row.error_message) return String(row.error_message);
  const data = row.data;
  if (!data || typeof data !== "object") {
    return row.value !== undefined
      ? `${formatNumber(row.value, 4)} ${row.unit || ""}`.trim()
      : "—";
  }
  const preferred = ["price", "latest", "close", "change_pct", "main_net_inflow", "nav", "status"];
  const entries = preferred
    .filter((key) => data[key] !== undefined && data[key] !== null)
    .map((key) => `${key}=${String(data[key])}`);
  return entries.length ? entries.join(" · ") : JSON.stringify(data).slice(0, 160);
}

function openRecord(kicker, title, record) {
  elements.dialogKicker.textContent = kicker;
  elements.dialogTitle.textContent = title;
  elements.dialogContent.textContent = JSON.stringify(record, null, 2);
  elements.dialog.showModal();
}

function configureAutoRefresh() {
  if (state.refreshTimer) window.clearInterval(state.refreshTimer);
  if (state.anomalyRefreshTimer) window.clearInterval(state.anomalyRefreshTimer);
  if (state.goldRealtimeTimer) window.clearInterval(state.goldRealtimeTimer);
  state.refreshTimer = null;
  state.anomalyRefreshTimer = null;
  state.goldRealtimeTimer = null;
  if (elements.autoRefresh.checked) {
    state.refreshTimer = window.setInterval(() => {
      if (document.hidden) return;
      if (state.activeView === "sectors") {
        loadSectorOverview(false);
      } else if (state.activeView === "us") {
        loadUsMarket(false);
      } else if (state.activeView === "gold") {
        loadGoldMarket(false);
      } else if (state.activeView === "etf") {
        loadEtfMarket(false);
      } else if (state.activeView === "futures") {
        loadFuturesMarket(false);
      } else {
        loadDashboard(false);
      }
    }, state.activeView === "etf" ? 10000 : 30000);
    state.anomalyRefreshTimer = window.setInterval(loadRealtimeMarketAnomaly, 5000);
    state.goldRealtimeTimer = window.setInterval(loadGoldRealtime, 5000);
  }
}

function setConnection(status, label) {
  elements.connection.className = `connection-state ${status === "ready" ? "" : status}`;
  elements.connection.querySelector("span").textContent = label;
}

function showMessage(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  window.clearTimeout(showMessage.timer);
  showMessage.timer = window.setTimeout(() => elements.toast.classList.remove("visible"), 2600);
}

function setText(id, value) {
  const element = document.querySelector(`#${id}`);
  if (element) element.textContent = value ?? "—";
}

function formatInteger(value) {
  const parsed = number(value);
  return parsed === null ? "—" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(parsed);
}

function formatNumber(value, digits = 2) {
  const parsed = number(value);
  return parsed === null
    ? "—"
    : new Intl.NumberFormat("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(parsed);
}

function formatMoney(value) {
  const parsed = number(value);
  if (parsed === null) return "—";
  const absolute = Math.abs(parsed);
  if (absolute >= 1e12) return `${formatNumber(parsed / 1e12, 2)} 万亿`;
  if (absolute >= 1e8) return `${formatNumber(parsed / 1e8, 2)} 亿`;
  if (absolute >= 1e4) return `${formatNumber(parsed / 1e4, 2)} 万`;
  return formatNumber(parsed, 0);
}

function signedPercent(value, digits = 2) {
  const parsed = number(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : ""}${formatNumber(parsed, digits)}%`;
}

function signedNumber(value, digits = 2) {
  const parsed = number(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : ""}${formatNumber(parsed, digits)}`;
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function formatClock(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function relativeTime(value) {
  if (!value) return "无有效快照";
  const elapsed = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(elapsed)) return "时间未知";
  const seconds = Math.max(0, Math.round(elapsed / 1000));
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} 小时前` : `${Math.round(hours / 24)} 天前`;
}

function formatDuration(start, finish) {
  if (!start || !finish) return finish ? "—" : "运行中";
  const seconds = Math.max(0, (new Date(finish) - new Date(start)) / 1000);
  return seconds < 60 ? `${seconds.toFixed(1)} 秒` : `${(seconds / 60).toFixed(1)} 分钟`;
}

function freshnessLabel(status) {
  return {
    realtime: "实时",
    delayed: "延时",
    fetch_time: "仅采集时间",
    unknown: "时间未知",
  }[status] || status || "未知";
}

function statusLabel(status) {
  return {
    success: "成功",
    delayed: "延迟",
    partial_success: "部分成功",
    failed: "失败",
    skipped: "跳过",
    running: "运行中",
    pending: "等待首次执行",
  }[status] || status || "未知";
}

function priorityLabel(priority) {
  return {
    critical: "关键",
    standard: "标准",
    low: "低频",
  }[priority] || priority || "标准";
}

function priorityClass(priority) {
  return {
    critical: "failed",
    standard: "success",
    low: "neutral",
  }[priority] || "neutral";
}

function displayTaskName(value) {
  return String(value || "")
    .replace(/^collect_/, "")
    .replaceAll("_", " ");
}

function marketClass(value) {
  const parsed = number(value);
  if (parsed === null || parsed === 0) return "";
  return parsed > 0 ? "positive" : "negative";
}

function firstNumber(data, keys) {
  for (const key of keys) {
    const parsed = number(data?.[key]);
    if (parsed !== null) return parsed;
  }
  return null;
}

function number(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function financialAmountNumber(value) {
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value.replaceAll(",", ""));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return number(value);
}

function dateValue(value) {
  if (!value) return 0;
  const date = new Date(value).getTime();
  return Number.isFinite(date) ? date : 0;
}

function emptyBlock(message) {
  return `<div class="empty-row">${escapeHtml(message)}</div>`;
}

function tableEmpty(columns, message) {
  return `<tr><td colspan="${columns}" class="empty-row">${escapeHtml(message)}</td></tr>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function debounce(fn, wait) {
  let timer;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
}
