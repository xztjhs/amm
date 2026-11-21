/**
 * AMM Frontend — tools 模块（从 app.js 拆出）
 * 依赖 core 提供的全局符号，统一从 window 取，避免隐式依赖。
 */
(function () {
    'use strict';
    const API_BASE = '/api';

    // 本模块自持的引擎目录缓存（原为 core 的全局变量）
    let engineCatalog = null;
    let engineInfo = null;

    // 借用 core 的基础工具（#1 拆分）
    const apiGet = (...a) => window.apiGet(...a);
    const apiPost = (...a) => window.apiPost(...a);
    const toast = (...a) => window.toast(...a);
    const escapeHtml = (...a) => window.escapeHtml(...a);
    const escapeAttr = (...a) => window.escapeAttr(...a);

    // 从 core 借用的辅助（core 都挂在 window 上）
    const hfLoadSettings = (...a) => window.hfLoadSettings(...a);
    const loadProxySettings = (...a) => window.loadProxySettings(...a);
    const refreshDownloadStatus = (...a) => window.refreshDownloadStatus(...a);
    const refreshQuantize = (...a) => window.refreshQuantize(...a);
    const refreshVgTask = (...a) => window.refreshVgTask(...a);

    // ---- Tools (模型下载 / GGUF 量化 / vLLM→GGUF 转换) ----
    async function renderTools() {
        try { await loadProxySettings(); } catch (e) { console.error('loadProxySettings', e); }
        try { await hfLoadSettings(); } catch (e) { console.error('hfLoadSettings', e); }
        try { await refreshDownloadStatus(); } catch (e) { console.error('refreshDownloadStatus', e); }
        try { await refreshQuantize(); } catch (e) { console.error('refreshQuantize', e); }
        try { await refreshVgTask(); } catch (e) { console.error('refreshVgTask', e); }
    }

    // ================================================================
    // v1.1 Settings · 推理引擎管理（目录式：上游版本 + 下载/安装/卸载）
    // ================================================================
    const ENG_ICONS = {
        llama_cpp: '🦙', vllm: '⚡', diffusers: '🎨', sentence_transformers: '🧬',
    };
    const emExpanded = {};   // engType -> bool（展开全部上游版本）
    const emShowAll = {};    // engType -> bool（展开前 5 个可安装版本）

    function emToggleMore(engType) {
        emExpanded[engType] = !emExpanded[engType];
        renderEngineManagement();
    }

    function emToggleMore(engType) {
        if (emExpanded[engType]) return;
        if (emShowAll[engType]) emExpanded[engType] = true;
        else emShowAll[engType] = true;
        renderEngineManagement();
    }

    function emCollapse(engType) {
        emExpanded[engType] = false;
        emShowAll[engType] = false;
        renderEngineManagement();
    }

    async function emRefreshCatalog(force) {
        const el = document.getElementById('engineTasks');
        if (el && force) el.innerHTML = '<div style="font-size:11.5px;color:var(--text-muted)">查询上游版本…</div>';
        const data = await apiGet('/engines/catalog' + (force ? '?refresh=1' : ''));
        if (!data || !data.engines) {
            toast('引擎目录获取失败', 'error');
            return;
        }
        engineCatalog = { engines: data.engines, engines_root: data.engines_root };
        // 兼容旧代码：engineInfo 结构
        engineInfo = {};
        Object.keys(data.engines).forEach(t => {
            engineInfo[t] = {
                display_name: data.engines[t].display_name,
                supported_categories: data.engines[t].supports,
                description: data.engines[t].note || '',
            };
        });
        renderEngineManagement();
        const el2 = document.getElementById('emUpdatedAt');
        if (el2) {
            const anyErr = Object.values(data.engines).some(e => e.upstream_error);
            el2.textContent = anyErr
                ? '⚠️ 部分上游查询失败，已显示缓存/内置清单'
                : '版本清单已更新（缓存 30 分钟）';
        }
        if (force) toast('上游版本已刷新');
    }

    function renderEngineManagement() {
        const container = document.getElementById('engineList');
        if (!container) return;
        if (!engineCatalog || !engineCatalog.engines) {
            container.innerHTML = '<p style="color:var(--text-muted)">加载引擎目录…</p>';
            emRefreshCatalog(false);
            return;
        }

        const html = Object.keys(engineCatalog.engines).map(engType => {
            const e = engineCatalog.engines[engType];
            const icon = ENG_ICONS[engType] || '⚙️';
            const installed = e.installed || [];
            const instKey = {};
            installed.forEach(v => { instKey[v.version + '|' + (v.flavor || '')] = v; });

            // 默认只渲染「已安装」的版本 —— 上游动辄几十个版本，
            // 全量渲染会让设置页卡住。点「展开全部版本」才加载其余。
            const PAGE = 5;
            const allUp = e.upstream || [];
            const sorted = allUp.slice().sort((a, b) => {
                const ai = (a.variants || []).some(v => instKey[a.version + '|' + v.flavor]) ? 0 : 1;
                const bi = (b.variants || []).some(v => instKey[b.version + '|' + v.flavor]) ? 0 : 1;
                return ai - bi;
            });

            const installedRows = sorted.filter(u =>
                (u.variants || []).some(v => {
                    const it = instKey[u.version + '|' + v.flavor];
                    return it && it.status === 'installed';
                })
            );
            const restRows = sorted.filter(u => !installedRows.includes(u));

            let visible, hiddenCount;
            if (emExpanded[engType]) {
                visible = sorted;
                hiddenCount = 0;
            } else if (emShowAll[engType]) {
                visible = [...installedRows, ...restRows.slice(0, PAGE)];
                hiddenCount = Math.max(0, restRows.length - PAGE);
            } else {
                // 折叠态：只显示已安装的；一个都没装时给前几个候选
                visible = installedRows.length ? installedRows : restRows.slice(0, 3);
                hiddenCount = restRows.length - (installedRows.length ? 0 : Math.min(3, restRows.length));
            }

            const rows = visible.map(u => {
                const cells = (u.variants || []).map(v => {
                    const key = u.version + '|' + v.flavor;
                    const inst = instKey[key];
                    if (inst && inst.status === "installed") {
                        return `<div class="eng-var">
                            <span class="eng-var-flavor">${escapeHtml(v.flavor || 'default')}</span>
                            <span class="eng-pill ok">已安装${inst.size_mb ? ' · ' + inst.size_mb + 'MB' : ''}</span>
                            <button class="btn btn-sm btn-danger" onclick="window.emTask('${engType}','${escapeAttr(u.version)}','${escapeAttr(v.flavor)}','uninstall')">卸载</button>
                        </div>`;
                    }
                    if (inst) {
                        // 目录存在但二进制缺失（例如只点了「下载」、编译没跑完）
                        return `<div class="eng-var">
                            <span class="eng-var-flavor">${escapeHtml(v.flavor || 'default')}</span>
                            <span class="eng-pill busy">未完成编译${inst.size_mb ? ' · ' + inst.size_mb + 'MB' : ''}</span>
                            <button class="btn btn-sm btn-success" onclick="window.emTask('${engType}','${escapeAttr(u.version)}','${escapeAttr(v.flavor)}','install')">继续安装</button>
                            <button class="btn btn-sm btn-danger" onclick="window.emTask('${engType}','${escapeAttr(u.version)}','${escapeAttr(v.flavor)}','uninstall')">清理</button>
                        </div>`;
                    }
                    return `<div class="eng-var">
                        <span class="eng-var-flavor">${escapeHtml(v.flavor || 'default')}</span>
                        <span class="eng-pill no">未安装</span>
                        <button class="btn btn-sm" onclick="window.emTask('${engType}','${escapeAttr(u.version)}','${escapeAttr(v.flavor)}','download')">下载</button>
                        <button class="btn btn-sm btn-success" onclick="window.emTask('${engType}','${escapeAttr(u.version)}','${escapeAttr(v.flavor)}','install')">安装</button>
                    </div>`;
                }).join('');
                return `<tr>
                    <td class="ver">${escapeHtml(u.version)}
                        ${u.local_only ? '<span class="eng-pill">本地</span>'
                                       : (u.prerelease ? '<span class="eng-pill bad">pre</span>' : '')}</td>
                    <td style="font-size:11px;color:var(--text-muted);white-space:nowrap">${escapeHtml((u.published_at || '').slice(0, 10) || '--')}</td>
                    <td><div class="eng-vars">${cells}</div></td>
                </tr>`;
            }).join('');

            const installedList = installed.length
                ? installed.map(v => `<span class="eng-pill ok" title="${escapeAttr(v.install_path || '')}">${escapeHtml(v.version)}${v.flavor ? ' · ' + escapeHtml(v.flavor) : ''}${v.status === 'incomplete' ? ' ⚠️' : ''}</span>`).join('')
                : '<span class="eng-pill no">未安装任何版本</span>';

            return `<div class="engine-card">
                <div class="engine-card-head">
                    <div style="min-width:0">
                        <div class="engine-card-title">${icon} ${escapeHtml(e.display_name || engType)}
                            ${e.flavors.length > 1 ? '<span class="eng-pill">cpu / cuda</span>' : '<span class="eng-pill">cuda only</span>'}
                        </div>
                        <div class="engine-card-desc">${escapeHtml(e.note || '')}</div>
                        <div class="engine-meta-line">
                            <span>来源 <b>${escapeHtml(e.upstream_source === 'github' ? 'GitHub Releases' : 'PyPI')}</b></span>
                            <span>安装方式 <b>${e.kind === 'source-build' ? '源码编译' : 'pip venv'}</b></span>
                            <span>支持 <b>${escapeHtml((e.supports || []).join(' / '))}</b></span>
                            <span><a href="${escapeAttr(e.homepage || '#')}" target="_blank" rel="noopener">上游页面 ↗</a></span>
                        </div>
                        <div class="engine-meta-line">已装 ${installedList}</div>
                    </div>
                </div>
                <div class="engine-ver-wrap">
                    <table class="engine-ver-table">
                        <thead><tr><th style="width:150px">版本</th><th style="width:110px">发布</th><th>Flavor / 操作</th></tr></thead>
                        <tbody>${rows || '<tr><td colspan="3" class="rm-empty">上游清单为空（检查网络后点「刷新上游版本」）</td></tr>'}</tbody>
                    </table>
                </div>
                ${hiddenCount > 0 ? `<button class="btn btn-sm eng-more" onclick="window.emToggleMore('${engType}')">展开剩余 ${hiddenCount} 个可安装版本 ↓</button>` : ''}
                ${emExpanded[engType] ? `<button class="btn btn-sm eng-more" onclick="window.emCollapse('${engType}')">收起（只看已安装）↑</button>` : ''}
                ${emExpanded[engType] && sorted.length > PAGE ? `<button class="btn btn-sm eng-more" onclick="window.emToggleMore('${engType}')">收起 ↑</button>` : ''}
            </div>`;
        }).join('');

        container.innerHTML = html;
        emRefreshTasks();
    }

    async function emTask(engineType, version, flavor, action) {
        if (action === 'uninstall' && !confirm(`确定卸载 ${engineType} ${version} (${flavor})？\n将删除安装目录，正在运行的实例会失效。`)) return;
        const r = await apiPost('/engines/tasks', {
            engine_type: engineType, version, flavor, action,
        });
        if (!r) { toast('请求失败', 'error'); return; }
        if (r.error) { toast('失败: ' + r.error, 'error'); return; }
        toast(`已提交${action === 'uninstall' ? '卸载' : action === 'download' ? '下载' : '安装'}任务: ${version} (${flavor})`, 'success');
        openEngineTask(r.task && r.task.task_id);
        emRefreshTasks();
        if (!engineTaskTimer) engineTaskTimer = setInterval(emRefreshTasks, 2500);
    }

    async function emRefreshTasks() {
        const box = document.getElementById('engineTasks');
        if (!box) return;
        const data = await apiGet('/engines/tasks');
        const tasks = (data && data.tasks) || [];
        if (!tasks.length) { box.innerHTML = ''; return; }
        const active = tasks.filter(t => t.status === 'running' || t.status === 'pending');
        box.innerHTML = `<div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text-secondary)">
                任务队列（${active.length} 运行中 / 共 ${tasks.length}）</div>` +
            tasks.slice(0, 8).map(t => {
                const pct = Math.round((t.progress || 0) * 100);
                const cls = t.status === 'done' ? 'ok' : t.status === 'failed' ? 'bad' : 'busy';
                return `<div class="task-row">
                    <span class="tname" title="${escapeAttr(t.message || '')}">${escapeHtml(t.action)} · ${escapeHtml(t.engine_type)} ${escapeHtml(t.version)}${t.flavor ? '/' + escapeHtml(t.flavor) : ''}</span>
                    <span><span class="eng-pill ${cls}">${escapeHtml(t.status)}</span></span>
                    <span class="task-bar"><i style="width:${pct}%"></i></span>
                    <span style="font-size:11px;color:var(--text-muted)">${pct}%</span>
                    <span style="text-align:right"><button class="btn btn-sm" onclick="window.emOpenTask('${escapeAttr(t.task_id)}')">日志</button></span>
                </div>`;
            }).join('');
        // 完成后刷新目录
        if (!active.length) {
            if (engineTaskTimer) { clearInterval(engineTaskTimer); engineTaskTimer = null; }
            if (window.__emHadActive) {
                window.__emHadActive = false;
                emRefreshCatalog(false);
            }
        } else {
            window.__emHadActive = true;
        }
    }

    function actionLabel(a) {
        return { install: '安装', download: '下载', uninstall: '卸载' }[a] || a || '';
    }
    function statusLabel(s) {
        return { pending: '排队中', running: '进行中', done: '已完成', failed: '失败' }[s] || s || '';
    }

    function openEngineTask(taskId) {
        const box = document.getElementById('engineTaskModal');
        if (!box) return;
        // 浮动窗：不遮挡页面，安装期间可继续操作
        box.classList.add('active');
        box.classList.remove('minimized');
        document.getElementById('engineTaskTitle').textContent =
            '引擎任务 · ' + (taskId ? String(taskId).split('-').slice(1, 3).join(' ') : '');
        if (taskId) emPollTask(taskId);
    }

    function taskFloatToggle() {
        const box = document.getElementById('engineTaskModal');
        if (box) box.classList.toggle('minimized');
    }

    function taskFloatClose() {
        const box = document.getElementById('engineTaskModal');
        if (box) box.classList.remove('active');
    }

    async function emOpenTask(taskId) { openEngineTask(taskId); }

    async function emPollTask(taskId) {
        const meta = document.getElementById('engineTaskMeta');
        const logEl = document.getElementById('engineTaskLog');
        const t = await apiGet('/engines/tasks/' + encodeURIComponent(taskId));
        if (!t || t.error) {
            if (meta) meta.textContent = '任务不存在或已过期';
            return;
        }
        if (meta) {
            const pct = Math.round((t.progress || 0) * 100);
            meta.innerHTML = `<b>${escapeHtml(t.engine_type)} ${escapeHtml(t.version)}${t.flavor ? '/' + escapeHtml(t.flavor) : ''}</b>
                · ${actionLabel(t.action)} · <b class="task-status-${escapeHtml(t.status)}">${statusLabel(t.status)}</b> · ${pct}%<br>
                <span class="task-msg">${escapeHtml(t.message || '')}</span>${t.error ? '<br><span style="color:var(--error)">' + escapeHtml(t.error) + '</span>' : ''}`;
        }
        const bar = document.getElementById('engineTaskBar');
        if (bar) {
            const pct = Math.round((t.progress || 0) * 100);
            bar.style.width = pct + '%';
            bar.className = t.status === 'failed' ? 'bad' : (t.status === 'done' ? 'ok' : '');
        }
        if (logEl) {
            logEl.textContent = (t.log || []).join('\n') || '（暂无日志）';
            logEl.scrollTop = logEl.scrollHeight;
        }
        if (t.status === 'running' || t.status === 'pending') {
            setTimeout(() => emPollTask(taskId), 2000);
        } else {
            emRefreshTasks();
        }
    }


    // ---- 导出（#1 拆分后由本模块提供）----
    window.renderTools = renderTools;
    window.emCollapse = emCollapse;
    window.emOpenTask = emOpenTask;
    window.emPollTask = emPollTask;
    window.emRefreshCatalog = emRefreshCatalog;
    window.emRefreshTasks = emRefreshTasks;
    window.emTask = emTask;
    window.emToggleMore = emToggleMore;
    window.openEngineTask = openEngineTask;
    window.taskFloatClose = taskFloatClose;
    window.taskFloatToggle = taskFloatToggle;
})();
