#!/usr/bin/env python3
"""
Version checker for Euroscope plugins.
Monitors GitHub releases and commits, creates issues for outdated versions.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

from github import Github, GithubException
from packaging import version


@dataclass
class Plugin:
    """Represents a plugin from the versions table."""
    name: str
    github_url: str
    owner: str
    repo: str
    versions: list[str]
    is_commit_hash: bool


def parse_readme(readme_path: str) -> list[Plugin]:
    """
    Parse the versions markdown table to extract plugin information.

    Args:
        readme_path: Path to the versions file (VERSIONS.md)

    Returns:
        List of Plugin objects
    """
    with open(readme_path, 'r', encoding='utf-8') as f:
        content = f.read()

    plugins = []
    in_table = False

    for line in content.split('\n'):
        line = line.strip()

        # Skip header and separator rows
        if line.startswith('| Plugin') or line.startswith('| ---'):
            in_table = True
            continue

        # End of table
        if in_table and not line.startswith('|'):
            break

        # Parse table row
        if in_table and line.startswith('|'):
            cells = [cell.strip() for cell in line.split('|')[1:-1]]

            if len(cells) < 2:
                continue

            plugin_cell = cells[0]
            version_cells = cells[1:]

            # Skip entries without source
            if '**NO SRC**' in plugin_cell:
                continue

            # Extract plugin name and GitHub URL
            url_match = re.search(r'\[([^\]]+)\]\(([^\)]+)\)', plugin_cell)
            if not url_match:
                continue

            plugin_name = url_match.group(1)
            github_url = url_match.group(2).rstrip('/')

            # Extract owner and repo from GitHub URL
            github_pattern = r'github\.com/([^/]+)/([^/]+)'
            github_match = re.search(github_pattern, github_url)
            if not github_match:
                continue

            owner = github_match.group(1)
            repo = github_match.group(2)

            # Check if versions are commit hashes (7+ hex characters) or semantic versions
            versions = [v.strip() for v in version_cells]
            is_commit_hash = bool(versions[0] and re.match(r'^[0-9a-f]{7,}$', versions[0]))

            plugins.append(Plugin(
                name=plugin_name,
                github_url=github_url,
                owner=owner,
                repo=repo,
                versions=versions,
                is_commit_hash=is_commit_hash
            ))

    return plugins


def get_latest_version(gh: Github, plugin: Plugin) -> Optional[str]:
    """
    Fetch the latest version/commit from GitHub.

    Args:
        gh: GitHub API client
        plugin: Plugin object

    Returns:
        Latest version string or commit hash, None if not found
    """
    try:
        repo = gh.get_repo(f"{plugin.owner}/{plugin.repo}")

        if plugin.is_commit_hash:
            # Get latest commit from default branch
            default_branch = repo.default_branch
            commits = repo.get_commits(sha=default_branch)
            latest_commit = commits[0]
            return latest_commit.sha[:7]  # Return short hash
        else:
            # Get latest release
            try:
                latest_release = repo.get_latest_release()
                # Strip 'v' prefix if present
                tag = latest_release.tag_name
                return tag.lstrip('v')
            except GithubException as e:
                if e.status == 404:
                    # No releases found, try tags
                    tags = repo.get_tags()
                    if tags.totalCount > 0:
                        latest_tag = tags[0]
                        return latest_tag.name.lstrip('v')
                raise
    except GithubException as e:
        print(f"Error fetching version for {plugin.name}: {e}", file=sys.stderr)
        return None


def is_version_outdated(current: str, latest: str, is_commit_hash: bool) -> bool:
    """
    Compare two versions to determine if an update is needed.

    Args:
        current: Current version string
        latest: Latest version string
        is_commit_hash: Whether versions are commit hashes

    Returns:
        True if current version is outdated
    """
    if is_commit_hash:
        # For commit hashes, just check if they're different
        return current.lower() != latest.lower()

    # For semantic versions, use packaging library
    try:
        return version.parse(current) < version.parse(latest)
    except version.InvalidVersion:
        # If parsing fails, do string comparison
        return current != latest


def check_for_existing_issue(gh: Github, repo_name: str, plugin_name: str, new_version: str) -> bool:
    """
    Check if an issue already exists for this version update.

    Args:
        gh: GitHub API client
        repo_name: Repository name (owner/repo)
        plugin_name: Name of the plugin
        new_version: New version to check for

    Returns:
        True if issue already exists
    """
    try:
        repo = gh.get_repo(repo_name)
        title = f"Update {plugin_name} to {new_version}"

        # Search for open issues with the version-update label
        issues = repo.get_issues(state='open', labels=['version-update'])

        for issue in issues:
            if issue.title == title:
                return True

        return False
    except GithubException as e:
        print(f"Error checking for existing issues: {e}", file=sys.stderr)
        return False


def create_issue(gh: Github, repo_name: str, plugin: Plugin, new_version: str, dry_run: bool) -> None:
    """
    Create a GitHub issue for a version update.

    Args:
        gh: GitHub API client
        repo_name: Repository name (owner/repo)
        plugin: Plugin object
        new_version: New version available
        dry_run: If True, only print what would be done
    """
    title = f"Update {plugin.name} to {new_version}"

    # Build issue body
    version_type = "commit" if plugin.is_commit_hash else "version"
    current_versions = ', '.join(set(plugin.versions))

    body = f"""A new {version_type} of **{plugin.name}** is available.

