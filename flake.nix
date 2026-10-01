{
  description = "roadmap-dfs: DFS on your roadmap";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # One name per command, everywhere: scripts/dfs_foo.py (or .sh) is the command
      # `dfs-foo` and the app `.#dfs-foo`. A script is a command when it runs on its
      # own: every .sh, and a .py with a __main__; the rest are modules. (Not
      # lib.hasInfix: it is a regex match, which overflows the stack on dfs_tui.py.)
      contains = needle: text: builtins.replaceStrings [ needle ] [ "" ] text != text;
      commands = lib.mapAttrs' (file: _: lib.nameValuePair
          (lib.replaceStrings [ "_" ] [ "-" ] (lib.removeSuffix ".py" (lib.removeSuffix ".sh" file)))
          file)
        (lib.filterAttrs (file: type: type == "regular"
            && lib.hasPrefix "dfs_" file
            && (lib.hasSuffix ".sh" file
                || (lib.hasSuffix ".py" file
                    && contains "__name__ == \"__main__\"" (builtins.readFile (./scripts + "/${file}")))))
          (builtins.readDir ./scripts));

      # The whole tool goes under share/roadmap-dfs, laid out as in the repo: the
      # scripts find SKILL.md, the templates and each other from their own path
      # (dfs_paths.TOOL_ROOT), so they are never copied out of it, only pointed at.
      roadmap-dfs = pkgs:
        let
          runtimePath = lib.makeBinPath (with pkgs; [
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

          # dfs-sandbox also puts the commands on the container's PATH. The container
          # mounts the host's /nix/store, so the store path is valid inside it as it is.
          installPhase = ''
            runHook preInstall

            share=$out/share/roadmap-dfs
            mkdir -p $share $out/bin
            cp -r scripts templates docs SKILL.md README.md LICENCE.txt $share/
            rm -f $share/scripts/test_*.py
            chmod +x $share/scripts/*.py $share/scripts/*.sh
            patchShebangs $share

            ${lib.concatStrings (lib.mapAttrsToList (name: file: ''
              makeWrapper $share/scripts/${file} $out/bin/${name} --prefix PATH : ${runtimePath}
            '') commands)}
            wrapProgram $out/bin/dfs-sandbox --prefix CONTAINER_PATH_PREFIX : $out/bin

            runHook postInstall
          '';

          meta = {
            description = "DFS on your roadmap";
            mainProgram = "dfs-tui";
          };
        };
    in
    {
      packages = forAllSystems (pkgs: {
        roadmap-dfs = roadmap-dfs pkgs;
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.roadmap-dfs;
      });

      apps = forAllSystems (pkgs:
        let
          p = self.packages.${pkgs.stdenv.hostPlatform.system}.roadmap-dfs;
          apps = lib.mapAttrs (name: file: {
            type = "app";
            program = "${p}/bin/${name}";
            meta.description = "roadmap-dfs: scripts/${file}";
          }) commands;
        in
        apps // { default = apps.dfs-tui; });
    };
}
