# Self-Hosted PostgreSQL on EC2 — Ops Runbook

A reference for running PostgreSQL on a single EC2 instance (pilot/low-traffic scale).
Keep this in the repo. Update it whenever the setup changes.

> **Golden rule:** A backup you have never restored is a hope, not a backup.
> Test the restore monthly (see §1.3).

---

## 0. Baseline setup (fill in for your instance)

| Thing | Value |
|---|---|
| EC2 instance id | `i-xxxxxxxx` |
| Instance class | e.g. `t4g.small` (2 GB RAM, ARM/Graviton) |
| OS | e.g. Amazon Linux 2023 / Ubuntu 24.04 |
| PostgreSQL version | e.g. 16 |
| Data volume (EBS) | separate gp3 volume mounted at `/var/lib/postgresql` |
| Backup bucket | `s3://YOUR-BUCKET/pg-backups` |
| DB name | `yourdb` |
| Backend security group | `sg-xxxxxxxx` |

**Key design decisions:**
- Data dir + WAL live on a **separate EBS volume** from the OS root (grow live, snapshot independently).
- Instance uses an **IAM role** for S3 access — never long-lived access keys on the box.
- SSH is via **SSM Session Manager** (no open port 22). Postgres port 5432 open **only** to the backend SG.

---

## 1. Backups (never skip)

**Chosen approach: AWS Data Lifecycle Manager (DLM)** — scheduled EBS snapshots of the
instance's root volume (which holds the Docker volume `onsumm_pgdata`), with automatic
retention. Fully managed, no scripts. Plus an optional weekly logical dump for
table-level / portable restores.

### 1.1 DLM policy (primary — set up once in the console)
1. **Tag the volume:** EC2 → Volumes → the volume on `i-0f5581552e59645fa` →
   Manage tags → `Backup = onsumm-db`.
2. **Create policy:** EC2 → Lifecycle Manager → Create → *EBS snapshot policy*:
   - Target by tag `Backup = onsumm-db`
   - Schedule: Daily **02:30 UTC** (08:00 IST), retention **keep 14**
   - (optional) second schedule: Weekly, keep 8
   - IAM role: **Default** (`AWSDataLifecycleManagerDefaultRole` — console creates it)
   - Copy tags: on; Fast Snapshot Restore: off
3. (recommended) Add a **cross-region copy** rule (e.g. → ap-southeast-1) for DR.

**Consistency note:** EBS snapshots of a running DB are *crash-consistent*. Postgres
recovers cleanly from these via WAL replay (same as power loss). Good enough for a pilot.
For app-consistency later, use DLM pre/post SSM scripts (`CHECKPOINT` + fsfreeze).

### 1.2 Optional weekly logical dump (table-level / portable restore)
DLM restores a whole volume, not one table. Keep a logical dump for that case:
```bash
# /usr/local/bin/pg-dump-s3.sh  (runs on the EC2 host)
#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date +%F)
docker exec onsumm-pg pg_dump -U postgres -Fc --no-owner --no-privileges onsumm_db \
  > /tmp/onsumm_db-$STAMP.dump
aws s3 cp /tmp/onsumm_db-$STAMP.dump s3://YOUR-BUCKET/pg-dumps/ && rm -f /tmp/onsumm_db-$STAMP.dump
```
Drive with a systemd timer (`OnCalendar=Mon 03:00`). Needs an instance IAM role with
S3 write + an S3 bucket with a 30-day lifecycle rule. Optional — DLM is the primary.

### 1.3 Restore drills (do each at least once, then periodically)
- **DLM restore:** create a volume from the latest snapshot → attach to a test instance →
  start a `postgres:18` container pointed at it → confirm row counts.
- **Logical restore:** `docker exec -i onsumm-pg pg_restore -U postgres --no-owner -d restore_test < dump`.

Record last successful restore test here: `LAST RESTORE TEST: ____`

---

## 2. Patching, security & disk-full

