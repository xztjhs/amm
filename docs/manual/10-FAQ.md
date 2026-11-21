# 第 10 章 常见问题 FAQ

> 版本：v1.9 ｜ 更新日期：2026-11-17
> 按主题分类。每条都是真实遇到的问题。

---

## 10.1 首次使用

### Q1：第一次用，应该先做什么？

打开首页确认 **Dashboard 有数据**（CPU/内存/GPU 都有值），
然后去 **Models** 用一个 0.6B 的 embedding 模型练手：

1. 类别选 `Embedding`
2. 引擎选 `llama.cpp`
3. 模型文件选 `Qwen/Qwen3-Embedding-0.6B-GGUF/...Q8_0.gguf`
4. 实例 UID **留空**
5. 点启动

启动成功后去 **Playground → Embedding** 点测试，能看到向量维度就说明环境完全 OK。

### Q2：必须有 GPU 吗？

不是必须，但有 GPU 体验完全不同。

- **纯 CPU**：能跑小模型（embedding、小语言模型），速度慢但可用
- **有 GPU**：llama.cpp 的 CUDA 构建、vLLM、Diffusers 都能用

纯 CPU 部署时，`install.sh` 会回退 CPU 构建，不影响安装。

### Q3：显存要多大？

| 模型规模 | 最低显存 | 推荐 |
|---|---|---|
| Embedding 0.6B | 1G | 2G |
| 7B / 8B（Q4） | 6G | 12G |
| 14B（Q4） | 10G | 20G |
| 27B / 32B（Q4） | 20G | 40G |
| 70B（Q4） | 40G | 80G |
| 文生图（SDXL 级） | 8G | 16G |
| 文生视频（Wan2.2） | 24G+ | 48G+ |

本环境 RTX A5000 24G 的实测表现：7B 级对话模型流畅，27B Q2 能跑但慢，视频模型吃力。

---

## 10.2 模型与推理

### Q4：启动实例时 UID 留空还是自己填？

**建议留空**。系统会按模型文件自动生成带类别身份的名字，如
`Qwen3-Embedding-0.6B-Q8_0.gguf` → `qwen3-embedding-0.6b`。

自己填的话，**千万不要只填裸家族名**（如 `qwen3`），原因见 Q5。

### Q5：为什么 Cherry Studio 把我 qwen3 embedding 显示成 "Qwen3 14B"？

因为 Cherry Studio 会拿模型 ID 去"模型名注册表"做**归一化模糊匹配**
（去掉 `-14b`、`-0.6b` 这类参数尺寸后缀再比对）。

```
qwen3              → 归一化后命中 qwen3-14b → 显示 "Qwen3 14B"
                    → capabilities 被覆盖成聊天模型 → embedding 能力丢失
qwen3-embedding-0.6b → 命中 qwen3-embedding-0-6b → 名称与能力都正确
```

**解决**：把实例 UID 改成带类别身份的完整名字。v1.9 起向导会自动生成正确的 UID，
并在你手填风险名称时实时 ⚠ 提示。

### Q6：同一个模型能开多个实例吗？

可以。每个实例有独立的 UID 和端口（18100+），互不干扰。

典型用途：

- 同一 embedding 模型跑两个不同精度（Q4 / Q8）做效果对比
- 对话模型开两个副本分担并发（但注意显存翻倍）

### Q7：Stop 和「结束运行」有什么区别？

| 操作 | 进程 | 实例定义 | 端口 |
|---|---|---|---|
| Stop | 停止 | **保留** | 保留（可重新 Start） |
| 结束运行 | 停止 | **删除** | 释放给其他实例 |

想临时腾显存用 Stop；确定不再需要这个实例用「结束运行」。

### Q8：参数改了要重启吗？

大部分参数（ctx_size、n_gpu_layers、threads 等）是**启动参数**，改了必须重启实例。

只有少数运行时参数（默认的 temperature/top_p 等）改完立即生效。

界面上改完参数会提示是否重启。

### Q9：模型加载很慢，正常吗？

