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

import dnf

def print_all_package_repo_info():
    # 1. Initialize DNF Base
    base = dnf.Base()
    
    # 2. Load Configuration & Repositories
    print("Loading configuration...")
    base.read_all_repos()
    
    # 3. Fill the Sack (Load RPMDB + Remote Metadata)
    # This is where the matching happens in memory
    print("Loading package metadata (this may take a moment)...")
    base.fill_sack()
    
    # 4. Query ALL installed packages
    query = base.sack.query().installed()

    print("-" * 80)
    print(f"{'Package':<40} | {'Version':<20} | {'Detected Repository'}")
    print("-" * 80)

    for pkg in sorted(query, key=lambda p: p.name):
        # pkg.reponame is populated by libdnf matching the installed NEVRA 
        # to the available metadata loaded in step 3.
        repo_id = pkg.reponame
        
        # If it says @System or anaconda, it means DNF couldn't find 
        # a matching package in the currently enabled repositories.
        if repo_id == "anaconda" or repo_id == "@System":
            repo_display = f"{repo_id} (No remote match found)"
        else:
            repo_display = repo_id

        print(f"{pkg.name:<40} | {pkg.evr:<20} | {repo_display}")

    print("-" * 80)
    base.close()

if __name__ == "__main__":
    print_all_package_repo_info()
