# Hardened C++ (g++) image for the judge sandbox.
# Build:  docker build -t hireflow-judge-cpp:latest -f judge/images/Dockerfile.cpp .
FROM gcc:13

RUN apt-get update \
    && apt-get install -y --no-install-recommends time \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
