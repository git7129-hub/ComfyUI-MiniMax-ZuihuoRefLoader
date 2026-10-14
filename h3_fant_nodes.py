"""用户自有复刻版 MiniMax H3 Media Loader / Reference Splitter（原 220 / 221）。

功能与原 ComfyUI-MiniMax-ContextIR 的 220 / 221 节点完全等价，但类名改为
Zuihuo 前缀、可独立安装，不触碰原插件。两个节点都带外部输入口（图 / 音 /
提示词 / duration），遵循 ComfyUI 原生行为：外部端口一接通，框架把链路值传给
execute、对应 widget 值被忽略，无需节点自写禁用逻辑。

依赖：h3_media_io（自包含解码助手，仅依赖 numpy/torch/PIL/av/folder_paths）、
h3_resolution（仅依赖 comfy_api.latest + math）。两者均不 import 原插件内部模块。
"""

import json

import torch
import torch.nn.functional as F

import comfy.utils
from comfy_api.latest import ComfyExtension, io

from . import h3_media_io as media_io
from .h3_resolution import RATIOS, _dual_ratio_options, parse_size


PICTURES = 90
VIDEOS = 30
VIDEO_AUDIOS = 30
AUDIOS = 90
STRINGS = 30
# Reference Splitter output port caps (smaller than the loading limits)
SPLIT_PICTURES = 9
SPLIT_VIDEOS = 3
SPLIT_VIDEO_AUDIOS = 3
SPLIT_AUDIOS = 3
SPLIT_STRINGS = 3

H3_REFS = io.Custom("H3_REFS")

# Splitter outputs adapt to whatever is connected (picture slots are IMAGE,
# audio slots AUDIO), so validation can't map a dynamic socket count back to
# static per-slot types.
SLOT_TEMPLATE = io.MatchType.Template(
    "minimax_h3_splitter_slot", allowed_types=[io.Image, io.Audio])


def _media_names():
    return (
        [f"picture_{i}" for i in range(1, PICTURES + 1)]
        + [f"video_{i}" for i in range(1, VIDEOS + 1)]
        + [f"video_audio_{i}" for i in range(1, VIDEO_AUDIOS + 1)]
        + [f"audio_{i}" for i in range(1, AUDIOS + 1)]
    )


def _partition(items):
    """Split items into the native groups, preserving list order.

    A video's split audio goes to the paired group (its <Audio N> is
    emitted just after its <Video N>) or to the standalone group,
    depending on the item's audio_mode.
    """
    pictures, videos, video_audios, audios, standalone_tracks, strings = \
        [], [], [], [], [], []
    for item in items:
        # Items switched off in the loader are kept in the list but never
        # reach the model, so the tag numbering closes up around them.
        if isinstance(item, dict) and item.get("enabled") is False:
            continue
        kind = item.get("kind")
        if kind == "picture":
            pictures.append(item)
        elif kind == "video":
            mode = item.get("audio_mode", "paired")
            has_audio = bool(item.get("has_audio"))
            if has_audio and mode == "standalone":
                # standalone mode sends only the soundtrack — no video slot
                standalone_tracks.append(item)
            else:
                videos.append(item)
                if has_audio and mode == "paired":
                    video_audios.append(item)
                else:
                    video_audios.append(None)
        elif kind == "audio":
            audios.append(item)
        elif kind == "string":
            strings.append(item)
    # standalone video tracks come after the standalone audio items so the
    # <Audio M> ordinals match the splitter's audio_M sockets
    return pictures, videos, video_audios, audios + standalone_tracks, strings


def _ordered_autogrow(values):
    """Flatten an Autogrow input dict into a list ordered by socket suffix.

    Autogrow hands us {socket_name: value}; the numeric suffix decides the
    order so image_0, image_1, ... keep the slot order the user wired.
    ``None`` entries (socket present but nothing flowing) are dropped so they
    can never occupy a reference slot.
    """
    if not values:
        return []

    def sort_key(item):
        try:
            return int(str(item[0]).rsplit("_", 1)[-1])
        except ValueError:
            return 10_000

    return [value for _, value in sorted(values.items(), key=sort_key)
            if value is not None]


