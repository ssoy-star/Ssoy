"""레퍼런스(잘 나가는 쇼츠) 분석: 컷 리듬·훅 속도·음량을 수치화하고 프레임 시트를 뽑는다.

Claude가 이 리포트와 프레임 시트를 보고 자막 위치/폰트 느낌/색/레이아웃을
읽어낸 뒤 shorts/styles/<이름>.json 커스텀 스타일로 저장하는 용도.
"""

import json
import re
import subprocess

from .probe import probe


def scene_cuts(path, threshold=0.30):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [float(m.group(1)) for m in re.finditer(r"pts_time:([\d.]+)", proc.stderr)]


def loudness(path):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)
    return float(m[-1]) if m else None


def contact_sheet(path, out_jpg, duration, n=16, cols=4):
    rows = (n + cols - 1) // cols
    rate = n / max(duration, 0.1)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(path),
         "-vf", f"fps={rate:.5f},scale=270:-2,tile={cols}x{rows}:padding=4:color=white",
         "-frames:v", "1", "-q:v", "3", str(out_jpg)], check=True)


def hook_strip(path, out_jpg, seconds=3.0, n=6):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-t", f"{seconds}", "-i", str(path),
         "-vf", f"fps={n / seconds:.3f},scale=300:-2,tile={n}x1:padding=4:color=white",
         "-frames:v", "1", "-q:v", "3", str(out_jpg)], check=True)


def analyze(path, out_dir, with_transcript=False, model="small", threshold=0.30):
    out_dir.mkdir(parents=True, exist_ok=True)
    info = probe(path)
    dur = info["duration"]
    cuts = scene_cuts(path, threshold)
    shots = [b - a for a, b in zip([0.0] + cuts, cuts + [dur]) if b - a > 0.05]
    report = {
        "file": str(path),
        "duration_s": round(dur, 2),
        "resolution": f"{info['width']}x{info['height']}",
        "fps": round(info["fps"], 2),
        "cuts": len(cuts),
        "cuts_per_10s": round(len(cuts) / max(dur, 0.1) * 10, 2),
        "avg_shot_s": round(sum(shots) / len(shots), 2) if shots else None,
        "first_cut_s": round(cuts[0], 2) if cuts else None,
        "cut_times": [round(c, 2) for c in cuts],
        "loudness_lufs": loudness(path) if info["has_audio"] else None,
    }
    contact_sheet(path, out_dir / "sheet.jpg", dur)
    hook_strip(path, out_dir / "hook.jpg", seconds=min(3.0, dur))
    if with_transcript and info["has_audio"]:
        from .transcribe import transcribe
        tr = transcribe(path, model=model)
        words = tr["words"]
        report["words_per_sec"] = round(len(words) / max(dur, 0.1), 2)
        report["hook_line"] = " ".join(w["text"] for w in words if w["start"] < 3.0)
        report["transcript"] = " ".join(s["text"] for s in tr["segments"])
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report
