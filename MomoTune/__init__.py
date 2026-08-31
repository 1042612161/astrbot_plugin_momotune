"""MomoTune - 日系二次元风格点歌插件。"""

from gsuid_core.sv import Plugins

Plugins(
    name="MomoTune",
    disable_force_prefix=True,
    allow_empty_prefix=True,
    alias=["MomoTune", "点歌"],
)

from . import momotune_config  # noqa: F401
from . import momotune_music  # noqa: F401
