{
  description = "roadmap-dfs: DFS on your roadmap";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # The whole tool goes under share/roadmap-dfs, laid out as in the repo: the
      # scripts find SKILL.md, the templates and each other from their own path
      # (dfs_paths.TOOL_ROOT), so they are never copied out of it, only pointed at.
      roadmap-dfs = pkgs:
        let
          runtimePath = pkgs.lib.makeBinPath (with pkgs; [
            python3
            git
            bash
            coreutils
            gnused
            gnugrep
            gawk
            diffutils
            findutils
            procps
            util-linux # `script`
          ]);
        in
        pkgs.stdenvNoCC.mkDerivation {
          pname = "roadmap-dfs";
          version = self.shortRev or self.dirtyShortRev or "dev";
          src = self;

          nativeBuildInputs = [ pkgs.makeWrapper ];
          buildInputs = [ pkgs.python3 pkgs.bash ];

          dontConfigure = true;
          dontBuild = true;

          installPhase = ''
            runHook preInstall

            share=$out/share/roadmap-dfs
            mkdir -p $share $out/bin
            cp -r scripts templates docs SKILL.md README.md LICENCE.txt $share/
            rm -f $share/scripts/test_*.py
            cp sandbox.sh $share/sandbox.sh
            chmod +x $share/scripts/*.py $share/scripts/*.sh $share/sandbox.sh
            patchShebangs $share

            # dfs_foo.py / dfs_foo.sh -> dfs-foo, and the screen is `dfs`.
            for f in $share/scripts/dfs_*.py $share/scripts/dfs_*.sh; do
              name=$(basename "$f"); name=''${name%.*}; name=''${name//_/-}
              makeWrapper "$f" $out/bin/$name --prefix PATH : ${runtimePath}
            done
            ln -s dfs-tui $out/bin/dfs

            runHook postInstall
          '';

          meta = {
            description = "DFS on your roadmap";
            mainProgram = "dfs";
          };
        };

      # sandbox.sh with the tool on the container's PATH. The container mounts the
      # host's /nix/store, so the store path is valid inside it as it is.
      sandbox = pkgs:
        let tool = self.packages.${pkgs.stdenv.hostPlatform.system}.roadmap-dfs; in
        pkgs.writeShellScriptBin "roadmap-dfs-sandbox" ''
          export CONTAINER_PATH_PREFIX="${tool}/bin''${CONTAINER_PATH_PREFIX:+:$CONTAINER_PATH_PREFIX}"
          exec ${pkgs.bash}/bin/bash ${tool}/share/roadmap-dfs/sandbox.sh "$@"
        '';
    in
    {
      packages = forAllSystems (pkgs: {
        roadmap-dfs = roadmap-dfs pkgs;
        sandbox = sandbox pkgs;
        default = roadmap-dfs pkgs;
      });

      apps = forAllSystems (pkgs:
        let p = self.packages.${pkgs.stdenv.hostPlatform.system}; in
        rec {
          tui = { type = "app"; program = "${p.roadmap-dfs}/bin/dfs"; };
          sandbox = { type = "app"; program = "${p.sandbox}/bin/roadmap-dfs-sandbox"; };
          default = tui;
        });
    };
}
