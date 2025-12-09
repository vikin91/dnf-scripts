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

This script builds a compact NEVRA → repository index that can be transferred
to an air-gapped system for package origin discovery.

Usage:
    # Build index from a direct URL (requires network)
    ./build_offline_index.py --baseurl https://mirror.example.com/rhel/9/baseos/x86_64/os/ \\
                             --repo-id rhel-9-baseos \\
                             --output indexes/rhel-9-baseos.json

    # Build index from URLs in a .repo config file (requires network)
    ./build_offline_index.py --repo-urls-from /etc/yum.repos.d/centos.repo \\
                             --output indexes/

    # Build index from local DNF cache (NO network required!)
    ./build_offline_index.py --from-cache /var/cache/dnf \\
                             --output indexes/

    # With variable substitution (for URLs containing $releasever, $basearch)
    ./build_offline_index.py --repo-urls-from /etc/yum.repos.d/redhat.repo \\
                             --releasever 9 --basearch x86_64 \\
                             --output indexes/

    # Disable SSL verification (for self-signed certs or proxies)
    ./build_offline_index.py --baseurl https://... --repo-id myrepo \\
                             --insecure --output index.json

Output Format:
    {
        "metadata": {
            "repo_id": "rhel-9-baseos",
            "source": "https://..." or "/var/cache/dnf/...",
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
import re
import sys
import json
import gzip
import bz2
import ssl
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from configparser import ConfigParser

# Global SSL context (modified by --insecure flag)
SSL_CONTEXT = None

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
    # From a direct URL (requires network):
    %(prog)s --baseurl https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/os/ \\
             --repo-id centos-9-baseos \\
             --output centos-9-baseos.json

    # From a .repo config file (requires network):
    %(prog)s --repo-urls-from /etc/yum.repos.d/centos.repo --output indexes/

    # From local DNF cache (NO network required):
    %(prog)s --from-cache /var/cache/dnf --output indexes/
        """
    )
    
    source_group = parser.add_argument_group('Source (choose one)')
    source_group.add_argument(
        '--baseurl',
        help='Direct URL to the repository (requires network)'
    )
    source_group.add_argument(
        '--repo-urls-from',
        metavar='REPO_FILE',
        help='Parse .repo config file to extract URLs, then download (requires network)'
    )
    source_group.add_argument(
        '--from-cache',
        metavar='CACHE_DIR',
        help='Build index from local cache directory (e.g., /var/cache/dnf). NO network required.'
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
    parser.add_argument(
        '--insecure', '-k',
        action='store_true',
        help='Disable SSL certificate verification (use with caution)'
    )
    parser.add_argument(
        '--releasever',
        help='Value to substitute for $releasever in URLs (e.g., 9)'
    )
    parser.add_argument(
        '--basearch',
        help='Value to substitute for $basearch in URLs (e.g., x86_64)'
    )
    
    return parser.parse_args()


def download_file(url, verbose=False):
    """Download a file and return its contents."""
    if verbose:
        print(f"    Downloading: {url}")
    
    req = Request(url, headers={'User-Agent': 'offline-index-builder/1.0'})
    
    try:
        with urlopen(req, timeout=60, context=SSL_CONTEXT) as response:
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


def substitute_variables(url, releasever=None, basearch=None):
    """Substitute $releasever and $basearch in URLs."""
    if releasever:
        url = url.replace('$releasever', releasever)
    if basearch:
        url = url.replace('$basearch', basearch)
    return url


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
    
    Returns a dict: {nevra_key: repo_id}
    """
    packages = {}
    
    if verbose:
        print("    Parsing primary.xml...")
    
    root = ET.fromstring(primary_content)
    
    # Handle namespaced XML
    if root.tag.startswith('{'):
        default_ns = root.tag.split('}')[0] + '}'
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
        nevra_key = f"{name}|{epoch}|{version}|{release}|{arch}"
        
        packages[nevra_key] = repo_id
        count += 1
        
        if verbose and count % 1000 == 0:
            print(f"    Processed {count} packages...")
    
    if verbose:
        print(f"    Total packages found: {count}")
    
    return packages


def decompress_file(filepath, content=None):
    """Decompress a file based on its extension. Returns decompressed content."""
    if content is None:
        with open(filepath, 'rb') as f:
            content = f.read()
    
    if filepath.endswith('.gz'):
        return gzip.decompress(content)
    elif filepath.endswith('.bz2'):
        return bz2.decompress(content)
    elif filepath.endswith('.xz'):
        import lzma
        return lzma.decompress(content)
    else:
        return content


def find_primary_files(cache_dir, verbose=False):
    """
    Scan a directory tree for primary.xml.gz or primary.sqlite.bz2 files.
    
    Returns a list of tuples: (repo_id, primary_file_path)
    """
    found = []
    
    if verbose:
        print(f"[*] Scanning {cache_dir} for metadata files...")
    
    for root, dirs, files in os.walk(cache_dir):
        # Look for repodata directories
        if 'repodata' in dirs:
            repodata_path = os.path.join(root, 'repodata')
            
            # Try to determine repo_id from directory name
            # DNF cache format: <repo-id>-<hash>/repodata/
            parent_dir = os.path.basename(root)
            
            # Extract repo_id by removing the hash suffix
            # Pattern: repo-id-<16 hex chars>
            match = re.match(r'^(.+)-[a-f0-9]{16}$', parent_dir)
            if match:
                repo_id = match.group(1)
            else:
                repo_id = parent_dir
            
            # Look for primary metadata files
            for filename in os.listdir(repodata_path):
                if 'primary' in filename:
                    if filename.endswith(('.xml.gz', '.xml.xz', '.xml.bz2', '.xml')):
                        primary_path = os.path.join(repodata_path, filename)
                        found.append((repo_id, primary_path, 'xml'))
                        if verbose:
                            print(f"    Found: {repo_id} -> {primary_path}")
                        break
                    elif filename.endswith('.sqlite.bz2') or filename.endswith('.sqlite.gz'):
                        primary_path = os.path.join(repodata_path, filename)
                        found.append((repo_id, primary_path, 'sqlite'))
                        if verbose:
                            print(f"    Found (sqlite): {repo_id} -> {primary_path}")
                        break
    
    return found


def parse_primary_sqlite(db_content, repo_id, verbose=False):
    """
    Parse primary.sqlite and extract all package NEVRAs.
    
    Returns a dict: {nevra_key: repo_id}
    """
    import sqlite3
    import tempfile
    
    packages = {}
    
    if verbose:
        print("    Parsing primary.sqlite...")
    
    # Write to temp file (sqlite3 requires a file)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.sqlite') as tmp:
        tmp.write(db_content)
        tmp_path = tmp.name
    
    try:
        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()
        
        # Query all packages
        cursor.execute("SELECT name, epoch, version, release, arch FROM packages")
        
        count = 0
        for row in cursor:
            name, epoch, version, release, arch = row
            epoch = epoch if epoch else '0'
            
            nevra_key = f"{name}|{epoch}|{version}|{release}|{arch}"
            packages[nevra_key] = repo_id
            count += 1
        
        conn.close()
        
        if verbose:
            print(f"    Total packages found: {count}")
    
    finally:
        os.unlink(tmp_path)
    
    return packages


def build_index_from_cache(cache_dir, verbose=False):
    """
    Build indexes from a local cache directory (e.g., /var/cache/dnf).
    
    Returns a list of index dicts.
    """
    indexes = []
    
    primary_files = find_primary_files(cache_dir, verbose)
    
    if not primary_files:
        print(f"[!] No primary metadata files found in {cache_dir}")
        print("    Make sure the directory contains DNF cache data.")
        print("    Expected structure: <cache_dir>/<repo-id>-<hash>/repodata/")
        return []
    
    print(f"[*] Found {len(primary_files)} repository metadata files")
    
    for repo_id, primary_path, file_type in primary_files:
        print(f"\n[*] Building index for: {repo_id}")
        print(f"    Source: {primary_path}")
        
        try:
            # Read and decompress
            print("    Decompressing...")
            primary_content = decompress_file(primary_path)
            
            # Parse based on file type
            print("    Parsing package list...")
            if file_type == 'xml':
                packages = parse_primary_xml(primary_content, repo_id, verbose)
            else:  # sqlite
                packages = parse_primary_sqlite(primary_content, repo_id, verbose)
            
            index = {
                "metadata": {
                    "repo_id": repo_id,
                    "source": primary_path,
                    "generated": datetime.utcnow().isoformat(),
                    "package_count": len(packages)
                },
                "packages": packages
            }
            
            indexes.append(index)
            print(f"    Packages indexed: {len(packages)}")
            
        except Exception as e:
            print(f"[!] Error processing {repo_id}: {e}")
            continue
    
    return indexes


def build_index_from_url(baseurl, repo_id, verbose=False):
    """
    Build a NEVRA index from a repository URL.
    """
    print(f"\n[*] Building index for: {repo_id}")
    print(f"    Base URL: {baseurl}")
    
    baseurl = baseurl.rstrip('/')
    
    # Step 1: Download repomd.xml
    print("\n[*] Step 1: Downloading repomd.xml...")
    repomd_url = f"{baseurl}/repodata/repomd.xml"
    repomd_content = download_file(repomd_url, verbose)
    
    if not repomd_content:
        print("[!] Failed to download repomd.xml")
        return None
    
    # Step 2: Parse repomd.xml
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
    
    # Step 4: Decompress
    print("\n[*] Step 4: Decompressing metadata...")
    primary_content = decompress_file(primary_location, primary_compressed)
    
    # Step 5: Parse
    print("\n[*] Step 5: Parsing package list...")
    packages = parse_primary_xml(primary_content, repo_id, verbose)
    
    index = {
        "metadata": {
            "repo_id": repo_id,
            "source": baseurl,
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
            urls = baseurl.strip().split('\n')
            repos[section] = urls[0].strip()
    
    return repos


def save_index(index, output_path, compress=False):
    """Save the index to a JSON file."""
    if compress:
        if not output_path.endswith('.gz'):
            output_path += '.gz'
        with gzip.open(output_path, 'wt', encoding='utf-8') as f:
            json.dump(index, f, separators=(',', ':'))
    else:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2)
    
    return output_path


def main():
    global SSL_CONTEXT
    
    args = parse_args()
    
    print("=" * 70)
    print("Offline Index Builder")
    print("=" * 70)
    
    # Set up SSL context if --insecure is specified
    if args.insecure:
        print("\n[!] WARNING: SSL certificate verification is DISABLED")
        SSL_CONTEXT = ssl.create_default_context()
        SSL_CONTEXT.check_hostname = False
        SSL_CONTEXT.verify_mode = ssl.CERT_NONE
    
    indexes = []
    
    # === SOURCE: Local Cache ===
    if args.from_cache:
        if not os.path.isdir(args.from_cache):
            print(f"[!] Cache directory not found: {args.from_cache}")
            sys.exit(1)
        
        print(f"\n[*] Building indexes from local cache: {args.from_cache}")
        print("    (No network access required)")
        
        indexes = build_index_from_cache(args.from_cache, args.verbose)
    
    # === SOURCE: Direct URL ===
    elif args.baseurl:
        if not args.repo_id:
            print("[!] --repo-id is required when using --baseurl")
            sys.exit(1)
        
        if args.releasever:
            print(f"[*] Using releasever: {args.releasever}")
        if args.basearch:
            print(f"[*] Using basearch: {args.basearch}")
        
        baseurl = substitute_variables(args.baseurl, args.releasever, args.basearch)
        index = build_index_from_url(baseurl, args.repo_id, args.verbose)
        if index:
            indexes.append(index)
    
    # === SOURCE: .repo Config File ===
    elif args.repo_urls_from:
        if not os.path.exists(args.repo_urls_from):
            print(f"[!] Repo file not found: {args.repo_urls_from}")
            sys.exit(1)
        
        print(f"\n[*] Parsing repo config: {args.repo_urls_from}")
        print("    (Will download from extracted URLs - network required)")
        
        if args.releasever:
            print(f"[*] Using releasever: {args.releasever}")
        if args.basearch:
            print(f"[*] Using basearch: {args.basearch}")
        
        repos = parse_repo_file(args.repo_urls_from)
        if not repos:
            print("[!] No enabled repositories with baseurl found in .repo file")
            print("    Note: metalink/mirrorlist URLs are not supported.")
            sys.exit(1)
        
        for repo_id, baseurl in repos.items():
            baseurl = substitute_variables(baseurl, args.releasever, args.basearch)
            if '$' in baseurl:
                print(f"\n[!] WARNING: URL for {repo_id} contains unsubstituted variables:")
                print(f"    {baseurl}")
                print(f"    Use --releasever and/or --basearch to substitute them.")
                continue
            
            index = build_index_from_url(baseurl, repo_id, args.verbose)
            if index:
                indexes.append(index)
    
    else:
        print("[!] One of --baseurl, --repo-urls-from, or --from-cache is required")
        sys.exit(1)
    
    # === Save Indexes ===
    if not indexes:
        print("\n[!] No indexes were built")
        sys.exit(1)
    
    for index in indexes:
        repo_id = index['metadata']['repo_id']
        
        if os.path.isdir(args.output):
            output_path = os.path.join(args.output, f"{repo_id}.json")
        else:
            output_path = args.output
        
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        
        final_path = save_index(index, output_path, args.compress)
        
        print(f"\n[✓] Index saved: {final_path}")
        print(f"    Packages indexed: {index['metadata']['package_count']}")
        
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
