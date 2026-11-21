/**
 * AMM - AI Models Manage Frontend
 * =====================================
 * 混合引擎架构: vllm / llama.cpp / diffusers
 * 支持引擎选择、版本管理、模型启停
 */
(function() {
    'use strict';

    const API_BASE = '/api';
    const REFRESH_INTERVAL = 5000;

    // ---- State ----
    let config = null;
    let instances = {};
    let systemInfo = null;
    let engineInfo = null;
    let engineVersions = null;
    let engineCatalog = null;      // v1.1: 引擎目录（上游版本 + 本地安装）
    let engineTaskTimer = null;
    let runningModels = [];
    let refreshTimer = null;
    let logRefreshTimer = null;

    const categoryIcons = {
        chat: '💬', embedding: '🧬', asr: '🎙️', tts: '🔊',
        reranker: '📊', ocr: '👁️', image: '🖼️', video: '🎬',
    };

    const categoryNames = {
        chat: 'Chat/LLM/VLM', embedding: 'Embedding', asr: 'ASR',
        tts: 'TTS', reranker: 'Reranker', ocr: 'OCR',
        image: 'Text-to-Image', video: 'Video',
    };

    // ---- Init ----
    async function init() {
        setupNavigation();
        setupClock();
        setupThemeToggle();
        setupRefreshInterval();
        setupRefreshLogsBtn();
        await loadConfig();
        // 品牌必须先于其它渲染：它同时决定浏览器标题与 favicon
        await loadBrand();
        await loadEngines();
        await refreshAll();
        startAutoRefresh();
        populateLogModelSelect();
    }

    // ---- Navigation ----
    function setupNavigation() {
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', e => {
                e.preventDefault();
                const tab = item.dataset.tab;
                document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
                item.classList.add('active');
                document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
                document.getElementById('tab-' + tab).classList.add('active');
                if (tab === 'launch') { msOnEnter(); }
                if (tab === 'router') rtRefresh();
                if (tab === 'playground') renderPlayground();
                if (tab === 'logs') { window.lcLoadSources(); window.lcRefresh(); }
                if (tab === 'settings') renderSettings();
                if (tab === 'tools') window.renderTools();
            });
        });
    }

    function setupClock() {
        function tick() {
            const el = document.getElementById('clock');
            if (el) el.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
        }
        tick();
        setInterval(tick, 1000);
    }

    function setupRefreshLogsBtn() {
        // 日志中心不需要按钮绑定（按钮直接调 window.lcRefresh）
        const sel = document.getElementById('lcSource');
        if (sel) window.lcLoadSources();
    }

    // ---- Toast ----
    function toast(msg, type, ms) {
        type = type || 'info';
        const container = document.getElementById('toastContainer');
        if (!container) return;
        const el = document.createElement('div');
        el.className = 'toast toast-' + type;
        el.textContent = msg;
        container.appendChild(el);
        setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, ms || 3000);
    }

    // ---- API ----
    async function apiGet(path) {
        try {
            const resp = await fetch(API_BASE + path);
            updateServerStatus(true);
            return await resp.json();
        } catch (e) {
            updateServerStatus(false);
            console.error('GET ' + path + ':', e);
            return null;
        }
    }

    async function apiPost(path, body) {
        body = body || {};
        try {
            const resp = await fetch(API_BASE + path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            updateServerStatus(true);
            return await resp.json();
        } catch (e) {
            updateServerStatus(false);
            console.error('POST ' + path + ':', e);
            return null;
        }
    }

    async function apiPut(path, body) {
        body = body || {};
        try {
            const resp = await fetch(API_BASE + path, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            updateServerStatus(true);
            return await resp.json();
        } catch (e) {
            updateServerStatus(false);
            console.error('PUT ' + path + ':', e);
            return null;
        }
    }

    async function apiDelete(path) {
        try {
            const resp = await fetch(API_BASE + path, { method: 'DELETE' });
            updateServerStatus(true);
            return await resp.json();
        } catch (e) {
            updateServerStatus(false);
            console.error('DELETE ' + path + ':', e);
            return null;
        }
    }

    function updateServerStatus(online) {
        const dot = document.getElementById('serverStatus');
        const txt = document.getElementById('serverStatusText');
        if (!dot || !txt) return;
        dot.className = 'status-dot ' + (online ? 'online' : 'offline');
        txt.textContent = online ? 'Online' : 'Offline';
    }

    // ---- Config ----
    async function loadConfig() {
        config = await apiGet('/config/models');
    }

    async function loadEngines() {
        engineInfo = await apiGet('/engines');
        engineVersions = await apiGet('/engines/versions');
    }

    // ---- Refresh ----
    async function refreshAll() {
        try {
            instances = (await apiGet('/instances')) || instances;
            systemInfo = (await apiGet('/system')) || systemInfo;
        } catch (e) {
            console.error('Refresh:', e);
        }
        const tab = document.querySelector('.nav-item.active')?.dataset?.tab;
        if (!tab || tab === 'dashboard') { renderDashboard(); renderDashboardDiffTasks(); }
        if (tab === 'launch') {
            const activePane = document.querySelector('.ms-pane.active');
            if (!activePane || activePane.id === 'ms-instances') rmRefresh();
        }
        const el = document.getElementById('lastUpdate');
        if (el) el.textContent = 'Last update: ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
    }

    // ---- 动态刷新间隔 (v0.5) ----
    function currentRefreshMs() {
        const el = document.getElementById('refreshInterval');
        let v = el ? parseInt(el.value) : NaN;
        if (isNaN(v)) return REFRESH_INTERVAL;
        return Math.max(100, Math.min(300000, v)); // 100ms ~ 5min
    }
    function startAutoRefresh() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = setInterval(refreshAll, currentRefreshMs());
    }
    function setupRefreshInterval() {
        const sel = document.getElementById('refreshInterval');
        if (!sel) return;
        const saved = localStorage.getItem('amm-refresh-ms');
        if (saved && Array.from(sel.options).some(o => o.value === saved)) sel.value = saved;
        else sel.value = '500';
        sel.addEventListener('change', () => {
            localStorage.setItem('amm-refresh-ms', sel.value);
            if (refreshTimer) {
                clearInterval(refreshTimer);
                refreshTimer = setInterval(refreshAll, currentRefreshMs());
            }
            toast('刷新间隔已设为 ' + labelMs(currentRefreshMs()), 'success');
        });
    }
    function labelMs(ms) {
        if (ms < 1000) return ms + 'ms';
        if (ms < 60000) return (ms/1000) + 's';
        return Math.round(ms/60000) + 'min';
    }

    // ---- Dashboard ----
    function renderDashboard() {
        renderSystemCards();
        renderDashboardGPU();
        renderDashboardCPUProcs();
        renderDashboardModels();
    }

    // CPU 推理进程监控（PID / 程序 / CPU / 内存 / 线程 / 运行时长）
    function renderDashboardCPUProcs() {
        const box = document.getElementById('dashboardCPUProcs');
        const hint = document.getElementById('cpuProcsHint');
        if (!box) return;
        const procs = (systemInfo && systemInfo.cpu_procs) || [];
        // 本区块只关心「CPU 做推理」的进程；走 GPU 的 llama-server 由上方 GPU 状态展示
        const cpuOnly = procs.filter(p => !p.uses_gpu);
        const gpuCompanions = procs.filter(p => p.uses_gpu);
        if (hint) {
            const totalCpu = cpuOnly.reduce((s, p) => s + (p.cpu_percent || 0), 0);
            const extra = gpuCompanions.length
                ? ` · 另有 ${gpuCompanions.length} 个走 GPU 的推理进程（见上方 GPU 状态）` : '';
            hint.textContent = cpuOnly.length
                ? `（${cpuOnly.length} 个 · 合计 ${totalCpu.toFixed(1)}% CPU${extra}）`
                : extra;
        }
        if (!cpuOnly.length) {
            box.innerHTML = '<div class="cpu-procs-empty">当前没有 CPU 推理进程在用算力（llama-server / llama-tts / vLLM / transformers 等）；GPU 上的推理请看上方 GPU 状态</div>';
            return;
        }
        box.innerHTML = `<div class="cpu-procs-table-wrap"><table class="rm-table cpu-procs-table">
            <thead><tr>
                <th>PID</th><th>程序</th><th>命令行</th><th>CPU</th><th>内存</th><th>线程</th><th>运行时长</th>
            </tr></thead>
            <tbody>${cpuOnly.map(p => `<tr>
                <td class="rm-mono">${p.pid}</td>
                <td>${escapeHtml(p.name || '--')} <span class="eng-pill">CPU</span></td>
                <td class="cpu-proc-cmd" title="${escapeAttr(p.command || '')}">${escapeHtml(commandBasename(p.command || ''))}</td>
                <td><span style="color:${(p.cpu_percent || 0) > 80 ? 'var(--error)' : 'var(--accent)'}">${(p.cpu_percent || 0).toFixed(1)}%</span></td>
                <td>${fmtMb(p.memory_mb)}</td>
                <td>${p.threads || '--'}</td>
                <td>${p.uptime_seconds ? formatUptime(p.uptime_seconds) : '--'}</td>
            </tr>`).join('')}</tbody>
        </table></div>`;
    }

    // 命令行只展示开头一段（模型路径很长），完整内容放 title
    function commandBasename(cmd) {
        const s = String(cmd || '');
        return s.length > 96 ? s.slice(0, 96) + '…' : s;
    }

    function fmtMb(mb) {
        if (mb == null) return '--';
        const v = Number(mb);
        if (v >= 1024) return (v / 1024).toFixed(2) + ' GB';
        return v.toFixed(0) + ' MB';
    }

    function renderDashboardGPU() {
        const container = document.getElementById('dashboardGPU');
        if (!container) return;
        if (!systemInfo || !systemInfo.gpus || !systemInfo.gpus.length) {
            container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">No GPU detected</div>';
            return;
        }
        container.innerHTML = systemInfo.gpus.map(g => `
            <div class="gpu-card dash-gpu-card">
                <div class="gpu-card-name"><span style="color:var(--success);font-size:16px">⬛</span>${escapeHtml(g.name)} <span style="color:var(--text-muted);font-size:11px">GPU#${g.id}</span></div>
                <div class="dash-gpu-grid">
                    <div class="gpu-metric"><span class="gpu-metric-label">计算利用率</span><span class="gpu-metric-value" style="color:var(--accent)">${g.util_sm ?? g.load ?? '--'}%</span><div class="gpu-bar"><div class="gpu-bar-fill load" style="width:${g.util_sm ?? g.load ?? 0}%"></div></div></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">显存带宽</span><span class="gpu-metric-value" style="color:var(--cyan)">${fmtPct(g.util_mem)}</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">编码器</span><span class="gpu-metric-value">${fmtPct(g.util_enc)}</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">解码器</span><span class="gpu-metric-value">${fmtPct(g.util_dec)}</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">显存占用</span><span class="gpu-metric-value" style="color:var(--purple)">${g.memory_percent}%</span><div class="gpu-bar"><div class="gpu-bar-fill mem" style="width:${g.memory_percent}%"></div></div><span style="font-size:10px;color:var(--text-muted)">${g.memory_used_mb.toFixed(0)}/${g.memory_total_mb} MB</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">温度</span><span class="gpu-metric-value" style="color:var(--error)">${g.temperature!=null?g.temperature+'°C':'--'}</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">风扇转速</span><span class="gpu-metric-value">${g.fan_speed!=null?g.fan_speed+'%':'—'}</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">功耗</span><span class="gpu-metric-value">${g.power_draw?(g.power_draw||0).toFixed(0)+'W':'--'}${g.power_limit?' / '+(g.power_limit||0).toFixed(0)+'W':''}</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">SM时钟</span><span class="gpu-metric-value">${g.clocks_sm!=null?g.clocks_sm+'MHz':'--'}</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">显存时钟</span><span class="gpu-metric-value">${g.clocks_mem!=null?g.clocks_mem+'MHz':'--'}</span></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">PCIe</span><span class="gpu-metric-value">Gen${g.pcie||'--'}</span></div>
                </div>
                <div class="dash-gpu-procs">
                    <div class="gpu-procs-title">🖥️ 正在运行的程序 (${(g.running_processes||[]).length})</div>
                    ${(g.running_processes||[]).length ? g.running_processes.map(p=>`<div class="gpu-proc-item" title="${escapeHtml(p.command||p.name)}"><span class="gpu-proc-pid">PID ${escapeHtml(p.pid)}</span><span class="gpu-proc-name">${escapeHtml(p.command||p.name)}</span><span class="gpu-proc-mem">${p.gpu_memory_mb!=null?escapeHtml(p.gpu_memory_mb)+' MB':''}</span></div>`).join('') : '<div class="gpu-proc-empty">无运行程序</div>'}
                </div>
            </div>`).join('');
    }

    // 渲染 diffusers 推理任务 (Dashboard 底部, 实时提交; v0.7)
    async function renderDashboardDiffTasks() {
        const box = document.getElementById('dashboardDiffTasks');
        const title = document.getElementById('diffTasksTitle');
        if (!box) return;
        let tasks = [];
        try {
            const r = await apiGet('/bridge/diffusers/status');
            tasks = (r && r.tasks) || [];
        } catch (e) { tasks = []; }
        if (title) title.style.display = (tasks.length ? '' : 'none');
        if (!tasks.length) { box.innerHTML = ''; return; }
        const now = Date.now() / 1000;
        const fmt = (s) => (s ?? 0).toFixed(1) + 's';
        box.innerHTML = tasks.map(t => {
            let statusHtml;
            if (t.status === 'submitted') { statusHtml = '<span style="color:var(--text-muted)">排队中 ⏱ ' + fmt(now - t.submitted_at) + '</span>'; }
            else if (t.status === 'running') { statusHtml = '<span style="color:var(--accent)">推理中 ⏱ ' + fmt(now - t.submitted_at) + '</span>'; }
            else if (t.status === 'completed') { statusHtml = '<span style="color:var(--success)">✓ 完成 ' + (t.elapsed_total!=null?fmt(t.elapsed_total):'') + '</span>'; }
            else { statusHtml = '<span style="color:var(--error)">✗ 失败</span>'; }
            return `<div class="diff-task-item">
                <span class="diff-task-id">${escapeHtml(t.model_id.toUpperCase())} #${t.seq}</span>
                <span class="diff-task-prompt">${escapeHtml((t.prompt||'').slice(0,50))||''}</span>
                ${statusHtml}
            </div>`;
        }).join('');
    }

    function short(a, n) { n = n||70; return a.length>n ? a.slice(0,n)+'…' : a; }

    function fmtPct(v) { return v==null ? '--' : (v+'%'); }


    function renderSystemCards() {
        if (!systemInfo) return;
        document.getElementById('cpuValue').textContent = systemInfo.cpu_percent + '%';
        document.getElementById('cpuBar').style.width = systemInfo.cpu_percent + '%';
        document.getElementById('memValue').textContent = systemInfo.memory_used_gb + '/' + systemInfo.memory_total_gb + ' GB';
        document.getElementById('memBar').style.width = systemInfo.memory_percent + '%';
        document.getElementById('diskValue').textContent = systemInfo.disk_used_gb + '/' + systemInfo.disk_total_gb + ' GB';
        document.getElementById('diskBar').style.width = systemInfo.disk_percent + '%';
        document.getElementById('uptimeValue').textContent = formatUptime(systemInfo.uptime_seconds);
    }

    /**
     * 统一的 Start / Stop / Restart 按钮组（v1.1）
     * Dashboard Model Status / Running Models / 向导 全部复用，保证行为一致：
     *   - Start  : 非运行态可点，走 /api/instances/{uid}/start
     *   - Stop   : 运行/启动中可点，走 /api/instances/{uid}/stop
     *   - Restart: 始终可点（内部先 stop 再 start）
     */
    function rowActions(uid, status) {
        const sc = status || 'stopped';
        const running = sc === 'running' || sc === 'starting';
        const id = escapeAttr(uid);
        return `
            <button class="btn btn-sm btn-success" onclick="event.stopPropagation();window.startModelWithVariant('${id}')" ${running ? 'disabled' : ''}>Start</button>
            <button class="btn btn-sm btn-danger" onclick="event.stopPropagation();window.stopModel('${id}')" ${running ? '' : 'disabled'}>Stop</button>
            <button class="btn btn-sm" onclick="event.stopPropagation();window.restartModel('${id}')">Restart</button>`;
    }

    // 启动时选择引擎版本 / CPU·CUDA 构建
    // 详情弹窗：加载/应用「引擎构建（版本 · flavor）」
    async function loadModalVariants(modelId) {
        const sel = document.getElementById('modalVariant');
        if (!sel) return;
        const inst = instances[modelId] || {};
        try {
            const v = await apiGet('/instances/' + encodeURIComponent(modelId) + '/variants');
            if (!v || v.error) { sel.innerHTML = '<option value="">（无可用构建）</option>'; return; }
            const list = v.variants || [];
            if (!list.length) { sel.innerHTML = '<option value="">（无可用构建）</option>'; return; }
            const cur = v.current || {};
            const curVer = (cur.version || '').trim();
            // 当前没指定版本时，按实例回填的值猜一个默认选中项
            const instVer = (inst.engine_version || '').trim();
            const instFl = (inst.flavor || '').trim();
            sel.innerHTML = list.map(x => {
                const isCur = (curVer && x.version === curVer && (!cur.flavor || x.flavor === cur.flavor))
                    || (!curVer && instVer && x.version === instVer && instFl && x.flavor === instFl);
                const tag = x.installed ? '' : '（未安装）';
                return `<option value="${escapeAttr(x.version)}|${escapeAttr(x.flavor)}" ${isCur ? 'selected' : ''} ${x.installed ? '' : 'disabled'}>${escapeHtml(x.label)}${tag}</option>`;
            }).join('');
            sel.dataset.loaded = '1';
        } catch (e) {
            sel.innerHTML = '<option value="">（加载失败）</option>';
        }
    }

    async function modalApplyVariant(modelId) {
        const sel = document.getElementById('modalVariant');
        const inst = instances[modelId];
        if (!sel || !inst) return;
        const raw = sel.value || '';
        if (!raw) { toast('请选择构建', 'info'); return; }
        const [version, flavor] = raw.split('|');
        const same = (inst.engine_version || '').trim() === version &&
                     (inst.flavor || '').trim() === flavor;
        if (same) { toast('构建未变化'); return; }

        const running = (inst.status === 'running');
        if (running && !confirm(
            `实例 ${modelId} 正在运行，切换构建会停止并按 ${version} · ${flavor} 重启。继续？`)) return;

        // 通过启动接口带上 version/flavor，后端会写入实例并在需要时重启
        if (running) {
            await apiPost('/instances/' + encodeURIComponent(modelId) + '/stop');
        }
        const r = await apiPost('/instances/' + encodeURIComponent(modelId) + '/start',
                                { version, flavor });
        if (r && r.success) {
            toast(`✅ 已切换为 ${version} · ${flavor}`, 'success');
            if (instances[modelId]) {
                instances[modelId].engine_version = version;
                instances[modelId].flavor = flavor;
            }
            closeModal();
            if (typeof refreshAll === 'function') refreshAll();
        } else {
            toast('❌ ' + ((r && r.error) || '切换失败'), 'error');
        }
    }

    async function startModelWithVariant(uid) {
        const v = await apiGet('/instances/' + encodeURIComponent(uid) + '/variants');
        if (!v || v.error) { await startModel(uid); return; }
        const list = (v.variants || []).filter(x => x.installed);
        if (!list.length) { await startModel(uid); return; }

        // 只有一种可用构建时直接启动，不打断用户
        const cur = v.current || {};
        const curMatch = list.find(x =>
            (!cur.version || x.version === cur.version) &&
            (!cur.flavor || x.flavor === cur.flavor));
        if (list.length === 1 && curMatch) { await startModel(uid); return; }

        const modal = document.getElementById('modelModal');
        const title = document.getElementById('modalTitle');
        const body = document.getElementById('modalBody');
        if (!modal || !body) { await startModel(uid); return; }
        title.textContent = '启动 ' + uid;
        body.innerHTML = `
            <div class="kv-grid">
                <div class="field span-2"><label>引擎构建 <span class="hint">（版本 · CPU/CUDA）</span></label>
                    <select id="svPick" class="form-select">
                        ${list.map(x => {
                            const sel = (curMatch && x.version === curMatch.version && x.flavor === curMatch.flavor) ? 'selected' : '';
                            return `<option value="${escapeAttr(x.version)}|${escapeAttr(x.flavor)}" ${sel}>${escapeHtml(x.label)}</option>`;
                        }).join('')}
                    </select>
                </div>
            </div>
            <p class="hint" style="font-size:12px">CUDA 构建需要 GPU 与对应 CUDA 运行时；CPU 构建不占显存。</p>
            <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
                <button class="btn btn-sm" onclick="window.closeModal()">取消</button>
                <button class="btn btn-sm btn-success" onclick="window.__doStart('${escapeAttr(uid)}')">▶ 启动</button>
            </div>`;
        modal.classList.add('active');
    }

    async function __doStart(uid) {
        const el = document.getElementById('svPick');
        const [version, flavor] = (el?.value || '').split('|');
        closeModal();
        toast('正在启动 ' + uid + '…');
        const r = await apiPost('/instances/' + encodeURIComponent(uid) + '/start',
                                { version, flavor });
        if (r && r.success) toast('✅ 已启动 ' + uid, 'success');
        else toast('❌ ' + ((r && r.error) || '启动失败'), 'error');
        if (typeof refreshAll === 'function') refreshAll();
        else if (typeof rmRefresh === 'function') rmRefresh();
    }

    // Dashboard Model Status：按类别给出可选引擎
    const CATEGORY_ENGINES = {
        chat: ['llama_cpp', 'vllm'],
        embedding: ['llama_cpp', 'vllm', 'sentence_transformers'],
        reranker: ['llama_cpp', 'sentence_transformers'],
        asr: ['llama_cpp'],
        tts: ['llama_cpp'],
        ocr: ['llama_cpp'],
        image: ['diffusers'],
        video: ['diffusers'],
        t2i: ['diffusers'], t2v: ['diffusers'], i2v: ['diffusers'],
    };

    function engineOptions(inst) {
        const cat = inst.category || 'chat';
        const list = CATEGORY_ENGINES[cat] || ['llama_cpp'];
        const cur = inst.engine_type || list[0];
        const all = list.includes(cur) ? list : [cur, ...list];
        return all.map(e =>
            `<option value="${escapeAttr(e)}" ${e === cur ? 'selected' : ''}>${escapeHtml(e)}</option>`
        ).join('');
    }

    async function dashSetEngine(uid, engine) {
        const inst = instances[uid];
        if (!inst) return;
        if (inst.status === 'running') {
            if (!confirm(`实例 ${uid} 正在运行，切换引擎会先停止再按新引擎重启。继续？`)) {
                renderDashboardModels();
                return;
            }
        }
        const r = await apiPut('/instances/' + encodeURIComponent(uid) + '/engine',
                               { engine_type: engine, restart: inst.status === 'running' });
        if (r && r.success) {
            toast(`✅ 引擎已切换为 ${engine}`, 'success');
            if (instances[uid]) instances[uid].engine_type = engine;
        } else {
            toast('❌ ' + ((r && r.error) || '切换失败'), 'error');
        }
        if (typeof refreshAll === 'function') refreshAll();
        else if (typeof renderDashboardModels === 'function') renderDashboardModels();
    }

    // Dashboard Model Status：列表式（v1.1，替代 3x3 卡片）
    function renderDashboardModels() {
        const container = document.getElementById('dashboardModels');
        if (!container) return;

        // 9 类固定场景 + 动态实例，统一成一份行数据
        const fixedIds = ['chat', 'embedding', 'reranker', 'ocr', 'asr', 'tts', 't2i', 't2v', 'i2v'];
        const rows = [];
        fixedIds.forEach(id => {
            const inst = instances[id];
            if (!inst) return;
            rows.push({ uid: id, inst, dynamic: false });
        });
        Object.values(instances || {}).forEach(inst => {
            if (!inst || !inst.is_dynamic) return;
            if (rows.some(r => r.uid === inst.model_id)) return;
            rows.push({ uid: inst.model_id, inst, dynamic: true });
        });

        if (!rows.length) {
            container.innerHTML = '<div class="tpl-empty">暂无模型实例</div>';
            return;
        }

        const head = `<div class="model-row head">
            <span>模型</span>
            <span class="col-cat">类别</span>
            <span class="hide-md">引擎</span>
            <span class="hide-sm">端口</span>
            <span>状态</span>
            <span class="hide-md">显存</span>
            <span class="hide-md">运行时长</span>
            <span style="text-align:right">操作</span>
        </div>`;

        const body = rows.map(({ uid, inst, dynamic }) => {
            const sc = inst.status || 'stopped';
            const stCls = ['running', 'starting', 'error'].includes(sc) ? sc : 'stopped';
            const icon = categoryIcons[inst.category] || '🤖';
            // 引擎列：llama_cpp@b12381 [cuda]
            // flavor 用带色徽章，cuda 绿色 / cpu 灰蓝，一眼分清跑在哪
            const fl = (inst.flavor || '').trim();
            const flCls = fl === 'cuda' ? 'flavor-cuda' : (fl === 'cpu' ? 'flavor-cpu' : '');
            const engineLabel = escapeHtml(inst.engine_type || '--') +
                (inst.engine_version ? `<span style="color:var(--text-muted)">@${escapeHtml(inst.engine_version)}</span>` : '') +
                (fl ? ` <span class="eng-pill ${flCls}">${escapeHtml(fl)}</span>` : '');
            return `<div class="model-row" onclick="window.openModelDetail('${escapeAttr(uid)}')" style="cursor:pointer">
                <span class="col-name" title="${escapeAttr(inst.name || uid)}">
                    ${icon}<span class="nm">${escapeHtml(inst.name || uid)}</span>
                    ${dynamic ? '<span class="eng-pill">动态</span>' : ''}
                </span>
                <span class="col-cat"><span class="rm-badge badge-${escapeHtml(inst.category)}">${escapeHtml(inst.category)}</span></span>
                <span class="mono hide-md" title="${escapeAttr(inst.engine_type || '')}">${engineLabel}</span>
                <span class="mono hide-sm">${inst.port || '--'}</span>
                <span class="col-status"><span class="status-indicator ${stCls}"></span><span class="stxt">${escapeHtml(sc)}</span></span>
                <span class="mono hide-md">${inst.gpu_memory_mb ? Math.round(inst.gpu_memory_mb) + ' MB' : '--'}</span>
                <span class="mono hide-md">${inst.uptime_seconds ? formatUptime(inst.uptime_seconds) : '--'}</span>
                <span class="col-actions">${rowActions(uid, sc)}</span>
            </div>`;
        }).join('');

        container.innerHTML = head + body;
    }


    // t2i 参数/高级设置变化时刷新显存估算
    function bindParamChange(modelId) {
        const sel = document.querySelectorAll(
            `input[id^=param-${modelId}-], select[id^=param-${modelId}-], select[id^=adv-${modelId}-], input[id^=adv-${modelId}-]`
        );
        sel.forEach(el => el.addEventListener('input', refreshT2iVramModels));
        sel.forEach(el => el.addEventListener('change', refreshT2iVramModels));
    }

    // ---- 显存估算 (diffusers T2I / Qwen-Image-25B) v0.7.2 ----
    function _dtypeBytes(q) { return (q==='fp8'||q==='float8')?1:((q==='bf16'||q==='fp16'||q==='float16'||q==='bfloat16')?2:4); }
    function estimateT2iVram(d) {
        // d: { quant, compute_dtype, offload, width, height, num_images, num_steps }
        const width = Math.max(512, d.width||1024);
        const height = Math.max(512, d.height||1024);
        const nimg = Math.min(Math.max(1,d.num_images||1),4);
        const steps = Math.max(1,d.num_steps||28);
        const quant = (d.quant||'fp8').toLowerCase();
        const compute = (d.compute_dtype||'bf16').toLowerCase();
        const offload = (d.offload||'gpu').toLowerCase();

        const storeB = _dtypeBytes(quant);    // 权重存储字节
        const actB = _dtypeBytes(compute);     // 激活字节
        const paramB = 26;                      // Qwen-Image-25B 近似总参 (含 T5-ish text encoder + transformer + VAE)
        const REF_LATENT = 16384;              // 1024x1024 -> latent 128x128 tokens
        const BASE_ACT_GB = 9.0;               // 1MP (1024x1024) 时的激活峰值估算 (GB)

        // 1) 静态权重 (存 GPU by default, offload 时部分下 CPU)
        const weights = paramB * storeB;         // GB  -> FP8 26GB / BF16 52GB / FP32 104GB

        // 2) 激活: 随 latent tokens 线性 + num_images 系数 + compute 精度 + 步数慢增
        const latentToken = (width/8)*(height/8);
        const actScale = latentToken / REF_LATENT;
        const mult = 1 + 0.08*(nimg-1);
        const computeFactor = actB/2;           // bf16/fp16=1, fp32=2
        const stepFactor = 1 + 0.004*steps;
        const act = BASE_ACT_GB * actScale * mult * computeFactor * stepFactor;

        // 3) 固定开销
        const fixed = 2.0;   // GB: CUDA context, 临时张量

        // 4) offload 策略对显存驻留的影响
        let resident = weights + act + fixed;
        if (offload==='group' || offload==='model') resident *= 0.55;   // CPU offload 显著降低驻留

        const mb = Math.round(resident*1024);
        return {
            mb,
            gb: +(resident).toFixed(1),
            weights_mb: Math.round(weights*1024),
            act_mb: Math.round(act*1024),
            fixed_mb: Math.round(fixed*1024),
            offload,
            is_oom: resident > 84.0,
            note: `存储${quant.toUpperCase()}·计算${compute.toUpperCase()}·offload=${offload}`,
        };
    }

    function fmtMb(mb) { return mb>=1024 ? (mb/1024).toFixed(1)+' GB' : mb+' MB'; }

    // ---- 显存估算 (diffusers video / Wan2.2-A14B) v0.7.3 ----
    function estimateVideoVram(d) {
        const quant = (d.quant||'fp8').toLowerCase();
        const compute = (d.compute_dtype||'bf16').toLowerCase();
        const offload = (d.offload||'gpu').toLowerCase();
        const res = String(d.resolution||'480p');
        const frames = Math.max(1, d.num_frames||81);
        const steps = Math.max(1, d.num_steps||50);
        const storeB = _dtypeBytes(quant);
        const weights = 54 * (storeB === 1 ? 0.5 : (storeB === 2 ? 1 : 2)); // FP8~27G, BF16~54G, FP32~110G
        // 激活: 随帧数线性 + 分辨率(720p=2.5x) + 计算精度 + 步数线性
        const actBase = res==='720p' ? 30 : 12;       // 81帧基准激活(GB)
        const actGB = actBase * (frames/81) * (res==='720p'?2.5:1.0) * (compute==='fp32'?1.8:1.0) * (1+0.003*steps);
        const fixedGB = 4.0; // CUDA context + VAE + text encoder 常驻
        let resident = weights + actGB + fixedGB;
        if (offload==='group' || offload==='model') resident *= 0.55;
        return { mb: Math.max(1024, Math.round(resident*1024)), gb: +resident.toFixed(1),
            weights_mb: Math.round(weights*1024), act_mb: Math.round(actGB*1024), fixed_mb: Math.round(fixedGB*1024),
            offload, is_oom: resident > 85.0,
            note: `${res}·${frames}f·${steps}步 ${quant.toUpperCase()}/${compute.toUpperCase()}/offload=${offload}` };
    }

    function renderParamInput(modelId, param, inst) {
        const val = (inst && inst.parameters && inst.parameters[param.name] !== undefined) ? inst.parameters[param.name] : param.default;
        let input;
        if (param.type === 'boolean') {
            input = `<label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="param-${modelId}-${param.name}" ${val ? 'checked' : ''} style="accent-color:var(--accent)"> ${param.label}</label>`;
        } else if (param.type === 'select') {
            input = `<select class="form-select" id="param-${modelId}-${param.name}">${(param.options || []).map(o => `<option value="${o}" ${o === val ? 'selected' : ''}>${o}</option>`).join('')}</select>`;
        } else if (param.type === 'float') {
            input = `<input type="number" class="form-input" id="param-${modelId}-${param.name}" value="${val}" min="${param.min || 0}" max="${param.max || 100}" step="${param.step || 0.1}">`;
        } else {
            input = `<input type="${param.type === 'number' ? 'number' : 'text'}" class="form-input" id="param-${modelId}-${param.name}" value="${val}" ${param.min != null ? 'min="' + param.min + '"' : ''} ${param.max != null ? 'max="' + param.max + '"' : ''}>`;
        }
        return `<div class="param-item"><label>${param.label}${param.label_en && param.label_en !== param.label ? ' <span style="opacity:.6">(' + param.label_en + ')</span>' : ''}${param.description ? '<span class="param-desc">' + param.description + '</span>' : ''}</label>${input}</div>`;
    }

    function toggleDetailBody(id) {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.toggle('expanded');
        if (el.classList.contains('expanded')) {
            const mid = id.replace('body-', '');
            loadStartupState(mid);
        }
    }

    // ---- 启动命令编排 (v0.6) ----
    function renderStartupCard(id, key) {
        return `<div class="startup-card" style="margin-top:16px;border-top:1px solid var(--border);padding-top:14px;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
                <h4 style="font-size:14px;margin:0;">🚀 启动命令编排 (人工加载命令行)</h4>
                <span id="startup-state-${id}" style="font-size:11px;color:var(--text-muted)"></span>
            </div>
            <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px;">
                基于上方参数点击“按参数生成”预览启动命令行；可手动修改，点“保存为启动脚本”写入 <code>scripts/${id}.sh</code>；
                保存后 Start 将直接执行该脚本启动推理服务。
            </div>
            <textarea id="startup-cmd-${id}" rows="4" spellcheck="false"
                style="width:100%;font-family:monospace;font-size:11px;padding:8px 10px;background:var(--bg-input);border:1px solid var(--border);border-radius:6px;color:var(--text);line-height:1.5;resize:vertical;"
                placeholder="点击「生成启动命令」自动填入，或直接粘贴/编写命令行..."></textarea>
            <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
                <button class="btn btn-sm" onclick="generateStartupCmd('${id}')">⚙️ 生成启动命令</button>
                <button class="btn btn-sm btn-primary" onclick="saveStartupCmd('${id}')">💾 保存为启动脚本</button>
                <button class="btn btn-sm" onclick="clearStartupCmd('${id}')">🗑 清除自定义</button>
                <button class="btn btn-sm btn-warning" onclick="restartModel('${id}')">🔄 Restart (执行脚本)</button>
            </div>
        </div>`;
    }

    async function loadStartupState(id) {
        try {
            const r = await apiGet('/instances/' + id + '/command');
            const el = document.getElementById('startup-state-' + id);
            const ta = document.getElementById('startup-cmd-' + id);
            if (el && r) el.textContent = r.has_custom ? '状态: 自定义命令 ✍️' : '状态: 自动生成(未自定义)';
            if (ta && r && r.startup_command) ta.value = r.startup_command;
        } catch (e) { console.error('startup state', e); }
    }

    async function generateStartupCmd(id) {
        const ta = document.getElementById('startup-cmd-' + id);
        try {
            const r = await apiPost('/instances/' + id + '/command/preview', {});
            if (r && r.shell) ta.value = r.shell;
            else toast('生成失败: ' + (r.error || '未知'));
        } catch (e) { toast('生成失败: ' + e.message); }
    }

    async function saveStartupCmd(id) {
        const ta = document.getElementById('startup-cmd-' + id);
        if (!ta || !ta.value.trim()) { toast('启动命令为空'); return; }
        try {
            const r = await apiPut('/instances/' + id + '/command', { command: ta.value });
            toast(r.success ? '已保存为启动脚本' : '保存失败: ' + (r.error || ''));
            loadStartupState(id);
        } catch (e) { toast('保存失败: ' + e.message); }
    }

    // apiDelete 需新建 helper (已在上方新增); 清除自定义启动命令
    async function clearStartupCmd(id) {
        if (!confirm('清除自定义启动命令并恢复自动生成？')) return;
        try {
            await apiDelete('/instances/' + id + '/command');
            const ta = document.getElementById('startup-cmd-' + id);
            if (ta) ta.value = '';
            loadStartupState(id);
            toast('已清除自定义启动命令');
        } catch (e) { toast('清除失败: ' + e.message); }
    }

    // ---- Diffusers Advanced Settings (FP8 quant / offload) ----
    async function renderAdvancedSettings(modelId) {
        const wrap = document.getElementById('adv-wrap-' + modelId);
        if (!wrap) return "";
        const defaults = {
            quant: '',          // 空 / fp8 / bf16 / none
            compute_dtype: 'bf16',
            boundary_ratio: '', // 空 = 自动 (Wan2.2 -> 0.875)
            cpu_offload: false,
            offload: 'gpu',      // gpu / model / group
        };
        try {
            const r = await apiGet('/instances/' + modelId + '/advanced');
            if (r && r.settings) {
                Object.assign(defaults, {
                    quant: r.settings.quant || '',
                    compute_dtype: r.settings.compute_dtype || 'bf16',
                    boundary_ratio: (r.settings.boundary_ratio === null || r.settings.boundary_ratio === undefined) ? '' : r.settings.boundary_ratio,
                    cpu_offload: !!r.settings.cpu_offload,
                    offload: r.settings.offload || 'gpu',
                });
            }
        } catch (e) {
            console.warn('renderAdvancedSettings load failed', e);
        }
        const id = `adv-${modelId}`;
        wrap.innerHTML = `
            <div class="advanced-settings" style="margin-top:18px;padding:14px;background:var(--bg-input);border-radius:var(--radius);border:1px solid var(--border);">
                <h4 style="font-size:13px;margin-bottom:8px;color:var(--text-secondary);">🧠 Advanced (Diffusers / FP8 量化)</h4>
                <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px;">
                    变更后需 Restart 模型生效。FP8 存储 + BF16 计算, 84G 显存可跑 27B MoE (Wan2.2-A14B)
                </div>
                <div class="params-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                    <div class="param-item"><label>Quant 存储精度<span class="param-desc">fp8=降显存(推荐); bf16=全精度; none=关闭</span></label>
                        <select class="form-select" id="${id}-quant">
                            <option value="" ${defaults.quant===''?'selected':''}>(自动 - 默认)</option>
                            <option value="fp8" ${defaults.quant==='fp8'?'selected':''}>fp8 (FP8 存储, BF16 计算) ⭐</option>
                            <option value="bf16" ${defaults.quant==='bf16'?'selected':''}>bf16 (BF16 全精度)</option>
                            <option value="none" ${defaults.quant==='none'?'selected':''}>none (禁用量化)</option>
                        </select>
                    </div>
                    <div class="param-item"><label>Compute Dtype<span class="param-desc">前向计算精度</span></label>
                        <select class="form-select" id="${id}-compute_dtype">
                            <option value="bf16" ${defaults.compute_dtype==='bf16'?'selected':''}>bf16 ⭐</option>
                            <option value="fp16" ${defaults.compute_dtype==='fp16'?'selected':''}>fp16</option>
                            <option value="fp32" ${defaults.compute_dtype==='fp32'?'selected':''}>fp32</option>
                        </select>
                    </div>
                    <div class="param-item"><label>Boundary Ratio<span class="param-desc">Wan2.2 MoE 双专家切换点 (SNR)</span></label>
                        <input type="number" class="form-input" id="${id}-boundary_ratio" value="${defaults.boundary_ratio}" min="0" max="1" step="0.001" placeholder="(自动 0.875)">
                    </div>
                    <div class="param-item"><label>CPU Offload<span class="param-desc">显存仍不够时启用, 按叶子切 CPU/GPU</span></label>
                        <label style="display:flex;align-items:center;gap:6px;margin-top:6px;">
                            <input type="checkbox" id="${id}-cpu_offload" ${defaults.cpu_offload?'checked':''} style="accent-color:var(--accent)"> 启用
                        </label>
                    </div>
                    <div class="param-item"><label>Offload 策略<span class="param-desc">gpu=全驻显存(推荐); model=序列CPU offload; group=叶子offload</span></label>
                        <select class="form-select" id="${id}-offload">
                            <option value="gpu" ${!defaults.offload || defaults.offload==='gpu'?'selected':''}>gpu (全驻 GPU) ⭐</option>
                            <option value="model" ${defaults.offload==='model'?'selected':''}>model (enable_model_cpu_offload)</option>
                            <option value="group" ${defaults.offload==='group'?'selected':''}>group (leaf_level offload)</option>
                        </select>
                    </div>
                </div>
                <div style="margin-top:12px;display:flex;gap:8px;">
                    <button class="btn btn-primary btn-sm" onclick="saveAdvancedSettings('${modelId}')">Save Advanced</button>
                    <button class="btn btn-sm" onclick="restartModel('${modelId}')">🔄 Restart Now</button>
                </div>
            </div>`;
        if (modelId === 't2i') refreshT2iVramModels();
        bindParamChange(modelId);
    }

    async function saveAdvancedSettings(modelId) {
        const id = `adv-${modelId}`;
        const settings = {
            quant: document.getElementById(`${id}-quant`)?.value || '',
            compute_dtype: document.getElementById(`${id}-compute_dtype`)?.value || '',
            boundary_ratio: document.getElementById(`${id}-boundary_ratio`)?.value || '',
            cpu_offload: document.getElementById(`${id}-cpu_offload`)?.checked || false,
            offload: document.getElementById(`${id}-offload`)?.value || 'gpu',
        };
        // 空字符串转 null (后端会接受并写 None)
        if (settings.quant === '') settings.quant = '';
        if (settings.boundary_ratio === '') settings.boundary_ratio = '';
        const r = await apiPut('/instances/' + modelId + '/advanced', settings);
        if (r && r.success) {
            toast('✅ Advanced settings saved for ' + modelId, 'success');
        } else {
            toast('❌ ' + (r && r.error || 'Failed'), 'error');
        }
    }

    // ---- Engine Selection ----
    async function selectEngine(modelId, engineType) {
        const r = await apiPut('/instances/' + modelId + '/engine', { engine_type: engineType, engine_version: '' });
        if (r && r.success) {
            toast('✅ ' + modelId + ' engine → ' + engineType, 'success');
            await loadConfig();
            await refreshAll();
        } else {
            toast('❌ ' + (r && r.error || 'Failed'), 'error');
        }
    }

    // ---- Model Actions ----
    async function startModel(modelId) {
        setModelBusy(modelId, true);
        toast('Starting ' + modelId + '...');
        try {
            const r = await apiPost('/instances/' + modelId + '/start');
            if (r && r.error) { toast('❌ ' + r.error, 'error'); }
            else if (r && r.success) {
                toast('✅ ' + modelId + ' started (' + (r.engine || '') + ')', 'success');
                // 启动后轮询等待进入 running, 并抓取实时 GPU/CPU
                await waitModelStatus(modelId, 'running', 60000);
            }
        } finally { setModelBusy(modelId, false); }
        await refreshAll();
    }

    async function stopModel(modelId) {
        setModelBusy(modelId, true);
        toast('⏹ Stopping ' + modelId + '...');
        try {
            const r = await apiPost('/instances/' + modelId + '/stop');
            if (r && r.error) { toast('❌ ' + r.error, 'error'); }
            else {
                toast('✅ ' + modelId + ' stopped', 'success');
            }
            await fastRefresh();
        } finally { setModelBusy(modelId, false); }
        await refreshAll();
    }

    async function restartModel(modelId) {
        setModelBusy(modelId, true);
        toast('🔄 Restarting ' + modelId + '...');
        try {
            const r = await apiPost('/instances/' + modelId + '/restart');
            if (r && r.error) toast('❌ ' + r.error, 'error');
            else toast('🔄 ' + modelId + ' restarted', 'success');
        } finally { setModelBusy(modelId, false); }
        await refreshAll();
    }

    // 按钮 loading 态: 禁用该模型所有操作按钮, 防止重复点击/无双反馈
    function setModelBusy(modelId, busy) {
        document.querySelectorAll('[data-mid="' + modelId + '"]').forEach(b => {
            b.disabled = busy;
            b.textContent = busy ? (b.dataset.action === 'stop' ? '⏳停止中' : '⏳' + (b.dataset.action||'').toUpperCase()) : b.dataset.label;
        });
        if (busy) toast('⏳ ' + modelId + ' 处理中...');
    }

    // 轮询等待模型进入指定状态 (start 用); 拒绝状态返回 false
    async function waitModelStatus(modelId, want, timeoutMs) {
        const timeout = timeoutMs || 60000;
        const start = Date.now();
        while (Date.now() - start < timeout) {
            const inst = (await apiGet('/instances')) || {};
            const it = inst[modelId];
            if (it && it.status === want) return true;
            if (it && (it.status === 'error' || it.status === 'failed')) { toast('❌ ' + modelId + ' 启动失败: ' + (it.error || ''), 'error'); return false; }
            await new Promise(r => setTimeout(r, 1500));
        }
        toast('⏱ ' + modelId + ' 状态等待超时', 'info');
        return false;
    }

    // 快速轮询一次 (force 重新拉取 instances+system 以刷新 GPU/CPU)
    async function fastRefresh() {
        try {
            instances = (await apiGet('/instances')) || instances;
            systemInfo = (await apiGet('/system')) || systemInfo;
        } catch (e) {}
        renderDashboard();
    }

    async function selectModelFile(modelId, file) {
        const r = await apiPut('/instances/' + modelId + '/model-file', { model_file: file });
        if (r && r.success) toast('📁 Model file updated');
    }

    async function saveParameters(modelId) {
        const params = {};
        document.querySelectorAll('[id^="param-' + modelId + '-"]').forEach(el => {
            const name = el.id.replace('param-' + modelId + '-', '');
            params[name] = el.type === 'checkbox' ? el.checked : (el.type === 'number' ? parseFloat(el.value) : el.value);
        });
        const r = await apiPut('/instances/' + modelId + '/parameters', params);
        toast(r && r.success ? ('✅ Parameters saved for ' + modelId) : ('❌ Failed'), r && r.success ? 'success' : 'error');
    }

    function viewLogs(modelId) {
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        document.querySelector('[data-tab="logs"]').classList.add('active');
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        document.getElementById('tab-logs').classList.add('active');
        window.lcLoadSources().then(() => {
            const sel = document.getElementById('lcSource');
            if (sel) sel.value = modelId;
            window.lcRefresh();
        });
    }

    // ---- VRAM 估算 (Models 页 t2i) v0.7.2 ----
    async function refreshT2iVramModels() {
        const box = document.getElementById('t2i-vram-models');
        if (!box) return;
        let adv = { quant: 'fp8', compute_dtype: 'bf16', offload: 'gpu' };
        try { const r = await apiGet('/instances/t2i/advanced'); if (r && r.settings) adv = r.settings || {}; } catch(e) {}
        const get = (id, dflt) => { const el = document.getElementById('param-t2i-'+id); return el ? (el.type==='checkbox'?el.checked:parseFloat(el.value)) : dflt; };
        const est = estimateT2iVram({
            quant: adv.quant || '', compute_dtype: adv.compute_dtype || 'bf16', offload: adv.offload || 'gpu',
            width: get('width', 1024), height: get('height', 1024),
            num_images: get('num_images', 1), num_steps: get('num_inference_steps', 28),
        });
        const color = est.is_oom ? 'var(--error)' : 'var(--success)';
        box.innerHTML = `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px;">
            <span>💾 估算显存: <strong style="color:${color};font-size:15px">${fmtMb(est.mb)}</strong></span>
            <span style="color:var(--text-muted)">权重${fmtMb(est.weights_mb)} + 激活${fmtMb(est.act_mb)} + 固定${fmtMb(est.fixed_mb)}</span>
            ${est.is_oom ? '<span style="color:var(--error)">⚠ 超出 85G 显存! 降低分辨率/改 offload</span>' : ''}
            <span style="color:var(--text-muted)">${est.note}</span>
        </div>`;
    }

    // ---- GPU ----
    function renderGPU() {
        if (!systemInfo || !systemInfo.gpus) return;
        const container = document.getElementById('gpuGrid');
        if (!container) return;
        if (!systemInfo.gpus.length) {
            container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-muted)">No GPU detected</div>';
            return;
        }
        container.innerHTML = systemInfo.gpus.map(g => `
            <div class="gpu-card">
                <div class="gpu-card-name"><span style="color:var(--success);font-size:18px">⬛</span>${g.name}</div>
                <div class="gpu-metrics">
                    <div class="gpu-metric"><span class="gpu-metric-label">GPU Load</span><span class="gpu-metric-value" style="color:var(--accent)">${g.load}%</span><div class="gpu-bar"><div class="gpu-bar-fill load" style="width:${g.load}%"></div></div></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">Memory</span><span class="gpu-metric-value" style="color:var(--purple)">${g.memory_used_mb.toFixed(0)}/${g.memory_total_mb} MB</span><div class="gpu-bar"><div class="gpu-bar-fill mem" style="width:${g.memory_percent}%"></div></div></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">Temperature</span><span class="gpu-metric-value" style="color:var(--error)">${g.temperature}°C</span><div class="gpu-bar"><div class="gpu-bar-fill temp" style="width:${g.temperature / 100 * 100}%"></div></div></div>
                    <div class="gpu-metric"><span class="gpu-metric-label">Power</span><span class="gpu-metric-value">${(g.power_draw || 0).toFixed(0)}W / ${(g.power_limit || 0).toFixed(0)}W</span></div>
                </div>
            </div>`).join('');
    }

    // 旧日志下拉已由 Log Center 取代（保留函数以免外部引用报错）
    function populateLogModelSelect() {
        window.lcLoadSources();
    }

    // 日志行着色器: 支持 vLLM/llama.cpp/引擎/服务多格式, 按级别着色, 提升对比度
    const LOG_LEVEL_COLOR = {
        'DEBUG': '#8b90a8',
        'INFO': '#7dd3fc',
        'WARNING': '#fbbf24',
        'WARN': '#fbbf24',
        'ERROR': '#f87171',
        'CRITICAL': '#f87171',
    };
    const TS_RE = /(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?|\d{2}-\d{2} \d{2}:\d{2}:\d{2})/;
    // 级别优先匹配 [INFO] / ERROR: 这类显式标注；
    // 避免把 "aiohttp.access" 里的字样或模型名误判成级别。
    const LEVEL_RE = /(?:\[(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\]|^\s*(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b|\b(ERROR|CRITICAL|FATAL|WARNING)\b[::])/;
    // 日志正文里给 HTTP 状态码与常见路径着色（先转义再替换，安全）
    function highlightHttp(text) {
        let out = escapeHtml(text);
        out = out.replace(/\b(5\d\d)\b/g, '<span class="http-5xx">$1</span>');
        out = out.replace(/\b(4\d\d)\b(?!\s*ms)/g, '<span class="http-4xx">$1</span>');
        out = out.replace(/\b(2\d\d)\b/g, '<span class="http-2xx">$1</span>');
        return out;
    }

    function renderLogLines(logs) {
        if (!logs || !logs.length) return '(empty)';
        return logs.map(function (line) {
            const raw = String(line);
            if (!raw.trim()) return '';
            const trimmed = raw.replace(/^\s+/, '');
            const isStack = /^(\s+|\([^)]*pid=\d+\)|Process |Traceback|File "|\s+File |\s+raise|\s+return|\s+\^+)/.test(raw);
            const lvMatch = trimmed.match(LEVEL_RE);
            const lvGroup = lvMatch ? (lvMatch[1] || lvMatch[2] || lvMatch[3] || '') : '';
            const lv = lvGroup ? lvGroup.toUpperCase() : (isStack ? 'TRACE' : '');
            const tsMatch = trimmed.match(TS_RE);
            // 堆栈: 灰但更亮, 保留堆栈感
            if (isStack && !lv) {
                if (/Traceback|Process |^File|\^+$/.test(raw)) {
                    return '<div class="log-line log-stack-head">' + escapeHtml(raw) + '</div>';
                }
                return '<div class="log-line log-stack">' + escapeHtml(raw) + '</div>';
            }
            if (lv) {
                let head = '';
                let rest = raw;
                if (tsMatch) {
                    // 把时间戳单独高亮
                    const tsInline = raw.indexOf(tsMatch[1]);
                    head = '<span class="log-ts">' + escapeHtml(tsMatch[1]) + '</span>';
                    rest = raw.slice(0, tsInline) + raw.slice(tsInline + tsMatch[1].length);
                }
                return '<div class="log-line log-lv-line" data-lv="' + escapeHtml(lv) + '">'
                    + head
                    + '<span class="log-lv">' + escapeHtml(lv) + '</span>'
                    + '<span class="log-txt">' + highlightHttp(rest) + '</span>'
                    + '</div>';
            }
            return '<div class="log-line log-plain">' + escapeHtml(raw) + '</div>';
        }).join('');
    }

    // ---- logcenter: 已拆分到 js/logcenter.js (#1 模块化) ----
    // ---- Settings + Engine Management ----
    async function renderSettings() {
        const data = await apiGet('/settings');
        if (data) {
            document.getElementById('cfgHost').textContent = data.host || '--';
            document.getElementById('cfgPort').textContent = data.port || '--';
            document.getElementById('cfgModelsDir').textContent = data.models_dir || '--';
            document.getElementById('cfgLogsDir').textContent = data.logs_dir || '--';
            const cv = document.getElementById('cfgVersion');
            if (cv) cv.textContent = data.version || '--';
            const nn = document.getElementById('cfgNodeName');
            if (nn && !nn.value) nn.value = data.node_name || 'AMM';
            applyBrand(data);
            const an = document.getElementById('cfgAppName');
            const as_ = document.getElementById('cfgAppSubtitle');
            const al = document.getElementById('cfgAppLogo');
            if (an && !an.value) an.value = data.app_name || 'AMM';
            if (as_ && !as_.value) as_.value = data.app_subtitle || 'AI Models Manage';
            if (al && !al.value) al.value = data.app_logo || '/assets/logo.png';
            await dkLoad();
            await pipLoad();
            await pathsLoad();
            await authLoad();
            // 文件管理器只在首次进入 Settings 时初始化（避免每次刷新都重新列目录）
            if (typeof window.fmInit === 'function' && !window.__fmInited) {
                window.__fmInited = true;
                setTimeout(() => window.fmInit(), 100);
            }
        }
        await window.emRefreshCatalog(false);
        // 设置项索引（搜索条下方的快捷跳转 chip）
        try { settingsBuildIndex(); } catch (e) { console.warn('settings index', e); }
    }

    // ================================================================
    // Settings：分区索引 + 关键词搜索 + 全部展开（便于快速定位功能项）
    // ================================================================
    let _settingsExpanded = false;

    function settingsGroups() {
        const panel = document.getElementById('settingsPanel');
        if (!panel) return [];
        return Array.from(panel.querySelectorAll(':scope > .setting-group'));
    }

    function settingsGroupTitle(g) {
        const h = g.querySelector('h3');
        return (h ? h.textContent : '').trim() || '(未命名分组)';
    }

    // 分组索引：点一下跳到该分组
    function settingsBuildIndex() {
        const box = document.getElementById('settingsIndex');
        if (!box) return;
        const groups = settingsGroups();
        if (!groups.length) { box.innerHTML = ''; return; }
        box.innerHTML = groups.map((g, i) => {
            const id = g.id || ('settings-group-' + i);
            if (!g.id) g.id = id;
            return `<button class="settings-chip" data-target="${escapeAttr(id)}"
                        onclick="window.settingsJump('${escapeAttr(id)}')">${escapeHtml(settingsGroupTitle(g))}</button>`;
        }).join('');
    }

    function settingsJump(id) {
        const el = document.getElementById(id);
        if (!el) return;
        // 目标分组若被搜索隐藏，先清掉过滤
        if (el.classList.contains('settings-hidden')) settingsSearchClear();
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        el.classList.remove('settings-flash');
        void el.offsetWidth;
        el.classList.add('settings-flash');
        setTimeout(() => el.classList.remove('settings-flash'), 1400);
    }

    // 关键词过滤：命中分组标题或分组内任意文本即保留，并高亮分组标题
    function settingsSearch(kw) {
        const q = String(kw || '').trim().toLowerCase();
        const groups = settingsGroups();
        const counter = document.getElementById('settingsSearchCount');
        let hits = 0;
        groups.forEach(g => {
            const text = (g.textContent || '').toLowerCase();
            const titleHit = settingsGroupTitle(g).toLowerCase().includes(q);
            const hit = !q || titleHit || text.includes(q);
            g.classList.toggle('settings-hidden', !hit);
            const h = g.querySelector('h3');
            if (h) {
                // 恢复原始标题再按需高亮
                const raw = h.dataset.rawTitle || h.textContent;
                h.dataset.rawTitle = raw;
                h.innerHTML = (!q || !titleHit) ? escapeHtml(raw) : highlightTerm(raw, kw);
            }
            if (hit) hits++;
        });
        if (counter) {
            counter.textContent = q ? `命中 ${hits} / ${groups.length} 个分区` : '';
        }
        // 搜索时自动展开，避免结果藏在折叠里
        if (q && !_settingsExpanded) {
            groups.forEach(g => g.querySelectorAll('details').forEach(d => { d.open = true; }));
        }
    }

    function highlightTerm(text, kw) {
        const safe = escapeHtml(text);
        const k = String(kw || '').trim();
        if (!k) return safe;
        try {
            const re = new RegExp('(' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
            return safe.replace(re, '<mark class="settings-mark">$1</mark>');
        } catch (e) {
            return safe;
        }
    }

    function settingsSearchClear() {
        const inp = document.getElementById('settingsSearch');
        if (inp) inp.value = '';
        settingsSearch('');
    }

    // 全部展开 / 收起：展开所有 details，并把长列表标记为「全量可见」
    function settingsToggleExpand() {
        _settingsExpanded = !_settingsExpanded;
        const btn = document.getElementById('settingsExpandBtn');
        settingsGroups().forEach(g => {
            g.querySelectorAll('details').forEach(d => { d.open = _settingsExpanded; });
            g.classList.toggle('settings-expanded', _settingsExpanded);
        });
        if (btn) btn.textContent = _settingsExpanded ? '📕 全部收起' : '📖 全部展开';
    }

    // ================================================================
    // 品牌（#10）：项目名/副标题/LOGO 一改，顶栏 + 标题栏 + favicon 全部跟着变
    // ================================================================
    async function loadBrand() {
        try {
            const d = await apiGet('/settings');
            if (d) applyBrand(d);
        } catch (e) {
            console.warn('品牌配置加载失败', e);
        }
    }
    function applyBrand(cfg) {
        if (!cfg) return;
        const name = (cfg.app_name || 'AMM').trim();
        const sub = (cfg.app_subtitle || '').trim();
        const logo = (cfg.app_logo || '/assets/logo.png').trim();

        const bt = document.getElementById('brandText');
        const bs = document.getElementById('brandSub');
        if (bt) bt.textContent = name;
        if (bs) { bs.textContent = sub; bs.style.display = sub ? '' : 'none'; }

        // 浏览器标题栏
        document.title = sub ? `${name} - ${sub}` : name;

        // LOGO：顺便用作 favicon
        const img = document.getElementById('brandLogo');
        if (img && logo) img.src = logo;
        const fav = document.getElementById('faviconLink');
        if (fav && logo) { fav.href = logo; fav.type = ''; }
    }

    async function saveBrand() {
        const name = (document.getElementById('cfgAppName')?.value || '').trim();
        if (!name) { toast('项目名称不能为空', 'error'); return; }
        const payload = {
            app_name: name,
            app_subtitle: (document.getElementById('cfgAppSubtitle')?.value || '').trim(),
            app_logo: (document.getElementById('cfgAppLogo')?.value || '').trim(),
        };
        const r = await apiPost('/settings/brand', payload);
        if (r && r.success) {
            applyBrand(r);
            toast('✅ 品牌已更新，标题栏与 LOGO 已同步', 'success');
        } else {
            toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
        }
    }

    async function resetBrand() {
        if (!confirm('恢复默认品牌（AMM / AI Models Manage / 默认 LOGO）？')) return;
        const r = await apiPost('/settings/brand', {
            app_name: 'AMM', app_subtitle: 'AI Models Manage', app_logo: '/assets/logo.png',
        });
        if (r && r.success) {
            applyBrand(r);
            const an = document.getElementById('cfgAppName');
            const as_ = document.getElementById('cfgAppSubtitle');
            const al = document.getElementById('cfgAppLogo');
            if (an) an.value = 'AMM';
            if (as_) as_.value = 'AI Models Manage';
            if (al) al.value = '/assets/logo.png';
            toast('已恢复默认品牌', 'success');
        }
    }

    // ================================================================
    // 服务目录配置（#9）
    // ================================================================
    let _pathRows = [];

    async function pathsLoad() {
        const data = await apiGet('/settings/paths');
        const box = document.getElementById('cfgPathsBody');
        if (!box || !data) return;
        _pathRows = data.paths || [];
        box.innerHTML = _pathRows.map((p, i) => {
            const cls = p.exists ? (p.writable ? 'ok' : 'bad') : 'bad';
            const label = p.exists ? (p.writable ? '可读写' : '只读') : '不存在';
            return `<tr>
                <td class="rm-mono">${escapeHtml(p.key)}</td>
                <td><input class="form-input" id="cfgPath-${i}" value="${escapeAttr(p.path)}" style="font-family:ui-monospace,monospace;font-size:11.5px;"></td>
                <td><span class="lcc-badge ${cls === 'ok' ? 'ok' : 'bad'}">${label}</span></td>
                <td><button class="btn btn-sm" onclick="window.cfgPathBrowse(${i})">📂</button></td>
            </tr>`;
        }).join('');
    }

    async function savePaths() {
        const payload = {};
        _pathRows.forEach((p, i) => {
            const el = document.getElementById('cfgPath-' + i);
            if (el && el.value.trim() && el.value.trim() !== p.path) {
                payload[p.key] = el.value.trim();
            }
        });
        if (!Object.keys(payload).length) { toast('没有变更', 'info'); return; }
        const r = await apiPost('/settings/paths', payload);
        if (r && r.success) {
            toast('✅ 已保存，重启服务后生效', 'success');
            await pathsLoad();
            await authLoad();
            // 文件管理器只在首次进入 Settings 时初始化（避免每次刷新都重新列目录）
            if (typeof window.fmInit === 'function' && !window.__fmInited) {
                window.__fmInited = true;
                setTimeout(() => window.fmInit(), 100);
            }
        } else {
            toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
        }
    }

    function cfgPathBrowse(i) {
        const row = _pathRows[i];
        if (!row) return;
        window.fmOpenPicker(row.path, (picked) => {
            const el = document.getElementById('cfgPath-' + i);
            if (el) el.value = picked;
        });
    }

    // ================================================================
    // 鉴权开关（#2）
    // ================================================================
    async function authLoad() {
        const d = await apiGet('/settings/auth');
        const el = document.getElementById('authStatusBadge');
        if (!el || !d) return;
        const enforced = !!d.auth_enforced;
        el.className = 'lcc-badge ' + (enforced ? 'ok' : 'bad');
        el.textContent = enforced
            ? `鉴权已开启（默认Key:${d.has_default_key ? '有' : '无'} · 虚拟Key:${d.virtual_key_count || 0}）`
            : '鉴权已关闭（内网免 Key）';
    }

    async function disableAuth() {
        const d = await apiGet('/settings/auth');
        const extra = d && d.auth_enforced
            ? '\n\n当前：' + (d.has_default_key ? '已设默认 Key' : '无默认 Key') +
              ' / 虚拟 Key ' + (d.virtual_key_count || 0) + ' 个'
            : '';
        const ok = confirm(
            '⚠️ 确认完全关闭鉴权？\n\n' +
            '关闭后，任何能访问本端口（包括外网映射）的人\n' +
            '都可以直接调用 /v1/* 消耗你的算力与 API 额度。\n' +
            '仅建议在可信内网使用。' + extra);
        if (!ok) return;
        const typed = prompt(
            '这是危险操作。请输入 YES（大写）确认：', '');
        if (typed !== 'YES') { toast('已取消（未输入 YES）', 'info'); return; }

        const r = await apiPost('/settings/auth/disable', { confirm: true });
        if (r && r.success) {
            toast('🔓 鉴权已关闭，客户端现在可免 Key 直连', 'warn', 6000);
            await authLoad();
            await dkLoad();
        } else {
            toast('❌ ' + ((r && r.error) || '操作失败'), 'error');
        }
    }

    async function enableAuth() {
        const r = await apiPost('/settings/auth/enable', {});
        if (r && r.success) {
            toast('🔒 鉴权已开启', 'success');
            if (r.warning) toast('⚠ ' + r.warning, 'warn', 8000);
            await authLoad();
        } else {
            toast('❌ ' + ((r && r.error) || '操作失败'), 'error');
        }
    }

    // LOGO 选择：从文件管理器挑文件（而不是目录）
    function cfgLogoBrowse() {
        const cur = document.getElementById('cfgAppLogo')?.value || '/amm/frontend/assets';
        const dir = cur.includes('/') ? cur.slice(0, cur.lastIndexOf('/')) : '/amm/frontend/assets';
        window.fmGo(dir || '/amm/frontend/assets');
        toast('在文件列表里点目标图片的「查看」，即可把它设为 LOGO', 'info', 6000);
        // 复用文件管理器的 view 钩子：选中文件后回填
        const fmPanel = document.getElementById('fmPanel');
        if (fmPanel) fmPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // 品牌预览实时联动（输入即预览，保存才生效）
    document.addEventListener('input', (e) => {
        const id = e.target && e.target.id;
        if (id === 'cfgAppLogo') {
            const img = document.getElementById('brandPreview');
            if (img) img.src = e.target.value || 'assets/logo.png';
        }
        if (id === 'cfgAppName' || id === 'cfgAppSubtitle') {
            const n = (document.getElementById('cfgAppName')?.value || 'AMM').trim();
            const sub = (document.getElementById('cfgAppSubtitle')?.value || '').trim();
            const t = document.getElementById('brandPreviewText');
            if (t) t.textContent = sub ? `${n} - ${sub}` : n;
        }
    });

    // ---- Settings: PyPI 镜像 ----
    let _pipPresets = [];

    async function pipLoad() {
        const d = await apiGet('/settings/pip');
        if (!d) return;
        _pipPresets = d.presets || [];
        const sel = document.getElementById('cfgPipPreset');
        const inp = document.getElementById('cfgPipIndex');
        if (sel) {
            sel.innerHTML = '<option value="">选择国内镜像…</option>' +
                _pipPresets.map(p => `<option value="${escapeAttr(p.url)}">${escapeHtml(p.label)}</option>`).join('') +
                '<option value="__custom__">自定义…</option>';
            if (_pipPresets.some(p => p.url === d.index_url)) sel.value = d.index_url;
        }
        if (inp && !inp.value) inp.value = d.index_url || '';
    }

    function pipPresetChange() {
        const sel = document.getElementById('cfgPipPreset');
        const inp = document.getElementById('cfgPipIndex');
        if (!sel || !inp) return;
        if (sel.value && sel.value !== '__custom__') inp.value = sel.value;
        else if (sel.value === '__custom__') { inp.value = ''; inp.focus(); }
    }

    async function savePipIndex() {
        const inp = document.getElementById('cfgPipIndex');
        const url = (inp && inp.value || '').trim();
        const r = await apiPost('/settings/pip', { index_url: url });
        if (r && r.success) {
            toast('✅ PyPI 镜像已更新：' + r.effective, 'success');
            if (inp) inp.value = r.effective;
        } else toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
    }

    // ---- Settings: 对外服务默认 API Key ----
    let _dkCache = '';

    async function dkLoad() {
        const el = document.getElementById('cfgDefaultKey');
        if (!el) return;
        const d = await apiGet('/settings/default-key');
        if (!d) return;
        _dkCache = '';
        el.value = d.has_key ? (d.masked + '（点击「复制」获取完整值）') : '';
        el.placeholder = d.has_key ? '' : '未设置 —— 客户端可免 Key 直连';
    }

    async function dkGenerate() {
        if (!confirm('生成新的默认 API Key？旧的会立即失效，客户端需要更新。')) return;
        const r = await apiPost('/settings/default-key/generate', {});
        if (!r || r.error) { toast('生成失败: ' + ((r && r.error) || ''), 'error'); return; }
        _dkCache = r.key || '';
        const el = document.getElementById('cfgDefaultKey');
        if (el) el.value = r.key || '';
        toast('✅ 已生成，点「复制」带到客户端', 'success');
    }

    async function dkCopy() {
        let key = _dkCache;
        if (!key) {
            const r = await apiPost('/settings/default-key/reveal', {});
            if (!r || !r.key) { toast('尚未设置默认 Key', 'error'); return; }
            key = r.key;
            _dkCache = key;
            const el = document.getElementById('cfgDefaultKey');
            if (el) el.value = key;
        }
        try {
            await navigator.clipboard.writeText(key);
            toast('已复制到剪贴板', 'success');
        } catch { prompt('复制下面的 Key：', key); }
    }

    async function dkClear() {
        if (!confirm('清除默认 API Key？\n清除后客户端可不带 Key 直连（仅建议内网自用）。')) return;
        const r = await apiDelete('/settings/default-key');
        if (r && r.cleared) {
            _dkCache = '';
            const el = document.getElementById('cfgDefaultKey');
            if (el) { el.value = ''; el.placeholder = '未设置 —— 客户端可免 Key 直连'; }
            toast('已清除', 'success');
        } else toast('清除失败', 'error');
    }

    async function saveNodeName() {
        const el = document.getElementById('cfgNodeName');
        const name = (el && el.value || '').trim();
        if (!name) { toast('分组名不能为空', 'error'); return; }
        const r = await apiPost('/settings/node-name', { node_name: name });
        if (r && r.success) toast('✅ 分组名已更新：' + r.node_name, 'success');
        else toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
    }

    // ---- 导出（拆分后 core 自己提供）----
    window.saveNodeName = saveNodeName;
    window.loadBrand = loadBrand;
    window.saveBrand = saveBrand;
    window.resetBrand = resetBrand;
    window.pathsLoad = pathsLoad;
    window.savePaths = savePaths;
    window.cfgPathBrowse = cfgPathBrowse;
    window.settingsSearch = settingsSearch;
    window.settingsSearchClear = settingsSearchClear;
    window.settingsToggleExpand = settingsToggleExpand;
    window.settingsJump = settingsJump;
    window.settingsBuildIndex = settingsBuildIndex;
    window.cfgLogoBrowse = cfgLogoBrowse;
    window.authLoad = authLoad;
    window.disableAuth = disableAuth;
    window.enableAuth = enableAuth;

    // ---- tools: 已拆分到 js/tools.js (#1 模块化) ----
    // ---- Modal ----
    function openModelDetail(modelId) {
        const inst = instances[modelId];
        if (!inst) return;
        const modal = document.getElementById('modelModal');
        const title = document.getElementById('modalTitle');
        const body = document.getElementById('modalBody');
        if (!modal || !title || !body) return;
        const icon = categoryIcons[inst.category] || '🤖';
        title.textContent = icon + ' ' + inst.name;
        const sc = inst.status || 'stopped';
        body.innerHTML = `
            <div style="margin-bottom:16px;">
                <span class="status-indicator ${sc}" style="display:inline-block;vertical-align:middle;"></span>
                <span style="margin-left:8px;font-weight:600">${sc.toUpperCase()}</span>
                <span style="margin-left:16px;color:var(--text-muted)">Port: ${inst.port}</span>
                <span style="margin-left:16px;color:var(--text-muted)">Engine: ${inst.engine_type || 'N/A'}</span>
            </div>
            <div class="modal-file-row">
                <label>推理引擎</label>
                <div class="input-with-btn">
                    <select id="modalEngine" class="form-select" style="max-width:220px;"
                            onchange="window.modalEngineChange('${escapeAttr(modelId)}')">
                        ${engineOptions(inst)}
                    </select>
                    <button class="btn btn-sm btn-primary" onclick="window.modalApplyEngine('${escapeAttr(modelId)}')">💾 应用引擎</button>
                    <span class="hint" style="align-self:center;">切换后参数会按新引擎重新加载</span>
                </div>
            </div>
            <div class="modal-file-row">
                <label>引擎构建</label>
                <div class="input-with-btn">
                    <select id="modalVariant" class="form-select" style="max-width:220px;">
                        <option value="">加载中…</option>
                    </select>
                    <button class="btn btn-sm btn-primary" onclick="window.modalApplyVariant('${escapeAttr(modelId)}')">💾 应用构建</button>
                    <span class="hint" style="align-self:center;">选 CUDA 走 GPU，选 CPU 不占显存</span>
                </div>
            </div>
            <div class="modal-file-row">
                <label>模型文件</label>
                <div class="input-with-btn">
                    <input id="modalModelFile" class="form-input" value="${escapeAttr(inst.selected_model_file || '')}"
                           placeholder="相对 /models 的路径">
                    <button class="btn btn-sm" onclick="window.modalPickFile('${escapeAttr(modelId)}')">📂 浏览</button>
                    <button class="btn btn-sm btn-primary" onclick="window.modalSaveFile('${escapeAttr(modelId)}')">💾 应用</button>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;color:var(--text-muted);font-size:13px;">
                <div>GPU Memory: <strong>${inst.gpu_memory_mb ? inst.gpu_memory_mb.toFixed(0) + ' MB' : 'N/A'}</strong></div>
                <div>CPU: <strong>${inst.cpu_percent ? inst.cpu_percent.toFixed(1) + '%' : 'N/A'}</strong></div>
                <div>Memory: <strong>${inst.memory_mb ? inst.memory_mb.toFixed(0) + ' MB' : 'N/A'}</strong></div>
                <div>Uptime: <strong>${inst.uptime_seconds ? formatUptime(inst.uptime_seconds) : 'N/A'}</strong></div>
                <div>Requests: <strong>${inst.request_count}</strong></div>
                <div>Errors: <strong style="color:var(--error)">${inst.error_count}</strong></div>
            </div>
            <div style="margin-top:16px;display:flex;gap:8px;">
                <button class="btn btn-success btn-sm" onclick="window.startModel('${modelId}')">Start</button>
                <button class="btn btn-danger btn-sm" onclick="window.stopModel('${modelId}')">Stop</button>
                <button class="btn btn-sm" onclick="window.restartModel('${modelId}')">Restart</button>
                <button class="btn btn-sm" onclick="viewLogs('${modelId}')">View Logs</button>
            </div>
            <div style="margin-top:16px;">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;flex-wrap:wrap;">
                    <h4 style="font-size:13px;margin:0;">Parameters（可编辑）</h4>
                    <div style="display:flex;gap:6px;">
                        <button class="btn btn-sm" onclick="window.saveModalParams('${escapeAttr(modelId)}')">💾 仅保存</button>
                        <button class="btn btn-sm btn-primary" onclick="window.applyModalParams('${escapeAttr(modelId)}')"
                                title="保存参数并重启服务，让改动立即生效">💾 保存并重启</button>
                    </div>
                </div>
                <div id="modalParams" class="form-grid" data-mid="${escapeAttr(modelId)}">
                    <div style="font-size:12px;color:var(--text-muted)">加载参数定义…</div>
                </div>
                <details style="margin-top:10px;">
                    <summary style="font-size:11.5px;color:var(--text-muted);cursor:pointer">原始 JSON</summary>
                    <pre style="background:var(--bg-input);padding:12px;border-radius:6px;font-size:11.5px;color:var(--text-secondary);overflow-x:auto;margin-top:6px;">${JSON.stringify(inst.parameters, null, 2)}</pre>
                </details>
            </div>
            <div style="margin-top:16px;border-top:1px solid var(--border);padding-top:14px;">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;">
                    <h4 style="font-size:13px;margin:0;">🚀 启动命令编排</h4>
                    <span style="font-size:11px;color:var(--text-muted)">保存后 Start 将直接执行该脚本</span>
                </div>
                <textarea id="modalCmd" rows="4" spellcheck="false"
                    style="width:100%;font-family:ui-monospace,monospace;font-size:11px;padding:8px 10px;background:var(--bg-input);border:1px solid var(--border);border-radius:6px;color:var(--text);line-height:1.5;resize:vertical;"
                    placeholder="点「生成命令」按当前参数生成，或直接粘贴自定义命令行…"></textarea>
                <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
                    <button class="btn btn-sm" onclick="window.previewCmd('${escapeAttr(modelId)}')">⚙️ 生成命令</button>
                    <button class="btn btn-sm btn-primary" onclick="window.saveCmd('${escapeAttr(modelId)}')">💾 保存为启动脚本</button>
                    <button class="btn btn-sm" onclick="window.clearCmd('${escapeAttr(modelId)}')">🗑 清除自定义</button>
                </div>
            </div>`;
        modal.classList.add('active');
        loadModalParams(modelId, inst);
        loadCmd(modelId);
        loadModalVariants(modelId);   // 引擎构建（版本 · flavor）下拉
    }

    async function loadCmd(modelId) {
        const el = document.getElementById('modalCmd');
        if (!el) return;
        const r = await apiGet('/instances/' + encodeURIComponent(modelId) + '/command');
        if (r && r.startup_command) el.value = r.startup_command;
    }

    async function previewCmd(modelId) {
        const el = document.getElementById('modalCmd');
        const r = await apiPost('/instances/' + encodeURIComponent(modelId) + '/command/preview', {});
        if (!r || r.error) { toast('生成失败: ' + ((r && r.error) || ''), 'error'); return; }
        if (el) el.value = r.shell || (r.args || []).join(' ');
        toast('已按当前参数生成启动命令');
    }

    async function saveCmd(modelId) {
        const el = document.getElementById('modalCmd');
        const command = (el && el.value || '').trim();
        if (!command) { toast('命令为空', 'error'); return; }
        const r = await apiPut('/instances/' + encodeURIComponent(modelId) + '/command', { command });
        if (r && r.success) toast('✅ 启动脚本已保存（scripts/' + modelId + '.sh）', 'success');
        else toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
    }

    async function clearCmd(modelId) {
        const r = await apiDelete('/instances/' + encodeURIComponent(modelId) + '/command');
        if (r && r.success) {
            const el = document.getElementById('modalCmd');
            if (el) el.value = '';
            toast('已清除自定义启动命令', 'success');
        } else {
            toast('❌ ' + ((r && r.error) || '失败'), 'error');
        }
    }

    // 实例详情里的参数编辑（替代已下线的 Models 页参数区）
    async function loadModalParams(modelId, inst) {
        const box = document.getElementById('modalParams');
        if (!box) return;
        const engine = (inst && inst.engine_type) || '';
        const category = (inst && inst.category) || '';
        const data = await apiGet('/schema/params?' + new URLSearchParams({ category, engine }).toString());
        const defs = (data && data.parameters) || [];
        if (!defs.length) {
            box.innerHTML = '<div style="font-size:12px;color:var(--text-muted)">该引擎没有预置参数。</div>';
            return;
        }
        const cur = (inst && inst.parameters) || {};
        box.innerHTML = defs.map(p => {
            const val = cur[p.name] !== undefined ? cur[p.name] : p.default;
            const id = `mp-${modelId}-${p.name}`;
            let input;
            if (p.type === 'boolean') {
                input = `<label style="display:flex;align-items:center;gap:6px;font-size:12px">
                    <input type="checkbox" id="${id}" ${val ? 'checked' : ''} style="accent-color:var(--accent)"> ${val ? '开启' : '关闭'}</label>`;
            } else if (p.type === 'select') {
                input = `<select class="form-select" id="${id}">${(p.options || []).map(o =>
                    `<option value="${escapeAttr(o)}" ${String(o) === String(val) ? 'selected' : ''}>${escapeHtml(o)}</option>`).join('')}</select>`;
            } else if (p.type === 'number' || p.type === 'float') {
                input = `<input type="number" class="form-input" id="${id}" value="${escapeAttr(val)}"
                    ${p.min != null ? `min="${p.min}"` : ''} ${p.max != null ? `max="${p.max}"` : ''}
                    step="${p.step != null ? p.step : (p.type === 'float' ? 0.05 : 1)}">`;
            } else {
                input = `<input type="text" class="form-input" id="${id}" value="${escapeAttr(val == null ? '' : val)}">`;
            }
            return `<div class="field"><label title="${escapeAttr(p.label || p.name)}">${escapeHtml(p.label || p.name)}
                <span style="opacity:.55;font-size:10px">${escapeHtml(p.name)}</span></label>${input}</div>`;
        }).join('');
    }

    // ---- Dashboard 弹窗：更换模型文件 ----
    async function modalSaveFile(modelId) {
        const el = document.getElementById('modalModelFile');
        const file = (el && el.value || '').trim();
        if (!file) { toast('请填写或选择模型文件', 'error'); return; }
        const r = await apiPut('/instances/' + encodeURIComponent(modelId) + '/model-file',
                               { model_file: file });
        if (r && r.success) {
            toast('✅ 模型文件已更新，重启后生效', 'success');
            if (instances[modelId]) instances[modelId].selected_model_file = file;
            if (typeof rmRefresh === 'function') rmRefresh();
            if (typeof renderDashboard === 'function') renderDashboard();
        } else {
            toast('❌ ' + ((r && r.error) || '更新失败'), 'error');
        }
    }

    function modalPickFile(modelId) {
        // 复用现有文件浏览器；选完后回填到弹窗输入框
        if (typeof openFileBrowser !== 'function') { toast('文件浏览器不可用', 'error'); return; }
        window.__modalFileTarget = modelId;
        openFileBrowser(modelId);
    }

    // ---- 弹窗内切换引擎：应用后按新引擎重载参数 ----
    function modalEngineChange(modelId) {
        const sel = document.getElementById('modalEngine');
        const inst = instances[modelId];
        if (!sel || !inst) return;
        const changed = sel.value !== (inst.engine_type || '');
        const btn = [...document.querySelectorAll('#modelModal button')]
            .find(b => b.textContent.includes('应用引擎'));
        if (btn) btn.disabled = !changed;
    }

    async function modalApplyEngine(modelId) {
        const sel = document.getElementById('modalEngine');
        const inst = instances[modelId];
        if (!sel || !inst) return;
        const engine = sel.value;
        if (engine === (inst.engine_type || '')) { toast('引擎未变化'); return; }
        if (inst.status === 'running' &&
            !confirm(`实例 ${modelId} 正在运行，切换引擎会停止后按新引擎重启。继续？`)) return;

        const btn = [...document.querySelectorAll('#modelModal button')]
            .find(b => b.textContent.includes('应用引擎'));
        if (btn) { btn.disabled = true; btn.textContent = '切换中…'; }

        const r = await apiPut('/instances/' + encodeURIComponent(modelId) + '/engine',
                               { engine_type: engine, restart: inst.status === 'running' });
        if (btn) { btn.disabled = false; btn.textContent = '💾 应用引擎'; }

        if (r && r.success) {
            toast(`✅ 引擎已切换为 ${engine}`, 'success');
            if (instances[modelId]) instances[modelId].engine_type = engine;
            // 按新引擎重新加载参数表单
            try {
                const params = await apiGet('/models/' + encodeURIComponent(modelId) + '/parameters');
                if (params) loadModalParams(modelId, instances[modelId]);
            } catch (e) { /* 参数接口不存在时忽略 */ }
            if (typeof refreshAll === 'function') refreshAll();
        } else {
            toast('❌ ' + ((r && r.error) || '切换失败'), 'error');
        }
    }

    async function collectModalParams(modelId) {
        const box = document.getElementById('modalParams');
        if (!box) return null;
        const params = {};
        box.querySelectorAll('input[id^="mp-"], select[id^="mp-"]').forEach(el => {
            const name = el.id.replace(`mp-${modelId}-`, '');
            if (el.type === 'checkbox') params[name] = el.checked;
            else if (el.type === 'number') params[name] = el.value === '' ? '' : parseFloat(el.value);
            else params[name] = el.value;
            if (params[name] === '') delete params[name];
        });
        return params;
    }

    async function applyModalParams(modelId) {
        const params = await collectModalParams(modelId);
        if (!params) return;
        const btn = document.querySelector(`#modelModal button[onclick*="applyModalParams"]`);
        if (btn) { btn.disabled = true; btn.textContent = '重启中…'; }
        const r = await apiPut('/instances/' + encodeURIComponent(modelId) + '/apply',
                               { parameters: params, restart: true });
        if (btn) { btn.disabled = false; btn.textContent = '💾 保存并重启'; }
        if (r && r.success) {
            toast('✅ 参数已保存并重启生效', 'success');
            if (instances[modelId]) {
                instances[modelId].parameters = { ...(instances[modelId].parameters || {}), ...params };
            }
            document.getElementById('modelModal')?.classList.remove('active');
            if (typeof rmRefresh === 'function') rmRefresh();
        } else {
            const msg = (r && (r.error || (r.restart && r.restart.error))) || '失败';
            toast('❌ ' + msg, 'error');
        }
    }

    async function saveModalParams(modelId) {
        const params = await collectModalParams(modelId);
        if (!params) return;
        const r = await apiPut('/instances/' + encodeURIComponent(modelId) + '/parameters', params);
        if (r && r.success) {
            toast('✅ 参数已保存到 ' + modelId, 'success');
            if (instances[modelId]) instances[modelId].parameters = { ...(instances[modelId].parameters || {}), ...params };
        } else {
            toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
        }
    }

    function closeModal() {
        document.getElementById('modelModal')?.classList.remove('active');
    }

    // ---- Helpers ----
    function formatUptime(seconds) {
        if (!seconds || seconds <= 0) return '--';
        const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
        if (d > 0) return d + 'd ' + h + 'h';
        if (h > 0) return h + 'h ' + m + 'm';
        if (m > 0) return m + 'm ' + s + 's';
        return s + 's';
    }

    async function renderPlayground() {
        // playground 标签页初始化
        setupPlaygroundTabs();
    }

    // ---- Playground Chat (v0.2: model select / vision / session params / history) ----
    let chatHistory = [];
    let chatModelId = null;

    async function setupPlaygroundTabs() {
        document.querySelectorAll('.pg-tab').forEach(tab => {
            tab.onclick = function() {
                document.querySelectorAll('.pg-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                document.querySelectorAll('.pg-content').forEach(c => c.classList.remove('active'));
                document.getElementById('pg-' + tab.dataset.pg).classList.add('active');
                // 切到某个面板时刷新对应「已部署实例」下拉
                const pg = tab.dataset.pg;
                const map = { embedding: ['pgEmbModel', 'embedding'], rerank: ['pgRerankModel', 'rerank'],
                              asr: ['pgAsrModel', 'asr'], tts: ['pgTtsModel', 'tts'], ocr: ['pgOcrModel', 'ocr'] };
                if (map[pg]) pgFillModelSelect(map[pg][0], map[pg][1]);
            };
        });
        // 填充 Playground 模型选择 (chat + vision)
        await populatePlaygroundModels();
        // 各类别的已部署实例下拉
        pgFillModelSelect('pgEmbModel', 'embedding');
        pgFillModelSelect('pgRerankModel', 'rerank');
        pgFillModelSelect('pgAsrModel', 'asr');
        pgFillModelSelect('pgTtsModel', 'tts');
        pgFillModelSelect('pgOcrModel', 'ocr');
        // 显存实时估算 + 只读高级参数 + 输入变化刷新 (T2I/T2V/I2V)
        setupT2iVram();
        setupT2vVram();
        setupI2vVram();

    }

    // ---- T2I 显存估算 (Playground) ----
    function setupT2iVram() { setupPgVram('t2i', { fields:['pgT2IWidth','pgT2IHeight','pgT2ISteps','pgT2INum'], vramFn: estimateT2iVram, cfg(m){return {width:m('pgT2IWidth',1024),height:m('pgT2IHeight',1024),num_images:m('pgT2INum',1),num_steps:m('pgT2ISteps',28)};}, advLabel:'T2I' }); }
    function setupT2vVram() { setupPgVram('t2v', { fields:['pgT2VResolution','pgT2VFrames','pgT2VSteps'], vramFn: estimateVideoVram, cfg(m){return {resolution:m('pgT2VResolution','480p'), num_frames:m('pgT2VFrames',81), num_steps:m('pgT2VSteps',50)};}, advLabel:'T2V' }); }
    function setupI2vVram() { setupPgVram('i2v', { fields:[], vramFn: estimateVideoVram, cfg:null, advLabel:'I2V' }); }

    // ---- 通用: Playground 显存估算 + 只读高级参数展示 ----
    function setupPgVram(modelId, opt) {
        refreshPgVram(modelId, opt);
        (opt.fields||[]).forEach(id => { const el=document.getElementById(id); if(el){ el.addEventListener('input',()=>refreshPgVram(modelId,opt)); el.addEventListener('change',()=>refreshPgVram(modelId,opt)); } });
    }
    async function refreshPgVram(modelId, opt) {
        const box = document.getElementById('pgVram-'+modelId);
        const advBox = document.getElementById('pgAdv-'+modelId);
        let adv = { quant:'fp8', compute_dtype:'bf16', boundary_ratio:'', cpu_offload:false, offload:'gpu' };
        try { const r = await apiGet('/instances/'+modelId+'/advanced'); if (r && r.settings) adv = r.settings || {}; } catch(e){}
        if (box && opt && opt.vramFn) {
            const g = (id, d) => { const el = document.getElementById(id); return el ? parseFloat(el.value) : d; };
            const est = opt.vramFn(Object.assign(
                { quant:adv.quant||'', compute_dtype:adv.compute_dtype||'bf16', offload:adv.offload||'gpu' },
                opt.cfg ? opt.cfg(g) : {}
            ));
            const color = est.is_oom ? 'var(--error)' : 'var(--success)';
            box.innerHTML = `<span>💾 估算显存 <strong style="color:${color}">${fmtMb(est.mb)}</strong></span>
                <span style="opacity:.75">${est.weights_mb!=null?('权重 '+fmtMb(est.weights_mb)+' + '):''}${est.act_mb!=null?('激活 '+fmtMb(est.act_mb)+' + '):''}${est.fixed_mb!=null?('固定 '+fmtMb(est.fixed_mb)):''}</span>
                ${est.is_oom ? '<b style="color:var(--error)">⚠ 超85G!</b>' : ''}`;
        }
        if (advBox) {
            const q = adv.quant ? String(adv.quant).toUpperCase() : 'AUTO';
            const cd = adv.compute_dtype || 'bf16';
            const ol = adv.offload || 'gpu';
            const br = (adv.boundary_ratio===null||adv.boundary_ratio===undefined||adv.boundary_ratio==='') ? '自动' : adv.boundary_ratio;
            advBox.innerHTML = `<div style="font-size:11px;color:var(--text-muted);line-height:1.7;">
                <b style="color:var(--text-secondary)">🎛 模型高级参数 (模型级·推理只读)</b> ·
                Quant <b>${q}</b> · Compute <b>${cd}</b> · Offload <b>${ol}</b> ·
                Boundary <b>${br}</b> · CPU-Offload：<b>${adv.cpu_offload?'ON':'OFF'}</b>
                <span style="opacity:.7">(可在 Models → ${opt.advLabel||modelId.toUpperCase()} → Advanced 修改, 需 Restart 生效)</span>
            </div>`;
        }
    }

    async function populatePlaygroundModels() {
        const sel = document.getElementById('pgChatModel');
        if (!sel) return;
        const ids = ['chat'];  // 目前仅 chat 适配对话
        sel.innerHTML = '<option value="">-- 选择模型 --</option>';
        ids.forEach(id => {
            const inst = instances[id];
            if (!inst) return;
            const name = (inst.engine_type || '') + ' · ' + (inst.selected_model_file ? inst.selected_model_file.split('/').pop() : inst.name);
            sel.innerHTML += `<option value="${id}" ${chatModelId === id ? 'selected' : ''}>${id} — ${escapeHtml(name)} ${inst.status === 'running' ? '(运行中)' : '(未启动)'}</option>`;
        });
        if (sel.options.length === 1) sel.innerHTML += '<option value="">(无可用模型，请先在 Launch Model 启动)</option>';
        // 会话参数固化
        sel.onchange = function() { chatModelId = sel.value; };
    }

    function renderChatHistory() {
        const container = document.getElementById('pgChatMessages');
        if (!container) return;
        if (!chatHistory.length) return;
        container.innerHTML = chatHistory.map(m => m.role === 'user'
            ? '<div class="pg-chat-user"><strong>You:</strong> ' + escapeHtml(m.content) + '</div>'
            : '<div class="pg-chat-bot"><strong>Bot:</strong> ' + escapeHtml(m.content) + '</div>').join('');
        container.scrollTop = container.scrollHeight;
    }

    async function sendChatMessage() {
        const input = document.getElementById('pgChatInput');
        const msg = input.value.trim();
        if (!msg) return;
        const container = document.getElementById('pgChatMessages');
        const modelSel = document.getElementById('pgChatModel');
        const modelId = modelSel ? modelSel.value : 'chat';
        if (!modelId) { toast('请先选择模型', 'error'); return; }
        const inst = instances[modelId];
        if (!inst || inst.status !== 'running') {
            container.innerHTML += '<div class="pg-chat-bot"><strong>Bot:</strong> <span style="color:var(--error)">模型未运行，请先在 Launch Model 启动。</span></div>';
            return;
        }

        // 构建 user 消息 (含可选图片)
        const sys = document.getElementById('pgChatSystem')?.value.trim();
        const imgInput = document.getElementById('pgChatImage');
        const userMsg = { role: 'user', content: msg };
        if (imgInput && imgInput.files && imgInput.files[0]) {
            const b64 = await new Promise((resolve, reject) => {
                const r = new FileReader();
                r.onload = () => resolve(r.result.split(',', 2)[1]);
                r.onerror = reject;
                r.readAsDataURL(imgInput.files[0]);
            });
            const mime = imgInput.files[0].type || 'image/png';
            userMsg.content = [{ type: 'text', text: msg }, { type: 'image_url', image_url: { url: 'data:' + mime + ';base64,' + b64 } }];
        }
        chatHistory.push(userMsg);
        input.value = '';

        container.innerHTML += '<div class="pg-chat-user"><strong>You:</strong> ' + escapeHtml(msg) + (userMsg.image_url ? ' <span class="pg-img-tag">🖼️</span>' : '') + '</div>';
        container.innerHTML += '<div class="pg-chat-bot" id="pgChatLoading"><strong>Bot:</strong> <span style="color:var(--text-muted)">Thinking...</span></div>';
        container.scrollTop = container.scrollHeight;

        const messages = [];
        if (sys) messages.push({ role: 'system', content: sys });
        messages.push(...chatHistory);
        const payload = {
            model: 'chat',
            messages: messages,
            stream: true,
            temperature: parseFloat(document.getElementById('pgChatTemp')?.value || '0.7'),
            max_tokens: parseInt(document.getElementById('pgChatMaxTokens')?.value || '16384'),
            top_p: parseFloat(document.getElementById('pgChatTopP')?.value || '0.9'),
        };
        const tStart = performance.now();
        let ttft = null, tokens = 0;
        const perfBox = document.createElement('div');
        perfBox.className = 'pg-chat-perf';
        container.appendChild(perfBox);
        try {
            const resp = await fetch('/v1/chat/completions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const reader = resp.body.getReader();
            const dec = new TextDecoder();
            let buf = '';
            const botEl = document.getElementById('pgChatLoading');
            if (botEl) botEl.innerHTML = '<strong>Bot:</strong> <span style="color:var(--text-muted)">Thinking...</span>';
            let full = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buf += dec.decode(value, { stream: true });
                let idx;
                while ((idx = buf.indexOf('\n')) >= 0) {
                    const line = buf.slice(0, idx).trim();
                    buf = buf.slice(idx + 1);
                    if (!line.startsWith('data:')) continue;
                    const chunk = line.slice(5).trim();
                    if (chunk === '[DONE]') break;
                    try {
                        const j = JSON.parse(chunk);
                        const c = j.choices && j.choices[0];
                        if (ttft === null && c && c.delta && c.delta.content) ttft = performance.now() - tStart;
                        if (c && c.delta && c.delta.content) { full += c.delta.content; tokens++; }
                        if (c && (c.delta && c.delta.content)) {
                            if (botEl) botEl.innerHTML = '<strong>Bot:</strong> ' + escapeHtml(full);
                            perfBox.innerHTML = chatPerfHtml({ ttft: ttft, tokens: tokens, e2e: performance.now() - tStart, running: true });
                        }
                    } catch (e) {}
                }
            }
            document.getElementById('pgChatLoading')?.remove();
            const finalEl = container.querySelector('#pgChatLoading') ? null : botEl;
            if (!container.querySelector('.pg-chat-bot[data-final="1"]')) {
                container.innerHTML += '<div class="pg-chat-bot" data-final="1"><strong>Bot:</strong> ' + escapeHtml(full || '(空回复)') + '</div>';
            }
            chatHistory.push({ role: 'assistant', content: full });
            const e2e = performance.now() - tStart;
            perfBox.innerHTML = perfCard('Chat 性能', [
                ['TTFT (首Token)', ttft ? (ttft/1000).toFixed(3)+' s' : '--'],
                ['总 Token', tokens],
                ['TPS', tokens>0 ? (tokens/(e2e/1000)).toFixed(2) : '--'],
                ['端到端', (e2e/1000).toFixed(2)+' s'],
            ]);
        } catch (e) {
            document.getElementById('pgChatLoading')?.remove();
            container.innerHTML += '<div class="pg-chat-bot"><strong>Bot:</strong> <span style="color:var(--error)">Error: ' + escapeHtml(e.message) + '</span></div>';
            perfBox.remove();
        }
        container.scrollTop = container.scrollHeight;
        // 会话持久化到 localStorage
        localStorage.setItem('amm-chat-history', JSON.stringify(chatHistory.slice(-50)));
    }

    function chatPerfHtml(opt) {
        const ttft = opt.ttft ? (opt.ttft/1000).toFixed(3)+' s' : '--';
        const e2e = opt.running ? '进行中' : (opt.e2e/1000).toFixed(2)+' s';
        return perfCard('Chat 性能', [
            ['TTFT (首Token)', ttft],
            ['Token', opt.tokens],
            ['端到端', e2e],
        ]);
    }

    function clearChatHistory() {
        if (!confirm('确认清空当前会话？')) return;
        chatHistory = [];
        localStorage.removeItem('amm-chat-history');
        document.getElementById('pgChatMessages').innerHTML = '<div class="pg-chat-welcome">会话已清空，开始新对话吧。</div>';
    }
    function clearChatImage() {
        const el = document.getElementById('pgChatImage');
        if (el) el.value = '';
        toast('已清除上传图片');
    }
    function loadChatHistory() {
        try { chatHistory = JSON.parse(localStorage.getItem('amm-chat-history') || '[]'); } catch (e) { chatHistory = []; }
        renderChatHistory();
    }

    // ---- Playground: Embedding / ASR / TTS / Rerank / OCR (v0.3 9类测试+性能) ----
    function perfCard(title, metrics) {
        return `<div class="pg-perf-card"><div class="pg-perf-title">⚡ ${title}</div><div class="pg-perf-grid">${metrics.map(m=>`<div class="pg-perf-item"><span class="pg-perf-label">${m[0]}</span><span class="pg-perf-value">${m[1]}</span></div>`).join('')}</div></div>`;
    }

    // ---- Playground: 已部署模型下拉（v1.2） ----
    // 列出所有能服务该能力的实例：category 匹配，或模型文件/参数匹配。
    function pgCandidates(kind) {
        const cats = { embedding: ['embedding'], rerank: ['reranker'], asr: ['asr'],
                       tts: ['tts'], ocr: ['ocr'] }[kind] || [];
        const out = [];
        Object.values(instances || {}).forEach(inst => {
            if (!inst) return;
            const f = (inst.selected_model_file || '').toLowerCase();
            let ok = cats.includes(inst.category);
            if (!ok && kind === 'embedding') {
                ok = !!(inst.parameters || {}).embeddings || f.includes('embedding');
            }
            if (!ok && kind === 'rerank') {
                ok = !!(inst.parameters || {}).reranking || f.includes('rerank');
            }
            if (ok) out.push(inst);
        });
        // 运行中的排前面
        out.sort((a, b) => (b.status === 'running' ? 1 : 0) - (a.status === 'running' ? 1 : 0));
        return out;
    }

    function pgFillModelSelect(selId, kind) {
        const sel = document.getElementById(selId);
        if (!sel) return;
        const prev = sel.value;
        const list = pgCandidates(kind);
        if (!list.length) {
            sel.innerHTML = '<option value="">（该类别暂无已部署实例，请先在 Launch Model 启动）</option>';
            return;
        }
        sel.innerHTML = list.map(inst => {
            const label = inst.model_id + (inst.status === 'running' ? ' · 运行中' : ' · ' + inst.status);
            return `<option value="${escapeAttr(inst.model_id)}">${escapeHtml(label)}</option>`;
        }).join('');
        if (prev && list.some(i => i.model_id === prev)) sel.value = prev;
    }

    function pgSelectedModel(selId) {
        const sel = document.getElementById(selId);
        return sel ? sel.value : '';
    }

    async function runEmbedding() {
        const inp = document.getElementById('pgEmbInput').value.trim();
        const texts = inp.split('|').map(s=>s.trim()).filter(Boolean);
        const perfEl = document.getElementById('pgEmbPerf'), resEl = document.getElementById('pgEmbResult');
        const modelId = pgSelectedModel('pgEmbModel');
        if (!modelId) { pgFillModelSelect('pgEmbModel', 'embedding'); if (!pgSelectedModel('pgEmbModel')) return showNotRun(perfEl, resEl, 'embedding'); }
        const sel = pgSelectedModel('pgEmbModel') || modelId;
        const inst = instances[sel];
        if (!inst || inst.status !== 'running') return showNotRun(perfEl, resEl, sel || 'embedding');
        perfEl.innerHTML = '<div class="pg-placeholder">计算中...</div>';
        const t0 = performance.now();
        let t1;
        try {
            const r = await fetch('/v1/embeddings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model: sel, input: texts.length===1?texts[0]:texts})});
            t1 = performance.now();
            const data = await r.json();
            const e2e = (t1 - t0)/1000;
            const dim = data.data && data.data[0] ? (data.data[0].embedding||[]).length : 0;
            const n = data.data ? data.data.length : 0;
            perfEl.innerHTML = perfCard('Embedding 性能', [['端到端', e2e.toFixed(3)+' s'], ['向量条数', n], ['维度', dim], ['吞吐', (e2e>0?(n/e2e):0).toFixed(1)+' 条/s']]);
            resEl.textContent = JSON.stringify(data.data ? data.data.map(x=>({index:x.index, dim:(x.embedding||[]).length, preview:(x.embedding||[]).slice(0,3)})) : data, null, 2);
        } catch(e){ perfEl.innerHTML='<div class="pg-placeholder" style="color:var(--error)">Error: '+escapeHtml(e.message)+'</div>'; }
    }

    function showToast(msg){ toast(msg, 'info'); }

    async function runASR() {
        const fi = document.getElementById('pgAsrFile');
        const perfEl = document.getElementById('pgAsrPerf'), resEl = document.getElementById('pgAsrResult');
        if (!fi.files || !fi.files[0]) return showToast('请先上传音频');
        pgFillModelSelect('pgAsrModel', 'asr');
        const asrId = pgSelectedModel('pgAsrModel');
        const asrInst = instances[asrId];
        if (!asrInst || asrInst.status !== 'running') return showNotRun(perfEl, resEl, asrId || 'asr');
        perfEl.innerHTML = '<div class="pg-placeholder">识别中...</div>';
        const fd = new FormData();
        fd.append('file', fi.files[0]);
        fd.append('model', asrId);
        const t0 = performance.now();
        try {
            const r = await fetch('/v1/audio/transcriptions', {method:'POST', body: fd});
            const t1 = performance.now();
            const text = await r.text();
            const e2e = (t1 - t0)/1000;
            perfEl.innerHTML = perfCard('ASR 性能', [['端到端', e2e.toFixed(2)+'s'], ['音频', (fi.files[0].size/1024).toFixed(0)+' KB'], ['RTF', e2e>0?(e2e/ (fi.files[0].size/1024) ).toFixed(3):'--']]);
            resEl.innerHTML = '<pre style="margin:0">'+esc(text)+'</pre>';
        }catch(e){ resEl.innerHTML='<div class="pg-placeholder" style="color:var(--error)">'+esc(e.message)+'</div>'; }
    }

    async function runTTS() {
        const text = document.getElementById('pgTtsInput').value.trim();
        const perfEl = document.getElementById('pgTtsPerf'), resEl = document.getElementById('pgTtsResult');
        if(!text) return showToast('请输入文本');
        pgFillModelSelect('pgTtsModel', 'tts');
        const ttsId = pgSelectedModel('pgTtsModel');
        const ttsInst = instances[ttsId];
        if (!ttsInst || ttsInst.status !== 'running') return showNotRun(perfEl, resEl, ttsId || 'tts');
        perfEl.innerHTML = '<div class="pg-placeholder">生成中...</div>';
        const t0 = performance.now();
        try {
            const r = await fetch('/v1/audio/speech', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model: ttsId, input:text})});
            const blob = await r.blob();
            const t1 = performance.now();
            const e2e = (t1-t0)/1000;
            perfEl.innerHTML = perfCard('TTS 性能', [['端到端', e2e.toFixed(2)+'s'], ['音频大小', (blob.size/1024).toFixed(1)+' KB'], ['RTF', e2e>0?'-' : '--']]);
            const url = URL.createObjectURL(blob);
            resEl.innerHTML = '<audio controls src="'+url+'"></audio><div style="margin-top:6px;font-size:11px;color:var(--text-muted)">'+ (blob.size/1024).toFixed(1)+' KB 生成耗时 '+e2e.toFixed(2)+'s</div>';
        }catch(e){ resEl.innerHTML='<div class="pg-placeholder" style="color:var(--error)">'+esc(e.message)+'</div>'; }
    }

    async function runRerank() {
        const query = document.getElementById('pgRerankQuery').value.trim();
        const docs = document.getElementById('pgRerankDocs').value.split('\n').map(s=>s.trim()).filter(Boolean);
        const perfEl = document.getElementById('pgRerankPerf'), resEl = document.getElementById('pgRerankResult');
        if(!query||!docs.length) return showToast('请输入 query 与文档');
        pgFillModelSelect('pgRerankModel', 'rerank');
        const rrId = pgSelectedModel('pgRerankModel');
        const rrInst = instances[rrId];
        if (!rrInst || rrInst.status !== 'running') return showNotRun(perfEl, resEl, rrId || 'reranker');
        perfEl.innerHTML = '<div class="pg-placeholder">重排序中...</div>';
        const t0 = performance.now(); let t1;
        try {
            const r = await fetch('/v1/rerank', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model: rrId, query, documents: docs})});
            t1 = performance.now();
            const data = await r.json();
            const e2e = (t1-t0)/1000;
            const results = data.results||data.data || [];
            perfEl.innerHTML = perfCard('重排序', [['端到端', e2e.toFixed(3)+'s'], ['文档数', docs.length], ['吞吐', (e2e>0?(docs.length/e2e):0).toFixed(1)+' doc/s']]);
            resEl.innerHTML = results.map(x=>`<div style="padding:4px 0;border-bottom:1px solid var(--border)"><b>#${x.index}</b> score=${(x.score||0).toFixed(4)} · ${esc(docs[x.index]||x.document||'')}</div>`).join('') || esc(JSON.stringify(data));
        }catch(e){ resEl.textContent='Error: '+e.message; }
    }

    async function runOCR() {
        const fi = document.getElementById('pgOcrFile');
        const perfEl = document.getElementById('pgOcrPerf'), resEl = document.getElementById('pgOcrResult');
        if (!fi.files || !fi.files[0]) return showToast('请先上传图片');
        perfEl.innerHTML = '<div class="pg-placeholder">识别中...</div>';
        const b64 = await new Promise((res,rej)=>{const rd=new FileReader();rd.onload=()=>res(rd.result.split(',',2)[1]);rd.onerror=rej;rd.readAsDataURL(fi.files[0]);});
        pgFillModelSelect('pgOcrModel', 'ocr');
        const ocrId = pgSelectedModel('pgOcrModel');
        const ocrInst = instances[ocrId];
        if (!ocrInst || ocrInst.status !== 'running') return showNotRun(perfEl, resEl, ocrId || 'ocr');
        const t0 = performance.now();
        try{
            const r = await fetch('/v1/ocr', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model: ocrId, image:b64})});
            const t1 = performance.now();
            const data = await r.json();
            const text = data.text || data.content || JSON.stringify(data);
            const e2e = (t1-t0)/1000;
            perfEl.innerHTML = perfCard('OCR 性能', [['端到端', e2e.toFixed(2)+'s'], ['图片', (fi.files[0].size/1024).toFixed(0)+' KB'], ['字数', (typeof text==='string'?text.length:0)]]);
            resEl.innerHTML = '<div style="white-space:pre-wrap">'+esc(typeof text==='string'?text:JSON.stringify(text))+'</div>';
        }catch(e){ resEl.textContent='Error: '+e.message; }
    }

    function esc(s){ return escapeHtml(String(s)); }

    function showNotRun(perfEl, resEl, name){
        if(resEl) resEl.innerHTML = '<div class="pg-placeholder" style="color:var(--warning)">模型 ' + name + ' 未运行，请先在 Launch Model 启动。</div>';
        if(perfEl) perfEl.innerHTML = '';
    }

    async function generateImage() {
        const prompt = document.getElementById('pgT2IPrompt').value;
        const width = parseInt(document.getElementById('pgT2IWidth').value);
        const height = parseInt(document.getElementById('pgT2IHeight').value);
        const steps = parseInt(document.getElementById('pgT2ISteps').value);
        const guidance = parseFloat(document.getElementById('pgT2IGuidance').value);
        const num = Math.min(Math.max(parseInt(document.getElementById('pgT2INum').value||'1'),1),4);
        const seed = parseInt(document.getElementById('pgT2ISeed').value || '-1');
        const result = document.getElementById('pgT2IResult');
        const t0 = Date.now();
        const timer = startLiveTimer(result, t0);
        result.innerHTML = '<div class="pg-placeholder">Generating... <span class="pg-live-time">⏱ 0s</span></div>';
        try {
            const resp = await fetch('/v1/images/generations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: prompt, width: width, height: height, num_inference_steps: steps, guidance_scale: guidance, n: num, seed: seed, save_to_disk: true }),
            });
            const data = await resp.json();
            stopLiveTimer(timer);
            const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
            if (data.data && data.data[0] && data.data[0].b64_json) {
                // 图片卡片网格 (支持多图)
                const imgs = data.data.map((d,i) => {
                    const dl = (data.download_urls && data.download_urls[i]) || '';
                    return `<div class="pg-img-card">
                        <img src="data:image/png;base64,${d.b64_json}" alt="生成图 ${i+1}" loading="lazy">
                        <div class="pg-img-meta">${width}×${height} · #${i+1}</div>
                        ${dl ? `<a class="pg-img-dl" href="${dl}" target="_blank" title="下载">⬇ 下载</a>` : ''}
                    </div>`;
                }).join('');
                const total = data.saved_paths && data.saved_paths.length ? data.saved_paths.join('<br>') : '';
                result.innerHTML = `<div class="pg-img-grid ${data.data.length>1?'multi':''}">${imgs}</div>
                    <div style="margin-top:10px;font-size:12px;color:var(--text-muted);text-align:center;">提交到完成耗时 ${elapsed}s</div>
                    ${total ? `<div style="margin-top:6px;font-size:11px;color:var(--text-muted);text-align:center;word-break:break-all;">💾 ${total}</div>` : ''}`;
            } else {
                result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">' + escapeHtml(JSON.stringify(data)) + '</div>';
            }
        } catch (e) {
            stopLiveTimer(timer);
            result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">Error: ' + escapeHtml(e.message) + '</div>';
        }
    }

    async function callApi() {
        const endpoint = document.getElementById('pgApiEndpoint').value;
        const result = document.getElementById('pgApiResult');
        result.textContent = 'Loading...';
        try {
            const resp = await fetch(endpoint);
            const data = await resp.json();
            result.textContent = JSON.stringify(data, null, 2);
        } catch (e) {
            result.textContent = 'Error: ' + e.message;
        }
    }

    // ---- Live timing helpers (v0.7: 提交->完成 实时计时) ----
    function startLiveTimer(container, t0) {
        const iv = setInterval(() => {
            const s = ((Date.now() - t0) / 1000).toFixed(1) + 's';
            const el = container.querySelector('.pg-live-time');
            if (el) el.textContent = '⏱ ' + s;
        }, 500);
        return iv;
    }
    function stopLiveTimer(iv) { if (iv) clearInterval(iv); }

    // ---- T2V (Wan2.2-T2V-A14B) ----
    async function generateVideo() {
        const prompt = document.getElementById('pgT2VPrompt').value;
        const result = document.getElementById('pgT2VResult');
        const hint = document.getElementById('pgT2VHint');
        const saveDisk = document.getElementById('pgT2VSaveDisk')?.checked || false;

        const payload = {
            prompt: prompt,
            resolution: document.getElementById('pgT2VResolution').value,
            num_frames: parseInt(document.getElementById('pgT2VFrames').value),
            num_inference_steps: parseInt(document.getElementById('pgT2VSteps').value),
            guidance_scale: parseFloat(document.getElementById('pgT2VGuidance').value),
            guidance_scale_2: parseFloat(document.getElementById('pgT2VGuidance2').value),
            seed: parseInt(document.getElementById('pgT2VSeed').value),
            save_to_disk: saveDisk,
            video_type: 't2v',
        };
        result.innerHTML = '<div class="pg-placeholder">生成中... 5s 480P 预计 5-15 分钟（依赖 offload/quant 设置） <span class="pg-live-time">⏱ 0s</span></div>';
        hint.textContent = '已发送, 等待推理...';
        const t0 = Date.now();
        const timer = startLiveTimer(result, t0);
        try {
            const resp = await fetch('/v1/videos/generations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            stopLiveTimer(timer);
            const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
            if (data.data && data.data[0] && data.data[0].b64_json) {
                const mime = data.data[0].mime || 'video/mp4';
                const src = `data:${mime};base64,${data.data[0].b64_json}`;
                let extra = '';
                if (data.download_urls && data.download_urls.length) {
                        extra = `<div style="margin-top:8px;color:var(--success);font-size:12px;">💾 已落盘: ${escapeHtml(data.saved_paths && data.saved_paths[0])} · <a href="${data.download_urls[0]}" target="_blank" style="color:var(--accent)">⬇ 下载 MP4</a></div>`;
                } else if (data.saved_paths && data.saved_paths.length) {
                    extra = `<div style="margin-top:8px;color:var(--success);font-size:12px;">💾 已落盘: ${escapeHtml(data.saved_paths[0])}</div>`;
                }
                result.innerHTML = `<video controls autoplay loop muted style="max-width:100%;border-radius:8px;" src="${src}"></video><div style="margin-top:8px;color:var(--text-muted);font-size:12px;">提交到完成耗时 ${elapsed}s · ${(data.data[0].b64_json.length * 3 / 4 / 1024 / 1024).toFixed(1)} MB</div>${extra}`;
                hint.textContent = '✅ 完成 (' + elapsed + 's)';
            } else {
                result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">' + escapeHtml(JSON.stringify(data)) + '</div>';
                hint.textContent = '❌ 失败';
            }
        } catch (e) {
            stopLiveTimer(timer);
            result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">Error: ' + escapeHtml(e.message) + '</div>';
            hint.textContent = '❌ 异常';
        }
    }

    // ---- I2V (Wan2.2-I2V-A14B) ----
    async function generateI2V() {
        const prompt = document.getElementById('pgI2VPrompt').value;
        const fileInput = document.getElementById('pgI2VImage');
        const result = document.getElementById('pgI2VResult');
        const saveDisk = document.getElementById('pgI2VSaveDisk')?.checked || false;
        if (!fileInput.files || !fileInput.files[0]) {
            result.innerHTML = '<div class="pg-placeholder" style="color:var(--warning)">请先上传首帧图</div>';
            return;
        }
        const file = fileInput.files[0];
        // base64 编码
        const b64 = await new Promise((resolve, reject) => {
            const r = new FileReader();
            r.onload = () => resolve(r.result.split(',', 2)[1]);
            r.onerror = reject;
            r.readAsDataURL(file);
        });
        const payload = {
            prompt: prompt,
            image: b64,
            resolution: document.getElementById('pgI2VResolution').value,
            num_frames: parseInt(document.getElementById('pgI2VFrames').value),
            num_inference_steps: parseInt(document.getElementById('pgI2VSteps').value),
            guidance_scale: parseFloat(document.getElementById('pgI2VGuidance').value),
            seed: parseInt(document.getElementById('pgI2VSeed').value),
            save_to_disk: saveDisk,
            video_type: 'i2v',
        };
        result.innerHTML = '<div class="pg-placeholder">生成中... 5s 480P 预计 5-15 分钟 <span class="pg-live-time">⏱ 0s</span></div>';
        const t0 = Date.now();
        const timer = startLiveTimer(result, t0);
        try {
            const resp = await fetch('/v1/videos/generations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            stopLiveTimer(timer);
            const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
            if (data.data && data.data[0] && data.data[0].b64_json) {
                const mime = data.data[0].mime || 'video/mp4';
                const src = `data:${mime};base64,${data.data[0].b64_json}`;
                let extra = '';
                if (data.download_urls && data.download_urls.length) {
                    extra = `<div style="margin-top:8px;color:var(--success);font-size:12px;">💾 已落盘: ${escapeHtml(data.saved_paths && data.saved_paths[0])} · <a href="${data.download_urls[0]}" target="_blank" style="color:var(--accent)">⬇ 下载 MP4</a></div>`;
                } else if (data.saved_paths && data.saved_paths.length) {
                    extra = `<div style="margin-top:8px;color:var(--success);font-size:12px;">💾 已落盘: ${escapeHtml(data.saved_paths[0])}</div>`;
                }
                result.innerHTML = `<video controls autoplay muted style="max-width:100%;border-radius:8px;" src="${src}"></video><div style="margin-top:8px;color:var(--text-muted);font-size:12px;">提交到完成耗时 ${elapsed}s</div>${extra}`;
            } else {
                result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">' + escapeHtml(JSON.stringify(data)) + '</div>';
            }
        } catch (e) {
            stopLiveTimer(timer);
            result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">Error: ' + escapeHtml(e.message) + '</div>';
        }
    }

    // ---- Theme Toggle (light / dark) ----
    function setupThemeToggle() {
        const btn = document.getElementById('themeToggle');
        if (!btn) return;
        // 读取上次选择, 默认 dark
        const saved = localStorage.getItem('amm-theme') || 'dark';
        applyTheme(saved);
        btn.addEventListener('click', () => {
            const cur = document.documentElement.getAttribute('data-theme') || 'dark';
            const next = cur === 'dark' ? 'light' : 'dark';
            applyTheme(next);
            localStorage.setItem('amm-theme', next);
        });
    }
    function applyTheme(theme) {
        const root = document.documentElement;
        if (theme === 'light') root.setAttribute('data-theme', 'light');
        else root.removeAttribute('data-theme');
        const btn = document.getElementById('themeToggle');
        if (btn) btn.textContent = theme === 'light' ? '☀️' : '🌙';
    }

    async function reloadServerConfig() {
        toast('重载配置...');
        const r = await apiPost('/settings/reload', {});
        toast(r && r.ok ? '✅ 配置已重载' : ('❌ ' + ((r && r.error) || '失败')), r && r.ok ? 'success' : 'error');
        await refreshAll();
    }

    async function restartServer() {
        if (!confirm('确认重启 AMM 服务？所有运行中的模型将停止。')) return;
        toast('正在重启服务...');
        const r = await apiPost('/settings/restart', {});
        toast(r && r.ok ? '✅ 服务重启中，页面将刷新' : ('❌ ' + ((r && r.error) || '失败')), r && r.ok ? 'success' : 'error');
        if (r && r.ok) setTimeout(() => location.reload(), 2500);
    }

    async function downloadServerLog() {
        const data = await apiGet('/logs/server?lines=500');
        if (data && data.logs) {
            const blob = new Blob([data.logs.join('\n')], { type: 'text/plain' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'amm_server.log';
            a.click();
            URL.revokeObjectURL(a.href);
        } else { toast('❌ 日志获取失败', 'error'); }
    }

    let fbState = { modelId: null, engine: '', curPath: '', stack: [] };

    function openFileBrowser(modelId) {
        const inst = instances[modelId];
        const engine = (inst && inst.engine_type) || 'llama_cpp';
        fbState = { modelId: modelId, engine: engine, curPath: '', stack: [], curDir: '' };
        const modal = document.getElementById('fileBrowserModal');
        modal.classList.add('active');
        document.getElementById('fileBrowserTitle').textContent = '📂 浏览模型文件 - ' + modelId + ' (' + engine + ')';
        fbLoadDir('');
    }
    function closeFileBrowser() { document.getElementById('fileBrowserModal')?.classList.remove('active'); }

    async function fbLoadDir(relDir) {
        const body = document.getElementById('fileBrowserBody');
        if (!body) return;
        body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">加载中...</div>';
        const data = await apiGet('/fs/list?path=' + encodeURIComponent(relDir));
        if (!data) { body.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">加载失败</div>'; return; }
        fbState.curDir = relDir;
        fbState.curPath = data.current || '';

        const ext = (fbState.engine === 'llama_cpp' || fbState.engine === 'llama') ? ['.gguf', '.bin'] : ['.safetensors', '.bin', '.gguf'];
        let html = '';
        // 路径输入 + 面包屑导航
        html += `<div style="margin-bottom:8px;">`;
        html += `<div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:12px;color:var(--text-muted)">🗂 路径:</span>
            <input class="form-input fb-path-input" id="fbPathInput" value="${escapeHtml(data.relative || '')}" placeholder="/models 根目录" style="flex:1;font-family:monospace;font-size:12px">
            <button class="btn btn-sm" onclick="fbGoPath()">前往</button>
            <button class="btn btn-sm" onclick="fbUp()" title="上级目录">↑ 上级</button>
        </div>`;
        html += `<div style="margin-top:6px;font-size:12px;display:flex;align-items:center;gap:4px;flex-wrap:wrap;">`;
        html += `<span style="color:var(--text-muted);font-size:11px">breadcrumb:</span>` +
            (_buildBreadcrumb(relDir) || '');
        html += `</div></div>`;
        // 当前路径标识
        html += `<div style="font-size:11px;color:var(--text-muted);margin-bottom:8px;padding:4px 8px;background:var(--bg-input);border-radius:4px;word-break:break-all;">📁 /models${data.relative ? '/' + escapeHtml(data.relative) : ''}</div>`;
        // 目录列表
        html += '<div class="fb-dirs"><div style="font-size:11px;color:var(--text-muted);margin:4px 0">子目录 (' + (data.dirs || []).length + '):</div>';
        if (data.parent_relative !== null && data.parent_relative !== undefined) {
            html += `<div class="fb-item fb-dir" onclick="fbGotoDir('${escapeAttr(data.parent_relative)}')" title="返回上级">📁 <span>.. 上级目录</span></div>`;
        }
        (data.dirs || []).forEach(d => {
            html += `<div class="fb-item fb-dir" onclick="fbGotoDir('${escapeAttr(d.path)}')">📁 ${escapeHtml(d.name)}</div>`;
        });
        if (!(data.dirs || []).length && data.parent_relative === null) {
            html += '<div style="font-size:12px;color:var(--text-muted);padding:4px 0">(无子目录)</div>';
        }
        html += '</div>';
        // 文件
        html += `<div style="font-size:11px;color:var(--text-muted);margin:4px 0">📄 模型文件 (${modelFilesFor(data, ext).length}):</div>`;
        const modelFiles = modelFilesFor(data, ext);
        if (!modelFiles.length) {
            html += '<div style="font-size:12px;color:var(--text-muted);padding:4px 0">(当前目录无匹配模型文件，请进入子目录继续浏览)</div>';
        }
        modelFiles.forEach(f => {
            const curFile = instances[fbState.modelId] && instances[fbState.modelId].selected_model_file;
            const picked = !!curFile && curFile === f.path;
            html += `<div class="fb-item fb-file${picked ? ' fb-picked' : ''}" onclick="fbPickFile('${fbState.modelId}', '${escapeAttr(f.path)}')">🧩 ${escapeHtml(f.name)} <span style="float:right;color:var(--text-muted);font-size:11px">${f.size_mb}MB</span></div>`;
        });
        // 选整个目录 (HF/Diffusers 目录型模型)
        if (data.relative) {
            html += `<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:8px;">`;
            html += `<button class="btn btn-sm btn-primary" onclick="fbPickDir('${fbState.modelId}')">✔ 选当前整个目录作为模型 "${escapeHtml(data.relative)}"</button>`;
            html += '</div>';
        }
        body.innerHTML = html;
    }

    function _buildBreadcrumb(relDir) {
        if (!relDir) return '<span class="fb-crumb fb-crumb-root" onclick="fbGotoDir(\'\')">/models</span>';
        const parts = relDir.split('/').filter(Boolean);
        let html = '';
        let acc = '';
        html += '<span class="fb-crumb fb-crumb-root" onclick="fbGotoDir(\'\')">/models</span>';
        parts.forEach((p, i) => {
            acc = acc + (acc ? '/' : '') + p;
            html += ' <span style="color:var(--text-muted)">/</span> ';
            html += `<span class="fb-crumb" onclick="fbGotoDir('${escapeAttr(acc)}')">${escapeHtml(p)}</span>`;
        });
        return html;
    }

    function fbGoPath() {
        const v = document.getElementById('fbPathInput')?.value || '';
        fbLoadDir(v.replace(/^\/models\/?/, ''));
    }

    // 过滤模型文件
    function modelFilesFor(data, ext) { return (data.files || []).filter(f => ext.includes(f.ext)); }


    function fbGotoDir(d) { fbLoadDir(d); }
    function fbUp() {
        const parts = fbState.curDir.split('/').filter(Boolean);
        parts.pop();
        fbLoadDir(parts.join('/'));
    }

    async function fbPickFile(modelId, path) {
        await selectModelFile(modelId, path);
        await fbCheckPreset(modelId, path);
        _syncModalModelFile(path);
        await refreshAll();
        closeFileBrowser();
    }

    async function fbPickDir(modelId) {
        const dir = fbState.curDir;
        await selectModelFile(modelId, dir);
        await fbCheckPreset(modelId, dir);
        _syncModalModelFile(dir);
        await refreshAll();
        closeFileBrowser();
    }

    // Dashboard 弹窗里点「浏览」时，选完直接把路径回填到输入框
    function _syncModalModelFile(path) {
        const el = document.getElementById('modalModelFile');
        if (el && path) el.value = path;
    }

    async function fbCheckPreset(modelId, file) {
        const inst = config[modelId] && instances[modelId] ? instances[modelId] : null;
        const engine = (inst && inst.engine_type) || fbState.engine || 'llama_cpp';
        const r = await apiGet('/instances/preset?model_file=' + encodeURIComponent(file) + '&engine=' + encodeURIComponent(engine));
        const box = document.getElementById('preset-' + modelId);
        if (!box) return;
        if (r && r.found) {
            const dataHtml = '<pre style="max-height:120px;overflow:auto;font-size:11px;background:var(--bg-input);padding:8px;border-radius:6px">' + escapeHtml(JSON.stringify(r.data, null, 2)) + '</pre>';
            box.innerHTML = `<div class="preset-found" style="margin-bottom:10px;padding:10px;background:var(--bg-input);border:1px solid var(--success);border-radius:6px">
                <div style="font-size:12px;color:var(--success);font-weight:600;margin-bottom:6px">🎯 检测到预设配置: ${escapeHtml(r.path)}</div>
                ${dataHtml}
                <div style="margin-top:8px;display:flex;gap:8px">
                    <button class="btn btn-sm btn-primary" onclick="applyPresetFile('${modelId}')">📥 加载此预设</button>
                    <button class="btn btn-sm" onclick="closePresetBox('${modelId}')">忽略</button>
                </div>
            </div>`;
        } else {
            box.innerHTML = '';
        }
    }

    async function applyPresetFile(modelId) {
        const inst = instances[modelId];
        const engine = (inst && inst.engine_type) || fbState.engine || 'llama_cpp';
        const file = (inst && inst.selected_model_file) || '';
        const r = await apiPost('/instances/preset/apply', { model_id: modelId, model_file: file, engine: engine });
        if (r && r.ok) {
            toast('✅ 已应用预设: ' + r.preset_path + ' (' + r.applied.length + ' 参数)', 'success');
            await refreshAll();
        } else {
            toast('❌ ' + ((r && r.error) || '应用失败'), 'error');
        }
    }

    function escapeAttr(s) { return escapeHtml(String(s)); }
    function closePresetBox(modelId) { const b = document.getElementById('preset-' + modelId); if (b) b.innerHTML = ''; }



    // ---- Model Download (v0.5) : 代理 / 版本选择 / 文件清单 / 断点续传 / 速度 / ETA ----
    // 下载任务缓存 (用于局部进度刷新)
    let dlTasksCache = [];

    function fmtSize2(bytes) {
        if (!bytes && bytes !== 0) return '-';
        if (bytes === 0) return '0 B';
        const units = ['B','KB','MB','GB','TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
    }
    function fmtSpeed(bps) {
        if (!bps) return '0 B/s';
        if (bps >= 1048576) return (bps/1048576).toFixed(1)+' MB/s';
        if (bps >= 1024) return (bps/1024).toFixed(1)+' KB/s';
        return bps.toFixed(0)+' B/s';
    }
    function fmtEta(sec) {
        if (sec === null || sec === undefined || isNaN(sec) || sec < 0) return '';
        if (sec < 60) return '剩余 ' + Math.ceil(sec) + 's';
        if (sec < 3600) {
            const m = Math.floor(sec/60), s = Math.ceil(sec%60);
            return '剩余 ' + m + 'm' + (s<10?'0':'') + s + 's';
        }
        const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60);
        return '剩余 ' + h + 'h' + m + 'm';
    }

    // 代理相关
    async function loadProxySettings() {
        const r = await apiGet('/models/download/proxy');
        if (!r || !r.proxy) return;
        const p = r.proxy;
        const en = document.getElementById('dlProxyEnabled');
        const url = document.getElementById('dlProxyUrl');
        if (en) en.checked = !!p.enabled;
        if (url) { url.value = p.url || ''; url.disabled = !p.enabled; }
    }
    function toggleProxyInput() {
        const en = document.getElementById('dlProxyEnabled');
        const url = document.getElementById('dlProxyUrl');
        if (url) url.disabled = !en.checked;
        if (en && en.checked && url && !url.value) url.focus();
    }
    async function saveProxy() {
        const en = document.getElementById('dlProxyEnabled');
        const url = document.getElementById('dlProxyUrl');
        const body = { enabled: en.checked, url: url.value.trim() };
        const r = await apiPost('/models/download/proxy', body);
        if (r && r.ok) toast('✅ 代理设置已保存' + (body.enabled ? ' (' + body.url + ')' : ''), 'success');
        else toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
    }

    // ---- HuggingFace 镜像站 + API Key (v1.1) ----
    async function hfLoadSettings() {
        const r = await apiGet('/models/download/settings');
        if (!r || !r.settings) return;
        const s = r.settings;
        const preset = document.getElementById('hfEndpointPreset');
        const ep = document.getElementById('hfEndpoint');
        const tok = document.getElementById('hfToken');
        const state = document.getElementById('hfTokenState');
        if (ep) ep.value = s.hf_endpoint || '';
        if (preset) {
            const urls = Array.from(preset.options).map(o => o.value);
            preset.value = urls.includes(s.hf_endpoint) ? s.hf_endpoint : '__custom__';
        }
        if (ep && !ep.value) ep.value = s.hf_endpoint || 'https://hf-mirror.com';
        if (tok) {
            tok.value = '';
            tok.placeholder = s.has_hf_token
                ? `已保存 ${s.hf_token_masked}（留空 = 不修改）`
                : 'hf_xxx（可选）';
        }
        if (state) {
            state.textContent = s.has_hf_token ? `✅ 已配置 ${s.hf_token_masked}` : '未配置';
            state.style.color = s.has_hf_token ? 'var(--success)' : 'var(--text-muted)';
        }
    }

    function hfPresetChange() {
        const preset = document.getElementById('hfEndpointPreset');
        const ep = document.getElementById('hfEndpoint');
        if (!preset || !ep) return;
        if (preset.value === '__custom__') { ep.focus(); return; }
        ep.value = preset.value;
    }

    async function hfSaveSettings() {
        const ep = (document.getElementById('hfEndpoint')?.value || '').trim();
        const tok = (document.getElementById('hfToken')?.value || '').trim();
        const body = { hf_endpoint: ep };
        if (tok) body.hf_token = tok;
        const r = await apiPost('/models/download/settings', body);
        if (!r || r.error) { toast('保存失败: ' + ((r && r.error) || ''), 'error'); return; }
        toast('✅ HuggingFace 设置已保存', 'success');
        await hfLoadSettings();
    }

    async function hfClearToken() {
        if (!confirm('确定清除已保存的 HuggingFace API Key？')) return;
        const r = await apiPost('/models/download/settings', { hf_token: '' });
        if (!r || r.error) { toast('清除失败', 'error'); return; }
        toast('已清除 HF API Key', 'success');
        await hfLoadSettings();
    }

    // 版本与文件查询
    async function loadDownloadVersions() {
        const modelId = document.getElementById('dlModelId')?.value.trim();
        if (!modelId) return toast('请输入模型 ID', 'error');
        const source = document.getElementById('dlSource')?.value || 'huggingface';
        const panel = document.getElementById('dlVersionPanel');
        const verEl = document.getElementById('dlVersions');
        if (verEl) verEl.innerHTML = '<p style="font-size:12px;color:var(--text-muted)">查询中...</p>';
        if (panel) panel.style.display = 'block';
        toast('🔍 查询 ' + modelId + ' 版本...');
        const r = await apiPost('/models/downloads', { model_id: modelId, source: source });
        if (!r || r.error) {
            if (verEl) verEl.innerHTML = '<p style="font-size:12px;color:var(--error)">查询失败: ' + escapeHtml((r && r.error) || '网络错误') + '</p>';
            return;
        }
        const versions = r.versions || [];
        if (!versions.length) { if (verEl) verEl.innerHTML = '<p style="font-size:12px;color:var(--error)">未找到任何版本</p>'; return; }
        verEl.innerHTML = versions.map((v, i) => `
            <label style="display:inline-flex;align-items:center;gap:6px;margin:2px 8px 2px 0;cursor:pointer;padding:3px 6px;border:1px solid var(--border);border-radius:6px;background:var(--bg-card);">
                <input type="radio" name="dlRev" value="${escapeAttr(v.revision)}" ${i===0?'checked':''} onchange="window.selectDownloadVersion()">
                <span style="font-size:12px;">${escapeHtml(v.revision)}</span>
                <span style="font-size:11px;color:var(--text-muted);">(${escapeHtml(v.type)})</span>
                <span style="font-size:11px;color:var(--text-muted);">${v.total_size?fmtSize2(v.total_size):''}</span>
            </button>
        `).join('');
        // 记录最新选中的版本文件
        window.__dlVersions = versions;
        window.__dlSelectedRev = versions[0];
        showDownloadVersionFiles(versions[0]);
    }
    function showDownloadVersion() {
        const val = document.querySelector('input[name="dlver"]:checked')?.value;
        const versions = window.__dlVersions || [];
        const v = versions.find(x => x.revision === val);
        if (v) { window.__dlSelectedRev = v; showDownloadVersionFiles(v); }
    }
    function vSize(bytes) { return fmtSize2(bytes); }
    function showDownloadVersionFiles(v) {
        const el = document.getElementById('dlVersionFiles');
        const stat = document.getElementById('dlFilesStats');
        if (!el) return;
        if (!v) { el.innerHTML = '<p style="font-size:12px;color:var(--text-muted)">无文件信息</p>'; return; }
        const files = v.files || [];
        if (!files.length) { el.innerHTML = '<p style="font-size:12px;color:var(--text-muted)">' + (v.error ? '加载失败: ' + escapeHtml(v.error) : '未获取到文件清单') + '</p>'; return; }
        el.innerHTML = files.map((f, i) => `
            <label style="display:flex;align-items:center;gap:8px;padding:3px 2px;cursor:pointer;font-size:12px;">
                <input type="checkbox" class="dl-file-cb" value="${escapeAttr(f.filename)}" onchange="updateDlFilesStats()">
                <span style="flex:1;word-break:break-all;">${escapeHtml(f.filename)}</span>
                <span style="color:var(--text-muted);font-size:11px;">${f.size?fmtSize2(f.size):''}</span>
            </label>
        `).join('');
        updateDlFilesStats();
        if (stat) stat.textContent = '共 ' + files.length + ' 个文件, 合计 ' + fmtSize2(v.total_size||0);
    }
    function updateDlFilesStats() {
        const cbs = document.querySelectorAll('.dl-file-cb');
        const el = document.getElementById('dlFilesStats');
        if (!el || !cbs.length) return;
        const sel = Array.from(cbs).filter(c => c.checked);
        el.textContent = '已选 ' + sel.length + ' 个文件 / 共 ' + cbs.length;
    }
    function selectAllVersionFiles() {
        document.querySelectorAll('.dl-file-cb').forEach(c => c.checked = true); updateDlFilesStats();
    }
    function deselectAllVersionFiles() {
        document.querySelectorAll('.dl-file-cb').forEach(c => c.checked = false); updateDlFilesStats();
    }

    async function startModelDownload() {
        const modelId = document.getElementById('dlModelId')?.value.trim();
        if (!modelId) return toast('请输入模型 ID', 'error');
        const source = document.getElementById('dlSource')?.value || 'modelscope';
        const category = document.getElementById('dlCategory')?.value || '';
        const rev = (window.__dlSelectedRev || {}).revision || '';
        const target_dir = document.getElementById('dlTargetDir')?.value.trim() || '';
        const cbs = document.querySelectorAll('.dl-file-cb');
        let files = [];
        if (cbs.length) files = Array.from(cbs).filter(c => c.checked).map(c => c.value);
        const total = (window.__dlSelectedRev || {}).total_size || 0;
        const total_files = (window.__dlSelectedRev && (window.__dlSelectedRev.files||[]).length) || 0;
        toast('⬇️ 开始下载 ' + modelId + (rev?' ('+rev+')':'') + ' ...');
        const r = await apiPost('/models/download', {
            model_id: modelId, source: source, category: category,
            revision: rev, files: files, total: total, total_files: total_files,
            target_dir: target_dir,
        });
        if (r && r.ok) {
            toast('✅ 已提交下载任务 ' + r.task_id, 'success');
            await refreshDownloadStatus();
            startDlProgressPoll();
        } else {
            toast('❌ ' + ((r && r.error) || '提交失败'), 'error');
        }
    }

    let dlPollTimer = null;
    function startDlProgressPoll() {
        if (dlPollTimer) clearInterval(dlPollTimer);
        // 高频率刷新任务列表中的进度
        dlPollTimer = setInterval(async () => {
            const r = await apiGet('/models/download/status');
            if (!r || !r.tasks) return;
            const running = r.tasks.some(t => t.status === 'downloading' || t.status === 'pending');
            if (running) {
                renderDownloadTasks(r.tasks);
            } else {
                clearInterval(dlPollTimer); dlPollTimer = null;
                renderDownloadTasks(r.tasks);
            }
        }, 1000);
    }

    async function refreshDownloadStatus() {
        const r = await apiGet('/models/download/status');
        if (!r || !r.tasks) return;
        renderDownloadTasks(r.tasks);
        // 若有运行中任务, 启动轮询
        if (r.tasks.some(t => t.status === 'downloading' || t.status === 'pending')) startDlProgressPoll();
    }

    function renderDownloadTasks(tasks) {
        const listEl = document.getElementById('downloadTaskList');
        if (!listEl) return;
        if (!tasks.length) {
            listEl.innerHTML = '<p style="color:var(--text-muted);font-size:12px">暂无下载任务</p>';
            return;
        }
        dlTasksCache = tasks;
        listEl.innerHTML = tasks.map(t => {
            const st = t.status;
            const pct = (t.total > 0) ? Math.min(100, Math.round(t.downloaded / t.total * 100)) : 0;
            let bar = '';
            let extra = '';
            if (st === 'downloading') {
                bar = `<div style="height:6px;background:var(--border);border-radius:3px;margin-top:6px;overflow:hidden;"><div style="height:100%;width:${pct}%;background:var(--primary,#3b82f6);transition:width .8s;"></div></div>`;
                extra = `<div style="font-size:11px;color:var(--text-muted);margin-top:4px;display:flex;gap:12px;flex-wrap:wrap;">
                    <span>⬇️ ${fmtSpeed(t.speed)}</span>`;
                if (t.total > 0) extra += `<span>${pct}%</span>`;
                if (t.eta) extra += `<span>${fmtEta(t.eta)}</span>`;
                if (t.current_file) extra += `<span style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeAttr(t.current_file)}">${escapeHtml(t.current_file)}</span>`;
                extra += `</div>`;
            }
            return `<div class="dl-task" style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span style="font-size:12px;font-weight:600">${escapeHtml(t.source)} · ${escapeHtml(t.model_id)}${t.revision?'<span style="color:var(--text-muted);font-weight:400"> @'+escapeHtml(t.revision)+'</span>':''}</span>
                    <span style="display:flex;align-items:center;gap:8px;">
                        ${st==='downloading'?'<button class="btn btn-xs" onclick="window.cancelDownload(\''+t.task_id+'\')">✕ 取消</button>':''}
                        <span class="version-status ${st}">${st}</span>
                    </span>
                </div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:4px;word-break:break-all">${escapeHtml(t.detail || t.error || '')}</div>
                ${bar}
                ${extra}
            </div>`;
        }).join('');
    }

    async function cancelDownload(taskId) {
        if (!confirm('确定取消下载任务?')) return;
        const r = await apiPost('/models/download/cancel', { task_id: taskId });
        if (r && r.ok) toast('已请求取消', 'info'); else toast('取消失败', 'error');
        setTimeout(refreshDownloadStatus, 500);
    }
    window.cancelDownload = cancelDownload;

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(String(text)));
        return div.innerHTML;
    }
    window.startModel = startModel;
    window.stopModel = stopModel;
    window.restartModel = restartModel;
    window.saveParameters = saveParameters;
    window.selectModelFile = selectModelFile;
    window.selectEngine = selectEngine;
    window.openModelDetail = openModelDetail;
    window.modalApplyVariant = modalApplyVariant;
    window.loadModalVariants = loadModalVariants;
    window.saveModalParams = saveModalParams;
    window.applyModalParams = applyModalParams;
    window.modalEngineChange = modalEngineChange;
    window.modalApplyEngine = modalApplyEngine;
    window.modalSaveFile = modalSaveFile;
    window.modalPickFile = modalPickFile;
    window.previewCmd = previewCmd;
    window.saveCmd = saveCmd;
    window.clearCmd = clearCmd;
    window.closeModal = closeModal;
    window.viewLogs = viewLogs;
    window.toggleDetailBody = toggleDetailBody;
    window.sendChatMessage = sendChatMessage;
    window.generateImage = generateImage;
    window.runEmbedding = runEmbedding;
    window.runASR = runASR;
    window.runTTS = runTTS;
    window.runRerank = runRerank;
    window.runOCR = runOCR;
    window.startModelDownload = startModelDownload;
    window.refreshDownloadStatus = refreshDownloadStatus;
    window.loadProxySettings = loadProxySettings;
    window.toggleProxyInput = toggleProxyInput;
    window.saveProxy = saveProxy;
    window.hfLoadSettings = hfLoadSettings;
    window.hfPresetChange = hfPresetChange;
    window.hfSaveSettings = hfSaveSettings;
    window.hfClearToken = hfClearToken;
    window.reloadServerConfig = reloadServerConfig;
    window.restartServer = restartServer;
    window.downloadServerLog = downloadServerLog;
    window.loadDownloadVersions = loadDownloadVersions;
    window.selectDownloadVersion = showDownloadVersion;
    window.showDownloadVersionFiles = showDownloadVersionFiles;
    window.updateDlFilesStats = updateDlFilesStats;
    window.selectAllVersionFiles = selectAllVersionFiles;
    window.deselectAllVersionFiles = deselectAllVersionFiles;
    window.generateVideo = generateVideo;
    window.generateI2V = generateI2V;
    // ---- GGUF Quantize (v0.4) ----
    // ---- GGUF Quantize (v0.4/v0.5) ----
    let qState = { mode: 'src', curDir: '' };

    function qParent(relDir) {
        const p = relDir.split('/').filter(Boolean); p.pop(); return p.join('/');
    }

    function openQuantizeSrcBrowser() {
        qState = { mode: 'src', curDir: '' };
        const m = document.getElementById('fileBrowserModal');
        m.classList.add('active');
        document.getElementById('fileBrowserTitle').textContent = '📂 选择源 GGUF 文件';
        qLoadDir('');
    }
    function openQuantizeOutBrowser() {
        qState = { mode: 'out', curDir: '' };
        const m = document.getElementById('fileBrowserModal');
        m.classList.add('active');
        document.getElementById('fileBrowserTitle').textContent = '📂 选择输出文件夹';
        qLoadDir('');
    }
    // --- vLLM -> GGUF 源选择 (目录 or safetensors) ---
    function openVgSrcBrowser() {
        qState = { mode: 'vsrc', curDir: '' };
        const m = document.getElementById('fileBrowserModal');
        m.classList.add('active');
        document.getElementById('fileBrowserTitle').textContent = '📂 选择 vLLM 模型 (目录/safetensors)';
        qLoadDir('');
    }
    function openVgOutBrowser() {
        qState = { mode: 'vout', curDir: '' };
        const m = document.getElementById('fileBrowserModal');
        m.classList.add('active');
        document.getElementById('fileBrowserTitle').textContent = '📂 选择 vLLM→GGUF 输出目录 (可新建)';
        qLoadDir('');
    }
    async function qLoadDir(relDir) {
        const body = document.getElementById('fileBrowserBody');
        if (!body) return;
        body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">加载中...</div>';
        const data = await apiGet('/fs/list?path=' + encodeURIComponent(relDir));
        if (!data) { body.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">加载失败</div>'; return; }
        qState.curDir = relDir;
        let html = '';
        html += `<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;">
            <span style="font-size:12px;color:var(--text-muted)">路径:</span>
            <input class="form-input fb-path-input" value="${escapeHtml(data.current||'')}" readonly style="flex:1;font-family:monospace">
            ${relDir ? '<button class="btn btn-sm" onclick="qUp()">↑ 上级</button>' : ''}
            ${(qState.mode==='out'||qState.mode==='vout') ? `<button class="btn btn-sm btn-primary" onclick="qPickDirTarget()">✔ 选此目录为输出</button>` : ''}
        </div>`;
        html += '<div style="font-size:11px;color:var(--text-muted);margin:4px 0">目录:</div>';
        if (relDir) html += `<div class="fb-item fb-dir" onclick="qLoadDir('${escapeAttr(qParent(relDir))}')">📁 .. (上级)</div>`;
        (data.dirs||[]).forEach(d => { html += `<div class="fb-item fb-dir" onclick="qLoadDir('${escapeAttr(d.path)}')">📁 ${escapeHtml(d.name)}</div>`; });
        if (qState.mode==='src') {
            html += '<div style="font-size:11px;color:var(--text-muted);margin:6px 0">GGUF 文件:</div>';
            const ggs = (data.files||[]).filter(f => /^\.gguf$/i.test(f.ext));
            ggs.forEach(f => { html += `<div class="fb-item fb-file" onclick="qPickFile('${escapeAttr(f.path)}')">🧩 ${escapeHtml(f.name)}</div>`; });
            if (!ggs.length) html += '<div style="font-size:12px;color:var(--text-muted);padding:4px 0">(当前目录无 GGUF 文件)</div>';
        } else if (qState.mode==='vsrc') {
            html += '<div style="font-size:11px;color:var(--text-muted);margin:6px 0">vLLM 模型(safetensors):</div>';
            const st = (data.files||[]).filter(f => /\.safetensors$/i.test(f.ext) || /\.bin$/i.test(f.ext));
            st.forEach(f => { html += `<div class="fb-item fb-file" onclick="vgPickSrc('${escapeAttr(f.path)}')">🧩 ${escapeHtml(f.name)}</div>`; });
            html += `<div style="margin-top:6px;border-top:1px solid var(--border);padding-top:6px;">`;
            html += `<button class="btn btn-sm btn-primary" onclick="vgPickSrc('${escapeHtml(relDir)}')">✔ 选当前目录作为模型</button>`;
            html += `<div style="font-size:11px;color:var(--text-muted);margin-top:4px">HF 模型目录应含 config.json/model_index.json</div></div>`;
        } else if (qState.mode==='out' || qState.mode==='vout' || qState.mode==='dld') {
            html += '<div style="margin-top:6px;border-top:1px solid var(--border);padding-top:6px;">';
            html += `<button class="btn btn-sm btn-primary" onclick="qPickDirTarget()">✔ 选当前目录作输出</button> `;
            html += `<button class="btn btn-sm" onclick="qNewDir()">➕ 新建子目录</button>`;
            html += '<div style="font-size:11px;color:var(--text-muted);margin-top:4px">当前: /models/' + escapeHtml(relDir) + '</div></div>';
        } else {
            html += '<div style="font-size:11px;color:var(--text-muted);margin:6px 0">可进入目录后点"✔ 选此目录"作为输出目录</div>';
        }
        body.innerHTML = html;
    }
    function qUp() { const p = qState.curDir.split('/').filter(Boolean); p.pop(); qLoadDir(p.join('/')); }
    async function qPickFile(path) {
        document.getElementById('quSrc').value = path;
        // 输出目录默认取源所在目录
        const dir = path.split('/').slice(0,-1).join('/');
        document.getElementById('quOut').value = dir || '/';
        closeFileBrowser();
        toast('已选源: ' + path + '；输出目录默认同目录', 'info');
    }
    async function qPickDir() {
        document.getElementById('quOut').value = qState.curDir || '/';
        closeFileBrowser();
        toast('输出目录: /models/' + (qState.curDir || '') , 'info');
    }
    // 通用输出目标选择 (quOut 或 vgOut 视当前浏览器来源)
    async function qPickDirTarget() {
        const d = qState.curDir || '/';
        if (qState.mode === 'vout') {
            document.getElementById('vgOut').value = d;
            closeFileBrowser();
            toast('vLLM→GGUF 输出目录: /models/' + d, 'info');
        } else if (qState.mode === 'dld') {
            document.getElementById('dlTargetDir').value = d;
            closeFileBrowser();
            toast('下载目标目录: /models/' + d, 'info');
        } else {
            document.getElementById('quOut').value = d;
            closeFileBrowser();
            toast('输出目录: /models/' + d, 'info');
        }
    }
    // 模型下载目标目录浏览器（复用 fileBrowserModal）
    function openDownloadDirBrowser() {
        qState = { mode: 'dld', curDir: '' };
        const m = document.getElementById('fileBrowserModal');
        m.classList.add('active');
        document.getElementById('fileBrowserTitle').textContent = '📂 选择下载目标目录 (任选/可新建)';
        qLoadDir('');
    }
    window.openDownloadDirBrowser = openDownloadDirBrowser;
    // vLLM 源选中 (文件或目录)
    async function vgPickSrc(path) {
        document.getElementById('vgSrc').value = path;
        closeFileBrowser();
        toast('已选 vLLM 源: ' + path, 'info');
    }
    // 新建目录 (输出模式)
    async function qNewDir() {
        const name = prompt('新建子目录名:');
        if (!name || !/^[\w\-.]+$/.test(name)) return toast('目录名非法', 'error');
        const r = await apiPost('/fs/mkdir', { path: (qState.curDir ? qState.curDir + '/' : '') + name });
        if (r && r.ok) { qLoadDir(qState.curDir); toast('目录已创建', 'success'); }
        else toast('❌ ' + ((r && r.error) || '新建失败'), 'error');
    }

    // --- vLLM -> GGUF ---
    async function startVllm2Gguf() {
        const src = document.getElementById('vgSrc')?.value.trim();
        if (!src) return toast('请选择 vLLM 源模型', 'error');
        const quant = document.getElementById('vgQuant')?.value || 'q4_k_m';
        const outDir = document.getElementById('vgOut')?.value.trim() || undefined;
        const name = document.getElementById('vgName')?.value.trim() || undefined;
        toast('🧬 转换 ' + src + ' ...');
        const r = await apiPost('/convert/hf', { source: src, model_name: name, quant: quant, out_dir: outDir });
        if (r && r.ok) { toast('✅ 已提交 ' + r.task_id, 'success'); setTimeout(refreshVgTask, 3000); }
        else toast('❌ ' + ((r && r.error) || '提交失败'), 'error');
    }
    async function refreshVgTask() {
        const el = document.getElementById('vgTaskList');
        if (!el) return;
        const r = await apiGet('/quantize/status');
        if (!r || !r.tasks) return;
        const vg = r.tasks.filter(t => t.task_id.startsWith('vg_'));
        if (!vg.length) { el.innerHTML = '<p style="color:var(--text-muted);font-size:12px">暂无转换任务</p>'; return; }
        el.innerHTML = vg.map(t => `
            <div style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span style="font-size:12px;font-weight:600">${escapeHtml(t.quant_type)} ← ${escapeHtml(t.source)}</span>
                    <span class="version-status ${t.status}">${t.status}</span>
                </div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:4px;word-break:break-all">${escapeHtml(t.detail||t.error||t.out_path||'')}</div>
            </div>`).join('');
    }
    window.openVgSrcBrowser = openVgSrcBrowser;
    window.openVgOutBrowser = openVgOutBrowser;
    window.startVllm2Gguf = startVllm2Gguf;
    window.refreshVgTask = refreshVgTask;
    window.miniVgRefresh = refreshVgTask;
    window.openQuantizeSrcBrowser = openQuantizeSrcBrowser;
    window.openQuantizeOutBrowser = openQuantizeOutBrowser;
    // GGUF 量化 / vLLM→GGUF 文件浏览器内部回调（HTML onclick 直接引用，必须暴露到 window）
    window.qLoadDir = qLoadDir;
    window.qPickFile = qPickFile;
    window.qPickDir = qPickDir;
    window.qPickDirTarget = qPickDirTarget;
    window.qUp = qUp;
    window.qNewDir = qNewDir;
    window.vgPickSrc = vgPickSrc;
    function closeQModal() { closeFileBrowser(); }

    async function startQuantize() {
        const src = document.getElementById('quSrc')?.value.trim();
        if (!src) return toast('请选择源 GGUF 文件', 'error');
        const qtype = document.getElementById('quType')?.value || 'q4_k_m';
        const outDir = document.getElementById('quOut')?.value.trim() || undefined;
        toast('⚗️ 开始量化 ' + src + ' -> ' + qtype + ' ...');
        const r = await apiPost('/quantize', { source: src, quant_type: qtype, out_dir: outDir });
        if (r && r.ok) {
            toast('✅ 已提交转换任务 ' + r.task_id, 'success');
            setTimeout(refreshQuantize, 2000);
        } else {
            toast('❌ ' + ((r && r.error) || '提交失败'), 'error');
        }
    }
    async function refreshQuantize() {
        const listEl = document.getElementById('quantizeTaskList');
        if (!listEl) return;
        const r = await apiGet('/quantize/status');
        if (!r || !r.tasks) return;
        if (!r.tasks.length) { listEl.innerHTML = '<p style="color:var(--text-muted);font-size:12px">无转换任务</p>'; return; }
        listEl.innerHTML = r.tasks.map(t => `
            <div style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span style="font-size:12px;font-weight:600">${escapeHtml(t.quant_type)} ← ${escapeHtml(t.source)}</span>
                    <span class="version-status ${t.status}">${t.status}</span>
                </div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:4px;word-break:break-all">${escapeHtml(t.out_path || t.error || t.detail || '')}</div>
            </div>`).join('');
    }
    window.startQuantize = startQuantize;
    window.refreshQuantize = refreshQuantize;
    window.refreshQuantizeStatus = refreshQuantize;

    window.callApi = callApi;
    window.clearChatHistory = clearChatHistory;
    window.clearChatImage = clearChatImage;
    window.loadChatHistory = loadChatHistory;
    window.generateStartupCmd = generateStartupCmd;
    window.saveStartupCmd = saveStartupCmd;
    window.clearStartupCmd = clearStartupCmd;
    window.loadStartupState = loadStartupState;
    window.openFileBrowser = openFileBrowser;
    window.closeFileBrowser = closeFileBrowser;
    window.fbLoadDir = fbLoadDir;
    window.fbGoPath = fbGoPath;
    window.fbGotoDir = fbGotoDir;
    window.fbUp = fbUp;
    window.fbPickFile = fbPickFile;
    window.fbPickDir = fbPickDir;
    window.applyPresetFile = applyPresetFile;
    window.closePresetBox = closePresetBox;

    // ---- 用户手册 / 帮助搜索 (v0.8) ----
    const __docs = { list: [], current: null };

    // 极简 Markdown -> HTML（覆盖手册使用到的子集：标题/表格/代码块/列表/引用/加粗/行内码/链接）
        function mdToHtml(mdText) {
        const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const inline = s => s
            .replace(/`([^`]+)`/g, (m, c) => '<code>' + esc(c) + '</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => {
                u = (u || '').trim();
                if (/^https?:\/\//i.test(u) || /^mailto:/i.test(u)) {
                    return '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + t + '</a>';
                }
                return '<a href="#" class="help-md-link" data-file="' + esc(u) + '">' + t + '</a>';
            });
        let lines = (mdText || '').split(/\n/);
        let out = [], inPre = false, inTable = false, tableRows = [];
        // 单元格解析与表格输出（tpipe 需在函数作用域，供循环外收尾 flush 使用）
        const cells = s => s.replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()));
        const tpipe = (row, head) => '<tr>' + cells(row).map(c => '<' + (head ? 'th' : 'td') + '>' + c + '</' + (head ? 'th' : 'td') + '>').join('') + '</tr>';
        const flushTable = () => {
            if (!tableRows.length) return;
            out.push('<table><thead>' + tpipe(tableRows[0], true) + '</thead>');
            if (tableRows.length > 2) out.push('<tbody>' + tableRows.slice(2).map(r => tpipe(r, false)).join('') + '</tbody>');
            out.push('</table>');
            inTable = false; tableRows = [];
        };
        for (const raw of lines) {
            const line = raw.replace(/\r$/, '');
            const t = line.trim();
            if (t.startsWith('```')) {
                if (inPre) { out.push('</code></pre>'); inPre = false; }
                else { out.push('<pre><code>'); inPre = true; }
                continue;
            }
            if (inPre) { out.push(esc(line) + '\n'); continue; }
            if (/^\|/.test(t) && /\|$/.test(t)) {
                if (!inTable) { inTable = true; tableRows = []; }
                tableRows.push(line);
                continue;
            }
            if (inTable) flushTable();
            if (/^#{1,6}\s/.test(line)) {
                const m = line.match(/^(#{1,6})\s+(.*)$/);
                out.push(`<h${m[1].length}>` + inline(m[2]) + `</h${m[1].length}>`);
            } else if (/^\s*[-*_][-*_\s]*$/.test(t)) {
                out.push('<hr>');
            } else if (/^[-*]\s/.test(line)) out.push('<li>' + inline(line.replace(/^[-*]\s/, '')) + '</li>');
            else if (/^\d+\.\s/.test(line)) out.push('<li>' + inline(line.replace(/^\d+\.\s/, '')) + '</li>');
            else if (/^>\s?/.test(line)) out.push('<blockquote>' + inline(line.replace(/^>\s?/, '')) + '</blockquote>');
            else if (t === '') out.push('<br>');
            else out.push('<p>' + inline(line) + '</p>');
        }
        if (inPre) out.push('</code></pre>');
        if (inTable) flushTable();
        return out.join('');
    }
    async function loadHelp() {
        try {
            const r = await fetch(API_BASE + '/docs');
            const data = await r.json();
            __docs.docs = (data && data.docs) || [];
            const verEl = document.getElementById('helpModalVer');
            if (verEl) verEl.textContent = data.version || '';
            renderHelpToc();
        } catch (e) { console.error('load help failed', e); }
    }

    function renderHelpToc() {
        const toc = document.getElementById('helpToc');
        if (!toc) return;
        toc.innerHTML = '<div style="font-weight:bold;color:var(--text-secondary);padding:4px 8px;margin-bottom:6px">目录</div>' +
            (__docs.docs || []).map((d, i) =>
                '<button class="help-list-item' + (i === 0 && !__docs.current ? ' active' : '') + '" data-doc="' + d.file + '">' + d.title + '</button>'
            ).join('');
        toc.querySelectorAll('.help-list-item').forEach(btn => {
            btn.addEventListener('click', () => openHelpDoc(btn.dataset.doc));
        });
        if (!__docs.current && __docs.docs && __docs.docs.length) openHelpDoc(__docs.docs[0].file);
    }

    async function openHelpDoc(file, query) {
        try {
            const r = await fetch(API_BASE + '/docs/' + file);
            const text = await r.text();
            __docs.current = file;
            document.querySelectorAll('.help-list-item').forEach(b => b.classList.toggle('active', b.dataset.doc === file));
            const content = document.getElementById('helpContent');
            let html = mdToHtml(text);
            if (query) {
                const qesc = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                const re = new RegExp('(' + qesc + ')', 'gi');
                html = html.replace(re, '<mark class="hl">$1</mark>');
            }
            content.innerHTML = html;
            wireHelpLinks(content);
        } catch (e) { console.error(e); }
    }

    function resolveDocFile(raw) {
        const docs = __docs.docs || [];
        let name = String(raw || '').replace(/^\.\//, '').replace(/^[#/]+/, '').trim();
        if (!name) return null;
        const clean = name.replace(/\.md$/i, '');
        const norm = s => s.toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, '');
        const nc = norm(clean);
        let hit = docs.find(d => d.file === name || d.file === clean + '.md' || norm(d.file) === nc);
        if (hit) return hit.file;
        const n = (name.match(/^(\d+)/) || [])[1];
        if (n) { hit = docs.find(d => d.file.startsWith(n + '-')); if (hit) return hit.file; }
        hit = docs.find(d => d.title && norm(d.title) === nc);
        if (hit) return hit.file;
        return docs.find(d => d.title && norm(d.title).includes(nc)) || null;
    }
    function wireHelpLinks(container) {
        container.querySelectorAll('a.help-md-link').forEach(a => {
            const raw = a.getAttribute('data-file');
            a.addEventListener('click', (e) => {
                e.preventDefault();
                const f = resolveDocFile(raw);
                if (f) openHelpDoc(f);
                else { a.style.textDecoration = 'line-through'; a.title = '无法定位该章节'; }
            });
        });
    }

    function openHelp() {
        const m = document.getElementById('helpModal');
        if (!m) return;
        m.style.display = 'flex';
        if (!Array.isArray(__docs.docs) || !__docs.docs.length) loadHelp();
    }
    function closeHelp() {
        const m = document.getElementById('helpModal');
        if (m) m.style.display = 'none';
    }

    function setupHelp() {
        document.getElementById('helpToggle')?.addEventListener('click', openHelp);
        document.getElementById('helpClose')?.addEventListener('click', closeHelp);
        // Esc / 遮罩点击关闭
        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeHelp(); });
        const modal = document.getElementById('helpModal');
        if (modal) modal.addEventListener('click', e => { if (e.target === modal) closeHelp(); });
        // 更新页脚版本
        fetch(API_BASE + '/settings').then(r => r.json()).then(d => {
            const fv = document.querySelector('#footerVersion [data-ver]');
            if (fv && d && d.version) fv.textContent = d.version;
        }).catch(() => {});
    }

    // ================================================================
    // v1.1 启动向导（Launch Model / Running Models 共用）
    //   类别 → 引擎 → 版本/flavor → 模型文件 → 参数 → 启动
    // ================================================================
    const WIZ = {};                 // hostId -> 向导状态
    const WIZ_CATEGORIES = [
        ['chat', 'Chat / LLM / VLM'], ['embedding', 'Embedding'], ['reranker', 'Reranker'],
        ['asr', 'ASR'], ['tts', 'TTS'], ['ocr', 'OCR'],
        ['image', 'Text-to-Image'], ['video', 'Video'],
    ];
    let wizFileCtx = null;          // 文件选择弹窗上下文 {hostId, engine}

    function wizState(hostId) {
        if (!WIZ[hostId]) {
            WIZ[hostId] = {
                category: 'chat', engine: '', version: '', flavor: '',
                modelFile: '', params: {}, uid: '', name: '', replicas: 1,
                paramDefs: [], models: { files: [], dirs: [] }, busy: false,
            };
        }
        return WIZ[hostId];
    }

    // 客户端（Cherry Studio 等）会拿模型 id 去做「模型名注册表」归一化模糊匹配，
    // 裸家族名（qwen3）会被认成 qwen3-14b，带量化后缀的名字又匹配不到。
    // 这里在前端给出同样的推荐规则，让用户起 uid 时少踩坑。
    const WIZ_CATEGORY_TOKENS = {
        chat: ['chat', 'instruct', 'llm'],
        embedding: ['embedding', 'embed', 'bge', 'gte', 'e5-', '-m3', 'vector'],
        reranker: ['rerank'], asr: ['asr', 'whisper'], tts: ['tts'],
        ocr: ['ocr'], image: ['image', 'flux', 'sdxl'], video: ['video', 'wan'],
    };

    function wizModelIdentity(modelFile) {
        if (!modelFile) return '';
        const parts = String(modelFile).replace(/\\/g, '/').split('/').filter(Boolean);
        if (!parts.length) return '';
        const stem = parts[parts.length - 1].replace(/\.[^.]+$/, '');
        const generic = ['models', 'gguf', 'ggml', 'model', 'root', 'weights'];
        const cands = parts.length >= 2 ? [parts[parts.length - 2], stem] : [stem];
        for (let c of cands) {
            c = String(c).toLowerCase()
                .replace(/(?:[-_.](?:gguf|ggml|bin|safetensors|fp16|bf16|fp8|q\d+(?:_[a-z0-9]+)*|int[48]))+$/g, '')
                .replace(/[^a-z0-9._-]+/g, '-')
                .replace(/[-_.]{2,}/g, '-')
                .replace(/^[-._]+|[-._]+$/g, '');
            if (c && !generic.includes(c)) return c;
        }
        return '';
    }

    function wizSuggestUid(category, modelFile) {
        let base = wizModelIdentity(modelFile);
        const tokens = WIZ_CATEGORY_TOKENS[category] || [];
        const hasIdentity = base && tokens.some(t => base.includes(t));
        if (base && !hasIdentity && category && category !== 'chat') base = category + '-' + base;
        return base || category || 'model';
    }

    // 保留原始大小写的身份片段（用于「模型名称」建议）。
    // uid 必须小写化，但名称不应 —— 否则名称与 uid 完全相同，
    // 客户端（Cherry Studio 等）会拿 id 去注册表覆盖名称，我们的名字就丢了。
    function wizModelIdentityRaw(modelFile) {
        if (!modelFile) return '';
        const parts = String(modelFile).replace(/\\/g, '/').split('/').filter(Boolean);
        if (!parts.length) return '';
        const stem = parts[parts.length - 1].replace(/\.[^.]+$/, '');
        const generic = ['models', 'gguf', 'ggml', 'model', 'root', 'weights'];
        const cands = parts.length >= 2 ? [parts[parts.length - 2], stem] : [stem];
        for (let c of cands) {
            c = String(c)
                .replace(/(?:[-_.](?:gguf|ggml|bin|safetensors|fp16|bf16|fp8|q\d+(?:_[a-z0-9]+)*|int[48]))+$/gi, '')
                .replace(/\s+/g, '-')
                .replace(/[^A-Za-z0-9._-]+/g, '-')
                .replace(/[-_.]{2,}/g, '-')
                .replace(/^[-._]+|[-._]+$/g, '');
            if (c && !generic.includes(c.toLowerCase())) return c;
        }
        return '';
    }

    function wizSuggestName(category, modelFile) {
        let name = wizModelIdentityRaw(modelFile);
        if (!name) return '';
        if (category && category !== 'chat' && !name.toLowerCase().includes(category)) {
            name += ' (' + category + ')';
        }
        return name;
    }

    function wizUidWarnings(uid, category) {
        const out = [];
        const u = String(uid || '').trim().toLowerCase();
        if (!u) return out;
        const tokens = WIZ_CATEGORY_TOKENS[category] || [];
        const hasIdentity = tokens.some(t => u.includes(t));
        if (category !== 'chat' && !hasIdentity) {
            out.push('UID 里没有 ' + category + ' 身份关键词，客户端可能把它认成聊天模型并丢掉 ' + category + ' 能力');
        }
        if (/^[a-z]+\d*(\.\d+)?$/.test(u)) {
            out.push('这是裸家族名，客户端按名字模糊匹配会命中具体尺寸条目（如 qwen3 → Qwen3 14B）');
        }
        if (/[-_.](fp16|bf16|fp8|gguf)$/.test(u) || /[-_.]q\d/.test(u)) {
            out.push('带量化/格式后缀，客户端注册表匹配不到，会被当成无能力的自定义模型');
        }
        return out;
    }

    function wizInstalled(engineType) {
        const list = (engineCatalog && engineCatalog.engines && engineCatalog.engines[engineType] &&
            engineCatalog.engines[engineType].installed) || [];
        return list.filter(v => v.status === 'installed');
    }

    function wizEnginesFor(category) {
        if (!engineCatalog || !engineCatalog.engines) return [];
        return Object.keys(engineCatalog.engines).filter(t => {
            const e = engineCatalog.engines[t];
            return (e.supports || []).includes(category);
        }).map(t => {
            const e = engineCatalog.engines[t];
            return { type: t, label: e.display_name || t, installed: wizInstalled(t).length, flavors: e.flavors || [], defaultFlavor: e.default_flavor || '' };
        });
    }

    async function wizInit(hostId, opts) {
        opts = opts || {};
        const host = document.getElementById(hostId);
        if (!host) return;
        if (!engineCatalog) {
            try { engineCatalog = await apiGet('/engines/installed'); } catch (e) { engineCatalog = null; }
        }
        const st = wizState(hostId);
        if (opts.category && opts.category !== st.category) { st.category = opts.category; st.engine = ''; }
        host.innerHTML = `
            <div class="wizard">
                <div class="wizard-head">
                    <div>
                        <h3 class="wizard-title">${opts.title || '🚀 启动模型实例'}</h3>
                        <div class="wizard-sub">${opts.subtitle || '按模型类别选择推理引擎与版本，再挑选模型文件并设置参数'}</div>
                    </div>
                    <button class="btn btn-sm" onclick="window.wizRefreshCatalog('${hostId}')">🔄 刷新引擎</button>
                </div>
                <div class="wizard-steps" id="wiz-steps-${hostId}"></div>
                <div id="wiz-alert-${hostId}"></div>
                <div class="form-grid" id="wiz-tiers-${hostId}"></div>
                <div class="wizard-params" id="wiz-params-${hostId}"></div>
                <div class="wizard-actions" id="wiz-actions-${hostId}"></div>
                <div class="wizard-preview" id="wiz-preview-${hostId}" style="display:none"></div>
            </div>`;
        wizRender(hostId);
        // 首屏也需要拉一次参数定义（否则 ⑤ 参数区会是空的）
        await wizLoadParams(hostId);
        wizRenderParams(hostId);
    }

    async function wizRefreshCatalog(hostId) {
        engineCatalog = await apiGet('/engines/installed');
        wizRender(hostId);
        toast('引擎状态已刷新');
    }

    function wizSteps(hostId) {
        const st = wizState(hostId);
        const steps = [
            ['类别', !!st.category],
            ['引擎', !!st.engine],
            ['版本', !!st.version],
            ['模型文件', !!st.modelFile],
            ['参数', st.paramDefs.length > 0],
        ];
        let done = 0;
        steps.forEach(s => { if (s[1]) done++; });
        return steps.map((s, i) => {
            const cls = s[1] ? 'done' : (i === done ? 'active' : '');
            return `<span class="wizard-step ${cls}"><span class="idx">${i + 1}</span>${s[0]}</span>`;
        }).join('<span class="wizard-arrow">›</span>');
    }

    function wizRender(hostId) {
        const st = wizState(hostId);
        const tiers = document.getElementById('wiz-tiers-' + hostId);
        if (!tiers) return;

        const engines = wizEnginesFor(st.category);
        if (!st.engine || !engines.some(e => e.type === st.engine)) {
            st.engine = engines.length ? engines[0].type : '';
        }
        const curEngine = engines.find(e => e.type === st.engine);
        const installed = st.engine ? wizInstalled(st.engine) : [];

        // 版本 / flavor
        const verOptions = installed.map(v =>
            `<option value="${escapeAttr(v.version + '|' + (v.flavor || ''))}" ${(st.version === v.version && st.flavor === (v.flavor || '')) ? 'selected' : ''}>${escapeHtml(v.version)}${v.flavor ? ' · ' + escapeHtml(v.flavor) : ''}${v.size_mb ? ' · ' + v.size_mb + 'MB' : ''}</option>`
        ).join('');
        if (installed.length && !st.version) {
            st.version = installed[0].version;
            st.flavor = installed[0].flavor || '';
        }

        // 步骤条必须在默认值回填之后再算，否则「版本」永远是待完成态
        const stepsEl = document.getElementById('wiz-steps-' + hostId);
        if (stepsEl) stepsEl.innerHTML = wizSteps(hostId);

        tiers.innerHTML = `
            <div class="field">
                <label>① 模型类别</label>
                <select class="form-select" onchange="window.wizSet('${hostId}','category',this.value)">
                    ${WIZ_CATEGORIES.map(c => `<option value="${c[0]}" ${c[0] === st.category ? 'selected' : ''}>${c[1]}</option>`).join('')}
                </select>
            </div>
            <div class="field">
                <label>② 推理引擎</label>
                <select class="form-select" onchange="window.wizSet('${hostId}','engine',this.value)">
                    ${engines.length ? engines.map(e => `<option value="${e.type}" ${e.type === st.engine ? 'selected' : ''}>${escapeHtml(e.label)}${e.installed ? '' : '（未安装）'}</option>`).join('')
                        : '<option value="">（该类别暂无可用引擎）</option>'}
                </select>
                <span class="hint">${curEngine ? (curEngine.flavors.length > 1 ? '支持 cpu / cuda 两种构建' : '仅 cuda 构建') : ''}</span>
            </div>
            <div class="field">
                <label>③ 引擎版本 / Flavor</label>
                <select class="form-select" onchange="window.wizSetVersion('${hostId}',this.value)">
                    ${verOptions || '<option value="">（暂无已安装版本）</option>'}
                </select>
            </div>
            <div class="field span-2">
                <label>④ 模型文件 <span class="hint">（相对 /models）</span></label>
                <div class="input-with-btn">
                    <input class="form-input" value="${escapeAttr(st.modelFile)}" placeholder="点右侧浏览，或直接粘贴路径"
                           onchange="window.wizSet('${hostId}','modelFile',this.value)">
                    <button class="btn btn-sm" onclick="window.wizOpenFiles('${hostId}')">📂 浏览</button>
                </div>
                <span class="hint" id="wiz-filehint-${hostId}"></span>
            </div>
            <div class="field">
                <label>实例 UID <span class="hint">（客户端看到的模型 ID，留空自动生成）</span></label>
                <input class="form-input" value="${escapeAttr(st.uid)}" placeholder="${escapeAttr(wizSuggestUid(st.category, st.modelFile))}"
                       onchange="window.wizSet('${hostId}','uid',this.value)">
                <span class="hint" id="wiz-uidhint-${hostId}"></span>
            </div>
            <div class="field">
                <label>模型名称 <span class="hint">（客户端显示的模型名，留空自动生成）</span></label>
                <input class="form-input" value="${escapeAttr(st.name || '')}" placeholder="${escapeAttr(wizSuggestName(st.category, st.modelFile))}"
                       onchange="window.wizSet('${hostId}','name',this.value)">
                <span class="hint">名称与 UID 不同时，客户端会优先保留这个名字</span>
            </div>
            <div class="field">
                <label>副本数</label>
                <input class="form-input" type="number" min="1" max="8" value="${st.replicas}"
                       onchange="window.wizSet('${hostId}','replicas',parseInt(this.value,10)||1)">
            </div>`;

        // 提示信息
        const alertEl = document.getElementById('wiz-alert-' + hostId);
        if (alertEl) {
            if (!engines.length) {
                alertEl.innerHTML = `<div class="wizard-alert warn">当前还没有支持 <b>${escapeHtml(st.category)}</b> 的推理引擎实现。</div>`;
            } else if (st.engine && !installed.length) {
                alertEl.innerHTML = `<div class="wizard-alert warn">
                    引擎 <b>${escapeHtml(st.engine)}</b> 还没有已安装版本，无法启动。
                    请到 <a onclick="window.zzGoto('settings')">Settings → 推理引擎管理</a> 安装（llama.cpp 需按 cpu/cuda 分别安装）。
                </div>`;
            } else {
                alertEl.innerHTML = '';
            }
        }

        // 实例 UID 的客户端兼容性提示（不阻断启动，只提醒）
        const uidHintEl = document.getElementById('wiz-uidhint-' + hostId);
        if (uidHintEl) {
            const uidWarn = wizUidWarnings(st.uid || wizSuggestUid(st.category, st.modelFile), st.category);
            uidHintEl.innerHTML = uidWarn.length
                ? uidWarn.map(w => `<span style="color:var(--warn,#d99000)">⚠ ${escapeHtml(w)}</span>`).join('<br>')
                : '';
        }

        wizRenderParams(hostId);
        wizRenderActions(hostId);
    }

    async function wizLoadParams(hostId) {
        const st = wizState(hostId);
        if (!st.category || !st.engine) { st.paramDefs = []; return; }
        const qs = new URLSearchParams({ category: st.category, engine: st.engine });
        const data = await apiGet('/schema/params?' + qs.toString());
        st.paramDefs = (data && data.parameters) || [];
        // 初始化默认值
        st.paramDefs.forEach(p => {
            if (st.params[p.name] === undefined) st.params[p.name] = p.default;
        });
    }

    function wizRenderParams(hostId) {
        const st = wizState(hostId);
        const box = document.getElementById('wiz-params-' + hostId);
        if (!box) return;
        const head = `<div class="wizard-params-head">
                <h4>⑤ 参数（取自模型类别定义，与引擎对齐）</h4>
                <button class="btn btn-sm" onclick="window.wizReloadParams('${hostId}')">↺ 重载默认值</button>
            </div>`;
        if (!st.paramDefs.length) {
            box.innerHTML = head + '<div style="font-size:11.5px;color:var(--text-muted)">该引擎/类别没有预置参数，将使用引擎默认值。</div>';
            return;
        }
        const inputs = st.paramDefs.map(p => wizParamField(hostId, p)).join('');
        box.innerHTML = head + `<div class="form-grid">${inputs}</div>`;
    }

    function wizParamField(hostId, p) {
        const st = wizState(hostId);
        const name = p.name;
        const val = st.params[name] !== undefined ? st.params[name] : p.default;
        const onchg = `onchange="window.wizSetParam('${hostId}','${escapeAttr(name)}',this)"`;
        let input;
        if (p.type === 'boolean') {
            input = `<label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-secondary)">
                        <input type="checkbox" ${val ? 'checked' : ''} ${onchg} style="accent-color:var(--accent)"> ${val ? '开启' : '关闭'}
                     </label>`;
        } else if (p.type === 'select') {
            input = `<select class="form-select" ${onchg}>${(p.options || []).map(o =>
                `<option value="${escapeAttr(o)}" ${String(o) === String(val) ? 'selected' : ''}>${escapeHtml(o)}</option>`).join('')}</select>`;
        } else if (p.type === 'number' || p.type === 'float') {
            input = `<input type="number" class="form-input" value="${escapeAttr(val)}"
                        ${p.min != null ? `min="${p.min}"` : ''} ${p.max != null ? `max="${p.max}"` : ''}
                        step="${p.step != null ? p.step : (p.type === 'float' ? 0.05 : 1)}" ${onchg}>`;
        } else {
            input = `<input type="text" class="form-input" value="${escapeAttr(val == null ? '' : val)}" ${onchg}>`;
        }
        return `<div class="field">
            <label title="${escapeAttr(p.label || name)}">${escapeHtml(p.label || name)}
                <span style="opacity:.55;font-size:10px">${escapeHtml(name)}</span></label>
            ${input}
        </div>`;
    }

    function wizRenderActions(hostId) {
        const st = wizState(hostId);
        const box = document.getElementById('wiz-actions-' + hostId);
        if (!box) return;
        const canLaunch = !!(st.category && st.engine && st.version && st.modelFile && !st.busy);
        box.innerHTML = `
            <button class="btn btn-success" ${canLaunch ? '' : 'disabled'} onclick="window.wizLaunch('${hostId}')">▶ 启动实例</button>
            <button class="btn" onclick="window.wizPreview('${hostId}')">👁 预览配置</button>
            <button class="btn" ${st.modelFile ? '' : 'disabled'} onclick="window.tplOpenEditor('', ${JSON.stringify({ fromWizard: hostId })})">💾 存为模版</button>
            <span class="hint" style="font-size:11px;color:var(--text-muted)">
                ${canLaunch ? '' : '请先补齐：' + [st.engine ? '' : '引擎', st.version ? '' : '版本', st.modelFile ? '' : '模型文件'].filter(Boolean).join(' / ')}
            </span>`;
    }

    function wizSet(hostId, key, value) {
        const st = wizState(hostId);
        if (key === 'category') {
            st.category = value;
            st.engine = '';
            st.version = '';
            st.flavor = '';
            st.paramDefs = [];
            st.params = {};
            st.models = { files: [], dirs: [] };
            wizLoadParams(hostId).then(() => wizRender(hostId));
            wizRender(hostId);
            return;
        }
        if (key === 'engine') {
            st.engine = value;
            st.version = '';
            st.flavor = '';
            st.paramDefs = [];
            st.params = {};
            wizLoadParams(hostId).then(() => wizRender(hostId));
            wizRender(hostId);
            return;
        }
        st[key] = value;
        if (key === 'uid') st.uidTouched = true;
        if (key === 'name') st.nameTouched = true;
        // 换模型文件时，把没被用户动过的 UID / 名称重新按新文件推荐一次
        if (key === 'modelFile') {
            if (!st.uidTouched) st.uid = wizSuggestUid(st.category, st.modelFile);
            if (!st.nameTouched) st.name = wizSuggestName(st.category, st.modelFile);
            wizRender(hostId);
            return;
        }
        wizRenderActions(hostId);
        const stepsEl = document.getElementById('wiz-steps-' + hostId);
        if (stepsEl) stepsEl.innerHTML = wizSteps(hostId);
        const uidHintEl = document.getElementById('wiz-uidhint-' + hostId);
        if (uidHintEl) {
            const warn = wizUidWarnings(st.uid || wizSuggestUid(st.category, st.modelFile), st.category);
            uidHintEl.innerHTML = warn.length
                ? warn.map(w => `<span style="color:var(--warn,#d99000)">⚠ ${escapeHtml(w)}</span>`).join('<br>')
                : '';
        }
    }

    function wizSetVersion(hostId, raw) {
        const st = wizState(hostId);
        const [ver, fl] = String(raw || '').split('|');
        st.version = ver || '';
        st.flavor = fl || '';
        wizRenderActions(hostId);
        const stepsEl = document.getElementById('wiz-steps-' + hostId);
        if (stepsEl) stepsEl.innerHTML = wizSteps(hostId);
    }

    function wizSetParam(hostId, name, el) {
        const st = wizState(hostId);
        if (el.type === 'checkbox') st.params[name] = el.checked;
        else if (el.type === 'number') st.params[name] = el.value === '' ? '' : parseFloat(el.value);
        else st.params[name] = el.value;
    }

    async function wizReloadParams(hostId) {
        const st = wizState(hostId);
        st.params = {};
        await wizLoadParams(hostId);
        wizRenderParams(hostId);
        toast('参数已重置为默认值');
    }

    function wizPayload(hostId) {
        const st = wizState(hostId);
        const params = {};
        Object.keys(st.params).forEach(k => {
            const v = st.params[k];
            if (v === '' || v === null || v === undefined) return;
            if (st.paramDefs.some(p => p.name === k && p.type === 'string') && /^(mmproj)$/i.test(k)) {
                params[k] = v;
            } else {
                params[k] = v;
            }
        });
        const payload = {
            model_type: st.category,
            engine_type: st.engine,
            engine_version: st.version,
            flavor: st.flavor,
            model_file: st.modelFile,
            parameters: params,
            replicas: st.replicas || 1,
        };
        // 留空则由后端按模型文件派生（带类别身份的 uid / 可读名称）
        const uid = (st.uid || '').trim() || wizSuggestUid(st.category, st.modelFile);
        if (uid) payload.model_uid = uid;
        const displayName = (st.name || '').trim() || wizSuggestName(st.category, st.modelFile);
        if (displayName) payload.model_name = displayName;
        return payload;
    }

    function wizPreview(hostId) {
        const box = document.getElementById('wiz-preview-' + hostId);
        if (!box) return;
        if (box.style.display === 'none') {
            box.style.display = '';
            box.textContent = JSON.stringify(wizPayload(hostId), null, 2);
        } else {
            box.style.display = 'none';
        }
    }

    async function wizLaunch(hostId) {
        const st = wizState(hostId);
        const payload = wizPayload(hostId);
        st.busy = true; wizRenderActions(hostId);
        toast('正在启动实例…');
        const result = await apiPost('/instances/launch', payload);
        st.busy = false;
        if (!result) { toast('启动请求失败', 'error'); wizRenderActions(hostId); return; }
        if (result.error) {
            toast('启动失败: ' + result.error, 'error');
        } else {
            toast(`实例 ${result.model_uid || ''} 已启动（端口 ${result.port || '--'}）`, 'success');
            st.uid = '';
            st.name = '';
            st.uidTouched = false;
            st.nameTouched = false;
            wizRender(hostId);
            if (activeTab() === 'launch') rmRefresh();
            if (activeTab() === 'dashboard') refreshAll();
        }
        // 后端对 uid 的客户端兼容性提示（如裸家族名会被 Cherry 认成 Qwen3 14B）
        if (Array.isArray(result.warnings) && result.warnings.length) {
            toast('⚠ ' + result.warnings[0], 'warn', 8000);
        }
        wizRenderActions(hostId);
    }

    function activeTab() {
        return document.querySelector('.nav-item.active')?.dataset?.tab || 'dashboard';
    }

    // ---- 模型文件选择弹窗 ----
    async function wizOpenFiles(hostId) {
        const st = wizState(hostId);
        wizFileCtx = { hostId };
        const modal = document.getElementById('fileBrowserModal');
        if (!modal) return;
        modal.classList.add('active');
        document.getElementById('fileBrowserTitle').textContent =
            `📂 选择模型文件 · ${st.category} / ${st.engine}`;
        await wizLoadFiles(hostId, '');
    }

    async function wizLoadFiles(hostId, filter) {
        const st = wizState(hostId);
        const body = document.getElementById('fileBrowserBody');
        if (!body) return;
        body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">加载中…</div>';
        const qs = new URLSearchParams({ category: st.category, engine: st.engine });
        const data = await apiGet('/schema/models?' + qs.toString());
        if (!data) { body.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">加载失败</div>'; return; }
        st.models = { files: data.files || [], dirs: data.dirs || [] };
        const kw = (filter || '').toLowerCase();
        const files = st.models.files.filter(f => !kw || f.path.toLowerCase().includes(kw));
        const dirs = st.models.dirs.filter(d => !kw || d.path.toLowerCase().includes(kw));

        let html = `<div style="margin-bottom:10px;display:flex;gap:6px;align-items:center;">
            <input class="form-input" id="wizFileKw" placeholder="按路径过滤…" style="flex:1" value="${escapeAttr(filter || '')}"
                   onkeydown="if(event.key==='Enter'){window.wizLoadFiles('${hostId}', this.value)}">
            <button class="btn btn-sm" onclick="window.wizLoadFiles('${hostId}', document.getElementById('wizFileKw').value)">🔍 过滤</button>
            <button class="btn btn-sm" onclick="window.wizLoadFiles('${hostId}', '')">↺ 重置</button>
        </div>
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">
            根目录 ${escapeHtml(data.root || '/models')} · ${files.length} 个文件 / ${dirs.length} 个模型目录（显示前 300 条）
        </div>`;

        if (dirs.length) {
            html += '<div style="font-size:11px;color:var(--text-muted);margin:8px 0 4px">📁 目录型模型（vLLM / Diffusers / sentence-transformers 用）</div>';
            html += dirs.slice(0, 60).map(d => `<div class="fb-item fb-dir"
                onclick="window.wizPickFile('${hostId}','${escapeAttr(d.path)}')">
                📁 ${escapeHtml(d.path)} <span style="float:right;font-size:11px;color:var(--text-muted)">${d.size_mb}MB</span></div>`).join('');
        }

        html += '<div style="font-size:11px;color:var(--text-muted);margin:10px 0 4px">📄 文件</div>';
        if (!files.length) {
            html += '<div style="font-size:12px;color:var(--text-muted);padding:8px 0">没有匹配文件。可在 Tools → 模型下载 里先下载模型。</div>';
        } else {
            html += files.slice(0, 300).map(f => `<div class="fb-item fb-file"
                onclick="window.wizPickFile('${hostId}','${escapeAttr(f.path)}')">
                🧩 ${escapeHtml(f.path)}
                <span style="float:right;font-size:11px;color:var(--text-muted)">${f.size_mb}MB ${f.is_mmproj ? '<span class="eng-pill">mmproj</span>' : ''}</span></div>`).join('');
        }
        body.innerHTML = html;
    }

    function wizPickFile(hostId, path) {
        const st = wizState(hostId);
        st.modelFile = path;
        document.getElementById('fileBrowserModal')?.classList.remove('active');
        // 若是 mmproj，自动填到参数里
        if (/mmproj/i.test(path)) {
            st.params['mmproj'] = path;
        }
        wizRender(hostId);
        toast('已选择: ' + path);
    }

    // ================================================================
    // v1.0/v1.1 Running Models (动态实例)
    // ================================================================
    async function rmRefresh() {
        const [running, all] = await Promise.all([
            apiGet('/running-models'),
            apiGet('/instances'),
        ]);
        if (!running && !all) return;
        const merged = new Map();
        // 以 /instances 为全集（固定 9 类 + 动态实例）——合并「启动新实例」后，
        // 这个页面就是唯一入口，停着的实例也要能看到并一键启动。
        Object.values(all || {}).forEach(inst => {
            if (!inst || !inst.model_id) return;
            merged.set(inst.model_id, {
                model_uid: inst.model_id,
                model_name: inst.name,
                model_type: inst.category,
                engine_type: inst.engine_type,
                engine_version: inst.engine_version,
                flavor: inst.flavor,
                port: inst.port,
                status: inst.status,
                replicas: inst.replicas,
                model_file: inst.selected_model_file,
                uptime_seconds: inst.uptime_seconds,
                gpu_memory_mb: inst.gpu_memory_mb,
                memory_mb: inst.memory_mb,
                cpu_percent: inst.cpu_percent,
                request_count: inst.request_count,
                is_dynamic: !!inst.is_dynamic,
            });
        });
        // 运行时数据（PID/CPU/显存/请求数）以 /running-models 为准，覆盖一层
        ((running && running.models) || []).forEach(m => {
            const cur = merged.get(m.model_uid) || {};
            merged.set(m.model_uid, { ...cur, ...m, is_dynamic: cur.is_dynamic });
        });
        runningModels = Array.from(merged.values());
        // 运行中的排前面，其余按类别 + 端口
        runningModels.sort((a, b) => {
            const ar = a.status === 'running' ? 0 : 1;
            const br = b.status === 'running' ? 0 : 1;
            if (ar !== br) return ar - br;
            return (a.port || 0) - (b.port || 0);
        });
        rmRender();
    }

    function rmRender() {
        const tbody = document.getElementById('rmTableBody');
        if (!tbody) return;
        const stats = {
            running: runningModels.filter(m => m.status === 'running').length,
            dynamic: runningModels.filter(m => m.is_dynamic).length,
            // 「已占用端口」只算真正在跑（或正在启动）的实例
            ports: runningModels.filter(m => ['running', 'starting'].includes(m.status))
                .map(m => m.port).filter(Boolean),
            vram: runningModels.reduce((s, m) => s + (m.gpu_memory_mb || 0), 0),
        };
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        set('rmStatRunning', stats.running + ' / ' + runningModels.length);
        set('rmStatDynamic', stats.dynamic);
        set('rmStatPorts', stats.ports.length ? stats.ports.join(', ') : '--');
        set('rmStatVram', stats.vram ? Math.round(stats.vram) : '--');

        if (!runningModels.length) {
            tbody.innerHTML = '<tr><td colspan="13" class="rm-empty">暂无运行中的模型实例，点右上角「启动新实例」创建一个</td></tr>';
            return;
        }
        tbody.innerHTML = runningModels.map(m => {
            const icon = categoryIcons[m.model_type] || '🤖';
            const engineLabel = escapeHtml(m.engine_type || '--') +
                (m.engine_version ? `<span style="color:var(--text-muted);font-size:11px;">@${escapeHtml(m.engine_version)}</span>` : '');
            const statusClass = ['running', 'starting', 'error'].includes(m.status) ? m.status : 'stopped';
            const uid = escapeAttr(m.model_uid);
            return `<tr onclick="window.openModelDetail('${uid}')" title="点整行查看 / 编辑该实例">
                <td class="rm-mono">${escapeHtml(m.model_uid)}</td>
                <td>${icon} ${escapeHtml(m.model_name || m.model_uid)}</td>
                <td><span class="rm-badge badge-${escapeHtml(m.model_type)}">${escapeHtml(m.model_type)}</span></td>
                <td>${engineLabel}</td>
                <td>${m.flavor ? `<span class="eng-pill">${escapeHtml(m.flavor)}</span>` : '--'}</td>
                <td class="rm-mono">${m.port || '--'}</td>
                <td><span class="status-indicator ${statusClass}"></span> ${escapeHtml(m.status)}</td>
                <td>${m.gpu_memory_mb ? Math.round(m.gpu_memory_mb) + ' MB' : '--'}</td>
                <td>${m.memory_mb ? Math.round(m.memory_mb) + ' MB' : '--'}</td>
                <td>${m.cpu_percent != null ? Number(m.cpu_percent).toFixed(1) + '%' : '--'}</td>
                <td>${m.uptime_seconds ? formatUptime(m.uptime_seconds) : '--'}</td>
                <td>${m.request_count || 0}</td>
                <td class="rm-actions">
                    ${rowActions(m.model_uid, m.status)}
                    ${m.is_dynamic ? `<button class="btn btn-sm" onclick="event.stopPropagation();window.rmTerminate('${uid}')" title="停止并从实例列表移除">结束运行</button>` : ''}
                    <button class="btn btn-sm" onclick="event.stopPropagation();window.rmSaveTpl('${uid}')" title="另存为模版">💾</button>
                </td>
            </tr>`;
        }).join('');
    }

    async function rmStop(uid) {
        const result = await apiPost(`/instances/${encodeURIComponent(uid)}/stop`);
        if (result && result.error) toast('停止失败: ' + result.error, 'error');
        else toast('已停止 ' + uid, 'success');
        rmRefresh();
    }

    async function rmRestart(uid) {
        toast('正在重启 ' + uid + '…');
        const result = await apiPost(`/instances/${encodeURIComponent(uid)}/restart`);
        if (result && result.error) toast('重启失败: ' + result.error, 'error');
        else toast('已重启 ' + uid, 'success');
        rmRefresh();
    }

    async function rmTerminate(uid) {
        if (!confirm(`确定结束实例 ${uid} 的运行？\n将停止服务、释放端口并删除该实例定义（不可恢复）。`)) return;
        const result = await apiDelete(`/instances/${encodeURIComponent(uid)}`);
        if (result && result.error) toast('结束运行失败: ' + result.error, 'error');
        else toast('已结束运行 ' + uid, 'success');
        rmRefresh();
    }

    async function rmSaveTpl(uid) {
        const r = await apiPost('/templates/from-instance', { model_id: uid });
        if (!r) { toast('导出失败', 'error'); return; }
        if (r.error) { toast('导出失败: ' + r.error, 'error'); return; }
        toast('已保存模版: ' + r.template.name, 'success');
        tplRefresh();
    }

    // ================================================================
    // v1.1 Launch Model（模版 CRUD + 编辑器）
    // ================================================================
    let tplList = [];
    let tplCurrent = null;
    let tplEditing = null;    // 正在编辑的模版（null = 新建）

    async function tplRefresh() {
        const cat = document.getElementById('tplCategory')?.value || '';
        const kw = (document.getElementById('tplKeyword')?.value || '').trim();
        const params = new URLSearchParams();
        if (cat) params.set('category', cat);
        if (kw) params.set('q', kw);
        const data = await apiGet('/templates' + (params.toString() ? '?' + params.toString() : ''));
        if (!data) return;
        tplList = data.templates || [];
        tplRender();
    }

    function tplRender() {
        const grid = document.getElementById('tplGrid');
        const stats = document.getElementById('tplStats');
        if (!grid) return;
        if (stats) stats.textContent = `共 ${tplList.length} 个模版`;
        if (!tplList.length) {
            grid.innerHTML = '<div class="tpl-empty">没有匹配的模版</div>';
            return;
        }
        grid.innerHTML = tplList.map(t => {
            const icon = categoryIcons[t.category] || '🤖';
            const engines = (t.engines || []).join(' / ');
            const vram = t.vram_hint_gb ? `~${t.vram_hint_gb}G 显存` : '';
            const custom = t.is_custom ? '<span class="tpl-tag tpl-tag-custom">自定义</span>' : '';
            const tid = escapeAttr(t.id);
            return `<div class="tpl-card" onclick="window.tplOpenDetail('${tid}')">
                <div class="tpl-card-head">
                    <span class="tpl-name">${icon} ${escapeHtml(t.name)}</span>${custom}
                </div>
                <div class="tpl-card-cat">
                    <span class="rm-badge badge-${escapeHtml(t.category)}">${escapeHtml(t.category)}</span>
                    ${vram ? `<span class="tpl-vram">${vram}</span>` : ''}
                </div>
                <div class="tpl-card-desc">${escapeHtml(t.description || '（无描述）')}</div>
                <div class="tpl-card-foot"><span class="tpl-engines">${escapeHtml(engines || '--')}</span></div>
            </div>`;
        }).join('');
    }

    async function tplOpenDetail(tid) {
        const tpl = await apiGet('/templates/' + encodeURIComponent(tid));
        if (!tpl) { toast('模版加载失败', 'error'); return; }
        tplCurrent = tpl;
        const box = document.getElementById('tplDetail');
        const title = document.getElementById('tplDetailTitle');
        const body = document.getElementById('tplDetailBody');
        if (!box || !title || !body) return;
        title.textContent = tpl.name;

        const engineRows = (tpl.engines || []).map(e => {
            const params = Object.keys(e.params || {});
            const chips = params.length
                ? `<div class="chip-list">${params.map(k => `<span class="chip">${escapeHtml(k)}=${escapeHtml(String(e.params[k]))}</span>`).join('')}</div>`
                : '<span style="font-size:11px;color:var(--text-muted)">（默认参数）</span>';
            return `<tr>
                <td class="rm-mono">${escapeHtml(e.engine)}</td>
                <td>${escapeHtml(e.version || 'latest')}</td>
                <td>${e.flavor ? `<span class="eng-pill">${escapeHtml(e.flavor)}</span>` : '--'}</td>
                <td style="max-width:340px">${chips}</td>
            </tr>`;
        }).join('') || '<tr><td colspan="4" class="rm-empty">未声明引擎</td></tr>';

        const sourceRows = (tpl.sources || []).map(s =>
            `<tr><td>${escapeHtml(s.provider)}</td><td class="rm-mono">${escapeHtml(s.repo_id)}</td>
             <td>${escapeHtml((s.files || []).join(', ') || '全部文件')}</td></tr>`
        ).join('') || '<tr><td colspan="3" class="rm-empty">无下载源（手动指定模型文件）</td></tr>';

        body.innerHTML = `
            <p class="tpl-desc">${escapeHtml(tpl.description || '')}</p>
            <div class="tpl-meta">
                <span>类型 <b>${escapeHtml(tpl.category)}</b></span>
                ${tpl.context_length ? `<span>上下文 <b>${tpl.context_length}</b></span>` : ''}
                ${tpl.vram_hint_gb ? `<span>显存 <b>~${tpl.vram_hint_gb}G</b></span>` : ''}
                ${tpl.model_file ? `<span>模型文件 <b class="rm-mono">${escapeHtml(tpl.model_file)}</b></span>` : ''}
                ${(tpl.tags || []).length ? `<span>标签 <b>${escapeHtml(tpl.tags.join(', '))}</b></span>` : ''}
            </div>
            <h4>引擎与版本</h4>
            <table class="rm-table"><thead><tr><th>引擎</th><th>版本</th><th>Flavor</th><th>参数</th></tr></thead>
            <tbody>${engineRows}</tbody></table>
            <h4>下载源</h4>
            <table class="rm-table"><thead><tr><th>源</th><th>仓库</th><th>文件</th></tr></thead><tbody>${sourceRows}</tbody></table>
            <h4>部署</h4>
            <div class="form-grid">
                <div class="field"><label>引擎</label>
                    <select id="tplDeployEngine" class="form-select">
                        ${(tpl.engines || []).map((e, i) =>
                            `<option value="${i}">${escapeHtml(e.engine)}${e.version ? '@' + escapeHtml(e.version) : ''}${e.flavor ? '/' + escapeHtml(e.flavor) : ''}</option>`).join('')
                            || '<option value="0">（无）</option>'}
                    </select></div>
                <div class="field"><label>实例 UID <span class="hint">（客户端看到的模型 ID）</span></label><input id="tplDeployUid" class="form-input" placeholder="留空自动生成"></div>
                <div class="field"><label>模型名称 <span class="hint">（客户端显示的名字）</span></label><input id="tplDeployName" class="form-input" placeholder="留空使用模版名"></div>
                <div class="field span-2"><label>模型文件（相对 /models）</label>
                    <input id="tplDeployFile" class="form-input" value="${escapeAttr(tpl.model_file || '')}" placeholder="留空使用模版默认"></div>
                <div class="field"><label>副本数</label><input id="tplDeployReplicas" type="number" class="form-input" value="1" min="1" max="8"></div>
            </div>
            <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap;">
                <button class="btn btn-success" onclick="window.tplDeploy()">▶ 一键部署</button>
                <button class="btn" onclick="window.tplDownload()">📥 下载模型文件</button>
                <button class="btn" onclick="window.tplOpenEditor('${escapeAttr(tpl.id)}')">✏️ 编辑</button>
                <button class="btn" onclick="window.tplDuplicate('${escapeAttr(tpl.id)}')">⧉ 复制</button>
                ${tpl.is_custom ? `<button class="btn btn-danger" onclick="window.tplDelete('${escapeAttr(tpl.id)}')">🗑 删除</button>` : ''}
            </div>`;
        box.style.display = 'block';
        box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function tplCloseDetail() {
        const box = document.getElementById('tplDetail');
        if (box) box.style.display = 'none';
        tplCurrent = null;
    }

    async function tplDeploy() {
        if (!tplCurrent) return;
        const idx = parseInt(document.getElementById('tplDeployEngine')?.value || '0', 10);
        const eng = (tplCurrent.engines || [])[idx] || {};
        const uid = (document.getElementById('tplDeployUid')?.value || '').trim();
        const displayName = (document.getElementById('tplDeployName')?.value || '').trim();
        const file = (document.getElementById('tplDeployFile')?.value || '').trim();
        const replicas = parseInt(document.getElementById('tplDeployReplicas')?.value || '1', 10) || 1;

        const payload = {
            engine_type: eng.engine,
            engine_version: eng.version || '',
            flavor: eng.flavor || '',
            parameters: eng.params || {},
            replicas: replicas,
        };
        if (uid) payload.model_uid = uid;
        if (displayName) payload.model_name = displayName;
        if (file) payload.model_file = file;

        toast('正在按模版部署…');
        const result = await apiPost(`/templates/${encodeURIComponent(tplCurrent.id)}/deploy`, payload);
        if (!result) { toast('部署请求失败', 'error'); return; }
        if (result.error) { toast('部署失败: ' + result.error, 'error'); return; }
        toast(`已部署 ${result.model_uid}（端口 ${result.port}）`, 'success');
        rmRefresh();
    }

    async function tplDownload() {
        if (!tplCurrent) return;
        const src = await apiGet(`/templates/${encodeURIComponent(tplCurrent.id)}/sources`);
        if (!src || !src.sources || !src.sources.length) {
            toast('该模版没有声明下载源，请手动指定模型文件', 'error');
            return;
        }
        const s = src.sources[0];
        const payload = {
            model_id: s.repo_id,
            source: s.provider || 'huggingface',
            category: tplCurrent.category,
            files: s.files || [],
            revision: s.revision || '',
            target_dir: tplCurrent.id,
        };
        const r = await apiPost('/models/download', payload);
        if (!r) { toast('下载请求失败', 'error'); return; }
        if (r.error) { toast('下载失败: ' + r.error, 'error'); return; }
        toast(`已开始下载 ${s.repo_id}（任务 ${r.task_id}），可在 Tools 页查看进度`, 'success');
    }

    async function tplDelete(tid) {
        if (!confirm(`确定删除模版 ${tid}？`)) return;
        const r = await apiDelete('/templates/' + encodeURIComponent(tid));
        if (r && r.error) { toast('删除失败: ' + r.error, 'error'); return; }
        toast('模版已删除', 'success');
        tplCloseDetail();
        tplRefresh();
    }

    async function tplDuplicate(tid) {
        const newId = prompt('新模版 ID（可选，字母数字-_.）', tid + '-copy');
        if (newId === null) return;
        const r = await apiPost(`/templates/${encodeURIComponent(tid)}/duplicate`, { id: (newId || '').trim() });
        if (!r || r.error) { toast('复制失败: ' + (r && r.error), 'error'); return; }
        toast('已复制为 ' + r.template.name, 'success');
        tplRefresh();
    }

    // ---- 模版编辑器（新建 / 编辑） ----
    function tplBlank(category) {
        return {
            id: '', name: '', category: category || 'chat', description: '',
            tags: [], sources: [], engines: [], model_file: '',
            vram_hint_gb: null, context_length: null,
        };
    }

    async function tplOpenEditor(tid, opts) {
        opts = opts || {};
        let tpl = null;
        if (tid) {
            tpl = await apiGet('/templates/' + encodeURIComponent(tid));
            if (!tpl) { toast('模版加载失败', 'error'); return; }
        } else if (opts.fromWizard) {
            const st = wizState(opts.fromWizard);
            tpl = tplBlank(st.category);
            tpl.model_file = st.modelFile || '';
            tpl.name = st.modelFile ? st.modelFile.split('/').pop().replace(/\.(gguf|safetensors|bin)$/i, '') : '';
            tpl.engines = [{
                engine: st.engine, version: st.version, flavor: st.flavor,
                params: JSON.parse(JSON.stringify(st.params || {})),
            }];
        } else {
            tpl = tplBlank('');
        }
        tplEditing = tid ? { id: tid, isCustom: !!tpl.is_custom } : null;

        const modal = document.getElementById('tplEditorModal');
        const title = document.getElementById('tplEditorTitle');
        const body = document.getElementById('tplEditorBody');
        if (!modal || !body) return;
        title.textContent = tid ? `编辑模版 · ${tpl.name}` : '新建模版';
        body.innerHTML = tplEditorHtml(tpl, tid);
        modal.classList.add('active');
    }

    function tplCloseEditor() {
        document.getElementById('tplEditorModal')?.classList.remove('active');
        tplEditing = null;
    }

    function tplEditorHtml(tpl, tid) {
        const catOptions = WIZ_CATEGORIES.map(c =>
            `<option value="${c[0]}" ${c[0] === tpl.category ? 'selected' : ''}>${c[1]}</option>`).join('');
        const tagStr = (tpl.tags || []).join(', ');
        const engineRows = (tpl.engines || []).map((e, i) => tplEngineRow(e, i)).join('');
        const sourceRows = (tpl.sources || []).map((s, i) => tplSourceRow(s, i)).join('');

        return `
        <div class="tpl-editor-section">
            <h4>基本信息</h4>
            <div class="form-grid">
                <div class="field"><label>模版 ID <span class="hint">（留空按名称生成）</span></label>
                    <input class="form-input" id="teId" value="${escapeAttr(tpl.id || '')}" ${tid ? 'readonly' : ''}></div>
                <div class="field"><label>名称 *</label>
                    <input class="form-input" id="teName" value="${escapeAttr(tpl.name || '')}" placeholder="Qwen3-Embedding-0.6B"></div>
                <div class="field"><label>类别 *</label>
                    <select class="form-select" id="teCategory">${catOptions}</select></div>
                <div class="field"><label>标签 <span class="hint">（逗号分隔）</span></label>
                    <input class="form-input" id="teTags" value="${escapeAttr(tagStr)}" placeholder="embedding, qwen3"></div>
                <div class="field full"><label>描述</label>
                    <textarea class="form-input" id="teDesc" rows="2">${escapeHtml(tpl.description || '')}</textarea></div>
                <div class="field"><label>上下文长度</label>
                    <input type="number" class="form-input" id="teCtx" value="${tpl.context_length || ''}" placeholder="32768"></div>
                <div class="field"><label>显存提示 (GB)</label>
                    <input type="number" step="0.1" class="form-input" id="teVram" value="${tpl.vram_hint_gb || ''}" placeholder="2"></div>
                <div class="field span-2"><label>默认模型文件（相对 /models）</label>
                    <input class="form-input" id="teModelFile" value="${escapeAttr(tpl.model_file || '')}" placeholder="Qwen/Qwen3-Embedding-0.6B-GGUF/xxx.gguf"></div>
            </div>
        </div>

        <div class="tpl-editor-section">
            <h4>引擎与参数
                <button class="btn btn-sm" onclick="window.teAddEngine()">＋ 添加引擎</button></h4>
            <div id="teEngines">${engineRows || '<div style="font-size:12px;color:var(--text-muted)">尚未声明引擎</div>'}</div>
        </div>

        <div class="tpl-editor-section">
            <h4>下载源
                <button class="btn btn-sm" onclick="window.teAddSource()">＋ 添加来源</button></h4>
            <div id="teSources">${sourceRows || '<div style="font-size:12px;color:var(--text-muted)">尚未声明下载源</div>'}</div>
        </div>

        <div style="display:flex;gap:8px;justify-content:flex-end;border-top:1px solid var(--border);padding-top:12px;">
            <button class="btn" onclick="window.tplCloseEditor()">取消</button>
            <button class="btn btn-success" onclick="window.tplSaveEditor()">💾 保存模版</button>
        </div>`;
    }

    function _engineOptions(selected) {
        const types = (engineCatalog && engineCatalog.engines) ? Object.keys(engineCatalog.engines)
            : ['llama_cpp', 'vllm', 'diffusers', 'sentence_transformers'];
        return types.map(t => {
            const label = (engineCatalog && engineCatalog.engines && engineCatalog.engines[t] && engineCatalog.engines[t].display_name) || t;
            return `<option value="${t}" ${t === selected ? 'selected' : ''}>${escapeHtml(label)}</option>`;
        }).join('');
    }

    function tplEngineRow(e, i) {
        const paramsStr = Object.keys(e.params || {}).map(k => `${k}=${e.params[k]}`).join(', ');
        return `<div class="tpl-engine-row" data-idx="${i}">
            <div class="form-grid">
                <div class="field"><label>引擎</label>
                    <select class="form-select te-engine">${_engineOptions(e.engine)}</select></div>
                <div class="field"><label>版本 <span class="hint">（空=latest）</span></label>
                    <input class="form-input te-version" value="${escapeAttr(e.version || '')}" placeholder="latest / 0.22.1"></div>
                <div class="field"><label>Flavor</label>
                    <select class="form-select te-flavor">
                        <option value="" ${!e.flavor ? 'selected' : ''}>（默认）</option>
                        <option value="cuda" ${e.flavor === 'cuda' ? 'selected' : ''}>cuda</option>
                        <option value="cpu" ${e.flavor === 'cpu' ? 'selected' : ''}>cpu</option>
                    </select></div>
                <div class="field span-2"><label>参数 <span class="hint">（k=v, 逗号分隔）</span></label>
                    <input class="form-input te-params" value="${escapeAttr(paramsStr)}" placeholder="ctx_size=32768, n_gpu_layers=-1"></div>
                <div class="field" style="justify-content:flex-end">
                    <button class="btn btn-sm btn-danger" onclick="this.closest('.tpl-engine-row').remove()">移除</button></div>
            </div>
        </div>`;
    }

    function tplSourceRow(s, i) {
        return `<div class="tpl-engine-row" data-idx="${i}">
            <div class="form-grid">
                <div class="field"><label>来源</label>
                    <select class="form-select ts-provider">
                        <option value="huggingface" ${s.provider === 'huggingface' ? 'selected' : ''}>huggingface</option>
                        <option value="modelscope" ${s.provider === 'modelscope' ? 'selected' : ''}>modelscope</option>
                    </select></div>
                <div class="field span-2"><label>仓库 ID</label>
                    <input class="form-input ts-repo" value="${escapeAttr(s.repo_id || '')}" placeholder="Qwen/Qwen3-Embedding-0.6B"></div>
                <div class="field"><label>revision</label>
                    <input class="form-input ts-rev" value="${escapeAttr(s.revision || '')}" placeholder="main"></div>
                <div class="field span-2"><label>文件 <span class="hint">（逗号分隔，空=全部）</span></label>
                    <input class="form-input ts-files" value="${escapeAttr((s.files || []).join(', '))}"></div>
                <div class="field" style="justify-content:flex-end">
                    <button class="btn btn-sm btn-danger" onclick="this.closest('.tpl-engine-row').remove()">移除</button></div>
            </div>
        </div>`;
    }

    function teAddEngine() {
        const box = document.getElementById('teEngines');
        if (!box) return;
        if (box.querySelector('div[style*="尚未声明引擎"]')) box.innerHTML = '';
        box.insertAdjacentHTML('beforeend', tplEngineRow({ engine: 'llama_cpp', version: 'latest', flavor: 'cuda', params: {} }, 0));
    }

    function teAddSource() {
        const box = document.getElementById('teSources');
        if (!box) return;
        if (box.querySelector('div[style*="尚未声明下载源"]')) box.innerHTML = '';
        box.insertAdjacentHTML('beforeend', tplSourceRow({ provider: 'huggingface', repo_id: '', files: [] }, 0));
    }

    function teCollect() {
        const engines = [];
        document.querySelectorAll('#teEngines .tpl-engine-row').forEach(row => {
            const engine = row.querySelector('.te-engine')?.value || '';
            if (!engine) return;
            const params = {};
            (row.querySelector('.te-params')?.value || '').split(',').forEach(kv => {
                const s = kv.trim();
                if (!s) return;
                const i = s.indexOf('=');
                if (i < 0) return;
                const k = s.slice(0, i).trim();
                let v = s.slice(i + 1).trim();
                if (v === 'true') v = true; else if (v === 'false') v = false;
                else if (v !== '' && !isNaN(Number(v))) v = Number(v);
                if (k) params[k] = v;
            });
            engines.push({
                engine,
                version: (row.querySelector('.te-version')?.value || '').trim(),
                flavor: row.querySelector('.te-flavor')?.value || '',
                params,
            });
        });
        const sources = [];
        document.querySelectorAll('#teSources .tpl-engine-row').forEach(row => {
            const repo = (row.querySelector('.ts-repo')?.value || '').trim();
            if (!repo) return;
            sources.push({
                provider: row.querySelector('.ts-provider')?.value || 'huggingface',
                repo_id: repo,
                revision: (row.querySelector('.ts-rev')?.value || '').trim(),
                files: (row.querySelector('.ts-files')?.value || '').split(',').map(s => s.trim()).filter(Boolean),
            });
        });
        const ctx = document.getElementById('teCtx')?.value;
        const vram = document.getElementById('teVram')?.value;
        return {
            id: (document.getElementById('teId')?.value || '').trim(),
            name: (document.getElementById('teName')?.value || '').trim(),
            category: document.getElementById('teCategory')?.value || 'chat',
            description: (document.getElementById('teDesc')?.value || '').trim(),
            tags: (document.getElementById('teTags')?.value || '').split(',').map(s => s.trim()).filter(Boolean),
            model_file: (document.getElementById('teModelFile')?.value || '').trim(),
            context_length: ctx ? parseInt(ctx, 10) : null,
            vram_hint_gb: vram ? parseFloat(vram) : null,
            engines, sources,
        };
    }

    async function tplSaveEditor() {
        const payload = teCollect();
        if (!payload.name) { toast('请填写模版名称', 'error'); return; }
        if (!payload.engines.length) { toast('至少声明一个引擎', 'error'); return; }
        let r;
        if (tplEditing) {
            r = await apiPut('/templates/' + encodeURIComponent(tplEditing.id), payload);
        } else {
            r = await apiPost('/templates', payload);
        }
        if (!r) { toast('保存失败', 'error'); return; }
        if (r.error) { toast('保存失败: ' + r.error, 'error'); return; }
        toast('模版已保存: ' + (r.template && r.template.name || payload.name), 'success');
        tplCloseEditor();
        tplRefresh();
        if (tplCurrent) tplOpenDetail(r.template.id);
    }

    function tplOpenSave() {
        tplOpenEditor('');
    }

    // ================================================================
    // v1.0 Token Router (R3/R4)
    // ================================================================
    // ---- Models 页：二级标签（启动 / 运行中 / 模版） ----
    function msSwitch(which) {
        document.querySelectorAll('.sub-tab').forEach(b =>
            b.classList.toggle('active', b.dataset.sub === which));
        document.querySelectorAll('.ms-pane').forEach(p =>
            p.classList.toggle('active', p.id === 'ms-' + which));
        if (which === 'instances') {
            if (typeof rmRefresh === 'function') rmRefresh();
            // 首次进入实例页时按需初始化启动向导（折叠态不渲染，省一次引擎查询）
            if (document.getElementById('instWizardGroup')?.style.display !== 'none') {
                wizInit('instWizard', {
                    title: '🚀 启动新实例',
                    subtitle: '类别 → 引擎 → 版本 → 模型文件 → 参数；端口自动分配（18100+），实例定义持久化',
                });
            }
        }
        if (which === 'templates' && typeof tplRefresh === 'function') tplRefresh();
    }

    // Models 页进入：默认停在「实例管理」，并刷新运行中列表
    function msOnEnter() {
        const activePane = document.querySelector('.ms-pane.active');
        const which = activePane ? activePane.id.replace('ms-', '') : 'instances';
        if (which === 'instances') {
            if (typeof rmRefresh === 'function') rmRefresh();
        } else if (which === 'templates') {
            if (typeof tplRefresh === 'function') tplRefresh();
        }
    }

    // 启动向导展开 / 收起（并排在同一页，避免两个几乎一样的向导页）
    function msToggleWizard() {
        const group = document.getElementById('instWizardGroup');
        const btn = document.getElementById('instWizardToggle');
        if (!group) return;
        const nowHidden = group.style.display === 'none';
        group.style.display = nowHidden ? '' : 'none';
        if (btn) btn.textContent = nowHidden ? '✕ 收起启动向导' : '🚀 启动新实例';
        if (nowHidden) {
            wizInit('instWizard', {
                title: '🚀 启动新实例',
                subtitle: '类别 → 引擎 → 版本 → 模型文件 → 参数；端口自动分配（18100+），实例定义持久化',
            });
            group.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    // ================================================================
    // Token Router（v1.3）：provider 自动拉模型 / key 编辑 / 定价 / 计费
    // ================================================================
    let rtModelsCache = [];
    let rtProvidersCache = [];
    let tkEditingId = null;

    function fmtTok(n) {
        n = Number(n || 0);
        if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
        if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
        return String(n);
    }

    async function rtRefresh() {
        const status = await apiGet('/router/status');
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        if (status) {
            set('rtProviders', status.providers);
            set('rtKeys', status.keys);
            set('rtModels', status.merged_models);
            const t = status.usage || {};
            set('rtCost', '¥' + (t.cost_cny || 0).toFixed(2));
        }
        await Promise.all([rtLoadProviders(), rtLoadModels(), rtLoadKeys(), rtLoadUsage()]);
    }

    // ---------------- Provider ----------------
    async function rtLoadProviders() {
        const data = await apiGet('/providers');
        const tbody = document.getElementById('pvTableBody');
        if (!tbody || !data) return;
        rtProvidersCache = data.providers || [];
        const rows = rtProvidersCache.map(p => {
            const fetched = (p.fetched_models || []).length;
            const manual = (p.models || []).length;
            const cost = p.cost || {};
            const priceTxt = (cost.input || cost.output)
                ? `${cost.input || 0} / ${cost.output || 0}` : '--';
            const keyTxt = p.has_key
                ? (p.api_key_masked || '✅ 已配置')
                : '❌ 未配置';
            const warn = p.api_key_in_env_field
                ? ' <span class="eng-pill bad" title="Key 被填进环境变量名字段，已自动兼容">⚠</span>' : '';
            return `<tr>
            <td class="rm-mono">${escapeHtml(p.name)}${warn}</td>
            <td>${escapeHtml(p.type)}</td>
            <td class="rm-mono" style="font-size:11px">${escapeHtml(p.base_url || '--')}</td>
            <td>${fetched || manual} <span class="hint">(${fetched} 自动)</span></td>
            <td style="font-size:11px">${keyTxt}</td>
            <td class="rm-mono" style="font-size:11px">${escapeHtml(priceTxt)}</td>
            <td class="rm-actions">
                <button class="btn btn-sm" onclick="window.pvEdit('${escapeAttr(p.name)}')">编辑</button>
                <button class="btn btn-sm" onclick="window.pvFetchModels('${escapeAttr(p.name)}')">拉模型</button>
                <button class="btn btn-sm btn-danger" onclick="window.pvDelete('${escapeAttr(p.name)}')">删</button>
            </td></tr>`;
        }).join('');
        tbody.innerHTML = rows || '<tr><td colspan="7" class="rm-empty">尚未配置 provider</td></tr>';
    }

    function pvTypeChange() {
        const t = document.getElementById('pvType')?.value;
        const base = document.getElementById('pvBase');
        const defaults = {
            openai: 'https://api.openai.com/v1',
            anthropic: 'https://api.anthropic.com/v1',
            deepseek: 'https://api.deepseek.com/v1',
            icompify: 'https://api.icompify.com/v1',
        };
        if (base && !base.value.trim() && defaults[t]) base.placeholder = defaults[t];
    }

    function pvResetForm() {
        ['pvName', 'pvBase', 'pvKey', 'pvCostIn', 'pvCostOut'].forEach(id => {
            const el = document.getElementById(id); if (el) el.value = '';
        });
        const w = document.getElementById('pvWeight'); if (w) w.value = '1';
        const hint = document.getElementById('pvKeyHint');
        if (hint) hint.textContent = '留空表示不修改；也可填环境变量名';
        const box = document.getElementById('pvFetched'); if (box) box.textContent = '';
    }

    function pvClearKey() {
        const el = document.getElementById('pvKey');
        if (el) { el.value = ''; el.dataset.clear = '1'; }
        const hint = document.getElementById('pvKeyHint');
        if (hint) hint.textContent = '保存后将清除已配置的 Key';
    }

    function pvEdit(name) {
        const p = rtProvidersCache.find(x => x.name === name);
        if (!p) return;
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        set('pvName', p.name);
        set('pvType', p.type || 'openai');
        set('pvBase', p.base_url || '');
        set('pvWeight', p.weight || 1);
        const cost = p.cost || {};
        set('pvCostIn', cost.input ?? '');
        set('pvCostOut', cost.output ?? '');
        const keyEl = document.getElementById('pvKey');
        if (keyEl) { keyEl.value = ''; delete keyEl.dataset.clear; }
        const hint = document.getElementById('pvKeyHint');
        if (hint) hint.textContent = p.has_key ? `已配置（${p.api_key_masked || '•••'}）；留空则保持不变` : '尚未配置 Key';
        const box = document.getElementById('pvFetched');
        if (box) {
            const n = (p.fetched_models || []).length;
            box.textContent = n ? `已拉取 ${n} 个模型${p.fetched_at ? '（' + new Date(p.fetched_at * 1000).toLocaleString() + '）' : ''}`
                : (p.fetch_error ? '上次拉取失败: ' + p.fetch_error : '尚未拉取模型');
        }
        document.getElementById('pvName')?.scrollIntoView({ block: 'center' });
    }

    async function pvSave() {
        const name = (document.getElementById('pvName')?.value || '').trim();
        if (!name) { toast('请填写 provider 名称', 'error'); return; }
        const payload = {
            type: document.getElementById('pvType')?.value || 'openai',
            weight: parseInt(document.getElementById('pvWeight')?.value || '1', 10) || 1,
        };
        const base = (document.getElementById('pvBase')?.value || '').trim();
        if (base) payload.base_url = base;
        const keyEl = document.getElementById('pvKey');
        const keyVal = (keyEl?.value || '').trim();
        if (keyVal) payload.api_key = keyVal;
        if (keyEl?.dataset.clear === '1') payload.clear_api_key = true;
        const cin = document.getElementById('pvCostIn')?.value;
        const cout = document.getElementById('pvCostOut')?.value;
        if (cin !== '' || cout !== '') {
            payload.cost = { input: parseFloat(cin) || 0, output: parseFloat(cout) || 0 };
        }

        const r = await apiPost('/providers/' + encodeURIComponent(name), payload);
        if (!r || r.error) { toast('保存失败: ' + (r && r.error), 'error'); return; }
        toast('Provider 已保存', 'success');
        if (keyEl) delete keyEl.dataset.clear;
        rtRefresh();
    }

    async function pvFetchModels(nameArg) {
        const name = nameArg || (document.getElementById('pvName')?.value || '').trim();
        if (!name) { toast('请先填写并保存 provider 名称', 'error'); return; }
        // 若表单里有未保存的 url/key，先保存，避免拉取到旧配置
        if (!nameArg) await pvSave();
        toast('正在用该 provider 的 URL + Key 拉取模型…');
        const r = await apiPost('/providers/' + encodeURIComponent(name) + '/models');
        if (!r) { toast('拉取请求失败', 'error'); return; }
        if (!r.ok) { toast('拉取失败: ' + (r.error || ''), 'error'); return; }
        toast(`拉取成功：${r.count} 个模型`, 'success');
        const box = document.getElementById('pvFetched');
        if (box) box.textContent = `已拉取 ${r.count} 个模型：` +
            (r.models || []).slice(0, 8).map(m => m.id || m).join('、') +
            (r.count > 8 ? ' …' : '');
        rtRefresh();
    }

    async function pvDelete(name) {
        if (!confirm(`确定删除 provider ${name}？`)) return;
        await apiDelete('/providers/' + encodeURIComponent(name));
        toast('已删除', 'success');
        rtRefresh();
    }

    // ---------------- 可用模型清单 ----------------
    async function rtLoadModels() {
        const data = await apiGet('/router/models');
        rtModelsCache = (data && data.data) || [];
        rtRenderModels();
        rtRenderKeyModelPicker();
    }

    function rtModelRow(m) {
        const owned = m.owned_by || '';
        const isLocal = owned === 'amm-local';
        const srcLabel = isLocal ? '本地实例' : escapeHtml(owned.replace('provider:', 'Provider · '));
        const pricing = m.pricing_cny_per_mtok || {};
        const pIn = pricing.input != null ? pricing.input : '--';
        const pOut = pricing.output != null ? pricing.output : '--';
        const extra = isLocal
            ? `${escapeHtml(m.model_type || '')} · ${escapeHtml(m.engine || '')} · :${m.port}`
            : escapeHtml(m.category || '');
        const ctx = m.context_length ? fmtTok(m.context_length) : '--';
        return `<tr>
            <td class="rm-mono" style="font-size:12px">${escapeHtml(m.id)}</td>
            <td style="font-size:11.5px">${srcLabel}</td>
            <td style="font-size:11.5px;color:var(--text-muted)">${extra || '--'}</td>
            <td class="rm-mono" style="font-size:11.5px">${pIn}</td>
            <td class="rm-mono" style="font-size:11.5px">${pOut}</td>
            <td class="rm-mono" style="font-size:11.5px">${ctx}</td>
            <td><button class="btn btn-sm" onclick="window.rtEditModel('${escapeAttr(m.id)}')">编辑</button></td>
        </tr>`;
    }

    function rtRenderModels() {
        const tbody = document.getElementById('rtModelTableBody');
        if (!tbody) return;
        const q = (document.getElementById('rmFilter')?.value || '').toLowerCase().trim();
        let list = rtModelsCache;
        if (q) {
            list = list.filter(m =>
                (m.id || '').toLowerCase().includes(q) ||
                (m.owned_by || '').toLowerCase().includes(q) ||
                (m.provider || '').toLowerCase().includes(q));
        }
        tbody.innerHTML = list.map(rtModelRow).join('') ||
            '<tr><td colspan="5" class="rm-empty">没有匹配的模型</td></tr>';
        const c = document.getElementById('rmCount');
        if (c) c.textContent = `${list.length} / ${rtModelsCache.length} 个模型`;
    }

    function rtRenderKeyModelPicker() {
        const box = document.getElementById('tkModelPicker');
        if (!box) return;
        const prev = new Set(
            [...box.querySelectorAll('input[type=checkbox]:checked')].map(i => i.value));
        if (!rtModelsCache.length) {
            box.innerHTML = '<span class="hint">暂无可用模型 <!-- 先在 Provider 里拉取 --></span>';
            return;
        }
        box.innerHTML = rtModelsCache.map(m => {
            const checked = prev.has(m.id) ? 'checked' : '';
            const tag = (m.owned_by || '') === 'amm-local' ? '本地' : '外部';
            return `<label class="tk-chip ${checked ? 'on' : ''}">
                <input type="checkbox" value="${escapeAttr(m.id)}" ${checked}
                       onchange="this.parentElement.classList.toggle('on', this.checked)">
                <span class="tk-chip-tag">${tag}</span>${escapeHtml(m.id)}</label>`;
        }).join('');
    }

    // ---------------- 虚拟 Key ----------------
    async function rtLoadKeys() {
        const data = await apiGet('/token-router/keys');
        const tbody = document.getElementById('tkTableBody');
        if (!tbody || !data) return;
        const rows = (data.keys || []).map(k => {
            const exp = k.expires_at
                ? new Date(k.expires_at * 1000).toLocaleString()
                : '永久';
            const limits = [];
            if (k.rpm_limit) limits.push(`RPM ${k.rpm_limit}`);
            if (k.monthly_token_limit) limits.push(`月 ${fmtTok(k.monthly_token_limit)}`);
            if (k.total_token_limit) limits.push(`总 ${fmtTok(k.total_token_limit)}`);
            const state = !k.enabled ? '<span class="eng-pill bad">已禁用</span>'
                : (k.expired ? '<span class="eng-pill bad">已过期</span>'
                    : '<span class="eng-pill ok">正常</span>');
            return `<tr>
            <td class="rm-mono" style="font-size:11px">${escapeHtml(k.key)}</td>
            <td>${escapeHtml(k.name)}</td>
            <td style="font-size:11px">${limits.join(' · ') || '不限'}</td>
            <td style="font-size:11px">${fmtTok(k.used_tokens)} / ${k.total_token_limit ? fmtTok(k.total_token_limit) : '∞'}</td>
            <td style="font-size:11px">${escapeHtml(exp)}</td>
            <td>${state}</td>
            <td class="rm-actions">
                <button class="btn btn-sm" onclick="window.tkEdit('${escapeAttr(k.key_id)}')">编辑</button>
                <button class="btn btn-sm" onclick="window.tkCopy('${escapeAttr(k.key_id)}')">复制</button>
                <button class="btn btn-sm btn-danger" onclick="window.tkRevoke('${escapeAttr(k.key_id)}')">吊销</button>
            </td></tr>`;
        }).join('');
        tbody.innerHTML = rows || '<tr><td colspan="7" class="rm-empty">尚未签发 key（当前为无鉴权本地模式）</td></tr>';
    }

    function tkSelectedModels() {
        return [...document.querySelectorAll('#tkModelPicker input[type=checkbox]:checked')]
            .map(i => i.value);
    }

    function tkCancelEdit() {
        tkEditingId = null;
        ['tkName', 'tkRpm', 'tkMonthly', 'tkTotal', 'tkExpires', 'tkNote'].forEach(id => {
            const el = document.getElementById(id); if (el) el.value = '';
        });
        document.querySelectorAll('#tkModelPicker input[type=checkbox]').forEach(i => {
            i.checked = false; i.parentElement.classList.remove('on');
        });
        const btn = document.getElementById('tkSubmitBtn');
        if (btn) btn.textContent = '＋ 签发 Key';
        const cancel = document.getElementById('tkCancelBtn');
        if (cancel) cancel.style.display = 'none';
        const hint = document.getElementById('tkEditHint');
        if (hint) hint.textContent = '';
    }

    function tkEdit(keyId) {
        apiGet('/token-router/keys').then(data => {
            const k = (data?.keys || []).find(x => x.key_id === keyId);
            if (!k) { toast('未找到该 key', 'error'); return; }
            const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
            set('tkName', k.name || '');
            set('tkRpm', k.rpm_limit || 0);
            set('tkMonthly', k.monthly_token_limit || 0);
            set('tkTotal', k.total_token_limit || 0);
            set('tkNote', k.note || '');
            if (k.expires_at) {
                const d = new Date(k.expires_at * 1000);
                const pad = n => String(n).padStart(2, '0');
                set('tkExpires', `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`);
            } else set('tkExpires', '');
            const allowed = new Set(k.allowed_models || []);
            document.querySelectorAll('#tkModelPicker input[type=checkbox]').forEach(i => {
                i.checked = allowed.has(i.value);
                i.parentElement.classList.toggle('on', i.checked);
            });
            tkEditingId = keyId;
            const btn = document.getElementById('tkSubmitBtn');
            if (btn) btn.textContent = '💾 保存修改';
            const cancel = document.getElementById('tkCancelBtn');
            if (cancel) cancel.style.display = '';
            const hint = document.getElementById('tkEditHint');
            if (hint) hint.textContent = `正在编辑：${k.name}（${k.key}）`;
            document.getElementById('tkName')?.scrollIntoView({ block: 'center' });
        });
    }

    async function tkCopy(keyId) {
        const r = await apiPost('/token-router/keys/' + encodeURIComponent(keyId) + '/reveal');
        if (!r || r.error) { toast('获取明文失败: ' + (r && r.error), 'error'); return; }
        try {
            await navigator.clipboard.writeText(r.key);
            toast('已复制到剪贴板', 'success');
        } catch {
            // 剪贴板被拒时退化为可手选的弹窗
            prompt('复制下面的 Key：', r.key);
        }
    }

    function tkPayload() {
        const expRaw = document.getElementById('tkExpires')?.value || '';
        return {
            name: (document.getElementById('tkName')?.value || '').trim() || 'app',
            rpm_limit: parseInt(document.getElementById('tkRpm')?.value || '0', 10) || 0,
            monthly_token_limit: parseInt(document.getElementById('tkMonthly')?.value || '0', 10) || 0,
            total_token_limit: parseInt(document.getElementById('tkTotal')?.value || '0', 10) || 0,
            expires_at: expRaw ? new Date(expRaw).getTime() / 1000 : 0,
            note: (document.getElementById('tkNote')?.value || '').trim(),
            allowed_models: tkSelectedModels(),
        };
    }

    async function tkIssue() {
        const payload = tkPayload();
        if (tkEditingId) {
            const r = await apiPut('/token-router/keys/' + encodeURIComponent(tkEditingId), payload);
            if (!r || r.error || r.success === false) {
                toast('保存失败: ' + ((r && r.error) || ''), 'error'); return;
            }
            toast('Key 已更新', 'success');
            tkCancelEdit();
            rtRefresh();
            return;
        }
        const r = await apiPost('/token-router/keys', payload);
        if (!r || r.error) { toast('签发失败: ' + (r && r.error), 'error'); return; }
        const box = document.getElementById('tkNew');
        if (box) {
            box.innerHTML = `<div class="tk-newkey">
                <div style="font-size:12px;margin-bottom:6px;">新 Key（仅此一次显示，请立即保存）:</div>
                <code>${escapeHtml(r.key)}</code>
                <button class="btn btn-sm" onclick="navigator.clipboard.writeText('${escapeAttr(r.key)}');window.toastSaved&&toast('已复制','success')">复制</button>
            </div>`;
        }
        toast('Key 已签发', 'success');
        rtRefresh();
    }

    async function tkRevoke(keyId) {
        if (!confirm('确定吊销该 key？吊销后不可恢复')) return;
        await apiDelete('/token-router/keys/' + encodeURIComponent(keyId));
        toast('已吊销', 'success');
        rtRefresh();
    }

    // ---------------- 用量 / 计费 / 指标 ----------------
    function rtExport(fmt) {
        window.open('/api/token-router/billing/export?format=' + encodeURIComponent(fmt), '_blank');
    }

    async function rtLoadUsage() {
        const data = await apiGet('/token-router/usage');
        const tbody = document.getElementById('usageTableBody');
        const totals = document.getElementById('usageTotals');
        if (!data) return;
        const t = data.totals || {};
        if (totals) {
            totals.textContent = `总请求 ${t.requests || 0} · 总 Tokens ${fmtTok(t.total_tokens)} · 累计消费 ¥${(t.cost_cny || 0).toFixed(4)}`;
        }
        if (tbody) {
            const rows = (data.rows || []).map(r => `<tr>
                <td class="rm-mono" style="font-size:11px">${escapeHtml(r.key)}</td>
                <td>${r.requests}</td>
                <td>${fmtTok(r.input_tokens)}</td>
                <td>${fmtTok(r.output_tokens)}</td>
                <td>${fmtTok(r.total_tokens)}</td>
                <td class="rm-mono">¥${(r.cost_cny || 0).toFixed(4)}</td></tr>`).join('');
            tbody.innerHTML = rows || '<tr><td colspan="6" class="rm-empty">暂无用量记录</td></tr>';
        }
        // 性能指标
        const mt = document.getElementById('metricsTableBody');
        if (mt) {
            const rows = (data.rows || []).filter(r => (r.metrics || {}).count);
            mt.innerHTML = rows.map(r => {
                const m = r.metrics || {};
                return `<tr>
                    <td class="rm-mono" style="font-size:11px">${escapeHtml(r.key)}</td>
                    <td>${m.ttft_ms != null ? m.ttft_ms + ' ms' : '--'}</td>
                    <td>${m.itl_ms != null ? m.itl_ms + ' ms' : '--'}</td>
                    <td>${m.tpot_ms != null ? m.tpot_ms + ' ms' : '--'}</td>
                    <td>${m.tps != null ? m.tps : '--'}</td>
                    <td>${m.e2e_ms != null ? m.e2e_ms + ' ms' : '--'}</td>
                    <td>${m.count || 0}</td></tr>`;
            }).join('') || '<tr><td colspan="7" class="rm-empty">暂无性能数据（需经 /api/router/relay 代理调用后才会采集）</td></tr>';
        }
    }

    // ---- 可用模型清单：编辑元数据 ----
    function rtEditModel(modelId) {
        const m = rtModelsCache.find(x => x.id === modelId) || { id: modelId };
        const pr = m.pricing_cny_per_mtok || {};
        const cats = ['', 'chat', 'embedding', 'reranker', 'asr', 'tts', 'ocr',
                      'image', 'video'];
        const modal = document.getElementById('modelModal');
        const title = document.getElementById('modalTitle');
        const body = document.getElementById('modalBody');
        if (!modal || !body) return;
        title.textContent = '编辑模型 · ' + modelId;
        body.innerHTML = `
            <div class="kv-grid">
                <div class="field"><label>模型 ID</label>
                    <input class="form-input" value="${escapeAttr(modelId)}" readonly></div>
                <div class="field"><label>类型</label>
                    <select id="mmCategory" class="form-select">
                        ${cats.map(c => `<option value="${c}" ${(m.category || '') === c ? 'selected' : ''}>${c || '（未设置）'}</option>`).join('')}
                    </select></div>
                <div class="field"><label>状态</label>
                    <select id="mmStatus" class="form-select">
                        ${['', 'running', 'stopped', 'disabled'].map(v =>
                            `<option value="${v}" ${(m.status || '') === v ? 'selected' : ''}>${v || '（未设置）'}</option>`).join('')}
                    </select></div>
                <div class="field"><label>输入单价 <span class="hint">（元/百万 tokens）</span></label>
                    <input id="mmPin" type="number" step="0.01" class="form-input" value="${pr.input ?? ''}"></div>
                <div class="field"><label>输出单价 <span class="hint">（元/百万 tokens）</span></label>
                    <input id="mmPout" type="number" step="0.01" class="form-input" value="${pr.output ?? ''}"></div>
                <div class="field"><label>上下文长度 <span class="hint">（tokens）</span></label>
                    <input id="mmCtx" type="number" class="form-input" value="${m.context_length ?? ''}"></div>
                <div class="field"><label>最大输出 tokens</label>
                    <input id="mmMax" type="number" class="form-input" value="${m.max_tokens ?? ''}"></div>
                <div class="field span-2"><label>备注</label>
                    <input id="mmNote" class="form-input" value="${escapeAttr(m.note || '')}"></div>
            </div>
            <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px;">
                <button class="btn btn-sm" onclick="window.closeModal()">取消</button>
                <button class="btn btn-sm btn-primary" onclick="window.rtSaveModel('${escapeAttr(modelId)}')">💾 保存</button>
            </div>`;
        modal.classList.add('active');
    }

    async function rtSaveModel(modelId) {
        const val = id => {
            const el = document.getElementById(id);
            return el ? el.value : '';
        };
        const payload = {
            category: val('mmCategory'),
            status: val('mmStatus'),
            input_price: val('mmPin'),
            output_price: val('mmPout'),
            context_length: val('mmCtx'),
            max_tokens: val('mmMax'),
            note: val('mmNote'),
        };
        const r = await apiPut('/router/models/' + encodeURIComponent(modelId), payload);
        if (r && r.success) {
            toast('✅ 已保存', 'success');
            closeModal();
            rtLoadModels();
        } else toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
    }

    // ---- Log Center：调用历史 ----
    function lcSwitch(which) {
        const isCalls = which === 'calls';
        document.getElementById('lcFilePane').style.display = isCalls ? 'none' : '';
        document.getElementById('lcCallsPane').style.display = isCalls ? '' : 'none';
        document.getElementById('lcTabFile').classList.toggle('active', !isCalls);
        document.getElementById('lcTabCalls').classList.toggle('active', isCalls);
        if (isCalls) lccLoad();
    }

    let lccPage = 1;
    let lccPageSize = 20;
    let lccTotal = 0;

    async function lccLoad() {
        const params = lcQueryParamsLcc();
        const data = await apiGet('/router/calls?' + params.toString());
        const listBox = document.getElementById('lccList');
        if (!listBox || !data) return;

        // 筛选下拉（只填一次，保留选择）
        const selM = document.getElementById('lccModel');
        const selP = document.getElementById('lccProvider');
        if (selM && !selM.dataset.filled && (data.models || []).length) {
            selM.innerHTML = '<option value="">全部模型</option>' +
                data.models.map(x => `<option value="${escapeAttr(x)}">${escapeHtml(x)}</option>`).join('');
            selM.dataset.filled = '1';
        }
        if (selP && !selP.dataset.filled && (data.providers || []).length) {
            selP.innerHTML = '<option value="">全部来源</option>' +
                data.providers.map(x => `<option value="${escapeAttr(x)}">${escapeHtml(x)}</option>`).join('');
            selP.dataset.filled = '1';
        }

        lccTotal = data.total || 0;
        const calls = data.calls || [];
        const pages = Math.max(1, Math.ceil(lccTotal / lccPageSize));
        if (lccPage > pages) lccPage = pages;
        const startIdx = (lccPage - 1) * lccPageSize;
        const rows = calls.slice(startIdx, startIdx + lccPageSize);

        const cnt = document.getElementById('lccCount');
        if (cnt) cnt.textContent = `${lccTotal} 条 · 第 ${lccPage}/${pages} 页`;

        listBox.innerHTML = rows.length ? `
            <table class="lcc-table">
                <thead><tr>
                    <th style="width:150px">时间</th>
                    <th>模型</th>
                    <th style="width:90px">来源</th>
                    <th style="width:70px">状态</th>
                    <th style="width:76px">E2E</th>
                    <th style="width:70px">TPS</th>
                    <th style="width:56px"></th>
                </tr></thead>
                <tbody>
                ${rows.map((c, i) => {
                    const m = c.metrics || {};
                    const t = c.ts ? new Date(c.ts * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
                    const ok = c.status === 'ok';
                    return `<tr class="lcc-row" onclick="window.lccToggle(${i})">
                        <td class="lcc-dim">${escapeHtml(t)}</td>
                        <td class="lcc-name">${escapeHtml(c.model || '')}</td>
                        <td class="lcc-dim">${escapeHtml(c.provider || 'local')}</td>
                        <td><span class="lcc-badge ${ok ? 'ok' : 'bad'}">${escapeHtml(c.status || '')}</span></td>
                        <td class="lcc-dim">${m.e2e_ms != null ? m.e2e_ms + 'ms' : '--'}</td>
                        <td class="lcc-dim">${m.tps != null ? m.tps : '--'}</td>
                        <td class="lcc-caret">▸</td>
                    </tr>
                    <tr class="lcc-detail" id="lcc-d-${i}" style="display:none;">
                        <td colspan="7">
                            <div class="lcc-panes">
                                <div class="lcc-pane"><b>输入</b><pre>${escapeHtml(c.input || '(空)')}</pre></div>
                                <div class="lcc-pane"><b>输出</b><pre>${escapeHtml(c.output || '(空)')}</pre></div>
                                <div class="lcc-pane"><b>参数</b><pre>${escapeHtml(JSON.stringify(c.params || {}, null, 1))}</pre></div>
                                <div class="lcc-pane"><b>性能</b><pre>${escapeHtml(JSON.stringify(m, null, 1))}</pre></div>
                            </div>
                        </td>
                    </tr>`;
                }).join('')}
                </tbody>
            </table>
            <div class="lcc-pager">
                <button class="btn btn-sm" onclick="window.lccGo(1)" ${lccPage <= 1 ? 'disabled' : ''}>« 首页</button>
                <button class="btn btn-sm" onclick="window.lccGo(${lccPage - 1})" ${lccPage <= 1 ? 'disabled' : ''}>‹ 上一页</button>
                <span class="hint">第 ${lccPage} / ${pages} 页（共 ${lccTotal} 条）</span>
                <button class="btn btn-sm" onclick="window.lccGo(${lccPage + 1})" ${lccPage >= pages ? 'disabled' : ''}>下一页 ›</button>
                <button class="btn btn-sm" onclick="window.lccGo(${pages})" ${lccPage >= pages ? 'disabled' : ''}>末页 »</button>
                <select class="form-select" style="max-width:110px;" onchange="window.lccSetSize(this.value)">
                    ${[20, 50, 100, 200].map(n =>
                        `<option value="${n}" ${n === lccPageSize ? 'selected' : ''}>${n} 条/页</option>`).join('')}
                </select>
            </div>`
            : '<p class="hint" style="font-size:12px">没有匹配的调用记录</p>';
    }

    function lcQueryParamsLcc() {
        const p = new URLSearchParams();
        const model = document.getElementById('lccModel')?.value || '';
        const provider = document.getElementById('lccProvider')?.value || '';
        const kw = (document.getElementById('lccKw')?.value || '').trim();
        const from = document.getElementById('lccFrom')?.value || '';
        const to = document.getElementById('lccTo')?.value || '';
        if (model) p.set('model', model);
        if (provider) p.set('source', provider);
        if (kw) p.set('q', kw);
        if (from) p.set('since', String(new Date(from).getTime() / 1000));
        if (to) p.set('until', String(new Date(to).getTime() / 1000));
        p.set('limit', '2000');
        return p;
    }

    function lccToggle(i) {
        const d = document.getElementById('lcc-d-' + i);
        if (!d) return;
        const open = d.style.display !== 'none';
        d.style.display = open ? 'none' : '';
        const row = d.previousElementSibling;
        if (row) row.querySelector('.lcc-caret').textContent = open ? '▸' : '▾';
    }

    function lccGo(page) {
        lccPage = Math.max(1, page);
        lccLoad();
    }

    function lccSetSize(n) {
        lccPageSize = parseInt(n, 10) || 20;
        lccPage = 1;
        lccLoad();
    }

    function lccResetFilters() {
        ['lccModel', 'lccProvider', 'lccKw', 'lccFrom', 'lccTo'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        lccPage = 1;
        lccLoad();
    }

    window.rtRefresh = rtRefresh;
    window.msSwitch = msSwitch;
    window.msOnEnter = msOnEnter;
    window.msToggleWizard = msToggleWizard;
    window.dashSetEngine = dashSetEngine;
    window.startModelWithVariant = startModelWithVariant;
    window.__doStart = __doStart;
    window.engineOptions = engineOptions;
    window.rtLoadProviders = rtLoadProviders;
    window.rtLoadModels = rtLoadModels;
    window.rtRenderModels = rtRenderModels;
    window.pvSave = pvSave;
    window.pvEdit = pvEdit;
    window.pvDelete = pvDelete;
    window.pvFetchModels = pvFetchModels;
    window.pvResetForm = pvResetForm;
    window.pvClearKey = pvClearKey;
    window.pvTypeChange = pvTypeChange;
    window.rtLoadKeys = rtLoadKeys;
    window.tkIssue = tkIssue;
    window.tkRevoke = tkRevoke;
    window.tkEdit = tkEdit;
    window.tkCopy = tkCopy;
    window.tkCancelEdit = tkCancelEdit;
    window.rtLoadUsage = rtLoadUsage;
    window.rtExport = rtExport;
    window.rtEditModel = rtEditModel;
    window.rtSaveModel = rtSaveModel;
    window.lcSwitch = lcSwitch;
    window.lccLoad = lccLoad;
    window.lccToggle = lccToggle;
    window.lccGo = lccGo;
    window.lccSetSize = lccSetSize;
    window.lccResetFilters = lccResetFilters;

    window.tplRefresh = tplRefresh;
    window.tplOpenDetail = tplOpenDetail;
    window.tplCloseDetail = tplCloseDetail;
    window.tplDeploy = tplDeploy;
    window.tplDownload = tplDownload;
    window.tplDelete = tplDelete;
    window.tplDuplicate = tplDuplicate;
    window.tplOpenSave = tplOpenSave;
    window.tplOpenEditor = tplOpenEditor;
    window.tplCloseEditor = tplCloseEditor;
    window.tplSaveEditor = tplSaveEditor;
    window.teAddEngine = teAddEngine;
    window.teAddSource = teAddSource;

    window.rmRefresh = rmRefresh;
    window.rmStop = rmStop;
    window.rmRestart = rmRestart;
    window.rmTerminate = rmTerminate;
    window.rmSaveTpl = rmSaveTpl;

    // v1.1 启动向导
    window.wizInit = wizInit;
    window.wizSet = wizSet;
    window.wizSetVersion = wizSetVersion;
    window.wizSetParam = wizSetParam;
    window.wizReloadParams = wizReloadParams;
    window.wizLaunch = wizLaunch;
    window.wizPreview = wizPreview;
    window.wizOpenFiles = wizOpenFiles;
    window.wizLoadFiles = wizLoadFiles;
    window.wizPickFile = wizPickFile;
    window.wizRefreshCatalog = wizRefreshCatalog;

    // v1.1 引擎管理
    window.zzGoto = function (tab) {
        const item = document.querySelector('.nav-item[data-tab="' + tab + '"]');
        if (item) item.click();
    };

    window.openHelpDoc = openHelpDoc;
    setupHelp();

    // ---- 基础工具导出（供拆分出的模块借用，#1 模块化）----
    window.AMM_BASE = {
        apiGet, apiPost, apiPut, apiDelete, toast, escapeHtml, escapeAttr,
        formatUptime, API_BASE,
    };
    window.apiGet = apiGet;
    window.apiPost = apiPost;
    window.apiPut = apiPut;
    window.apiDelete = apiDelete;
    window.toast = toast;
    window.escapeHtml = escapeHtml;
    window.escapeAttr = escapeAttr;
    window.dkGenerate = dkGenerate;
    window.dkCopy = dkCopy;
    window.dkClear = dkClear;
    window.pipPresetChange = pipPresetChange;
    window.savePipIndex = savePipIndex;
    window.lcSwitch = lcSwitch;

    // ---- Boot ----
    document.addEventListener('DOMContentLoaded', init);
})();
