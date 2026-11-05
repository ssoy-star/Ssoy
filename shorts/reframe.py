"""세로(9:16) 재구성: 컷마다 얼굴 위치를 찾아 크롭 중심을 잡는다."""

import statistics

DEFAULT_CENTER = (0.5, 0.42)


def face_centers(path, segments, samples=3):
    """각 컷의 (fx, fy) 정규화 얼굴 중심. 못 찾으면 전체 중앙값 → 화면 중앙."""
    try:
        import cv2
    except ImportError:
        return [DEFAULT_CENTER] * len(segments)

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return [DEFAULT_CENTER] * len(segments)
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)  # 아이폰 세로 촬영본(회전 메타데이터) 바로 세우기

    raw = []  # 컷마다 [프레임별 [얼굴 후보 (cx, cy, 면적)...]]
    for s, e in segments:
        frames = []
        for k in range(1, samples + 1):
            t = s + (e - s) * k / (samples + 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            scale = 640 / max(h, w)
            small = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            min_side = max(20, int(min(small.shape[:2]) * 0.06))
            faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(min_side, min_side))
            sw, sh = small.shape[1], small.shape[0]
            frames.append([((x + fw / 2) / sw, (y + fh / 2) / sh, fw * fh) for x, y, fw, fh in faces])
        raw.append(frames)
    cap.release()

    # 전체 영상에서 '가장 큰 얼굴'들의 중앙값 = 이 영상의 대표 얼굴 위치
    largest = [max(fr, key=lambda f: f[2]) for frames in raw for fr in frames if fr]
    if not largest:
        return [DEFAULT_CENTER] * len(segments)
    gx = statistics.median(f[0] for f in largest)
    gy = statistics.median(f[1] for f in largest)

    # 대표 위치에서 너무 먼 후보(손·지퍼 등 오탐)는 버린다. 얼굴 높이는 잘 안 변하므로 세로를 엄격히.
    per_seg = []
    for frames in raw:
        ok = [f for fr in frames for f in fr if abs(f[1] - gy) <= 0.15 and abs(f[0] - gx) <= 0.35]
        per_seg.append((statistics.median(f[0] for f in ok), statistics.median(f[1] for f in ok))
                       if ok else None)
    fallback = (gx, gy)
    out, prev = [], None
    for c in per_seg:
        c = c or prev or fallback
        if prev and abs(c[0] - prev[0]) < 0.04 and abs(c[1] - prev[1]) < 0.04:
            c = prev  # 미세한 흔들림은 무시해 화면이 덜컹거리지 않게
        out.append(c)
        prev = c
    return out


def zoom_pattern(n, mode, amount):
    """점프컷 줌: 'alternate'는 컷마다 1.0 ↔ amount 교차 (요즘 토킹헤드 쇼츠 문법)."""
    if mode == "none" or amount <= 1.0:
        return [1.0] * n
    if mode == "alternate":
        return [1.0 if i % 2 == 0 else amount for i in range(n)]
    if mode == "punch":  # 3컷마다 한 번 강하게
        return [amount if i % 3 == 2 else 1.0 for i in range(n)]
    return [1.0] * n


def _even(v):
    return max(2, int(round(v / 2)) * 2)


def crop_box(src_w, src_h, target_aspect, center, zoom):
    """원본에서 잘라낼 (w, h, x, y). target_aspect = w/h."""
    if src_w / src_h > target_aspect:
        h0, w0 = src_h, src_h * target_aspect
    else:
        w0, h0 = src_w, src_w / target_aspect
    w, h = _even(w0 / zoom), _even(h0 / zoom)
    w, h = min(w, src_w - src_w % 2), min(h, src_h - src_h % 2)
    fx, fy = center
    x = min(max(fx * src_w - w / 2, 0), src_w - w)
    y = min(max(fy * src_h - h * 0.42, 0), src_h - h)  # 얼굴을 살짝 위쪽에 둔다
    return w, h, int(x) // 2 * 2, int(y) // 2 * 2
