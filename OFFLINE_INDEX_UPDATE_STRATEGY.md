# Offline Index Update Strategy

This document describes strategies for keeping offline NEVRA indexes current on air-gapped systems.

## Understanding Repository Changes

### How Often Do Repositories Change?

| Repository Type | Update Frequency | Change Volume | Examples |
|-----------------|------------------|---------------|----------|
| **Stable Point Release** | Daily to weekly | Low-Medium | RHEL 9.4 baseos/appstream |
| **Extended Update Support (EUS)** | Weekly to monthly | Low | RHEL 8.6 EUS |
| **Stream/Rolling** | Daily | High | CentOS Stream 9, Fedora Rawhide |
| **Frozen/Archive** | Rarely (critical only) | Very Low | RHEL 8.4 after EOL |

### Types of Changes

Repository metadata changes fall into three categories:

#### 1. Additions (Most Common)

New packages or new versions of existing packages are added.

```
Before:
  bash-5.1.8-6.el9.x86_64

After:
  bash-5.1.8-6.el9.x86_64    ← still present (temporarily)
  bash-5.1.8-7.el9.x86_64    ← new security update
```

**Impact on Index:** New NEVRA entries need to be added.

#### 2. Removals (Common)

Old package versions are removed when newer versions are published. Red Hat does not keep unlimited historical versions in active repositories.

```
Before:
  bash-5.1.8-6.el9.x86_64
  bash-5.1.8-7.el9.x86_64

After:
  bash-5.1.8-7.el9.x86_64    ← only latest remains
  (bash-5.1.8-6.el9 REMOVED)
```

**Impact on Index:** If your index only contains current repo state, you lose the ability to match older installed packages.

#### 3. Rebuilds (Occasional)

A package is rebuilt with the same version string but different content (e.g., to incorporate a backported fix without bumping the version).

```
Before:
  openssl-3.0.7-24.el9.x86_64  (checksum: abc123)

After:
  openssl-3.0.7-24.el9.x86_64  (checksum: def456, rebuilt)
```

**Impact on Index:** NEVRA is identical, so no index change needed. The match still works.

### Typical Change Patterns by Event

| Event | What Changes | Frequency |
|-------|--------------|-----------|
| **Security Errata (RHSA)** | 1-10 packages updated | Multiple per week |
| **Bug Fix Errata (RHBA)** | 1-50 packages updated | Weekly |
| **Enhancement Errata (RHEA)** | Variable | Monthly |
| **Point Release (9.3 → 9.4)** | Hundreds of packages | Every 6 months |
| **Module Stream Update** | Packages in that module | Variable |

---

## The Staleness Problem

When an air-gapped system has packages installed from an older repository state, a current index may not match them.

### Example Scenario

```
Timeline:
─────────────────────────────────────────────────────────────────────

Jan 15: Air-gapped system installed with RHEL 9.4 media
        bash-5.1.8-6.el9 installed

Feb 1:  Security update released
        bash-5.1.8-6.el9 → bash-5.1.8-7.el9
        (old version removed from active repo)

Feb 15: You build an offline index from current repo
        Index contains: bash-5.1.8-7.el9
        Index does NOT contain: bash-5.1.8-6.el9

Result: Package on air-gapped system shows "No match"
        even though it legitimately came from rhel-9-baseos
```

---

## Update Strategies

### Strategy 1: Full Replacement (Simplest)

**Approach:** Periodically rebuild the index from scratch and replace the old one.

```bash
# Monthly rebuild
./build_offline_index.py \
    --baseurl https://cdn.redhat.com/.../baseos/os/ \
    --repo-id rhel-9-baseos \
    --output indexes/rhel-9-baseos.json
```

**Pros:**
- Simple to implement
- Index stays small
- Always reflects current repo state

**Cons:**
- Loses matches for packages from older repo snapshots
- Match rate degrades as system ages without updates

**Best For:** Systems that are regularly updated and stay close to current repo state.

---

### Strategy 2: Cumulative/Incremental Merge (Recommended)

**Approach:** Merge new index data with existing index, keeping all historical entries.

```bash
# Build new index
./build_offline_index.py --baseurl ... --output new.json

# Merge with existing (keeps old + adds new)
./merge_indexes.py --base indexes/cumulative.json \
                   --update new.json \
                   --output indexes/cumulative.json
```

**Pros:**
- Best match rate over time
- Handles both old and new package versions
- Single file to manage

**Cons:**
- Index grows over time (may become large after years)
- Contains "stale" entries for packages that no longer exist
- Needs periodic cleanup/compaction

**Best For:** Long-lived air-gapped systems that may have packages from various points in time.

**Growth Estimate:**

