"""내레이션 넣기: 대본 줄마다 시작 시점(at)을 정해 영상에 목소리를 얹는다.

- 목소리: AI 음성(edge-tts, 한국어 SunHi/InJoon/Hyunsu) 또는 사용자가 녹음한 줄별 파일("file").
- 줄이 다음 줄 시작 시점을 넘으면 최대 1.35배까지 자연스럽게 빠르게 맞춘다.
- "echo": true 인 줄(예: "야호~!")에는 메아리 효과.
- 영상의 원래 소리는 작게 깔고, 최종 음량은 -14 LUFS.

설정 예 (JSON):
{
 "video": "output/x.mp4", "output": "output/x_voice.mp4",
 "voice": "ko-KR-SunHiNeural", "rate": "+10%", "bed_db": -8,
 "lines": [{"at": 0.0, "text": "겨울 코디 아직도 고민해?"}, {"at": 24.6, "text": "야호~!", "echo": true}]
}
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path

MAX_TEMPO = 1.35
GAP = 0.08


def _duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def synth(text, out, voice="ko-KR-SunHiNeural", rate="+10%", pitch="+0Hz"):
    """AI 음성 합성 (edge-tts). 프록시 환경의 인증서를 쓰도록 certifi 경로를 맞춘다."""
    import certifi

    ca = os.environ.get("SSL_CERT_FILE")
    if ca:
        certifi.where = lambda: ca
    import edge_tts

    async def run():
        await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(out))

    asyncio.run(run())
    return out


def trim_silence(src, out):
    """앞뒤 무음을 잘라 48kHz 모노 wav 로."""
    af = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02,areverse,"
          "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.06,areverse")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", af, "-ac", "1",
                    "-ar", "48000", str(out)], check=True)
    return out


def schedule(lines, durations, video_len):
    """각 줄의 (시작, 배속, 끝)을 정한다."""
    plan, prev_end = [], 0.0
    for i, (ln, d) in enumerate(zip(lines, durations)):
        start = max(float(ln["at"]), prev_end + GAP if i else float(ln["at"]))
        limit = float(lines[i + 1]["at"]) if i + 1 < len(lines) else video_len
        window = max(limit - start, 0.3)
        tempo = 1.0
        if d > window + 0.25:  # 다음 장면까지 너무 넘치면 조금 빠르게
            tempo = min(MAX_TEMPO, d / window)
        end = start + d / tempo
        plan.append({"start": round(start, 3), "tempo": round(tempo, 3), "end": round(end, 3)})
        prev_end = end
    return plan


def build(cfg, workdir, log=print):
    video = Path(cfg["video"])
    out = Path(cfg["output"])
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    lines = cfg["lines"]
    vlen = _duration(video)

    clips, durs = [], []
    for i, ln in enumerate(lines):
        raw = Path(ln["file"]) if ln.get("file") else workdir / f"line{i:02d}.mp3"
        if not ln.get("file"):
            synth(ln["text"], raw, cfg.get("voice", "ko-KR-SunHiNeural"),
                  ln.get("rate", cfg.get("rate", "+10%")), ln.get("pitch", cfg.get("pitch", "+0Hz")))
        clip = trim_silence(raw, workdir / f"line{i:02d}.wav")
        clips.append(clip)
        durs.append(_duration(clip))

    plan = schedule(lines, durs, vlen)
    for ln, p in zip(lines, plan):
        flag = f" (x{p['tempo']})" if p["tempo"] > 1 else ""
        log(f"  {p['start']:5.2f}–{p['end']:5.2f}s{flag}  {ln['text']}")
    if plan and plan[-1]["end"] > vlen + 0.05:
        log(f"  ⚠ 마지막 줄이 영상 끝({vlen:.2f}s)을 {plan[-1]['end'] - vlen:.2f}s 넘습니다 — 대본을 줄이세요.")

    inputs = ["-i", str(video)]
    for c in clips:
        inputs += ["-i", str(c)]
    g = []
    for i, (ln, p) in enumerate(zip(lines, plan)):
        chain = f"[{i + 1}:a]aformat=sample_rates=48000:channel_layouts=mono"
        if p["tempo"] > 1.0:
            chain += f",atempo={p['tempo']}"
        if ln.get("echo"):
            chain += ",apad=pad_dur=1.2,aecho=0.8:0.6:260|520:0.45|0.25"
        ms = int(p["start"] * 1000)
        chain += f",adelay={ms}|{ms},aformat=channel_layouts=stereo[l{i}]"
        g.append(chain)
    n = len(lines)
    g.append("".join(f"[l{i}]" for i in range(n)) +
             f"amix=inputs={n}:duration=longest:normalize=0,"
             f"loudnorm=I=-15:TP=-2:LRA=9,aresample=48000[vo]")
    bed_db = float(cfg.get("bed_db", -8))
    g.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={bed_db}dB[bed]")
    g.append("[vo][bed]amix=inputs=2:duration=longest:normalize=0,"
             f"atrim=duration={vlen:.3f},loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(g),
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", str(out)], check=True)
    (workdir / "voice_plan.json").write_text(json.dumps(
        [{**ln, **p} for ln, p in zip(lines, plan)], ensure_ascii=False, indent=1))
    return out
