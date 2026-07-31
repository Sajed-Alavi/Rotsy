# Retention policies

Policy-driven cleanup, so old tags do not accumulate forever.

## Creating a policy

**Retention & Cleanup → New policy**. A policy targets one repository and expresses a rule — keep the most recent N tags, delete anything older than N days, or match tags by pattern.

## Always preview first

Every policy has a **Preview** action that runs it as a dry run and lists exactly what *would* be deleted, without deleting anything.

Use it. A retention rule that looks obviously correct in the abstract will surprise you the first time it meets real tag names — `latest`, release candidates, and anything a build system pushed with an unusual pattern.

## Running

**Run** executes one policy. **Run all** executes every enabled policy, and also offers a global dry run so you can see the combined effect before committing.

Policies also run automatically on the daily schedule set by `RETENTION_RUN_AT`.

## Deleting a policy

Policies can be removed from the same page. Removing a policy does not restore anything it deleted.

## Interaction with scanning

Deleting a tag removes it from the registry but leaves its ledger entry and reports. That is intentional — "we used to ship an image with this critical CVE" is worth keeping.

## Remember the compaction

As with manual deletion, retention frees disk only after the Nexus **Compact blob store** task runs. See [Deleting images](/docs/deleting-images).
