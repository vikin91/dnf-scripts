#!/usr/bin/python3
# Copyright (C) 2025
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <https://www.gnu.org/licenses/>.

"""
Offline Repository Discovery

This script runs on an AIR-GAPPED system (without internet access) and discovers
which repository each installed package came from by using a pre-built index.

The index is created by `build_offline_index.py` on a connected system and
transferred to the air-gapped system.

This script does NOT require DNF or any network access. It only needs:
    - The RPMDB (/var/lib/rpm) - to list installed packages
    - Pre-built index file(s) - to map NEVRA to repository

Usage:
    # With a single index file
    ./repo_discovery_offline.py --index rhel-9-baseos.json

    # With multiple index files (will merge them)
    ./repo_discovery_offline.py --index rhel-9-baseos.json --index rhel-9-appstream.json

    # With an index directory
    ./repo_discovery_offline.py --index-dir ./indexes/

Dependencies:
    - Python 3 (standard library only - no DNF required!)
    - rpm command (to query installed packages)
"""

import os
import sys
import json
import gzip
import argparse
import subprocess
from datetime import datetime


def parse_args():
    parser = argparse.ArgumentParser(
        description="Discover package origins using pre-built offline index.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Single index:
    %(prog)s --index rhel-9-baseos.json

    # Multiple indexes:
    %(prog)s --index baseos.json --index appstream.json

    # Index directory:
    %(prog)s --index-dir ./indexes/

    # Output to CSV:
    %(prog)s --index indexes/ --format csv > packages.csv
        """
    )
    
    parser.add_argument(
        '--index', '-i',
        action='append',
        help='Path to index file (can specify multiple times)'
    )
    parser.add_argument(
        '--index-dir',
        help='Path to directory containing index files'
    )
    parser.add_argument(
        '--format', '-f',
        choices=['table', 'csv', 'json'],
        default='table',
        help='Output format (default: table)'
    )
    parser.add_argument(
        '--unmatched-only',
        action='store_true',
        help='Only show packages that could not be matched'
    )
    parser.add_argument(
        '--matched-only',
        action='store_true',
        help='Only show packages that were matched'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )
    
    return parser.parse_args()


def load_index(index_path, verbose=False):
    """
    Load a NEVRA index from a JSON file.
    
    Returns: dict with 'metadata' and 'packages' keys
    """
    if verbose:
        print(f"    Loading: {index_path}")
    
    try:
        if index_path.endswith('.gz'):
            with gzip.open(index_path, 'rt', encoding='utf-8') as f:
                data = json.load(f)
        else:
            with open(index_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        
        if verbose:
            meta = data.get('metadata', {})
            print(f"      Repo: {meta.get('repo_id', 'unknown')}")
            print(f"      Packages: {meta.get('package_count', 'unknown')}")
            print(f"      Generated: {meta.get('generated', 'unknown')}")
        
        return data
    
    except Exception as e:
        print(f"[!] Failed to load index {index_path}: {e}")
        return None


def load_all_indexes(index_files, index_dir, verbose=False):
    """
    Load all index files and merge them into a single lookup dict.
    
    Returns: dict mapping nevra_key -> repo_id
    """
    merged_packages = {}
    loaded_repos = []
    
    files_to_load = []
    
    # Collect index files from --index arguments
    if index_files:
        files_to_load.extend(index_files)
    
    # Collect index files from --index-dir
    if index_dir:
        if os.path.isdir(index_dir):
            for filename in os.listdir(index_dir):
                if filename.endswith('.json') or filename.endswith('.json.gz'):
                    files_to_load.append(os.path.join(index_dir, filename))
    
    if not files_to_load:
        print("[!] No index files found")
        return None, []
    
    print(f"[*] Loading {len(files_to_load)} index file(s)...")
    
    for index_path in files_to_load:
        data = load_index(index_path, verbose)
        if data:
            packages = data.get('packages', {})
            merged_packages.update(packages)
            
            meta = data.get('metadata', {})
            loaded_repos.append({
                'repo_id': meta.get('repo_id', 'unknown'),
                'package_count': meta.get('package_count', 0),
                'generated': meta.get('generated', 'unknown'),
                'file': index_path
            })
    
    print(f"    Total packages in index: {len(merged_packages)}")
    
    return merged_packages, loaded_repos


def get_installed_packages_rpm():
    """
    Get installed packages using the rpm command.
    
    This does NOT require DNF - it directly queries the RPMDB.
    
    Returns: list of dicts with keys: name, epoch, version, release, arch
    """
    print("[*] Querying installed packages from RPMDB...")
    
    # Use rpm query with custom format
    # %{EPOCH} returns (none) if not set, we need to handle that
    query_format = '%{NAME}|%{EPOCH}|%{VERSION}|%{RELEASE}|%{ARCH}\n'
    
    try:
        result = subprocess.run(
            ['rpm', '-qa', '--queryformat', query_format],
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"[!] rpm command failed: {e}")
        return None
    except FileNotFoundError:
        print("[!] rpm command not found. Is this an RPM-based system?")
        return None
    
    packages = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        
        parts = line.split('|')
        if len(parts) != 5:
            continue
        
        name, epoch, version, release, arch = parts
        
        # Handle (none) epoch
        if epoch == '(none)':
            epoch = '0'
        
        packages.append({
            'name': name,
            'epoch': epoch,
            'version': version,
            'release': release,
            'arch': arch
        })
    
    print(f"    Found {len(packages)} installed packages")
    return packages


def make_nevra_key(pkg):
    """Create a NEVRA key string from a package dict."""
    return f"{pkg['name']}|{pkg['epoch']}|{pkg['version']}|{pkg['release']}|{pkg['arch']}"


def discover_origins(installed_packages, package_index):
    """
    Cross-reference installed packages against the index.
    
    Returns: list of dicts with package info + discovered repo
    """
    print("[*] Cross-referencing packages against index...")
    
    results = []
    match_count = 0
    miss_count = 0
    
    for pkg in installed_packages:
        nevra_key = make_nevra_key(pkg)
        
        if nevra_key in package_index:
            repo = package_index[nevra_key]
            match_count += 1
        else:
            repo = None
            miss_count += 1
        
        results.append({
            'name': pkg['name'],
            'epoch': pkg['epoch'],
            'version': pkg['version'],
            'release': pkg['release'],
            'arch': pkg['arch'],
            'nevra_key': nevra_key,
            'repo': repo
        })
    
    print(f"    Matched: {match_count}")
    print(f"    Unmatched: {miss_count}")
    
    return results


def format_evr(epoch, version, release):
    """Format epoch:version-release string."""
    if epoch and epoch != '0':
        return f"{epoch}:{version}-{release}"
    return f"{version}-{release}"


def output_table(results, unmatched_only=False, matched_only=False):
    """Output results as a formatted table."""
    header = f"{'Package Name':<40} | {'Version':<25} | {'Repository'}"
    print("\n" + "-" * len(header))
    print(header)
    print("-" * len(header))
    
    for pkg in sorted(results, key=lambda p: p['name']):
        repo = pkg['repo']
        
        # Filter based on flags
        if unmatched_only and repo is not None:
            continue
        if matched_only and repo is None:
            continue
        
        evr = format_evr(pkg['epoch'], pkg['version'], pkg['release'])
        repo_display = repo if repo else "(No match)"
        
        print(f"{pkg['name']:<40} | {evr:<25} | {repo_display}")
    
    print("-" * len(header))


def output_csv(results, unmatched_only=False, matched_only=False):
    """Output results as CSV."""
    print("name,epoch,version,release,arch,repository")
    
    for pkg in sorted(results, key=lambda p: p['name']):
        repo = pkg['repo']
        
        if unmatched_only and repo is not None:
            continue
        if matched_only and repo is None:
            continue
        
        repo_str = repo if repo else ""
        print(f"{pkg['name']},{pkg['epoch']},{pkg['version']},{pkg['release']},{pkg['arch']},{repo_str}")


def output_json(results, unmatched_only=False, matched_only=False):
    """Output results as JSON."""
    filtered = []
    
    for pkg in results:
        repo = pkg['repo']
        
        if unmatched_only and repo is not None:
            continue
        if matched_only and repo is None:
            continue
        
        filtered.append(pkg)
    
    print(json.dumps(filtered, indent=2))


def main():
    args = parse_args()
    
    if not args.index and not args.index_dir:
        print("[!] Either --index or --index-dir is required")
        print("    Use --help for usage information")
        sys.exit(1)
    
    print("=" * 70)
    print("Offline Repository Discovery")
    print("=" * 70)
    print()
    print("This script discovers package origins WITHOUT network access.")
    print("It uses pre-built indexes created by build_offline_index.py")
    print()
    
    # Load indexes
    package_index, loaded_repos = load_all_indexes(
        args.index, args.index_dir, args.verbose
    )
    
    if not package_index:
        print("[!] No packages loaded from indexes")
        sys.exit(1)
    
    # Show loaded repos
    print("\n[*] Loaded repositories:")
    for repo in loaded_repos:
        print(f"    - {repo['repo_id']}: {repo['package_count']} packages")
        print(f"      (generated: {repo['generated']})")
    
    # Get installed packages
    print()
    installed = get_installed_packages_rpm()
    
    if not installed:
        print("[!] Could not get installed packages")
        sys.exit(1)
    
    # Perform discovery
    print()
    results = discover_origins(installed, package_index)
    
    # Output results
    if args.format == 'table':
        output_table(results, args.unmatched_only, args.matched_only)
    elif args.format == 'csv':
        output_csv(results, args.unmatched_only, args.matched_only)
    elif args.format == 'json':
        output_json(results, args.unmatched_only, args.matched_only)
    
    # Summary
    matched = sum(1 for r in results if r['repo'] is not None)
    total = len(results)
    
    print(f"\nSummary:")
    print(f"  Total installed: {total}")
    print(f"  Matched: {matched} ({100*matched/total:.1f}%)")
    print(f"  Unmatched: {total - matched}")
    
    if total - matched > 0:
        print("\nNote: Unmatched packages may be from:")
        print("  - Repositories not included in the index")
        print("  - Older/newer versions than what's in the index")
        print("  - Manually installed .rpm files")


if __name__ == "__main__":
    main()

