# Sounio Rewrite Lane

This directory is the first-class home for the Sounio side of the rewrite.

Responsibilities:

- benchmark manifests and experiment semantics
- kernel invocation contracts
- scientific runtime assumptions
- domain math policies that should not stay buried in Python orchestration

The goal is to make `Sounio` the real scientific kernel for the Darwin Research OS rewrite, while keeping this repository separate from the upstream language/compiler repository.

Implementation-grade request for the upstream Sounio maintainer:

- [Sounio Platform RFC](/Users/demetriosagourakis/Documents/New%20project/docs/rewrite/sounio_platform_rfc.md)
