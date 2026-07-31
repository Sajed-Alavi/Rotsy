# Workflow: responding to a new CVE

A serious vulnerability is announced. You need to know whether you are exposed, and where.

## 1. Update the databases first

**Vulnerability Scanning → Database Management → Force**, both scanners.

This is the step people skip. A brand-new CVE will not be in a database built before it was published, and scanning against a stale database produces a confident, wrong "not affected".

Note the build dates after the update. If they predate the announcement, the database does not have it yet — wait rather than concluding you are clear.

## 2. Search existing findings

**Vulnerability Scanning → Findings**, search the CVE id.

This searches what has already been scanned. It is instant, and if it returns hits you have your answer immediately. If it returns nothing, that is not yet a clean bill of health — the affected images may not have been re-scanned since the database update.

## 3. Re-scan what matters

Findings reflect the database as it was at scan time. To get a current answer, re-scan against the updated database.

Go to **Images**, and use the Scan button on the images you actually care about — production, internet-facing, whatever your criteria are. There is deliberately no "scan everything" button; on a large registry that is hours of work, and it is rarely what you want in the first hour of an incident.

Remember that `baseline` images have never been scanned at all. If the vulnerable package is likely in older images, scan those explicitly.

## 4. Confirm and act

Re-check Findings. For each affected image, the finding gives the package, the installed version and the fixed version where one exists.

## 5. Verify the fix

Push the rebuilt image. If push-triggered scanning is configured it is scanned automatically; confirm the new tag comes back clean and that the old tag is the only one still flagged.

Then delete the vulnerable tags, and run the **Compact blob store** task from Task Manager — otherwise the content is still on disk and still pullable by digest.

## Worth knowing in advance

**Two scanners will disagree.** A CVE found by one and not the other is normal — different databases, different matching. During an incident, treat either as a hit.

**`unknown` severity is not "safe".** New CVEs frequently arrive without a severity assigned. Filter on the CVE id, not on severity.
