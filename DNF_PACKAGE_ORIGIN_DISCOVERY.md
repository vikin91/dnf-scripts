# DNF Package Origin Discovery

This document explains how DNF identifies which repository a package was installed from on a RHEL system. It is based on analysis of the DNF source code (Python + libdnf).

## Table of Contents

1. [Overview](#overview)
2. [Key Concepts](#key-concepts)
3. [The .repo File: Configuration, Not Data](#the-repo-file-configuration-not-data)
4. [Red Hat CDN Authentication](#red-hat-cdn-authentication)
5. [The Sack: DNF's In-Memory Search Engine](#the-sack-dnfs-in-memory-search-engine)
6. [How DNF Matches Packages to Repositories](#how-dnf-matches-packages-to-repositories)
7. [Where the Data Lives on Disk](#where-the-data-lives-on-disk)
8. [The History Database (history.sqlite)](#the-history-database-historysqlite)
9. [Manual Tracing: Finding Package Origin Yourself](#manual-tracing-finding-package-origin-yourself)
10. [Programmatic Discovery](#programmatic-discovery)

---

## Overview

When you run `dnf list installed` and see output like:

```
bash.x86_64    5.1.8-6.el9    @rhel-9-baseos
```

The `@rhel-9-baseos` part is not always stored persistently. DNF often **discovers** this information at runtime by cross-referencing:

1. **Installed packages** (from the local RPM database)
2. **Available packages** (from cached repository metadata)

This document explains exactly how that matching works.

---

## Key Concepts

| Term | Description |
|------|-------------|
| **RPMDB** | The local RPM database (`/var/lib/rpm`). Contains all installed packages. |
| **Sack** | An in-memory data structure that aggregates RPMDB + repository metadata. |
| **NEVRA** | Name-Epoch-Version-Release-Architecture. The unique identifier for a package. |
| **Repository Metadata** | XML/SQLite files downloaded from remote repos containing package catalogs. |
| **history.sqlite** | SQLite database at `/var/lib/dnf/history.sqlite` storing transaction history. |
| **libdnf** | The C++ library that powers DNF's core functionality (including the Sack). |
| **.repo file** | Configuration file in `/etc/yum.repos.d/` that tells DNF where to find repositories. |

---

## The .repo File: Configuration, Not Data

A common misconception is that `.repo` files contain repository data. They don't — they contain **configuration** that tells DNF where to find the actual data.

### Location

```
/etc/yum.repos.d/*.repo
```

### What's Inside

```ini
[rhel-9-for-x86_64-baseos-rpms]
name = Red Hat Enterprise Linux 9 for x86_64 - BaseOS (RPMs)
baseurl = https://cdn.redhat.com/content/dist/rhel9/$releasever/x86_64/baseos/os
enabled = 1
gpgcheck = 1
gpgkey = file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release
sslcacert = /etc/rhsm/ca/redhat-uep.pem
sslclientkey = /etc/pki/entitlement/*-key.pem
sslclientcert = /etc/pki/entitlement/*.pem
```

### Key Fields

| Field | Purpose |
|-------|---------|
| `[section-name]` | Repository ID (e.g., `rhel-9-for-x86_64-baseos-rpms`) |
| `baseurl` | URL where the repository metadata and packages live |
| `metalink` | Alternative to baseurl; URL that returns a list of mirrors |
| `mirrorlist` | Alternative to baseurl; URL that returns mirror URLs |
| `enabled` | Whether this repo is active (1) or disabled (0) |
| `gpgcheck` | Whether to verify package signatures |
| `sslclientcert` | Client certificate for authentication (Red Hat CDN) |
| `sslclientkey` | Client key for authentication (Red Hat CDN) |

### Important: Variables in URLs

URLs often contain variables that DNF substitutes at runtime:

| Variable | Source | Example Value |
|----------|--------|---------------|
| `$releasever` | `/etc/os-release` or RPM | `9` |
| `$basearch` | System architecture | `x86_64` |

Example transformation:
```
Config:   https://cdn.redhat.com/.../rhel9/$releasever/$basearch/baseos/os
Resolved: https://cdn.redhat.com/.../rhel9/9/x86_64/baseos/os
```

### The .repo File is Just a Pointer

```
┌─────────────────────────────────────┐
│  /etc/yum.repos.d/redhat.repo       │
│  ─────────────────────────────────  │
│  baseurl = https://cdn.redhat.com/..│  ← Just a URL (pointer)
└─────────────────────────────────────┘
                 │
                 │  DNF follows this URL
                 ▼
┌─────────────────────────────────────┐
│  Remote Repository                  │
│  ─────────────────────────────────  │
│  repodata/repomd.xml                │  ← Actual metadata
│  repodata/*-primary.xml.gz          │  ← Package catalog
│  Packages/*.rpm                     │  ← Actual packages
└─────────────────────────────────────┘
```

---

## Red Hat CDN Authentication

Red Hat's Content Delivery Network (`cdn.redhat.com`) is **not publicly accessible**. It requires client certificate authentication tied to a valid Red Hat subscription.

### Why Downloads Fail Without Authentication

If you try to download directly from `cdn.redhat.com`:

```bash
curl https://cdn.redhat.com/content/dist/rhel9/9/x86_64/baseos/os/repodata/repomd.xml
# Result: 403 Forbidden
```

### How DNF Authenticates

When you register a RHEL system with `subscription-manager`, it provisions:

| File | Purpose |
|------|---------|
| `/etc/pki/consumer/cert.pem` | Identifies the registered system |
| `/etc/pki/consumer/key.pem` | Private key for the consumer cert |
| `/etc/pki/entitlement/*.pem` | Proves subscription entitlement |
| `/etc/rhsm/ca/redhat-uep.pem` | Red Hat's CA certificate |

DNF (via libdnf → librepo → libcurl) automatically uses these certificates when connecting to Red Hat URLs.

### The Authentication Flow

```
┌─────────────────────────────────────────────────────────────┐
│                         DNF                                 │
│                          │                                  │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ libdnf reads .repo file                             │   │
│  │ Sees: sslclientcert = /etc/pki/entitlement/*.pem    │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ librepo/libcurl establishes HTTPS connection        │   │
│  │ Presents client certificate to cdn.redhat.com       │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Red Hat CDN validates certificate                   │   │
│  │ Checks subscription entitlements                    │   │
│  │ Returns: 200 OK + metadata                          │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Implications for Custom Scripts

If you write a script that downloads from `cdn.redhat.com` without providing client certificates, you'll get `403 Forbidden`.

**Options:**

1. **Use DNF to download metadata first** (`dnf makecache`), then read from local cache
2. **Use public mirrors** (CentOS Stream, Rocky Linux, AlmaLinux) for testing
3. **Add certificate support** to your script (complex)

### Public Alternatives (No Authentication Required)

These RHEL-compatible distributions have public mirrors:

| Distribution | Base URL Example |
|--------------|------------------|
| CentOS Stream 9 | `https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/os/` |
| Rocky Linux 9 | `https://download.rockylinux.org/pub/rocky/9/BaseOS/x86_64/os/` |
| AlmaLinux 9 | `https://repo.almalinux.org/almalinux/9/BaseOS/x86_64/os/` |

---

## The Sack: DNF's In-Memory Search Engine

The "Sack" is the central data structure DNF uses to store and query package information.

### What It Is

- **In-Memory Object**: A Python object (`dnf.sack.Sack`) wrapping a C++ object from `libdnf`, which in turn wraps `libsolv`'s `Pool`.
- **Aggregator**: A unified container holding **all known packages**—both installed and available from repositories.
- **Transient**: It is built fresh every time DNF runs; it does not persist to disk.

### How It Is Built

The Sack is constructed by `base.fill_sack()` in `dnf/base.py`:

1. **Load System Packages**: Opens `/var/lib/rpm` (RPMDB) to find installed packages. These are added to the Sack as belonging to the `@System` repository.

2. **Load Available Packages**: Reads cached metadata files for enabled repositories from `/var/cache/dnf/`. These are usually binary `.solv` files or XML/SQLite files.

3. **Implicit Matching**: `libdnf` automatically matches installed packages against available packages using their NEVRA. If an installed package matches a package in the `rhel-9-baseos` metadata, the Sack links them in memory.

### Relevant Code

```python
# dnf/sack.py (lines 54-62)
def _build_sack(base):
    cachedir = base.conf.cachedir
    dnf.util.ensure_dir(cachedir)
    return Sack(pkgcls=dnf.package.Package, pkginitval=base,
                arch=base.conf.substitutions["arch"],
                cachedir=cachedir, rootdir=base.conf.installroot,
                logfile=os.path.join(base.conf.logdir, dnf.const.LOG_HAWKEY),
                logdebug=base.conf.logfilelevel > 9)
```

```python
# dnf/base.py (lines 461-465) - Loading system repo
self._sack.load_system_repo(build_cache=False)

# dnf/base.py (line 484) - Loading remote repo metadata
self._sack.load_repo(repo._repo, **mdload_flags)
```

---

## How DNF Matches Packages to Repositories

The matching logic is straightforward but important to understand:

### The NEVRA Key

DNF uses the tuple `(Name, Epoch, Version, Release, Architecture)` as a unique identifier. Two packages with identical NEVRAs are considered the same package.

### The Matching Process

1. **Build Index**: When the Sack loads repository metadata, it indexes every package by its NEVRA.

2. **Cross-Reference**: For each installed package (from RPMDB), DNF checks if an identical NEVRA exists in the remote index.

3. **Assign Origin**: If a match is found, the installed package can be associated with that repository.

### Code Representation

```python
# Conceptual logic (simplified)
remote_index = {}

# Index all available packages from repos
for pkg in sack.query().available():
    key = (pkg.name, pkg.epoch, pkg.version, pkg.release, pkg.arch)
    remote_index[key] = pkg.reponame  # e.g., 'rhel-9-baseos'

# Match installed packages
for pkg in sack.query().installed():
    key = (pkg.name, pkg.epoch, pkg.version, pkg.release, pkg.arch)
    if key in remote_index:
        origin = remote_index[key]  # Found!
    else:
        origin = "@System"  # No match in current repos
```

### Important Note on `pkg.reponame`

The `Package` object for **installed** packages (from RPMDB) reports `reponame` as `@System` because that's where it was loaded from. The actual origin lookup happens through:

1. Checking `history.sqlite` first (if populated)
2. Falling back to runtime matching against available packages

```python
# dnf/package.py (lines 81-94)
@property
def _from_repo(self):
    pkgrepo = None
    if self._from_system:
        pkgrepo = self.base.history.repo(self)  # Check history DB first
    if pkgrepo:
        return '@' + pkgrepo
    return self.reponame  # Fallback to runtime matching
```

---

## Where the Data Lives on Disk

### RPMDB (Installed Packages)

- **Location**: `/var/lib/rpm/`
- **Contents**: Berkeley DB or SQLite files containing all installed package metadata.
- **Access**: `rpm -qa` or DNF's `sack.query().installed()`

### Repository Metadata Cache

- **Location**: `/var/cache/dnf/<repo-id>-<hash>/repodata/`
- **Key Files**:
  - `repomd.xml` - Index file pointing to other metadata
  - `*-primary.xml.gz` or `*-primary.sqlite.bz2` - Package catalog
  - `*-filelists.xml.gz` - File listings
  - `*-other.xml.gz` - Changelogs

### Example: Finding bash in Primary Metadata

```bash
# Navigate to the cache
cd /var/cache/dnf/rhel-9-baseos-*/repodata/

# Find the primary file
grep "primary" repomd.xml

# Search for bash (XML format)
zgrep "bash-5.1.8" *-primary.xml.gz

# Or if SQLite format
bunzip2 -k *-primary.sqlite.bz2
sqlite3 *-primary.sqlite "SELECT name, version, release, arch FROM packages WHERE name='bash';"
```

---

## The History Database (history.sqlite)

### Location

`/var/lib/dnf/history.sqlite`

### Purpose

Records transaction history—what packages were installed, removed, or updated, and when.

### Schema (Key Tables)

- `trans` - Transaction records (timestamp, user, command)
- `rpm` - Package records (NEVRA, repo_id)
- `trans_item` - Links transactions to packages

### When Repository Info Gets Written

Repository information is written to `history.sqlite` **only during DNF transactions**:

```python
# dnf/base.py (line 1127-1128) - Beginning a transaction
tid = self.history.beg(rpmdb_version, using_pkgs, [], cmdline=cmdline,
                       comment=comment, persistence=self._persistence)
```

**Important**: If packages were installed by other means (e.g., Anaconda installer, manual `rpm -i`), they will NOT have repository info in the history database until a DNF transaction touches them.

### Querying the History Database

```bash
# Get repo for a specific package
sqlite3 /var/lib/dnf/history.sqlite \
  "SELECT r.repoid FROM rpm r WHERE r.name='bash';"

# List all packages with their repos from the latest transaction
sqlite3 /var/lib/dnf/history.sqlite \
  "SELECT r.name, r.version, ti.repoid 
   FROM trans_item ti 
   JOIN rpm r ON ti.item_id = r.item_id 
   ORDER BY r.name;"
```

---

## Manual Tracing: Finding Package Origin Yourself

Here is a step-by-step guide to manually trace where `bash-5.1.8-6.el9.x86_64` came from:

### Step 1: Verify the Package is Installed

```bash
rpm -q bash
# Output: bash-5.1.8-6.el9.x86_64
```

### Step 2: List Enabled Repositories

```bash
dnf repolist
# Note the repo IDs, e.g., rhel-9-baseos, rhel-9-appstream
```

### Step 3: Locate the Metadata Cache

```bash
ls -d /var/cache/dnf/rhel-9-baseos-*/repodata/
```

### Step 4: Search the Primary Metadata

```bash
cd /var/cache/dnf/rhel-9-baseos-*/repodata/

# For XML format
zgrep -l "bash" *-primary.xml.gz
zgrep "<name>bash</name>" *-primary.xml.gz -A 10

# For SQLite format
bunzip2 -c *-primary.sqlite.bz2 > /tmp/primary.sqlite
sqlite3 /tmp/primary.sqlite "SELECT * FROM packages WHERE name='bash';"
```

### Step 5: Verify the Match

If you find `bash-5.1.8-6.el9.x86_64` in the `rhel-9-baseos` primary metadata, you have manually confirmed the origin.

---

## Programmatic Discovery

### Using DNF's Python API

The scripts in this repository (`print_repo_info.py` and `repo_discovery.py`) demonstrate how to programmatically discover package origins.

### Core Logic

```python
import dnf

def discover_origin(package_name):
    base = dnf.Base()
    base.read_all_repos()
    base.fill_sack()
    
    # Build index of available packages
    remote_index = {}
    for pkg in base.sack.query().available():
        key = (pkg.name, pkg.epoch or 0, pkg.version, pkg.release, pkg.arch)
        remote_index[key] = pkg.reponame
    
    # Find installed package and match
    for pkg in base.sack.query().installed().filter(name=package_name):
        key = (pkg.name, pkg.epoch or 0, pkg.version, pkg.release, pkg.arch)
        origin = remote_index.get(key, "Unknown")
        print(f"{pkg.nevra}: {origin}")
    
    base.close()
```

### Key API Methods

| Method | Description |
|--------|-------------|
| `base.read_all_repos()` | Loads repo configs from `/etc/yum.repos.d/` |
| `base.fill_sack()` | Builds the Sack by loading RPMDB + metadata |
| `sack.query().installed()` | Returns query for installed packages |
| `sack.query().available()` | Returns query for packages in remote repos |
| `pkg.nevra` | Full package identifier string |
| `pkg.reponame` | Repository ID the package belongs to |

---

## Summary

1. **DNF does NOT always store repository info persistently** for every package.

2. **Runtime Discovery**: DNF matches installed packages (from RPMDB) against available packages (from repo metadata) using NEVRA as the key.

3. **The Sack is the brain**: It's an in-memory index rebuilt on every DNF invocation.

4. **history.sqlite records transactions**, not system state. Only packages touched by DNF transactions get their repo recorded there.

5. **Manual tracing is possible**: The raw data lives in `/var/cache/dnf/*/repodata/` as XML or SQLite files.

---

## References

- DNF Source Code: https://github.com/rpm-software-management/dnf
- libdnf Source Code: https://github.com/rpm-software-management/libdnf
- RPM Documentation: https://rpm.org/documentation.html

