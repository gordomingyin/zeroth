#!/bin/bash
# Rolling capture of a live HLS stream: re-fetch the variant playlist,
# download only new segments, run for N seconds. Usage: rollcap.sh <variant_url> <seconds> <outprefix>
VURL="$1"; TOTAL="$2"; PRE="$3"
mkdir -p "$PRE.segs"; cd "$PRE.segs"
declare -A seen
n=0; end=$((SECONDS+TOTAL))
while [ $SECONDS -lt $end ]; do
  curl -s -A "Mozilla/5.0" --referer https://kick.com/ "$VURL" -o v.m3u8 || { sleep 5; continue; }
  while read u; do
    key=$(echo "$u" | md5sum | cut -d' ' -f1)
    if [ -z "${seen[$key]}" ]; then
      seen[$key]=1; n=$((n+1))
      curl -s -A "Mozilla/5.0" --referer https://kick.com/ "$u" -o seg_$(printf %05d $n).ts
    fi
  done < <(grep "^https" v.m3u8)
  sleep 12
done
echo "GOT $n SEGS"
cat seg_*.ts > ../$PRE.ts
cd .. && ffmpeg -y -loglevel error -i $PRE.ts -ac 1 -ar 16000 $PRE.wav && rm -rf $PRE.segs $PRE.ts && echo RECORDED $PRE.wav
