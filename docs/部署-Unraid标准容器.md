# AMM 部署：Unraid 标准容器（dockerMan）

> 更新：2026-11-17 ｜ 对应版本 v1.9
> AMM 在 Unraid 上以**标准 dockerMan 容器**运行（Docker 页可见图标、可 UI 启停、可模板重建）。
> 不要用 `docker compose up` —— compose 起的容器没有 Unraid label，Docker 页看不到。

---

## 一、当前部署实况

| 项 | 值 |
|---|---|
| 容器名 | `AMM`（**大写**，`docker restart amm` 会报 No such container） |
| 镜像 | `amm:rocky9-cu132`（1.93 GB） |
| 端口 | `60008 -> 80`（WebUI + `/api/*` + `/v1/*` 统一入口） |
| 运行时 | `--runtime nvidia` |
| 架构 | RTX A5000 24G，CUDA 13.2 |
| 重启策略 | `unless-stopped` |
| 健康检查 | 有（`HEALTHCHECK` 探测 `/api/health`） |
| 访问地址 | `http://<YOUR_HOST>:60008/` |

当前状态：`Up (healthy)`，版本 v1.9。

---

## 二、挂载（路径固定，勿改）

```
/path/to/amm              -> /amm             (程序+数据，含 SQLite)
/path/to/models  -> /models          (模型文件，70G)
/path/to/amm-engines      -> /opt/amm-engines (推理引擎，支持在线装卸)
```

> **代码是挂载进去的**，改代码 `git pull` + `docker restart AMM` 即可，不用重建镜像。

---

## 三、Unraid 三个 label（Docker 页识别关键）

```bash
-l net.unraid.docker.managed=dockerman
-l "net.unraid.docker.icon=https://raw.githubusercontent.com/xztjhs/amm/master/frontend/assets/logo.png"
-l "net.unraid.docker.webui=http://[IP]:[PORT:80]"
```

缺任何一个，Docker 页就不能正确显示/管理。
**图标必须是 raw 直链**，GitHub 的 `/blob/` 链接返回 HTML 页面，不会显示成图标。

---

## 四、完整启动命令

```bash
docker rm -f AMM 2>/dev/null

docker run -d --name AMM \
  -l net.unraid.docker.managed=dockerman \
  -l "net.unraid.docker.icon=https://raw.githubusercontent.com/xztjhs/amm/master/frontend/assets/logo.png" \
  -l "net.unraid.docker.webui=http://[IP]:[PORT:80]" \
  --restart unless-stopped --runtime nvidia \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -e TZ=Asia/Shanghai \
  -e AMM_ROOT=/amm -e MODELS_DIR=/models \
  -e PYTHONPATH=/amm/backend \
  -e AMM_ENGINES_ROOT=/opt/amm-engines \
  -e HF_HOME=/amm/data/huggingface \
  -e MODELSCOPE_CACHE=/amm/data/modelscope \
  -e AMM_AUTO_RELEASE=full \
  -e AMM_START_PRELOAD=0 \
  -e AMM_LOG_MAX_BYTES=52428800 \
  -e AMM_LOG_BACKUPS=5 \
  -p 60008:80 \
  -v /path/to/amm:/amm \
  -v /path/to/models:/models \
  -v /path/to/amm-engines:/opt/amm-engines \
  amm:rocky9-cu132 /bin/bash /amm/deploy/docker-entrypoint.sh
```

`docker-entrypoint.sh` 是 supervisor 模式：server 崩了自动重启，收到 SIGTERM 优雅退出。

---

## 五、镜像构建

### 5.1 用哪个 Dockerfile

| 文件 | 适用场景 |
|---|---|
| `deploy/Dockerfile.local-cuda` | **推荐**。用本地 CUDA 组件包，构建不依赖外网 |
| `deploy/Dockerfile.unraid` | 原始版。现场下载 NVIDIA RPM，网络差时大概率失败 |

### 5.2 构建命令

```bash
cd /path/to/amm/deploy

# 构建上下文需要 requirements.txt
cp ../backend/requirements.txt ./requirements.txt

# 准备 CUDA 组件包（见 5.3），放到 ./cuda-bundle/
docker build -f Dockerfile.local-cuda -t amm:rocky9-cu132 .
```

耗时约 3-5 分钟（系统依赖 + pip 依赖），不包含网络下载。

### 5.3 CUDA 组件包（关键）

镜像里 CUDA 是精简安装，缺几个组件会导致 llama.cpp 跑不起来。组件包目录：

