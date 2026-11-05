#!/usr/bin/env bash
# 새 클라우드 세션마다 한 번 실행: 파이썬 패키지 + 한글 자막 폰트 준비
set -euo pipefail
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "ffmpeg 가 필요합니다"; exit 1; }

if ! python3 -c "import faster_whisper, cv2, gdown" 2>/dev/null; then
  echo "▶ 파이썬 패키지 설치…"
  pip install -q -r requirements.txt
fi

FONT_DIR=shorts/fonts
mkdir -p "$FONT_DIR"
CDN=https://cdn.jsdelivr.net/gh
get() { [ -s "$FONT_DIR/$1" ] || { echo "▶ 폰트 $1"; curl -fsSL --retry 3 -o "$FONT_DIR/$1" "$2"; }; }
get BlackHanSans-Regular.ttf "$CDN/google/fonts@main/ofl/blackhansans/BlackHanSans-Regular.ttf"
get Jua-Regular.ttf          "$CDN/google/fonts@main/ofl/jua/Jua-Regular.ttf"
get DoHyeon-Regular.ttf      "$CDN/google/fonts@main/ofl/dohyeon/DoHyeon-Regular.ttf"
get Pretendard-ExtraBold.otf "$CDN/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/public/static/Pretendard-ExtraBold.otf"
get Pretendard-SemiBold.otf  "$CDN/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/public/static/Pretendard-SemiBold.otf"

mkdir -p inbox work output
echo "✔ 준비 완료 — python3 -m shorts styles"
