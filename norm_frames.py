#!/usr/bin/env python3
import os, sys
sys.path.insert(0, "/amm")
import numpy as np
from diffusers.utils import export_to_video

def export_frames(frames, fps=16):
    """从 Wan pipeline 输出提取帧并导出 mp4。
    frames 可以是 WanPipelineOutput/.ndarray/元组。"""
    # 1) 提取为 ndarray: 优先 .frames, 兼容 tuple/list/videos
    if hasattr(frames, "frames"):
        arr = np.asarray(frames.frames)
    elif hasattr(frames, "videos"):
        arr = np.asarray(frames.videos)
    elif isinstance(frames, np.ndarray):
        arr = frames
    elif isinstance(frames, (list, tuple)):
        # 可能是 (frames,) 或直接帧列表
        if len(frames) == 1 and hasattr(frames[0], "frames"):
            arr = np.asarray(frames[0].frames)
        else:
            arr = frames
    else:
        raise TypeError(f"无法提取帧: {type(frames)}")
    # 5D -> 4D (squeeze batch)
    if arr.ndim == 5:
        arr = arr[0]
    # [T,C,H,W] -> [T,H,W,C]
    if arr.ndim == 4 and arr.shape[1] in (1, 3) and (arr.shape[3] not in (1, 2, 3)):
        arr = np.transpose(arr, (0, 2, 3, 1))
    # 值域与通道
    if arr.size:
        if np.issubdtype(arr.dtype, np.floating) and abs(arr).max() <= 1.0:
            arr = (arr * 255.0).clip(0, 255)
        arr = arr.astype(np.uint8)
        if arr.ndim == 4 and arr.shape[3] == 1:
            arr = np.repeat(arr, 3, axis=3)
        elif arr.ndim == 4 and arr.shape[3] == 4:
            arr = arr[..., :3]
    vf = [arr[i] for i in range(arr.shape[0])]
    return export_to_video(vf, fps=fps)

normalize_frames = export_frames