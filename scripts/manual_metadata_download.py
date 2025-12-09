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
Manual Repository Metadata Download

This script demonstrates how DNF downloads repository metadata WITHOUT using
DNF's high-level API. It shows the raw HTTP requests and file parsing that
DNF performs internally.

This is for educational purposes - to understand what files are needed and
how to obtain them on a system without DNF installed.

Requirements:
    - Python 3 with standard library (requests is optional, uses urllib)
    - Network access to repository mirrors
    - A .repo file or known repository URL

What this script does:
    1. Parses a .repo file to extract baseurl/metalink
    2. Downloads repomd.xml (the index file)
    3. Parses repomd.xml to find primary metadata location
    4. Downloads primary.xml.gz (the package catalog)
    5. Saves everything to a directory structure matching DNF's cache
"""

import os
import sys
import hashlib
import gzip
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from configparser import ConfigParser

# Namespace used in repomd.xml
REPO_NS = {'repo': 'http://linux.duke.edu/metadata/repo'}


def parse_repo_file(repo_file_path):
    """
    Parse a .repo file and extract repository information.
    
    Returns a dict: {repo_id: {'baseurl': ..., 'metalink': ..., 'enabled': bool}}
    """
    config = ConfigParser()
    config.read(repo_file_path)
    
    repos = {}
    for section in config.sections():
        repos[section] = {
            'baseurl': config.get(section, 'baseurl', fallback=None),
            'metalink': config.get(section, 'metalink', fallback=None),
            'mirrorlist': config.get(section, 'mirrorlist', fallback=None),
            'enabled': config.getboolean(section, 'enabled', fallback=True),
            'name': config.get(section, 'name', fallback=section),
        }
    return repos


def resolve_baseurl(repo_info):
    """
    Resolve the actual base URL from baseurl, metalink, or mirrorlist.
    
    For simplicity, this just returns the baseurl if available.
    In reality, DNF would fetch the metalink/mirrorlist and parse it.
    """
    if repo_info.get('baseurl'):
        # baseurl might be a newline-separated list
        urls = repo_info['baseurl'].strip().split('\n')
        return urls[0].strip()
    
    if repo_info.get('metalink'):
        # In reality, we'd fetch this URL and parse the XML to get mirrors
        print(f"  [!] Metalink found: {repo_info['metalink']}")
        print(f"      To resolve, you'd need to fetch this URL and parse the XML.")
        return None
    
    if repo_info.get('mirrorlist'):
        # In reality, we'd fetch this URL to get a list of mirrors
        print(f"  [!] Mirrorlist found: {repo_info['mirrorlist']}")
        print(f"      To resolve, you'd need to fetch this URL.")
        return None
    
    return None


def compute_cache_dir_hash(baseurl):
    """
    Compute the 16-character hash suffix for the cache directory.
    
    NOTE: This is a simplified version. The actual libdnf hash computation
    may differ based on multiple factors (URL normalization, etc.).
    """
    return hashlib.sha256(baseurl.encode('utf-8')).hexdigest()[:16]


def download_file(url, dest_path):
    """Download a file from URL to destination path."""
    print(f"  Downloading: {url}")
    
    req = Request(url, headers={'User-Agent': 'manual-metadata-download/1.0'})
    
    try:
        with urlopen(req, timeout=30) as response:
            content = response.read()
            
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, 'wb') as f:
                f.write(content)
            
            print(f"  Saved to: {dest_path} ({len(content)} bytes)")
            return content
    except HTTPError as e:
        print(f"  [!] HTTP Error {e.code}: {e.reason}")
        return None
    except URLError as e:
        print(f"  [!] URL Error: {e.reason}")
        return None


def parse_repomd(repomd_content):
    """
    Parse repomd.xml and extract metadata file locations.
    
    Returns a dict: {type: {'location': ..., 'checksum': ...}}
    """
    root = ET.fromstring(repomd_content)
    
    metadata = {}
    for data_elem in root.findall('repo:data', REPO_NS):
        data_type = data_elem.get('type')
        location = data_elem.find('repo:location', REPO_NS)
        checksum = data_elem.find('repo:checksum', REPO_NS)
        
        if location is not None:
            metadata[data_type] = {
                'location': location.get('href'),
                'checksum': checksum.text if checksum is not None else None,
                'checksum_type': checksum.get('type') if checksum is not None else None,
            }
    
    return metadata


def download_repo_metadata(repo_id, baseurl, output_dir):
    """
    Download repository metadata to a directory structure matching DNF's cache.
    
    Creates: <output_dir>/<repo_id>-<hash>/repodata/
    """
    print(f"\n[*] Downloading metadata for: {repo_id}")
    print(f"    Base URL: {baseurl}")
    
    # Compute cache directory name (similar to DNF)
    hash_suffix = compute_cache_dir_hash(baseurl)
    cache_dir = os.path.join(output_dir, f"{repo_id}-{hash_suffix}")
    repodata_dir = os.path.join(cache_dir, "repodata")
    
    print(f"    Cache dir: {cache_dir}")
    
    # Step 1: Download repomd.xml
    repomd_url = f"{baseurl.rstrip('/')}/repodata/repomd.xml"
    repomd_path = os.path.join(repodata_dir, "repomd.xml")
    
    repomd_content = download_file(repomd_url, repomd_path)
    if not repomd_content:
        print(f"  [!] Failed to download repomd.xml")
        return False
    
    # Step 2: Parse repomd.xml to find primary metadata
    print(f"\n[*] Parsing repomd.xml...")
    metadata = parse_repomd(repomd_content)
    
    print(f"    Found metadata types: {list(metadata.keys())}")
    
    # Step 3: Download primary metadata (the package catalog)
    if 'primary' not in metadata:
        print(f"  [!] No primary metadata found in repomd.xml")
        return False
    
    primary_info = metadata['primary']
    primary_url = f"{baseurl.rstrip('/')}/{primary_info['location']}"
    primary_filename = os.path.basename(primary_info['location'])
    primary_path = os.path.join(repodata_dir, primary_filename)
    
    print(f"\n[*] Downloading primary metadata...")
    primary_content = download_file(primary_url, primary_path)
    if not primary_content:
        print(f"  [!] Failed to download primary metadata")
        return False
    
    # Step 4: Optionally download other metadata (filelists, other, etc.)
    # For package-to-repo discovery, primary is sufficient
    
    print(f"\n[✓] Successfully downloaded metadata for {repo_id}")
    print(f"    Location: {repodata_dir}")
    
    # Show what's in the primary file
    print(f"\n[*] Inspecting primary metadata...")
    try:
        if primary_filename.endswith('.gz'):
            with gzip.open(primary_path, 'rt', encoding='utf-8') as f:
                # Just read first 1000 chars to show structure
                sample = f.read(2000)
                # Count packages (rough estimate)
                pkg_count = sample.count('<package type="rpm"')
                print(f"    Format: Gzipped XML")
                print(f"    Sample packages found in first 2KB: {pkg_count}")
        elif primary_filename.endswith('.sqlite.bz2'):
            print(f"    Format: SQLite (bz2 compressed)")
    except Exception as e:
        print(f"    Could not inspect: {e}")
    
    return True


def main():
    print("=" * 70)
    print("Manual Repository Metadata Download Demo")
    print("=" * 70)
    print()
    print("This script demonstrates how DNF downloads repository metadata.")
    print("It shows the raw HTTP requests and file parsing involved.")
    print()
    
    # Example: Parse a repo file
    repo_files = [
        '/etc/yum.repos.d/redhat.repo',
        '/etc/yum.repos.d/rhel.repo',
        '/etc/yum.repos.d/fedora.repo',
        '/etc/yum.repos.d/centos.repo',
    ]
    
    found_repo_file = None
    for rf in repo_files:
        if os.path.exists(rf):
            found_repo_file = rf
            break
    
    if not found_repo_file:
        print("[!] No standard repo file found.")
        print("    You can modify this script to use a known baseurl directly.")
        print()
        print("Example manual usage:")
        print("  baseurl = 'https://mirror.example.com/rhel/9/baseos/x86_64/os/'")
        print("  download_repo_metadata('rhel-9-baseos', baseurl, '/tmp/dnf-cache')")
        sys.exit(1)
    
    print(f"[*] Found repo file: {found_repo_file}")
    repos = parse_repo_file(found_repo_file)
    
    print(f"[*] Repositories defined:")
    for repo_id, info in repos.items():
        status = "enabled" if info['enabled'] else "disabled"
        print(f"    - {repo_id}: {info['name']} ({status})")
    
    # Try to download metadata for the first enabled repo
    output_dir = "/tmp/manual-dnf-cache"
    
    for repo_id, info in repos.items():
        if not info['enabled']:
            continue
        
        baseurl = resolve_baseurl(info)
        if baseurl:
            success = download_repo_metadata(repo_id, baseurl, output_dir)
            if success:
                print(f"\n[*] You can now use this metadata with repo_discovery.py")
                print(f"    by pointing DNF's cachedir to: {output_dir}")
            break
        else:
            print(f"\n[!] Could not resolve baseurl for {repo_id}")
            print(f"    This repo uses metalink/mirrorlist which requires additional parsing.")


if __name__ == "__main__":
    main()

