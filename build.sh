#!/usr/bin/env bash
# Reproducible native ARM64 integration build; no daemon to deploy at runtime.
set -euo pipefail
cd "$(dirname "$0")"
IMAGE="unfoldedcircle/r2-pyinstaller:3.11.13-0.7.0"
rm -rf build dist package
docker run --rm --platform linux/arm64 \
  --user="$(id -u):$(id -g)" \
  -v "$(pwd):/workspace" \
  -w /workspace \
  "$IMAGE" bash -euxo pipefail -c '
    python -m pip install -r requirements.txt
    python -m pip check
    python tools/check_remoteplay_patch.py
    pyinstaller --clean --onedir --name intg-ps5 \
      --collect-all pyremoteplay \
      --collect-all psnawp_api \
      --collect-all ucapi_framework \
      --collect-submodules pyps4_2ndscreen \
      --collect-all cryptography \
      intg-ps5/driver.py
  '
mkdir -p package/bin package/licenses
cp -a dist/intg-ps5/. package/bin/
mv package/bin/intg-ps5 package/bin/driver
cp driver.json package/
cp LICENSE LICENSE.sbr LICENSE.pyremoteplay THIRD_PARTY_NOTICES.md package/licenses/
test -x package/bin/driver
test -s package/driver.json
tar -czf uc-intg-ps5-connect-aarch64.tar.gz -C package .
printf '\nInstallable artifact: %s\n' "$PWD/uc-intg-ps5-connect-aarch64.tar.gz"
