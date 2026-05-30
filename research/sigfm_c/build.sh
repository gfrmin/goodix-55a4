#!/usr/bin/env bash
# Build the offline SIGFM validation harness against the vendored matcher + OpenCV4.
set -euo pipefail
cd "$(dirname "$0")"
# Link only the OpenCV modules SIGFM needs. `pkg-config --libs opencv4` on Arch
# over-links opencv_viz (VTK) / opencv_hdf, whose transitive symbols fail to resolve.
g++ -O2 -std=c++17 harness.cpp sigfm.cpp \
  $(pkg-config --cflags opencv4) \
  -lopencv_core -lopencv_imgproc -lopencv_features2d -lopencv_imgcodecs -lopencv_flann \
  -o harness
echo "built ./harness"
