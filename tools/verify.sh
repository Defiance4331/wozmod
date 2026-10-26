#!/bin/sh
# Run every test this machine has inputs for, the slow opt-ins included (the DLL compile, font geometry,
# a full install). Input paths come from work/harness.toml [tests]; see tests/conftest.py. A missing input
# fails the run instead of skipping its tests (WOZMOD_REQUIRE_INPUTS).
# Arguments go to pytest, e.g. tools/verify.sh tests/test_runtime_build.py -k release. A first argument --full also
# reruns the art build when nothing it reads has changed since it last passed (WOZMOD_ART_FULL=1).
cd "$(dirname "$0")/.." || exit 1
[ "$1" = --full ] && { export WOZMOD_ART_FULL=1; shift; }
for flag in ART_COMPOSE ART_NO_WO1PC ART_WORDS AUDIO CONTEXTS EVENT_TIMING FONT_FACE_SOURCE FONT_GEOMETRY INSTALL_E2E MOVIE_CAPTIONS \
  RUNTIME_COMPILE SHOUT_BANKS STEP_KEYS STOCK_PIECES TEXT_ROOM; do
  export "WOZMOD_$flag=1"
done
export WOZMOD_REQUIRE_INPUTS=1
[ $# -gt 0 ] && exec uv run --extra native pytest -q -rs "$@"
# The install has a time budget, so it runs last with the machine to itself, not beside the other workers.
uv run --extra native pytest -q -rs --ignore=tests/test_install_e2e.py || status=1
uv run --extra native pytest -q -rs -n0 tests/test_install_e2e.py || status=1
exit ${status:-0}
