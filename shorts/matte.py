"""배경 바꾸기: Robust Video Matting(RVM)으로 사람만 오려 다른 배경 위에 합성한다.

- 그린스크린 없이 일반 촬영본에서 동작 (단색 벽/커튼 앞이면 특히 깔끔).
- 프레임 사이 기억(recurrent state)을 쓰므로 머리카락 가장자리가 덜 깜빡인다.
- 구간별로 다른 배경을 지정할 수 있다 (예: 첫 컷은 정상석, 나머지는 전망).

모델: shorts/models/rvm_mobilenetv3_fp32.onnx (setup.sh 가 내려받음, GPL-3.0)
"""

import json
import subprocess
from pathlib import Path

import numpy as np

from . import PKG

MODEL = PKG / "models" / "rvm_mobilenetv3_fp32.onnx"


def load_background(spec, W, H):
    """배경 이미지를 9:16 창으로 잘라 W×H float32(0~1) 배열로.

    spec: {"image": 경로, "x": 창 중심 가로 위치 0~1, "y": 세로 위치 0~1,
           "zoom": 1.0, "blur": 가우시안 블러(심도 효과), "brightness": 0, "saturation": 1}
    """
    x, y = spec.get("x", 0.5), spec.get("y", 0.5)
    zoom = spec.get("zoom", 1.0)
    blur = spec.get("blur", 2.0)
    zw, zh = int(W * zoom) // 2 * 2, int(H * zoom) // 2 * 2
    vf = (f"scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=lanczos,"
          f"crop={W}:{H}:x='(iw-{W})*{x}':y='(ih-{H})*{y}'")
    if blur > 0:
        vf += f",gblur=sigma={blur}"
    vf += (f",eq=brightness={spec.get('brightness', 0)}:saturation={spec.get('saturation', 1.0)}"
           f":contrast={spec.get('contrast', 1.0)}")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(spec["image"]), "-vf", vf, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.float32) / 255.0


def grade_foreground(fgr, g):
    """실내 조명으로 찍힌 사람을 야외 빛에 맞추는 간단한 보정."""
    if not g:
        return fgr
    out = fgr * np.array(g.get("gain", [1.0, 1.0, 1.0]), np.float32)
    c = g.get("contrast", 1.0)
    out = (out - 0.5) * c + 0.5 + g.get("brightness", 0.0)
    s = g.get("saturation", 1.0)
    if s != 1.0:
        lum = out @ np.array([0.299, 0.587, 0.114], np.float32)
        out = lum[..., None] + (out - lum[..., None]) * s
    return np.clip(out, 0, 1)


def replace_background(src, out, backgrounds, default_bg, W=1080, H=1920,
                       ratio=0.25, fg_grade=None, ambience=None, log=print):
    """backgrounds: [{"from": s, "to": e, **bg_spec}, ...] (원본 시간 기준), default_bg: bg_spec."""
    import onnxruntime as ort

    if not MODEL.exists():
        raise FileNotFoundError(f"매팅 모델이 없습니다: {MODEL} (bash setup.sh)")
    sess = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])

    bg_cache = {}

    def bg_for(t):
        for i, b in enumerate(backgrounds):
            if b["from"] <= t < b["to"]:
                if i not in bg_cache:
                    bg_cache[i] = load_background(b, W, H)
                return bg_cache[i]
        if "default" not in bg_cache:
            bg_cache["default"] = load_background(default_bg, W, H)
        return bg_cache["default"]

    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vf",
         f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )
    # 오디오: 원본 첫 오디오 트랙 + (선택) 바람 소리 같은 현장음
    extra, afilter = [], "[1:a:0]aresample=48000[a]"
    if ambience == "wind":
        extra = ["-f", "lavfi", "-i", "anoisesrc=color=brown:amplitude=0.5:r=48000"]
        afilter = ("[1:a:0]aresample=48000,aformat=channel_layouts=stereo[a0];"
                   "[2:a]highpass=f=80,lowpass=f=600,volume='0.10+0.06*sin(2*PI*t/6)':eval=frame,"
                   "aformat=channel_layouts=stereo[w];"
                   "[a0][w]amix=inputs=2:duration=first:normalize=0[a]")
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", "30", "-i", "-", "-i", str(src), *extra,
         "-filter_complex", afilter, "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)],
        stdin=subprocess.PIPE,
    )

    rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4
    dsr = np.array([ratio], np.float32)
    n, frame_bytes = 0, W * H * 3
    while True:
        buf = dec.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3).astype(np.float32) / 255.0
        src_t = img.transpose(2, 0, 1)[None]
        fgr, pha, *rec = sess.run(None, {"src": src_t, "r1i": rec[0], "r2i": rec[1],
                                          "r3i": rec[2], "r4i": rec[3], "downsample_ratio": dsr})
        fg = grade_foreground(fgr[0].transpose(1, 2, 0), fg_grade)
        a = pha[0, 0][..., None]
        comp = fg * a + bg_for(n / 30.0) * (1 - a)
        enc.stdin.write((np.clip(comp, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
        n += 1
        if n % 60 == 0:
            log(f"  배경 합성 {n}프레임 ({n / 30:.0f}초)")
    enc.stdin.close()
    enc.wait()
    dec.wait()
    if enc.returncode != 0:
        raise RuntimeError("합성 영상 인코딩 실패")
    return out


def run_config(config_path, log=print):
    """JSON 설정으로 실행 (cli 의 `background` 명령)."""
    cfg = json.loads(Path(config_path).read_text())
    return replace_background(
        cfg["source"], cfg["output"], cfg.get("backgrounds", []), cfg["default"],
        ratio=cfg.get("ratio", 0.25), fg_grade=cfg.get("fg_grade"),
        ambience=cfg.get("ambience"), log=log,
    )
