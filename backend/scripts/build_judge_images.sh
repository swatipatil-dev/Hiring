#!/usr/bin/env bash
# Build the hardened per-language sandbox images used by the judge.
# Run from anywhere with Docker running:  ./scripts/build_judge_images.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Building hireflow-judge-python:latest ..."
docker build -t hireflow-judge-python:latest -f "$ROOT/judge/images/Dockerfile.python" "$ROOT"

echo "Building hireflow-judge-cpp:latest ..."
docker build -t hireflow-judge-cpp:latest -f "$ROOT/judge/images/Dockerfile.cpp" "$ROOT"

echo "Building hireflow-judge-node:latest ..."
docker build -t hireflow-judge-node:latest -f "$ROOT/judge/images/Dockerfile.node" "$ROOT"

echo "Building hireflow-judge-java:latest ..."
docker build -t hireflow-judge-java:latest -f "$ROOT/judge/images/Dockerfile.java" "$ROOT"

echo "Done. Images:"
docker images --filter "reference=hireflow-judge-*"
