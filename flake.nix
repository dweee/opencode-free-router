{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }: let
    pkgs = nixpkgs.legacyPackages.x86_64-linux;
  in {
    packages.x86_64-linux.sync-source = pkgs.stdenvNoCC.mkDerivation {
      name = "opencode-free-router-sync-source";
      src = self;
      nativeBuildInputs = [ pkgs.nodejs_24 ];
      buildPhase = "node --test test/*.test.ts";
      installPhase = "mkdir -p $out/share/opencode-free-router && cp -r bin src templates scripts data $out/share/opencode-free-router/";
    };
  };
}
