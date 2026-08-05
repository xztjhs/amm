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

    function startAutoRefresh() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = setInterval(refreshAll, REFRESH_INTERVAL);
    }

    // ---- Dashboard ----
    function renderDashboard() {
        renderSystemCards();
        renderDashboardModels();
    }

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
        const ids = ['chat', 'embedding', 'asr', 'tts', 'reranker', 'ocr', 't2i', 't2v', 'i2v'];
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
                    <button class="btn btn-sm btn-success" onclick="event.stopPropagation();window.startModel('${id}')" ${sc === 'running' ? 'disabled' : ''}>Start</button>
                    <button class="btn btn-sm btn-danger" onclick="event.stopPropagation();window.stopModel('${id}')" ${sc !== 'running' ? 'disabled' : ''}>Stop</button>
                    <button class="btn btn-sm" onclick="event.stopPropagation();window.restartModel('${id}')">Restart</button>
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
                        <button class="btn btn-sm btn-success" onclick="event.stopPropagation();window.startModel('${id}')" ${sc === 'running' ? 'disabled' : ''}>Start</button>
                        <button class="btn btn-sm btn-danger" onclick="event.stopPropagation();window.stopModel('${id}')" ${sc !== 'running' ? 'disabled' : ''}>Stop</button>
                    </div>
                </div>
                <div class="model-detail-body" id="body-${id}">
                    ${engineSelector}
                    <div style="margin-bottom:14px;">
                        <label style="font-size:12px;color:var(--text-muted);font-weight:500">Model File</label>
                        <select class="form-select" style="width:100%" onchange="selectModelFile('${id}', this.value)">
                            ${models.map(m => `<option value="${m.file || m.model_id || ''}" ${(inst && inst.selected_model_file === (m.file || m.model_id)) ? 'selected' : ''}>${m.name} (${m.type || m.source || ''})</option>`).join('')}
                        </select>
                    </div>
                    <h4 style="font-size:14px;margin-bottom:10px;">Parameters (${currentEngine})</h4>
                    <div class="params-grid">
                        ${relevantParams.map(p => renderParamInput(id, p, inst)).join('')}
                    </div>
                    <div style="margin-top:12px;display:flex;gap:8px;">
                        <button class="btn btn-primary btn-sm" onclick="saveParameters('${id}')">Save Parameters</button>
                        <button class="btn btn-sm" onclick="viewLogs('${id}')">View Logs</button>
                    </div>
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
        document.getElementById(id)?.classList.toggle('expanded');
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
        toast('Starting ' + modelId + '...');
        const r = await apiPost('/instances/' + modelId + '/start');
        if (r) toast(r.error ? ('❌ ' + r.error) : ('✅ ' + modelId + ' started (' + (r.engine || '') + ')'), r.error ? 'error' : 'success');
        await refreshAll();
    }

    async function stopModel(modelId) {
        toast('Stopping ' + modelId + '...');
        const r = await apiPost('/instances/' + modelId + '/stop');
        if (r) toast(r.error ? ('❌ ' + r.error) : ('⏹ ' + modelId + ' stopped'), r.error ? 'error' : 'success');
        await refreshAll();
    }

    async function restartModel(modelId) {
        toast('Restarting ' + modelId + '...');
        const r = await apiPost('/instances/' + modelId + '/restart');
        if (r) toast(r.error ? ('❌ ' + r.error) : ('🔄 ' + modelId + ' restarted'), r.error ? 'error' : 'success');
        await refreshAll();
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
        }
        await loadEngines();
        renderEngineManagement();
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

    function setupPlaygroundTabs() {
        document.querySelectorAll('.pg-tab').forEach(tab => {
            tab.onclick = function() {
                document.querySelectorAll('.pg-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                document.querySelectorAll('.pg-content').forEach(c => c.classList.remove('active'));
                document.getElementById('pg-' + tab.dataset.pg).classList.add('active');
            };
        });
    }

    async function sendChatMessage() {
        const input = document.getElementById('pgChatInput');
        const msg = input.value.trim();
        if (!msg) return;
        const container = document.getElementById('pgChatMessages');
        container.innerHTML += '<div class="pg-chat-user"><strong>You:</strong> ' + escapeHtml(msg) + '</div>';
        input.value = '';

        const chatInst = instances.chat;
        if (!chatInst || chatInst.status !== 'running') {
            container.innerHTML += '<div class="pg-chat-bot"><strong>Bot:</strong> <span style="color:var(--error)">Chat model is not running. Please start it first.</span></div>';
            return;
        }

        container.innerHTML += '<div class="pg-chat-bot" id="pgChatLoading"><strong>Bot:</strong> <span style="color:var(--text-muted)">Thinking...</span></div>';
        container.scrollTop = container.scrollHeight;

        try {
            const resp = await fetch('/v1/chat/completions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: 'chat', messages: [{ role: 'user', content: msg }], max_tokens: 512 }),
            });
            const data = await resp.json();
            document.getElementById('pgChatLoading').remove();
            const reply = (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) || JSON.stringify(data);
            container.innerHTML += '<div class="pg-chat-bot"><strong>Bot:</strong> ' + escapeHtml(reply) + '</div>';
        } catch (e) {
            document.getElementById('pgChatLoading').remove();
            container.innerHTML += '<div class="pg-chat-bot"><strong>Bot:</strong> <span style="color:var(--error)">Error: ' + escapeHtml(e.message) + '</span></div>';
        }
        container.scrollTop = container.scrollHeight;
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

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(text));
        return div.innerHTML;
    }

    // ---- Global Exports ----
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
    window.callApi = callApi;

    // ---- Boot ----
    document.addEventListener('DOMContentLoaded', init);
})();
