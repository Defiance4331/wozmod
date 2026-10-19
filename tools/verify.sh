#!/bin/sh
# Run every test this machine has inputs for, the slow opt-ins included (the DLL compile, font geometry,
# a full install). Input paths come from work/harness.toml [tests]; see tests/conftest.py. A missing input
# fails the run instead of skipping its tests (WOZMOD_REQUIRE_INPUTS).
# Arguments go to pytest, e.g. tools/verify.sh tests/test_runtime_build.py -k release
cd "$(dirname "$0")/.." || exit 1
for flag in ART_NO_WO1PC ART_WORDS AUDIO FONT_FACE_SOURCE FONT_GEOMETRY INSTALL_E2E RUNTIME_COMPILE; do
  export "WOZMOD_$flag=1"
done
export WOZMOD_REQUIRE_INPUTS=1
exec uv run --extra native pytest -q -rs "$@"
