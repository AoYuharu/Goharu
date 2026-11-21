"""Netease lossless download tool.

按歌曲 ID 从网易云下载无损 FLAC（br=999000 音源），自动写标签。
无无损音源时可回退到 320 kbps MP3（可用 allow_fallback 关闭）。
Cookie 解析顺序：显式 cookie_file → 环境变量 NETEASE_COOKIE →
MCP netease-music 脚本目录下的 MUSIC/MyJar/*.txt → ~/.netease_cookie.txt。
"""

import json
import os
import re
from pathlib import Path

import requests

from Tools.config_proxy import get_config
from Tools.registry import registry

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
API_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 300
COOKIE_NAMES = ("goharu.txt", "Mum.txt", "jtt.txt", "HYH.txt")
LOSSLESS_BR = 999000
FALLBACK_BR = 320000


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def _extract_cookie(text: str):
    for pattern in (r"(?i)-H\s+'Cookie:\s*([^']+)'", r"(?i)-b\s+'([^']+)'"):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    stripped = (text or "").strip()
    if "=" in stripped and ";" in stripped and "curl " not in stripped:
        return stripped
    return None


def _candidate_cookie_files(explicit: str = ""):
    """依次产出候选来源：Path 表示文件，tuple 表示已取到的 cookie。"""
    if explicit and explicit.strip():
        yield Path(explicit).expanduser()
        return
    env = os.getenv("NETEASE_COOKIE")
    if env and env.strip():
        yield ("__env__", env.strip())
        return
    servers = get_config().get("tools.mcp.servers", {}) or {}
    for spec in servers.values():
        if not isinstance(spec, dict):
            continue
        for arg in spec.get("args") or []:
            script = Path(str(arg))
            if script.suffix.lower() != ".py":
                continue
            jar_dir = script.parent / "MUSIC" / "MyJar"
            for name in COOKIE_NAMES:
                yield jar_dir / name
    yield Path.home() / ".netease_cookie.txt"


def _load_cookie(explicit: str = ""):
    """返回 (cookie, 来源描述)。来源描述不包含 Cookie 内容。"""
    for candidate in _candidate_cookie_files(explicit):
        if isinstance(candidate, tuple):
            return candidate[1], "环境变量 NETEASE_COOKIE"
        path = candidate
        if not path.is_file():
            continue
        try:
            cookie = _extract_cookie(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if cookie:
            return cookie, f"文件 {path}"
    return None, "未找到 Cookie"


def _headers(cookie: str) -> dict:
    return {
        "User-Agent": UA,
        "Referer": "https://music.163.com/",
        "Origin": "https://music.163.com",
        "Cookie": cookie,
    }


def _sanitize(name: str) -> str:
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, " ")
    return " ".join(name.split()).strip() or "untitled"


def _default_save_dir() -> Path:
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def _song_info(song_id: str, headers: dict) -> dict:
    resp = requests.get(
        f"https://music.163.com/api/song/detail?ids=%5B{song_id}%5D",
        headers=headers,
        timeout=API_TIMEOUT,
    )
    songs = (resp.json() or {}).get("songs") or []
    if not songs:
        raise LookupError(f"查不到歌曲 ID {song_id} 的详情")
    info = songs[0]
    return {
        "title": info.get("name") or f"song_{song_id}",
        "artist": ",".join(a.get("name", "") for a in info.get("artists", [])) or "未知歌手",
        "album": (info.get("album") or {}).get("name") or "",
    }


def _source(song_id: str, br: int, headers: dict) -> dict:
    resp = requests.get(
        f"https://music.163.com/api/song/enhance/player/url?ids=%5B{song_id}%5D&br={br}",
        headers=headers,
        timeout=API_TIMEOUT,
    )
    data = (resp.json() or {}).get("data") or [{}]
    return data[0]


def _write_tags(path: Path, fmt: str, meta: dict) -> None:
    if fmt == "flac":
        from mutagen.flac import FLAC

        audio = FLAC(path)
        audio["title"] = meta["title"]
        audio["artist"] = meta["artist"]
        audio["album"] = meta["album"]
        audio.save()
        return
    from mutagen.id3 import ID3, TALB, TIT2, TPE1

    try:
        audio = ID3(path)
    except Exception:
        audio = ID3()
    audio["TIT2"] = TIT2(encoding=3, text=[meta["title"]])
    audio["TPE1"] = TPE1(encoding=3, text=[meta["artist"]])
    audio["TALB"] = TALB(encoding=3, text=[meta["album"]])
    audio.save(path)


