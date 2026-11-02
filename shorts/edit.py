"""컷 편집 계획: 무음/공백/추임새를 잘라내 '쉼 없이 몰아치는' 쇼츠 리듬을 만든다."""

import re
import subprocess

# 컷 강도 프리셋: (무음 최소 길이, 단어 사이 최대 허용 공백, 말 앞뒤 여유)
PACES = {
    "tight": {"min_silence": 0.28, "max_gap": 0.35, "pad": 0.07},
    "normal": {"min_silence": 0.45, "max_gap": 0.65, "pad": 0.10},
    "loose": {"min_silence": 0.80, "max_gap": 1.10, "pad": 0.15},
    "off": None,
}

FILLERS = {"음", "어", "아", "으", "흠", "그", "음음", "어어", "에", "엄", "um", "uh"}


def norm_word(text):
    return re.sub(r"[\s.,!?…~\-]+", "", text).lower()


def is_filler(word):
    return norm_word(word["text"]) in FILLERS


def detect_silences(path, noise_db=-35, min_dur=0.3):
    """ffmpeg silencedetect 결과를 [(start, end), ...]로 돌려준다."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    out, start = [], None
    for line in proc.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, float("inf")))
    return out


def parse_time(s):
    s = s.strip()
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        t = 0.0
        for p in parts:
            t = t * 60 + p
        return t
    return float(s)


def parse_ranges(spec):
    """'0:12.5-0:20, 3-9' → [(12.5, 20.0), (3.0, 9.0)] (순서 유지 = 재배치 가능)."""
    if not spec:
        return None
    if isinstance(spec, list):
        return [(float(a), float(b)) for a, b in spec]
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, b = part.split("-", 1)
        out.append((parse_time(a), parse_time(b)))
    return out


def _subtract(interval, cuts):
    s, e = interval
    pieces, cur = [], s
    for cs, ce in sorted(cuts):
        if ce <= cur or cs >= e:
            continue
        if cs > cur:
            pieces.append((cur, cs))
        cur = max(cur, ce)
    if cur < e:
        pieces.append((cur, e))
    return pieces


def plan_segments(duration, words, ranges=None, silences=None, pace="tight",
                  cut_fillers=True, min_keep=0.18):
    """원본 시간축 기준 유지 구간 리스트 [(s, e), ...]를 만든다 (재생 순서대로)."""
    ranges = ranges or [(0.0, duration)]
    ranges = [(max(0.0, s), min(duration, e)) for s, e in ranges if e > s]
    p = PACES.get(pace)
    if p is None:
        return [r for r in ranges if r[1] - r[0] > 0.05]

    pad = p["pad"]
    cuts = []
    for s, e in silences or []:
        e = min(e, duration)
        if e - s >= p["min_silence"] and (e - pad) - (s + pad) > 0.05:
            cuts.append((s + pad, e - pad))

    ws = sorted(words, key=lambda w: w["start"])
    for a, b in zip(ws, ws[1:]):
        if b["start"] - a["end"] > p["max_gap"]:
            cuts.append((a["end"] + pad, b["start"] - pad))
    if cut_fillers:
        for w in ws:
            if is_filler(w) and w["end"] - w["start"] < 1.2:
                cuts.append((w["start"] - 0.02, w["end"] + 0.02))

    segments = []
    for rs, re_ in ranges:
        inside = [w for w in ws if rs <= (w["start"] + w["end"]) / 2 < re_
                  and not (cut_fillers and is_filler(w))]
        rcuts = list(cuts)
        if inside:  # 첫 마디 전, 마지막 마디 뒤의 군더더기 제거
            rcuts.append((rs, inside[0]["start"] - pad))
            rcuts.append((inside[-1]["end"] + pad + 0.12, re_))
        for piece in _subtract((rs, re_), rcuts):
            if piece[1] - piece[0] >= min_keep:
                segments.append((round(piece[0], 3), round(piece[1], 3)))
    return segments


def map_words(words, segments, cut_fillers=True):
    """원본 시간의 단어를 편집본 시간축으로 옮긴다. 반환: (words, 총 길이)."""
    offsets, t = [], 0.0
    for s, e in segments:
        offsets.append(t)
        t += e - s
    total = t

    def place(w, i):
        s, e = segments[i]
        ns = offsets[i] + min(max(w["start"], s), e) - s
        ne = offsets[i] + min(max(w["end"], s), e) - s
        ne = max(ne, ns + 0.12)
        return {**w, "start": round(ns, 3), "end": round(ne, 3), "seg": i}

    out, used = [], set()
    for i, (s, e) in enumerate(segments):
        for j, w in enumerate(words):
            mid = (w["start"] + w["end"]) / 2
            if s <= mid < e:
                out.append(place(w, i))
                used.add(j)
    for j, w in enumerate(words):
        if j in used or (cut_fillers and is_filler(w)):
            continue
        best, best_ov = None, 0.0
        for i, (s, e) in enumerate(segments):
            ov = min(e, w["end"]) - max(s, w["start"])
            if ov > best_ov:
                best, best_ov = i, ov
        if best is None:  # 타임스탬프가 살짝 어긋난 단어: 0.5초 이내 가장 가까운 컷에 붙인다
            dist = [(min(abs(w["start"] - e), abs(w["end"] - s)), i)
                    for i, (s, e) in enumerate(segments)]
            if dist and min(dist)[0] <= 0.5:
                best = min(dist)[1]
        if best is not None:
            out.append(place(w, best))
    out.sort(key=lambda w: (w["start"], w["seg"]))
    return out, round(total, 3)
