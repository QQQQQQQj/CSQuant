"""统一配置入口：.env（密钥/本地路径）+ config.json（策略参数）。

纪律：代码中禁止散读 os.environ，一律经本模块。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


def _load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class Config:
    REQUIRED_STRATEGY_KEYS = [
        "model_version", "blend", "market_weights", "market_regime_bands",
        "item_weights", "signal_thresholds", "confidence_weights",
        "risk_bands", "fee_table", "slippage_bands", "collect",
    ]

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else PROJECT_ROOT
        self._dotenv = _load_dotenv(self.root / ".env")
        # 代理支持：.env 中的代理配置注入环境变量（requests 默认 trust_env=True 自动生效）
        # 不覆盖系统已有环境变量。访问 steamcommunity.com 通常需要代理/加速器。
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
            if key not in os.environ and self._dotenv.get(key):
                os.environ[key] = self._dotenv[key]
        cfg_path = self.root / "config.json"
        if not cfg_path.exists():
            raise ConfigError(f"缺少策略配置文件: {cfg_path}")
        self.strategy: dict = json.loads(cfg_path.read_text(encoding="utf-8"))
        missing = [k for k in self.REQUIRED_STRATEGY_KEYS if k not in self.strategy]
        if missing:
            raise ConfigError(f"config.json 缺少必需键: {missing}")

    # ---- 密钥 / 本地参数（环境变量优先于 .env）----
    def get(self, key: str, default: str | None = None) -> str | None:
        return os.environ.get(key) or self._dotenv.get(key, default)

    @property
    def csqaq_token(self) -> str | None:
        return self.get("CSQAQ_API_TOKEN")

    @property
    def steamdt_key(self) -> str | None:
        return self.get("STEAMDT_API_KEY")

    @property
    def steam_id_64(self) -> str | None:
        return self.get("STEAM_ID_64")

    @property
    def steam_login_secure(self) -> str | None:
        return self.get("STEAM_LOGIN_SECURE")

    @property
    def qq_bot_http(self) -> str:
        return self.get("QQ_BOT_HTTP", "http://127.0.0.1:3000") or "http://127.0.0.1:3000"

    @property
    def qq_bot_token(self) -> str | None:
        return self.get("QQ_BOT_TOKEN")

    @property
    def qq_group_id(self) -> str | None:
        return self.get("QQ_GROUP_ID")

    @property
    def db_path(self) -> Path:
        p = self.get("DB_PATH")
        return Path(p) if p else self.root / "data" / "csquant.db"

    @property
    def log_level(self) -> str:
        return (self.get("LOG_LEVEL", "INFO") or "INFO").upper()

    # ---- 策略参数 ----
    @property
    def model_version(self) -> str:
        return self.strategy["model_version"]

    @property
    def config_version(self) -> str:
        """自动配置版本：model_version + 策略参数内容哈希。

        任何权重/阈值变更都会改变哈希，不依赖人工递增版本号；
        信号/回测均以此版本落库，可互相关联。
        """
        canonical = json.dumps(self.strategy, sort_keys=True,
                               ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
        return f"{self.model_version}+{digest}"

    @property
    def offline(self) -> bool:
        """离线模式（质量检查/测试）：禁用一切外部 Provider 与 QQ 通道。"""
        return os.environ.get("CSQUANT_OFFLINE") == "1"


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """测试用：重置单例。"""
    global _config
    _config = None
