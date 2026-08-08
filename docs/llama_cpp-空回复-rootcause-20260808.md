# llama.cpp Chat 侧"有 think 但回复为空"根因分析

> 日期: 2026-08-08 | 模型: Qwen3.6-35B-A3B-Uncensored (llama_cpp) | 现场实测复现

## 结论先行

**不是 llama.cpp 输出有问题，也不是服务没起来 —— 是"Qwen3 思考模型特性" + "Playground 前端只读 content 不读 reasoning_content + 本地渲染逻辑"三者叠加。**

llama-server 实际**正确返回**了两部分:
- `delta.reasoning_content` / `message.reasoning_content` = 思考过程（"think"）
- `delta.content` / `message.content` = 最终回复

只要给足 token 预算,content 就正常出现。

## 现场复现数据（live: 192.168.100.245:60006）

| 请求 max_tokens | 结果 | content | finish_reason |
|---|---|---|---|
| 128 | 全花在思考上 | 空 `""` | `length`（思考耗尽预算） |
| 500 (stream) | reasoning 500 tok, content 0 | 空 | `length` |
| 1024 | 回答正常 | `'我是通义千问…'` | `stop` |
| 4096 | 回答正常 | `'我是通义千问…'` | `stop` |

- 问题提问 `1+1等于几` 在 max_tokens=128 时: `content:""`、`finish_reason:"length"`、usage.completion_tokens=128 —— 全部 token 被 reasoning 吃掉。
- 同样提问 max_tokens=1000: 思考 615 字符后停，`content:'2'`, `finish_reason:stop`。

## 结论：Qwen3 思考模型，思考太啰嗦占满 max_tokens

1. 该 `Qwen3.6-35B-A3B-Uncensored` 是 Qwen3 类**思考模型**，默认先输出一段很长的 reasoning（几分~几千 token）才出真正回答。
2. Playground 默认 `max_tokens=1024`（app.js `pgChatMaxTokens` 默认 1024）。**简单问题够，但长思考/复杂问题会把 1024 全部烧在 reasoning 上，导致 content 为空 → 前端显示"空回复"。**
3. **这是模型/参数配置问题，不是 llama.cpp 自身输出 bug。** llama.cpp 端输出结构完全正确。

## 前端另有渲染缺陷（放大问题）

`frontend/js/app.js` 流式处理里**只取 `delta.content`**（约 870 行 `if (c && c.delta && c.delta.content)`），**完全忽略 `delta.reasoning_content`**：

- 用户在 Playground 看到的"think"其实只是固定占位 `Thinking...`，**真实的 reasoning_content 从未被渲染/展示**。
- 当内容全被 thinking 占用时（finish_reason=length），`full` 一直为空 → 最终显示 `(空回复)`。

## 建议修复（任选/组合）

### A. 治本=max_tokens（默认改大)
把 `frontend/index.html` 里 `pgChatMaxTokens` 默认值 `1024` → `4096`（或更高）。Qwen3 思考模型需要大预算。

### B. 前端渲染 reasoning_content（体验）
在 app.js 流式循环中，把 `delta.reasoning_content` 也累加并展示（可折叠"思考中…"面板），
只在 `delta.content` 出现时把 Bot 文本更新为 content。否则用户只能看到"Thinking..."占位，看不到真实思考，问题期不可见。

### C. 可选: 用非思考模式/限制思考
- llama-server 是否支持 `--reasoning-budget`（限制思考 token 数）视构建版本而定。
- 或换用不带思考的 Qwen3 模型/在聊天模板里关闭 x-ceed 思考（模型相关）。
- 若需要"纯回复不回思考"，在服务启动参数上加限制思考的开关。

## 一键验证命令（快速复现）
```bash
B=192.168.100.245:60006
# 思考占满 → 空回复
curl -s -X POST $B/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"chat","messages":[{"role":"user","content":"1+1等于几？只回答结果"}],"max_tokens":128}'
# 预算充足 → 正常回复
curl -s -X POST $B/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"chat","messages":[{"role":"user","content":"1+1等于几？只回答结果"}],"max_tokens":1000}'
```