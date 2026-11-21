# CUDA 与引擎安装避坑指南

> 2026-11-18 整理。记录在 AMM 上安装推理引擎（重点 llama.cpp）时踩过的坑，
> 以及安装包（镜像 + cuda-bundle）为绕开这些坑做的设计。
> 面向后续接手的人，也方便未来自己快速回忆。

---

## 一、一句话结论

**精简 CUDA 镜像只有「运行时」，没有「编译器」。** 想在里面编译任何 CUDA 程序
（llama.cpp / vLLM），必须补齐三样东西，缺一样都会以看似无关的错误失败：

| 缺什么 | 报错长什么样 | 补什么 |
|---|---|---|
| nvcc 编译器本体 | `未找到 nvcc` | `cuda-nvcc` 的 `bin/nvcc` 等 |
| cudart 静态库 | `cannot find -lcudart_static` / `-lcudadevrt` | `libcudart_static.a`、`libcudadevrt.a` |
| targets 头文件目录 | `fatal error: cuda_runtime.h: No such file` | `targets/x86_64-linux/include/` |
| driver stub | `Target "ggml-cuda" links to: CUDA::cuda_driver` | `stubs/libcuda.so`（`cuda-driver-devel`） |

**关键**：这几样是**逐层暴露**的。你补了第一个才会看到第二个，很容易以为「又出了新问题」，
实际上是同一个根因的几段。实测顺序就是：nvcc → 静态库 → 头文件 → driver stub。

---

## 二、坑 1：llama.cpp v1.0.0 起，内嵌 Web UI 让构建必然失败

### 现象

```
UI: missing required asset(s):
    workbox[hash].js
gmake[3]: *** [tools/ui/CMakeFiles/llama-ui-assets.dir/build.make:71] Error 1
gmake[1]: *** [tools/server/CMakeFiles/llama-server.dir/rule] Error 2
```

进度卡在 45%（CPU）/ 30%（CUDA），看起来像 llama-server 编不出来。

### 根因

v1.0.0 的 llama.cpp 给 `llama-server` 内嵌了一个 Web UI：

1. `tools/server/CMakeLists.txt` 里 `add_dependencies(${TARGET} llama-ui-assets)` 是
   **硬编码的**，不受 `LLAMA_BUILD_UI` 开关控制
2. 构建时从 HF Bucket 拉 `dist.tar.gz`
3. `scripts/ui-assets.cmake` 的 `ui_validate_assets()` 强制要求 `workbox[hash].js`
4. 但官方发布的压缩包里**没有这个文件** → 校验失败 → Error 1

所以关掉 `LLAMA_BUILD_UI` 也挡不住，因为依赖是写死的。

### 解法

AMM 自己有前端（`/amm/frontend`），根本不需要 llama.cpp 的 UI。在 cmake 配置里显式关闭
预构建 UI，让它走「无资源」的 fallback 分支（那里只 WARNING 不报错）：

```python
cfg += ["-DLLAMA_BUILD_UI=OFF", "-DLLAMA_USE_PREBUILT_UI=OFF"]
```

代码位置：`backend/core/engine_catalog.py` → `_build_llama()`

### ⚠️ 最容易踩的陷阱

**这个开关只对「全新的 build 目录」有效。** 如果 `build-cpu/` 之前用旧配置生成过，
里面的 `llama-ui-assets` 目标会残留，重装仍然失败。

判断方法：
```bash
docker exec AMM grep -E "LLAMA_BUILD_UI|LLAMA_USE_PREBUILT_UI" \
  /opt/amm-engines/llama_cpp/<ver>-<flavor>/build-<flavor>/CMakeCache.txt
# 应该都是 OFF
```

若缓存是 `OFF` 但仍失败，**删掉整个 build 目录重装**：
```bash
docker exec AMM rm -rf /opt/amm-engines/llama_cpp/v1.0.0-cpu/build-cpu
```

实测：CPU 版第一次失败就是残留导致的，删 build 目录后一次通过（0→100%）。

---

## 三、坑 2：CUDA 工具链三层缺失

### 2.1 没有 nvcc

```
失败: 未找到 nvcc，无法编译 CUDA 版 llama.cpp。
```

镜像里 `/usr/local/cuda-13.2/` 下有 `include/`、`lib64/`、`nvvm/`、`targets/`，
唯独**没有 `bin/`** —— 只有运行时。

