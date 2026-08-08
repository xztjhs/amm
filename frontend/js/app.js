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
                if (tab === 'models') renderModelsDetail();
                if (tab === 'playground') renderPlayground();
                if (tab === 'gpu') renderGPU();
                if (tab === 'logs') startLogAutoRefresh();
                if (tab === 'settings') renderSettings();
                if (tab === 'tools') renderTools();
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
        document.getElementById('refreshLogs')?.addEventListener('click', refreshLogs);
    }

    // ---- Toast ----
    function toast(msg, type) {
        type = type || 'info';
        const container = document.getElementById('toastContainer');
        if (!container) return;
        const el = document.createElement('div');
        el.className = 'toast toast-' + type;
        el.textContent = msg;
        container.appendChild(el);
        setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3000);
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
        if (!tab || tab === 'dashboard') renderDashboard();
        if (tab === 'gpu') renderGPU();
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
        renderDashboardModels();
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

    function renderDashboardModels() {
        const container = document.getElementById('dashboardModels');
        if (!container) return;
        const ids = ['chat', 'embedding', 'reranker', 'ocr', 'asr', 'tts', 't2i', 't2v', 'i2v'];
        let html = '';
        ids.forEach(id => {
            const inst = instances[id];
            if (!inst) return;
            const sc = inst.status || 'stopped';
            const icon = categoryIcons[inst.category] || '🤖';
            const engine = inst.engine_type || '--';
            html += `
            <div class="model-card" onclick="window.openModelDetail('${id}')">
                <div class="model-card-header">
                    <span class="model-card-name">${icon} ${inst.name}</span>
                    <span class="model-card-badge badge-${inst.category}">${inst.category}</span>
                </div>
                <div class="model-card-status">
                    <span class="status-indicator ${sc}"></span>
                    <span style="font-size:12px;color:var(--text-muted)">${sc.toUpperCase()} · ${engine}</span>
                </div>
                <div class="model-card-info">
                    <span>Port</span><span>${inst.port || '--'}</span>
                    <span>GPU Mem</span><span>${inst.gpu_memory_mb ? inst.gpu_memory_mb.toFixed(0) + ' MB' : '--'}</span>
                    <span>CPU</span><span>${inst.cpu_percent ? inst.cpu_percent.toFixed(1) + '%' : '--'}</span>
                    <span>Uptime</span><span>${inst.uptime_seconds ? formatUptime(inst.uptime_seconds) : '--'}</span>
                </div>
                <div class="model-card-actions">
                    <button data-mid="${id}" data-action="start" data-label="Start" class="btn btn-sm btn-success" onclick="event.stopPropagation();window.startModel('${id}')" ${sc === 'running' ? 'disabled' : ''}>Start</button>
                    <button data-mid="${id}" data-action="stop" data-label="Stop" class="btn btn-sm btn-danger" onclick="event.stopPropagation();window.stopModel('${id}')" ${sc !== 'running' ? 'disabled' : ''}>Stop</button>
                    <button data-mid="${id}" data-action="restart" data-label="Restart" class="btn btn-sm" onclick="event.stopPropagation();window.restartModel('${id}')">Restart</button>
                </div>
            </div>`;
        });
        container.innerHTML = html;
    }

    // ---- Models Detail Tab (with Engine Selection) ----
    function renderModelsDetail() {
        if (!config) return;
        const container = document.getElementById('modelsDetail');
        if (!container) return;
        const keys = ['chat_model', 'embedding_model', 'asr_model', 'tts_model', 'reranker_model', 'ocr_model', 't2i_model', 't2v_model', 'i2v_model'];
        let html = '';
        keys.forEach(key => {
            const cfg = config[key];
            if (!cfg) return;
            const id = cfg.id;
            const inst = instances[id];
            const sc = inst ? inst.status : 'stopped';
            const icon = categoryIcons[cfg.category] || '🤖';
            const params = cfg.parameters || [];
            const models = cfg.available_models || [];
            const currentEngine = (inst && inst.engine_type) || cfg.engine_type || '';
            const availEngines = cfg.available_engines || [];

            // Engine selector
            let engineSelector = '';
            if (availEngines.length > 0) {
                engineSelector = '<div class="engine-selector">' +
                    '<span class="engine-selector-label">⚙️ 推理引擎:</span>' +
                    availEngines.map(eng => {
                        const sel = eng === currentEngine ? ' selected' : '';
                        return `<button class="engine-option${sel}" onclick="event.stopPropagation();window.selectEngine('${id}','${eng}')">${eng}${sel ? ' ✓' : ''}</button>`;
                    }).join('') +
                    '</div>';
            }

            // Filter params for current engine
            const relevantParams = params.filter(p => !p.engine || p.engine === currentEngine);

            html += `
            <div class="model-detail-card">
                <div class="model-detail-header" onclick="window.toggleDetailBody('body-${id}')">
                    <div class="model-detail-title">
                        <span style="font-size:20px">${icon}</span>
                        <div><h3>${cfg.name}</h3><span style="font-size:11px;color:var(--text-muted)">${cfg.description || ''}</span></div>
                    </div>
                    <div class="model-detail-controls">
                        <span class="status-indicator ${sc}"></span>
                        <span style="font-size:12px;color:var(--text-muted)">${currentEngine} :${cfg.port}</span>
                        <button data-mid="${id}" data-action="start" data-label="Start" class="btn btn-sm btn-success" onclick="event.stopPropagation();window.startModel('${id}')" ${sc === 'running' ? 'disabled' : ''}>Start</button>
                        <button data-mid="${id}" data-action="stop" data-label="Stop" class="btn btn-sm btn-danger" onclick="event.stopPropagation();window.stopModel('${id}')" ${sc !== 'running' ? 'disabled' : ''}>Stop</button>
                    </div>
                </div>
                <div class="model-detail-body" id="body-${id}">
                    ${engineSelector}
                    <div class="model-file-box" style="margin-bottom:14px;">
                        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
                            <label style="font-size:12px;color:var(--text-muted);font-weight:500">📁 模型文件 (路径浏览)</label>
                            <button class="btn btn-sm" onclick="window.openFileBrowser('${id}')">📂 浏览 /models</button>
                        </div>
                        <div class="model-file-current" style="font-size:12px;font-family:monospace;padding:8px 10px;background:var(--bg-input);border:1px solid var(--border);border-radius:6px;word-break:break-all;" id="mfile-cur-${id}">${inst && inst.selected_model_file ? escapeHtml(inst.selected_model_file) : '(未选择)'}</div>
                    </div>
                    <div id="preset-${id}"></div>
                    <h4 style="font-size:14px;margin-bottom:10px;">Parameters (${currentEngine})</h4>
                    <div class="params-grid">
                        ${relevantParams.map(p => renderParamInput(id, p, inst)).join('')}
                    </div>
                    <div style="margin-top:12px;display:flex;gap:8px;">
                        <button class="btn btn-primary btn-sm" onclick="saveParameters('${id}')">Save Parameters</button>
                        <button class="btn btn-sm" onclick="viewLogs('${id}')">View Logs</button>
                    </div>
                    ${['chat_model','embedding_model'].includes(key) ? renderStartupCard(id, key) : ''}
                    ${currentEngine === 'diffusers' ? renderAdvancedSettings(id) : ''}
                </div>
            </div>`;
        });
        container.innerHTML = html;
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
        return `<div class="param-item"><label>${param.label}${param.description ? '<span class="param-desc">' + param.description + '</span>' : ''}</label>${input}</div>`;
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

    // ---- Diffusers Advanced Settings (FP8 quant / CPU offload / boundary) ----
    // 这些字段不放在 instance.parameters 里, 走 /api/instances/{id}/advanced 写 yaml
    async function renderAdvancedSettings(modelId) {
        // 默认值 (从 yaml 读取, 失败则用兜底)
        const defaults = {
            quant: '',          // 空 / fp8 / bf16 / none
            compute_dtype: 'bf16',
            boundary_ratio: '', // 空 = 自动 (Wan2.2 -> 0.875)
            cpu_offload: false,
        };
        try {
            const r = await apiGet('/instances/' + modelId + '/advanced');
            if (r && r.settings) {
                Object.assign(defaults, {
                    quant: r.settings.quant || '',
                    compute_dtype: r.settings.compute_dtype || 'bf16',
                    boundary_ratio: (r.settings.boundary_ratio === null || r.settings.boundary_ratio === undefined) ? '' : r.settings.boundary_ratio,
                    cpu_offload: !!r.settings.cpu_offload,
                });
            }
        } catch (e) {
            console.warn('renderAdvancedSettings load failed', e);
        }
        const id = `adv-${modelId}`;
        return `
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
                </div>
                <div style="margin-top:12px;display:flex;gap:8px;">
                    <button class="btn btn-primary btn-sm" onclick="saveAdvancedSettings('${modelId}')">Save Advanced</button>
                    <button class="btn btn-sm" onclick="restartModel('${modelId}')">🔄 Restart Now</button>
                </div>
            </div>`;
    }

    async function saveAdvancedSettings(modelId) {
        const id = `adv-${modelId}`;
        const settings = {
            quant: document.getElementById(`${id}-quant`)?.value || '',
            compute_dtype: document.getElementById(`${id}-compute_dtype`)?.value || '',
            boundary_ratio: document.getElementById(`${id}-boundary_ratio`)?.value || '',
            cpu_offload: document.getElementById(`${id}-cpu_offload`)?.checked || false,
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
            renderModelsDetail();
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
        document.getElementById('logModelSelect').value = modelId;
        refreshLogs();
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

    // ---- Logs ----
    function populateLogModelSelect() {
        const sel = document.getElementById('logModelSelect');
        if (!sel) return;
        const ids = ['chat', 'embedding', 'asr', 'tts', 'reranker', 'ocr', 't2i', 't2v', 'i2v'];
        sel.innerHTML = '<option value="">-- 选择模型 --</option>' + ids.map(id => `<option value="${id}">${id.toUpperCase()} - ${categoryNames[instances[id]?.category] || id}</option>`).join('');
        sel.addEventListener('change', refreshLogs);
    }

    async function refreshLogs() {
        const modelId = document.getElementById('logModelSelect')?.value;
        const lines = document.getElementById('logLines')?.value || 100;
        const viewer = document.getElementById('logViewer');
        if (!viewer) return;
        if (!modelId) { viewer.textContent = 'Select a model to view logs...'; return; }
        const data = await apiGet('/instances/' + modelId + '/logs?lines=' + lines);
        viewer.textContent = (data && data.logs) ? (data.logs.join('\n') || '(empty)') : 'Failed to load logs.';
        viewer.scrollTop = viewer.scrollHeight;
    }

    function startLogAutoRefresh() {
        if (document.getElementById('autoRefreshLogs')?.checked) {
            refreshLogs();
            if (logRefreshTimer) clearInterval(logRefreshTimer);
            logRefreshTimer = setInterval(() => {
                if (document.getElementById('autoRefreshLogs')?.checked) refreshLogs();
            }, 3000);
        }
    }

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
        }
        await loadEngines();
        renderEngineManagement();
    }

    // ---- Tools (模型下载 / GGUF 量化 / vLLM→GGUF 转换) ----
    async function renderTools() {
        try { await loadProxySettings(); } catch (e) { console.error('loadProxySettings', e); }
        try { await refreshDownloadStatus(); } catch (e) { console.error('refreshDownloadStatus', e); }
        try { await refreshQuantize(); } catch (e) { console.error('refreshQuantize', e); }
        try { await refreshVgTask(); } catch (e) { console.error('refreshVgTask', e); }
    }

    function renderEngineManagement() {
        const container = document.getElementById('engineList');
        if (!container) return;
        if (!engineInfo) { container.innerHTML = '<p style="color:var(--text-muted)">Loading engines...</p>'; return; }

        const engineMeta = {
            llama_cpp: { icon: '🦙', name: 'llama.cpp (GGUF)', desc: '高性能 GGUF 量化模型推理，支持 CPU/GPU 混合' },
            vllm: { icon: '⚡', name: 'vLLM', desc: 'PagedAttention / continuous batching，高并发 LLM 推理' },
            diffusers: { icon: '🎨', name: 'Diffusers', desc: 'HuggingFace 扩散模型，文生图 / 视频生成' },
        };

        let html = '';
        Object.keys(engineMeta).forEach(engType => {
            const meta = engineMeta[engType];
            const engCfg = engineInfo[engType] || {};
            const versions = (engineVersions && engineVersions[engType]) || [];
            const cats = engCfg.supported_categories || [];

            html += '<div class="engine-item" style="flex-direction:column;align-items:stretch;">' +
                '<div style="display:flex;justify-content:space-between;align-items:flex-start;">' +
                `<div><div class="engine-item-name">${meta.icon} ${meta.name}</div>` +
                `<div class="engine-item-desc">${meta.desc || engCfg.description || ''}</div>` +
                `<div class="engine-item-cats">${cats.map(c => `<span class="engine-cat-tag">${c}</span>`).join('')}</div></div>` +
                `<div style="display:flex;gap:6px;">` +
                versions.map(v => `<button class="btn btn-sm ${v.status === 'installed' ? 'btn-danger' : 'btn-success'}" onclick="window.toggleEngineInstall('${engType}','${v.version}','${v.status}')">${v.version} ${v.status === 'installed' ? '(卸载)' : '(安装)'}</button>`).join('') +
                '</div></div>' +
                '<div style="margin-top:8px;font-size:11px;color:var(--text-muted)">' +
                versions.map(v => `<span style="margin-right:12px;">v${v.version}: <span class="version-status ${v.status || 'available'}">${v.status || 'available'}</span></span>`).join('') +
                '</div></div>';
        });
        container.innerHTML = html;
    }

    async function toggleEngineInstall(engineType, version, currentStatus) {
        if (currentStatus === 'installed') {
            if (!confirm('确认卸载 ' + engineType + ' ' + version + '？')) return;
            toast('Uninstalling ' + engineType + ' ' + version + '...');
            const r = await apiPost('/engines/uninstall', { engine_type: engineType, version: version });
            toast(r && r.ok ? ('✅ Uninstalled') : ('❌ ' + ((r && r.message) || 'Failed')), r && r.ok ? 'success' : 'error');
        } else {
            toast('Installing ' + engineType + ' ' + version + '... (may take several minutes)');
            const r = await apiPost('/engines/install', { engine_type: engineType, version: version });
            toast(r && r.status === 'done' ? ('✅ ' + r.message) : (r && r.status === 'failed' ? ('❌ ' + r.message) : '⏳ ' + (r && r.message)), r && r.status === 'done' ? 'success' : 'info');
        }
        await loadEngines();
        renderEngineManagement();
    }

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
                <span style="margin-left:16px;color:var(--text-muted)">Model: ${inst.selected_model_file}</span>
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
            <div style="margin-top:16px;"><h4 style="font-size:13px;margin-bottom:8px;">Parameters</h4><pre style="background:var(--bg-input);padding:12px;border-radius:6px;font-size:12px;color:var(--text-secondary);overflow-x:auto;">${JSON.stringify(inst.parameters, null, 2)}</pre></div>`;
        modal.classList.add('active');
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
            };
        });
        // 填充 Playground 模型选择 (chat + vision)
        await populatePlaygroundModels();
        // 显示本地会话历史
        renderChatHistory();
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
        if (sel.options.length === 1) sel.innerHTML += '<option value="">(无可用模型，请先在 Models 启动)</option>';
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
            container.innerHTML += '<div class="pg-chat-bot"><strong>Bot:</strong> <span style="color:var(--error)">模型未运行, 请先在 Models 页启动。</span></div>';
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

    async function runEmbedding() {
        const inp = document.getElementById('pgEmbInput').value.trim();
        const texts = inp.split('|').map(s=>s.trim()).filter(Boolean);
        const perfEl = document.getElementById('pgEmbPerf'), resEl = document.getElementById('pgEmbResult');
        const inst = instances.embedding;
        if (!inst || inst.status !== 'running') return showNotRun(perfEl, resEl, 'embedding');
        perfEl.innerHTML = '<div class="pg-placeholder">计算中...</div>';
        const t0 = performance.now();
        let t1;
        try {
            const r = await fetch('/v1/embeddings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model:'embedding', input: texts.length===1?texts[0]:texts})});
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
        perfEl.innerHTML = '<div class="pg-placeholder">识别中...</div>';
        const fd = new FormData();
        fd.append('file', fi.files[0]);
        fd.append('model','asr');
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
        perfEl.innerHTML = '<div class="pg-placeholder">生成中...</div>';
        const t0 = performance.now();
        try {
            const r = await fetch('/v1/audio/speech', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model:'tts', input:text})});
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
        perfEl.innerHTML = '<div class="pg-placeholder">重排序中...</div>';
        const t0 = performance.now(); let t1;
        try {
            const r = await fetch('/v1/rerank', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({query, documents: docs})});
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
        const t0 = performance.now();
        try{
            const r = await fetch('/v1/ocr', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model:'ocr', image:b64})});
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
        if(resEl) resEl.innerHTML = '<div class="pg-placeholder" style="color:var(--warning)">模型 ' + name + ' 未运行, 请先在 Models 页启动。</div>';
        if(perfEl) perfEl.innerHTML = '';
    }

    async function generateImage() {
        const prompt = document.getElementById('pgT2IPrompt').value;
        const width = parseInt(document.getElementById('pgT2IWidth').value);
        const height = parseInt(document.getElementById('pgT2IHeight').value);
        const steps = parseInt(document.getElementById('pgT2ISteps').value);
        const guidance = parseFloat(document.getElementById('pgT2IGuidance').value);
        const result = document.getElementById('pgT2IResult');
        result.innerHTML = '<div class="pg-placeholder">Generating... please wait</div>';
        try {
            const resp = await fetch('/v1/images/generations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: prompt, width: width, height: height, num_inference_steps: steps, guidance_scale: guidance, n: 1 }),
            });
            const data = await resp.json();
            if (data.data && data.data[0] && data.data[0].b64_json) {
                result.innerHTML = '<img src="data:image/png;base64,' + data.data[0].b64_json + '" style="max-width:100%;border-radius:8px;">';
            } else {
                result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">' + escapeHtml(JSON.stringify(data)) + '</div>';
            }
        } catch (e) {
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
        result.innerHTML = '<div class="pg-placeholder">生成中... 5s 480P 预计 5-15 分钟（依赖 cpu_offload/quant 设置）</div>';
        hint.textContent = '已发送, 等待推理...';
        const t0 = Date.now();
        try {
            const resp = await fetch('/v1/videos/generations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
            if (data.data && data.data[0] && data.data[0].b64_json) {
                const mime = data.data[0].mime || 'video/mp4';
                const src = `data:${mime};base64,${data.data[0].b64_json}`;
                let extra = '';
                if (data.saved_paths && data.saved_paths.length) {
                    extra = `<div style="margin-top:8px;color:var(--success);font-size:12px;">💾 已落盘: ${escapeHtml(data.saved_paths[0])}</div>`;
                }
                result.innerHTML = `<video controls autoplay loop muted style="max-width:100%;border-radius:8px;" src="${src}"></video><div style="margin-top:8px;color:var(--text-muted);font-size:12px;">耗时 ${elapsed}s · ${(data.data[0].b64_json.length * 3 / 4 / 1024 / 1024).toFixed(1)} MB</div>${extra}`;
                hint.textContent = '✅ 完成';
            } else {
                result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">' + escapeHtml(JSON.stringify(data)) + '</div>';
                hint.textContent = '❌ 失败';
            }
        } catch (e) {
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
        result.innerHTML = '<div class="pg-placeholder">生成中... 5s 480P 预计 5-15 分钟</div>';
        const t0 = Date.now();
        try {
            const resp = await fetch('/v1/videos/generations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
            if (data.data && data.data[0] && data.data[0].b64_json) {
                const mime = data.data[0].mime || 'video/mp4';
                const src = `data:${mime};base64,${data.data[0].b64_json}`;
                let extra = '';
                if (data.saved_paths && data.saved_paths.length) {
                    extra = `<div style="margin-top:8px;color:var(--success);font-size:12px;">💾 已落盘: ${escapeHtml(data.saved_paths[0])}</div>`;
                }
                result.innerHTML = `<video controls autoplay loop muted style="max-width:100%;border-radius:8px;" src="${src}"></video><div style="margin-top:8px;color:var(--text-muted);font-size:12px;">耗时 ${elapsed}s</div>${extra}`;
            } else {
                result.innerHTML = '<div class="pg-placeholder" style="color:var(--error)">' + escapeHtml(JSON.stringify(data)) + '</div>';
            }
        } catch (e) {
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
        renderModels();
        closeFileBrowser();
    }

    async function fbPickDir(modelId) {
        const dir = fbState.curDir;
        await selectModelFile(modelId, dir);
        await fbCheckPreset(modelId, dir);
        renderModels();
        closeFileBrowser();
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
            renderModels();
        } else {
            toast('❌ ' + ((r && r.error) || '应用失败'), 'error');
        }
    }

    // 渲染 Models 页 (别名, 保持与现有呼吸一致)
    async function renderModels() { renderModelsDetail(); }

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
        const cbs = document.querySelectorAll('.dl-file-cb');
        let files = [];
        if (cbs.length) files = Array.from(cbs).filter(c => c.checked).map(c => c.value);
        const total = (window.__dlSelectedRev || {}).total_size || 0;
        const total_files = (window.__dlSelectedRev && (window.__dlSelectedRev.files||[]).length) || 0;
        toast('⬇️ 开始下载 ' + modelId + (rev?' ('+rev+')':'') + ' ...');
        const r = await apiPost('/models/download', {
            model_id: modelId, source: source, category: category,
            revision: rev, files: files, total: total, total_files: total_files,
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
    window.toggleEngineInstall = toggleEngineInstall;
    window.openModelDetail = openModelDetail;
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
        } else if (qState.mode==='out' || qState.mode==='vout') {
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
        } else {
            document.getElementById('quOut').value = d;
            closeFileBrowser();
            toast('输出目录: /models/' + d, 'info');
        }
    }
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

    // ---- Boot ----
    document.addEventListener('DOMContentLoaded', init);
})();
