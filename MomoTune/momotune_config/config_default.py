"""MomoTune WebConsole 配置项。"""

from typing import Dict

from gsuid_core.utils.plugins_config.models import GSC, GsIntConfig, GsStrConfig


CONFIG_DEFAULT: Dict[str, GSC] = {
    "ncm_api_base": GsStrConfig(
        title="网易云 API 地址",
        desc="NCM-plugin 网易云服务地址",
        data="https://api.ames.cc.cd",
    ),
    "ncm_kugou_api_base": GsStrConfig(
        title="酷狗 API 地址",
        desc="NCM-plugin 酷狗服务地址",
        data="http://127.0.0.1:3040",
    ),
    "ncm_search_limit": GsIntConfig(
        title="搜索结果数量",
        desc="每次搜索最多展示的曲目数量",
        data=10,
        max_value=30,
    ),
    "ncm_quality": GsStrConfig(
        title="音质等级",
        desc="网易云音质等级，酷狗会自动映射",
        data="exhigh",
        options=["standard", "higher", "exhigh", "lossless", "hires", "jyeffect", "sky", "jymaster"],
    ),
    "ncm_cookie": GsStrConfig(
        title="网易云 Cookie",
        desc="可选；推荐在 NCM-plugin 后端配置，插件侧留空即可",
        data="",
        secret=True,
    ),
    "ncm_kugou_cookie": GsStrConfig(
        title="酷狗 Cookie",
        desc="可选；酷狗搜索需要登录凭据时填写 token=...;userid=...",
        data="",
        secret=True,
    ),
}
