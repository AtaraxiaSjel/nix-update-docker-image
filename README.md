# Nix Update Docker Image

A tool to update Docker image derivations in Nix.

## Usage

To run the script, execute the following command:

```bash
nix-update-docker-image --config /path/to/your/config.yaml /path/to/your/nix/configuration
```

## Configuration

The configuration file is a YAML file with the following format:

```yaml
# Registry credentials for private repositories.
registries:
  docker.io:
    username: "your-dockerhub-username"
    token: "your-dockerhub-token"
  ghcr.io:
    username: "your-github-username"
    token: "your-github-token"

# Version patterns are used to select which version of a docker image to use.
version_patterns:
  - pattern: '.*/qbittorrent[:@].*'
    version_regex: '^5\.[0-9]+\.[0-9]+.+ls[0-9]+$'
  - pattern: '.*'
    version_regex: '^latest$'
```