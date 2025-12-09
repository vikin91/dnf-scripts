# DNF Scripts

Utility scripts for discovering package-to-repository mappings on RHEL systems.

## Scripts

| Script | Description |
|--------|-------------|
| `print_repo_info.py` | Uses DNF's built-in `pkg.reponame` property (requires DNF to have linked packages to repos) |
| `repo_discovery.py` | Manually cross-references installed packages against cached metadata files |
| `manual_metadata_download.py` | Downloads repo metadata WITHOUT using DNF (educational demo) |
| `build_offline_index.py` | Builds compact NEVRA index for air-gapped systems (run on connected system) |
| `repo_discovery_offline.py` | Discovers package origins on air-gapped systems (no DNF or network required!) |

### Key Difference

| Script | How It Works | When It Shows Repo Info |
|--------|--------------|------------------------|
| `print_repo_info.py` | Reads `pkg.reponame` from DNF Package objects | Only if DNF has already associated the package with a repo (may show `@System` otherwise) |
| `repo_discovery.py` | Builds NEVRA index from metadata files, then matches installed packages | As long as metadata files exist in `/var/cache/dnf/` |

**In practice**: `repo_discovery.py` is more reliable because it performs explicit NEVRA matching against the raw metadata, similar to how DNF internally discovers origins. `print_repo_info.py` depends on DNF's internal state which may not always link installed packages to their source repos.

## Prerequisites

- **Python 3** with `dnf` module (pre-installed on RHEL)
- **Root permissions** (to read DNF cache and configuration)
- **Enabled repositories** in `/etc/yum.repos.d/`

## What You Need on the RHEL System

| System State | `print_repo_info.py` | `repo_discovery.py` |
|--------------|---------------------|---------------------|
| Fresh install (no activation) | ❌ Shows `@System` | ❌ No metadata to match against |
| Activated + `dnf makecache` | ⚠️ May show `@System` | ✅ Works (reads metadata files) |
| After any DNF transaction | ✅ Works | ✅ Works |

**Key Point**: `repo_discovery.py` only needs cached metadata files on disk. It does NOT require DNF to have performed any transaction.

## Required Metadata for `repo_discovery.py`

### What Files Are Needed

The script requires **repository metadata** (package catalogs) to be cached locally. These files contain the list of all packages available in each repository.

### Default Location on RHEL

```
/var/cache/dnf/<repo-id>-<hash>/repodata/
```

Example for `rhel-9-baseos`:
```
/var/cache/dnf/rhel-9-baseos-a1b2c3d4e5f6/repodata/
├── repomd.xml                    # Index file (points to other files)
├── <hash>-primary.xml.gz         # Package catalog (XML format)
│   or
├── <hash>-primary.sqlite.bz2     # Package catalog (SQLite format)
├── <hash>-filelists.xml.gz       # File listings (optional)
└── <hash>-other.xml.gz           # Changelogs (optional)
```

The **critical file** is `primary.xml.gz` or `primary.sqlite.bz2` — this contains the NEVRA (Name-Epoch-Version-Release-Arch) for every package in the repository.

### How to Check If Metadata Exists

```bash
# List all cached repos
ls /var/cache/dnf/

# Check if a specific repo has metadata
ls /var/cache/dnf/rhel-9-baseos-*/repodata/
```

### How to Get Metadata If Missing

```bash
# Download metadata for all enabled repositories
sudo dnf makecache

# Or, download metadata for a specific repo only
sudo dnf makecache --repo=rhel-9-baseos
```

### Common Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| `/var/cache/dnf/` is empty | System not activated or repos not enabled | Activate RHEL subscription, enable repos |
| `repo_discovery.py` shows 0 available packages | Metadata not downloaded yet | Run `dnf makecache` |
| Packages show "No matching repo found" | Installed version not in current repo metadata | Normal for updated/removed packages |

## Usage

```bash
# Uses DNF's pkg.reponame (may show @System for unlinked packages)
sudo ./print_repo_info.py

# Explicit NEVRA matching against metadata files (recommended)
sudo ./repo_discovery.py

# Cache-only mode: fail if metadata is not cached (no network access)
sudo ./repo_discovery.py --cacheonly
```

### `repo_discovery.py` Options

| Flag | Description |
|------|-------------|
| `-C`, `--cacheonly` | Use only cached metadata. Fails if cache is missing. Without this flag, DNF may download metadata automatically. |

## How It Works

### `print_repo_info.py`
- Calls `base.fill_sack()` to load DNF state
- Iterates installed packages and reads `pkg.reponame`
- For installed packages, `reponame` is often `@System` unless DNF has linked it

### `repo_discovery.py`
- Calls `base.fill_sack()` to load DNF state
- Builds an index: `{(name, epoch, version, release, arch): repo_id}` from `sack.query().available()`
- Cross-references each installed package's NEVRA against this index
- Reports the matching repo or "No matching repo found"

See [DNF_PACKAGE_ORIGIN_DISCOVERY.md](DNF_PACKAGE_ORIGIN_DISCOVERY.md) for detailed technical documentation.

## Manual Metadata Download (Without DNF)

The `manual_metadata_download.py` script demonstrates how to download repository metadata **without using the DNF Python module**. This is useful for:

- Understanding the raw HTTP requests DNF makes
- Systems where DNF is not installed but you have the RPMDB
- Educational purposes

