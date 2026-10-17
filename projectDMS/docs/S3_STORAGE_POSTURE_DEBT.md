# S3 storage posture: two carried items

Both were established by measurement during the R-A8I staging execution. Neither
is fixed by R-A8J; both are written down here so they are decided rather than
rediscovered.

---

## 1. Production documents and production backups share one bucket

**Observed.** Production runs a single S3 bucket, `contraclaim`. It serves
document storage (`AWS_BUCKET_NAME`) and, under
`BACKUP_S3_PREFIX=contraclaim/backups`, the off-site backup copy as well. There is
no second production bucket; the `contraclaim-backup` name that appears elsewhere
is a cron file name, not a bucket.

**Why it matters.** An off-site backup exists to survive the loss of the thing it
backs up. A backup sharing a bucket with the data survives a *server* loss but not
a bucket-level one: one deletion policy, one lifecycle rule, one credential
compromise, one accidental `--recursive` reaches both. The blast radius of the
backup is the blast radius of the data it is protecting, which is the one property
a backup is supposed not to have.

Staging is already the shape production should be:
`contraclaim-staging-documents-mp` and `contraclaim-staging-backups-mp` are
separate buckets, and the staging runtime principal holds `DeleteObject` on the
documents bucket and not on the backups one.

**Deliberately not changed in R-A8J.** Splitting the production bucket is a
production configuration change with a data move behind it, and this phase deploys
nothing. It is sequenced after the release, not inside it.

**What closing it involves, when it is scheduled.**

1. Create a production backups bucket in `ap-south-1`, versioning on, public
   access blocked.
2. Give the production runtime principal `DeleteObject` on the documents bucket
   and **not** on the backups bucket, matching the staging policy.
3. Point `BACKUP_S3_BUCKET` at the new bucket and drop `BACKUP_S3_PREFIX` back to
   a plain path.
4. Copy the existing backup prefix across, verify one archive byte-for-byte and
   with `gzip -t`, and only then stop writing to the old prefix.
5. Re-run `scripts/backup_offsite_s3.sh` and confirm the new bucket receives the
   next nightly run.

Until then: the off-site leg is a copy on different hardware, not a copy in a
different failure domain, and any RPO statement should say so.

---

## 2. The R-A8I probe object cannot be removed by the principal that wrote it

**Disposition: EXPECTED RESIDUE FROM A LEAST-PRIVILEGE POLICY. Not a gate
defect.**

**Observed.** R-A8I's positive control wrote and read back one object in the
staging backup bucket and could not delete it:

| | |
|---|---|
| Bucket | `contraclaim-staging-backups-mp` (`ap-south-1`) |
| Key | `_ra8i-probe/R-A8I-20260906T0311IST/probe.txt` |
| Size | 43 bytes |
| Content | the literal string `R-A8I staging probe R-A8I-20260906T0311IST` |
| Contains | no credential, no business data, no personal data |

`DeleteObject`, `DeleteObjects`, `ListBucketVersions` and
`PutLifecycleConfiguration` are all denied to the staging runtime principal.

**Why this is not a gate defect.** Gate 8's eight bullets are backup succeeds,
uploads backup succeeds, Qdrant backup or rebuild verified, FalkorDB backup
verified, Redis requirement accepted or tested, full restore drill, RPO/RTO
recorded, rollback documented and tested. **None of them requires deleting a
backup object.** A backup principal that cannot delete from the backup bucket is
the correct posture, not a gap: the whole point of withholding `DeleteObject` is
that a compromised or buggy backup runner cannot destroy the backups. The residue
is the cost of that property, and it is the right trade.

**Do not broaden the staging policy to clean this up.** Granting `DeleteObject`
to the runtime principal to remove one 43-byte file would trade a permanent
weakening of the backup posture for a cosmetic tidy-up.

**Owner cleanup, if it is wanted.** Requires an account-level principal, not the
staging runtime one. Either:

```bash
# With owner/admin credentials - NOT the staging runtime pair.
aws s3api delete-object \
  --bucket contraclaim-staging-backups-mp \
  --key '_ra8i-probe/R-A8I-20260906T0311IST/probe.txt'
```

or, in the S3 console, open `contraclaim-staging-backups-mp`, enter the
`_ra8i-probe/` prefix, select `probe.txt` and delete it. If bucket versioning is
on, the delete leaves a marker and the version must be removed from the
**Show versions** view to remove the object itself.

**A better fix than either, for the next run.** Put the probe under a prefix
covered by a lifecycle rule that expires objects after a day, set once by the
owner. The runtime principal still gets no delete permission, and the probe
removes itself.

---

## Provenance

Both items are measurements from `R-A8I-STAGING-EXECUTION-RECEIPT.md` §2, §3 and
§13, re-derived rather than restated: the single production bucket from
`AWS_BUCKET_NAME` and `BACKUP_S3_PREFIX` in the production environment, and the
delete denial from the run's own positive-control matrix.