| 模型规模 | 正常加载时间 |
|---|---|
| Embedding 0.6B | 1-3 秒 |
| 7B（Q4，GPU） | 5-15 秒 |
| 27B（Q4，GPU） | 20-60 秒 |
| 35B（CPU） | 1-3 分钟 |

如果明显超出，看是不是在做 CPU offload（显存不足时会退到 CPU，慢很多）。

### Q10：为什么我的模型回答的思考过程，不是最终答案？

对话模型（特别是 Qwen3 系列）会输出 `reasoning_content`（思考链）+ `content`（最终答案）。

- Playground 会分别显示
- 调用历史的「输出」记录的是实际生成的全部内容
- 如果只想看最终答案，在参数里关掉思考（`no_reasoning` → `--reasoning off`）

---

## 10.3 客户端对接

### Q11：Cherry Studio / Open WebUI 怎么连 AMM？

```
API 类型：OpenAI
Base URL：http://<服务器IP>:<端口>/v1
API Key：（留空，或用 Token Router 签发的虚拟 Key）
```

然后点"检查"/"获取模型列表"，应该能看到所有 running 的实例。

> **端口注意**：本环境是 60008，官方 compose 默认 60006，以实际部署为准。
> 填 `http://<IP>:<端口>` 不带 `/v1` 是错的，一定要带。

### Q12：客户端里模型很多、很乱怎么办？

三个办法：

1. **只启动需要的实例** —— `/v1/models` 只列 running 的
2. **用 Token Router 的 Key 白名单** —— 签发 Key 时只勾选要暴露的模型
3. **改客户端分组名** —— Settings → 客户端分组名，会作为 `owned_by` 显示

### Q13：外部 provider 的模型为什么客户端看不到？

需要先在 **Token Router → 外部 Provider** 里：

1. 填 Base URL + API Key
2. 点「🔄 拉取模型」同步模型列表
3. 点「💾 保存 Provider」

保存后 `/v1/models` 才会汇总进来。

### Q14：为什么调用报 "Incorrect API key provided" 但 Key 是对的？

极可能是 **Provider 的 Base URL 配错了**。

```bash
curl -s http://localhost:60008/api/providers/<name> | python3 -m json.tool
```

检查 `base_url` 是不是你要用的服务商。实测踩过：Key 是 icompify 的，
但 Base URL 是 `https://api.openai.com/v1`，于是 Key 被发到 OpenAI，
返回的报错信息指向 OpenAI，非常难排查。

---

## 10.4 鉴权与安全

### Q15：清了默认 Key，为什么还是 401？

这是**刻意的安全设计**。

一旦签发过任何 Key，`auth_enforced` 会保持为 true，
防止旧 Key 持有者在你"清除"后突然获得无鉴权访问。

**真正退回免鉴权**：Settings → **完全关闭鉴权**（需输入 YES 确认）。

### Q16：关闭鉴权和清除 Key 有什么区别？

| 操作 | default_key | auth_enforced | 虚拟 Key | 结果 |
|---|---|---|---|---|
| 清除默认 Key | 删除 | 无虚拟 Key 时才置 false | 不动 | 可能仍 401 |
| **完全关闭鉴权** | 删除 | **强制 false** | 保留 | 真正免鉴权 |

虚拟 Key 保留是为了将来重新开启时历史与配额不丢。

### Q17：关闭鉴权有风险吗？

有。关闭后**任何能访问该端口的人都能调 `/v1/*`**，消耗你的算力和外部 API 额度。

只在以下情况关：

- 纯内网、可信网络
- 端口没有映射到外网
- 只有自己用

界面上有二次确认（要输入 YES）。

### Q18：虚拟 Key 泄露了怎么办？

Token Router → 找到该 Key → 吊销。立即失效，不可恢复。

建议给每个应用单独签发 Key，泄露时只影响一个。

### Q19：API Key 存在哪里？安全吗？

存在 `data/router.json`，明文（文件权限 600，只有 root 可读）。

不要把这个文件提交到代码仓库，不要发给别人。`.gitignore` 已排除 `data/`。

