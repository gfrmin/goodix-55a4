#!/usr/bin/env bash
# Build the offline SIGFM validation harness against the vendored matcher + OpenCV (4 or 5).
set -euo pipefail
cd "$(dirname "$0")"
# OpenCV ships as opencv5 on current Arch and opencv4 on older boxes; take whichever
# pkg-config knows about rather than pinning a major (this tree is built on more than
# one machine).
CV=""
for m in opencv5 opencv4; do
  if pkg-config --exists "$m"; then CV="$m"; break; fi
done
if [ -z "$CV" ]; then
  echo "no OpenCV pkg-config module found (tried opencv5, opencv4)" >&2
  exit 1
fi
echo "building against $CV $(pkg-config --modversion "$CV")"

# SIFT's module was renamed features2d -> features in OpenCV 5; the headers still
# compile unchanged, only the library name moved.
case "$CV" in
  opencv5) FEATURES=-lopencv_features ;;
  *)       FEATURES=-lopencv_features2d ;;
esac

# Link only the OpenCV modules SIGFM needs. `pkg-config --libs` on Arch over-links
# opencv_viz (VTK) / opencv_hdf, whose transitive symbols fail to resolve.
g++ -O2 -std=c++17 harness.cpp sigfm.cpp \
  $(pkg-config --cflags "$CV") -DSRC_DIR="\"$PWD\"" \
  -lopencv_core -lopencv_imgproc "$FEATURES" -lopencv_imgcodecs -lopencv_flann \
  -o harness
echo "built ./harness"
