#!/bin/bash
# =========================================================================
# CUDA 组件补齐脚本
# =========================================================================
# 背景：AMM 镜像里的 CUDA 13.2 是精简安装，常缺以下组件，
#       导致 llama.cpp 的 CUDA 编译失败（报错各不相同，很难一眼看出）：
#
#   缺失组件      症状
#   ------------  ------------------------------------------------------
#   nvvm (cicc)   nvcc 报 "--error 0x7f"，CMakeDetermineCUDACompiler 失败
#   libcublas     cmake 报 "Imported target CUDA::cublas ... not found"
#   cublas 头文件 编译报 "cublas_v2.h: No such file or directory"
#   cccl          "Imported target CUDA::cudart includes non-existent path
#                  .../include/cccl"
#
# 用法:
#   bash cuda_components.sh [/usr/local/cuda-13.2]
#   AMM_CUDA_SOURCE_CONTAINER=AMM bash cuda_components.sh   # 从别的容器拷贝
# =========================================================================
set -u

CUDA_DIR="${1:-/usr/local/cuda-13.2}"
REPO="${CUDA_REPO:-https://developer.download.nvidia.com/compute/cuda/repos/rhel9/x86_64}"
CUDA_VER="${CUDA_VER:-13.2.86}"
CUBLAS_VER="${CUBLAS_VER:-13.4.1.3}"
SRC_CONTAINER="${AMM_CUDA_SOURCE_CONTAINER:-}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

need_cic() { [ -x "$CUDA_DIR/nvvm/bin/cicc" ]; }
need_cublas() { ls "$CUDA_DIR"/lib64/libcublas.so >/dev/null 2>&1 \
    && ls "$CUDA_DIR"/lib64/libcublasLt.so >/dev/null 2>&1; }
need_headers() { [ -f "$CUDA_DIR/include/cublas_v2.h" ]; }
need_cccl() { [ -d "$CUDA_DIR/targets/x86_64-linux/include/cccl" ]; }

have_docker() { command -v docker >/dev/null 2>&1; }

# ---------------------------------------------------------------
# 优先从现成容器拷贝（快、不依赖外网）
# ---------------------------------------------------------------
from_container() {
    local c="$1"
    docker inspect "$c" >/dev/null 2>&1 || return 1
    echo -e "  ${YELLOW}  从容器 $c 拷贝 CUDA 组件...${NC}"

    if ! need_cic; then
        docker exec "$c" sh -lc "cd $CUDA_DIR && tar -chf - nvvm 2>/dev/null" | tar -xf - -C "$CUDA_DIR" 2>/dev/null || true
    fi
    if ! need_cublas; then
        # 必须拷「真实版本文件」+「.so.N 链接」+「.so 链接」三件套：
        # cmake 找的是 CUDA::cublas（用 libcublas.so），链接期用 libcublas.so.13，
        # 只拷一部分会出现 "Target ggml-cuda links to CUDA::cublas but the target was not found"。
        docker exec "$c" sh -lc "cd $CUDA_DIR/lib64 && tar -chf - \
            libcublas.so.13.4.1.3 libcublasLt.so.13.4.1.3 libnvblas.so.13.4.1.3 2>/dev/null" \
            | tar -xf - -C "$CUDA_DIR/lib64" 2>/dev/null || true
        cd "$CUDA_DIR/lib64" 2>/dev/null || true
        for f in libcublas libcublasLt libnvblas; do
            if [ -f "${f}.so.13.4.1.3" ]; then
                ln -sf "${f}.so.13.4.1.3" "${f}.so.13"
                ln -sf "${f}.so.13" "${f}.so"
            elif [ -f "${f}.so.13" ]; then
                ln -sf "${f}.so.13" "${f}.so"
            fi
        done
    fi
    if ! need_headers; then
        docker exec "$c" sh -lc "cd $CUDA_DIR/include && tar -chf - cublas*.h 2>/dev/null" \
            | tar -xf - -C "$CUDA_DIR/include" 2>/dev/null || true
    fi
    if ! need_cccl; then
        docker exec "$c" sh -lc "cd $CUDA_DIR/targets/x86_64-linux/include && tar -chf - cccl 2>/dev/null" \
            | tar -xf - -C "$CUDA_DIR/targets/x86_64-linux/include" 2>/dev/null || true
    fi
    return 0
}

# ---------------------------------------------------------------
# 回退：从 NVIDIA 官方源下载 RPM
# ---------------------------------------------------------------
download_rpm() {
    local pkg="$1"
    command -v dnf >/dev/null 2>&1 || return 1
    dnf install -y --quiet "$pkg" 2>/dev/null && return 0
    # dnf 装不上就手动拉 rpm（--http1.1 避免 HTTP/2 断流）
    local url="$REPO/${pkg}-${CUDA_VER}-1.x86_64.rpm"
    curl -fsSL --http1.1 --retry 10 --retry-all-errors --retry-delay 3 \
         -C - -o "/tmp/${pkg}.rpm" "$url" 2>/dev/null || return 1
    rpm -ivh --nodeps "/tmp/${pkg}.rpm" 2>/dev/null
}

