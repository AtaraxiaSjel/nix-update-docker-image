{
  description = "A tool to update Docker image digests in Nix files.";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        nix-update-docker-image = pkgs.python3Packages.callPackage ./default.nix { };
      in
      {
        packages.default = nix-update-docker-image;
        apps.default = {
          type = "app";
          program = "${nix-update-docker-image}/bin/nix-update-docker-image";
        };
      }
    );
}
