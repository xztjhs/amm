/**
 * AMM Frontend — logcenter 模块（从 app.js 拆出）
 * 依赖 core 提供的全局符号，统一从 window 取，避免隐式依赖。
 */
(function () {
    'use strict';
    const API_BASE = '/api';

    // 借用 core 的基础工具（#1 拆分）
    const apiGet = (...a) => window.apiGet(...a);
    const apiPost = (...a) => window.apiPost(...a);
    const toast = (...a) => window.toast(...a);
    const escapeHtml = (...a) => window.escapeHtml(...a);
    const escapeAttr = (...a) => window.escapeAttr(...a);

    // 从 core 借用的辅助（core 都挂在 window 上）
    // （无）

    // ---- Log Center (R5) ----
    async function lcLoadSources() {
        const sel = document.getElementById('lcSource');
        if (!sel) return;
        const data = await apiGet('/logs/center/sources');
        if (!data) return;
        const cur = sel.value;
        const opts = ['<option value="all">全部来源</option>', '<option value="server">服务日志</option>'];
        (data.instances || []).forEach(id => {
            const f = (data.server_log || []).find(x => x.source === id);
            const size = f ? ' (' + Math.round(f.size_bytes / 1024) + 'KB)' : '';
            opts.push(`<option value="${escapeHtml(id)}">${escapeHtml(id)}${size}</option>`);
        });
        sel.innerHTML = opts.join('');
        if (cur) sel.value = cur;
    }

    // ---- Log Center：导出 / 文件管理 / 清理 ----
    function lcQueryParams() {
        const source = document.getElementById('lcSource')?.value || 'all';
        const level = document.getElementById('lcLevel')?.value || 'ALL';
        const q = (document.getElementById('lcKeyword')?.value || '').trim();
        const lines = document.getElementById('lcLines')?.value || 200;
        const p = new URLSearchParams({ source, level, lines });
        if (q) p.set('q', q);
        return p;
    }

    function lcExport() {
        window.open('/api/logs/center/export?' + lcQueryParams().toString(), '_blank');
    }

    async function lcOpenFiles() {
        const box = document.getElementById('lcFilesBox');
        if (!box) return;
        if (box.style.display !== 'none') { box.style.display = 'none'; return; }
        const d = await apiGet('/logs/center/files');
        if (!d) return;
        const rows = (d.files || []).map(f => `
            <tr>
                <td class="rm-mono" style="font-size:11.5px">${escapeHtml(f.name)}</td>
                <td>${f.size_mb} MB</td>
                <td style="font-size:11.5px;color:var(--text-muted)">${new Date(f.mtime * 1000).toLocaleString()}</td>
                <td class="rm-actions">
                    <button class="btn btn-sm" onclick="window.lcViewFile('${escapeAttr(f.source)}')">查看</button>
                    <button class="btn btn-sm" onclick="window.lcDownloadFile('${escapeAttr(f.source)}')">下载</button>
                </td>
            </tr>`).join('') || '<tr><td colspan="4" class="rm-empty">暂无日志文件</td></tr>';
        box.innerHTML = `
            <div class="dl-head" style="margin-bottom:8px;">
                <h4 style="margin:0;font-size:13px;">🗂 日志文件（${d.count} 个 · 共 ${d.total_mb} MB）</h4>
            </div>
            <table class="rm-table"><thead><tr>
                <th>文件</th><th>大小</th><th>修改时间</th><th>操作</th>
            </tr></thead><tbody>${rows}</tbody></table>`;
        box.style.display = '';
    }

    function lcViewFile(source) {
        const sel = document.getElementById('lcSource');
        if (sel) {
            let opt = [...sel.options].find(o => o.value === source);
            if (!opt) { opt = new Option(source, source); sel.add(opt); }
            sel.value = source;
        }
        lcRefresh();
        document.getElementById('lcViewer')?.scrollIntoView({ block: 'center' });
    }

    function lcDownloadFile(source) {
        window.open('/api/logs/center/download?source=' + encodeURIComponent(source), '_blank');
    }

    async function lcCleanup() {
        const src = document.getElementById('lcSource')?.value || 'all';
        const name = src === 'all' ? '全部日志文件' : src;
        if (!confirm(`确定清理 ${name}？\n\n「清空」保留文件但删除内容（服务可继续写入）；\n「删除」会移除文件本身。`)) return;
        const del = confirm('要删除文件本身吗？\n确定 = 删除文件；取消 = 仅清空内容');
        const r = await apiPost('/logs/center/cleanup', {
            mode: del ? 'delete' : 'truncate',
            source: src,
        });
        if (r && r.success) {
            toast(`✅ 已清理 ${r.count} 个文件，释放 ${r.freed_mb} MB`, 'success');
            lcRefresh();
            const box = document.getElementById('lcFilesBox');
            if (box && box.style.display !== 'none') lcOpenFiles();
        } else toast('❌ 清理失败', 'error');
    }

    async function lcRefresh() {
        const viewer = document.getElementById('lcViewer');
        if (!viewer) return;
        const source = document.getElementById('lcSource')?.value || 'all';
        const level = document.getElementById('lcLevel')?.value || 'ALL';
        const q = (document.getElementById('lcKeyword')?.value || '').trim();
        const lines = document.getElementById('lcLines')?.value || 200;

        const params = new URLSearchParams({ source, level, lines });
        if (q) params.set('q', q);
        const data = await apiGet('/logs/center?' + params.toString());
        if (!data) { viewer.textContent = '日志获取失败'; return; }

        const stats = document.getElementById('lcStats');
        if (stats) {
            const counts = data.level_counts || {};
            const parts = ['总计 ' + data.total];
            ['ERROR', 'WARNING', 'INFO', 'DEBUG', 'RAW'].forEach(lv => {
                if (counts[lv]) parts.push(`${lv} ${counts[lv]}`);
            });
            stats.textContent = parts.join(' · ');
        }

        if (!data.entries || !data.entries.length) {
            viewer.textContent = '(没有匹配的日志)';
            return;
        }
        viewer.innerHTML = data.entries.map(e => {
            const lv = (e.level || 'RAW').toUpperCase();
            const src = `<span class="log-src">[${escapeHtml(e.source)}]</span>`;
            const ts = e.ts ? `<span class="log-ts">${escapeHtml(e.ts)}</span>` : '';
            return `<div class="log-line log-lv-line" data-lv="${escapeHtml(lv)}">`
                + ts + src
                + `<span class="log-lv">${escapeHtml(lv)}</span>`
                + `<span class="log-txt">${escapeHtml(e.message)}</span></div>`;
        }).join('');
        viewer.scrollTop = viewer.scrollHeight;
    }

    function lcAutoToggle() {
        const on = document.getElementById('lcAuto')?.checked;
        if (logRefreshTimer) { clearInterval(logRefreshTimer); logRefreshTimer = null; }
        if (on) {
            lcRefresh();
            logRefreshTimer = setInterval(lcRefresh, 5000);
        }
    }

    function lcDownload() {
        const source = document.getElementById('lcSource')?.value || 'server';
        if (source === 'all') { toast('请先选择具体来源再下载', 'error'); return; }
        window.open(API_BASE + '/logs/center/download?source=' + encodeURIComponent(source), '_blank');
    }

    window.lcLoadSources = lcLoadSources;
    window.lcRefresh = lcRefresh;
    window.lcAutoToggle = lcAutoToggle;
    window.lcDownload = lcDownload;
    window.saveNodeName = saveNodeName;
    window.saveBrand = saveBrand;
    window.resetBrand = resetBrand;
    window.pathsLoad = pathsLoad;
    window.savePaths = savePaths;
    window.cfgPathBrowse = cfgPathBrowse;
    window.cfgLogoBrowse = cfgLogoBrowse;
    window.disableAuth = disableAuth;
    window.enableAuth = enableAuth;
    window.authLoad = authLoad;
    window.dkGenerate = dkGenerate;
    window.pipPresetChange = pipPresetChange;
    window.savePipIndex = savePipIndex;
    window.dkCopy = dkCopy;
    window.dkClear = dkClear;


    // ---- 导出（#1 拆分后由本模块提供）----
    window.lcAutoToggle = lcAutoToggle;
    window.lcCleanup = lcCleanup;
    window.lcDownload = lcDownload;
    window.lcDownloadFile = lcDownloadFile;
    window.lcExport = lcExport;
    window.lcLoadSources = lcLoadSources;
    window.lcOpenFiles = lcOpenFiles;
    window.lcQueryParams = lcQueryParams;
    window.lcRefresh = lcRefresh;
    window.lcViewFile = lcViewFile;
})();
