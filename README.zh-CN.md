# MomoTune（AstrBot 网易云点歌）

这是 [MomoTune](https://github.com/MimoKit/MomoTune) 的 AstrBot 适配版，保留原有网易云搜索、候选卡片、数字选歌、歌曲 ID 直达、歌曲卡片和语音发送流程。酷狗源已移除；本插件不会请求或展示歌词接口。

## 使用

- `点歌 晴天`：搜索网易云；`唱歌`、`来一首` 为同义指令。
- 搜索结果超过一首时回复 `1`～`10` 选择，选择状态按会话和用户隔离；默认 60 秒过期，可配置为 30～300 秒。
- `点歌 421423808`：按网易云歌曲 ID 直接播放。

卡片模板、字体和配色沿用 MomoTune，渲染统一使用 [pytakumi](https://github.com/KimigaiiWuyi/pytakumi)；封面会先转为 Data URI，避免发送时外链失效。

## 配置

AstrBot WebUI 会从 `_conf_schema.json` 生成配置。`ncm_api_base` 提供两种选择：

1. `https://api.ames.cc.cd`：MomoTune 原有兼容接口（`/cloudsearch`、`/song/detail`、`/song/url/v1`）。
2. `https://api.qijieya.cn/meting/`：MetingAPI，固定 `server=netease`，按 Meting 参数调用 `type=search`、`type=url`、`type=pic`。搜索结果中的 `url` 解析出歌曲 ID，并缓存 `name`、`artist`、`pic` 等详情；直达 ID 且缓存不存在时再次使用 `type=search` 按 ID 查询，绝不调用 `type=song` 或 `type=lrc`。

`proxy` 用于 API 搜索、详情、封面和音频下载，例如 `http://127.0.0.1:7890`，留空直连。`ncm_cookie` 仅发送给 MomoTune 兼容接口。

`selection_ttl_seconds` 控制搜索候选等待数字回复的有效期，默认 `60` 秒，允许范围为 `30`～`300` 秒；代码也会执行边界限制，超出范围的值会自动收敛到最近边界。

## MetingAPI 适配设计

MetingAPI 返回的是扁平歌曲数组（`name`、`artist`、`url`、`pic`），而原接口返回嵌套 `result.songs`。适配层将两者归一到同一 `Song` 模型：从 `url` 查询参数提取 ID，搜索时缓存歌曲详情，播放时调用 `type=url`，封面直接使用 `pic` 并下载为 Data URI。直达 ID 没有缓存时，使用 `type=search` 按 ID 补充详情，不依赖 `type=song`；这样候选选择、卡片渲染、ID 直达和下载逻辑无需分叉，能最大限度保持原有功能行为。MetingAPI 不提供专辑/时长时按缺省值展示。

## 安装

将仓库放入 `AstrBot/data/plugins/astrbot_plugin_momotune`，安装 `requirements.txt` 后重载插件。依赖：`httpx>=0.27.0`、`pytakumi>=0.1.0`。

## 项目结构

```text
astrbot_plugin_momotune/
├── main.py                    # AstrBot 命令、事件和消息发送
├── MomoTune/
│   ├── models.py              # 统一歌曲模型与业务异常
│   ├── sources.py             # 两种网易云 API 和音频下载
│   ├── renderer.py            # pytakumi 卡片渲染
│   ├── selection.py           # 用户候选与可配置 TTL
│   └── assets/
│       ├── templates/search_list.html
│       └── fonts/LXGWWenKai-Regular.ttf
├── _conf_schema.json          # AstrBot 插件配置
├── metadata.yaml              # 插件元数据
├── logo.png                   # AstrBot 插件图标
├── requirements.txt           # 运行依赖
├── preview/                   # README 预览图片
└── LICENSE
```

分层边界以“是否依赖 AstrBot”为准：只有 `main.py` 了解 `AstrMessageEvent`、消息组件和配置对象；`MomoTune` 包只处理歌曲、网络、渲染和候选状态，便于独立测试并避免循环依赖。保留首字母大写的包名是为了兼容原仓库已有路径，避免在 Windows 和 Linux 间出现大小写冲突。

本项目基于 GPLv3 许可的 MomoTune 代码和资源进行适配，请保留原版权声明。
