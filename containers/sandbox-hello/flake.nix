{
  description = "The hello example: a devShell with a few tools on PATH";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          # All of these are binary-cached, so adding one costs a download
          # once and nothing thereafter -- provided the warm layer is not
          # invalidated. See _loader.py for why it is copied in before the
          # source is.
          packages = with pkgs; [
            hello
            jq
            ripgrep
            git
            python3
          ];

          # hello.py reads this, so entering the shell visibly changes the app.
          HELLO_WHO = "the devShell";

          # No shellHook. `nix develop --command` fires it too, so anything it
          # echoes lands in front of the command's real output on every single
          # invocation -- including overrides, where it is actively wrong.
        };
      });

      packages = forAll (pkgs: {
        default = pkgs.writeShellApplication {
          name = "hello-app";
          runtimeInputs = with pkgs; [ python3 ];
          text = ''exec python3 ${./hello.py} "$@"'';
        };
      });
    };
}
