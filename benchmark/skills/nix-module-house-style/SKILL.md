---
name: nix-module-house-style
description: Design reusable Nix packages and NixOS or nix-darwin modules with typed options, upstream conventions, and documented integration.
metadata:
  opencode/autoinvoke: false
---

# Nix module house style

## Fit the project

- Follow the project's pinned inputs, package ownership, and existing layout.
  Prefer a suitable upstream package or module over a local rewrite.
- Put application changes and portable package recipes in their owning source
  when the project separates ownership. A vendored copy does not establish
  ownership. Do not create repositories or move unrelated code merely to
  impose this preference.
- Extend an existing flake when present. For a new reusable package whose
  deliverable includes a flake, a useful layout is:

  ```text
  flake.nix
  flake.lock
  nix/package.nix
  nix/modules/<name>.nix
  README.md
  ```

- Expose named package and module outputs appropriate to the supported
  platforms. Provide default aliases when useful. Add `nixosModules`,
  `darwinModules`, or Home Manager modules only where requested or relevant.

## Design the interface

- Model modules on comparable modules in the project's pinned nixpkgs.
  Use `mkEnableOption`, a package option where useful, and typed options for
  user-facing configuration.
- Prefer a structured `settings` option for applications with structured
  configuration. Use the appropriate `pkgs.formats` type and generator;
  use `freeformType` when mixing declared and application-defined settings.
- Give options clear descriptions and useful examples. Use `defaultText`
  where it explains an otherwise opaque default.
- Keep raw configuration escape hatches only where the application's format
  or existing interface calls for them.
- Pass secrets through runtime credential or file mechanisms supported by
  the service. On systemd, consider `LoadCredential`; provide suitable
  `*File` options when appropriate. Do not embed secret values in store files.
- Define service users, writable state, and unit dependencies according to
  actual runtime needs. For mount dependencies, use appropriate systemd mount
  relationships rather than assuming a generic boot-order dependency suffices.
- Integrate proxy routes and shared configuration through existing modules
  and helpers rather than creating a competing configuration tree.
- Use platform-native service management and configuration interfaces.
  Do not assume a systemd module also supports nix-darwin.

## Build cost and documentation

- Consider cache and rebuild impact before introducing broad overlays or
  overrides, especially for large dependency trees. Check whether an override
  is necessary and report material build costs; do not remove required
  behavior solely to obtain a cache hit.
- Document supported platforms, package and module outputs, installation,
  important options, and known constraints in the existing documentation.
  For a reusable flake, keep the README as its entry point.
- Validate the exact affected output as required by the task and repository.
  Do not describe an attribute as build-tested when only evaluation ran.
- Preserve existing documentation unless its removal is part of the task.
