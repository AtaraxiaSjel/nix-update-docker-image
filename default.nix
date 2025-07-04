{
  lib,
  buildPythonApplication,
  setuptools,
  requests,
  pyyaml,
  packaging,
}:

buildPythonApplication {
  pname = "nix-update-docker-image";
  version = "0.1.0";
  format = "pyproject";

  src = lib.cleanSource ./.;

  build-system = [ setuptools ];

  dependencies = [
    requests
    pyyaml
    packaging
  ];

  doCheck = false;

  meta = with lib; {
    description = "A tool to update Docker image digests in Nix files.";
    homepage = "https://github.com/AtaraxiaSjel/nix-update-docker-image";
    license = licenses.mit;
    maintainers = with maintainers; [ ataraxiadev ];
  };
}
