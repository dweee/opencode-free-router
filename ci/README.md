# Public NixBench store reuse

This is a source-only project, with no package flake or release channels. Node
checks and releases create no Nix store outputs. NixBench runs are different:
the pinned public corpus builds Nix fixtures and dependencies in a dedicated
GitHub-hosted store.

The workflow keeps three boundaries:

* A trusted default-branch seed job reads the public output index saved by an
  earlier successful publisher. It restores those roots from Niks3 with source
  builds disabled and recursively verifies Niks3 signatures and contents. It
  checks closure size and free space, retains indirect seed roots through export,
  and removes its read credential before exporting bounded public store data.
* Model jobs receive only that data. Their daemon still treats them as untrusted
  clients. They have no OIDC permission, private-cache reader or publisher.
  A root-owned daemon hook records completed build outputs and GC roots outside
  model tools. It captures completed dependencies before a later failed build,
  without changing the benchmark's exit status. Cache retention failures surface
  separately at collection. A public seed remains indexed even on an all-cached
  run. Exports are bounded to 8,192 roots, 1 MiB manifest and 8 GiB NAR stream.
* Separate trusted publishers install the pinned Niks3 client before importing
  model data. They collect and publish completed bootstrap dependencies first,
  import data without executing package programs, then publish and save the new
  public index only after success. An explicit status artifact distinguishes no
  outputs from a failed or missing transfer. Failed benchmarks may still provide
  useful completed dependencies; they do not become successful measurements.

`NIKS3_PUSH_ENABLED=true` enables the default-branch publisher. Trusted seed and
publisher jobs need the owning repository's exact default-branch Niks3 OIDC
read/write grants. PRs exercise retention and failure export without any cache
credential or provider key. Read-policy rollout and a successful trusted run are
required before considering the integration live.

The GitHub cache contains only bounded public output names, keyed to the pinned
corpus and cache schema. Store data is read from and written to Niks3. The model
job never reads the shared private cache directly. Upstream public substituters
remain available for newly required dependencies. Benchmark results may reuse
identical already-built fixtures/dependencies across runs; no result is inferred
from a cache hit.

The small helpers in this directory are exact reviewed shared-controller source
files recorded in `shared-cache-helper-hashes.json`. This project does not add a
placeholder flake or install tested/stable promotion automation. The owning
benchmark-specific hook and workflow stay in this repository.
