"""
Model Registry & Router — 外部 provider 汇聚 + Token Router（R3/R4）
===================================================================
R3 外部模型接入（LiteLLM 能力融入）
  - provider 注册表：openai / anthropic / deepseek / 任意 OpenAI 兼容端点
  - /v1/models 返回 本地实例 + 外部 provider 模型 的合集
  - fallback 链 / 多 key 轮询 / 成本估算

R4 Token Router
  - 虚拟 key 管理：限流（RPM/TPM）、月度 token 限额、模型白名单
  - 路由策略：模型名直连 / 前缀强制 / 权重轮询 / 故障转移 / 显存感知
  - 用量统计：per-key / per-model 的 tokens、请求数、成本

存储（固定路径不动，新增独立文件）:
  $AMM_ROOT/data/router.json       provider / key / 用量（原子写）
Keys 支持环境变量引用，避免明文落盘：api_key_env: "ICOMPIFY_KEY"
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.core.sse_capture import SSETextCapture

logger = logging.getLogger("AMM.Router")

_KEY_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# 成本估算（人民币 元 / 1M tokens），可在 provider / 单模型上覆盖
DEFAULT_COST_TABLE = {
    "openai": {"input": 18.0, "output": 72.0},
    "anthropic": {"input": 22.0, "output": 108.0},
    "deepseek": {"input": 2.0, "output": 8.0},
    "icompify": {"input": 1.5, "output": 4.5},
    "local": {"input": 0.0, "output": 0.0},
}


def fmt_tokens(n: int) -> str:
    """tokens 自动换算：1_234 -> 1.2K，1_234_567 -> 1.2M，1_234_567_890 -> 1.2B"""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    for unit, div in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= div:
            v = n / div
            return f"{v:.1f}{unit}" if v < 100 else f"{v:.0f}{unit}"
    return str(int(n))


class RouterStore:
    """router.json 读写（provider / keys / usage）"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.data: Dict[str, Any] = {"providers": {}, "keys": {}, "usage": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.setdefault("providers", {})
                self.data.setdefault("keys", {})
                self.data.setdefault("usage", {})
                self.data.update({k: v for k, v in loaded.items()
                                  if k in ("providers", "keys", "usage",
                                           "auth_enforced", "default_api_key",
                                           "calls", "model_overrides")})
        except Exception as e:
            logger.warning(f"router.json 读取失败: {e}")

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)


