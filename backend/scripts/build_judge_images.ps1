# Build the hardened per-language sandbox images used by the judge.
# Run from the repo root with Docker Desktop running:  ./scripts/build_judge_images.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "Building hireflow-judge-python:latest ..."
docker build -t hireflow-judge-python:latest -f "$root/judge/images/Dockerfile.python" $root

Write-Host "Building hireflow-judge-cpp:latest ..."
docker build -t hireflow-judge-cpp:latest -f "$root/judge/images/Dockerfile.cpp" $root

Write-Host "Building hireflow-judge-node:latest ..."
docker build -t hireflow-judge-node:latest -f "$root/judge/images/Dockerfile.node" $root

Write-Host "Building hireflow-judge-java:latest ..."
docker build -t hireflow-judge-java:latest -f "$root/judge/images/Dockerfile.java" $root

Write-Host "Done. Images:"
docker images --filter "reference=hireflow-judge-*"