```
deploy/cuda-bundle/
├── lib64/
│   ├── libcublas.so.13.4.1.3       # CUDA::cublas 必需
│   ├── libcublasLt.so.13.4.1.3     # cmake target 必需
│   ├── libnvblas.so.13.4.1.3
│   └── libcudart.so.13.0.96        # 运行时必需（最容易漏）
├── include/
│   ├── cublas*.h                   # 编译头文件
│   └── cccl/                       # cub / thrust / libcudacxx
├── bin/cicc                        # nvvm 编译器前端
└── nvvm-lib/libnvvm.so*            # nvvm 运行库
```

**制作方法**（从一台已能跑 CUDA 的容器导出，必须用 `tar -ch` 解引用符号链接）：

```bash
BUNDLE=/path/to/amm/deploy/cuda-bundle
mkdir -p $BUNDLE/{lib64,include,bin,nvvm-lib}

# cublas + cudart（-h 必须加，否则只拷到断掉的符号链接）
docker exec AMM sh -c "cd /usr/local/cuda-13.2/lib64 && tar -chf - \
  libcublas.so.13.4.1.3 libcublasLt.so.13.4.1.3 libnvblas.so.13.4.1.3" \
  | tar -xf - -C $BUNDLE/lib64

# cudart 若源容器是 CUDA 13.0，版本号不同但要一起带上
docker exec <某CUDA容器> sh -c "cd /usr/local/cuda-13.0/lib64 && tar -chf - \
  libcudart.so.13.0.96" | tar -xf - -C $BUNDLE/lib64

# 头文件
docker exec AMM sh -c "cd /usr/local/cuda-13.2/include && tar -chf - ." \
  | tar -xf - -C $BUNDLE/include

# nvvm
docker exec AMM sh -c "cd /usr/local/cuda-13.2/nvvm && tar -chf - bin" | tar -xf - -C $BUNDLE
docker exec AMM sh -c "cd /usr/local/cuda-13.2/nvvm/lib64 && tar -chf - ." \
  | tar -xf - -C $BUNDLE/nvvm-lib
```

Dockerfile 会自动补 `.so` / `.so.N` 两级符号链接（组件包只带真实版本文件）。

---

## 六、模板文件

**位置**：`/boot/config/plugins/dockerMan/templates-user/my-AMM.xml`
**仓库副本**：`deploy/unraid/my-AMM.xml`

模板里包含 17 个配置项（端口 / 3 个路径 / 13 个环境变量），Docker 页据此可一键重建容器。

改端口/路径/环境变量的流程：

1. 改 `/boot/config/plugins/dockerMan/templates-user/my-AMM.xml`
2. 在 Unraid Docker 页点该容器 → Edit → 应用（Unraid 会按新模板重建）

> 改模板前先备份：`cp my-AMM.xml my-AMM.xml.bak-$(date +%Y%m%d-%H%M%S)`

### 6.1 推荐用同步脚本，别手改

```bash
cd /path/to/amm

# 只同步模板（自动备份 + 校验端口一致性）
bash deploy/sync-unraid-template.sh

# 同步模板并按模板重建容器（会中断运行中的实例）
bash deploy/sync-unraid-template.sh --recreate
```

脚本做三件手改容易漏的事：

1. **自动备份**旧模板为 `my-AMM.xml.bak-<时间戳>`
2. **校验 `<WebUI>` 端口与 `Type="Port"` 的 Config 值一致** —— 不一致会导致
   Docker 页的「WebUI」按钮连到错误端口（曾把 AMM 写成 `[PORT:80]` 而实际是 60008）
3. `--recreate` 时**从模板解析参数重建容器，并注入 dockerMan 标签**

### 6.2 三个 dockerMan 标签缺一不可

Unraid Docker 页靠容器上的 label 识别「标准受管容器」。**没有这些标签，
容器会显示为「孤儿」：没图标、没 WebUI 按钮、不能用界面启停/编辑/重建。**

```bash
docker inspect AMM --format '{{json .Config.Labels}}' | python3 -m json.tool
```

| 标签 | 作用 | 期望值 |
|---|---|---|
| `net.unraid.docker.managed` | 标记为受管容器 | `dockerman` |
| `net.unraid.docker.webui` | 容器卡片上的「WebUI」按钮 | `http://[IP]:[PORT:60008]` |
| `net.unraid.docker.icon` | 图标 URL | logo.png 地址 |