class TokenRouter:
    """本地模型 + 外部 provider 的统一路由与用量记账"""

    def __init__(self, manager, store: RouterStore, memory_total_mb: int = 0):
        self.manager = manager
        self.store = store
        self.memory_total_mb = memory_total_mb

    # ================================================================
    # Providers
    # ================================================================
    def list_providers(self, reveal: bool = False) -> List[Dict[str, Any]]:
        out = []
        for name, cfg in (self.store.data.get("providers") or {}).items():
            item = {
                "name": name,
                "type": cfg.get("type", "openai"),
                "base_url": cfg.get("base_url", ""),
                "models": cfg.get("models", []),
                # 自动拉取的模型清单（/models 探活结果），与手工 models 合并展示
                "fetched_models": cfg.get("fetched_models", []),
                "fetched_at": cfg.get("fetched_at", 0),
                "fetch_error": cfg.get("fetch_error", ""),
                "enabled": cfg.get("enabled", True),
                "has_key": bool(cfg.get("api_key") or cfg.get("api_key_env")),
                "api_key_env": cfg.get("api_key_env", ""),
                "cost": cfg.get("cost") or DEFAULT_COST_TABLE.get(cfg.get("type", ""), {}),
                "model_pricing": cfg.get("model_pricing") or {},
                "currency": "CNY",
                "weight": cfg.get("weight", 1),
            }
            if reveal and cfg.get("api_key"):
                item["api_key_masked"] = _mask(cfg["api_key"])
            if reveal and (cfg.get("api_key_env") or "").startswith("sk-"):
                # 历史上把真实 key 误填进 api_key_env 的情况
                item["api_key_masked"] = _mask(cfg["api_key_env"])
                item["api_key_in_env_field"] = True
            out.append(item)
        return sorted(out, key=lambda x: x["name"])

    def upsert_provider(self, name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        if not _KEY_PREFIX_RE.match(name or ""):
            raise ValueError(f"provider 名非法: {name}")
        providers = self.store.data.setdefault("providers", {})
        cur = providers.get(name, {})
        cur.update({
            "type": cfg.get("type", cur.get("type", "openai")),
            "base_url": (cfg.get("base_url") or cur.get("base_url") or "").rstrip("/"),
            "models": cfg.get("models", cur.get("models", [])),
            "enabled": bool(cfg.get("enabled", cur.get("enabled", True))),
            "weight": int(cfg.get("weight", cur.get("weight", 1)) or 1),
            "cost": cfg.get("cost") or cur.get("cost") or {},
        })
        if "model_pricing" in cfg:
            cur["model_pricing"] = cfg.get("model_pricing") or {}
        # key 只接受环境变量名或显式值；显式值不回显
        if cfg.get("api_key_env"):
            v = cfg["api_key_env"].strip()
            # 用户可能直接粘贴真实 key 到「环境变量名」框里，自动纠正到 api_key
            if v.startswith(("sk-", "Bearer ")):
                cur["api_key"] = v.replace("Bearer ", "").strip()
                cur.pop("api_key_env", None)
            else:
                cur["api_key_env"] = v
        if cfg.get("api_key"):
            cur["api_key"] = cfg["api_key"].strip()
            cur.pop("api_key_env", None)
        if cfg.get("clear_api_key"):
            cur.pop("api_key", None)
            cur.pop("api_key_env", None)
        providers[name] = cur
        self.store.save()
        logger.info(f"provider 已更新: {name}")
        return {"success": True, "provider": name}

    def delete_provider(self, name: str) -> bool:
        providers = self.store.data.get("providers") or {}
        if name not in providers:
            return False
        providers.pop(name)
        self.store.save()
        return True

    def resolve_provider_key(self, name: str) -> Optional[str]:
        cfg = (self.store.data.get("providers") or {}).get(name) or {}
        if cfg.get("api_key"):
            return cfg["api_key"]
        env = cfg.get("api_key_env")
        if env:
            # api_key_env 既可填环境变量名，也可直接填 sk- 开头的真实 key
            # （早期版本把真实 key 填进这个字段，导致鉴权失败且难排查）
            if env.startswith(("sk-", "Bearer ")):
                return env.replace("Bearer ", "").strip()
            return os.environ.get(env)
        return None

    # ================================================================
    # Virtual keys (Token Router)
    # ================================================================
    def list_keys(self) -> List[Dict[str, Any]]:
        out = []
        for k, cfg in (self.store.data.get("keys") or {}).items():
            usage = (self.store.data.get("usage") or {}).get(k, {})
            out.append({
                "key": _mask(k),
                "key_id": cfg.get("key_id") or k[-6:],
                "name": cfg.get("name", ""),
                "enabled": cfg.get("enabled", True),
                "rpm_limit": cfg.get("rpm_limit", 0),
                "tpm_limit": cfg.get("tpm_limit", 0),
                "monthly_token_limit": cfg.get("monthly_token_limit", 0),
                "allowed_models": cfg.get("allowed_models", []),
                "used_tokens": usage.get("total_tokens", 0),
                "requests": usage.get("requests", 0),
                "cost_cny": round(usage.get("cost_cny", 0.0), 4),
                "created_at": cfg.get("created_at", 0),
                "expires_at": cfg.get("expires_at", 0),
                "expired": bool(cfg.get("expires_at") and time.time() > cfg["expires_at"]),
                "total_token_limit": cfg.get("total_token_limit", 0),
                "note": cfg.get("note", ""),
            })
        return out

    def issue_key(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """签发新 key（返回明文一次，之后只存 hash 前缀索引）"""
        import secrets
        name = (cfg.get("name") or "").strip() or "app"
        raw = "sk-amm-" + secrets.token_urlsafe(24)
        keys = self.store.data.setdefault("keys", {})
        keys[raw] = {
            "key_id": raw[-8:],
            "name": name,
            "enabled": True,
            "created_at": time.time(),
            "rpm_limit": int(cfg.get("rpm_limit") or 0),
            "tpm_limit": int(cfg.get("tpm_limit") or 0),
            "monthly_token_limit": int(cfg.get("monthly_token_limit") or 0),
            "total_token_limit": int(cfg.get("total_token_limit") or 0),
            "expires_at": float(cfg.get("expires_at") or 0),
            "allowed_models": cfg.get("allowed_models") or [],
            "note": cfg.get("note", ""),
        }
        # 签发过 key 后永久禁用「无 key 放行」默认行为（除非显式清空所有 key 配置）
        self.store.data["auth_enforced"] = True
        self.store.save()
        logger.info(f"已签发 key: {name} (…{raw[-6:]})")
        return {
            "success": True,
            "key": raw,
            "name": name,
            "warning": "请立即保存，界面后续只显示掩码",
        }

    def update_key(self, key_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """编辑虚拟 key（按 key_id 或掩码定位）。name/enabled/限额/过期/白名单可改。"""
        keys = self.store.data.get("keys") or {}
        target = None
        for k, v in keys.items():
            cid = v.get("key_id") or k[-8:]
            if cid == key_id or _mask(k) == key_id or k.endswith(key_id):
                target = k
                break
        if target is None:
            return {"success": False, "error": f"key 不存在: {key_id}"}
        cur = keys[target]
        for field in ("name", "note"):
            if field in cfg and cfg[field] is not None:
                cur[field] = str(cfg[field])
        if "enabled" in cfg and cfg["enabled"] is not None:
            cur["enabled"] = bool(cfg["enabled"])
        for field in ("rpm_limit", "tpm_limit", "monthly_token_limit", "total_token_limit"):
            if field in cfg and cfg[field] is not None:
                cur[field] = int(cfg[field] or 0)
        if "expires_at" in cfg:
            cur["expires_at"] = float(cfg["expires_at"] or 0)
        if "allowed_models" in cfg and cfg["allowed_models"] is not None:
            cur["allowed_models"] = list(cfg["allowed_models"])
        self.store.save()
        logger.info(f"key 已更新: {cur.get('name')} (…{target[-6:]})")
        return {"success": True, "key_id": cur.get("key_id"), "key": _mask(target)}

    def revoke_key(self, key: str) -> bool:
        """吊销 key（支持精确 key、掩码或 key 尾部）"""
        keys = self.store.data.get("keys") or {}
        target = None
        if key in keys:
            target = key
        else:
            for k in keys:
                if _mask(k) == key or (len(key) >= 6 and k.endswith(key)):
                    target = k
                    break
        if target is None:
            return False
        keys.pop(target, None)
        # 注意：刻意**不**因「吊销完最后一个 key」而自动退回免鉴权模式。
        # 否则任何持有旧 Key 的客户端会突然获得无鉴权访问，属于安全退化。
        # 想退回免鉴权请在 Settings 显式清除默认 Key（clear_default_key 会清 auth_enforced）。
        self.store.save()
        logger.info(f"key 已吊销: …{target[-6:]}")
        return True

    # ---------------- 默认对外 API Key ----------------
    def get_default_key(self, reveal: bool = False) -> Dict[str, Any]:
        """读取「对外服务默认 API Key」。

        与虚拟 key 的区别：虚拟 key 用于 Token Router 计费与配额；
        默认 key 只是给客户端直连 AMM 用的一道简单门禁（可留空=不校验）。
        """
        raw = self.store.data.get("default_api_key") or ""
        return {
            "has_key": bool(raw),
            "masked": _mask(raw) if raw else "",
            "key": raw if reveal else "",
            "enforced": bool(self.store.data.get("auth_enforced")),
        }

    def generate_default_key(self, apply: bool = True) -> Dict[str, Any]:
        """随机生成一个默认 API Key（返回明文，便于直接复制到客户端）"""
        import secrets
        raw = "sk-amm-default-" + secrets.token_urlsafe(24)
        self.store.data["default_api_key"] = raw
        if apply:
            self.store.data["auth_enforced"] = True
        self.store.save()
        logger.info("已生成默认 API Key")
        return {"success": True, "key": raw, "masked": _mask(raw)}

    def clear_default_key(self) -> Dict[str, Any]:
        """清除默认 key —— 之后客户端可无 key 直连（仅限内网自用）"""
        self.store.data.pop("default_api_key", None)
        # 没有虚拟 key 时同时解除强制鉴权，否则客户端仍然连不上
        if not (self.store.data.get("keys") or {}):
            self.store.data["auth_enforced"] = False
        self.store.save()
        logger.info("已清除默认 API Key")
        return {"success": True, "cleared": True}

    def disable_auth_entirely(self) -> Dict[str, Any]:
        """彻底关闭鉴权（#2）

        与 clear_default_key 的区别：
          - clear_default_key 只在「没有虚拟 key」时才会解除 auth_enforced
          - 本方法无条件清除 default_api_key + auth_enforced，**不动虚拟 key**
            （虚拟 key 保留是为了将来重新开启时配额/计费历史不丢）

        危险操作：关闭后任何能访问本端口的人都能调 /v1/*。
        """
        had_default = bool(self.store.data.pop("default_api_key", None))
        self.store.data["auth_enforced"] = False
        self.store.save()
        kept = len(self.store.data.get("keys") or {})
        logger.warning(
            f"鉴权已彻底关闭（default_key_removed={had_default}, "
            f"kept_virtual_keys={kept}）")
        return {
            "success": True,
            "auth_enforced": False,
            "default_key_removed": had_default,
            "kept_virtual_keys": kept,
            "warning": "任何能访问本端口的人都可调用 /v1/*",
        }

    def enable_auth(self) -> Dict[str, Any]:
        """重新开启鉴权（#2）：开启后必须有 key 才能访问 /v1/*"""
        self.store.data["auth_enforced"] = True
        self.store.save()
        has_key = bool(self.store.data.get("default_api_key"))
        n_keys = len(self.store.data.get("keys") or {})
        logger.info(f"鉴权已开启（default={has_key}, virtual={n_keys}）")
        return {
            "success": True,
            "auth_enforced": True,
            "has_usable_key": has_key or n_keys > 0,
            "warning": "" if (has_key or n_keys) else
                       "已开启但没有任何可用 Key，客户端会全部 401，请先生成 Key",
        }

    def authenticate(self, key: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """校验 API key。返回 (ok, reason, key_cfg)。

        放行规则:
          - 从未签发过任何 key（auth_enforced 为假且 keys 为空）-> 本地部署模式放行
          - 一旦签发过 key，即使全部吊销也保持鉴权，不再退回放行
        """
        keys = self.store.data.get("keys") or {}
        default_key = self.store.data.get("default_api_key") or ""
        enforced = bool(self.store.data.get("auth_enforced"))
        if not keys and not default_key and not enforced:
            return True, "no-keys-configured", None
        if not key:
            return False, "missing-api-key", None
        # 默认对外 key：通过但不参与计费/配额（返回 None 表示匿名用量）
        if default_key and key == default_key:
            return True, "default-key", None
        cfg = keys.get(key)
        if cfg is None:
            return False, "invalid-api-key", None
        if not cfg.get("enabled", True):
            return False, "key-disabled", cfg
        exp = float(cfg.get("expires_at") or 0)
        if exp and time.time() > exp:
            return False, "key-expired", cfg
        return True, "ok", cfg

    def check_quota(self, key_cfg: Optional[Dict[str, Any]], model: str,
                    est_tokens: int = 0) -> Tuple[bool, str]:
        if not key_cfg:
            return True, "ok"
        allowed = key_cfg.get("allowed_models") or []
        if allowed and model not in allowed and "*" not in allowed:
            return False, f"model-not-allowed:{model}"
        usage = (self.store.data.get("usage") or {}).get(
            f"key:{key_cfg.get('key_id') or 'unknown'}", {})
        limit = int(key_cfg.get("monthly_token_limit") or 0)
        if limit and usage.get("monthly_tokens", 0) + est_tokens > limit:
            return False, "monthly-token-limit-exceeded"
        total_limit = int(key_cfg.get("total_token_limit") or 0)
        if total_limit and usage.get("total_tokens", 0) + est_tokens > total_limit:
            return False, "total-token-limit-exceeded"
        return True, "ok"

    # ================================================================
    # Routing
    # ================================================================
    async def fetch_provider_models(self, name: str, timeout: int = 15) -> Dict[str, Any]:
        """用 provider 的 base_url + api_key 调 /models，拉取全部可用模型并缓存。

        结果写入 provider.fetched_models，供 /api/router/models、虚拟 key 的
        模型多选、Playground 等复用，避免每次手工维护模型清单。
        """
        import aiohttp

        providers = self.store.data.setdefault("providers", {})
        cfg = providers.get(name)
        if not cfg:
            return {"ok": False, "error": f"provider 不存在: {name}"}

        key = self.resolve_provider_key(name)
        if not key:
            return {"ok": False, "error": "未配置 API key"}
        base = (cfg.get("base_url") or "").rstrip("/")
        if not base:
            return {"ok": False, "error": "未配置 base_url"}

        url = base + "/models"
        headers = {"Authorization": f"Bearer {key}"}
        # anthropic 用 x-api-key
        if (cfg.get("type") or "") == "anthropic":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}

        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    text = await r.text()
                    if r.status != 200:
                        err = f"HTTP {r.status}: {text[:200]}"
                        cfg["fetch_error"] = err
                        self.store.save()
                        return {"ok": False, "error": err, "status": r.status}
                    try:
                        data = json.loads(text)
                    except Exception:
                        return {"ok": False, "error": f"返回非 JSON: {text[:200]}"}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            cfg["fetch_error"] = err
            self.store.save()
            return {"ok": False, "error": err}

        raw = data.get("data") if isinstance(data, dict) else None
        if raw is None and isinstance(data, dict) and isinstance(data.get("models"), list):
            raw = data["models"]
        models: List[Dict[str, Any]] = []
        for m in raw or []:
            if isinstance(m, str):
                models.append({"id": m})
            elif isinstance(m, dict):
                mid = m.get("id") or m.get("name")
                if not mid:
                    continue
                item = {"id": mid}
                for k in ("context_length", "owned_by", "created", "max_tokens"):
                    if k in m:
                        item[k] = m[k]
                models.append(item)

        cfg["fetched_models"] = models
        cfg["fetched_at"] = time.time()
        cfg["fetch_error"] = ""
        self.store.save()
        logger.info(f"provider {name} 拉取到 {len(models)} 个模型")
        return {"ok": True, "count": len(models), "models": models}

    # ---------------- 可用模型清单的编辑（类型/状态/定价） ----------------
    def model_overrides(self) -> Dict[str, Any]:
        return self.store.data.setdefault("model_overrides", {})

    def update_model_meta(self, model_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """编辑某个模型的元数据：类型/状态/单价/上下文长度等。

        这些覆盖值优先级高于 provider/引擎推断值，
        用于修正自动拉取到的模型信息（例如 provider 没返回 max_tokens）。
        """
        if not model_id:
            return {"success": False, "error": "缺少 model_id"}
        ov = self.model_overrides().setdefault(model_id, {})
        for field in ("category", "status", "note"):
            if field in cfg and cfg[field] is not None:
                ov[field] = str(cfg[field])
        for field in ("input_price", "output_price", "context_length",
                      "max_tokens", "enabled"):
            if field in cfg and cfg[field] is not None and cfg[field] != "":
                try:
                    ov[field] = float(cfg[field]) if field.endswith("price") else int(cfg[field])
                except (TypeError, ValueError):
                    pass
        self.store.save()
        logger.info(f"模型元数据已更新: {model_id} -> {ov}")
        return {"success": True, "model_id": model_id, "meta": ov}

    def local_models(self) -> List[Dict[str, Any]]:
        out = []
        for inst in self.manager.get_all_instances().values():
            if inst.status != "running":
                continue
            item = {
                "id": inst.model_id,
                "object": "model",
                "owned_by": "amm-local",
                "model_type": inst.category,
                "engine": inst.engine_type,
                "port": inst.port,
                "is_dynamic": inst.is_dynamic,
            }
            # 本地实例同样应用「可用模型清单」里的覆盖（单价/上下文等）
            ov = (self.store.data.get("model_overrides") or {}).get(inst.model_id) or {}
            if ov:
                if ov.get("category"):
                    item["model_type"] = ov["category"]
                if ov.get("context_length"):
                    item["context_length"] = ov["context_length"]
                if ov.get("max_tokens"):
                    item["max_tokens"] = ov["max_tokens"]
                if ov.get("note"):
                    item["note"] = ov["note"]
                if ov.get("input_price") is not None or ov.get("output_price") is not None:
                    item["pricing_cny_per_mtok"] = {
                        "input": ov.get("input_price") or 0,
                        "output": ov.get("output_price") or 0,
                    }
            out.append(item)
        return out

    def all_models(self) -> List[Dict[str, Any]]:
        """本地运行实例 + 各 provider 的（手工 + 自动拉取）模型合集，按 id 去重。"""
        models = self.local_models()
        seen = {m["id"] for m in models}
        for pname, cfg in (self.store.data.get("providers") or {}).items():
            if not cfg.get("enabled", True):
                continue
            names = list(cfg.get("models") or []) + list(cfg.get("fetched_models") or [])
            for m in names:
                mid = m if isinstance(m, str) else (m.get("id") or m.get("name"))
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                item = {
                    "id": mid,
                    "object": "model",
                    "owned_by": f"provider:{pname}",
                    "provider": pname,
                }
                if isinstance(m, dict):
                    for k in ("context_length", "owned_by", "created"):
                        if k in m:
                            item[k] = m[k]
                pricing = (cfg.get("model_pricing") or {}).get(mid) \
                    or cfg.get("cost") or DEFAULT_COST_TABLE.get(cfg.get("type", ""), {})
                if pricing:
                    item["pricing_cny_per_mtok"] = dict(pricing)
                # 应用用户在「可用模型清单」里做的覆盖
                ov = (self.store.data.get("model_overrides") or {}).get(mid) or {}
                if ov:
                    if ov.get("category"):
                        item["category"] = ov["category"]
                    if ov.get("status"):
                        item["status"] = ov["status"]
                    if ov.get("context_length"):
                        item["context_length"] = ov["context_length"]
                    if ov.get("max_tokens"):
                        item["max_tokens"] = ov["max_tokens"]
                    if ov.get("note"):
                        item["note"] = ov["note"]
                    pi, po = ov.get("input_price"), ov.get("output_price")
                    if pi is not None or po is not None:
                        base = item.get("pricing_cny_per_mtok") or {}
                        item["pricing_cny_per_mtok"] = {
                            "input": pi if pi is not None else base.get("input", 0),
                            "output": po if po is not None else base.get("output", 0),
                        }
                models.append(item)
        return models

    async def relay_to_provider(self, request, body: Dict[str, Any], decision: Dict[str, Any]):
        """把一次 OpenAI chat/completions 请求转发到外部 provider。

        供 /v1/chat/completions 与 /api/router/relay 共用，
        保证「客户端直连 AMM」和「经 Token Router」两条路径行为一致，
        并且都会写入调用历史。
        """
        import aiohttp
        from aiohttp import web

        pname = decision.get("provider") or ""
        cfg = (self.store.data.get("providers") or {}).get(pname) or {}
        pkey = self.resolve_provider_key(pname)
        if not pkey:
            return web.json_response(
                {"error": {"message": f"provider {pname} 未配置 key",
                           "type": "config_error"}}, status=502)

        url = (cfg.get("base_url") or "").rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {pkey}"}
        if (cfg.get("type") or "") == "anthropic":
            headers = {"Content-Type": "application/json",
                       "x-api-key": pkey, "anthropic-version": "2023-06-01"}
        payload = dict(body)
        payload["model"] = decision.get("model") or payload.get("model")

        t0 = time.perf_counter()
        stream = bool(payload.get("stream"))
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, json=payload, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=600)) as r:
                    if stream and r.status == 200:
                        resp = web.StreamResponse(status=200, headers={
                            "Content-Type": r.headers.get("Content-Type", "text/event-stream"),
                            "Cache-Control": "no-cache",
                            "Access-Control-Allow-Origin": "*",
                        })
                        await resp.prepare(request)
                        first = None
                        emitted = 0
                        # 边转发边解析 SSE，流结束后把真正的输出写进调用历史
                        cap = SSETextCapture()
                        async for chunk in r.content.iter_any():
                            if first is None:
                                first = time.perf_counter()
                            cap.feed(chunk)
                            await resp.write(chunk)
                            emitted += len(chunk)
                        await resp.write_eof()
                        cap.close()
                        e2e = (time.perf_counter() - t0) * 1000
                        ttft = ((first or t0) - t0) * 1000
                        out_tok = max(1, emitted // 4)
                        metrics = _metrics(e2e, out_tok, ttft)
                        self.record_usage(None, payload["model"], pname,
                                          max(1, len(str(body.get("messages"))) // 4),
                                          out_tok, None, metrics)
                        self.record_call({
                            "ts": time.time(), "key": "anonymous",
                            "model": payload["model"], "provider": pname,
                            "status": "ok", "stream": True,
                            "input": _summarize(body), "output": _stream_output(cap),
                            "params": _params(body), "metrics": metrics,
                        })
                        return resp

                    text = await r.text()
                    if r.status != 200:
                        self.record_call({
                            "ts": time.time(), "key": "anonymous",
                            "model": payload["model"], "provider": pname,
                            "status": f"http{r.status}", "stream": stream,
                            "input": _summarize(body), "output": text[:500],
                            "params": _params(body), "metrics": {},
                        })
                        return web.json_response(
                            {"error": {"message": text[:1000],
                                       "type": "upstream_error",
                                       "provider": pname}}, status=r.status)
                    try:
                        data = json.loads(text)
                    except Exception:
                        return web.json_response(
                            {"error": {"message": text[:500]}}, status=502)

                    u = data.get("usage") or {}
                    out_tok = int(u.get("completion_tokens") or 0)
                    e2e = (time.perf_counter() - t0) * 1000
                    metrics = _metrics(e2e, out_tok, 0)
                    self.record_usage(None, payload["model"], pname,
                                      int(u.get("prompt_tokens") or 0),
                                      out_tok, None, metrics)
                    self.record_call({
                        "ts": time.time(), "key": "anonymous",
                        "model": payload["model"], "provider": pname,
                        "status": "ok", "stream": False,
                        "input": _summarize(body), "output": _extract(data),
                        "params": _params(body), "metrics": metrics,
                    })
                    return web.json_response(data)
        except Exception as e:
            logger.error(f"relay_to_provider 失败 {pname}: {e}")
            return web.json_response(
                {"error": {"message": str(e), "type": "relay_error"}}, status=502)

    def resolve(self, model: str) -> Dict[str, Any]:
        """决定一次请求应路由到本地实例还是外部 provider

        策略优先级:
          1. 前缀强制: local/<model> 强制本地；provider/<name>/<model> 强制外部
          2. 本地运行中实例精确匹配
          3. 外部 provider 声明的模型（按 weight 轮询挑一个）
          4. 显存感知: 本地有同类实例但未运行 -> 建议本地启动（由调用方决定）
        """
        if not model:
            return {"target": "none", "error": "missing model"}

        if model.startswith("local/"):
            real = model[len("local/"):]
            inst = self.manager.get_instance(real)
            if inst and inst.status == "running":
                return {"target": "local", "model": real, "port": inst.port,
                        "category": inst.category, "reason": "prefix-local"}
            return {"target": "none", "error": f"本地实例未运行: {real}"}

        if model.startswith("provider/"):
            parts = model.split("/", 2)
            if len(parts) == 3:
                pname, real = parts[1], parts[2]
                if pname in (self.store.data.get("providers") or {}):
                    return {"target": "provider", "provider": pname, "model": real,
                            "reason": "prefix-provider"}
            return {"target": "none", "error": f"provider 前缀非法: {model}"}

        inst = self.manager.get_instance(model)
        if inst and inst.status == "running":
            return {"target": "local", "model": model, "port": inst.port,
                    "category": inst.category, "reason": "local-match"}

        candidates = []
        for pname, cfg in (self.store.data.get("providers") or {}).items():
            if not cfg.get("enabled", True):
                continue
            # 手工声明的模型 + 通过 /models 自动拉取到的模型都要能路由
            names = set(cfg.get("models") or [])
            for fm in (cfg.get("fetched_models") or []):
                names.add(fm if isinstance(fm, str) else (fm.get("id") or ""))
            if model in names:
                candidates.append((pname, int(cfg.get("weight", 1) or 1)))
        if candidates:
            pick = self._weighted_pick(candidates)
            return {"target": "provider", "provider": pick, "model": model,
                    "reason": "provider-match"}

        if inst:
            return {"target": "local-stopped", "model": model, "port": inst.port,
                    "category": inst.category,
                    "error": f"实例 {model} 未运行，请先 start 或由路由器自动启动"}

        return {"target": "none", "error": f"未找到模型: {model}"}

    @staticmethod
    def _weighted_pick(candidates: List[Tuple[str, int]]) -> str:
        """按权重轮询（用时间片做确定性散列，避免额外状态）"""
        total = sum(max(1, w) for _, w in candidates)
        slot = int(time.time()) % total
        acc = 0
        for name, w in candidates:
            acc += max(1, w)
            if slot < acc:
                return name
        return candidates[0][0]

    # ================================================================
    # Usage accounting
    # ================================================================
    def record_usage(self, api_key: Optional[str], model: str, provider: str,
                     input_tokens: int, output_tokens: int,
                     key_cfg: Optional[Dict[str, Any]] = None,
                     metrics: Optional[Dict[str, Any]] = None) -> None:
        usage = self.store.data.setdefault("usage", {})
        store_key = self._usage_key(api_key, key_cfg)
        providers = self.store.data.get("providers") or {}
        cfg = providers.get(provider) or {}
        # 定价查找顺序: 单模型定价 > provider 自带 cost > provider type > provider 名 > local
        ov = (self.store.data.get("model_overrides") or {}).get(model) or {}
        ov_price = {}
        if ov.get("input_price") is not None or ov.get("output_price") is not None:
            ov_price = {"input": ov.get("input_price") or 0,
                        "output": ov.get("output_price") or 0}
        per_model = ov_price or (cfg.get("model_pricing") or {}).get(model) or {}
        cost_cfg = (per_model
                    or cfg.get("cost")
                    or DEFAULT_COST_TABLE.get(cfg.get("type", "") or "")
                    or DEFAULT_COST_TABLE.get(provider or "")
                    or DEFAULT_COST_TABLE["local"])
        cost = (input_tokens / 1_000_000.0) * float(cost_cfg.get("input", 0)) + \
               (output_tokens / 1_000_000.0) * float(cost_cfg.get("output", 0))

        entry = usage.setdefault(store_key, {
            "requests": 0, "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "cost_cny": 0.0, "by_model": {},
            "monthly_tokens": 0, "month": _month(),
        })
        if entry.get("month") != _month():
            entry["month"] = _month()
            entry["monthly_tokens"] = 0

        entry["requests"] += 1
        entry["input_tokens"] += input_tokens
        entry["output_tokens"] += output_tokens
        entry["total_tokens"] += input_tokens + output_tokens
        entry["monthly_tokens"] += input_tokens + output_tokens
        entry["cost_cny"] = entry.get("cost_cny", 0.0) + cost
        bm = entry["by_model"].setdefault(model, {"requests": 0, "tokens": 0, "cost_cny": 0.0,
                                                  "input_tokens": 0, "output_tokens": 0})
        bm["requests"] += 1
        bm["tokens"] += input_tokens + output_tokens
        bm["input_tokens"] = bm.get("input_tokens", 0) + input_tokens
        bm["output_tokens"] = bm.get("output_tokens", 0) + output_tokens
        bm["cost_cny"] = bm.get("cost_cny", 0.0) + cost

        # 延迟指标（毫秒）：e2e / ttft / itl / tpot / tps
        if metrics:
            m = entry.setdefault("metrics", {})
            cnt = m.get("count", 0) + 1
            m["count"] = cnt
            for k in ("e2e_ms", "ttft_ms", "itl_ms", "tpot_ms", "tps"):
                v = metrics.get(k)
                if v is None:
                    continue
                prev = m.get(k) or 0
                # 增量平均，避免存全量样本
                m[k] = round(prev + (float(v) - prev) / cnt, 2)
        self.store.save()

    @staticmethod
    def _usage_key(api_key: Optional[str], key_cfg: Optional[Dict[str, Any]]) -> str:
        """用量归档 key：用 key_id，避免明文 key 写进 usage 记录"""
        if key_cfg:
            return f"key:{key_cfg.get('key_id') or 'unknown'}"
        return "anonymous"

    # ---------------- 调用历史（输入输出详单） ----------------
    def record_call(self, entry: Dict[str, Any], keep: int = 2000) -> None:
        """记录一次完整的推理调用（含输入/输出摘要与性能指标）。

        只保留最近 keep 条，避免 router.json 无限膨胀；
        需要长期归档时走 CSV 导出。
        """
        calls = self.store.data.setdefault("calls", [])
        calls.append(entry)
        if len(calls) > keep:
            del calls[:-keep]
        self.store.save()

    def list_calls(self, model: str = "", source: str = "",
                   level: str = "", since: float = 0, until: float = 0,
                   keyword: str = "", limit: int = 200) -> Dict[str, Any]:
        """按模型 / 来源 / 时间 / 关键字筛选调用历史"""
        calls = self.store.data.get("calls") or []
        out = []
        for c in reversed(calls):
            if model and c.get("model") != model:
                continue
            if source and c.get("provider") != source:
                continue
            if level and c.get("status") != level:
                continue
            ts = float(c.get("ts") or 0)
            if since and ts < since:
                continue
            if until and ts > until:
                continue
            if keyword and keyword.lower() not in json.dumps(c, ensure_ascii=False).lower():
                continue
            out.append(c)
            if len(out) >= limit:
                break
        models = sorted({c.get("model") for c in calls if c.get("model")})
        providers = sorted({c.get("provider") for c in calls if c.get("provider")})
        # matched 是筛选后的总条数（分页用），calls 是本次返回的切片
        return {"calls": out, "total": len(out), "matched": len(out),
                "models": models, "providers": providers}

    def export_billing(self, fmt: str = "detail"):
        """导出账单/详单。

        fmt='detail' -> 每个 key × 模型 一行（详单）
        fmt='bill'   -> 每个 key 一行（账单汇总）
        返回 (rows, headers, filename)
        """
        usage = self.store.data.get("usage") or {}
        keys = self.store.data.get("keys") or {}
        # key_id -> 名称
        id2name = {}
        for k, cfg in keys.items():
            id2name[cfg.get("key_id") or k[-8:]] = cfg.get("name", "")
        stamp = time.strftime("%Y%m%d-%H%M%S")

        if fmt == "bill":
            headers = ["账号", "密钥ID", "请求数", "输入tokens", "输出tokens",
                       "总tokens", "金额(元)"]
            rows = []
            for sk, e in usage.items():
                kid = sk.split(":", 1)[1] if sk.startswith("key:") else sk
                rows.append({
                    "账号": id2name.get(kid, "匿名" if sk == "anonymous" else kid),
                    "密钥ID": kid,
                    "请求数": e.get("requests", 0),
                    "输入tokens": e.get("input_tokens", 0),
                    "输出tokens": e.get("output_tokens", 0),
                    "总tokens": e.get("total_tokens", 0),
                    "金额(元)": round(e.get("cost_cny", 0.0), 4),
                })
            rows.sort(key=lambda r: r["金额(元)"], reverse=True)
            return rows, headers, f"amm-bill-{stamp}.csv"

        headers = ["账号", "密钥ID", "模型", "请求数", "输入tokens", "输出tokens",
                   "总tokens", "输入单价(元/百万)", "输出单价(元/百万)", "金额(元)"]
        rows = []
        for sk, e in usage.items():
            kid = sk.split(":", 1)[1] if sk.startswith("key:") else sk
            name = id2name.get(kid, "匿名" if sk == "anonymous" else kid)
            for model, mv in (e.get("by_model") or {}).items():
                inp = mv.get("input_tokens", 0)
                out = mv.get("output_tokens", 0)
                cost = mv.get("cost_cny", 0.0)
                pin = round(cost / inp * 1_000_000, 4) if inp else 0
                pout = round(cost / out * 1_000_000, 4) if out else 0
                rows.append({
                    "账号": name, "密钥ID": kid, "模型": model,
                    "请求数": mv.get("requests", 0),
                    "输入tokens": inp, "输出tokens": out,
                    "总tokens": mv.get("tokens", 0),
                    "输入单价(元/百万)": pin, "输出单价(元/百万)": pout,
                    "金额(元)": round(cost, 6),
                })
        rows.sort(key=lambda r: r["金额(元)"], reverse=True)
        return rows, headers, f"amm-billing-detail-{stamp}.csv"

    def usage_summary(self) -> Dict[str, Any]:
        usage = self.store.data.get("usage") or {}
        total = {"requests": 0, "total_tokens": 0, "cost_cny": 0.0}
        rows = []
        for k, e in usage.items():
            total["requests"] += e.get("requests", 0)
            total["total_tokens"] += e.get("total_tokens", 0)
            total["cost_cny"] += e.get("cost_cny", 0.0)
            bm = {}
            for model, mv in (e.get("by_model") or {}).items():
                bm[model] = dict(mv)
                bm[model]["tokens_fmt"] = fmt_tokens(mv.get("tokens", 0))
                bm[model]["cost_cny"] = round(mv.get("cost_cny", 0.0), 4)
            rows.append({
                "key": k,
                "requests": e.get("requests", 0),
                "input_tokens": e.get("input_tokens", 0),
                "output_tokens": e.get("output_tokens", 0),
                "total_tokens": e.get("total_tokens", 0),
                "total_tokens_fmt": fmt_tokens(e.get("total_tokens", 0)),
                "cost_cny": round(e.get("cost_cny", 0.0), 4),
                "by_model": bm,
                "metrics": e.get("metrics") or {},
            })
        rows.sort(key=lambda r: r["total_tokens"], reverse=True)
        total["cost_cny"] = round(total["cost_cny"], 4)
        total["total_tokens_fmt"] = fmt_tokens(total["total_tokens"])
        return {"totals": total, "rows": rows, "currency": "CNY"}


def _metrics(e2e_ms: float, out_tokens: int, ttft_ms: float = 0.0) -> dict:
    e2e_ms = max(e2e_ms, 0.001)
    gen = max(e2e_ms - (ttft_ms or 0), 0.001)
    n = max(int(out_tokens or 0), 1)
    return {
        "e2e_ms": round(e2e_ms, 2),
        "ttft_ms": round(float(ttft_ms or 0), 2),
        "itl_ms": round(gen / max(n - 1, 1), 2),
        "tpot_ms": round(gen / n, 2),
        "tps": round(n / (e2e_ms / 1000.0), 2),
    }


def _summarize(body: dict, limit: int = 1200) -> str:
    parts = []
    for m in (body.get("messages") or []):
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):
            txt = " ".join(str(x.get("text", "")) for x in c
                           if isinstance(x, dict) and x.get("type") == "text")
        else:
            txt = str(c or "")
        parts.append(f"[{m.get('role', '?')}] {txt[:300]}")
    return "\n".join(parts)[:limit]


def _stream_output(cap) -> str:
    """流式结束后把捕获到的文本转成调用历史的 output 字段。"""
    text = (cap.text() or "").strip()
    if not text:
        if getattr(cap, "events", 0) == 0:
            return "(stream) 未解析到文本"
        return "(stream) 输出为空"
    return text + ("…" if getattr(cap, "truncated", False) else "")


def _extract(data) -> str:
    try:
        if isinstance(data, dict) and data.get("choices"):
            msg = data["choices"][0].get("message") or {}
            for k in ("content", "reasoning_content", "reasoning", "text"):
                v = msg.get(k)
                if v:
                    return str(v)[:1500]
    except Exception:
        pass
    return ""


def _params(body: dict) -> dict:
    return {"temperature": body.get("temperature"),
            "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
            "top_p": body.get("top_p")}


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return key[:3] + "***"
    return key[:7] + "..." + key[-4:]


def _month() -> str:
    t = time.localtime()
    return f"{t.tm_year}-{t.tm_mon:02d}"


def _find_key(data: Dict[str, Any], key_cfg: Dict[str, Any]) -> str:
    """反查 key 明文对应的存储 key（用于读取该 key 的用量）"""
    for k, v in (data.get("keys") or {}).items():
        if v is key_cfg:
            return k
    return ""