**Current {version_type}(s)**: {current_versions}
**Latest {version_type}**: {new_version}

**Repository**: {plugin.github_url}
**Release link**: {plugin.github_url}/{'commit' if plugin.is_commit_hash else 'releases/tag/v'}{new_version}

This issue was automatically created by the version checker.
"""

    if dry_run:
        print(f"\n[DRY RUN] Would create issue:")
        print(f"  Title: {title}")
        print(f"  Labels: version-update, automated")
        print(f"  Body:\n{body}")
    else:
        try:
            repo = gh.get_repo(repo_name)
            issue = repo.create_issue(
                title=title,
                body=body,
                labels=['version-update', 'automated']
            )
            print(f"Created issue #{issue.number}: {title}")
        except GithubException as e:
            print(f"Error creating issue for {plugin.name}: {e}", file=sys.stderr)


def main():
    """Main entry point for the version checker."""
    parser = argparse.ArgumentParser(
        description='Check for version updates in Euroscope plugins'
    )
    parser.add_argument(
        '--versions-file',
        default='VERSIONS.md',
        help='Path to versions file (default: VERSIONS.md)'
    )
    parser.add_argument(
        '--github-token',
        default=os.environ.get('GITHUB_TOKEN'),
        help='GitHub API token (or set GITHUB_TOKEN env var)'
    )
    parser.add_argument(
        '--repo',
        required=True,
        help='Target repository for issues (format: owner/repo)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print what would be done without creating issues'
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.github_token:
        print("Error: GitHub token is required. Set GITHUB_TOKEN env var or use --github-token",
              file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.versions_file):
        print(f"Error: Versions file not found: {args.versions_file}", file=sys.stderr)
        sys.exit(1)

    # Initialize GitHub client
    gh = Github(args.github_token)

    # Parse versions file
    print(f"Parsing {args.versions_file}...")
    plugins = parse_readme(args.versions_file)
    print(f"Found {len(plugins)} plugins to check\n")

    updates_found = 0
    errors = 0

    # Check each plugin
    for plugin in plugins:
        print(f"Checking {plugin.name}...", end=' ')

        latest = get_latest_version(gh, plugin)
        if latest is None:
            print("ERROR")
            errors += 1
            continue

        # Check if any version is outdated
        any_outdated = False
        for current in plugin.versions:
            if current and is_version_outdated(current, latest, plugin.is_commit_hash):
                any_outdated = True
                break

        if any_outdated:
            print(f"UPDATE AVAILABLE: {latest}")

            # Check for existing issue
            if check_for_existing_issue(gh, args.repo, plugin.name, latest):
                print(f"  Issue already exists, skipping")
            else:
                create_issue(gh, args.repo, plugin, latest, args.dry_run)
                updates_found += 1
        else:
            print("OK")

    # Summary
    print(f"\n{'=' * 50}")
    print(f"Summary:")
    print(f"  Plugins checked: {len(plugins)}")
    print(f"  Updates found: {updates_found}")
    print(f"  Errors: {errors}")

    if errors > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
