# ComfyUI-MiniMax-ZuihuoRefLoader

用户自有复刻版 **MiniMax H3 Media Loader / Reference Splitter** 自定义节点（ComfyUI V3 节点 API），用于把多张参考图 / 多段参考音频 / 提示词打包成 MiniMax H3 工作流所需的参考集（H3_REFS）。

本插件是**独立自有的自定义节点**，不修改任何第三方插件。功能与原 `ComfyUI-MiniMax-ContextIR` 的 220 / 221 节点完全等价，类名改为 `Zuihuo` 前缀，可单独安装到本地 ComfyUI 或 RunningHub。

> 派生自 [ComfyUI-Fantastic-MiniMaxH3-PromptBuilder](https://github.com/)（MIT License, Copyright (c) 2026 Adudeguyman），按 MIT 协议保留署名并开源。

---

## 包含的节点

| 节点 | 替代原节点 | 说明 |
| --- | --- | --- |
| `ZuihuoMiniMaxH3MediaLoader` | 原 220 `MiniMaxH3MediaLoaderFantastic` | 加载参考图 / 参考音频 / 提示词，输出 H3_REFS 参考集 |
| `ZuihuoMiniMaxH3ReferenceSplitter` | 原 221 `MiniMaxH3ReferenceSplitter` | 接收 H3_REFS，按分辨率/时长拆分为图片、视频、音频、文本等输出 |

### 外部接入自动覆盖内部输入（ComfyUI 原生行为）
两个节点都带有外部输入端口：

- `ZuihuoMiniMaxH3MediaLoader`：`images.image_0..8`（9 张图，Autogrow）、`audios.audio_0..2`（3 段音频，Autogrow）、`videos.video_0..2`（3 段视频，Autogrow）、`media_state`（STRING/JSON）、`prompt_text`（STRING）
- `ZuihuoMiniMaxH3ReferenceSplitter`：`references`（H3_REFS）、`aspect_ratio`、`duration`（FLOAT）、`short_edge_max`（INT）、`align_to`（INT）

只要某个端口被连线接上，ComfyUI 框架会把链路值传给节点的 `execute`、对应 widget 值被忽略——**节点本身无需写任何"禁用"逻辑**。这正是"外部节点接入后本节点内部输入自动失效"的效果，与 ComfyUI 原生节点原理一致。

### 兼容性
- 保留了原 `H3_REFS = io.Custom("H3_REFS")` 类型名，因此原工作流里 `MediaLoader → ReferenceSplitter` 的 `references` 连线（type=H3_REFS）可直接复用，下游消费者无需改动。
- 节点使用原生 V3 控件（Autogrow 图/音 + 标准 String/Float widget），**不依赖任何自定义前端 JS 面板**，无需 `web/` 目录。

---

## 环境要求

- ComfyUI 版本需支持 **V3 节点 API**（`comfy_api.latest` / `ComfyExtension` / `io.ComfyNode`）。
- Python 依赖：`av`、`pillow`、`numpy`（标准 ComfyUI 环境一般已具备）。

---

## 安装

### 方式一：Git 克隆（推荐，RunningHub / 本地通用）
进入你的 ComfyUI `custom_nodes` 目录，克隆本仓库：

```bash
cd <ComfyUI根目录>/custom_nodes
git clone https://github.com/git7129-hub/ComfyUI-MiniMax-ZuihuoRefLoader.git
cd ComfyUI-MiniMax-ZuihuoRefLoader
pip install -r requirements.txt
```

### 方式二：ComfyUI Manager（Git URL 安装）
在 ComfyUI Manager → Install Custom Nodes → 通过 Git URL 安装，粘贴：
`https://github.com/git7129-hub/ComfyUI-MiniMax-ZuihuoRefLoader.git`

### 方式三：上传 zip 包
将 `ComfyUI-MiniMax-ZuihuoRefLoader.zip` 解压后，把 `ComfyUI-MiniMax-ZuihuoRefLoader/` 文件夹整体放入 `<ComfyUI根目录>/custom_nodes/`。

> 安装后**完全重启** ComfyUI（不是刷新网页）。

---

## 验证节点已加载

```bash
curl -s http://127.0.0.1:8188/object_info/ZuihuoMiniMaxH3MediaLoader
```

应能看到输入口：`media_state`、`images`（Autogrow，9 图）、`audios`（Autogrow，3 音）、`videos`（Autogrow，3 视频）、`prompt_text`。同样核对 `ZuihuoMiniMaxH3ReferenceSplitter`。

---

## 目录结构

```
ComfyUI-MiniMax-ZuihuoRefLoader/
├── __init__.py          # V3 注册入口（comfy_entrypoint → ZuihuoRefLoaderExtension）
├── h3_fant_nodes.py     # 两个节点类定义
├── h3_media_io.py       # 自包含媒体解码助手（图片/音频/视频帧）
├── h3_resolution.py     # 分辨率 / 时长解析工具
├── requirements.txt
├── LICENSE              # MIT
└── README.md
```

## License

MIT © 2026 醉火 (git7129-hub)。派生自 MIT 项目 ComfyUI-Fantastic-MiniMaxH3-PromptBuilder。
