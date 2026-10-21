---
name: nix
description: Work with Nix expressions, flakes, packages, NixOS, Home Manager, and nix-darwin using pinned sources and focused validation.
metadata:
  opencode/autoinvoke: false
---

# Nix ecosystem guidance

## Establish the source of truth

- Read applicable repository guidance and inspect the affected flake outputs,
  modules, and working-tree changes before editing.
- Treat `flake.nix` and `flake.lock` as the dependency source of truth. Follow
  existing pins and compatibility constraints; do not copy release branches
  from examples or replace exact pins with unpinned dependencies.
- Verify package attributes, option names, defaults, and library functions
  against the sources used by the project. Current upstream documentation may
  describe a different revision.
- When available, use a Nix documentation or search tool to discover candidates,
  then inspect exact-name details and confirm against the pinned sources.
  Otherwise use local source definitions and focused `nix eval` queries.
- For current upstream behavior, consult official documentation or upstream
  source. Distinguish that evidence from what the project's pinned revision
  supports. State unresolved uncertainty instead of inventing an option.

## Flakes and dependencies

- Preserve supported systems and the project's existing output structure.
  Common outputs include `packages`, `devShells`, `checks`,
  `nixosConfigurations`, and `darwinConfigurations`.
- Use `follows` where dependencies are intended to share a compatible input.
  Preserve intentionally separate nixpkgs revisions.
- Update inputs only when required by the task. Use Nix to update the intended
  input and inspect the resulting lockfile diff; do not hand-edit lock data.
- Git-backed flakes normally omit untracked files. Check whether new files are
  included in the flake source before diagnosing missing-path failures.
  Staging files changes the index; do it only within the authorized workflow.

## Modules and packages

- Use module `imports`, `options`, and `config` for reusable configuration.
  Declare typed options and use `lib.mkIf` for conditional definitions.
- Lower option override priorities win. Ordinary definitions have priority 100,
  `mkDefault` uses 1000, and `mkForce` uses 50. Use these deliberately;
  `mkForce` does not override every possible priority.
- Use `mkBefore` and `mkAfter` when ordering merged list definitions matters.
- Use `override` for package-function arguments and `overrideAttrs` for
  derivation attributes. Use overlays when consumers should see a changed
  package set; use `callPackage` for package recipes.
- Prefer an existing suitable upstream package or module before maintaining
  a local implementation.

## Environments and platforms

- Use `mkShell` and `devShells` for project development environments.
  Follow existing direnv integration when present.
- Configure unfree-package policy in the package-set import or module context
  that actually evaluates the package. Do not assume a user-level setting
  affects a pure flake evaluation.
- Diagnose downloaded-binary failures before choosing a compatibility approach
  such as patching, an FHS environment, or `nix-ld`.
- Distinguish NixOS, nix-darwin, standalone Home Manager, and Home Manager used
  as a system module. Their outputs and activation commands differ.
- Keep secrets out of Nix expressions and generated store files. A private
  source repository does not make Nix-store contents secret.

## Validate the changed surface

- Follow the repository's formatter and required checks. Choose focused checks
  appropriate to the change and the user's instructions.
- Inspect the actual output names before selecting a command. Typical targets:

  ```sh
  nix eval .#packages.<system>.<package>.drvPath
  nix build .#packages.<system>.<package>
  nix eval .#nixosConfigurations.<name>.config.system.build.toplevel.drvPath
  nix eval .#darwinConfigurations.<name>.system.drvPath
  nix build .#checks.<system>.<check>
  ```

- Use broader `nix flake check` validation when the changed flake structure
  warrants it. Account for platform limitations and expensive builds.
- Investigate unexpected source builds through changed derivations, inputs,
  overlays, and cache availability. Use closure or derivation comparison tools
  when available and useful.
- Evaluation, building, cache availability, and activation establish different
  things. Report what actually ran and what remains unverified.
- Keep activation, deployment, rebooting, and deletion of old generations
  within the user's authorized scope. A build alone does not authorize them.
