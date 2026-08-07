#!/usr/bin/env node
/**
 * AMM 界面白盒自动化测试 (v0.5)
 * 双轨验证：
 *  A) 前端 DOM 存在性 + 交互（页面结构、导航、主题、9类Playground、引擎/文件浏览按钮）
 *  B) 后端 HTTP API 功能（health/system/instances/quantize/types/v1/models）
 * 注意: headless 沙箱可能无法跨网 fetch /api, 故后端用 HTTP 直验。
 */
const BASE = process.argv[2] || 'http://192.168.100.245:60006';
const CDP = 'http://127.0.0.1:18800';

let pass = 0, fail = 0;
const results = [];
function check(name, cond, extra = '') { if (cond) { pass++; results.push(`  ✅ ${name}`); } else { fail++; results.push(`  ❌ ${name} ${extra}`); } }

// ---- B: HTTP API 直测 ----
async function api(path) { try { const r = await fetch(BASE + path, { cache: 'no-store' }); const t = await r.text(); try { return { status: r.status, json: JSON.parse(t) }; } catch { return { status: r.status, raw: t }; } } catch (e) { return { err: String(e) }; } }

async function httpChecks() {
  const health = await api('/api/health');
  check('API /api/health ok', health.json?.status === 'ok', JSON.stringify(health).slice(0,60));
  const sys = await api('/api/system');
  check('API /api/system (含gpus)', sys.json?.gpus?.length >= 1, 'gpus=' + sys.json?.gpus?.length);
  const g0 = sys.json?.gpus?.[0];
  check('API GPU 含型号/利用率', g0?.name && g0?.util_sm !== undefined, JSON.stringify(g0).slice(0,60));
  check('API GPU 含 running_processes 字段', 'running_processes' in (g0 || {}));
  const inst = await api('/api/instances');
  check('API /api/instances 9模型', Object.keys(inst.json || {}).length >= 9, 'n=' + Object.keys(inst.json||{}).length);
  const qtypes = await api('/api/quantize/types');
  check('API 量化types(≥10种)', qtypes.json?.quant_types && Object.keys(qtypes.json.quant_types).length >= 10);
  check('API 量化含 q4_k_m', qtypes.json?.quant_types?.q4_k_m === 'q4_k_m');
  const fsl = await api('/api/fs/list?path=');
  check('API /api/fs/list 浏览 /models', (fsl.json?.dirs || []).length >= 1, 'dirs=' + fsl.json?.dirs?.length);
  const vm = await api('/v1/models');
  check('API /v1/models', vm.json?.data?.length > 0);
  const dl = await api('/api/models/download/status');
  check('API 下载 status 端点', dl.status === 200);
}

async function domChecks(c) {
  const ev = (expr, ap = false) => c.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: ap }).then(r => r.result?.value);
  await ev(`location.reload()`); await new Promise(r => setTimeout(r, 4000));

  const hasGPU = await ev(`!!document.querySelector('[data-tab="gpu"]')`);
  check('UI 导航无 GPU', !hasGPU);
  const navs = await ev(`[...document.querySelectorAll('.nav-item')].map(n=>n.dataset.tab).join(',')`);
  check('UI 至少5个导航', navs.split(',').length >= 5, navs);

  // 主题切换
  await ev(`document.getElementById('themeToggle').click()`); await new Promise(r => setTimeout(r, 300));
  const theme = await ev(`document.documentElement.getAttribute('data-theme') || 'dark'`);
  check('UI 主题切换生效', theme === 'light', theme);

  // 刷新间隔
  const rv = await ev(`document.getElementById('refreshInterval')?.value`);
  check('UI 刷新间隔默认500ms', rv === '500', 'v=' + rv);

  // 布局(HTML静态,不需数据)
  const gpuTitle = await ev(`[...document.querySelectorAll('.section-title')].some(x=>x.textContent.includes('GPU 状态'))`);
  const modelGrid = await ev(`!!document.getElementById('dashboardModels')`);
  const dashboard3col = await ev(`!!document.querySelector('.dashboard-models')`);
  check('UI Dashboard GPU标题', gpuTitle);
  check('UI Dashboard models 容器', modelGrid);
  check('UI Dashboard 3列class', dashboard3col);

  // Settings
  await ev(`document.querySelector('[data-tab="settings"]').click()`); await new Promise(r => setTimeout(r, 500));
  check('UI 下载面板', await ev(`!!document.getElementById('modelDownloadPanel')`));
  check('UI 量化面板', await ev(`!!document.getElementById('quantizePanel')`));
  check('UI 量化源"浏览"按钮', await ev(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='浏览')`));
  check('UI 量化输出目录输入', await ev(`!!document.getElementById('quOut')`));

  // Playground
  await ev(`document.querySelector('[data-tab="playground"]').click()`); await new Promise(r => setTimeout(r, 400));
  const pg = await ev(`[...document.querySelectorAll('.pg-tab')].map(x=>x.dataset.pg).join('-')`);
  check('UI 9类Playground', ['chat','embedding','asr','tts','rerank','ocr','image','video','i2v'].every(x => (pg||'').split('-').includes(x)), pg);
  check('UI Pg chat模型选择', await ev(`!!document.getElementById('pgChatModel')`));
  check('UI Pg embedding测试', await ev(`!!document.getElementById('pgEmbInput')`));

  // Models
  await ev(`document.querySelector('[data-tab="models"]').click()`); await new Promise(r => setTimeout(r, 400));
  check('UI 引擎选择器', (await ev(`document.querySelectorAll('.engine-option').length`)) >= 1);
  check('UI 文件浏览按钮', await ev(`[...document.querySelectorAll('button')].some(b=>b.textContent.includes('浏览'))`));

  // Logs
  await ev(`document.querySelector('[data-tab="logs"]').click()`); await new Promise(r => setTimeout(r, 300));
  check('UI 日志模型选择', await ev(`!!document.getElementById('logModelSelect')`));
}

async function run() {
  console.log(`\n🧪 AMM 白盒测试 -> ${BASE}\n`);
  console.log('-- B 后端 HTTP 接口 --');
  await httpChecks();

  console.log('-- A 前端 DOM --');
  try {
    const t = await (await fetch(CDP + '/json/new?about:blank', { method: 'PUT' })).json();
    const ws = new WebSocket(t.webSocketDebuggerUrl); let id = 0; const pending = new Map();
    const send = (m, pa = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method: m, params: pa })); });
    ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); } };
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    const c = { send };
    await send('Runtime.enable'); await send('Page.enable');
    await send('Page.navigate', { url: BASE });
    await new Promise(r => setTimeout(r, 5000));
    await nodeList(c);
    try { await (await fetch(CDP + '/json/close/' + t.id, { method: 'PUT' })); } catch (e) { }
  } catch (e) { fail++; results.push('  ❌ 前端 DOM 测试环境异常: ' + e.message); }

  console.log(results.join('\n'));
  console.log(`\n📊 结果: PASS ${pass} | FAIL ${fail}`);
  process.exit(fail ? 1 : 0);
}

run().catch(e => { console.error('执行异常:', e); process.exit(2); });