### 2.2 补了 nvcc，链接又失败

```
/usr/bin/ld: cannot find -lcudadevrt
/usr/bin/ld: cannot find -lcudart_static
```

`nvcc` 编译 CUDA 程序时默认链接 `libcudart_static.a`（静态）和 `libcudadevrt.a`
（设备运行时）。这两个在 `cuda-cudart-devel` 包里，运行时包不带。

### 2.3 再往后，头文件找不到

```
/usr/local/cuda-13.2/bin/../targets/x86_64-linux/include/cuda_runtime.h:82:10:
fatal error: crt/host_config.h: No such file or directory
```

**关键细节**：nvcc 默认在 `targets/x86_64-linux/include/` 找头文件，
但精简包只把文件放在 `include/`（少了一层 targets 路径）。同理，
nvcc 链接时在 `targets/x86_64-linux/lib/` 找库，而库实际在 `lib64/`。

### 2.4 最后还差 driver stub

```
CMake Error at ggml/src/ggml-cuda/CMakeLists.txt:181 (target_link_libraries):
  Target "ggml-cuda" links to:
    CUDA::cuda_driver
  but the target was not found.
```

cmake 的 FindCUDAToolkit 需要 `libcuda.so`（driver API 的导入库）。
它**不是**真实驱动 —— 真实驱动 `libcuda.so.1` 由宿主机的 NVIDIA 运行时
在容器启动时注入。编译期只需要一个 stub 满足链接即可。

来源：`cuda-driver-devel` 包，装在
`targets/x86_64-linux/lib/stubs/libcuda.so`（74KB 的 ELF）。

```bash
curl -fsSL -o /tmp/cd.rpm \
  https://developer.download.nvidia.com/compute/cuda/repos/rhel9/x86_64/cuda-driver-devel-13-2-13.2.86-1.x86_64.rpm
rpm2cpio /tmp/cd.rpm | cpio -idm     # 需要 cpio
# 产物：usr/local/cuda-13.2/targets/x86_64-linux/lib/stubs/libcuda.so
```

⚠️ 容器里可能没有 `cpio`（实测 AMM 容器就没有）。此时用 Python 解析 cpio
可直接提取（cpio newc 格式，110 字节头 + 文件名 + 4 字节对齐的数据）：
见 `deploy/export-cuda-bundle.sh` 注释；或临时起一个有 cpio 的容器解。

### 解法

把两个布局做成**互链**，任一种都能编译：

```bash
C=/usr/local/cuda-13.2
TI=$C/targets/x86_64-linux/include
L=$C/targets/x86_64-linux/lib

# 库：lib64 <-> targets/lib 双向软链
for f in libcudart_static.a libcudadevrt.a libcudart.so libcudart.so.13; do
  [ -e "$L/$f" ] && [ ! -e "$C/lib64/$f" ] && ln -sf "$L/$f" "$C/lib64/$f"
  [ -e "$C/lib64/$f" ] && [ ! -e "$L/$f" ] && ln -sf "$C/lib64/$f" "$L/$f"
done

# 头文件：targets/include 缺什么从 include 软链过来
for d in $C/include/*; do
  b=$(basename "$d")
  # 注意：空目录要先删，否则 ln 会跳过（踩过）
  [ -d "$TI/$b" ] && [ -z "$(ls -A "$TI/$b")" ] && rmdir "$TI/$b"
  [ -e "$TI/$b" ] || ln -sf "$d" "$TI/$b"
done
```

⚠️ **`crt` 目录的坑**：若 `targets/include/crt` 已存在但是空目录，
上面的 `[ -e ]` 判断会认为「已存在」而跳过，导致 `host_config.h` 仍找不到。必须先 `rmdir`。

### rpm 直接装会失败

```
error: unpacking of archive failed on file /usr/local/cuda-13.2/include:
cpio: File from package already exists as a directory in system
```

精简镜像的 `include` 往往是已存在的目录/挂载点，rpm 认为冲突而整体回滚。
绕过办法：**解包后逐文件拷贝，不覆盖已有文件**

