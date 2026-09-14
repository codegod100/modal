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
          # Kept small on purpose. Every path here has to be substituted into
          # the Modal image before the shell can be entered, and the image
          # cannot *build* anything -- see ../../README.md.
          packages = with pkgs; [
            hello
            jq
            ripgrep
            git
            python3
          ];

          # hello.py reads this, so entering the shell visibly changes the app.
          HELLO_WHO = "the devShell";

          shellHook = ''
            echo "hello devShell -- run: python3 hello.py"
          '';
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
