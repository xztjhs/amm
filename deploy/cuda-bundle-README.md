# CUDA 组件包（cuda-bundle）

供 `deploy/Dockerfile.local-cuda` 在构建期 COPY 进镜像，替代「构建时下载 NVIDIA RPM」。
原因：从 NVIDIA 源拉 158MB 的 cublas 反复 SSL 断流（curl exit 56），构建十次九次失败。

**这个包不只是运行时，还包含完整编译器** —— 否则用户在 Web 上装 llama.cpp 的
cuda flavor 必然失败（详见 `docs/CUDA与引擎安装避坑指南.md`）。

## 结构

```
cuda-bundle/                    约 1GB，二进制产物，不入 git
├── lib64/*.so*           538M  cublas / cublasLt / nvblas / cudart（运行时）
├── include/*.h            34M  cuda / cublas 头文件 + cccl/
├── bin/cicc               78M  nvvm 前端（nvcc 调它生成 cubin）
├── nvvm-lib/*            185M  nvvm 运行库
├── nvcc-bin/*            135M  nvcc / nvlink / ptxas / cudafe++ / fatbinary / bin2c
├── targets-include/*      32M  targets/x86_64-linux/include（nvcc 默认头文件路径）
├── targets-lib/*.a       2.5M  libcudart_static.a + libcudadevrt.a
└── stubs/libcuda.so      74K  driver stub（cmake 的 CUDA::cuda_driver 必需）
```

### 每个目录为什么必需

| 目录 | 缺了会怎样 |
|---|---|
| `lib64` | 运行期 `libcublas.so.13: cannot open shared object file` |
| `include` | 编译期 `cublas_v2.h: No such file` |
| `bin/cicc` | nvcc 报 `/usr/local/cuda-13.2/nvvm/bin/cicc: No such file` |
| `nvvm-lib` | cicc 启动失败 |
| `nvcc-bin` | 根本没有编译器，装 cuda 引擎直接报「未找到 nvcc」 |
| `targets-include` | `cuda_runtime.h: No such file or directory` |
| `targets-lib` | 链接期 `cannot find -lcudart_static` / `-lcudadevrt` |
| `stubs` | cmake 报 `Target "ggml-cuda" links to: CUDA::cuda_driver` |

⚠️ **只拷 `nvcc` 一个文件是跑不起来的。** nvcc 只是驱动，真正干活的是
`ptxas`（汇编）、`nvlink`（链接）、`cudafe++`（C++ 前端）、`fatbinary`（打包），
以及 `bin/cicc`（在 nvvm 目录）。少任何一个都会以奇怪的错误中断。

## 生产 bundle

**不要手敲 docker cp**，用脚本（含完整性前置校验）：

```bash
# 从一个 CUDA 组件完整的容器导出（默认容器名 AMM）
bash deploy/export-cuda-bundle.sh

# 指定容器与输出目录
bash deploy/export-cuda-bundle.sh AMM /path/to/amm/deploy/cuda-bundle
```

脚本流程：
1. 校验源容器是否有 nvcc / ptxas / nvlink / cudafe++ / cicc / cudart / 头文件 / 静态库
2. 分 6 步导出（运行时库 → 头文件 → nvvm → nvcc 全家桶 → targets 头 → targets 静态库）
3. 有 cccl 就一并导出
4. 打印各目录体积与文件数

若手头没有完整 CUDA 容器，可先起一个临时容器取件：

```bash
docker run --rm -d --name cuda-src nvidia/cuda:13.2.0-devel-rockylinux9 sleep 3600
bash deploy/export-cuda-bundle.sh cuda-src /tmp/cuda-bundle
docker rm -f cuda-src
```

## 使用

已内置在 `Dockerfile.local-cuda`，构建时自动 COPY：

```bash
cd deploy
cp ../backend/requirements.txt ./requirements.txt
docker build -f Dockerfile.local-cuda -t amm:rocky9-cu132 .
```

Dockerfile 会：
- COPY 上述 7 个目录到 `/usr/local/cuda-13.2/` 对应位置
- 把 `lib64` 与 `targets/x86_64-linux/lib` 做成互链（nvcc 两种布局都会找）
- 把 `include` 的条目软链进 `targets/x86_64-linux/include`
- **构建期编译一个最小 .cu 自检**，编不过直接构建失败

也可在运行期从挂载的 bundle 补齐（旧方式，保留兼容）：

```bash
bash deploy/cuda_components.sh /usr/local/cuda-13.2
```

## 版本

CUDA 13.2（`cuda_13.2.r13.2/compiler.37953736_0`）。
换 CUDA 版本时需要：改 `Dockerfile.local-cuda` 里的 `cuda-13.2` 路径与
`cublas` 的版本号后缀，并重新导出 bundle。