```bash
rpm2cpio pkg.rpm > /tmp/p.rpm2cpio
mkdir -p /tmp/ex && cd /tmp/ex && cpio -idm < /tmp/p.rpm2cpio
find . -type f -o -type l | while read f; do
  d="/${f#./}"
  mkdir -p "$(dirname "$d")"
  [ -e "$d" ] || cp -a "$f" "$d"     # 保留镜像原有运行时
done
```

---

## 四、坑 3：llama.cpp 上游版本全是 prerelease

### 现象

Settings → 推理引擎管理里冒出几十个 `b12475`、`b12474`…，挑不动。

### 根因

llama.cpp 的每个 CI 构建（`b#####`）在 GitHub 上都标记为 `prerelease: true`。
官方正式版只发 `vX.Y.Z`（目前就 `v1.0.0`、`v0.9.0`、`v0.8.0`）。

原实现有两个叠加问题：
1. 只取最新 25 条 release —— 这些**全是** b 版本，一条正式版都没有
2. 过滤后为空就「回退成不过滤」，于是 pre 版刷屏

### 解法

- **逐页翻**（每页 100，最多 3 页），凑够正式版即停
- 过滤后为空时**返回空列表**，而不是回退成 pre 列表
- 本地已安装的 pre 版仍要显示（标「本地」标签），否则无法卸载
- `AMM_ENGINE_INCLUDE_PRERELEASE=1` 可临时放开

代码位置：`backend/core/engine_catalog.py` → `_upstream_github()` / `_filter_stable()`

### 顺带一个变量遮蔽 bug

写「把本地已装版本注入上游列表」那段时，用了：

```python
for inst in inst:          # ← 把列表变量覆盖了
    ...
"installed": inst          # ← 变成循环最后一个元素，不再是 list
```

前端拿到 dict 当场报 `TypeError: string indices must be integers`。
教训：**循环变量别和容器同名**。

---

## 五、坑 4：实例端口不在宿主机上

### 现象

```bash
curl http://127.0.0.1:18082/health     # 空响应，看起来像实例挂了
```

但 AMM 页面显示 running，进程也在。

### 根因

实例端口（18081-18189、18100+）只监听**容器网络**，宿主机没有映射。
宿主机上 curl 当然连不上。

### 正确验证方式

```bash
docker exec AMM curl -s http://127.0.0.1:18082/health
docker exec AMM curl -s http://127.0.0.1:18082/v1/embeddings \
  -H 'Content-Type: application/json' -d '{"input":"test","model":"x"}'
```

我因为这条差点把正常工作的实例判成故障。

---

## 六、坑 5：日志把磁盘写爆

### 现象

`logs/amm_server.out` 长到 240MB+，约 150MB/天。89.6 万行里 89.6 万行是访问日志。

### 根因（两层）

1. `deploy/docker-entrypoint.sh` 用 `nohup python3.11 server.py >> amm_server.out` 启动
   —— 这个 `.out` **不轮转**
2. root logger 的 `StreamHandler` 是 INFO 级，`aiohttp.access` 的每条访问日志都进 stdout
3. 前端默认 0.5s 轮询 3 个接口 → 6 请求/秒 → 全部落到 `.out`

### 解法

- `amm_server.log` 保持 INFO 全量 + RotatingFileHandler（排障主入口）
- stdout/.out 降到 **WARNING**（只留异常回溯），`AMM_STDOUT_LOG_LEVEL` 可调
- 访问日志独立 `access.log`，20MB×2 轮转，`propagate=False`
- 过滤器丢掉健康轮询（`/api/system`、`/api/instances`、
  `/api/bridge/diffusers/status`、`/api/health`），保留 `/v1/*` 与非 GET
- `AMM_ACCESS_LOG=0` 可彻底关闭

### ⚠️ 这里有个隐蔽陷阱

第一版过滤器只看 `record.args` 里的 request 对象，**完全没生效**。
因为 aiohttp 的实现是：

```python
self.logger.info(self._log_format % tuple(values), extra=extra)
```

**消息已经格式化好写进 `record.msg`，`record.args` 是空的。**

正确做法：优先用 `args`，兜底用正则从 `"GET /path HTTP/1.1"` 抠 method/path：

```python
_REQ_RE = re.compile(r'"([A-Z]+)\s+(\S+)\s+HTTP/[\d.]+"?')
```

实测：改完后 40 秒内 `access.log` 和 `.out` 都是 0 字节（此前 40 秒涨 60KB）。