---

## 10.5 日志与记录

### Q20：调用历史的「输出」为什么是空的 / (stream)？

v1.9 已修复。现在：

- 本地 chat / embeddings / rerank 都会记录
- 流式调用记录实际生成的文本，不再是 `(stream)` 占位符

如果还看到 `(stream)`，说明版本较老或那条记录是修复前的。

### Q21：调用历史会一直增长吗？

不会。默认保留最近 2000 条，超出自动丢弃。

需要长期留档 → Token Router → 导出 CSV。

### Q22：日志文件会把磁盘写满吗？

v1.9 起自动轮转：默认 50MB × 5 份 = 最多 300MB。

可以调整：

```bash
-e AMM_LOG_MAX_BYTES=104857600   # 100MB
-e AMM_LOG_BACKUPS=3
```

历史上曾出现单文件涨到 174MB（0.5s 刷新间隔下约 30MB/小时）。

---

## 10.6 界面与品牌

### Q23：能改界面上的名字和 LOGO 吗？

可以。Settings → **项目品牌**：

- 项目名称：改顶栏和浏览器标题
- 副标题：标题栏显示为「名称 - 副标题」
- LOGO 路径：同时用作 favicon

保存后立即生效，刷新也保持。「恢复默认」一键还原。

### Q24：页面刷新太快/太慢，能调吗？

右上角刷新间隔下拉：0.1s ~ 5min。

- 日常用 2s ~ 5s
- 长期挂机用 10s ~ 30s
- 0.1s 只用于短时调试（会让日志增长很快）

### Q25：深色/浅色主题怎么切？

右上角 🌙 / ☀️ 按钮。选择会记忆，下次打开还是这个主题。

### Q26：升级后界面还是旧的怎么办？

浏览器缓存。**Ctrl+Shift+R** 强制刷新。

前端静态资源带版本号（v1.9 是 `?v=2.0`），正常情况下会自动失效旧缓存。

---

## 10.7 文件管理

### Q27：Settings 里的文件管理能访问哪些目录？

只能访问：`/amm`、`/models`、`/amm/logs`、`/amm/data`、`/amm/templates`、`/amm/verification`。

`/etc/passwd`、`/root`、`../` 穿越都会被拒绝（403）。

如需额外目录，设环境变量：

```bash
-e AMM_FS_ROOTS="/data:/backup"
```

### Q28：删除的文件能恢复吗？

能。默认删除是**移入回收站** `/amm/.trash`，文件名带时间戳。

要真正删除，在第二个确认对话框里选"永久删除"。

恢复方法：文件管理器进 `/amm/.trash`，把文件移回去。

### Q29：能在线编辑多大的文件？

文本文件上限 **2MB**。超过只允许下载，不允许在线编辑。

二进制文件（模型、图片、压缩包等）一律不允许在线编辑。

---

## 10.8 部署与升级

### Q30：改了代码要重建镜像吗？

**不需要**。代码目录是挂载进容器的：

```bash
cd /path/to/amm/app
git pull origin v1.0
docker restart AMM
```

### Q31：升级后版本号没变？

旧进程还在跑。`docker restart AMM` 后确认：

```bash
curl -s http://localhost:60008/api/settings | grep -o '"version":"[^"]*"'
```

### Q32：`git pull` 说本地有改动怎么办？

生产目录通常有有意保留的改动（节点名、自建模版、数据库）。用 stash：

```bash
git stash -u
git pull origin v1.0 --ff-only
git stash pop
```

**不要** `git checkout .`，会冲掉你的配置。

### Q33：两个分支 master / v1.0 用哪个？

**已经统一**（2026-11-17 起指向同一提交）。

- 日常开发推 `master`
- 生产容器保持在 `v1.0`
- 发布后执行 `git push origin master:v1.0` 保持同步

### Q34：容器重启后模型都没了？

模型实例定义存在 SQLite，不会丢。但**进程会停**。

之前 running 的实例会在服务启动后自动恢复（`desired_status=running`）。
恢复需要加载模型，大模型要等一会儿。

