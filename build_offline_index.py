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
Offline Index Builder

This script runs on a CONNECTED system (with internet access) and builds a
compact NEVRA → repository index that can be transferred to an air-gapped system.

The output is a JSON file containing all package identifiers from a repository,
which can be used by `repo_discovery_offline.py` to determine package origins
without network access.

Usage:
    # Build index from a direct URL
    ./build_offline_index.py --baseurl https://mirror.example.com/rhel/9/baseos/x86_64/os/ \\
                             --repo-id rhel-9-baseos \\
                             --output indexes/rhel-9-baseos.json

    # Build index from a .repo file (if it has direct baseurl)
    ./build_offline_index.py --repo-file /etc/yum.repos.d/myrepo.repo \\
                             --output indexes/

Output Format:
    {
        "metadata": {
            "repo_id": "rhel-9-baseos",
            "baseurl": "https://...",
            "generated": "2025-01-15T10:30:00",
            "package_count": 1234
        },
        "packages": {
            "bash|0|5.1.8|6.el9|x86_64": "rhel-9-baseos",
            ...
        }
    }
"""

import os
import sys
import json
import gzip
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from configparser import ConfigParser

# XML namespaces used in repository metadata
REPO_NS = {'repo': 'http://linux.duke.edu/metadata/repo'}
COMMON_NS = {'common': 'http://linux.duke.edu/metadata/common'}
RPM_NS = {'rpm': 'http://linux.duke.edu/metadata/rpm'}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build offline NEVRA index from repository metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # From a direct URL:
    %(prog)s --baseurl https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/os/ \\
             --repo-id centos-9-baseos \\
             --output centos-9-baseos.json

    # From a .repo file:
    %(prog)s --repo-file /etc/yum.repos.d/centos.repo --output indexes/
        """
    )
    
    source_group = parser.add_argument_group('Source (choose one)')
    source_group.add_argument(
        '--baseurl',
        help='Direct URL to the repository (e.g., https://mirror.../os/)'
    )
    source_group.add_argument(
        '--repo-file',
        help='Path to a .repo file to parse'
    )
    
    parser.add_argument(
        '--repo-id',
        help='Repository ID (required if using --baseurl)'
    )
    parser.add_argument(
        '--output', '-o',
        required=True,
        help='Output file path (JSON) or directory'
    )
    parser.add_argument(
        '--compress',
        action='store_true',
        help='Compress output with gzip'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )
    
    return parser.parse_args()


def download_file(url, verbose=False):
    """Download a file and return its contents."""
    if verbose:
        print(f"    Downloading: {url}")
    
    req = Request(url, headers={'User-Agent': 'offline-index-builder/1.0'})
    
    try:
        with urlopen(req, timeout=60) as response:
            content = response.read()
            if verbose:
                print(f"    Downloaded: {len(content)} bytes")
            return content
    except HTTPError as e:
        print(f"    [!] HTTP Error {e.code}: {e.reason}")
        return None
    except URLError as e:
        print(f"    [!] URL Error: {e.reason}")
        return None


def parse_repomd(repomd_content):
    """Parse repomd.xml and find the primary metadata location."""
    root = ET.fromstring(repomd_content)
    
    for data_elem in root.findall('repo:data', REPO_NS):
        if data_elem.get('type') == 'primary':
            location = data_elem.find('repo:location', REPO_NS)
            if location is not None:
                return location.get('href')
    
    return None


def parse_primary_xml(primary_content, repo_id, verbose=False):
    """
    Parse primary.xml and extract all package NEVRAs.
    
    This is the core parsing logic that extracts:
    - name: package name (e.g., "bash")
    - epoch: version epoch (usually 0)
    - version: version string (e.g., "5.1.8")
    - release: release string (e.g., "6.el9")
    - arch: architecture (e.g., "x86_64")
    
    Returns a dict: {nevra_key: repo_id}
    """
    packages = {}
    
    # Parse the XML
    # primary.xml structure:
    # <metadata>
    #   <package type="rpm">
    #     <name>bash</name>
    #     <arch>x86_64</arch>
    #     <version epoch="0" ver="5.1.8" rel="6.el9"/>
    #     ...
    #   </package>
    # </metadata>
    
    if verbose:
        print("    Parsing primary.xml...")
    
    root = ET.fromstring(primary_content)
    
    # Handle namespaced XML
    # The root element might be like: <metadata xmlns="http://linux.duke.edu/metadata/common" ...>
    # We need to handle both namespaced and non-namespaced elements
    
    # Try to find the default namespace
    ns = {'': ''}  # Default empty namespace
    if root.tag.startswith('{'):
        # Extract namespace from tag like {http://...}metadata
        default_ns = root.tag.split('}')[0] + '}'
        ns = {'md': default_ns[1:-1]}  # Remove { and }
        pkg_tag = f"{default_ns}package"
        name_tag = f"{default_ns}name"
        arch_tag = f"{default_ns}arch"
        version_tag = f"{default_ns}version"
    else:
        pkg_tag = 'package'
        name_tag = 'name'
        arch_tag = 'arch'
        version_tag = 'version'
    
    count = 0
    for pkg_elem in root.iter(pkg_tag):
        if pkg_elem.get('type') != 'rpm':
            continue
        
        name_elem = pkg_elem.find(name_tag)
        arch_elem = pkg_elem.find(arch_tag)
        version_elem = pkg_elem.find(version_tag)
        
        if name_elem is None or arch_elem is None or version_elem is None:
            continue
        
        name = name_elem.text
        arch = arch_elem.text
        epoch = version_elem.get('epoch', '0')
        version = version_elem.get('ver', '')
        release = version_elem.get('rel', '')
        
        # Create the NEVRA key: name|epoch|version|release|arch
        # Using | as delimiter because it's not valid in package names
        nevra_key = f"{name}|{epoch}|{version}|{release}|{arch}"
        
        packages[nevra_key] = repo_id
        count += 1
        
        if verbose and count % 1000 == 0:
            print(f"    Processed {count} packages...")
    
    if verbose:
        print(f"    Total packages found: {count}")
    
    return packages


