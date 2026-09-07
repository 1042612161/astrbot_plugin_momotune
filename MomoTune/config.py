"""MomoTune 配置取值与类型转换。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _filename(raw: Mapping[str, object], key: str) -> str:
    """读取资源文件名；资源目录由 renderer.py 固定，禁止路径穿越。"""

    value = str(raw[key]).strip()
    path = Path(value)
    if not value or path.name != value:
        raise ValueError(f"插件配置项 {key} 必须是文件名：{value!r}")
    return value


@dataclass(frozen=True, slots=True)
class RendererConfig:
    """Renderer 所需的已解析配置。"""

    template_filename: str
    font_filename: str
    max_cover_bytes: int


def load_renderer_config(raw: Mapping[str, object]) -> RendererConfig:
    """从 AstrBot 配置读取渲染参数并完成类型转换。"""

    max_cover_size = raw["max_cover_size"]
    return RendererConfig(
        template_filename=_filename(raw, "template_filename"),
        font_filename=_filename(raw, "font_filename"),
        max_cover_bytes=max_cover_size * 1024 * 1024,
    )
