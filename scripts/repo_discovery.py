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
DNF Repository Discovery Tool

This script identifies the origin repository for installed packages on a system.
It works by cross-referencing the installed packages (from RPMDB) against the
available metadata from enabled repositories.

This simulates how DNF "knows" where a package came from even if the history
database hasn't been populated yet.

Prerequisites:
    - Running as root (or with permissions to read DNF cache/configuration).
    - Enabled repositories in /etc/yum.repos.d/ or /etc/dnf/dnf.conf.
    - Cached metadata in /var/cache/dnf/ (or network access to download it).
"""

import sys
import argparse
import dnf
import logging

def parse_args():
    parser = argparse.ArgumentParser(
        description="Discover origin repository for all installed packages.",
        epilog="Example: %(prog)s --cacheonly"
    )
    parser.add_argument(
        "-C", "--cacheonly",
        action="store_true",
        help="Use only cached metadata. Fail if cache is missing or expired. "
             "Without this flag, DNF may download metadata from the network."
    )
    return parser.parse_args()

def discover_package_origins(cacheonly=False):
    # Configure basic logging to suppress noisy internal DNF logs
    logging.basicConfig(level=logging.WARNING)
    
    # 1. Initialize DNF Context
    try:
        base = dnf.Base()
    except Exception as e:
        print(f"Error: Failed to initialize DNF. Are you running with sufficient permissions?\nDetails: {e}")
        sys.exit(1)

    # 2. Load Configuration
    print("[*] Loading configuration...")
    try:
        base.read_all_repos()
    except Exception as e:
        print(f"Error: Could not read repository configuration.\nDetails: {e}")
        sys.exit(1)

    # Apply cacheonly mode if requested
    if cacheonly:
        print("[*] Running in cache-only mode (no network access).")
        base.conf.cacheonly = True

    # CHECK: Are there any enabled repositories?
    enabled_repos = list(base.repos.iter_enabled())
    if not enabled_repos:
        print("\n[!] ERROR: No repositories are enabled.")
        print("    Action: Check your configuration in /etc/yum.repos.d/")
        print("    Try running 'dnf repolist' to verify.")
        sys.exit(1)
    
    print(f"    - Found {len(enabled_repos)} enabled repositories.")

    # 3. Load Metadata (Fill the Sack)
    print("[*] Loading repository metadata...")
    try:
        base.fill_sack()
    except dnf.exceptions.RepoError as e:
        print(f"\n[!] ERROR: Failed to load repository metadata.")
        print(f"    Details: {e}")
        if cacheonly:
            print("\n    You are running with --cacheonly but metadata is not cached.")
            print("    Action: Run 'dnf makecache' first to download metadata.")
        else:
            print("    Action: Ensure you have network access.")
            print("    Action: Try running 'dnf makecache' manually to refresh the cache.")
        sys.exit(1)

    # 4. Build Index of Available Packages
    print("[*] Indexing available packages from remote repos...")
    remote_index = {}
    
    # Iterate over all packages found in the remote repository metadata
    available_query = base.sack.query().available()
    
    if available_query.count() == 0:
        print("\n[!] WARNING: No available packages found in repositories.")
        if cacheonly:
            print("    You are running with --cacheonly. The metadata cache may be empty or missing.")
            print("    Action: Run 'dnf makecache' to download metadata, then retry.")
        else:
            print("    This is unusual. It might mean your repositories are empty or metadata is corrupt.")
            print("    Action: Try 'dnf clean all' followed by 'dnf makecache'.")
    
    for pkg in available_query:
        # Create a unique key: (Name, Epoch, Version, Release, Arch)
        # We assume 0 for None epoch to match standard DNF behavior
        epoch = pkg.epoch if pkg.epoch is not None else 0
        nevra_key = (pkg.name, epoch, pkg.version, pkg.release, pkg.arch)
        
        # Map the unique key to the repository ID
        remote_index[nevra_key] = pkg.reponame

    # 5. Cross-Reference Installed Packages
    print("[*] Cross-referencing installed packages against remote index...")
    installed_query = base.sack.query().installed()
    
    if installed_query.count() == 0:
        print("\n[!] ERROR: No installed packages found in RPMDB.")
        print("    Action: Verify your RPM database integrity (rpm --rebuilddb).")
        sys.exit(1)

    # Formatting headers
    header = f"{'Package Name':<40} | {'Version':<25} | {'Discovered Origin'}"
    print("\n" + "-" * len(header))
    print(header)
    print("-" * len(header))

    match_count = 0
    miss_count = 0

    for pkg in sorted(installed_query, key=lambda p: p.name):
        epoch = pkg.epoch if pkg.epoch is not None else 0
        key = (pkg.name, epoch, pkg.version, pkg.release, pkg.arch)
        
        if key in remote_index:
            origin = remote_index[key]
            print(f"{pkg.name:<40} | {pkg.evr:<25} | {origin}")
            match_count += 1
        else:
            print(f"{pkg.name:<40} | {pkg.evr:<25} | (No matching repo found)")
            miss_count += 1

    print("-" * len(header))
    print(f"\nSummary:")
    print(f"  Matched: {match_count}")
    print(f"  Unmatched: {miss_count}")
    
    if miss_count > 0:
        print("\nNote on Unmatched Packages:")
        print("  Packages labeled '(No matching repo found)' are installed on your system")
        print("  but do not exist with the exact same version/arch in your currently")
        print("  enabled repositories. They might be:")
        print("    - Installed manually from a .rpm file.")
        print("    - From a repository that is now disabled.")
        print("    - Obsolete versions no longer hosted on the mirror.")

    base.close()

if __name__ == "__main__":
    args = parse_args()
    discover_package_origins(cacheonly=args.cacheonly)
