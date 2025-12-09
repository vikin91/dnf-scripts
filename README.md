# DNF Scripts

Utility scripts for discovering package-to-repository mappings on RHEL systems.

## Scripts

| Script | Description |
|--------|-------------|
| `print_repo_info.py` | Uses DNF's built-in `pkg.reponame` property (requires DNF to have linked packages to repos) |
| `repo_discovery.py` | Manually cross-references installed packages against cached metadata files |

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

## License

GPL-2.0-or-later (same as DNF)
