"""MomoTune 配置。"""

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.gs_config import StringConfig

from .config_default import CONFIG_DEFAULT

CONFIG_PATH = get_res_path() / "MomoTune" / "config.json"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

MOMOTUNE_CONFIG = StringConfig("MomoTune", CONFIG_PATH, CONFIG_DEFAULT)
MOMOTUNE_CONFIG.plugin_name = "MomoTune"