> **踩过的坑**：AMM 最初是手工 `docker run` 起的，三个标签全空。
> 它在 Docker 页里能跑但点不了 WebUI、也没法用界面重建，改配置只能敲命令行。
> 同机的 ACD / LMStudio 都是 `managed=dockerman`，对比之下问题很明显。

### 6.3 参照物：同机 ACD 容器的正确姿势

```bash
docker inspect ACD --format 'managed={{index .Config.Labels "net.unraid.docker.managed"}}'
# → managed=dockerman

grep -o "PORT:[0-9]*" /boot/config/plugins/dockerMan/templates-user/my-acd.xml
# → PORT:60009     ← 注意是宿主端口，不是容器内的 80
```

模板 `<WebUI>` 里的 `[PORT:xxxxx]` **必须写宿主映射端口**，
Unraid 会把它替换成实际 IP 拼成可点击链接。

---

## 七、验收四关

```bash
# ① 页面 HTTP
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:60008/        # 期望 200

# ② 数据接口
curl -s http://127.0.0.1:60008/api/health                                # 期望 {"status":"ok"}

# ③ Unraid 识别
docker inspect AMM --format '{{index .Config.Labels "net.unraid.docker.managed"}}'
# 期望 dockerman

# ④ 端口监听
ss -ltn | grep ":60008 "                                                 # 期望有输出
```

额外检查：

```bash
docker inspect AMM --format '{{.State.Health.Status}}'          # healthy
docker inspect AMM --format '{{.HostConfig.RestartPolicy.Name}}' # unless-stopped
curl -s http://127.0.0.1:60008/api/settings | grep -o '"version":"[^"]*"'
```

### 功能验收（不只是页面能开）

```bash
# 真实启动一个模型并推理
curl -s -X POST http://127.0.0.1:60008/api/instances/launch \
  -H 'Content-Type: application/json' \
  -d '{"model_type":"embedding","engine_type":"llama_cpp",
       "model_file":"Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
       "parameters":{"embeddings":true},"model_uid":"verify"}'
# 期望返回 ready=true

curl -s -X POST http://127.0.0.1:60008/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"verify","input":"test"}'
# 期望返回 1024 维向量

# 确认真的跑在 GPU 上（不能只看 --list-devices）
nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader
```

---

## 八、升级流程

```bash
ssh root@<YOUR_HOST>
cd /path/to/amm

# 生产目录有本地改动（models_config.yaml / data / templates/custom），先 stash
git stash -u
git pull origin v1.0 --ff-only
git stash pop

docker restart AMM
sleep 20
curl -s http://127.0.0.1:60008/api/health
curl -s http://127.0.0.1:60008/api/settings | grep -o '"version":"[^"]*"'
```

> **不要** `git checkout .`，会冲掉节点名、自建模版等本地配置。
> 升级后版本号没变 = 旧进程还在跑，`docker restart AMM`。

---

## 九、故障处理

| 症状 | 原因 | 处理 |
|---|---|---|
| `docker restart amm` 报 No such container | 容器名是大写 `AMM` | 用 `docker restart AMM` |
| 容器 Up 但页面打不开 | 端口被占，映射静默失效 | `ss -ltn \| grep :60008` 复核 |
| 模型启动报 `libcudart.so.13 not found` | 镜像缺运行库 | 见 5.3，重建镜像并带上 libcudart |
| 模型启动报 `libcublas.so.13 not found` | 同上 | 同上 |
| nvcc 报 `--error 0x7f` | 缺 nvvm(cicc) | 组件包补 `bin/cicc` + `nvvm-lib/` |
| `CUDA::cublas target not found` | cublas 库不全 | 需要 `.so` / `.so.13` / 真实版本三件套 |
| GPU 上看不到模型进程 | ggml 动态库缺失，静默退回 CPU | 查 `/usr/local/bin/libggml-cuda.so` 是否存在 |
| 容器挂了没人管 | 重启策略是 `no` | `docker update --restart unless-stopped AMM` |

完整排错见 `docs/manual/08-故障排查.md`。

---

## 十、日常运维

```bash
# 状态
docker ps --filter name=AMM
docker inspect AMM --format 'health={{.State.Health.Status}} policy={{.HostConfig.RestartPolicy.Name}}'

# 日志
docker exec AMM tail -50 /amm/logs/amm_server.log
docker exec AMM tail -50 /amm/logs/<实例UID>_server.log

# 显存
nvidia-smi
docker exec AMM ps aux | grep llama-server

# 磁盘
df -h /mnt/<disk>
docker exec AMM du -sh /amm/logs /amm/data
```

日志已自动轮转（50MB × 5），不会写满磁盘。