class ZuihuoMiniMaxH3MediaLoader(io.ComfyNode):
    """Drag-and-drop / file-picker loader for H3 reference media (Fant replica).

    Extended with external input ports so the reference media can be driven by
    the graph instead of (or in addition to) the drag-and-drop panel:

      * ``images``       - Autogrow IMAGE ports (image_0, image_1, ...)
      * ``audios``       - Autogrow AUDIO ports (audio_0, audio_1, ...)
      * ``videos``       - Autogrow VIDEO ports (video_0, video_1, video_2)
      * ``prompt_text``  - STRING port for the <...> prompt text
                           (named prompt_text, not prompt: see the tooltip)

    Each group is independent and *replaces* its counterpart in
    ``media_state`` only when at least one port is actually connected, so
    leaving the new ports unwired reproduces the original behaviour exactly.
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ZuihuoMiniMaxH3MediaLoader",
            display_name="Zuihuo Media Loader",
            description=(
                "Load MiniMax H3 reference media by drag-and-drop, file picker, "
                "or the external image/audio/prompt_text input ports. "
                "Wire 'references' to the MiniMax H3 Reference Splitter (or use the "
                "node's '+ Native-output splitter' button) when you want individual "
                "slots. A video's soundtrack can be split off and paired with it "
                "automatically."
            ),
            category="conditioning/video_models",
            inputs=[
                io.String.Input(
                    "media_state",
                    multiline=False,
                    default="[]",
                    tooltip="JSON list of media items, written by the node's panel.",
                ),
                io.Autogrow.Input(
                    "images",
                    optional=True,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Image.Input(
                            "image",
                            tooltip="外部图片输入；按 image_0、image_1 … 顺序占用参考图槽位。"
                                    "任一槽位接通后，本组整体替代 media_state 里的图片项。",
                        ),
                        prefix="image_",
                        min=0,
                        # 与 201 UnifiedToVideo 的 ref_images 规格保持一致（那边 max=9），
                        # 用 SPLIT_PICTURES 而不是加载上限 PICTURES(90)，否则界面会白渲染
                        # 90 个端口，而且超过 9 张下游根本收不下。
                        max=SPLIT_PICTURES,
                    ),
                ),
                io.Autogrow.Input(
                    "audios",
                    optional=True,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Audio.Input(
                            "audio",
                            tooltip="外部音频输入；按 audio_0、audio_1 … 顺序占用参考音频槽位。"
                                    "任一槽位接通后，本组整体替代 media_state 里的音频项。",
                        ),
                        prefix="audio_",
                        min=0,
                        max=SPLIT_AUDIOS,
                    ),
                ),
                io.Autogrow.Input(
                    "videos",
                    optional=True,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Video.Input(
                            "video",
                            tooltip="外部视频输入；按 video_0、video_1、video_2 顺序占用参考视频槽位"
                                    "（最多 3 个，与 Reference Splitter 的 video_1..3 对应）。"
                                    "任一槽位接通后，本组整体替代 media_state 里的视频项。",
                        ),
                        prefix="video_",
                        min=0,
                        max=SPLIT_VIDEOS,
                    ),
                ),
                io.String.Input(
                    "prompt_text",
                    multiline=True,
                    default="",
                    tooltip="外部提示词；非空时替代 media_state 里的 string 项"
                            "（即 subject_definitions 那段正文）。"
                            "注意：刻意不叫 prompt —— prompt 是 ComfyUI 的保留 hidden 输入名，"
                            "同名会被框架注入的整个 prompt 字典覆盖。",
                ),
            ],
            outputs=[H3_REFS.Output("references")],
        )

    @classmethod
    def validate_inputs(cls, media_state="[]", images=None, audios=None,
                        videos=None, prompt_text="", **kwargs):
        try:
            items = json.loads(media_state or "[]")
        except Exception:
            return "Media Loader state is corrupt; clear the node and re-add media."
        if not isinstance(items, list):
            return "Media Loader state is corrupt; clear the node and re-add media."
        pics = sum(1 for i in items if i.get("kind") == "picture")
        vids = sum(1 for i in items if i.get("kind") == "video")
        if pics > PICTURES:
            return f"{pics} pictures loaded; H3 accepts {PICTURES}."
        if vids > VIDEOS:
            return f"{vids} videos loaded; H3 accepts {VIDEOS}."
        if len(_ordered_autogrow(images)) > PICTURES:
            return f"{len(_ordered_autogrow(images))} external images wired; H3 accepts {PICTURES}."
        if len(_ordered_autogrow(audios)) > AUDIOS:
            return f"{len(_ordered_autogrow(audios))} external audios wired; H3 accepts {AUDIOS}."
        if len(_ordered_autogrow(videos)) > VIDEOS:
            return f"{len(_ordered_autogrow(videos))} external videos wired; H3 accepts {VIDEOS}."
        return True

    @classmethod
    def fingerprint_inputs(cls, media_state="[]", images=None, audios=None,
                           videos=None, prompt_text="", **kwargs):
        # Tensor payloads are covered by ComfyUI's normal input hashing; this
        # only needs to catch the file-panel state and the wiring silhouette.
        return (
            media_state,
            prompt_text or "",
            tuple(sorted(images or {})),
            tuple(sorted(audios or {})),
            tuple(sorted(videos or {})),
        )

    @classmethod
    def execute(cls, media_state="[]", images=None, audios=None, videos=None,
                prompt_text="", **kwargs) -> io.NodeOutput:
        # **kwargs 是必须的：ComfyUI 的 EXECUTE_NORMALIZED 会把 input_data_all 里的
        # 每个键原封不动转发给 execute()，任何多余键（例如框架注入的 hidden 输入）
        # 都会变成 "unexpected keyword argument" 直接炸掉节点。
        try:
            items = json.loads(media_state or "[]")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Media Loader state is corrupt; clear the node and re-add media.") from exc
        if not isinstance(items, list):
            raise ValueError("Media Loader state is corrupt; clear the node and re-add media.")

        pictures, videos_from_state, video_audios, audios_from_state, strings = _partition(items)

        def _trim(item):
            trim = item.get("trim") if isinstance(item, dict) else None
            if not isinstance(trim, dict):
                return None, None

            def num(value):
                try:
                    value = float(value)
                    return value if value > 0 else None
                except (TypeError, ValueError):
                    return None

            return num(trim.get("start")), num(trim.get("end"))

        # ---- external input ports: a wired group replaces its panel files ----
        # 先解析外部输入口：已接通的分组直接采用外部数据，**不再去读面板里的文件**，
        # 否则面板里残留的旧文件路径（例如换机器后已不存在）会先抛错。
        ext_pics = _ordered_autogrow(images)[:PICTURES]
        ext_auds = _ordered_autogrow(audios)[:AUDIOS]
        ext_vids = _ordered_autogrow(videos)[:VIDEOS]
        text = (prompt_text or "").strip()

        if ext_pics:
            pic_t = ext_pics
            print(f"[MiniMaxH3 Media Loader] {len(ext_pics)} external image(s) replace the panel's pictures")
        else:
            pic_t = [
                media_io.load_image(i["file"], crop=i.get("crop"))
                for i in pictures[:PICTURES]
            ]

        if ext_vids:
            vid_t = ext_vids
            print(f"[MiniMaxH3 Media Loader] {len(ext_vids)} external video(s) replace the panel's videos")
        else:
            vid_t = [
                media_io.load_video_frames(
                    i["file"],
                    start=_trim(i)[0],
                    end=_trim(i)[1],
                    crop=i.get("crop"),
                )
                for i in videos_from_state[:VIDEOS]
            ]
        vaud_t = [
            media_io.extract_audio(i["file"], start=_trim(i)[0], end=_trim(i)[1]) if i else None
            for i in video_audios[:VIDEO_AUDIOS]
        ]

        if ext_auds:
            aud_t = ext_auds
            print(f"[MiniMaxH3 Media Loader] {len(ext_auds)} external audio(s) replace the panel's audios")
        else:
            aud_t = []
            for i in audios_from_state[:AUDIOS]:
                if i.get("kind") == "video":
                    aud_t.append(media_io.extract_audio(i["file"], start=_trim(i)[0], end=_trim(i)[1]))
                else:
                    aud_t.append(media_io.load_audio(i["file"], start=_trim(i)[0], end=_trim(i)[1]))

        if text:
            string_out = [text]
            print("[MiniMaxH3 Media Loader] external prompt replaces the panel's string items")
        else:
            string_out = [s.get("text", "") for s in strings]

        bundle = {
            "pictures": pic_t,
            "videos": vid_t,
            "video_audios": vaud_t,
            "audios": aud_t,
            "strings": string_out,
            "items": items,
        }
        return io.NodeOutput(bundle)


class ZuihuoMiniMaxH3ReferenceSplitter(io.ComfyNode):
    """Fan a `references` bundle out into fixed 9/3/3/3/3 media slots.

    Empty slots are padded with None; execute also returns the two
    resolution pairs and the duration from its own widgets.
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ZuihuoMiniMaxH3ReferenceSplitter",
            display_name="Zuihuo Reference Splitter",
            description=(
                "Split a MiniMax H3 references bundle into individual picture / video / "
                "video_audio / audio / string slots for the H3 reference nodes."
            ),
            category="conditioning/video_models",
            inputs=[
                H3_REFS.Input("references"),
                io.DynamicCombo.Input(
                    "aspect_ratio",
                    options=_dual_ratio_options(),
                    tooltip="Aspect ratio for both size windows; each follows the selected ratio.",
                ),
                io.Float.Input(
                    "duration",
                    default=5.0,
                    min=1.0,
                    max=3600.0,
                    tooltip="Duration of the video to generate (seconds).",
                ),
                io.Int.Input(
                    "short_edge_max",
                    default=0,
                    min=0,
                    max=8192,
                    step=8,
                    tooltip="Short edge max pixels; 0 = no scaling. "
                            "Pictures are downscaled so their short edge fits, then aligned.",
                ),
                io.Int.Input(
                    "align_to",
                    default=16,
                    min=1,
                    max=128,
                    step=1,
                    tooltip="Pixel alignment for scaled dimensions.",
                ),
            ],
            outputs=(
                [io.MatchType.Output(SLOT_TEMPLATE, display_name=f"picture_{i}")
                 for i in range(1, SPLIT_PICTURES + 1)]
                + [io.MatchType.Output(SLOT_TEMPLATE, display_name=f"video_{i}")
                   for i in range(1, SPLIT_VIDEOS + 1)]
                + [io.MatchType.Output(SLOT_TEMPLATE, display_name=f"video_audio_{i}")
                   for i in range(1, SPLIT_VIDEO_AUDIOS + 1)]
                + [io.MatchType.Output(SLOT_TEMPLATE, display_name=f"audio_{i}")
                   for i in range(1, SPLIT_AUDIOS + 1)]
                + [io.String.Output(display_name=f"string_{i}")
                   for i in range(1, SPLIT_STRINGS + 1)]
                + [io.Int.Output("width_1", tooltip="First size: width (multiple of 32)."),
                   io.Int.Output("height_1", tooltip="First size: height (multiple of 32)."),
                   io.Int.Output("width_2", tooltip="Second size: width (multiple of 32)."),
                   io.Int.Output("height_2", tooltip="Second size: height (multiple of 32)."),
                   io.Float.Output("duration", tooltip="Requested video duration (seconds).")]
            ),
        )

    @classmethod
    def execute(cls, references=None, aspect_ratio=None, duration: float = 5.0,
                short_edge_max: int = 0, align_to: int = 16, **kwargs) -> io.NodeOutput:
        bundle = references or {}
        # positional padding like the original Fant splitter: empty slots stay
        # None so downstream nodes can tell unused slots apart
        def _pad(seq, n):
            seq = list(seq or [])[:n]
            return seq + [None] * (n - len(seq))

        pic_t = _pad(bundle.get("pictures"), SPLIT_PICTURES)
        vid_t = _pad(bundle.get("videos"), SPLIT_VIDEOS)
        vaud_t = _pad(bundle.get("video_audios"), SPLIT_VIDEO_AUDIOS)
        aud_t = _pad(bundle.get("audios"), SPLIT_AUDIOS)
        str_t = _pad([s if isinstance(s, str) else "" for s in bundle.get("strings") or []],
                     SPLIT_STRINGS)

        # global image scaling: downscale pictures so short edge <= short_edge_max,
        # both dimensions aligned to align_to; only downscales (never upscales)
        if short_edge_max > 0:
            scaled = []
            for img in pic_t:
                if img is None:
                    scaled.append(None)
                    continue
                _, h, w, _ = img.shape
                short = min(h, w)
                if short <= short_edge_max:
                    scaled.append(img)
                    continue
                scale = short_edge_max / short
                new_h = max(align_to, round(h * scale / align_to) * align_to)
                new_w = max(align_to, round(w * scale / align_to) * align_to)
                t = img[..., :3].permute(0, 3, 1, 2).float()
                t = F.interpolate(t, size=(new_h, new_w), mode="bilinear", align_corners=False)
                scaled.append(t.permute(0, 2, 3, 1).contiguous().to(img.dtype))
            pic_t = scaled

        ratio_label = (aspect_ratio or {}).get("aspect_ratio") or ""
        ratio = ratio_label.split(" ")[0]
        if ratio not in RATIOS:
            raise ValueError(f"Unknown MiniMax H3 aspect ratio: {ratio_label!r}")
        width_1, height_1, _ = parse_size((aspect_ratio or {}).get("size1"), ratio)
        width_2, height_2, _ = parse_size((aspect_ratio or {}).get("size2"), ratio)
        return io.NodeOutput(
            *(pic_t + vid_t + vaud_t + aud_t + str_t
              + [width_1, height_1, width_2, height_2, float(duration)]))


class ZuihuoRefLoaderExtension(ComfyExtension):
    async def get_node_list(self):
        return [ZuihuoMiniMaxH3MediaLoader, ZuihuoMiniMaxH3ReferenceSplitter]
