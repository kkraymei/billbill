"""路径和设置。数据放在 exe 旁边（macOS 放应用支持目录），换机器时整个目录带走。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", ROOT))     # 打包后的只读资源


def _app_dir() -> Path:
    override = os.environ.get("BILLBILL_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "BillBill"
        return Path(sys.executable).resolve().parent
    return ROOT


APP_DIR = _app_dir()
DATA_DIR = APP_DIR / "data"
TEMPLATE_DIR = APP_DIR / "templates"
DB_PATH = DATA_DIR / "billbill.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "language": "zh",           # zh / en
    "theme": "system",          # system / light / dark
    "template": "default",
    # 关：只有本人账户互转才不计收支。开：摘要写着「转账」的都不计。
    "transfer_words_neutral": False,
    "default_currency": "CNY",
    "large_amount": 1000.0,     # 超过这个金额打「大额」标签
    "dedup_days": 3,            # 跨渠道去重的日期窗口
    "llm_enable": False,
    "llama_model_path": "",     # 文字模型：认不出的列、归类
    "llama_vision_path": "",    # 视觉模型：扫描件、截图
    "llama_mmproj_path": "",
    "llama_server_path": "",
    "llama_backend": "metal" if sys.platform == "darwin" else "vulkan",
    "llama_context": 16384,
    "llama_gpu_layers": 99,
    "llama_start_timeout": 300,
    "llm_timeout": 900,
    "llm_max_tokens": 8000,
}


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        saved = json.loads(SETTINGS_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return settings
    # 旧版留下的键不再读，下次保存时自然消失。
    settings.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})
    return settings


def save_settings(patch: dict) -> dict:
    settings = {**load_settings(), **patch}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), "utf-8")
    return settings
