#!/usr/bin/env bash
# =============================================================================
# export-cuda-bundle.sh — 从一台「CUDA 组件齐全」的容器导出离线组件包
# =============================================================================
# 用途：AMM 镜像（deploy/Dockerfile.local-cuda）在构建期不下载 NVIDIA RPM，
#       而是 COPY 本地 cuda-bundle/。这个脚本负责把 bundle 生产出来。
#
# 为什么需要它：
#   1) 从 NVIDIA 源拉 158MB 的 cublas 反复 SSL 断流（curl exit 56），
#      构建十次九次失败 -> 二进制入包，构建期零下载
#   2) 精简 CUDA 镜像只有运行时，缺编译器三件套，导致 Web 上装
#      llama.cpp 的 cuda flavor 必然失败：
#        - 无 nvcc                    -> 编译无法开始
#        - 无 libcudart_static.a      -> cannot find -lcudart_static
#        - 无 targets/.../include     -> cuda_runtime.h: No such file
#
# 前置：宿主机上存在一个 CUDA 组件完整的容器（默认名 AMM）。
#       若手头没有，可先 docker run 一个 nvidia/cuda:13.2-devel 临时容器。
#
# 用法：
#   bash deploy/export-cuda-bundle.sh                    # 默认容器 AMM
#   bash deploy/export-cuda-bundle.sh my_cuda_container   # 指定容器名
#   bash deploy/export-cuda-bundle.sh AMM /path/to/bundle # 指定输出目录
#
# 产出结构（约 1GB，不进 git，放在部署端）：
#   cuda-bundle/
#   ├── lib64/*.so*             cublas / cublasLt / nvblas / cudart（运行时）
#   ├── include/*.h             cuda / cublas 头文件
#   ├── bin/cicc                nvvm 前端
#   ├── nvvm-lib/*              nvvm 运行库
#   ├── nvcc-bin/*              nvcc 编译器全家桶
#   ├── targets-include/*       targets/x86_64-linux/include
#   └── targets-lib/*.a         libcudart_static.a + libcudadevrt.a
# =============================================================================
set -euo pipefail

CONTAINER="${1:-AMM}"
BUNDLE="${2:-$(cd "$(dirname "$0")" && pwd)/cuda-bundle}"
CUDA_VER="${CUDA_VER:-13.2}"
CROOT="/usr/local/cuda-${CUDA_VER}"

say() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "找不到 docker"
docker inspect "$CONTAINER" >/dev/null 2>&1 || die "容器不存在: $CONTAINER"

say "容器=$CONTAINER  CUDA=$(echo "$CUDA_VER" | tr . -)  输出=$BUNDLE"

# ---- 前置校验：源容器必须真有这些东西，否则导出的包是残缺的 ----
say "校验源容器 CUDA 组件完整性"
MISSING=0
for p in bin/nvcc bin/ptxas bin/nvlink bin/cudafe++ bin/fatbinary \
         nvvm/bin/cicc lib64/libcudart.so.13 \
         targets/x86_64-linux/include/cuda_runtime.h; do
    if ! docker exec "$CONTAINER" test -e "$CROOT/$p"; then
        printf '  缺少 %s\n' "$p"
        MISSING=1
    fi
done
# 静态库可能只在 lib64，也可能只在 targets/lib，二者有其一即可
if ! docker exec "$CONTAINER" sh -c \
     "test -e $CROOT/lib64/libcudart_static.a || test -e $CROOT/targets/x86_64-linux/lib/libcudart_static.a"; then
    printf '  缺少 libcudart_static.a\n'; MISSING=1
fi
if ! docker exec "$CONTAINER" sh -c \
     "test -e $CROOT/lib64/libcudadevrt.a || test -e $CROOT/targets/x86_64-linux/lib/libcudadevrt.a"; then
    printf '  缺少 libcudadevrt.a\n'; MISSING=1
fi
# driver stub（cmake 的 CUDA::cuda_driver 目标必需）
if ! docker exec "$CONTAINER" sh -c \
     "test -e $CROOT/targets/x86_64-linux/lib/stubs/libcuda.so || test -e $CROOT/lib64/stubs/libcuda.so"; then
    printf '  缺少 libcuda.so（driver stub，来自 cuda-driver-devel）\n'; MISSING=1
fi
[ "$MISSING" -eq 0 ] || die "源容器 CUDA 组件不全，请先补装（见 docs/CUDA与引擎安装避坑指南.md）"

mkdir -p "$BUNDLE"/{lib64,include,bin,nvvm-lib,nvcc-bin,targets-include,targets-lib,stubs}

# ---- 1) 运行时动态库（-h 解引用符号链接，否则只拷到软链）----
say "1/6 导出运行时动态库 -> lib64/"
docker exec "$CONTAINER" sh -c \
  "cd $CROOT/lib64 && tar -chf - *.so *.so.* 2>/dev/null" \
  | tar -xf - -C "$BUNDLE/lib64"