---

## 七、坑 6：分支/部署不一致

### 现象

代码改了、也 push 了，但部署端拉下来还是旧的。

### 根因

**部署副本跟踪的分支是 `v1.0`，不是 `master`。** 两者曾相差 94 个 commit。

### 约定（务必遵守）

- `master` 是唯一开发主线
- 每次改完：`git branch -f v1.0 master && git push origin master v1.0` 两个都推
- 部署端 `git pull --ff-only origin v1.0`

### 另一个坑：部署端配置有本地改动

`backend/config/models_config.yaml` 在生产目录常被程序重写过（yaml 格式变化、`node_name` 等）。
`git pull` 会被挡。处理顺序：

```bash
cp backend/config/models_config.yaml /path/models_config.deployed.bak-$(date +%Y%m%d-%H%M%S)
git checkout -- backend/config/models_config.yaml
git pull --ff-only origin v1.0
# 然后把需要保留的本地项（如 node_name）补回仓库配置并提交
```

---

## 八、安装包设计（为什么这么组织）

### 演进过程

| 阶段 | 做法 | 问题 |
|---|---|---|
| v1 | `Dockerfile.unraid` 构建期下载 NVIDIA RPM | 拉 158MB cublas 反复 SSL 断流（exit 56），十次九次失败 |
| v2 | 改 `Dockerfile.local-cuda`，COPY 本地 cuda-bundle | 构建稳定了，但 bundle **只有运行时**（cublas+cicc），没编译器 → 装 cuda 引擎必失败 |
| v3（当前） | bundle 补齐 nvcc 全套 + targets 布局 | 镜像自带完整工具链，装引擎开箱即用 |

### 当前 bundle 结构

```
cuda-bundle/                       ~1GB，二进制产物，不入 git
├── lib64/*.so*             538M   cublas / cublasLt / nvblas / cudart（运行时）
├── include/*.h              34M   cuda / cublas 头文件 + cccl
├── bin/cicc                 78M   nvvm 前端（nvcc 调它生成 cubin）
├── nvvm-lib/*              185M   nvvm 运行库
├── nvcc-bin/*              135M   nvcc / nvlink / ptxas / cudafe++ / fatbinary / bin2c
├── targets-include/*        32M   targets/x86_64-linux/include（nvcc 默认搜索路径）
├── targets-lib/*.a         2.5M   libcudart_static.a + libcudadevrt.a
└── stubs/libcuda.so        74K    driver stub（cmake CUDA::cuda_driver 必需）
```

### 生产 bundle 的方法

用 `deploy/export-cuda-bundle.sh`，不要手敲 docker cp：

```bash
bash deploy/export-cuda-bundle.sh AMM              # 默认容器 AMM
bash deploy/export-cuda-bundle.sh AMM /path/to/bundle
```

脚本会先**校验源容器完整性**（缺哪样直接报出来），再分 6 步导出。
手敲 docker cp 最大的问题是会漏 `ptxas`/`nvlink`/`cudafe++` ——
只拷 `nvcc` 一个文件是跑不起来的。

### 构建镜像

```bash
cd deploy
cp ../backend/requirements.txt ./requirements.txt    # Dockerfile 要 COPY 它
docker build -f Dockerfile.local-cuda -t amm:rocky9-cu132 .
```

### 构建期自检

Dockerfile 里有一层专门验证工具链：

```dockerfile
RUN nvcc --version; \
    printf '__global__ void k(){}\nint main(){k<<<1,1>>>();return 0;}\n' > /tmp/t.cu; \
    nvcc -o /tmp/t /tmp/t.cu; ls -l /tmp/t
```

**编不过就在构建期失败**，不会等到用户在页面上点「安装引擎」才发现。
这一层是这次最重要的改进 —— 把故障从「用户使用时」提前到「打包时」。

### 验证方法（离线可复现）

用一个干净容器只挂 bundle，验证能否编译：

```bash
docker run --rm -v $PWD/cuda-bundle:/bundle:ro rockylinux:9 bash -c '
  dnf install -y gcc gcc-c++ >/dev/null 2>&1
  mkdir -p /usr/local/cuda-13.2/{bin,lib64,include,ready}
  cp -r /bundle/lib64/* /usr/local/cuda-13.2/lib64/
  cp -r /bundle/include/* /usr/local/cuda-13.2/include/
  cp -r /bundle/nvcc-bin/* /usr/local/cuda-13.2/bin/
  ... (按 Dockerfile 补齐布局)
  printf "__global__ void k(){}\nint main(){k<<<1,1>>>();return 0;}\n" > /tmp/t.cu
  nvcc -o /tmp/t /tmp/t.cu && echo OK
'
```

