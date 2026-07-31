# Storage analysis

**Storage Analyzer** answers "what is using the space?" per repository.

## Running an analysis

Pick a repository and click Analyze. Results stream in over Server-Sent Events as the backend walks the assets, so a large repository shows partial results immediately rather than a spinner for two minutes.

The result is a ranked breakdown you can expand, plus totals.

## Caching

Analyses are cached. Re-opening a repository shows the previous result immediately and tells you when it was taken; re-running refreshes it. For a large repository, a walk is expensive — repeating it on every page view would be antisocial toward Nexus.

## Blobstore vs repository

A repository's size is the sum of its assets. A **blobstore's** usage is what is actually on disk, which is a different number — blobs are shared between repositories that use the same store, and deleted-but-not-yet-compacted blobs still occupy space.

If blobstore usage is much higher than the sum of repository sizes, you have uncompacted deletions. See [Deleting images](/docs/deleting-images).

## Metrics over time

Storage Analyzer is a point-in-time view. **Metrics** tracks the same numbers over time, which is what you want for spotting growth trends, and is what [alerts](/docs/alerts) evaluate against.
