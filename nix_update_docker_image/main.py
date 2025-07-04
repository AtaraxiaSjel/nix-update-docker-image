import argparse
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
import yaml
from packaging.version import parse as parse_version
from requests.auth import HTTPBasicAuth

# --- Constants ---
MAX_TAGS = 3
DEFAULT_CONFIG_PATH = Path.home() / ".config/image-updater/config.yaml"
DEFAULT_CONFIG = {
    'version_patterns': [
        {
            'pattern': '.*',
            'version_regex': 'latest'
        }
    ]
}

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)


# --- Configuration ---
def load_config(config_path: Path) -> Dict:
    """Load configuration from a YAML file."""
    log.info(f"Loading config from {config_path}")
    try:
        with config_path.open('r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        log.warning(f"No config file found at {config_path}, using defaults.")
        return DEFAULT_CONFIG
    except Exception as e:
        log.error(f"Error loading config: {e}")
        return DEFAULT_CONFIG


# --- Image Parsing ---
def parse_image(image_str: str) -> Tuple[str, str]:
    """Parse an image string into (registry, repository)."""
    log.debug(f"Parsing image: {image_str}")
    base_image = image_str.split('@', 1)[0].split(':', 1)[0]
    parts = base_image.split('/', 1)

    if len(parts) > 1 and ('.' in parts[0] or ':' in parts[0]):
        registry, repo = parts
    else:
        registry = 'docker.io'
        repo = base_image
        if '/' not in repo:
            repo = f'library/{repo}'

    log.debug(f"Parsed registry: {registry}, repo: {repo}")
    return registry, repo


# --- Registry Clients ---
class RegistryClient(ABC):
    """Abstract base class for registry clients."""

    def __init__(self, config: Dict):
        self.config = config

    @abstractmethod
    def get_tags_and_digest(self, repo: str, version_regex: str) -> Tuple[List[str], str]:
        """Get tags and digest for a given repository and version regex."""
        pass


class DockerHubClient(RegistryClient):
    """Client for Docker Hub."""

    def get_tags_and_digest(self, repo: str, version_regex: str) -> Tuple[List[str], str]:
        log.info(f"Fetching tags for docker.io/{repo}")
        version_pattern = re.compile(version_regex)

        namespace, repository = repo.split('/', 1) if '/' in repo else ('library', repo)
        url = f"https://hub.docker.com/v2/repositories/{namespace}/{repository}/tags/?page_size=100&ordering=last_updated"

        auth = None
        docker_io_config = self.config.get('registries', {}).get('docker.io', {})
        if 'username' in docker_io_config and 'token' in docker_io_config:
            auth = HTTPBasicAuth(docker_io_config['username'], docker_io_config['token'])

        try:
            response = requests.get(url, auth=auth)
            response.raise_for_status()
            results = response.json().get('results', [])

            digest = ""
            for image in results:
                if version_pattern.match(image['name']):
                    digest = image['digest']
                    break

            if not digest:
                return [], ""

            tags = [img['name'] for img in results if img['digest'] == digest]
            return tags, digest

        except requests.exceptions.HTTPError as e:
            log.error(f"Failed to get tags from Docker Hub: {e.response.status_code} {e.response.reason}")
            return [], ""
        except Exception as e:
            log.error(f"Error getting tags from Docker Hub: {e}")
            return [], ""


class GhcrClient(RegistryClient):
    """Client for GitHub Container Registry."""

    def get_tags_and_digest(self, repo: str, version_regex: str) -> Tuple[List[str], str]:
        log.info(f"Fetching tags for ghcr.io/{repo}")
        version_pattern = re.compile(version_regex)
        owner, package = repo.split('/', 1)

        ghcr_config = self.config.get('registries', {}).get('ghcr.io', {})
        if 'token' not in ghcr_config:
            log.error("Missing token for ghcr.io registry in config.")
            return [], ""

        headers = {
            'Authorization': f"Bearer {ghcr_config['token']}",
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }

        ghcr_username = ghcr_config.get('username', '')
        if owner.lower() == ghcr_username.lower():
            base_url = f"https://api.github.com/users/{owner}/packages/container/{package}/versions"
        else:
            base_url = f"https://api.github.com/orgs/{owner}/packages/container/{package}/versions"

        try:
            page = 1
            while page <= 10:  # Safety break
                url = f"{base_url}?per_page=100&page={page}"
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                results = response.json()

                for image in results:
                    tags = image.get('metadata', {}).get('container', {}).get('tags', [])
                    if any(version_pattern.match(tag) for tag in tags):
                        return tags, image['name']  # 'name' is the digest for ghcr.io

                if len(results) < 100:
                    break
                page += 1

            log.warning(f"Can't find tags that satisfy version pattern for {repo}")
            return [], ""

        except requests.exceptions.HTTPError as e:
            log.error(f"Failed to get tags from GHCR: {e.response.status_code} {e.response.reason}")
            return [], ""
        except Exception as e:
            log.error(f"Error getting tags from GHCR: {e}")
            return [], ""


def get_registry_client(registry: str, config: Dict) -> Optional[RegistryClient]:
    """Factory function to get the correct registry client."""
    if registry == 'docker.io':
        return DockerHubClient(config)
    if registry == 'ghcr.io':
        return GhcrClient(config)
    log.warning(f"Unsupported registry: {registry}")
    return None


# --- File Processing ---
class NixFileUpdater:
    """Handles updating Nix files."""

    IMAGE_LINE_RE = re.compile(
        r'(?:^\s*# Tags:.*\n)?(^\s*image\s*=\s*[\'"])(.*?)([\'"])(.*)',
        re.MULTILINE
    )

    def __init__(self, config: Dict):
        self.config = config

    def find_nix_files(self, paths: List[Path]) -> Set[Path]:
        """Recursively find all Nix files in the given paths."""
        nix_files: Set[Path] = set()
        for path in paths:
            if path.is_file():
                if path.suffix == '.nix':
                    nix_files.add(path)
            elif path.is_dir():
                log.info(f"Searching directory: {path}")
                nix_files.update(path.rglob('*.nix'))
        return nix_files

    def process_file(self, file_path: Path):
        """Process a single Nix file."""
        log.info(f"Processing file: {file_path}")
        try:
            content = file_path.read_text()
            new_content, modified = self._update_content(content)
            if modified:
                file_path.write_text(new_content)
                log.info(f"Successfully updated {file_path}")
            else:
                log.info(f"No changes needed for {file_path}")
        except Exception as e:
            log.error(f"Failed to process file {file_path}: {e}")

    def _update_content(self, content: str) -> Tuple[str, bool]:
        """
        Updates image references in the given content.
        Returns the new content and a boolean indicating if changes were made.
        """
        modified = False

        def replace_func(match: re.Match) -> str:
            nonlocal modified
            pre, image_str, post, rest = match.groups()

            try:
                log.info(f"Found image: {image_str}")
                registry, repo = parse_image(image_str)

                client = get_registry_client(registry, self.config)
                if not client:
                    return match.group(0)

                version_regex = self._get_version_regex(image_str)
                version_tags, target_digest = client.get_tags_and_digest(repo, version_regex)

                if not target_digest:
                    log.warning(f"Could not find a digest for {image_str} with pattern {version_regex}")
                    return match.group(0)

                if self._is_already_updated(image_str, target_digest):
                    log.info("Digest already up-to-date.")
                    return match.group(0)

                new_image_ref = self._build_new_image_ref(image_str, target_digest)
                comment = self._format_tags_comment(pre, version_tags)

                log.info(f"Updating image reference to: {new_image_ref}")
                modified = True
                return f"{comment}\n{pre}{new_image_ref}{post}{rest}" if comment else f"{pre}{new_image_ref}{post}{rest}"

            except Exception as e:
                log.error(f"Failed to process image '{image_str}': {e}")
                return match.group(0)

        new_content = self.IMAGE_LINE_RE.sub(replace_func, content)
        return new_content, modified

    def _get_version_regex(self, image_str: str) -> str:
        """Find the matching version regex for the image string."""
        return next(
            (
                p['version_regex'] for p in self.config.get('version_patterns', [])
                if re.match(p['pattern'], image_str)
            ),
            '^latest$'
        )

    @staticmethod
    def _is_already_updated(image_str: str, target_digest: str) -> bool:
        """Check if the image digest is already the target digest."""
        if '@' in image_str:
            existing_digest_hash = image_str.split('@', 1)[1].split(':', 1)[-1]
            target_digest_hash = target_digest.split(':', 1)[-1]
            return existing_digest_hash == target_digest_hash
        return False

    @staticmethod
    def _build_new_image_ref(image_str: str, target_digest: str) -> str:
        """Construct the new image reference with the updated digest."""
        base_image = image_str.split('@', 1)[0].split(':', 1)[0]
        return f"{base_image}@{target_digest}"

    @staticmethod
    def _format_tags_comment(pre_match: str, tags: List[str]) -> str:
        """Format the comment with the list of tags."""
        if not tags:
            return ""
        if 'latest' in tags and len(tags) > 1:
            tags.remove('latest')

        tags_to_show = tags[:MAX_TAGS]
        leading_whitespace = pre_match[:len(pre_match) - len(pre_match.lstrip())]
        return f"{leading_whitespace}# Tags: {', '.join(tags_to_show)}"


# --- Main Execution ---
def main():
    """Main function to run the script."""
    parser = argparse.ArgumentParser(description='Update container image digests in Nix files.')
    parser.add_argument(
        'paths',
        nargs='*',
        type=Path,
        help='Files or directories to process (defaults to current directory).'
    )
    parser.add_argument(
        '-c', '--config',
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f'Path to config file (default: {DEFAULT_CONFIG_PATH})'
    )
    args = parser.parse_args()

    config = load_config(args.config)
    paths_to_process = args.paths if args.paths else [Path.cwd()]

    updater = NixFileUpdater(config)
    nix_files = updater.find_nix_files(paths_to_process)

    log.info(f"Found {len(nix_files)} Nix files to process.")
    for file_path in nix_files:
        updater.process_file(file_path)


if __name__ == "__main__":
    main()
