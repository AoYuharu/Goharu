"""Channel 发送工具 —— SendFile：把本机文件/公网资源经指定 channel 发给用户。

与 Gateway 的接线（镜像 memory_tools.set_memory_manager 的持有模块模式）：
Gateway 在 channel 启动后调 register_channel_sender(channel_type, fn)，
fn(path_or_url, caption, channel_id=None) -> (ok: bool, message: str)。
本模块不 import Gateway（方向：Gateway → Tools 单向注入）。
"""

import json
import logging

from Tools.registry import registry

logger = logging.getLogger(__name__)

# channel_type → 发送函数；多实例同 type 时后者覆盖（发最近挂载的）
_senders = {}


def register_channel_sender(channel_type: str, fn):
    """Gateway 注入：注册某种 channel 的文件发送入口。"""
    _senders[str(channel_type)] = fn
    logger.info("Channel tools: sender registered for '%s'", channel_type)


def registered_channel_types():
    return sorted(_senders.keys())


def _sender_for(channel: str, channel_id=None):
    """按 type（可选再按 channel_id 精确匹配）取发送函数。"""
    fn = _senders.get(str(channel or "").strip())
    if fn is None:
        return None
    if channel_id:

        def _filtered(path_or_url, caption="", cid=None):
            return fn(path_or_url, caption, channel_id=cid)

        return _filtered
    return fn


def send_file(channel: str, path: str, caption: str = "",
              file_type: str = "") -> str:
    """SendFile 工具 handler：经指定 channel 发送文件。

    file_type 可选覆盖自动推断（image/video/voice/file）——例如 .mp3
    默认按语音发送，传 file_type="file" 则作为普通文件发送。
    """
    channel = str(channel or "").strip()
    path = str(path or "").strip()
    caption = str(caption or "").strip()
    file_type = str(file_type or "").strip().lower()
    if not channel or not path:
        return json.dumps(
            {"status": "error", "error": "channel 和 path 都是必填参数"},
            ensure_ascii=False,
        )
    fn = _sender_for(channel)
    if fn is None:
        return json.dumps(
            {"status": "error",
             "error": f"channel '{channel}' 不可用或不支持发文件"
                      f"（已注册: {registered_channel_types()}）"},
            ensure_ascii=False,
        )
    try:
        ok, message = fn(path, caption, file_type=file_type or None)
    except Exception as e:
        logger.warning("send_file failed: %s", e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    logger.info("send_file: channel=%s path=%s type=%s ok=%s msg=%s",
                channel, path, file_type or "auto", ok, message)
    return json.dumps(
        {"status": "success" if ok else "error",
         "channel": channel, "path": path,
         "file_type": file_type or "auto",
         "message": message or ("已受理发送" if ok else "发送失败，见日志")},
        ensure_ascii=False,
    )


registry.register(
    name="SendFile",
    description=(
        "Send a local file or a public URL to the user via a messaging channel "
        "(e.g. qqbot). Use it to deliver generated reports, images, or any "
        "artifact the user asked for, instead of pasting long content into chat. "
        "channel is a registered channel type (call once to discover, error "
        "lists available ones). Local files up to 5MB; URLs are fetched by the "
        "platform. Media type is auto-inferred: images→image, videos→video, "
        "everything else (including audio files like .mp3)→plain file. Pass "
        "file_type explicitly to override the inference."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "Channel type to send through, e.g. 'qqbot'",
            },
            "path": {
                "type": "string",
                "description": "Local file path or public http(s) URL to send",
            },
            "caption": {
                "type": "string",
                "description": "Optional caption text sent along with the file",
            },
            "file_type": {
                "type": "string",
                "enum": ["image", "video", "file"],
                "description": "Optional override of auto-inferred media type",
            },
        },
        "required": ["channel", "path"],
    },
    handler=send_file,
    group="channel",
)
