"""Zuihuo MiniMax H3 Reference Loader —— 用户自有自定义节点（复刻原 220 / 221）。

只含两个节点：
  * ZuihuoMiniMaxH3MediaLoader      替代原 MiniMaxH3MediaLoaderFantastic
  * ZuihuoMiniMaxH3ReferenceSplitter 替代原 MiniMaxH3ReferenceSplitter

两个节点都带外部输入口（图 / 音 / 提示词 / duration），遵循 ComfyUI 原生行为：
外部端口一接通，框架把链路值传给 execute、对应 widget 值被忽略，无需节点自写禁用逻辑。
本插件不修改原 ComfyUI-MiniMax-ContextIR，可独立安装到 RunningHub / 本地 ComfyUI。
"""

from comfy_api.latest import ComfyExtension

from .h3_fant_nodes import (
    ZuihuoMiniMaxH3MediaLoader,
    ZuihuoMiniMaxH3ReferenceSplitter,
)


class ZuihuoRefLoaderExtension(ComfyExtension):
    async def get_node_list(self):
        return [
            ZuihuoMiniMaxH3MediaLoader,
            ZuihuoMiniMaxH3ReferenceSplitter,
        ]


async def comfy_entrypoint():
    return ZuihuoRefLoaderExtension()


__all__ = [
    "ZuihuoMiniMaxH3MediaLoader",
    "ZuihuoMiniMaxH3ReferenceSplitter",
    "ZuihuoRefLoaderExtension",
    "comfy_entrypoint",
]