def build_index_from_url(baseurl, repo_id, verbose=False):
    """
    Build a NEVRA index from a repository URL.
    
    Steps:
    1. Download repomd.xml to find primary metadata location
    2. Download primary.xml.gz
    3. Parse and extract all package NEVRAs
    4. Return as structured dict
    """
    print(f"\n[*] Building index for: {repo_id}")
    print(f"    Base URL: {baseurl}")
    
    # Normalize URL
    baseurl = baseurl.rstrip('/')
    
    # Step 1: Download repomd.xml
    print("\n[*] Step 1: Downloading repomd.xml...")
    repomd_url = f"{baseurl}/repodata/repomd.xml"
    repomd_content = download_file(repomd_url, verbose)
    
    if not repomd_content:
        print("[!] Failed to download repomd.xml")
        return None
    
    # Step 2: Parse repomd.xml to find primary metadata
    print("\n[*] Step 2: Parsing repomd.xml...")
    primary_location = parse_repomd(repomd_content)
    
    if not primary_location:
        print("[!] Could not find primary metadata in repomd.xml")
        return None
    
    print(f"    Primary metadata: {primary_location}")
    
    # Step 3: Download primary metadata
    print("\n[*] Step 3: Downloading primary metadata...")
    primary_url = f"{baseurl}/{primary_location}"
    primary_compressed = download_file(primary_url, verbose)
    
    if not primary_compressed:
        print("[!] Failed to download primary metadata")
        return None
    
    # Step 4: Decompress if needed
    print("\n[*] Step 4: Decompressing metadata...")
    if primary_location.endswith('.gz'):
        try:
            primary_content = gzip.decompress(primary_compressed)
        except Exception as e:
            print(f"[!] Failed to decompress: {e}")
            return None
    elif primary_location.endswith('.xz'):
        try:
            import lzma
            primary_content = lzma.decompress(primary_compressed)
        except ImportError:
            print("[!] lzma module not available for .xz decompression")
            return None
    else:
        primary_content = primary_compressed
    
    # Step 5: Parse primary.xml
    print("\n[*] Step 5: Parsing package list...")
    packages = parse_primary_xml(primary_content, repo_id, verbose)
    
    # Build the final index structure
    index = {
        "metadata": {
            "repo_id": repo_id,
            "baseurl": baseurl,
            "generated": datetime.utcnow().isoformat(),
            "package_count": len(packages)
        },
        "packages": packages
    }
    
    return index


def parse_repo_file(repo_file_path):
    """Parse a .repo file and extract repository definitions."""
    config = ConfigParser()
    config.read(repo_file_path)
    
    repos = {}
    for section in config.sections():
        baseurl = config.get(section, 'baseurl', fallback=None)
        enabled = config.getboolean(section, 'enabled', fallback=True)
        
        if baseurl and enabled:
            # Handle multi-line baseurls
            urls = baseurl.strip().split('\n')
            repos[section] = urls[0].strip()
    
    return repos


def save_index(index, output_path, compress=False):
    """Save the index to a JSON file."""
    if compress:
        if not output_path.endswith('.gz'):
            output_path += '.gz'
        with gzip.open(output_path, 'wt', encoding='utf-8') as f:
            json.dump(index, f, separators=(',', ':'))  # Compact JSON
    else:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2)
    
    return output_path


def main():
    args = parse_args()
    
    print("=" * 70)
    print("Offline Index Builder")
    print("=" * 70)
    
    indexes_to_build = []  # List of (repo_id, baseurl)
    
    if args.baseurl:
        if not args.repo_id:
            print("[!] --repo-id is required when using --baseurl")
            sys.exit(1)
        indexes_to_build.append((args.repo_id, args.baseurl))
    
    elif args.repo_file:
        if not os.path.exists(args.repo_file):
            print(f"[!] Repo file not found: {args.repo_file}")
            sys.exit(1)
        
        repos = parse_repo_file(args.repo_file)
        if not repos:
            print("[!] No enabled repositories with baseurl found in .repo file")
            print("    Note: metalink/mirrorlist URLs are not supported.")
            sys.exit(1)
        
        for repo_id, baseurl in repos.items():
            indexes_to_build.append((repo_id, baseurl))
    
    else:
        print("[!] Either --baseurl or --repo-file is required")
        sys.exit(1)
    
    # Build indexes
    for repo_id, baseurl in indexes_to_build:
        index = build_index_from_url(baseurl, repo_id, args.verbose)
        
        if not index:
            print(f"\n[!] Failed to build index for {repo_id}")
            continue
        
        # Determine output path
        if os.path.isdir(args.output):
            output_path = os.path.join(args.output, f"{repo_id}.json")
        else:
            output_path = args.output
        
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        
        # Save the index
        final_path = save_index(index, output_path, args.compress)
        
        print(f"\n[✓] Index saved: {final_path}")
        print(f"    Packages indexed: {index['metadata']['package_count']}")
        
        # Show file size
        size = os.path.getsize(final_path)
        if size > 1024 * 1024:
            print(f"    File size: {size / 1024 / 1024:.1f} MB")
        else:
            print(f"    File size: {size / 1024:.1f} KB")
    
    print("\n" + "=" * 70)
    print("Done! Transfer the index file(s) to the air-gapped system.")
    print("Then run: ./repo_discovery_offline.py --index <index_file>")
    print("=" * 70)


if __name__ == "__main__":
    main()

