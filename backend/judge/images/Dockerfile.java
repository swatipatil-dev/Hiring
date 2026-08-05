# Hardened Java (JDK 21) image for the judge sandbox.
# Build:  docker build -t hireflow-judge-java:latest -f judge/images/Dockerfile.java .
FROM eclipse-temurin:21-jdk

RUN apt-get update \
    && apt-get install -y --no-install-recommends time \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