### Q35：能同时跑生产和测试两套吗？

可以。用不同的容器名、端口、数据目录：

```bash
# 生产
-p 60008:80 -v /app/amm:/amm -v /app/data:/amm/data

# 测试
-p 60010:80 -v /app/att:/amm -v /app/att-data:/amm/data
```

模型目录可以共享（只读使用），引擎目录也可以共享。

---

## 10.9 故障与恢复

### Q36：容器莫名退出了，服务消失

检查退出码和重启策略：

```bash
docker inspect AMM --format 'Exit={{.State.ExitCode}} Policy={{.HostConfig.RestartPolicy.Name}}'
```

- `Exit=0`：被正常停止 → `docker start AMM`
- `Exit=255`：异常退出 → 看日志
- `Exit=137`：OOM 被杀 → 减小模型/降并发
- `Policy=no`：**根本问题** → `docker update --restart unless-stopped AMM`

宿主机重启过也会导致容器不回来（如果策略是 no）。

### Q37：模型启动成功但 GPU 上看不到

**判断依据只能是 nvidia-smi**：

```bash
nvidia-smi --query-compute-apps=pid,used_memory --format=csv
```

如果这里没你的进程，但 `--list-devices` 能看到 GPU，
说明 ggml 动态库缺失，运行时静默退回 CPU。

```bash
docker exec AMM ls /usr/local/bin/libggml-cuda.so
# 不存在 → 需要重装 llama.cpp（v1.9 install.sh 已修复）
```

### Q38：CUDA 编译报一堆错

先看是哪种：

| 报错 | 缺什么 |
|---|---|
| `--error 0x7f` | nvvm（cicc） |
| `CUDA::cublas not found` | libcublas |
| `cublas_v2.h not found` | cublas 头文件 |
| `.../cccl non-existent` | CCCL |

一条命令补齐：

```bash
bash /amm/deploy/cuda_components.sh
```

如果有本地组件包（推荐），挂载 `/opt/cuda-bundle:ro` 会秒级完成。

### Q39：gcc 版本不兼容 CUDA

CUDA 13.x 不支持 gcc 11.5。装并启用 gcc-toolset-12：

```bash
source /opt/rh/gcc-toolset-12/enable
gcc --version   # 12.x
```

### Q40：磁盘满了怎么快速腾空间？

按清理收益排序：

1. **删无用模型**（通常是大头，每个几 GB）
2. **清日志**：Log Center → 清理，或 `truncate -s 0`
3. **清回收站**：`/amm/.trash`
4. **清 pip 缓存**：`docker exec AMM pip cache purge`
5. **清下载缓存**：`/amm/data/huggingface`、`/amm/data/modelscope`

```bash
docker exec AMM du -sh /amm/* /models/* 2>/dev/null | sort -rh | head -15
```

---

## 10.10 其他

### Q41：能跑在 ARM 上吗？

理论上可以（llama.cpp 支持），但：

- 预编译的引擎可能有 x86 依赖
- CUDA 在 ARM 上支持有限
- 需要自己编译

不建议，除非有特殊需求。

### Q42：支持多用户吗？

AMM 本身是**单用户管理界面**，没有用户系统。

多用户场景建议：

- 每个用户/应用签发一个虚拟 Key（Token Router）
- 用 Key 的白名单和配额做隔离
- 不要直接暴露管理界面（`:60008/`）给不可信用户

### Q43：数据会上传到外部吗？

不会。AMM 完全本地运行。

唯一的外部通信是：

- 你主动配置的 provider（转发调用）
- 你主动下载模型（ModelScope / HuggingFace）
- 引擎版本查询（GitHub / PyPI）

### Q44：有 API 文档吗？

有，两个层次：

- **零 IT 经验版**：第 3 章
- **Agent / 自动化版**：第 4 章

也可以用 Playground 里的 API Debug 页签直接试接口。

### Q45：怎么反馈问题？

按第 8 章 8.11 收集五项信息（版本、症状、日志、环境、GPU），一起发出来。
