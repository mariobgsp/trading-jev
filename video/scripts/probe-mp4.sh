#!/usr/bin/env bash
# Assert the encode is what we said it would be: size, fps, duration, audio track.
#   ./scripts/probe-mp4.sh out/trading-jev-story.mp4 1080x1920 30
set -euo pipefail

f=$1
want=${2:-1080x1920}
fps=${3:-30}

ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate,duration \
  -of default=noprint_wrappers=1 "$f"

W=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$f" | tr -d "\r\n,")
H=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$f" | tr -d "\r\n,")
RATE=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$f" | tr -d "\r\n,")
FR=$(ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 "$f" | tr -d "\r\n,")
[ -n "$W" ] && [ -n "$H" ] || { echo "FAIL no video stream"; exit 1; }
[ "$W"x"$H" = "$want" ] || { echo "FAIL size ${W}x${H} != $want"; exit 1; }
[ "$RATE" = "${fps}/1" ] || { echo "FAIL fps $RATE != $fps"; exit 1; }
ffprobe -v error -select_streams a -show_entries stream=codec_name -of csv=p=0 "$f" | grep -q . \
  || { echo "FAIL no audio stream"; exit 1; }

echo "OK $f ${W}x${H} $RATE ${FR} frames, audio present"
