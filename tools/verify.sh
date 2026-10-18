#!/bin/sh
# Run every test this machine has inputs for, the slow opt-ins included (the DLL compile, art and font
# oracles, a full install). Input paths come from work/harness.toml [tests]; see tests/conftest.py.
# Arguments go to pytest, e.g. tools/verify.sh tests/test_runtime_build.py -k release
cd "$(dirname "$0")/.." || exit 1
for flag in ART_NO_WO1PC ART_ORACLE ART_WORDS AUDIO DISCS FONT_FACE_SOURCE FONT_GEOMETRY FONT_ORACLE FORMATS_ORACLE INSTALL_E2E \
            RUNTIME_COMPILE TEXT_ALIGNMENT TEXT_ORACLE; do
  export "WOZMOD_$flag=1"
done
exec uv run --extra native pytest -q -rs "$@"