### How It Works

1. **Parses `.repo` files** to extract `baseurl`, `metalink`, or `mirrorlist`
2. **Downloads `repomd.xml`** — the index file at `<baseurl>/repodata/repomd.xml`
3. **Parses `repomd.xml`** to find the primary metadata filename
4. **Downloads `primary.xml.gz`** — the package catalog
5. **Saves to DNF-compatible directory structure**

### The Download Process Visualized

```
.repo file                     Remote Repository
     │                               │
     ▼                               │
baseurl = https://mirror.../        │
     │                               │
     └────────── GET ───────────────►│
                                     │
                  repomd.xml ◄───────┘
                      │
                      ▼
              Parse: find <data type="primary">
                      │
                      ▼
         <hash>-primary.xml.gz
                      │
     └────────── GET ───────────────►│
                                     │
         primary.xml.gz ◄────────────┘
                      │
                      ▼
         /var/cache/dnf/<repo>-<hash>/repodata/
```

### Usage

```bash
# Run the demo (requires network access and a valid .repo file)
sudo ./manual_metadata_download.py

# Or modify the script to use a known baseurl directly:
# baseurl = 'https://mirror.example.com/rhel/9/baseos/x86_64/os/'
# download_repo_metadata('my-repo', baseurl, '/tmp/my-cache')
```

### Limitations

- Does not handle metalink/mirrorlist parsing (only direct baseurl)
- Does not verify GPG signatures
- Does not compute the exact same hash as libdnf (close approximation)
- For production use, use `dnf makecache` instead

## Air-Gapped System Support

For systems without internet access, we provide a two-script workflow:

### The Problem

On an air-gapped system:
- ❌ No network access to download metadata
- ❌ DNF cache is empty
- ✅ RPMDB exists (installed packages are known)
- ❓ Need to determine which repo each package came from

### The Solution

```
┌─────────────────────────────────────────────────────────────┐
│                    CONNECTED SYSTEM                         │
│                                                             │
│  $ ./build_offline_index.py \                               │
│        --baseurl https://mirror.../baseos/x86_64/os/ \      │
│        --repo-id rhel-9-baseos \                            │
│        --output indexes/rhel-9-baseos.json                  │
│                                                             │
│  Output: indexes/rhel-9-baseos.json (~5-10 MB)              │
└─────────────────────────────────────────────────────────────┘
                            │
                            │  (USB drive / secure transfer)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    AIR-GAPPED SYSTEM                        │
│                                                             │
│  $ ./repo_discovery_offline.py \                            │
│        --index indexes/rhel-9-baseos.json \                 │
│        --index indexes/rhel-9-appstream.json                │
│                                                             │
│  Output: Package → Repository mapping                       │
└─────────────────────────────────────────────────────────────┘
```

### Step 1: Build Index (Connected System)

```bash
# Build index for a single repo
./build_offline_index.py \
    --baseurl https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/os/ \
    --repo-id centos-9-baseos \
    --output indexes/centos-9-baseos.json

# Build indexes for multiple repos (run multiple times)
./build_offline_index.py \
    --baseurl https://mirror.stream.centos.org/9-stream/AppStream/x86_64/os/ \
    --repo-id centos-9-appstream \
    --output indexes/centos-9-appstream.json

# Optional: compress for smaller transfer
./build_offline_index.py --baseurl ... --output index.json --compress
```

### Step 2: Transfer to Air-Gapped System

Copy the `indexes/` directory to the air-gapped system via USB, CD, or approved transfer method.

### Step 3: Run Discovery (Air-Gapped System)

```bash
# With multiple indexes
./repo_discovery_offline.py \
    --index indexes/centos-9-baseos.json \
    --index indexes/centos-9-appstream.json

# Or with an index directory
./repo_discovery_offline.py --index-dir indexes/

# Output as CSV for further processing
./repo_discovery_offline.py --index-dir indexes/ --format csv > packages.csv

# Show only unmatched packages
./repo_discovery_offline.py --index-dir indexes/ --unmatched-only
```

### Index File Format

The index is a simple JSON structure:

```json
{
  "metadata": {
    "repo_id": "rhel-9-baseos",
    "baseurl": "https://...",
    "generated": "2025-01-15T10:30:00",
    "package_count": 1234
  },
  "packages": {
    "bash|0|5.1.8|6.el9|x86_64": "rhel-9-baseos",
    "kernel|0|5.14.0|362.el9|x86_64": "rhel-9-baseos",
    ...
  }
}
```

### Why This Works Without DNF

| Component | What It Does | Dependency |
|-----------|--------------|------------|
| `build_offline_index.py` | Downloads & parses primary.xml.gz | Python 3 + urllib (standard library) |
| `repo_discovery_offline.py` | Reads RPMDB, matches against index | Python 3 + `rpm` command |

Neither script requires the `dnf` Python module or network access on the target system.

### Keeping Indexes Updated

Repository metadata changes over time (new packages, security updates, version removals). See [OFFLINE_INDEX_UPDATE_STRATEGY.md](OFFLINE_INDEX_UPDATE_STRATEGY.md) for:

- How often repositories change
- Types of changes (additions vs. removals)
- Strategies for keeping indexes current
- Recommended update cadence

## License

GPL-2.0-or-later (same as DNF)
