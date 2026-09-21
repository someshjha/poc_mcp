#!/usr/bin/env bash
# Downloads the standalone Karate JAR (not committed -- 70MB binary) into
# this directory. Run once before `java -jar karate/karate.jar karate/`.
set -euo pipefail
cd "$(dirname "$0")"
KARATE_VERSION="1.5.1"
if [ -f karate.jar ]; then
  echo "karate.jar already present"
  exit 0
fi
curl -sSL -o karate.jar \
  "https://github.com/karatelabs/karate/releases/download/v${KARATE_VERSION}/karate-${KARATE_VERSION}.jar"
echo "downloaded karate.jar (v${KARATE_VERSION})"
