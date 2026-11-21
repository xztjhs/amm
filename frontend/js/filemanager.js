// ================================================================
// File Manager（#7）：本地文件浏览 / 查看 / 编辑 / 新建 / 删除 / 上传 / 下载
// 挂在 Settings 页，同时作为通用目录选择器被其它面板复用（fmOpenPicker）。
// ================================================================
(function () {
    'use strict';

    const API_BASE = '/api';

    const state = {
        cur: '',                 // 当前目录
        parent: '',
        entries: [],
        editing: null,           // 正在编辑的文件路径
        picker: null,            // 目录选择模式回调
        dirty: false,
    };

    const $ = (id) => document.getElementById(id);

    function fmtSize(n) {
        if (n == null) return '--';
        const u = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0, v = Number(n);
        while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
        return (i === 0 ? v : v.toFixed(1)) + ' ' + u[i];
    }

    function fmtTime(ts) {
        if (!ts) return '--';
        return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    async function apiGet(path) {
        const r = await fetch(API_BASE + path);
        return r.json();
    }

    async function apiSend(path, body) {
        const r = await fetch(API_BASE + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        return r.json();
    }

    // ---------------- 渲染 ----------------
    function render() {
        const listEl = $('fmList');
        const pathEl = $('fmPath');
        const statEl = $('fmStat');
        if (!listEl) return;

        if (pathEl) pathEl.value = state.cur;
        syncRootButtons();
        if (statEl) {
            statEl.textContent =
                `${state.entries.filter(e => e.type === 'dir').length} 个目录 · ` +
                `${state.entries.filter(e => e.type === 'file').length} 个文件`;
        }

        const rows = [];
        if (state.parent) {
            rows.push(`<tr class="fm-row" onclick="window.fmGo('${esc(state.parent)}')">
                <td><span class="fm-ico">↩</span> 上级目录</td>
                <td class="fm-dim">--</td><td class="fm-dim">--</td>
                <td class="fm-actions"></td></tr>`);
        }
        state.entries.forEach((e, i) => {
            const isDir = e.type === 'dir';
            const ico = isDir ? '📁' : iconFor(e.name);
            const nameCls = isDir ? 'fm-name dir' : 'fm-name';
            const onclick = isDir
                ? `window.fmGo('${esc(e.path)}')`
                : `window.fmView('${esc(e.path)}')`;
            rows.push(`<tr class="fm-row">
                <td class="${nameCls}" onclick="${onclick}">
                    <span class="fm-ico">${ico}</span> ${esc(e.name)}
                    ${e.symlink ? '<span class="fm-tag">link</span>' : ''}
                </td>
                <td class="fm-dim">${isDir ? '--' : fmtSize(e.size)}</td>
                <td class="fm-dim">${fmtTime(e.mtime)}</td>
                <td class="fm-actions">
                    ${isDir ? `<button class="btn btn-xs" onclick="window.fmGo('${esc(e.path)}')">进入</button>` : ''}
                    ${!isDir ? `<button class="btn btn-xs" onclick="window.fmView('${esc(e.path)}')">查看</button>` : ''}
                    ${!isDir ? `<a class="btn btn-xs" href="${API_BASE}/fm/download?path=${encodeURIComponent(e.path)}" download>下载</a>` : ''}
                    <button class="btn btn-xs" onclick="window.fmRename('${esc(e.path)}','${esc(e.name)}')">改名</button>
                    <button class="btn btn-xs btn-danger" onclick="window.fmDelete('${esc(e.path)}','${esc(e.name)}')">删除</button>
                </td>
            </tr>`);
        });

        listEl.innerHTML = rows.length ? `<table class="fm-table">
            <thead><tr><th>名称</th><th style="width:100px">大小</th>
            <th style="width:170px">修改时间</th><th style="width:260px">操作</th></tr></thead>
            <tbody>${rows.join('')}</tbody></table>`
            : '<div class="fm-empty">空目录</div>';
    }

    function iconFor(name) {
        const ext = (name.split('.').pop() || '').toLowerCase();
        if (['json', 'yaml', 'yml', 'toml', 'ini', 'conf'].includes(ext)) return '⚙️';
        if (['py', 'js', 'ts', 'sh', 'css', 'html', 'md'].includes(ext)) return '📄';
        if (['log', 'out', 'txt'].includes(ext)) return '📝';
        if (['gguf', 'safetensors', 'bin'].includes(ext)) return '🧠';
        if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return '🖼️';
        return '📃';
    }

    // ---------------- 导航 ----------------
    async function go(path) {
        const data = await apiGet('/fm/list?path=' + encodeURIComponent(path || ''));
        if (!data || data.error) {
            window.toast && window.toast('❌ ' + ((data && data.error) || '读取失败'), 'error');
            return;
        }
        state.cur = data.path;
        state.parent = data.parent || '';
        state.entries = [...(data.dirs || []), ...(data.files || [])];
        if (state.picker && $('fmPickerMode')) $('fmPickerMode').style.display = '';
        render();
    }

    async function up() {
        if (state.parent) await go(state.parent);
    }

    // 一键切换常用根目录（/amm、/models、日志、数据），省掉手输路径
    async function jumpRoot(root) {
        if (!root) return;
        await go(root);
        syncRootButtons();
    }

    // 当前目录落在哪个根下就高亮那个按钮（最长前缀匹配）
    function syncRootButtons() {
        const bar = $('fmRootSwitch');
        if (!bar) return;
        const cur = state.cur || '';
        let best = null, bestLen = -1;
        bar.querySelectorAll('button[data-root]').forEach(b => {
            const r = b.dataset.root || '';
            b.classList.remove('btn-primary');
            const hit = cur === r || cur.startsWith(r.endsWith('/') ? r : r + '/');
            if (hit && r.length > bestLen) { bestLen = r.length; best = b; }
        });
        if (best) best.classList.add('btn-primary');
    }

    // ---------------- 查看 / 编辑 ----------------
    async function view(path) {
        const d = await apiGet('/fm/read?path=' + encodeURIComponent(path));
        if (!d || d.error) {
            window.toast && window.toast('❌ ' + ((d && d.error) || '读取失败'), 'error');
            return;
        }
        state.editing = path;
        const box = $('fmEditor');
        const title = $('fmEditorTitle');
        const meta = $('fmEditorMeta');
        const area = $('fmContent');
        const saveBtn = $('fmSaveBtn');
        if (!box) return;

        box.style.display = '';
        if (title) title.textContent = d.name;
        if (meta) {
            meta.textContent = `${fmtSize(d.size)} · ${fmtTime(d.mtime)}` +
                (d.editable ? '' : ` · ${d.reason || '不可编辑'}`);
        }
        if (area) {
            area.value = d.content || '';
            area.readOnly = !d.editable;
        }
        if (saveBtn) saveBtn.disabled = !d.editable;
        state.dirty = false;
    }

    function closeEditor() {
        if (state.dirty && !confirm('有未保存的修改，确定关闭？')) return;
        const box = $('fmEditor');
        if (box) box.style.display = 'none';
        state.editing = null;
        state.dirty = false;
    }

    async function save() {
        if (!state.editing) return;
        const area = $('fmContent');
        if (!area) return;
        const r = await apiSend('/fm/write', {
            path: state.editing,
            content: area.value,
        });
        if (r && r.success) {
            state.dirty = false;
            window.toast && window.toast('✅ 已保存 ' + (r.name || ''), 'success');
            await go(state.cur);
        } else {
            window.toast && window.toast('❌ ' + ((r && r.error) || '保存失败'), 'error');
        }
    }

    // ---------------- 新建 / 上传 ----------------
    async function newFile() {
        const name = prompt('新文件名（含扩展名，例如 notes.md）：');
        if (!name) return;
        if (name.includes('/')) { window.toast && window.toast('名称不能含 /', 'error'); return; }
        const full = (state.cur.endsWith('/') ? state.cur : state.cur + '/') + name;
        const r = await apiSend('/fm/write', { path: full, content: '' });
        if (r && r.success) {
            window.toast && window.toast('✅ 已创建 ' + name, 'success');
            await go(state.cur);
            await view(full);
        } else {
            window.toast && window.toast('❌ ' + ((r && r.error) || '创建失败'), 'error');
        }
    }

    async function newDir() {
        const name = prompt('新目录名：');
        if (!name) return;
        if (name.includes('/')) { window.toast && window.toast('名称不能含 /', 'error'); return; }
        const full = (state.cur.endsWith('/') ? state.cur : state.cur + '/') + name;
        const r = await apiSend('/fm/mkdir', { path: full });
        if (r && r.success) {
            window.toast && window.toast('✅ 已创建目录 ' + name, 'success');
            await go(state.cur);
        } else {
            window.toast && window.toast('❌ ' + ((r && r.error) || '创建失败'), 'error');
        }
    }

    async function upload() {
        const input = $('fmUploadInput');
        if (!input || !input.files || !input.files.length) {
            window.toast && window.toast('请先选择文件', 'info');
            return;
        }
        const fd = new FormData();
        for (const f of input.files) fd.append('file', f, f.name);
        const url = API_BASE + '/fm/upload?path=' + encodeURIComponent(state.cur);
        try {
            const resp = await fetch(url, { method: 'POST', body: fd });
            const r = await resp.json();
            if (r && r.success) {
                window.toast && window.toast(
                    `✅ 已上传 ${r.files.length} 个文件（${fmtSize(r.bytes)}）`, 'success');
                input.value = '';
                await go(state.cur);
            } else {
                window.toast && window.toast('❌ ' + ((r && r.error) || '上传失败'), 'error');
            }
        } catch (e) {
            window.toast && window.toast('❌ 上传失败: ' + e, 'error');
        }
    }

    // ---------------- 删除 / 改名 ----------------
    async function del(path, name) {
        const permanent = confirm(
            `确定删除「${name}」？\n\n` +
            `确定 = 移入回收站（可恢复，位于 /amm/.trash）\n` +
            `取消 = 我改变主意了`);
        if (!permanent) return;
        const reallyDelete = confirm(
            `要彻底删除（不进回收站）吗？\n\n` +
            `确定 = 永久删除，不可恢复\n` +
            `取消 = 移入回收站`);
        const r = await apiSend('/fm/delete', { paths: [path], permanent: reallyDelete });
        if (r && r.success) {
            window.toast && window.toast(
                reallyDelete ? '✅ 已永久删除' : '✅ 已移入回收站', 'success');
            await go(state.cur);
        } else {
            const err = (r && r.failed && r.failed[0] && r.failed[0].error) ||
                (r && r.error) || '删除失败';
            window.toast && window.toast('❌ ' + err, 'error');
        }
    }

    async function rename(path, oldName) {
        const name = prompt('新名称：', oldName);
        if (!name || name === oldName) return;
        const r = await apiSend('/fm/rename', { path: path, new_name: name });
        if (r && r.success) {
            window.toast && window.toast('✅ 已重命名', 'success');
            await go(state.cur);
        } else {
            window.toast && window.toast('❌ ' + ((r && r.error) || '重命名失败'), 'error');
        }
    }

    // ---------------- 目录选择器（供其它面板复用） ----------------
    function openPicker(startPath, cb) {
        state.picker = cb || null;
        const box = $('fmPickerBar');
        if (box) box.style.display = '';
        go(startPath || '');
        const el = $('fmPanel');
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function pickCurrent() {
        if (state.picker) {
            state.picker(state.cur);
            state.picker = null;
            const box = $('fmPickerBar');
            if (box) box.style.display = 'none';
            window.toast && window.toast('已选择: ' + state.cur, 'success');
        }
    }

    function init() {
        const input = $('fmContent');
        if (input) {
            input.addEventListener('input', () => { state.dirty = true; });
        }
        go('');
    }

    window.fmGo = go;
    window.fmUp = up;
    window.fmView = view;
    window.fmSave = save;
    window.fmCloseEditor = closeEditor;
    window.fmNewFile = newFile;
    window.fmNewDir = newDir;
    window.fmUpload = upload;
    window.fmDelete = del;
    window.fmRename = rename;
    window.fmOpenPicker = openPicker;
    window.fmPickCurrent = pickCurrent;
    window.fmInit = init;
    window.fmRefresh = () => go(state.cur);
    window.fmJumpRoot = jumpRoot;
})();
