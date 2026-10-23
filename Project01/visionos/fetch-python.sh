#!/bin/sh
# Vision Pro 앱에 넣을 Python(BeeWare Python-Apple-support, visionOS용)을 vendor/에 받는다.
# 저장소에는 넣지 않는다(.gitignore). 판(版)과 SHA-256은 GitHub 릴리스 정보에서 확인한 값이다(2026-10-23).
#   https://github.com/beeware/Python-Apple-support/releases/tag/3.14-b13
set -eu
cd "$(dirname "$0")"
TAG=3.14-b13
NAME=Python-3.14-visionOS-support.b13.tar.gz
SHA256=c85c8c007092eb45a8737b31578c043fe3d5762ad98ed7a42426ac2d3bf7f22a
URL="https://github.com/beeware/Python-Apple-support/releases/download/$TAG/$NAME"

if [ -d vendor/Python.xcframework ]; then
    echo "이미 있습니다: vendor/Python.xcframework (다시 받으려면 vendor/를 지우세요)"
    exit 0
fi
mkdir -p vendor/download
curl -fL -o "vendor/download/$NAME" "$URL"
echo "$SHA256  vendor/download/$NAME" | shasum -a 256 -c -
tar -xzf "vendor/download/$NAME" -C vendor
test -d vendor/Python.xcframework || { echo "압축 안에 Python.xcframework가 없습니다"; exit 1; }
echo "준비됨: vendor/Python.xcframework"
