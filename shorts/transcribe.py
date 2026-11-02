"""faster-whisper로 단어 단위 타임스탬프가 있는 한국어 대본을 만든다."""

import json
import subprocess

import numpy as np

SAMPLE_RATE = 16000


def load_audio(path, sr=SAMPLE_RATE):
    """ffmpeg로 모노 16kHz float32 PCM을 디코드한다 (PyAV 버전 문제 회피)."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.float32).copy()


def transcribe(path, model="small", language="ko", vocab=None, beam_size=5):
    from faster_whisper import WhisperModel

    audio = load_audio(path)
    if audio.size == 0:
        return {"language": language, "duration": 0.0, "words": [], "segments": []}

    m = WhisperModel(model, device="cpu", compute_type="int8")
    prompt = None
    if vocab:
        prompt = ", ".join(vocab)
    segments, info = m.transcribe(
        audio,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        beam_size=beam_size,
        initial_prompt=prompt,
        condition_on_previous_text=False,
    )
    words, segs = [], []
    for s in segments:
        segs.append({"start": round(float(s.start), 3), "end": round(float(s.end), 3),
                     "text": s.text.strip()})
        for w in s.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append({
                "start": round(float(w.start), 3),
                "end": round(float(w.end), 3),
                "text": text,
                "prob": round(float(w.probability), 3),
            })
    return {
        "language": info.language,
        "duration": round(audio.size / SAMPLE_RATE, 3),
        "model": model,
        "words": words,
        "segments": segs,
    }


def fmt_ts(t):
    m, s = divmod(max(t, 0), 60)
    return f"{int(m):02d}:{s:05.2f}"


def transcript_text(tr):
    """사람이(그리고 Claude가) 읽고 하이라이트 구간을 고르기 쉬운 형태."""
    lines = []
    for s in tr["segments"]:
        lines.append(f"[{fmt_ts(s['start'])} → {fmt_ts(s['end'])}] {s['text']}")
    return "\n".join(lines) + "\n"


def save(tr, json_path, txt_path):
    json_path.write_text(json.dumps(tr, ensure_ascii=False, indent=1))
    txt_path.write_text(transcript_text(tr))