# ---- 2) 头文件 ----
say "2/6 导出头文件 -> include/"
docker exec "$CONTAINER" sh -c \
  "cd $CROOT/include && tar -chf - . 2>/dev/null" \
  | tar -xf - -C "$BUNDLE/include"

# ---- 3) nvvm（cicc 前端 + 运行库）----
say "3/6 导出 nvvm -> bin/cicc + nvvm-lib/"
docker exec "$CONTAINER" sh -c \
  "cd $CROOT/nvvm/bin && tar -chf - . 2>/dev/null" \
  | tar -xf - -C "$BUNDLE/bin"
docker exec "$CONTAINER" sh -c \
  "cd $CROOT/nvvm/lib64 && tar -chf - . 2>/dev/null" \
  | tar -xf - -C "$BUNDLE/nvvm-lib"

# ---- 4) nvcc 编译器全家桶 ----
# 注意：不能只拷 nvcc 一个文件。nvcc 是驱动，真正干活的是
#       cicc(cubin 前端,在 nvvm/bin) / ptxas(汇编) / nvlink(链接) /
#       cudafe++(C++ 前端) / fatbinary(打包)。少一个就报奇怪的错。
say "4/6 导出 nvcc 编译器 -> nvcc-bin/"
docker exec "$CONTAINER" sh -c \
  "cd $CROOT/bin && tar -chf - nvcc nvcc.profile nvlink ptxas cudafe++ \
   fatbinary bin2c __nvcc_device_query crt 2>/dev/null" \
  | tar -xf - -C "$BUNDLE/nvcc-bin"

# ---- 5) targets/include（nvcc 默认头文件搜索路径）----
say "5/6 导出 targets/include -> targets-include/"
docker exec "$CONTAINER" sh -c \
  "cd $CROOT/targets/x86_64-linux/include && tar -chf - . 2>/dev/null" \
  | tar -xf - -C "$BUNDLE/targets-include"

# ---- 6) targets/lib 静态库（nvcc 链接必需）----
say "6/6 导出 cudart 静态库 -> targets-lib/"
for f in libcudart_static.a libcudadevrt.a; do
    for d in "lib64" "targets/x86_64-linux/lib"; do
        if docker exec "$CONTAINER" test -e "$CROOT/$d/$f"; then
            docker exec "$CONTAINER" tar -chf - -C "$CROOT/$d" "$f" \
              | tar -xf - -C "$BUNDLE/targets-lib"
            break
        fi
    done
done

# ---- driver stub：cmake 的 CUDA::cuda_driver 目标必需 ----
# 缺了会报 "Target ggml-cuda links to: CUDA::cuda_driver" 而编译无法开始。
# 来自 cuda-driver-devel 包；精简镜像通常不带，这里从源容器补。
say "导出 driver stub -> stubs/libcuda.so"
STUB=""
for p in "targets/x86_64-linux/lib/stubs/libcuda.so" "lib64/stubs/libcuda.so"; do
    if docker exec "$CONTAINER" test -e "$CROOT/$p"; then
        STUB="$p"; break
    fi
done
if [ -n "$STUB" ]; then
    docker exec "$CONTAINER" tar -chf - -C "$(dirname "$CROOT/$STUB")" libcuda.so \
      | tar -xf - -C "$BUNDLE/stubs"
else
    printf '\033[1;33m  ⚠ 源容器无 libcuda.so stub，请装上 cuda-driver-devel 后重导\033[0m\n'
    printf '    否则构建期 cmake 会报 CUDA::cuda_driver 找不到\n'
fi

# ---- CCCL（cub/thrust/libcudacxx）----
# 精简 CUDA 包不带，cmake 会因 CUDA::cudart includes non-existent path 失败。
if docker exec "$CONTAINER" test -d "$CROOT/include/cccl"; then
    say "导出 CCCL 头文件 -> include/cccl/"
    docker exec "$CONTAINER" sh -c \
      "cd $CROOT/include/cccl && tar -chf - . 2>/dev/null" \
      | tar -xf - -C "$BUNDLE/include/cccl" || true
else
    say "源容器无 include/cccl，跳过（构建时若报 cccl 缺失需另行补齐）"
fi

# ---- 汇总 ----
say "完成，产出："
du -sh "$BUNDLE"/* 2>/dev/null | sed 's/^/  /'
echo
say "文件数: $(find "$BUNDLE" -type f | wc -l)"
echo
cat <<TIP
提示：
  - bundle 约 1GB，属于二进制产物，不要提交进 git
  - 构建镜像前确认 deploy/requirements.txt 存在（cp backend/requirements.txt deploy/）
  - 构建：cd deploy && docker build -f Dockerfile.local-cuda -t amm:rocky9-cu132 .
  - Dockerfile 内有 nvcc 自检，编不过会在构建期就失败，不会等到用户点安装才暴露
TIP
