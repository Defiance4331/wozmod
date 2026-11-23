#!/bin/sh
# cargo's rustc wrapper (.cargo/config.toml): run rustc with every path it embeds under $HOME, where cargo and rustup
# keep their sources, written from ~ instead. cargo calls it as `remap-paths.sh RUSTC ARGS...`.
rustc=$1
shift
exec "$rustc" "--remap-path-prefix=${HOME:?the build needs HOME, the prefix it writes as ~}=~" "$@"