实测结果：全新 `rockylinux:9` + bundle → nvcc 编译通过，输出 1MB 可执行文件。

### 端到端验证（最有力的一条）

用新镜像在干净容器里**完整编译 llama.cpp CUDA 版**，已验证通过：

```bash
docker run -d --name verify \
  -v /path/to/llama.cpp/src:/tmp/src:ro \
  amm:rocky9-cu132 bash -c '
    cmake -S /tmp/src -B /tmp/build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON \
      -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF
    cmake --build /tmp/build -j 32 --target llama-server'
```

实测：`CONFIG_RC=0` → 编译到 100% → `BUILD_RC=0`，
产出 `llama-server` 与 `libggml-cuda.so.1.0.0`（77MB）。

> 注：验证容器里 `llama-server --version` 会报 `libcuda.so.1: cannot open`，
> 这是**正常**的 —— 该容器没挂 GPU，真实驱动由 NVIDIA 运行时注入。
> 恰恰说明 stub 只服务于编译，不会污染运行期。

---

## 九、装好之后怎么验

```bash
# 1) 两个版本都在
docker exec AMM ls /opt/amm-engines/llama_cpp/

# 2) 版本号
docker exec AMM /opt/amm-engines/llama_cpp/v1.0.0-cpu/bin/llama-server --version
docker exec AMM /opt/amm-engines/llama_cpp/v1.0.0-cuda/bin/llama-server --version

# 3) 设备识别（关键：cuda 版必须能看到 GPU）
docker exec AMM /opt/amm-engines/llama_cpp/v1.0.0-cpu/bin/llama-server  --list-devices   # 应为 (none)
docker exec AMM /opt/amm-engines/llama_cpp/v1.0.0-cuda/bin/llama-server --list-devices   # 应列出 CUDA0: RTX ...

# 4) 真实推理（注意：要在容器内 curl，实例端口不在宿主机）
#    先在页面上启动一个实例，然后：
docker exec AMM curl -s http://127.0.0.1:18082/v1/embeddings \
  -H 'Content-Type: application/json' -d '{"input":"hello","model":"x"}'

# 5) 确认真的用了 GPU（日志里应有这些字样）
docker exec AMM grep -iE "using device CUDA0|offloading.*to GPU" logs/embedding_server.log
```

### 实测基线（2026-11-18）

| 项 | cpu | cuda |
|---|---|---|
| 大小 | 370.4 MB | 906.5 MB |
| `--list-devices` | (none) | `CUDA0: NVIDIA RTX A5000 (24112 MiB)` |
| `/v1/embeddings` | ✅ 返回向量 | ✅ 返回向量 |
| GPU 卸载 | 不适用 | ✅ `offloading output layer to GPU` |

---

## 十、显存注意事项

24GB 卡上**不要同时**跑 ASR + TTS + OCR + Reranker，会 OOM：

```
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 874.47 MiB on device 0:
cudaMalloc failed: out of memory
```

TTS 单独跑正常。需要共存时用 CPU flavor，或配置显存自动释放策略。

---

## 附录：相关文件索引

| 文件 | 作用 |
|---|---|
| `backend/core/engine_catalog.py` | 引擎目录、版本过滤、下载编译、CUDA 工具链自动补齐 |
| `deploy/Dockerfile.local-cuda` | 生产镜像（bundle 方式，构建期自检） |
| `deploy/Dockerfile.unraid` | 备用镜像（RPM 下载方式，网络好时可用） |
| `deploy/export-cuda-bundle.sh` | 导出 cuda-bundle（含完整性校验） |
| `deploy/cuda_components.sh` | 运行期从 bundle 补齐 CUDA 组件 |
| `deploy/docker-entrypoint.sh` | 容器启动（supervisor + 日志重定向） |
| `backend/server.py` | 日志分流、访问日志降噪 |
| `backend/tests/test_access_log_filter.py` | 锁死访问日志过滤行为 |
