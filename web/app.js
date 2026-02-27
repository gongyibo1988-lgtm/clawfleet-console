(function () {
  const AUTH = {
    csrfToken: null,
    authenticated: false,
    username: "",
  };

  function buildError(payload, status) {
    if (payload && payload.detail) return new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail));
    return new Error(`HTTP ${status}`);
  }

  async function rawApi(path, options) {
    const method = (options && options.method) ? String(options.method).toUpperCase() : "GET";
    const headers = { "Content-Type": "application/json", ...(options?.headers || {}) };
    if (method !== "GET" && method !== "HEAD" && !path.startsWith("/api/auth/") && AUTH.csrfToken) {
      headers["X-CSRF-Token"] = AUTH.csrfToken;
    }
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers,
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    return { response, payload };
  }

  async function api(path, options) {
    const first = await rawApi(path, options);
    if (first.response.ok) return first.payload;
    if (first.response.status === 401 && !path.startsWith("/api/auth/")) {
      const relogin = await ensureAuthenticated();
      if (!relogin) throw new Error("未登录或认证失败");
      const retry = await rawApi(path, options);
      if (!retry.response.ok) throw buildError(retry.payload, retry.response.status);
      return retry.payload;
    }
    throw buildError(first.payload, first.response.status);
  }

  async function promptLogin() {
    const { response, payload } = await rawApi("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ method: "biometric" }),
    });
    if (!response.ok) throw buildError(payload, response.status);
    AUTH.authenticated = true;
    AUTH.username = payload.username || "biometric-user";
    AUTH.csrfToken = payload.csrf_token || null;
    return true;
  }

  async function ensureAuthenticated() {
    const me = await rawApi("/api/auth/me");
    if (me.response.ok) {
      AUTH.authenticated = true;
      AUTH.username = me.payload.username || "";
      AUTH.csrfToken = me.payload.csrf_token || null;
      return true;
    }
    AUTH.authenticated = false;
    AUTH.csrfToken = null;
    try {
      return await promptLogin();
    } catch (error) {
      alert(`指纹登录失败：${error.message}`);
      return false;
    }
  }

  async function requestConfirmTicket(actionLabel) {
    try {
      const biometric = await api("/api/security/confirm", {
        method: "POST",
        body: JSON.stringify({ method: "biometric", action: actionLabel }),
      });
      return biometric.confirm_ticket;
    } catch (error) {
      const code = window.prompt(`请输入 ${actionLabel} 安全口令（指纹失败时回退）`);
      if (!code) throw new Error("未输入安全口令");
      const payload = await api("/api/security/confirm", {
        method: "POST",
        body: JSON.stringify({ method: "code", code, action: actionLabel }),
      });
      return payload.confirm_ticket;
    }
  }

  function esc(text) {
    return String(text || "").replace(/[&<>]/g, function (char) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[char];
    });
  }

  function fmtDetails(details) {
    const rows = Object.entries(details || {}).sort((a, b) => a[0].localeCompare(b[0]));
    if (!rows.length) return "-";
    return rows.map(([k, v]) => `${k}: ${v || "-"}`).join("\n");
  }

  function formatAgo(isoText) {
    if (!isoText) return "-";
    const time = Date.parse(isoText);
    if (Number.isNaN(time)) return isoText;
    const sec = Math.max(0, Math.floor((Date.now() - time) / 1000));
    if (sec < 60) return `${sec}秒前`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}分钟前`;
    const hour = Math.floor(min / 60);
    if (hour < 24) return `${hour}小时前`;
    return `${Math.floor(hour / 24)}天前`;
  }

  function parseDiskRows(details) {
    return Object.entries(details || {})
      .filter(([key]) => key.startsWith("disk_"))
      .map(([key, value]) => {
        const path = key.replace("disk_", "").replace(/_+/g, "/").replace(/^\/?root/, "/root");
        return { path, value: value || "-" };
      });
  }

  function parseServerModel(server) {
    const details = server.details || {};
    const memTotal = Number(details.mem_total_mb || 0);
    const memAvail = Number(details.mem_avail_mb || 0);
    const memUsed = memTotal > 0 ? Math.max(0, memTotal - memAvail) : 0;
    const memPercent = memTotal > 0 ? Math.round((memUsed / memTotal) * 100) : 0;
    return {
      name: server.server,
      reachable: !!server.reachable,
      capturedAt: server.captured_at || "-",
      ago: formatAgo(server.captured_at),
      error: server.error || "",
      hostname: details.hostname || "-",
      uptime: details.uptime || "-",
      loadavg: details.loadavg || "-",
      gatewayStatus: details.gateway_status || "unknown",
      gatewayPortListen: details.gateway_port_listen || "unknown",
      openclawVersion: details.openclaw_version || "-",
      openclawHealth: details.openclaw_health || "-",
      gatewayLogTail: (details.gateway_log_tail || "").split("|").filter(Boolean).join("\n"),
      memText: memTotal ? `${memUsed} / ${memTotal} MB` : "-",
      memPercent: memTotal ? `${memPercent}%` : "-",
      disks: parseDiskRows(details),
      raw: fmtDetails(details),
    };
  }

  function renderLineChart(series, key, color) {
    if (!series || !series.length) return '<div class="chart-empty">暂无数据</div>';
    const width = 360;
    const height = 120;
    const padding = 16;
    const values = series.map((item) => Number(item[key] || 0));
    const max = Math.max(...values, 1);
    const span = Math.max(1, values.length - 1);
    const points = values
      .map((value, index) => {
        const x = padding + (index / span) * (width - padding * 2);
        const y = height - padding - (value / max) * (height - padding * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="120" aria-label="${esc(key)} line chart">
      <polyline fill="none" stroke="${esc(color)}" stroke-width="2" points="${points}" />
      <line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" stroke="#d4dfed" />
      <text x="${width - 6}" y="12" font-size="11" text-anchor="end" fill="#57657a">max ${max}</text>
    </svg>`;
  }

  function renderAgentRankTable(rank) {
    if (!rank || !rank.length) return '<div class="chart-empty">暂无 Agent 数据</div>';
    const rows = rank.slice(0, 8).map((item) => {
      return `<tr>
        <td>${esc(item.agent)}</td>
        <td>${esc(item.sessions_24h)}</td>
        <td>${esc(item.errors_24h)}</td>
        <td>${esc(item.error_rate)}%</td>
        <td>${esc(item.latest_session_id || "-")}</td>
      </tr>`;
    }).join("");
    return `<table class="rank-table">
      <thead><tr><th>Agent</th><th>会话</th><th>错误</th><th>错误率</th><th>最近 Session</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  function renderSubagentRankTable(rank) {
    if (!rank || !rank.length) return '<div class="chart-empty">暂无 Subagent 数据</div>';
    const rows = rank.slice(0, 8).map((item) => {
      return `<tr>
        <td>${esc(item.subagent)}</td>
        <td>${esc(item.calls_24h)}</td>
        <td>${esc(item.errors_24h)}</td>
        <td>${esc(item.last_seen_at || "-")}</td>
      </tr>`;
    }).join("");
    return `<table class="rank-table">
      <thead><tr><th>Subagent</th><th>调用</th><th>错误</th><th>最近出现</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  function renderRuntimeBlock(runtime) {
    if (!runtime) return '<div class="chart-empty">Agent 运行分析未返回数据</div>';
    const runtimeErrors = runtime.errors && runtime.errors.length
      ? `<div class="error">${esc(runtime.errors.join(" | "))}</div>`
      : "";
    return `
      ${runtimeErrors}
      <div class="runtime-grid">
        <div class="chart-panel">
          <p class="chart-title">24 小时会话趋势</p>
          ${renderLineChart(runtime.agent_timeseries || [], "sessions", "#124f96")}
        </div>
        <div class="chart-panel">
          <p class="chart-title">24 小时错误趋势</p>
          ${renderLineChart(runtime.agent_timeseries || [], "errors", "#b12525")}
        </div>
      </div>
      <section class="section">
        <h4 class="section-title">Agent 活跃排行榜（24h）</h4>
        ${renderAgentRankTable(runtime.agent_rank)}
      </section>
      <section class="section">
        <h4 class="section-title">Subagent 调用排行榜（24h）</h4>
        ${renderSubagentRankTable(runtime.subagent_rank)}
      </section>
    `;
  }

  async function openTerminal(name) {
    if (!confirm(`要打开终端并连接到 ${name} 吗？`)) return;
    await api("/api/terminal/open", {
      method: "POST",
      body: JSON.stringify({ server: name }),
    });
  }

  function renderServerCard(server, runtime) {
    const view = parseServerModel(server);
    const statusClass = view.reachable ? "ok" : "bad";
    const gatewayOk = view.gatewayStatus === "active";
    const gatewayStatusText = view.gatewayStatus === "active" ? "运行中" : view.gatewayStatus;
    const portListenText = view.gatewayPortListen === "yes" ? "监听中" : view.gatewayPortListen;
    const gatewayBadge = gatewayOk ? "ok" : "bad";
    const error = view.error ? `<div class="error">${esc(view.error)}</div>` : "";
    const diskRows = view.disks.length
      ? view.disks.map((row) => `<div><strong>${esc(row.path)}</strong>: ${esc(row.value)}</div>`).join("")
      : "<div>-</div>";
    const logContent = view.gatewayLogTail || "（暂无最近日志）";
    return `<article class="server-card">
      <div class="server-head">
        <div>
          <h3 class="server-title">${esc(view.name)}</h3>
          <div class="server-sub">${esc(view.hostname)} · 更新于 ${esc(view.ago)}</div>
        </div>
        <span class="badge ${statusClass}">${view.reachable ? "可连接" : "不可连接"}</span>
      </div>
      ${error}
      <div class="chip-row">
        <div class="chip">
          <div class="k">网关状态</div>
          <div class="v"><span class="badge ${gatewayBadge}">${esc(gatewayStatusText)}</span></div>
        </div>
        <div class="chip">
          <div class="k">端口监听</div>
          <div class="v">${esc(portListenText)}</div>
        </div>
        <div class="chip">
          <div class="k">内存占用</div>
          <div class="v">${esc(view.memPercent)}</div>
        </div>
      </div>
      <section class="section">
        <h4 class="section-title">系统信息</h4>
        <div class="kv">
          <div class="k">采集时间</div><div>${esc(view.capturedAt)}</div>
          <div class="k">运行时长</div><div>${esc(view.uptime)}</div>
          <div class="k">负载</div><div>${esc(view.loadavg)}</div>
          <div class="k">OpenClaw 版本</div><div>${esc(view.openclawVersion)}</div>
          <div class="k">健康检查</div><div>${esc(view.openclawHealth)}</div>
          <div class="k">内存详情</div><div>${esc(view.memText)}</div>
        </div>
      </section>
      <section class="section">
        <h4 class="section-title">磁盘使用</h4>
        <div class="disk-list">${diskRows}</div>
      </section>
      <section class="section">
        <h4 class="section-title">Agent 运行分析（24h）</h4>
        ${renderRuntimeBlock(runtime)}
      </section>
      <details>
        <summary>网关日志</summary>
        <pre>${esc(logContent)}</pre>
      </details>
      <details>
        <summary>原始 KV 明细</summary>
        <pre>${esc(view.raw)}</pre>
      </details>
      <div class="actions">
        <button class="btn" data-open-terminal="${esc(server.server)}">打开 SSH</button>
      </div>
    </article>`;
  }

  function renderSummary(items) {
    const total = items.length;
    const up = items.filter((item) => item.reachable).length;
    const gatewayActive = items.filter((item) => (item.details || {}).gateway_status === "active").length;
    const warnings = items.filter((item) => {
      if (!item.reachable) return true;
      const details = item.details || {};
      return details.gateway_status !== "active" || details.gateway_port_listen !== "yes";
    }).length;
    const sumTotal = document.getElementById("sum-total");
    const sumUp = document.getElementById("sum-up");
    const sumGw = document.getElementById("sum-gw");
    const sumWarn = document.getElementById("sum-warn");
    if (sumTotal) sumTotal.textContent = String(total);
    if (sumUp) sumUp.textContent = String(up);
    if (sumGw) sumGw.textContent = String(gatewayActive);
    if (sumWarn) sumWarn.textContent = String(warnings);
  }

  async function renderDashboard() {
    const authOk = await ensureAuthenticated();
    if (!authOk) return;
    const cards = document.getElementById("cards");
    const updated = document.getElementById("updated-at");
    const refresh = document.getElementById("refresh");
    const updateAll = document.getElementById("update-all");
    const backupAll = document.getElementById("backup-all");
    const opsStatus = document.getElementById("ops-status");
    const empty = document.getElementById("empty");

    function setOpsStatus(message, kind) {
      if (!opsStatus) return;
      opsStatus.style.display = "block";
      opsStatus.className = "status";
      if (kind) opsStatus.classList.add(kind);
      opsStatus.textContent = message;
    }

    async function runMaintenance(action) {
      const label = action === "update" ? "一键更新" : "一键备份";
      if (!confirm(`确认执行${label}（全部服务器）吗？`)) return;
      setOpsStatus(`${label}执行中...`);
      try {
        const confirmTicket = await requestConfirmTicket(label);
        const response = await api(`/api/maintenance/${action}`, {
          method: "POST",
          body: JSON.stringify({ server: "all", confirm_ticket: confirmTicket }),
        });
        const values = Object.values(response.servers || {});
        const failed = values.filter((item) => !item.ok).length;
        if (failed > 0) {
          setOpsStatus(`${label}完成：${failed} 台失败，请查看日志输出。`, "bad");
        } else {
          setOpsStatus(`${label}完成：全部成功。`, "ok");
        }
      } catch (error) {
        setOpsStatus(`${label}失败：${error.message}`, "bad");
      }
    }

    async function load() {
      let data;
      let runtimeData = { servers: {} };
      try {
        const result = await Promise.all([
          api("/api/status"),
          api("/api/agent-runtime").catch((error) => ({ error: String(error), servers: {} })),
        ]);
        data = result[0];
        runtimeData = result[1] || { servers: {} };
      } catch (error) {
        updated.textContent = `拉取失败：${error.message}`;
        cards.innerHTML = "";
        if (empty) {
          empty.style.display = "block";
          empty.textContent = "状态拉取失败。";
        }
        return;
      }
      updated.textContent = data.updated_at ? `最近刷新：${data.updated_at}` : "等待首次轮询...";
      const items = Object.values(data.servers || {});
      renderSummary(items);
      if (!items.length) {
        cards.innerHTML = "";
        if (empty) {
          empty.style.display = "block";
          empty.textContent = "暂无状态数据。";
        }
      } else {
        const runtimeMap = runtimeData.servers || {};
        cards.innerHTML = items
          .map((item) => renderServerCard(item, runtimeMap[item.server] || null))
          .join("");
        if (empty) empty.style.display = "none";
      }
      cards.querySelectorAll("[data-open-terminal]").forEach((button) => {
        button.addEventListener("click", async () => {
          await openTerminal(button.getAttribute("data-open-terminal"));
        });
      });
    }

    refresh.addEventListener("click", load);
    if (updateAll) updateAll.addEventListener("click", () => runMaintenance("update"));
    if (backupAll) backupAll.addEventListener("click", () => runMaintenance("backup"));
    await load();
    setInterval(load, 5000);
  }

  async function renderSync() {
    const authOk = await ensureAuthenticated();
    if (!authOk) return;
    const rootsNode = document.getElementById("roots");
    const planButton = document.getElementById("plan-btn");
    const runButton = document.getElementById("run-btn");
    const planOutput = document.getElementById("plan-output");
    const sourceServer = document.getElementById("source-server");
    const targetServer = document.getElementById("target-server");
    const mode = document.getElementById("mode");
    const allowDelete = document.getElementById("allow-delete");
    const conflictsCard = document.getElementById("conflicts-card");
    const conflictsNode = document.getElementById("conflicts");
    const syncStatus = document.getElementById("sync-status");

    const cfg = await api("/api/config");
    const roots = cfg.sync.roots || [];
    const servers = Array.isArray(cfg.servers) ? cfg.servers : [];
    if (servers.length < 2) {
      if (syncStatus) {
        syncStatus.className = "status bad";
        syncStatus.textContent = "至少需要 2 台服务器才能执行同步。";
      }
      if (planButton) planButton.disabled = true;
      if (runButton) runButton.disabled = true;
      return;
    }
    function renderServerSelects() {
      if (!sourceServer || !targetServer) return;
      const options = servers
        .map((item) => `<option value="${esc(item.name)}">${esc(item.name)}</option>`)
        .join("");
      sourceServer.innerHTML = options;
      targetServer.innerHTML = options;
      if (servers.length > 1) {
        sourceServer.value = servers[0].name;
        targetServer.value = servers[1].name;
      }
    }

    function updateModeText() {
      if (!mode || !sourceServer || !targetServer) return;
      const sourceName = sourceServer.value || "源服务器";
      const targetName = targetServer.value || "目标服务器";
      const optionOneWay = mode.querySelector('option[value="one_way"]');
      const optionBi = mode.querySelector('option[value="bidirectional"]');
      if (optionOneWay) optionOneWay.textContent = `单向同步（${sourceName} -> ${targetName}）`;
      if (optionBi) optionBi.textContent = `双向同步（${sourceName} <-> ${targetName}，冲突需人工选择）`;
    }

    renderServerSelects();
    updateModeText();
    if (sourceServer) sourceServer.addEventListener("change", updateModeText);
    if (targetServer) targetServer.addEventListener("change", updateModeText);

    rootsNode.innerHTML = roots
      .map(
        (root, index) =>
          `<label class="root-item"><input type="checkbox" data-root value="${esc(root)}" ${index < 2 ? "checked" : ""} /> ${esc(root)}</label>`
      )
      .join("");

    let currentPlan = null;

    function readSelectedRoots() {
      return Array.from(document.querySelectorAll("[data-root]:checked")).map((node) => node.value);
    }

    function renderConflicts(conflicts) {
      if (!conflicts || !conflicts.length) {
        conflictsCard.style.display = "none";
        conflictsNode.innerHTML = "";
        return;
      }
      conflictsCard.style.display = "block";
      conflictsNode.innerHTML = conflicts
        .map(
          (item, idx) => `<div class="conflict-item">
            <div><strong>${esc(item.root)} :: ${esc(item.path)}</strong></div>
            <div class="choice-row">
              <label><input type="radio" name="conflict-${idx}" value="keep_a" checked /> keep_a</label>
              <label><input type="radio" name="conflict-${idx}" value="keep_b" /> keep_b</label>
              <label><input type="radio" name="conflict-${idx}" value="keep_both" /> keep_both</label>
            </div>
          </div>`
        )
        .join("");
    }

    function collectConflictResolutions() {
      if (!currentPlan || !currentPlan.conflicts) return [];
      return currentPlan.conflicts.map((item, idx) => {
        const checked = document.querySelector(`input[name=\"conflict-${idx}\"]:checked`);
        return {
          root: item.root,
          path: item.path,
          decision: checked ? checked.value : "keep_a",
        };
      });
    }

    planButton.addEventListener("click", async () => {
      const selectedRoots = readSelectedRoots();
      if (!selectedRoots.length) {
        alert("请至少选择一个同步目录。");
        return;
      }
      if (!sourceServer || !targetServer || !sourceServer.value || !targetServer.value) {
        alert("请选择源服务器和目标服务器。");
        return;
      }
      if (sourceServer.value === targetServer.value) {
        alert("源服务器和目标服务器不能相同。");
        return;
      }
      runButton.disabled = true;
      if (syncStatus) {
        syncStatus.className = "status";
        syncStatus.textContent = "正在生成计划...";
      }
      planOutput.textContent = "正在生成计划...";
      try {
        const response = await api("/api/sync/plan", {
          method: "POST",
          body: JSON.stringify({
            mode: mode.value,
            source_server: sourceServer.value,
            target_server: targetServer.value,
            roots: selectedRoots,
            allow_delete: !!allowDelete.checked,
          }),
        });
        currentPlan = response;
        renderConflicts(response.conflicts || []);
        planOutput.textContent = JSON.stringify(response, null, 2);
        runButton.disabled = false;
        if (syncStatus) {
          syncStatus.className = "status ok";
          syncStatus.textContent = `计划已生成（冲突 ${response.conflicts?.length || 0} 项）`;
        }
      } catch (error) {
        planOutput.textContent = `计划生成失败：${error.message}`;
        if (syncStatus) {
          syncStatus.className = "status bad";
          syncStatus.textContent = `计划生成失败：${error.message}`;
        }
      }
    });

    runButton.addEventListener("click", async () => {
      if (!currentPlan) return;
      if (!confirm("确认执行同步吗？这会传输并覆盖目标文件。")) return;
      runButton.disabled = true;
      if (syncStatus) {
        syncStatus.className = "status";
        syncStatus.textContent = "正在执行同步...";
      }
      planOutput.textContent = "正在执行同步...";
      try {
        const confirmTicket = await requestConfirmTicket("执行同步");
        const result = await api("/api/sync/run", {
          method: "POST",
          body: JSON.stringify({
            plan_id: currentPlan.plan_id,
            conflict_resolutions: collectConflictResolutions(),
            confirm_ticket: confirmTicket,
          }),
        });
        planOutput.textContent = JSON.stringify(result, null, 2);
        runButton.disabled = false;
        if (syncStatus) {
          syncStatus.className = "status ok";
          syncStatus.textContent = "同步执行成功。";
        }
      } catch (error) {
        planOutput.textContent = `同步执行失败：${error.message}`;
        runButton.disabled = false;
        if (syncStatus) {
          syncStatus.className = "status bad";
          syncStatus.textContent = `同步执行失败：${error.message}`;
        }
      }
    });
  }

  async function renderSettings() {
    const authOk = await ensureAuthenticated();
    if (!authOk) return;
    const pre = document.getElementById("config");
    const reload = document.getElementById("reload");
    const status = document.getElementById("settings-status");

    async function load() {
      if (status) {
        status.className = "status";
        status.textContent = "正在加载配置...";
      }
      try {
        const cfg = await api("/api/config");
        pre.textContent = JSON.stringify(cfg, null, 2);
        if (status) {
          status.className = "status";
          status.textContent = "配置加载完成。";
        }
      } catch (error) {
        pre.textContent = `配置加载失败：${error.message}`;
        if (status) {
          status.className = "status bad";
          status.textContent = `加载失败：${error.message}`;
        }
      }
    }

    reload.addEventListener("click", async () => {
      if (status) {
        status.className = "status";
        status.textContent = "正在重新加载配置...";
      }
      try {
        await api("/api/reload-config", { method: "POST" });
        await load();
      } catch (error) {
        if (status) {
          status.className = "status bad";
          status.textContent = `重新加载失败：${error.message}`;
        }
      }
    });

    await load();
  }

  async function renderSkills() {
    const authOk = await ensureAuthenticated();
    if (!authOk) return;
    const serverSelect = document.getElementById("skills-server");
    const repoInput = document.getElementById("skills-repo");
    const promptInput = document.getElementById("skills-prompt");
    const searchBtn = document.getElementById("skills-search-btn");
    const installBtn = document.getElementById("skills-install-btn");
    const refreshBtn = document.getElementById("skills-refresh-btn");
    const candidatesNode = document.getElementById("skills-candidates");
    const selectedMarket = document.getElementById("skills-selected-market");
    const marketDetailNode = document.getElementById("skills-market-detail");
    const copySource = document.getElementById("copy-source-server");
    const copyTarget = document.getElementById("copy-target-server");
    const copySkill = document.getElementById("copy-skill-name");
    const copySkillHint = document.getElementById("copy-skill-hint");
    const copyBtn = document.getElementById("copy-skill-btn");
    const copyStatus = document.getElementById("copy-status");
    const syncSkillsBtn = document.getElementById("sync-skills-btn");
    const syncSkillsStatus = document.getElementById("sync-skills-status");
    const skillsStatus = document.getElementById("skills-status");
    const listStatus = document.getElementById("skills-list-status");
    const skillsGrid = document.getElementById("skills-grid");
    const output = document.getElementById("skills-output");
    let latestServerMap = {};
    let latestCandidates = [];

    function setStatus(node, message, kind) {
      if (!node) return;
      node.className = "status";
      if (kind) node.classList.add(kind);
      node.textContent = message;
    }

    function renderServerSkillsCard(server) {
      if (server.error) {
        return `<article class="server-card">
          <h3>${esc(server.server_name)}</h3>
          <div class="status bad">${esc(server.error)}</div>
        </article>`;
      }
      const skills = Array.isArray(server.skills) ? server.skills : [];
      const rows = skills.length
        ? skills.map((item) => `<div class="skill-item">
            <strong>${esc(item.name)}</strong>
            <span class="pill">${esc(item.skill_type === "official" ? "官方技能" : "自装技能")}</span>
            <span class="pill">${esc(item.source || "-")}</span>
            <div class="muted">安装时间：${esc(item.installed_at || "-")}</div>
            <div class="muted">${esc(item.path || "-")}</div>
          </div>`).join("")
        : `<div class="skill-item">暂无技能</div>`;
      return `<article class="server-card">
        <h3>${esc(server.server_name)}（${skills.length}）</h3>
        <div class="skill-list">${rows}</div>
      </article>`;
    }

    async function loadSkills() {
      setStatus(listStatus, "正在加载技能列表...");
      try {
        const payload = await api("/api/skills/list");
        const map = payload.servers || {};
        latestServerMap = map;
        const items = Object.values(map);
        skillsGrid.innerHTML = items.map(renderServerSkillsCard).join("") || '<article class="server-card">暂无数据</article>';
        setStatus(listStatus, `加载完成：${items.length} 台服务器`, "ok");
        function resetServerOptions(node, includeAll) {
          if (!node) return;
          node.innerHTML = "";
          if (includeAll) {
            const option = document.createElement("option");
            option.value = "all";
            option.textContent = "全部服务器";
            node.appendChild(option);
          }
          items.forEach((item) => {
            const option = document.createElement("option");
            option.value = item.server_name;
            option.textContent = item.server_name;
            node.appendChild(option);
          });
        }
        resetServerOptions(serverSelect, true);
        resetServerOptions(copySource, false);
        resetServerOptions(copyTarget, false);
        populateCopySkillOptions();
      } catch (error) {
        setStatus(listStatus, `加载失败：${error.message}`, "bad");
      }
    }

    function renderCandidates(candidates) {
      latestCandidates = Array.isArray(candidates) ? candidates : [];
      if (!candidatesNode) return;
      if (!candidates || !candidates.length) {
        candidatesNode.innerHTML = '<div class="candidate-item">没有匹配候选</div>';
        if (selectedMarket) selectedMarket.innerHTML = '<option value="">没有候选</option>';
        return;
      }
      const selectedTarget = serverSelect ? serverSelect.value : "all";
      function isInstalledOnTarget(skillName) {
        const name = String(skillName || "").trim();
        if (!name) return false;
        const checkServer = (serverItem) => {
          const skills = Array.isArray(serverItem?.skills) ? serverItem.skills : [];
          return skills.some((item) => String(item?.name || "").trim() === name);
        };
        if (selectedTarget === "all") {
          return Object.values(latestServerMap || {}).some((serverItem) => checkServer(serverItem));
        }
        return checkServer(latestServerMap[selectedTarget]);
      }
      candidatesNode.innerHTML = candidates
        .map((item, index) => {
          const score = item.score === null || item.score === undefined ? "-" : item.score;
          const source = item.source || "clawhub";
          const installed = isInstalledOnTarget(item.name);
          const installedPill = installed ? ' <span class="pill" style="border-color:#98d5b3;color:#117a46;">installed</span>' : "";
          return `<div class="candidate-item"><strong>#${index + 1}</strong> ${esc(item.name)} <span class="pill">score=${esc(score)}</span> <span class="pill">${esc(source)}</span>${installedPill} <span class="muted">${esc(item.path)}</span></div>`;
        })
        .join("");
      if (selectedMarket) {
        selectedMarket.innerHTML = candidates
          .map((item, index) => {
            const score = item.score === null || item.score === undefined ? "-" : item.score;
            const installed = isInstalledOnTarget(item.name);
            const suffix = installed ? " [installed]" : "";
            return `<option value="${esc(item.path)}||${esc(item.name)}">${index + 1}. ${esc(item.name)}（score ${esc(score)}）${suffix}</option>`;
          })
          .join("");
      }
      loadSelectedMarketDetail();
    }

    function renderMarketDetail(detail, error) {
      if (!marketDetailNode) return;
      if (error) {
        marketDetailNode.className = "status bad";
        marketDetailNode.textContent = `详情加载失败：${error}`;
        return;
      }
      if (!detail) {
        marketDetailNode.className = "status";
        marketDetailNode.textContent = "请选择候选技能以查看主要功能与示例。";
        return;
      }
      const features = Array.isArray(detail.features) ? detail.features : [];
      const examples = Array.isArray(detail.examples) ? detail.examples : [];
      const featureHtml = features.length ? `<ul>${features.map((row) => `<li>${esc(row)}</li>`).join("")}</ul>` : "<div class=\"muted\">暂无功能列表</div>";
      const exampleHtml = examples.length ? `<ul>${examples.map((row) => `<li>${esc(row)}</li>`).join("")}</ul>` : "<div class=\"muted\">暂无示例</div>";
      marketDetailNode.className = "status";
      marketDetailNode.innerHTML = `<div><strong>${esc(detail.name || "-")}</strong></div>
        <div class="muted" style="margin-top:4px;">${esc(detail.summary || "暂无简介")}</div>
        <div style="margin-top:8px;"><strong>主要功能</strong>${featureHtml}</div>
        <div style="margin-top:8px;"><strong>使用示例</strong>${exampleHtml}</div>`;
    }

    async function loadSelectedMarketDetail() {
      if (!selectedMarket) return;
      const selectedRaw = selectedMarket.value || "";
      if (!selectedRaw || !selectedRaw.includes("||")) {
        renderMarketDetail(null, null);
        return;
      }
      const parts = selectedRaw.split("||");
      const marketPath = parts[0] || "";
      const marketName = parts[1] || "";
      marketDetailNode.className = "status";
      marketDetailNode.textContent = "正在加载候选详情...";
      try {
        const detail = await api("/api/skills/market-detail", {
          method: "POST",
          body: JSON.stringify({
            market_path: marketPath,
            market_name: marketName,
          }),
        });
        renderMarketDetail(detail, null);
      } catch (error) {
        const cleanedError = String(error.message || "")
          .replace(/\\u001b\[[0-9;]*m/g, "")
          .replace(/\s+/g, " ")
          .slice(0, 120);
        const fallback = latestCandidates.find((item) => item.name === marketName || item.path === marketPath) || null;
        if (fallback) {
          renderMarketDetail(
            {
              name: fallback.name,
              summary: `${fallback.description || "暂无简介"}（详情接口失败：${cleanedError || "请稍后重试"}）`,
              features: [],
              examples: [],
            },
            null
          );
          return;
        }
        renderMarketDetail(null, error.message);
      }
    }

    if (selectedMarket) {
      selectedMarket.addEventListener("change", loadSelectedMarketDetail);
    }

    if (serverSelect) {
      serverSelect.addEventListener("change", () => {
        if (latestCandidates.length) {
          renderCandidates(latestCandidates);
        }
      });
    }

    function populateCopySkillOptions() {
      if (!copySource || !copyTarget || !copySkill) return;
      const source = copySource.value;
      const target = copyTarget.value;
      const server = latestServerMap[source];
      const targetServer = latestServerMap[target];
      const skills = Array.isArray(server?.skills) ? server.skills : [];
      const targetSkills = Array.isArray(targetServer?.skills) ? targetServer.skills : [];
      const targetSet = new Set(targetSkills.map((item) => item?.name).filter((item) => !!item));
      const selected = new Set(Array.from(copySkill.selectedOptions || []).map((item) => item.value));
      if (!skills.length) {
        copySkill.innerHTML = '<option value="">暂无可复制技能</option>';
        if (copySkillHint) copySkillHint.textContent = "";
        return;
      }
      let missingCount = 0;
      copySkill.innerHTML = skills
        .map((item) => {
          const isMissing = !targetSet.has(item.name);
          if (isMissing) missingCount += 1;
          const labelPrefix = isMissing ? "🆕 " : "";
          const suffix = isMissing ? "（目标缺失）" : "";
          const selectedAttr = selected.has(item.name) ? " selected" : "";
          return `<option value="${esc(item.name)}"${selectedAttr}>${labelPrefix}${esc(item.name)}${suffix}</option>`;
        })
        .join("");
      if (copySkillHint) {
        copySkillHint.textContent = `共 ${skills.length} 个技能，其中 ${missingCount} 个在目标服务器缺失（已标注 🆕）。`;
      }
    }

    if (copySource) {
      copySource.addEventListener("change", populateCopySkillOptions);
    }
    if (copyTarget) {
      copyTarget.addEventListener("change", populateCopySkillOptions);
    }

    if (searchBtn) {
      searchBtn.addEventListener("click", async () => {
        const prompt = (promptInput.value || "").trim();
        if (!prompt) {
          setStatus(skillsStatus, "请输入提示词再检索。", "bad");
          return;
        }
        setStatus(skillsStatus, "正在检索技能市场候选...");
        try {
          const payload = await api("/api/skills/search-market", {
            method: "POST",
            body: JSON.stringify({ prompt, limit: 5 }),
          });
          renderCandidates(payload.candidates || []);
          setStatus(skillsStatus, "候选检索完成，请选择后安装。", "ok");
        } catch (error) {
          setStatus(skillsStatus, `检索失败：${error.message}`, "bad");
        }
      });
    }

    installBtn.addEventListener("click", async () => {
      const repo = (repoInput.value || "").trim();
      const prompt = (promptInput.value || "").trim();
      const selectedRaw = selectedMarket ? selectedMarket.value : "";
      let marketPath = "";
      let marketName = "";
      if (selectedRaw && selectedRaw.includes("||")) {
        const parts = selectedRaw.split("||");
        marketPath = parts[0] || "";
        marketName = parts[1] || "";
      }
      if (!repo && !marketPath && !prompt) {
        setStatus(skillsStatus, "请填写仓库地址，或先检索并选择候选。", "bad");
        return;
      }
      setStatus(skillsStatus, "正在安装技能...");
      output.textContent = "正在安装...";
      try {
        const confirmTicket = await requestConfirmTicket("安装技能");
        const payload = await api("/api/skills/install", {
          method: "POST",
          body: JSON.stringify({
            server: serverSelect.value,
            repo_url: repo || null,
            prompt: repo ? null : prompt || null,
            market_path: repo ? null : marketPath || null,
            market_name: repo ? null : marketName || null,
            confirm_ticket: confirmTicket,
          }),
        });
        output.textContent = JSON.stringify(payload, null, 2);
        const sample = Object.values(payload.servers || {})[0] || {};
        if (sample.mode === "market_selected") {
          output.textContent += "\n\n提示：已按你选中的 Top 候选安装。";
        }
        const failed = Object.values(payload.servers || {}).filter((item) => !item.ok).length;
        if (failed > 0) {
          setStatus(skillsStatus, `安装完成：${failed} 台失败`, "bad");
        } else {
          setStatus(skillsStatus, "安装完成：全部成功", "ok");
        }
        await loadSkills();
      } catch (error) {
        output.textContent = `安装失败：${error.message}`;
        setStatus(skillsStatus, `安装失败：${error.message}`, "bad");
      }
    });

    if (copyBtn) {
      copyBtn.addEventListener("click", async () => {
        if (!copySource || !copyTarget || !copySkill) return;
        const sourceServer = copySource.value;
        const targetServer = copyTarget.value;
        const skillNames = Array.from(copySkill.selectedOptions || [])
          .map((item) => item.value)
          .filter((value) => value);
        if (!sourceServer || !targetServer || !skillNames.length) {
          setStatus(copyStatus, "请选择源服务器、目标服务器和技能。", "bad");
          return;
        }
        if (sourceServer === targetServer) {
          setStatus(copyStatus, "源服务器和目标服务器不能相同。", "bad");
          return;
        }
        setStatus(copyStatus, `正在复制 ${skillNames.length} 个技能...`);
        try {
          const confirmTicket = await requestConfirmTicket("复制技能");
          const firstSkillName = skillNames[0] || null;
          const payload = await api("/api/skills/copy", {
            method: "POST",
            body: JSON.stringify({
              source_server: sourceServer,
              target_server: targetServer,
              skill_names: skillNames,
              skill_name: firstSkillName,
              confirm_ticket: confirmTicket,
            }),
          });
          output.textContent = JSON.stringify(payload, null, 2);
          if (payload.ok && payload.total) {
            setStatus(copyStatus, `复制完成：${payload.ok_count}/${payload.total}`, "ok");
            await loadSkills();
          } else {
            setStatus(
              copyStatus,
              `复制部分失败：${payload.ok_count || 0}/${payload.total || 0}，请查看输出`,
              "bad"
            );
          }
        } catch (error) {
          setStatus(copyStatus, `复制失败：${error.message}`, "bad");
        }
      });
    }

    if (syncSkillsBtn) {
      syncSkillsBtn.addEventListener("click", async () => {
        if (!confirm("确认按增量策略同步所有服务器技能吗？")) return;
        setStatus(syncSkillsStatus, "正在执行增量同步...");
        try {
          const confirmTicket = await requestConfirmTicket("增量同步技能");
          const payload = await api("/api/skills/sync", {
            method: "POST",
            body: JSON.stringify({ confirm_ticket: confirmTicket }),
          });
          output.textContent = JSON.stringify(payload, null, 2);
          if (payload.ok) {
            setStatus(syncSkillsStatus, `同步完成：${payload.ok_count}/${payload.total_actions}`, "ok");
          } else {
            setStatus(
              syncSkillsStatus,
              `同步部分失败：${payload.ok_count || 0}/${payload.total_actions || 0}，请查看输出`,
              "bad"
            );
          }
          await loadSkills();
        } catch (error) {
          setStatus(syncSkillsStatus, `同步失败：${error.message}`, "bad");
        }
      });
    }

    refreshBtn.addEventListener("click", loadSkills);
    await loadSkills();
  }

  async function renderCron() {
    const authOk = await ensureAuthenticated();
    if (!authOk) return;
    const refreshBtn = document.getElementById("cron-refresh-btn");
    const listStatus = document.getElementById("cron-list-status");
    const detailStatus = document.getElementById("cron-detail-status");
    const cronGrid = document.getElementById("cron-grid");
    const logsNode = document.getElementById("cron-detail-logs");
    const dailyNode = document.getElementById("cron-daily-groups");
    const outputNode = document.getElementById("cron-output-files");
    let cronMap = {};

    function setStatus(node, message, kind) {
      if (!node) return;
      node.className = "status";
      if (kind) node.classList.add(kind);
      node.textContent = message;
    }

    function renderJobItem(serverName, job) {
      const summary = job.summary || {};
      return `<div class="job-item" data-server="${esc(serverName)}" data-job-id="${esc(job.job_id)}">
        <div><strong>${esc(job.schedule || "-")}</strong> <span class="pill">${esc(job.source || "-")}</span></div>
        <div>${esc(job.command || "-")}</div>
        <div class="muted">24h 执行 ${esc(summary.runs_24h || 0)} 次 · 7d 执行 ${esc(summary.runs_7d || 0)} 次 · 7d 错误 ${esc(summary.errors_7d || 0)} · 状态 ${esc(summary.last_status || "unknown")}</div>
      </div>`;
    }

    function renderServerCard(server) {
      const jobs = Array.isArray(server.jobs) ? server.jobs : [];
      if (server.error) {
        return `<article class="server-card"><h3>${esc(server.server_name)}</h3><div class="status bad">${esc(server.error)}</div></article>`;
      }
      const rows = jobs.length
        ? jobs.map((job) => renderJobItem(server.server_name, job)).join("")
        : '<div class="job-item">暂无任务</div>';
      const listHtml = jobs.length > 8
        ? `<details><summary>任务较多，点击展开（${jobs.length}）</summary><div class="job-list">${rows}</div></details>`
        : `<div class="job-list">${rows}</div>`;
      return `<article class="server-card">
        <h3>${esc(server.server_name)}（${jobs.length}）</h3>
        ${listHtml}
      </article>`;
    }

    async function openOutput(serverName, remotePath) {
      setStatus(detailStatus, "正在打开输出文件...");
      try {
        const response = await api("/api/cron/open-output", {
          method: "POST",
          body: JSON.stringify({ server: serverName, remote_path: remotePath }),
        });
        setStatus(detailStatus, `已打开：${response.local_path}`, "ok");
      } catch (error) {
        setStatus(detailStatus, `打开失败：${error.message}`, "bad");
      }
    }

    function bindOutputButtons() {
      if (!outputNode) return;
      outputNode.querySelectorAll("[data-open-output]").forEach((button) => {
        button.addEventListener("click", async () => {
          await openOutput(button.getAttribute("data-server"), button.getAttribute("data-open-output"));
        });
      });
    }

    async function loadDetail(serverName, jobId) {
      setStatus(detailStatus, "正在加载任务详情...");
      logsNode.textContent = "加载中...";
      if (dailyNode) dailyNode.innerHTML = "";
      outputNode.innerHTML = "";
      try {
        const detail = await api("/api/cron/detail", {
          method: "POST",
          body: JSON.stringify({ server: serverName, job_id: jobId, lines: 200 }),
        });
        const summary = detail.summary || {};
        setStatus(
          detailStatus,
          `${serverName} · ${detail.schedule || "-"} · 24h执行 ${summary.runs_24h || 0} 次 · 7d执行 ${summary.runs_7d || 0} 次 · 7d错误 ${summary.errors_7d || 0} 次 · 状态 ${summary.last_status || "unknown"}`,
          summary.last_status === "error" ? "bad" : "ok"
        );
        logsNode.textContent = (detail.recent_logs || []).length ? detail.recent_logs.join("\n") : "暂无日志";
        const dailyBuckets = Array.isArray(detail.daily_buckets) ? detail.daily_buckets : [];
        if (dailyNode) {
          if (!dailyBuckets.length) {
            dailyNode.innerHTML = '<div class="output-item">近 7 天暂无命中执行日志</div>';
          } else {
            dailyNode.innerHTML = dailyBuckets
              .map((bucket, index) => {
                const logs = Array.isArray(bucket.logs) ? bucket.logs : [];
                return `<details ${index === 0 ? "open" : ""}>
                  <summary>${esc(bucket.date)} · 执行 ${esc(bucket.runs || 0)} 次 · 错误 ${esc(bucket.errors || 0)} 次</summary>
                  <pre>${esc(logs.join("\n") || "无日志")}</pre>
                </details>`;
              })
              .join("");
          }
        }
        const files = Array.isArray(detail.output_files) ? detail.output_files : [];
        if (!files.length) {
          outputNode.innerHTML = '<div class="output-item">无输出文本文件</div>';
        } else {
          outputNode.innerHTML = files
            .map((row) => {
              const existsText = row.exists ? "存在" : "不存在";
              return `<div class="output-item">
                <div>
                  <div><strong>${esc(row.remote_path)}</strong></div>
                  <div class="muted">${existsText}${row.size_bytes ? ` · ${row.size_bytes} bytes` : ""}</div>
                </div>
                <button data-server="${esc(serverName)}" data-open-output="${esc(row.remote_path)}" ${row.exists ? "" : "disabled"}>用TextEdit打开</button>
              </div>`;
            })
            .join("");
        }
        bindOutputButtons();
      } catch (error) {
        logsNode.textContent = "加载失败";
        setStatus(detailStatus, `详情加载失败：${error.message}`, "bad");
      }
    }

    function bindJobClicks() {
      if (!cronGrid) return;
      cronGrid.querySelectorAll("[data-job-id]").forEach((node) => {
        node.addEventListener("click", async () => {
          await loadDetail(node.getAttribute("data-server"), node.getAttribute("data-job-id"));
        });
      });
    }

    async function loadList() {
      setStatus(listStatus, "正在加载定时任务...");
      try {
        const payload = await api("/api/cron/list");
        cronMap = payload.servers || {};
        const items = Object.values(cronMap);
        cronGrid.innerHTML = items.map(renderServerCard).join("") || '<article class="server-card">暂无数据</article>';
        bindJobClicks();
        setStatus(listStatus, `加载完成：${items.length} 台服务器`, "ok");
      } catch (error) {
        setStatus(listStatus, `加载失败：${error.message}`, "bad");
      }
    }

    if (refreshBtn) refreshBtn.addEventListener("click", loadList);
    await loadList();
  }

  async function renderFleet() {
    const authOk = await ensureAuthenticated();
    if (!authOk) return;
    const refreshBtn = document.getElementById("fleet-refresh-btn");
    const generatedNode = document.getElementById("fleet-generated-at");
    const statusNode = document.getElementById("fleet-status");
    const alertStatusNode = document.getElementById("fleet-alert-status");
    const nodesNode = document.getElementById("fleet-nodes");
    const alertsNode = document.getElementById("fleet-alerts");
    const checkOutputNode = document.getElementById("fleet-check-output");
    const totalNode = document.getElementById("fleet-total");
    const onlineNode = document.getElementById("fleet-online");
    const onlineRateNode = document.getElementById("fleet-online-rate");
    const gwRateNode = document.getElementById("fleet-gw-rate");
    const abnormalNode = document.getElementById("fleet-abnormal");

    function setStatus(node, message, kind) {
      if (!node) return;
      node.className = "status";
      if (kind) node.classList.add(kind);
      node.textContent = message;
    }

    function riskPill(level) {
      if (level === "critical") return '<span class="pill bad">critical</span>';
      if (level === "warning") return '<span class="pill warn">warning</span>';
      return '<span class="pill ok">ok</span>';
    }

    function renderNodeCard(node) {
      const labels = Array.isArray(node.labels) && node.labels.length
        ? node.labels.map((item) => `<span class="pill">${esc(item)}</span>`).join("")
        : '<span class="pill">no-label</span>';
      const reasons = Array.isArray(node.risk_reasons) && node.risk_reasons.length
        ? node.risk_reasons.join(" / ")
        : "none";
      const diskRisks = Array.isArray(node.disk_risks) && node.disk_risks.length
        ? node.disk_risks.map((item) => `${item.path}: ${item.usage_percent}%`).join(" | ")
        : "none";
      return `<article class="node-card">
        <div class="node-head">
          <div>
            <strong>${esc(node.name)}</strong>
            <span class="pill">${esc(node.type)}</span>
            ${node.reachable ? '<span class="pill ok">online</span>' : '<span class="pill bad">offline</span>'}
            ${riskPill(node.risk_level)}
          </div>
          <button data-fleet-check="${esc(node.name)}">节点自检</button>
        </div>
        <div class="muted">SSH: ${esc(node.ssh_host)}</div>
        <div style="margin-top:6px;">${labels}</div>
        <div class="muted" style="margin-top:6px;">最后心跳：${esc(node.last_heartbeat || "-")} · 延迟：${esc(node.ssh_latency_ms || "-")}ms · 时钟偏移：${esc(node.clock_offset_sec || "-")}s</div>
        <div class="muted" style="margin-top:6px;">Gateway: ${esc(node.gateway_status || "unknown")} / 端口: ${esc(node.gateway_port_listen || "unknown")}</div>
        <div class="muted" style="margin-top:6px;">24h Agent: ${esc(node.agent_sessions_24h || 0)} 会话 / ${esc(node.agent_errors_24h || 0)} 错误 / ${esc(node.agent_error_rate_24h || 0)}%</div>
        <div class="muted" style="margin-top:6px;">风险原因：${esc(reasons)}</div>
        <div class="muted" style="margin-top:6px;">磁盘风险：${esc(diskRisks)}</div>
      </article>`;
    }

    function renderAlerts(events) {
      if (!alertsNode) return;
      if (!events || !events.length) {
        alertsNode.innerHTML = '<div class="alert-item">暂无告警</div>';
        return;
      }
      alertsNode.innerHTML = events
        .slice(0, 50)
        .map((event) => `<div class="alert-item">
          <strong>${esc(event.severity)}</strong> · ${esc(event.server)} · ${esc(event.rule_name)}
          <div class="muted">${esc(event.message || "-")}</div>
          <div class="muted">${esc(event.observed_at || "-")}</div>
        </div>`)
        .join("");
    }

    async function runNodeCheck(serverName) {
      checkOutputNode.textContent = `正在检查 ${serverName} ...`;
      try {
        const payload = await api("/api/fleet/node/check", {
          method: "POST",
          body: JSON.stringify({ server_name: serverName }),
        });
        checkOutputNode.textContent = JSON.stringify(payload, null, 2);
      } catch (error) {
        checkOutputNode.textContent = `自检失败: ${error.message}`;
      }
    }

    function bindCheckButtons() {
      if (!nodesNode) return;
      nodesNode.querySelectorAll("[data-fleet-check]").forEach((button) => {
        button.addEventListener("click", async () => {
          await runNodeCheck(button.getAttribute("data-fleet-check"));
        });
      });
    }

    async function load() {
      setStatus(statusNode, "正在加载混合云总览...");
      setStatus(alertStatusNode, "正在加载告警...");
      try {
        const [fleet, alerts] = await Promise.all([api("/api/fleet/overview"), api("/api/alerts")]);
        const summary = fleet.summary || {};
        if (generatedNode) generatedNode.textContent = `更新时间：${fleet.generated_at || "-"}`;
        if (totalNode) totalNode.textContent = String(summary.total_nodes || 0);
        if (onlineNode) onlineNode.textContent = String(summary.reachable_nodes || 0);
        if (onlineRateNode) onlineRateNode.textContent = `${summary.online_rate || 0}%`;
        if (gwRateNode) gwRateNode.textContent = `${summary.gateway_active_rate || 0}%`;
        if (abnormalNode) abnormalNode.textContent = String(summary.abnormal_nodes || 0);
        const nodes = Array.isArray(fleet.nodes) ? fleet.nodes : [];
        if (nodesNode) {
          nodesNode.innerHTML = nodes.length ? nodes.map(renderNodeCard).join("") : '<article class="node-card">暂无节点</article>';
        }
        bindCheckButtons();
        setStatus(statusNode, `加载完成：${nodes.length} 个节点`, "ok");
        renderAlerts(alerts.events || []);
        const alertSummary = alerts.summary || {};
        setStatus(
          alertStatusNode,
          `告警总数 ${alertSummary.total || 0}（critical ${alertSummary.critical || 0} / warning ${alertSummary.warning || 0}）`,
          (alertSummary.critical || 0) > 0 ? "bad" : "ok"
        );
      } catch (error) {
        setStatus(statusNode, `加载失败：${error.message}`, "bad");
        setStatus(alertStatusNode, `加载失败：${error.message}`, "bad");
      }
    }

    if (refreshBtn) refreshBtn.addEventListener("click", load);
    await load();
  }

  window.OpenClawApp = {
    renderDashboard,
    renderFleet,
    renderSync,
    renderSettings,
    renderSkills,
    renderCron,
  };
})();
