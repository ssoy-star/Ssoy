"""ffprobe 래퍼: 해상도·회전·HDR·오디오 유무 등 원본 정보를 읽는다."""

import json
import subprocess

HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG(아이폰 기본 HDR), PQ


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise ValueError(f"영상 스트림이 없습니다: {path}")

    w, h = int(v["width"]), int(v["height"])
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    if "rotate" in (v.get("tags") or {}):
        rotation = int(v["tags"]["rotate"])
    # ffmpeg는 기본적으로 자동 회전하므로 필터 입장에서 보이는 크기로 맞춘다.
    if abs(rotation) % 180 == 90:
        w, h = h, w

    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    if not fps or fps > 240:
        fps = 30.0

    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "path": str(path),
        "width": w,
        "height": h,
        "aspect": w / h,
        "fps": fps,
        "duration": duration,
        "rotation": rotation,
        "has_audio": a is not None,
        "hdr": v.get("color_transfer", "") in HDR_TRANSFERS,
        "vcodec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
    }
