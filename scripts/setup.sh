#!/bin/bash
# One-time setup for the packaging-prepress skill.
# Installs the Python libraries into a private venv and fetches the FOGRA39 CMYK profile
# (Adobe's official "CoatedFOGRA39.icc", = ISO Coated v2) into ~/.packaging-prepress.
set -e
H="${PREPRESS_HOME:-$HOME/.packaging-prepress}"
mkdir -p "$H"
[ -x "$H/venv/bin/python" ] || python3 -m venv "$H/venv"
# slow networks time out on big wheels -> generous timeout + retries, one package at a time
for p in numpy pillow pikepdf pymupdf scipy; do
  "$H/venv/bin/pip" install -q --timeout 120 --retries 10 "$p"
done
if [ ! -f "$H/CoatedFOGRA39.icc" ]; then
  curl -sSL --retry 5 --max-time 900 -o "$H/adobe_icc.zip" \
    "https://download.adobe.com/pub/adobe/iccprofiles/mac/AdobeICCProfilesCS4Mac_end-user.zip"
  unzip -o -j -q "$H/adobe_icc.zip" "Adobe ICC Profiles (end-user)/CMYK/CoatedFOGRA39.icc" -d "$H"
  rm -f "$H/adobe_icc.zip"
fi
"$H/venv/bin/python" -c "import pymupdf, pikepdf, PIL, numpy, scipy; print('packaging-prepress ready:', '$H')"