def _probe(path: Path, fmt: str) -> dict:
    if fmt == "flac":
        from mutagen.flac import FLAC

        info = FLAC(path).info
        return {
            "duration_s": round(info.length, 1),
            "sample_rate": info.sample_rate,
            "bits_per_sample": info.bits_per_sample,
            "channels": info.channels,
            "bitrate": info.bitrate,
        }
    from mutagen.mp3 import MP3

    info = MP3(path).info
    return {
        "duration_s": round(info.length, 1),
        "sample_rate": info.sample_rate,
        "bits_per_sample": None,
        "channels": info.channels,
        "bitrate": info.bitrate,
    }


def music_download_lossless(
    song_id: str,
    save_dir: str = "",
    allow_fallback: bool = True,
    cookie_file: str = "",
) -> str:
    """Download a NetEase song as lossless FLAC (falls back to 320 kbps MP3)."""
    try:
        sid = str(song_id or "").strip()
        if not sid:
            return _json({"status": "error", "error": "song_id 不能为空"})

        cookie, source = _load_cookie(cookie_file)
        if not cookie:
            return _json({"status": "error", "error": f"下载失败：{source}"})
        headers = _headers(cookie)

        try:
            meta = _song_info(sid, headers)
        except LookupError as exc:
            return _json({"status": "error", "error": str(exc)})

        item = _source(sid, LOSSLESS_BR, headers)
        url = item.get("url")
        fmt = "flac" if url and "flac" in str(item.get("type") or "").lower() else ""
        if not url or not fmt:
            if not allow_fallback:
                return _json({
                    "status": "error",
                    "error": (
                        f"{meta['title']} 无无损音源（fee={item.get('fee')}），"
                        f"已按 allow_fallback=false 中止"
                    ),
                })
            item = _source(sid, FALLBACK_BR, headers)
            url = item.get("url")
            if not url:
                return _json({
                    "status": "error",
                    "error": f"{meta['title']} 接口未返回下载地址（fee={item.get('fee')}）",
                })
            fmt = "mp3"

        dest_dir = Path(save_dir).expanduser() if (save_dir or "").strip() else _default_save_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{_sanitize(meta['title'])} - {_sanitize(meta['artist'])}.{fmt}"

        binary = requests.get(url, headers={"User-Agent": UA}, timeout=DOWNLOAD_TIMEOUT).content
        if len(binary) < 1300:
            return _json({
                "status": "error",
                "error": f"下载失败：只拿到 {len(binary)} 字节（音源异常）",
            })
        tmp_path = path.with_name(path.name + ".part")
        tmp_path.write_bytes(binary)
        os.replace(tmp_path, path)
        # 审计缝：大文件不传 bytes（Checkpoint finalize 时按需读盘哈希，超 max_blob_mb 只记元数据）
        try:
            from Tools.file_change_hook import emit_file_change

            emit_file_change(str(path), "create", None, None, "hook:music")
        except Exception:
            pass

        _write_tags(path, fmt, meta)
        probe = _probe(path, fmt)
        return _json({
            "status": "success",
            "lossless": fmt == "flac",
            "format": fmt,
            "path": str(path),
            "size_mb": round(len(binary) / 1024 / 1024, 2),
            "cookie_source": source,
            **probe,
            **meta,
        })
    except Exception as exc:
        return _json({"status": "error", "error": f"{type(exc).__name__}: {exc}"})


registry.register(
    name="music_download_lossless",
    description=(
        "Download a NetEase Cloud Music track by song_id as lossless FLAC "
        "(br=999000 source) and write title/artist/album tags. Falls back to "
        "320 kbps MP3 when no lossless source exists unless allow_fallback=false. "
        "Saves to Desktop by default. Use after finding the song_id with the "
        "netease music_search tool. Has side effects: writes a file to disk."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "song_id": {
                "type": "string",
                "description": "NetEase song id, e.g. '399367387'.",
            },
            "save_dir": {
                "type": "string",
                "description": "Target directory; empty = Desktop.",
                "default": "",
            },
            "allow_fallback": {
                "type": "boolean",
                "description": "Fall back to 320 kbps MP3 when no FLAC source. Default true.",
                "default": True,
            },
            "cookie_file": {
                "type": "string",
                "description": "Optional path to a cookie/curl dump file. Default: auto-detect.",
                "default": "",
            },
        },
        "required": ["song_id"],
    },
    handler=music_download_lossless,
    group="utility",
)