| Time Period | Approximate Index Size |
|-------------|------------------------|
| Initial | 5-10 MB per repo |
| After 1 year | 15-30 MB per repo |
| After 3 years | 30-60 MB per repo |

---

### Strategy 3: Version-Specific Indexes

**Approach:** Maintain separate indexes for each RHEL point release.

```
indexes/
├── rhel-9.0-baseos.json
├── rhel-9.1-baseos.json
├── rhel-9.2-baseos.json
├── rhel-9.3-baseos.json
├── rhel-9.4-baseos.json
├── rhel-9.0-appstream.json
├── rhel-9.1-appstream.json
...
```

**Pros:**
- Can identify exact origin (which point release)
- Clean separation of data
- Easy to add new versions

**Cons:**
- More files to manage
- Must determine which indexes to build (vault access needed)
- Larger total storage

**Best For:** Environments with mixed systems at different patch levels.

---

### Strategy 4: Vault/Archive Indexes

**Approach:** Build indexes from Red Hat's vault repositories, which contain historical packages.

Red Hat Vault URLs (examples):
```
# Point release archives
https://cdn.redhat.com/content/dist/rhel9/9.0/x86_64/baseos/os/
https://cdn.redhat.com/content/dist/rhel9/9.1/x86_64/baseos/os/
https://cdn.redhat.com/content/dist/rhel9/9.2/x86_64/baseos/os/

# Current (latest point release)
https://cdn.redhat.com/content/dist/rhel9/9/x86_64/baseos/os/
```

**Pros:**
- Official historical data
- Complete coverage of past versions
- Authoritative source

**Cons:**
- Requires Red Hat subscription for vault access
- More complex URL management
- Vault may not have all historical states

**Best For:** Enterprise environments with subscription access.

---

### Strategy 5: Snapshot-Based

**Approach:** Take periodic snapshots of indexes and keep multiple versions.

```
indexes/
├── current/
│   ├── rhel-9-baseos.json        # Latest
│   └── rhel-9-appstream.json
└── snapshots/
    ├── 2024-07-01/
    │   ├── rhel-9-baseos.json
    │   └── rhel-9-appstream.json
    ├── 2024-10-01/
    │   └── ...
    └── 2025-01-01/
        └── ...
```

**Pros:**
- Full historical coverage
- Can match any point in time
- Easy to understand

**Cons:**
- Highest storage requirements
- Must load multiple indexes for best coverage
- Complex management

**Best For:** Audit/compliance scenarios requiring exact version tracking.

---

## Recommended Approach

For most air-gapped environments, we recommend a **hybrid approach**:

### Primary: Cumulative Index (Strategy 2)

Maintain a single, ever-growing index per repository that accumulates all package versions seen over time.

```
indexes/
├── rhel-9-baseos-cumulative.json
└── rhel-9-appstream-cumulative.json
```

### Secondary: Point Release Indexes (Strategy 3)

For new deployments or major updates, build indexes from vault for specific point releases.

```
indexes/
├── rhel-9-baseos-cumulative.json
├── rhel-9-appstream-cumulative.json
├── point-releases/
│   ├── rhel-9.0-baseos.json     # From vault
│   ├── rhel-9.2-baseos.json
│   └── rhel-9.4-baseos.json
```

### Update Cadence

| Environment Type | Recommended Update Frequency |
|------------------|------------------------------|
| Security-sensitive | Weekly to bi-weekly |
| General production | Monthly |
| Stable/static systems | Quarterly |
| Archive/reference | As needed |

---

## Implementation Checklist

### Initial Setup

- [ ] Identify all repositories used by target systems
- [ ] Build initial indexes from current repo state
- [ ] Build indexes from vault for historical point releases (if needed)
- [ ] Transfer indexes to air-gapped environment
- [ ] Verify match rate on sample systems

### Ongoing Maintenance

- [ ] Schedule regular index rebuilds (monthly recommended)
- [ ] Merge new indexes with cumulative index
- [ ] Transfer updated indexes to air-gapped environment
- [ ] Monitor match rate trends (declining rate indicates staleness)
- [ ] Archive old snapshots as needed

### Monitoring

Track these metrics to detect when indexes need updating:

| Metric | Target | Action if Below |
|--------|--------|-----------------|
| Match rate | > 95% | Update indexes |
| Index age | < 30 days | Rebuild indexes |
| Unmatched critical packages | 0 | Investigate immediately |

---

## Future Enhancements

Potential tooling improvements:

1. **`merge_indexes.py`** — Merge old and new indexes, preserving historical entries
2. **`diff_indexes.py`** — Show what changed between two index versions
3. **`compact_indexes.py`** — Remove entries for packages no longer in any known repo
4. **`validate_index.py`** — Verify index integrity and coverage
5. **Auto-detection** — Script that determines which indexes to load based on `/etc/os-release`

