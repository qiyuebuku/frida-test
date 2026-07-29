(() => {
  "use strict";

  const RELATION_STYLES = {
    causal_influence: { label: "因果影响", color: "#b9414b" },
    temporal_progression: { label: "后续进展", color: "#2563a7" },
    confirmation: { label: "相互印证", color: "#157a5b" },
    contradiction: { label: "事实冲突", color: "#8e3c8f" },
    common_driver: { label: "共同驱动", color: "#a76812" },
    constraint: { label: "约束条件", color: "#59666f" },
    market_co_movement: { label: "市场联动", color: "#147f83" },
    same_fact: { label: "同一事实", color: "#4d7c6b" },
    same_event: { label: "同一事件", color: "#7153a6" },
  };

  const state = {
    overview: null,
    community: null,
    cy: null,
    viewMode: "overview",
    selectedCommunityId: "",
    selectedElement: null,
    activeTab: "selection",
    target: "prod",
    adapterName: "financial",
    layout: "cose",
    showObserved: true,
    showInferred: true,
    chunkScope: "all",
    neighborDepth: "all",
    nodeSearch: "",
    requestVersion: 0,
  };

  const refs = {};
  let searchFocusTimer = null;

  document.addEventListener("DOMContentLoaded", initialize);

  function initialize() {
    [
      "connection-dot", "connection-label", "header-subtitle",
      "target-select", "adapter-input", "refresh-button",
      "overview-view-button", "detail-view-button", "chunk-scope-control",
      "node-search", "show-observed", "show-inferred", "chunk-scope", "layout-select",
      "neighbor-depth", "fit-button", "reset-button", "cy", "graph-empty",
      "graph-summary", "relation-legend", "graph-tooltip", "detail-content", "toast",
    ].forEach((id) => {
      refs[toCamel(id)] = document.getElementById(id);
    });

    const params = new URLSearchParams(window.location.search);
    state.target = params.get("target") || "prod";
    state.adapterName = params.get("adapter") || "financial";
    state.selectedCommunityId = params.get("community") || "";
    state.viewMode = (
      params.get("view") === "cards" && state.selectedCommunityId
    ) ? "detail" : "overview";
    refs.targetSelect.value = state.target;
    refs.adapterInput.value = state.adapterName;

    bindEvents();
    loadCommunities();
  }

  function bindEvents() {
    refs.targetSelect.addEventListener("change", () => {
      state.target = refs.targetSelect.value;
      state.selectedCommunityId = "";
      state.viewMode = "overview";
      loadCommunities();
    });
    refs.adapterInput.addEventListener("change", () => {
      state.adapterName = refs.adapterInput.value.trim() || "financial";
      refs.adapterInput.value = state.adapterName;
      state.selectedCommunityId = "";
      state.viewMode = "overview";
      loadCommunities();
    });
    refs.refreshButton.addEventListener("click", loadCommunities);
    refs.overviewViewButton.addEventListener("click", showOverview);
    refs.detailViewButton.addEventListener("click", () => {
      if (state.selectedCommunityId) selectCommunity(state.selectedCommunityId);
    });
    refs.nodeSearch.addEventListener("input", () => {
      state.nodeSearch = refs.nodeSearch.value.trim().toLowerCase();
      applyGraphFilters();
      window.clearTimeout(searchFocusTimer);
      if (state.viewMode === "overview" && state.nodeSearch) {
        searchFocusTimer = window.setTimeout(focusFirstCommunityMatch, 180);
      }
    });
    refs.showObserved.addEventListener("change", () => {
      state.showObserved = refs.showObserved.checked;
      applyGraphFilters();
    });
    refs.showInferred.addEventListener("change", () => {
      state.showInferred = refs.showInferred.checked;
      applyGraphFilters();
    });
    refs.chunkScope.addEventListener("change", () => {
      state.chunkScope = refs.chunkScope.value;
      applyGraphFilters();
    });
    refs.layoutSelect.addEventListener("change", () => {
      state.layout = refs.layoutSelect.value;
      runLayout();
    });
    refs.neighborDepth.addEventListener("change", () => {
      state.neighborDepth = refs.neighborDepth.value;
      applyGraphFilters();
    });
    refs.fitButton.addEventListener("click", () => {
      if (state.cy) state.cy.fit(state.cy.elements(":visible"), 56);
    });
    refs.resetButton.addEventListener("click", resetGraphView);

    document.querySelectorAll(".detail-tab").forEach((button) => {
      button.addEventListener("click", () => {
        state.activeTab = button.dataset.tab;
        document.querySelectorAll(".detail-tab").forEach((tab) => {
          tab.classList.toggle("active", tab === button);
        });
        renderDetailPanel();
      });
    });
  }

  async function loadCommunities() {
    setConnection("loading", "正在读取 Community");
    if (state.viewMode === "detail" && state.selectedCommunityId) {
      await selectCommunity(state.selectedCommunityId);
      return;
    }
    await showOverview();
  }

  async function selectCommunity(communityId) {
    state.viewMode = "detail";
    state.selectedCommunityId = communityId;
    state.selectedElement = null;
    state.activeTab = "selection";
    syncActiveTab();
    syncViewControls();
    updateUrl();
    refs.detailContent.replaceChildren(
      createStatusBlock("loading-list", "正在加载 Community 子图"),
    );

    const version = ++state.requestVersion;
    try {
      const params = new URLSearchParams({ target: state.target });
      const response = await fetch(
        `/api/kg/graph-communities/${encodeURIComponent(communityId)}?${params.toString()}`,
      );
      if (!response.ok) throw await responseError(response);
      const payload = await response.json();
      if (version !== state.requestVersion) return;
      state.community = payload;
      refs.headerSubtitle.textContent =
        payload.community.title || payload.community.community_id;
      setConnection("ready", `已连接 ${state.target} · ${state.adapterName}`);
      syncViewControls();
      createGraph(payload);
      renderGraphMeta();
      renderDetailPanel();
    } catch (error) {
      if (version !== state.requestVersion) return;
      state.community = null;
      destroyGraph();
      refs.detailContent.replaceChildren(
        createStatusBlock("error-block", error.message || String(error)),
      );
      showToast(error.message || String(error));
    }
  }

  async function showOverview() {
    state.viewMode = "overview";
    state.community = null;
    state.selectedElement = null;
    state.activeTab = "selection";
    syncActiveTab();
    syncViewControls();
    updateUrl();
    refs.detailContent.replaceChildren(
      createStatusBlock("loading-list", "正在加载 Community 关系概览"),
    );

    const version = ++state.requestVersion;
    const params = new URLSearchParams({
      target: state.target,
      adapter_name: state.adapterName,
      graph_status: "active",
      sort_by: "relation_count",
      sort_order: "desc",
      limit: "0",
      offset: "0",
    });
    try {
      const response = await fetch(
        `/api/kg/graph-community-overview?${params.toString()}`,
      );
      if (!response.ok) throw await responseError(response);
      const payload = await response.json();
      if (version !== state.requestVersion) return;
      state.overview = payload;
      refs.headerSubtitle.textContent = "全局 Community 关系网络";
      setConnection("ready", `已连接 ${state.target} · ${state.adapterName}`);
      createOverviewGraph(payload);
      restoreOverviewSelection();
      renderGraphMeta();
      renderDetailPanel();
      updateUrl();
    } catch (error) {
      if (version !== state.requestVersion) return;
      state.overview = null;
      destroyGraph();
      renderGraphMeta();
      refs.detailContent.replaceChildren(
        createStatusBlock("error-block", error.message || String(error)),
      );
      showToast(error.message || String(error));
    }
  }

  function syncViewControls() {
    const overview = state.viewMode === "overview";
    refs.overviewViewButton.classList.toggle("active", overview);
    refs.detailViewButton.classList.toggle("active", !overview);
    refs.detailViewButton.disabled = !state.selectedCommunityId;
    refs.chunkScopeControl.classList.toggle("is-hidden", overview);
    refs.nodeSearch.placeholder = overview ? "查找社区" : "查找 Card";
    document.querySelectorAll(".detail-tab").forEach((tab) => {
      tab.disabled = overview && tab.dataset.tab !== "selection";
    });
  }

  function createOverviewGraph(payload) {
    destroyGraph();
    const nodeIds = new Set(
      (payload.nodes || []).map((node) => node.community_id),
    );
    const elements = [
      ...(payload.nodes || []).map((node) => ({
        group: "nodes",
        data: {
          id: node.community_id,
          label: [
            shortText(overviewCommunityTitle(node), 22),
            `${node.community_relation_count} 条跨社区关系`,
          ].join("\n"),
          searchText: [
            node.title,
            node.representative_summary,
            node.community_id,
          ].filter(Boolean).join(" ").toLowerCase(),
          size: overviewNodeSize(node.community_relation_count),
          cardCount: node.card_count,
          edgeCount: node.edge_count,
          relationCount: node.community_relation_count,
          isConnected: node.community_relation_count > 0 ? 1 : 0,
          raw: node,
        },
      })),
      ...(payload.edges || [])
        .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
        .map((edge) => {
          const style = RELATION_STYLES[edge.relation_kind] || {
            label: edge.relation_kind,
            color: "#65716b",
          };
          return {
            group: "edges",
            data: {
              id: edge.relation_id,
              source: edge.source,
              target: edge.target,
              relationKind: edge.relation_kind,
              relationLabel: style.label,
              relationColor: style.color,
              observedCount: edge.observed_edge_count,
              inferredCount: edge.inferred_edge_count,
              width: Math.min(7, 2 + Math.log2(edge.supporting_edge_count + 1)),
              raw: edge,
            },
          };
        }),
    ];

    state.cy = cytoscape({
      container: refs.cy,
      elements,
      minZoom: 0.18,
      maxZoom: 2.4,
      wheelSensitivity: 0.22,
      selectionType: "single",
      boxSelectionEnabled: false,
      style: [
        {
          selector: "node",
          style: {
            width: "data(size)",
            height: "data(size)",
            "background-color": "#ffffff",
            "border-color": "#157a5b",
            "border-width": 3,
            label: "data(label)",
            color: "#18211d",
            "font-size": 9,
            "font-family": "Inter, Noto Sans SC, Microsoft YaHei, sans-serif",
            "text-wrap": "wrap",
            "text-max-width": 122,
            "text-valign": "bottom",
            "text-margin-y": 8,
            "overlay-opacity": 0,
          },
        },
        {
          selector: "node[isConnected = 0]",
          style: {
            "background-color": "#dfe5e2",
            "border-color": "#aeb9b3",
            "border-width": 1,
            label: "",
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-color": "#2563a7",
            "border-width": 5,
            "background-color": "#e1ebf7",
            label: "data(label)",
            "z-index": 20,
          },
        },
        {
          selector: "edge",
          style: {
            width: "data(width)",
            "line-color": "data(relationColor)",
            "target-arrow-color": "data(relationColor)",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.9,
            "curve-style": "bezier",
            "control-point-step-size": 42,
            opacity: 0.76,
            label: "",
            "font-size": 8,
            color: "#4f5b55",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.9,
            "text-background-padding": 2,
            "text-rotation": "autorotate",
            "overlay-opacity": 0,
          },
        },
        {
          selector: "edge:selected, edge.edge-hover",
          style: {
            opacity: 1,
            label: "data(relationLabel)",
            "overlay-opacity": 0.06,
            "overlay-color": "#18211d",
          },
        },
        {
          selector: ".dimmed",
          style: { opacity: 0.1 },
        },
        {
          selector: ".hidden-by-filter",
          style: { display: "none" },
        },
      ],
    });

    state.cy.on("tap", "node", (event) => {
      selectOverviewCommunity(event.target);
    });
    state.cy.on("tap", "edge", (event) => {
      loadCommunityRelation(event.target.id());
    });
    state.cy.on("mouseover", "edge", (event) => {
      event.target.addClass("edge-hover");
    });
    state.cy.on("mouseout", "edge", (event) => {
      event.target.removeClass("edge-hover");
    });
    state.cy.on("mouseover", "node", (event) => {
      showOverviewTooltip(event.target, event.renderedPosition);
    });
    state.cy.on("mousemove", "node", (event) => {
      moveGraphTooltip(event.renderedPosition);
    });
    state.cy.on("mouseout", "node", hideGraphTooltip);
    state.cy.on("tap", (event) => {
      if (event.target === state.cy) {
        state.selectedCommunityId = "";
        state.selectedElement = null;
        state.cy.elements().unselect();
        applyGraphFilters();
        renderDetailPanel();
        updateUrl();
      }
    });
    runLayout();
    applyGraphFilters();
  }

  function selectOverviewCommunity(nodeOrId, { center = false } = {}) {
    if (!state.cy || state.viewMode !== "overview") return;
    const node = typeof nodeOrId === "string"
      ? state.cy.getElementById(nodeOrId)
      : nodeOrId;
    if (!node || !node.length) return;

    state.cy.elements().unselect();
    node.select();
    state.selectedCommunityId = node.id();
    state.selectedElement = {
      type: "community",
      data: node.data("raw"),
    };
    state.activeTab = "selection";
    syncActiveTab();
    syncViewControls();
    applyGraphFilters();
    renderDetailPanel();
    updateUrl();

    if (center) {
      state.cy.animate(
        {
          center: { eles: node },
          zoom: Math.max(state.cy.zoom(), 0.9),
        },
        { duration: 260 },
      );
    }
  }

  function restoreOverviewSelection() {
    if (!state.selectedCommunityId || !state.cy) return;
    const node = state.cy.getElementById(state.selectedCommunityId);
    if (!node.length) {
      state.selectedCommunityId = "";
      return;
    }
    selectOverviewCommunity(node);
  }

  function focusFirstCommunityMatch() {
    if (!state.cy || state.viewMode !== "overview" || !state.nodeSearch) return;
    const match = state.cy.nodes().filter(
      (node) => node.data("searchText").includes(state.nodeSearch),
    ).first();
    if (match.length) selectOverviewCommunity(match, { center: true });
  }

  function overviewCommunityTitle(node) {
    const title = String(node.title || "").trim();
    if (title && title !== "未命名关系社区") return title;
    const summary = String(node.representative_summary || "").trim();
    if (summary) return summary;
    const identity = String(node.community_id || "").trim();
    return identity
      ? `Community ${identity.slice(-6)}`
      : "未命名 Community";
  }

  function overviewNodeSize(relationCount) {
    const count = Math.max(0, Number(relationCount) || 0);
    if (count === 0) return 14;
    return Math.min(108, 30 + Math.sqrt(count) * 17);
  }

  function showOverviewTooltip(node, position) {
    if (!refs.graphTooltip || state.viewMode !== "overview") return;
    const item = node.data("raw") || {};
    refs.graphTooltip.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = overviewCommunityTitle(item);
    const metrics = document.createElement("span");
    metrics.textContent = [
      `${item.card_count || 0} Card`,
      `${item.edge_count || 0} 内部 Edge`,
      `${item.community_relation_count || 0} 跨社区关系`,
    ].join(" · ");
    refs.graphTooltip.append(title, metrics);
    refs.graphTooltip.classList.add("visible");
    moveGraphTooltip(position);
  }

  function moveGraphTooltip(position) {
    if (!refs.graphTooltip || !position) return;
    const width = refs.graphTooltip.offsetWidth || 280;
    const height = refs.graphTooltip.offsetHeight || 58;
    const left = Math.max(
      8,
      Math.min(position.x + 14, refs.cy.clientWidth - width - 8),
    );
    const top = Math.max(
      8,
      Math.min(position.y + 14, refs.cy.clientHeight - height - 8),
    );
    refs.graphTooltip.style.left = `${left}px`;
    refs.graphTooltip.style.top = `${top}px`;
  }

  function hideGraphTooltip() {
    if (refs.graphTooltip) refs.graphTooltip.classList.remove("visible");
  }

  async function loadCommunityRelation(relationId) {
    state.activeTab = "selection";
    syncActiveTab();
    refs.detailContent.replaceChildren(
      createStatusBlock("loading-list", "正在加载跨 Community 关系证据"),
    );
    try {
      const params = new URLSearchParams({
        target: state.target,
        adapter_name: state.adapterName,
      });
      const response = await fetch(
        `/api/kg/graph-community-relations/${encodeURIComponent(relationId)}?${params.toString()}`,
      );
      if (!response.ok) throw await responseError(response);
      state.selectedElement = {
        type: "community-relation",
        data: await response.json(),
      };
      applyGraphFilters();
      renderDetailPanel();
    } catch (error) {
      refs.detailContent.replaceChildren(
        createStatusBlock("error-block", error.message || String(error)),
      );
      showToast(error.message || String(error));
    }
  }

  function createGraph(payload) {
    destroyGraph();
    const nodeIds = new Set(
      (payload.nodes || []).map((node) => node.card_id),
    );
    const elements = [
      ...(payload.nodes || []).map((node) => ({
        group: "nodes",
        data: {
          id: node.card_id,
          label: shortText(
            node.summary || `Card ${node.card_id.slice(-8)}`,
            22,
          ),
          searchText: `${node.summary || ""} ${node.source_id || ""} ${node.card_id}`.toLowerCase(),
          degree: node.degree,
          size: Math.min(74, 36 + node.degree * 5),
          isCore: node.is_core ? 1 : 0,
          raw: node,
        },
      })),
      ...(payload.edges || [])
        .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
        .map((edge) => {
          const style = RELATION_STYLES[edge.relation_kind] || {
            label: edge.relation_kind,
            color: "#65716b",
          };
          return {
            group: "edges",
            data: {
              id: edge.edge_id,
              source: edge.source,
              target: edge.target,
              relationKind: edge.relation_kind,
              relationLabel: style.label,
              relationColor: style.color,
              decisionClass: edge.decision_class,
              crossChunk: edge.cross_chunk ? 1 : 0,
              raw: edge,
            },
          };
        }),
    ];

    state.cy = cytoscape({
      container: refs.cy,
      elements,
      minZoom: 0.22,
      maxZoom: 2.6,
      wheelSensitivity: 0.22,
      selectionType: "single",
      boxSelectionEnabled: false,
      style: [
        {
          selector: "node",
          style: {
            width: "data(size)",
            height: "data(size)",
            "background-color": "#ffffff",
            "border-color": "#738079",
            "border-width": 2,
            label: "data(label)",
            color: "#18211d",
            "font-size": 8,
            "font-family": "Inter, Noto Sans SC, Microsoft YaHei, sans-serif",
            "text-wrap": "wrap",
            "text-max-width": 105,
            "text-valign": "bottom",
            "text-margin-y": 7,
            "overlay-opacity": 0,
          },
        },
        {
          selector: "node[isCore = 1]",
          style: {
            shape: "diamond",
            "background-color": "#dcefe7",
            "border-color": "#157a5b",
            "border-width": 3,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-color": "#2563a7",
            "border-width": 4,
            "background-color": "#e1ebf7",
          },
        },
        {
          selector: "edge",
          style: {
            width: 2.2,
            "line-color": "data(relationColor)",
            "target-arrow-color": "data(relationColor)",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.8,
            "curve-style": "bezier",
            "control-point-step-size": 36,
            opacity: 0.78,
            label: "",
            "font-size": 7,
            color: "#4f5b55",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.88,
            "text-background-padding": 2,
            "text-rotation": "autorotate",
            "overlay-opacity": 0,
          },
        },
        {
          selector: "edge[decisionClass = 'inferred']",
          style: {
            "line-style": "dashed",
            opacity: 0.58,
          },
        },
        {
          selector: "edge:selected",
          style: {
            width: 4,
            opacity: 1,
            label: "data(relationLabel)",
            "overlay-opacity": 0.08,
            "overlay-color": "#18211d",
          },
        },
        {
          selector: "edge.edge-hover",
          style: {
            width: 3.2,
            opacity: 1,
            label: "data(relationLabel)",
          },
        },
        {
          selector: ".dimmed",
          style: { opacity: 0.1 },
        },
        {
          selector: ".hidden-by-filter",
          style: { display: "none" },
        },
      ],
    });

    state.cy.on("tap", "node", (event) => {
      state.selectedElement = { type: "node", data: event.target.data("raw") };
      state.activeTab = "selection";
      syncActiveTab();
      applyGraphFilters();
      renderDetailPanel();
    });
    state.cy.on("tap", "edge", (event) => {
      state.selectedElement = { type: "edge", data: event.target.data("raw") };
      state.activeTab = "selection";
      syncActiveTab();
      applyGraphFilters();
      renderDetailPanel();
    });
    state.cy.on("mouseover", "edge", (event) => {
      event.target.addClass("edge-hover");
    });
    state.cy.on("mouseout", "edge", (event) => {
      event.target.removeClass("edge-hover");
    });
    state.cy.on("tap", (event) => {
      if (event.target === state.cy) {
        state.selectedElement = null;
        applyGraphFilters();
        renderDetailPanel();
      }
    });
    runLayout();
    applyGraphFilters();
  }

  function runLayout() {
    if (!state.cy) return;
    if (state.viewMode === "overview" && state.layout === "cose") {
      runOverviewRelationshipLayout();
      return;
    }
    const common = {
      animate: true,
      animationDuration: 320,
      fit: true,
      padding: 62,
    };
    const options = {
      cose: {
        ...common,
        name: "cose",
        randomize: true,
        nodeRepulsion: () => 15000,
        idealEdgeLength: () => 135,
        edgeElasticity: () => 90,
        gravity: 0.18,
        numIter: 900,
        nodeDimensionsIncludeLabels: true,
      },
      breadthfirst: {
        ...common,
        name: "breadthfirst",
        directed: true,
        spacingFactor: 1.35,
        roots: state.community?.community?.core_card_id
          ? `#${escapeSelector(state.community.community.core_card_id)}`
          : undefined,
      },
      circle: { ...common, name: "circle", spacingFactor: 1.2 },
      grid: { ...common, name: "grid", avoidOverlap: true, spacingFactor: 1.2 },
    };
    const selectedLayout = options[state.layout] || options.cose;
    state.cy.layout(selectedLayout).run();
  }

  function runOverviewRelationshipLayout() {
    const cy = state.cy;
    if (!cy) return;
    const connectedNodes = cy.nodes("[isConnected = 1]");
    const isolatedNodes = cy.nodes("[isConnected = 0]");
    if (!connectedNodes.length) {
      cy.layout({
        name: "grid",
        animate: false,
        fit: true,
        padding: 56,
        avoidOverlap: true,
      }).run();
      return;
    }

    const width = Math.max(760, cy.width());
    const height = Math.max(560, cy.height());
    const coreWidth = Math.min(760, width * 0.62);
    const coreHeight = Math.min(520, height * 0.62);
    const coreElements = connectedNodes.union(connectedNodes.connectedEdges());
    const layout = coreElements.layout({
      name: "cose",
      animate: false,
      fit: false,
      randomize: true,
      boundingBox: {
        x1: (width - coreWidth) / 2,
        y1: (height - coreHeight) / 2,
        w: coreWidth,
        h: coreHeight,
      },
      nodeRepulsion: () => 22000,
      idealEdgeLength: () => 150,
      edgeElasticity: () => 80,
      gravity: 0.22,
      numIter: 1200,
      nodeDimensionsIncludeLabels: true,
    });
    layout.one("layoutstop", () => {
      positionIsolatedCommunities(isolatedNodes, width, height);
      cy.fit(cy.elements(":visible"), 48);
    });
    layout.run();
  }

  function positionIsolatedCommunities(nodes, width, height) {
    const total = nodes.length;
    if (!total) return;
    const centerX = width / 2;
    const centerY = height / 2;
    const perRing = Math.max(72, Math.ceil(total / 4));
    nodes.forEach((node, index) => {
      const ring = Math.floor(index / perRing);
      const ringStart = ring * perRing;
      const ringSize = Math.min(perRing, total - ringStart);
      const angle = ((index - ringStart) / ringSize) * Math.PI * 2
        - Math.PI / 2;
      const radiusX = width * 0.39 + ring * 22;
      const radiusY = height * 0.38 + ring * 18;
      node.position({
        x: centerX + Math.cos(angle) * radiusX,
        y: centerY + Math.sin(angle) * radiusY,
      });
    });
  }

  function applyGraphFilters() {
    if (!state.cy) return;
    const cy = state.cy;
    cy.batch(() => {
      cy.elements().removeClass("hidden-by-filter dimmed");

      cy.edges().forEach((edge) => {
        const decisionVisible = state.viewMode === "overview"
          ? (
            (edge.data("observedCount") > 0 && state.showObserved)
            || (edge.data("inferredCount") > 0 && state.showInferred)
          )
          : (
            (edge.data("decisionClass") === "observed" && state.showObserved)
            || (edge.data("decisionClass") === "inferred" && state.showInferred)
          );
        const scopeVisible =
          state.viewMode === "overview"
          || state.chunkScope === "all"
          || (state.chunkScope === "cross" && edge.data("crossChunk") === 1)
          || (state.chunkScope === "intra" && edge.data("crossChunk") === 0);
        if (!decisionVisible || !scopeVisible) edge.addClass("hidden-by-filter");
      });

      cy.nodes().forEach((node) => {
        const hasVisibleEdge = node.connectedEdges().some(
          (edge) => !edge.hasClass("hidden-by-filter"),
        );
        if (
          state.viewMode !== "overview"
          && !hasVisibleEdge
          && cy.edges().length
        ) {
          node.addClass("hidden-by-filter");
        }
      });

      if (
        state.selectedElement?.type === "node"
        && state.neighborDepth !== "all"
      ) {
        const selected = cy.getElementById(state.selectedElement.data.card_id);
        if (selected.length) {
          let visible = selected;
          let frontier = selected;
          const depth = Number(state.neighborDepth);
          for (let index = 0; index < depth; index += 1) {
            const connectedEdges = frontier.connectedEdges().filter(
              (edge) => !edge.hasClass("hidden-by-filter"),
            );
            const nextNodes = connectedEdges.connectedNodes();
            visible = visible.union(connectedEdges).union(nextNodes);
            frontier = nextNodes;
          }
          cy.elements().not(visible).addClass("hidden-by-filter");
        }
      }

      if (state.nodeSearch) {
        const matches = cy.nodes().filter(
          (node) => node.data("searchText").includes(state.nodeSearch)
            && !node.hasClass("hidden-by-filter"),
        );
        if (matches.length) {
          cy.elements().not(matches.union(matches.connectedEdges())).addClass("dimmed");
        }
      }
    });

    const visibleNodes = cy.nodes().filter((node) => !node.hasClass("hidden-by-filter"));
    refs.graphEmpty.classList.toggle("visible", visibleNodes.length === 0);
  }

  function resetGraphView() {
    state.selectedElement = null;
    if (state.viewMode === "overview") state.selectedCommunityId = "";
    state.nodeSearch = "";
    state.neighborDepth = "all";
    refs.nodeSearch.value = "";
    refs.neighborDepth.value = "all";
    if (state.cy) state.cy.elements().unselect();
    applyGraphFilters();
    renderDetailPanel();
    updateUrl();
    if (state.cy) state.cy.fit(state.cy.elements(":visible"), 56);
  }

  function renderGraphMeta() {
    refs.graphSummary.replaceChildren();
    refs.relationLegend.replaceChildren();
    if (state.viewMode === "overview") {
      if (!state.overview) {
        refs.graphEmpty.classList.add("visible");
        return;
      }
      const nodes = state.overview.nodes || [];
      const edges = state.overview.edges || [];
      [
        `${nodes.length} Community`,
        `${edges.length} Community Relation`,
        `${nodes.reduce((sum, item) => sum + item.card_count, 0)} Card`,
        `${edges.reduce((sum, item) => sum + item.supporting_edge_count, 0)} Supporting Edge`,
      ].forEach((text) => {
        const item = document.createElement("span");
        item.textContent = text;
        refs.graphSummary.append(item);
      });
      const counts = {};
      edges.forEach((edge) => {
        counts[edge.relation_kind] = (counts[edge.relation_kind] || 0) + 1;
      });
      renderRelationLegend(counts);
      return;
    }
    if (!state.community) {
      refs.graphEmpty.classList.add("visible");
      return;
    }
    const community = state.community.community;
    [
      `${community.card_count} Card`,
      `${community.edge_count} Edge`,
      `${community.observed_edge_count} Observed`,
      `${community.inferred_edge_count} Inferred`,
      `密度 ${formatDecimal(community.graph_density)}`,
      ...(community.graph_consistent ? [] : ["图状态不一致"]),
    ].forEach((text) => {
      const item = document.createElement("span");
      item.textContent = text;
      refs.graphSummary.append(item);
    });

    renderRelationLegend(community.relation_kind_counts || {});
  }

  function renderRelationLegend(counts) {
    Object.entries(counts).forEach(([kind, count]) => {
      const style = RELATION_STYLES[kind] || { label: kind, color: "#65716b" };
      const item = document.createElement("span");
      item.className = "legend-item";
      const line = document.createElement("span");
      line.className = "legend-line";
      line.style.setProperty("--legend-color", style.color);
      const label = document.createElement("span");
      label.textContent = `${style.label} ${count}`;
      item.append(line, label);
      refs.relationLegend.append(item);
    });
  }

  function renderDetailPanel() {
    refs.detailContent.replaceChildren();
    if (state.viewMode === "overview") {
      if (state.selectedElement?.type === "community-relation") {
        renderCommunityRelationDetail(state.selectedElement.data);
      } else if (state.selectedElement?.type === "community") {
        renderOverviewCommunityDetail(state.selectedElement.data);
      } else {
        renderOverviewDetail();
      }
      return;
    }
    if (!state.community) {
      refs.detailContent.append(
        createStatusBlock("no-results", "当前没有可查看的 Community"),
      );
      return;
    }

    if (state.activeTab === "report") {
      renderReport();
      return;
    }
    if (state.activeTab === "projections") {
      renderProjections();
      return;
    }
    if (state.selectedElement?.type === "node") {
      renderNodeDetail(state.selectedElement.data);
      return;
    }
    if (state.selectedElement?.type === "edge") {
      renderEdgeDetail(state.selectedElement.data);
      return;
    }
    renderCommunityDetail();
  }

  function renderOverviewDetail() {
    const overview = state.overview;
    if (!overview) {
      refs.detailContent.append(
        createStatusBlock("no-results", "当前没有可查看的 Community"),
      );
      return;
    }
    const nodes = overview.nodes || [];
    const edges = overview.edges || [];
    const observed = edges.reduce(
      (sum, edge) => sum + edge.observed_edge_count,
      0,
    );
    const inferred = edges.reduce(
      (sum, edge) => sum + edge.inferred_edge_count,
      0,
    );
    refs.detailContent.append(
      detailHeader(
        "Community Graph",
        "全局 Community 关系网络",
        `${nodes.length} 个 Community · ${edges.length} 条跨社区关系`,
      ),
      metricGrid([
        [nodes.length, "Community"],
        [edges.length, "Community Relation"],
        [observed, "Observed"],
        [inferred, "Inferred"],
        [nodes.reduce((sum, node) => sum + node.card_count, 0), "Card"],
        [edges.reduce((sum, edge) => sum + edge.supporting_edge_count, 0), "Supporting Edge"],
      ]),
      definitionSection("当前范围", [
        ["Adapter", state.adapterName],
        ["Community 总数", overview.total],
        ["已加载 Community", overview.visible_community_count],
        ["孤立 Community", nodes.filter((node) => !node.community_relation_count).length],
      ]),
    );
  }

  function renderOverviewCommunityDetail(item) {
    const overview = state.overview || {};
    const relations = (overview.edges || []).filter(
      (edge) => edge.source === item.community_id || edge.target === item.community_id,
    );
    const observed = relations.reduce(
      (sum, edge) => sum + edge.observed_edge_count,
      0,
    );
    const inferred = relations.reduce(
      (sum, edge) => sum + edge.inferred_edge_count,
      0,
    );
    const action = document.createElement("button");
    action.type = "button";
    action.className = "primary-action";
    action.textContent = "打开该 Community 的 Card 子图";
    action.addEventListener("click", () => selectCommunity(item.community_id));

    refs.detailContent.append(
      detailHeader(
        "Selected Community",
        overviewCommunityTitle(item),
        item.community_id,
      ),
      textSection(
        "代表性事件摘要",
        item.representative_summary || "当前 Community 没有可用摘要",
      ),
      metricGrid([
        [item.card_count, "Card"],
        [item.edge_count, "内部 Edge"],
        [relations.length, "跨社区关系"],
        [observed, "Observed"],
        [inferred, "Inferred"],
        [item.graph_version, "Graph Version"],
      ]),
      action,
      relationKindSection(
        relations.reduce((counts, relation) => {
          counts[relation.relation_kind] =
            (counts[relation.relation_kind] || 0) + 1;
          return counts;
        }, {}),
      ),
      overviewNeighborSection(item.community_id, relations),
      definitionSection("Community 状态", [
        ["事实报告", item.fact_report_status],
        ["条件预测", item.projection_status],
        ["最近更新", formatDate(item.updated_at)],
      ]),
    );
  }

  function overviewNeighborSection(communityId, relations) {
    const section = document.createElement("section");
    section.className = "detail-section";
    const heading = document.createElement("h3");
    heading.textContent = "连接的 Community";
    section.append(heading);
    if (!relations.length) {
      section.append(createStatusBlock("no-results", "暂无跨社区关系"));
      return section;
    }

    const nodesById = new Map(
      (state.overview?.nodes || []).map((node) => [node.community_id, node]),
    );
    const grouped = new Map();
    relations.forEach((relation) => {
      const neighborId = relation.source === communityId
        ? relation.target
        : relation.source;
      const current = grouped.get(neighborId) || {
        neighborId,
        relationKinds: new Set(),
        supportingEdgeCount: 0,
      };
      current.relationKinds.add(relation.relation_kind);
      current.supportingEdgeCount += relation.supporting_edge_count;
      grouped.set(neighborId, current);
    });

    const list = document.createElement("div");
    list.className = "neighbor-list";
    [...grouped.values()]
      .sort((left, right) => (
        right.supportingEdgeCount - left.supportingEdgeCount
      ))
      .forEach((group) => {
        const neighbor = nodesById.get(group.neighborId) || {
          community_id: group.neighborId,
        };
        const relationLabels = [...group.relationKinds].map((kind) => (
          RELATION_STYLES[kind]?.label || kind
        ));
        const button = document.createElement("button");
        button.type = "button";
        button.className = "neighbor-item";
        const identity = document.createElement("span");
        const title = document.createElement("strong");
        title.textContent = overviewCommunityTitle(neighbor);
        const meta = document.createElement("small");
        meta.textContent =
          `${relationLabels.join("、")} · ${group.supportingEdgeCount} 条支撑边`;
        identity.append(title, meta);
        const arrow = document.createElement("span");
        arrow.className = "neighbor-arrow";
        arrow.textContent = "→";
        button.append(identity, arrow);
        button.addEventListener(
          "click",
          () => selectOverviewCommunity(group.neighborId, { center: true }),
        );
        list.append(button);
      });
    section.append(list);
    return section;
  }

  function renderCommunityRelationDetail(payload) {
    const relation = payload.relation || {};
    const style = RELATION_STYLES[relation.relation_kind] || {
      label: relation.relation_kind || "未知关系",
    };
    const badges = document.createElement("div");
    badges.className = "badge-row";
    badges.append(
      badge(style.label, "kind-badge"),
      badge(`${relation.supporting_edge_count || 0} 条支撑边`, "status-badge"),
    );
    refs.detailContent.append(
      detailHeader(
        "Community Relation",
        style.label,
        relation.relation_id,
      ),
      badges,
      metricGrid([
        [relation.supporting_edge_count || 0, "Supporting Edge"],
        [relation.observed_edge_count || 0, "Observed"],
        [relation.inferred_edge_count || 0, "Inferred"],
      ]),
      definitionSection("方向", [
        ["Source Community", relation.source],
        ["Target Community", relation.target],
        ["Relation Kind", relation.relation_kind],
      ]),
      supportingEdgeSection(payload.supporting_edges || []),
    );
  }

  function supportingEdgeSection(edges) {
    const section = document.createElement("section");
    section.className = "detail-section";
    const heading = document.createElement("h3");
    heading.textContent = "底层 Card 关系";
    section.append(heading);
    if (!edges.length) {
      section.append(createStatusBlock("no-results", "没有可用的支撑边"));
      return section;
    }
    const list = document.createElement("div");
    list.className = "supporting-edge-list";
    edges.forEach((edge) => {
      const item = document.createElement("article");
      item.className = "supporting-edge-item";
      const title = document.createElement("strong");
      title.textContent = RELATION_STYLES[edge.relation_kind]?.label
        || edge.relation_kind;
      const source = document.createElement("p");
      source.textContent = `源：${edge.source_summary || edge.source}`;
      const target = document.createElement("p");
      target.textContent = `目标：${edge.target_summary || edge.target}`;
      item.append(title, source, target);
      if (edge.basis) {
        const basis = document.createElement("p");
        basis.className = "supporting-edge-basis";
        basis.textContent = edge.basis;
        item.append(basis);
      }
      list.append(item);
    });
    section.append(list);
    return section;
  }

  function renderCommunityDetail() {
    const item = state.community.community;
    refs.detailContent.append(
      detailHeader("Community", item.title || "未命名关系社区", item.community_id),
      metricGrid([
        [item.card_count, "Card"],
        [item.edge_count, "Edge"],
        [item.graph_version, "Graph Version"],
        [item.observed_edge_count, "Observed"],
        [item.inferred_edge_count, "Inferred"],
        [formatDecimal(item.graph_density), "Density"],
      ]),
      definitionSection("图状态", [
        ["一致性", item.graph_consistent ? "正常" : "成员 Card/Edge 已缺失"],
        ["已加载成员", `${item.loaded_card_count}/${item.card_count} Card · ${item.loaded_edge_count}/${item.edge_count} Edge`],
        ["核心 Card", item.core_card_id],
        ["锚点 Card", item.identity_anchor_card_id],
        ["事实报告", `${item.fact_report_status} · v${item.fact_report_version}`],
        ["条件预测", `${item.projection_status} · v${item.projection_version}`],
        ["图变更时间", formatDate(item.graph_changed_at)],
        ["最近更新", formatDate(item.updated_at)],
      ]),
      relationKindSection(item.relation_kind_counts || {}),
    );
  }

  function renderNodeDetail(node) {
    const badges = document.createElement("div");
    badges.className = "badge-row";
    if (node.is_core) badges.append(badge("核心节点", "status-badge observed"));
    if (node.is_identity_anchor) badges.append(badge("身份锚点", "status-badge"));
    badges.append(
      badge(node.content_available ? "摘要可用" : "摘要缺失", `status-badge ${node.content_available ? "observed" : "error"}`),
    );

    refs.detailContent.append(
      detailHeader("Cognitive Card", node.summary || "Card 摘要缺失", node.card_id),
      badges,
      metricGrid([
        [node.degree, "Degree"],
        [node.in_degree, "In"],
        [node.out_degree, "Out"],
      ]),
      textSection("原子事实摘要", node.summary || "Milvus 中未找到 Card Summary"),
      textSection("焦点原文证据", node.focus_evidence || "Milvus 中未找到 Focus Evidence"),
      definitionSection("来源", [
        ["Source", [node.source_type, node.source_id].filter(Boolean).join(":")],
        ["发布时间", formatDate(node.source_published_at)],
        ["Evidence", node.evidence_id],
        ["Primary Chunk", node.primary_chunk_id],
        ["Focus Refs", (node.focus_evidence_refs || []).join(", ")],
      ]),
      probeSection(node.relation_probes || []),
    );
  }

  function renderEdgeDetail(edge) {
    const relation = RELATION_STYLES[edge.relation_kind] || {
      label: edge.relation_kind,
      color: "#65716b",
    };
    const badges = document.createElement("div");
    badges.className = "badge-row";
    badges.append(
      badge(relation.label, "kind-badge"),
      badge(edge.decision_class, `status-badge ${edge.decision_class}`),
      badge(edge.cross_chunk ? "跨 Chunk" : "同 Chunk", "status-badge"),
    );
    const title = edge.relation_type || relation.label;
    refs.detailContent.append(
      detailHeader("Card Relation", title, edge.edge_id),
      badges,
      metricGrid([
        [formatDecimal(edge.confidence), "Confidence"],
        [edge.source_evidence_refs?.length || 0, "Source Refs"],
        [edge.target_evidence_refs?.length || 0, "Target Refs"],
      ]),
      textSection("关系依据", edge.basis || "无"),
      edge.inference_mechanism
        ? textSection("推断机制", edge.inference_mechanism)
        : document.createDocumentFragment(),
      definitionSection("方向", [
        ["Source Card", edge.source],
        ["Target Card", edge.target],
        ["Direction", edge.direction || `${edge.source} → ${edge.target}`],
        ["Source Chunk", edge.source_primary_chunk_id],
        ["Target Chunk", edge.target_primary_chunk_id],
        ["Relation Refs", (edge.relation_evidence_refs || []).join(", ")],
      ]),
    );
  }

  function renderReport() {
    const community = state.community.community;
    const header = detailHeader(
      "Fact Report",
      community.title || "事实性高级认知报告",
      `${community.fact_report_status} · version ${community.fact_report_version}`,
    );
    const status = document.createElement("div");
    status.className = "badge-row";
    status.append(
      badge(
        community.fact_report_status,
        `status-badge ${community.fact_report_status === "ready" ? "observed" : ""}`,
      ),
      badge(`${community.card_count} Card`, "status-badge"),
      badge(`${community.edge_count} Edge`, "status-badge"),
    );
    refs.detailContent.append(
      header,
      status,
      textSection(
        "报告正文",
        community.fact_report || "当前 Community 尚未生成事实报告",
        "report-text",
      ),
      definitionSection("生成状态", [
        ["生成时间", formatDate(community.fact_report_generated_at)],
        ["图版本", community.graph_version],
        ["报告版本", community.fact_report_version],
      ]),
    );
  }

  function renderProjections() {
    const community = state.community.community;
    refs.detailContent.append(
      detailHeader(
        "Conditional Projections",
        "条件性未来预测",
        `${community.projection_status} · version ${community.projection_version}`,
      ),
    );
    const projections = community.conditional_projections || [];
    if (!projections.length) {
      refs.detailContent.append(
        createStatusBlock("no-results", "当前事实基础没有形成可交付的条件预测"),
      );
      return;
    }
    projections.forEach((projection, index) => {
      const item = document.createElement("article");
      item.className = "projection-item";
      const title = document.createElement("h3");
      title.textContent =
        projection.judgement
        || projection.hypothesis
        || projection.possible_result
        || `条件预测 ${index + 1}`;
      item.append(title);

      const definitions = [
        ["成立条件", projection.conditions],
        ["可能结果", projection.possible_result],
        ["验证指标", projection.indicators || projection.validation_indicators],
        ["失效条件", projection.invalidation_conditions || projection.invalidation_condition],
        ["时间范围", projection.time_horizon],
      ].filter(([, value]) => hasValue(value));
      const dl = document.createElement("dl");
      definitions.forEach(([label, value]) => {
        const wrapper = document.createElement("div");
        const dt = document.createElement("dt");
        const dd = document.createElement("dd");
        dt.textContent = label;
        dd.textContent = formatValue(value);
        wrapper.append(dt, dd);
        dl.append(wrapper);
      });
      item.append(dl);
      refs.detailContent.append(item);
    });
    refs.detailContent.append(
      definitionSection("生成状态", [
        ["生成时间", formatDate(community.projection_generated_at)],
        ["预测版本", community.projection_version],
        ["预测数量", projections.length],
      ]),
    );
  }

  function relationKindSection(counts) {
    const section = document.createElement("section");
    section.className = "detail-section";
    const heading = document.createElement("h3");
    heading.textContent = "关系构成";
    const row = document.createElement("div");
    row.className = "badge-row";
    Object.entries(counts).forEach(([kind, count]) => {
      const relation = RELATION_STYLES[kind] || { label: kind };
      row.append(badge(`${relation.label} ${count}`, "kind-badge"));
    });
    if (!row.children.length) row.append(badge("暂无关系", "kind-badge"));
    section.append(heading, row);
    return section;
  }

  function probeSection(probes) {
    if (!probes.length) return document.createDocumentFragment();
    const section = document.createElement("section");
    section.className = "detail-section";
    const heading = document.createElement("h3");
    heading.textContent = "Relation Probes";
    const list = document.createElement("ul");
    list.className = "detail-list";
    probes.forEach((probe) => {
      const li = document.createElement("li");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = probe.probe_type || probe.relation_role || "probe";
      dd.textContent = probe.query || probe.probe || formatValue(probe);
      li.append(dt, dd);
      list.append(li);
    });
    section.append(heading, list);
    return section;
  }

  function detailHeader(eyebrowText, titleText, subtitleText) {
    const header = document.createElement("header");
    header.className = "detail-header";
    const eyebrow = document.createElement("div");
    eyebrow.className = "detail-eyebrow";
    eyebrow.textContent = eyebrowText;
    const title = document.createElement("h2");
    title.textContent = titleText;
    const subtitle = document.createElement("p");
    subtitle.textContent = subtitleText || "";
    header.append(eyebrow, title, subtitle);
    return header;
  }

  function metricGrid(items) {
    const grid = document.createElement("section");
    grid.className = "metric-grid";
    items.forEach(([value, label]) => {
      const item = document.createElement("div");
      item.className = "metric";
      const strong = document.createElement("strong");
      const span = document.createElement("span");
      strong.textContent = value ?? "—";
      span.textContent = label;
      item.append(strong, span);
      grid.append(item);
    });
    return grid;
  }

  function textSection(titleText, content, className = "") {
    const section = document.createElement("section");
    section.className = "detail-section";
    const heading = document.createElement("h3");
    heading.textContent = titleText;
    const text = document.createElement("p");
    text.className = className;
    text.textContent = content || "无";
    section.append(heading, text);
    return section;
  }

  function definitionSection(titleText, items) {
    const section = document.createElement("section");
    section.className = "detail-section";
    const heading = document.createElement("h3");
    heading.textContent = titleText;
    const list = document.createElement("dl");
    list.className = "detail-list";
    items.forEach(([label, value]) => {
      const wrapper = document.createElement("li");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = formatValue(value);
      wrapper.append(dt, dd);
      list.append(wrapper);
    });
    section.append(heading, list);
    return section;
  }

  function badge(text, className) {
    const element = document.createElement("span");
    element.className = className;
    element.textContent = text;
    return element;
  }

  function createStatusBlock(className, text) {
    const block = document.createElement("div");
    block.className = className;
    block.textContent = text;
    return block;
  }

  function destroyGraph() {
    if (state.cy) {
      state.cy.destroy();
      state.cy = null;
    }
    hideGraphTooltip();
    refs.cy.replaceChildren();
  }

  function syncActiveTab() {
    document.querySelectorAll(".detail-tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.tab === state.activeTab);
    });
  }

  function setConnection(status, label) {
    refs.connectionDot.className = "status-dot";
    if (status === "loading") refs.connectionDot.classList.add("loading");
    if (status === "error") refs.connectionDot.classList.add("error");
    refs.connectionLabel.textContent = label;
  }

  function updateUrl() {
    const params = new URLSearchParams();
    params.set("target", state.target);
    params.set("adapter", state.adapterName);
    params.set(
      "view",
      state.viewMode === "detail" ? "cards" : "overview",
    );
    if (state.selectedCommunityId) {
      params.set("community", state.selectedCommunityId);
    }
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  }

  function showToast(message) {
    refs.toast.textContent = message;
    refs.toast.classList.add("visible");
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => refs.toast.classList.remove("visible"), 4200);
  }

  async function responseError(response) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch (_) {
      // Keep the HTTP status when the body is not JSON.
    }
    return new Error(message);
  }

  function shortText(value, maxLength) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? String(value)
      : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(date);
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
  }

  function formatDecimal(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(number < 1 ? 3 : 2) : "—";
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) return value.map(formatValue).join("；");
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
  }

  function hasValue(value) {
    return value !== null
      && value !== undefined
      && value !== ""
      && (!Array.isArray(value) || value.length > 0);
  }

  function escapeSelector(value) {
    if (window.CSS?.escape) return window.CSS.escape(value);
    return String(value).replace(/([:.])/g, "\\$1");
  }

  function toCamel(value) {
    return value.replace(/-([a-z])/g, (_, character) => character.toUpperCase());
  }
})();