### 2.1 OS security patches (auto)
- **Amazon Linux 2023:** `dnf install dnf-automatic`, enable security-only in `/etc/dnf/automatic.conf`.
- **Ubuntu:** `unattended-upgrades`, security pocket only.
- Reboots stay **manual** — apply during a maintenance window.

### 2.2 PostgreSQL upgrades
- **Minor** (16.3 → 16.4): safe, in-place. `apt upgrade postgresql-16` / `dnf upgrade postgresql16`, restart. Do every couple months.
- **Major** (16 → 17): planned only, with a fresh backup first. Use `pg_upgrade`. Rare.

### 2.3 Disk-full (the #1 killer of self-hosted PG)
- **CloudWatch agent** reports disk metrics (EC2 does not natively). Alarm at **75%** usage.
- Data + WAL on a **separate gp3 EBS volume** — grow it live (`modify-volume` + `growpart` + `resize2fs`/`xfs_growfs`) without touching the OS disk.
- Quick manual check:
  ```bash
  df -h /var/lib/postgresql
  du -sh /var/lib/postgresql/*/main    # or /data depending on layout
  ```

### 2.4 Network / access hardening
- Security group: 5432 inbound **only from backend SG** (`sg-xxxxxxxx`). Never `0.0.0.0/0`.
- No open SSH — use **SSM Session Manager**: `aws ssm start-session --target i-xxxxxxxx`.
- `pg_hba.conf`: require `scram-sha-256`, restrict to the app subnet.
- `postgresql.conf`: `listen_addresses` = private IP only.

---

## 3. Downtime / recovery

Single instance — accept rare reboots, minimize impact:
- Backend uses a **connection pool with retry** so a short reboot blip doesn't crash the app.
- No HA / failover yet. When needed, the clean path is a **streaming-replication read replica** — don't build it prematurely.

### 3.1 Instance-dead recovery runbook (~15 min if restore is tested)
1. Launch a new EC2 from your base AMI (same setup as §0).
2. Attach/format the data EBS volume (or a fresh one).
3. Install Postgres (same major version).
4. Restore latest dump from S3 (§1.3 restore steps into the real DB).
5. Update the security group + repoint the backend's DB host env var.
6. Verify app connects and row counts look sane.

---

## 4. Tuning

Generate a config with **PGTune** (pgtune.leopard.in.ua) using the instance RAM/CPU.
Starting point for a `t4g.small` (2 GB RAM) pilot:
```conf
shared_buffers = 512MB
effective_cache_size = 1536MB
work_mem = 8MB
maintenance_work_mem = 128MB
max_connections = 50            # keep low — use a pooler, don't raise this
wal_compression = on
```

**Connection pooler (pgbouncer)** in front is higher-leverage than raising `max_connections`.
Run in `transaction` pooling mode for a typical web backend.

---

## 5. Quick command reference

```bash
# service
systemctl status postgresql
systemctl restart postgresql

# backups
systemctl list-timers pg-backup.timer
journalctl -u pg-backup.service --since today
/usr/local/bin/pg-backup.sh            # manual backup now

# health
df -h /var/lib/postgresql              # disk
psql -U postgres -c "SELECT count(*) FROM pg_stat_activity;"   # connections
psql -U postgres -c "SELECT pg_size_pretty(pg_database_size('yourdb'));"

# connect via SSM (no SSH)
aws ssm start-session --target i-xxxxxxxx
```

---

## Checklist before calling it "production-ready"
- [ ] Nightly backup timer enabled and verified running
- [ ] S3 lifecycle + versioning + object lock configured
- [ ] Restore tested at least once (date recorded in §1.3)
- [ ] Separate EBS volume for data, disk alarm at 75%
- [ ] IAM role (no access keys), SG locked to backend, SSH closed
- [ ] `unattended-upgrades` / `dnf-automatic` enabled
- [ ] Backend has connection retry / pooler
- [ ] This runbook filled in with real instance ids and bucket name
