This directory is a staging area for cluster-safe Sounio assets.

Populate:
- `source/` with a local checkout of the upstream `sounio` repository
- `bundle/` with a known-buildable `souc` binary plus `stdlib`

before building the F# worker image for `sounio_runtime_remote` cluster smokes.

Use:

`/Users/demetriosagourakis/Documents/New project/scripts/prepare_cluster_sounio_bundle.sh`

The Docker worker image prefers the staged `bundle/` directory and only falls
back to compiling from `source/` if a prebuilt bundle is absent.

For the current Linux cluster smoke, `SOUNIO_SKIP_PREBUILT_BUNDLE=1` is useful
when the prebuilt local binary is macOS-only and the image needs a Linux `souc`
plus `libsounio_runtime.so`.