from_repo() {
    if ! need_cic; then
        echo -e "  ${YELLOW}  安装 libnvvm (cicc)...${NC}"
        download_rpm "libnvvm-13-2" || echo -e "  ${RED}    失败${NC}"
    fi
    if ! need_cublas; then
        echo -e "  ${YELLOW}  安装 libcublas...${NC}"
        download_rpm "libcublas-13-2-${CUBLAS_VER}" || echo -e "  ${RED}    失败${NC}"
        ln -sf libcublas.so.13 "$CUDA_DIR/lib64/libcublas.so" 2>/dev/null || true
    fi
    if ! need_headers; then
        echo -e "  ${YELLOW}  安装 cublas-devel（头文件）...${NC}"
        download_rpm "libcublas-devel-13-2-${CUBLAS_VER}" || echo -e "  ${RED}    失败${NC}"
    fi
    if ! need_cccl; then
        echo -e "  ${YELLOW}  安装 CCCL 头文件...${NC}"
        local dst="$CUDA_DIR/targets/x86_64-linux/include/cccl"
        mkdir -p "$dst"
        cd /tmp || return 1
        curl -fsSL --http1.1 --retry 10 --retry-all-errors -C - \
             -o cccl.tar.gz \
             https://github.com/NVIDIA/cccl/releases/download/v3.5.0/cccl-src-v3.5.0.tar.gz 2>/dev/null \
            && tar xzf cccl.tar.gz 2>/dev/null || return 1
        cp -r cccl-src-v3.5.0/libcudacxx/include/* "$dst"/ 2>/dev/null || true
        cp -r cccl-src-v3.5.0/cub/cub "$dst"/cub 2>/dev/null || true
        cp -r cccl-src-v3.5.0/thrust/thrust "$dst"/thrust 2>/dev/null || true
        cp -r cccl-src-v3.5.0/cudax/include/* "$dst"/ 2>/dev/null || true
        rm -rf cccl-src-v3.5.0 cccl.tar.gz
    fi
}

# ---------------------------------------------------------------
# 0) 最快路径：本地预置组件包（离线可用，无需 docker 与外网）
#    目录结构：
#      $BUNDLE/lib64/*.so*      cublas / cublasLt / nvblas
#      $BUNDLE/include/*.h      cublas 头文件
#      $BUNDLE/bin/cicc         nvvm 前端
BUNDLE="${AMM_CUDA_BUNDLE:-/opt/cuda-bundle}"

from_bundle() {
    [ -d "$BUNDLE" ] || return 1
    echo -e "  ${YELLOW}  使用本地 CUDA 组件包: $BUNDLE${NC}"
    if ! need_cic && [ -f "$BUNDLE/bin/cicc" ]; then
        mkdir -p "$CUDA_DIR/nvvm/bin"
        cp -f "$BUNDLE"/bin/cicc "$CUDA_DIR/nvvm/bin/" 2>/dev/null || true
        # nvvm/lib 也要
        [ -d "$BUNDLE/nvvm-lib" ] && { mkdir -p "$CUDA_DIR/nvvm/lib64"; cp -f "$BUNDLE"/nvvm-lib/* "$CUDA_DIR/nvvm/lib64/" 2>/dev/null || true; }
    fi
    if ! need_cublas && ls "$BUNDLE"/lib64/libcublas*.so* >/dev/null 2>&1; then
        cp -f "$BUNDLE"/lib64/*.so* "$CUDA_DIR/lib64/" 2>/dev/null || true
        ( cd "$CUDA_DIR/lib64" 2>/dev/null
          for f in libcublas libcublasLt libnvblas; do
            if [ -f "${f}.so.13.4.1.3" ]; then
                ln -sf "${f}.so.13.4.1.3" "${f}.so.13"; ln -sf "${f}.so.13" "${f}.so"
            fi
          done )
    fi
    if ! need_headers && [ -f "$BUNDLE/include/cublas_v2.h" ]; then
        cp -f "$BUNDLE"/include/*.h "$CUDA_DIR/include/" 2>/dev/null || true
    fi
    if ! need_cccl && [ -d "$BUNDLE/include/cccl" ]; then
        mkdir -p "$CUDA_DIR/targets/x86_64-linux/include"
        cp -rf "$BUNDLE/include/cccl" "$CUDA_DIR/targets/x86_64-linux/include/" 2>/dev/null || true
    fi
    return 0
}

echo "CUDA 目录: $CUDA_DIR"

# 优先用指定容器，其次自动找 AMM。
# 注意：本脚本通常在「容器内」执行，而容器内没有 docker CLI，
# 所以只能先试 docker；没有就直接走官方源下载。
if from_bundle; then
    :
elif have_docker; then
    if [ -n "$SRC_CONTAINER" ] && docker inspect "$SRC_CONTAINER" >/dev/null 2>&1; then
        from_container "$SRC_CONTAINER" || from_repo
    elif docker inspect AMM >/dev/null 2>&1; then
        from_container AMM || from_repo
    else
        from_repo
    fi
else
    echo -e "  ${YELLOW}  无本地组件包且无 docker CLI，改用 NVIDIA 官方源下载${NC}"
    from_repo
fi

# ---------------------------------------------------------------
echo ""
echo "结果:"
need_cic && echo -e "  ${GREEN}✓ nvvm (cicc)${NC}" || echo -e "  ${RED}✗ nvvm${NC}"
need_cublas && echo -e "  ${GREEN}✓ libcublas${NC}" || echo -e "  ${RED}✗ libcublas${NC}"
need_headers && echo -e "  ${GREEN}✓ cublas 头文件${NC}" || echo -e "  ${RED}✗ cublas 头文件${NC}"
need_cccl && echo -e "  ${GREEN}✓ cccl${NC}" || echo -e "  ${RED}✗ cccl${NC}"

if need_cic && need_cublas && need_headers && need_cccl; then
    exit 0
fi
echo -e "${YELLOW}部分组件仍缺失，CUDA 编译可能失败（可回退 CPU 构建）${NC}"
exit 1
