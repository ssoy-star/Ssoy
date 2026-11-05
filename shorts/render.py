"""ffmpeg 필터그래프를 만들어 한 번에 최종 MP4를 인코딩한다."""

import subprocess

from .reframe import crop_box

OUT_W, OUT_H = 1080, 1920
HDR_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
               "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")


def _even(v):
    return max(2, int(round(v / 2)) * 2)


def resolve_layout(layout, info, style):
    if layout and layout != "auto":
        return layout
    if info["aspect"] <= 0.8:  # 이미 세로 영상
        return "crop"
    return style.get("landscape_layout", "crop")


def geometry(layout, info, style, W=OUT_W, H=OUT_H):
    """자막/제목 위치(ASS 1080x1920 좌표)와 전경 영상 배치."""
    cap_y = style["caption"].get("y", 0.66)
    title_y = style.get("title", {}).get("y", 0.09)
    if layout == "crop":
        return {
            "layout": "crop",
            "fg": (0, 0, OUT_W, OUT_H),
            "caption_pos": (5, OUT_W // 2, int(cap_y * OUT_H)),
            "title_pos": (8, OUT_W // 2, int(title_y * OUT_H)),
        }
    fg_h = _even(OUT_W / max(info["aspect"], 0.5625))
    fg_h = min(fg_h, int(OUT_H * 0.62))
    fy = _even((OUT_H - fg_h) / 2 - OUT_H * 0.03)
    return {
        "layout": layout,
        "fg": (0, fy, OUT_W, fg_h),
        "caption_pos": (8, OUT_W // 2, fy + fg_h + 50),
        "title_pos": (2, OUT_W // 2, fy - 40),
    }


def fx_chain(fx, W, H):
    """영화 느낌 효과: 타격 순간 화면 흔들림·번쩍임, 비네팅, 필름 그레인, 위아래 검은 띠.

    fx = {"hits": [3.35, 6.7], "shake": 14, "flash": 0.22, "vignette": true, "grain": 7,
          "letterbox": 0.065}
    """
    if not fx:
        return ""
    parts = []
    hits = [float(h) for h in fx.get("hits", [])]
    amp = int(fx.get("shake", 14) * W / OUT_W)
    if hits and amp > 0:
        dur = 0.2
        sx = "+".join(f"if(between(t,{h:.3f},{h + dur:.3f}),{amp}*sin((t-{h:.3f})*95)*(1-(t-{h:.3f})/{dur}),0)"
                      for h in hits)
        sy = "+".join(f"if(between(t,{h:.3f},{h + dur:.3f}),{amp}*cos((t-{h:.3f})*80)*(1-(t-{h:.3f})/{dur}),0)"
                      for h in hits)
        pw, ph = W + 4 * amp, H + 4 * amp
        parts.append(f"scale={pw}:{ph},crop={W}:{H}:x='{2 * amp}+{sx}':y='{2 * amp}+{sy}'")
    flash = float(fx.get("flash", 0))
    if hits and flash > 0:
        br = "+".join(f"if(between(t,{h:.3f},{h + 0.07:.3f}),{flash},0)" for h in hits)
        parts.append(f"eq=brightness='{br}':eval=frame")
    if fx.get("vignette"):
        parts.append("vignette=angle=PI/5")
    if fx.get("grain"):
        parts.append(f"noise=alls={int(fx['grain'])}:allf=t")
    lb = float(fx.get("letterbox", 0))
    if lb > 0:
        bh = int(H * lb) // 2 * 2
        parts.append(f"drawbox=x=0:y=0:w=iw:h={bh}:color=black:t=fill,"
                     f"drawbox=x=0:y=ih-{bh}:w=iw:h={bh}:color=black:t=fill")
    return ",".join(parts)


def build_filter(info, segments, zooms, centers, geom, ass_path, fonts_dir, duration,
                 style, has_bgm=False, bgm_db=-20, W=OUT_W, H=OUT_H, audio_mode="normalize",
                 speeds=None, fx=None):
    n = len(segments)
    sw, sh = info["width"], info["height"]
    k = W / OUT_W  # 드래프트(저해상도) 배율
    _, fy, _, fg_h = geom["fg"]
    fy, fg_h = _even(fy * k), _even(fg_h * k)
    layout = geom["layout"]
    g = []

    pre = HDR_TONEMAP if info.get("hdr") else ""
    g.append(f"[0:v]setpts=PTS-STARTPTS,{pre}fps=30,split={n}" + "".join(f"[v{i}]" for i in range(n)))
    if info["has_audio"]:
        g.append("[0:a]asetpts=PTS-STARTPTS,aresample=48000,"
                 "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"asplit={n}" + "".join(f"[a{i}]" for i in range(n)))

    speeds = speeds or [1.0] * n
    for i, ((s, e), z, c, sp) in enumerate(zip(segments, zooms, centers, speeds)):
        d = (e - s) / sp  # 편집본에서의 길이
        trim = f"[v{i}]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS"
        if sp != 1.0:  # 배속: 화면은 타임스탬프를 줄이고 30fps로 다시 맞춘다
            trim += f",setpts=PTS/{sp:.4f},fps=30"
        if layout == "crop":
            cw, ch, x, y = crop_box(sw, sh, OUT_W / OUT_H, c, z)
            g.append(f"{trim},crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos,setsar=1[sv{i}]")
        else:
            cw, ch, x, y = crop_box(sw, sh, W / fg_h, c, z)
            fg = f"crop={cw}:{ch}:{x}:{y},scale={W}:{fg_h}:flags=lanczos,setsar=1"
            if layout == "blur":
                bw, bh = _even(W / 6), _even(H / 6)
                g.append(f"{trim},split=2[b{i}][f{i}]")
                g.append(f"[b{i}]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
                         f"crop={bw}:{bh},gblur=sigma=6,scale={W}:{H},"
                         f"eq=brightness=-0.12:saturation=1.15,setsar=1[bb{i}]")
                g.append(f"[f{i}]{fg}[ff{i}]")
                g.append(f"[bb{i}][ff{i}]overlay=0:{fy}[sv{i}]")
            else:  # letterbox: 검은 배경 + 가운데 영상 + 위 제목/아래 자막
                g.append(f"{trim},{fg},pad={W}:{H}:0:{fy}:color=black[sv{i}]")
        if info["has_audio"]:
            fade = min(0.012, d / 4)
            tempo = f"atempo={sp:.4f}," if sp != 1.0 else ""
            g.append(f"[a{i}]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,{tempo}"
                     f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={d - fade:.3f}:d={fade:.3f}[sa{i}]")
        else:
            g.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.3f}[sa{i}]")

    g.append("".join(f"[sv{i}][sa{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]")

    def q(p):
        return "'" + str(p).replace("\\", "\\\\").replace("'", "\\'") + "'"

    vlast = "vs"
    fxc = fx_chain(fx, W, H)
    g.append(f"[cv]{fxc + ',' if fxc else ''}ass={q(ass_path)}:fontsdir={q(fonts_dir)}[vs]")
    bar = style.get("progress_bar", {})
    if bar.get("enabled", True):
        bh = max(2, int(bar.get("height", 10) * k))
        color = bar.get("color", "#FFE600").lstrip("#")[:6]
        g.append(f"color=c=0x{color}:s={W}x{bh}:r=30:d={duration:.3f}[bar]")
        g.append(f"[vs][bar]overlay=x='-w+w*t/{duration:.3f}':y={H - bh}:eval=frame:eof_action=pass[vb]")
        vlast = "vb"
    g.append(f"[{vlast}]format=yuv420p[vout]")

    # 거의 무음인 원본을 -14 LUFS로 올리면 방 잡음이 크게 들리므로 모드로 고른다
    norm = {
        "normalize": "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000",
        "keep": "aresample=48000",
        "mute": "volume=0,aresample=48000",
    }.get(audio_mode, "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000")
    if has_bgm:
        fo = max(0.0, duration - 1.5)
        g.append("[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"atrim=duration={duration:.3f},volume={bgm_db}dB,"
                 f"afade=t=in:d=0.5,afade=t=out:st={fo:.3f}:d=1.5[bgm]")
        g.append("[ca]asplit=2[voice][key]")
        g.append("[bgm][key]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=400[duck]")
        g.append(f"[voice][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,{norm}[aout]")
    else:
        g.append(f"[ca]{norm}[aout]")
    return ";\n".join(g) + "\n"


def render(src, filter_path, out_path, bgm=None, draft=False, log_path=None):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats", "-i", str(src)]
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
    cmd += [
        "-filter_complex_script", str(filter_path),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast" if draft else "medium",
        "-crf", "26" if draft else "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if log_path:
        log_path.write_text(" ".join(cmd) + "\n\n" + proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패 (로그: {log_path}):\n{proc.stderr[-2000:]}")
    return out_path


def extract_cover(video, out_jpg, t=1.0):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "2", str(out_jpg)], check=True)
    return out_jpg
