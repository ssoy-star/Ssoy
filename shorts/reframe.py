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

    per_seg = []
    for s, e in segments:
        found = []
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
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                found.append(((x + fw / 2) / small.shape[1], (y + fh / 2) / small.shape[0]))
        if found:
            per_seg.append((statistics.median(f[0] for f in found),
                            statistics.median(f[1] for f in found)))
        else:
            per_seg.append(None)
    cap.release()

    known = [c for c in per_seg if c]
    fallback = (
        (statistics.median(c[0] for c in known), statistics.median(c[1] for c in known))
        if known else DEFAULT_CENTER
    )
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
