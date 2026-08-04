# Access rules cookbook

Worked configurations, shortest first. Each one is a complete recipe: the role, its access mode, its permissions and its rules. For the grammar behind them, see [the permission model](/docs/permission-model).

Every recipe assumes one habit. **Set the baseline roles your users hold to `scoped` too.** Effective access is the union across a user's roles, so a single `unrestricted` role with no matching rules reopens everything the recipe was meant to close.

## 1. One team, one prefix

*Give a team read and scan access to every image called `abrisham*`, anywhere.*

Role **abrisham-team**, mode `scoped`, permissions `repositories:read`, `scan:read`, `scan:execute`, `storage:read`.

| Effect | Actions | Repository | Image |
|---|---|---|---|
| allow | read, scan | `*` | `abrisham*` |

They see `abrisham-frontend` and `abrisham-api` in every repository, can scan them, and cannot delete them. Nothing else appears anywhere in the UI — not in the browser, not in the reports list, not in storage analysis.

To confine them to one repository, change the repository pattern from `*` to that repository's name.

## 2. Read but never delete

*The same team, but they may clean up their own images.*

Add `repositories:write` to the role's permissions and the `delete` action to the rule:

| Effect | Actions | Repository | Image |
|---|---|---|---|
| allow | read, scan, delete | `*` | `abrisham*` |

Drop `delete` from the actions and they keep everything else — the permission key `repositories:write` alone is not enough to delete anything the rules do not reach.

## 3. Everything in production

*A release engineer who works across every production repository.*

Role **prod-release**, mode `scoped`, permissions `repositories:read`, `scan:read`, `scan:execute`, `metrics:read`.

| Effect | Actions | Repository | Image |
|---|---|---|---|
| allow | read, scan | `prod-*` | `**` |

`prod-*` matches `prod-eu`, `prod-us`, `prod-internal` — including ones created next month, with no rule to update. `staging-eu` stays invisible.

Because the image pattern is `**`, this role has repository-wide reach and *can* enable scanning for those repositories. Narrow the image pattern and that ability goes away.

## 4. Everything except the secrets

*Broad access, with a carve-out.* This is what Artifactory's exclude patterns do; here it is a deny rule.

Role **platform**, mode `scoped`:

| Effect | Actions | Repository | Image |
|---|---|---|---|
| allow | read, scan | `*` | `**` |
| deny | read, scan | `*` | `*-secrets*` |

Denies are read first and beat allows **within the same role**. `billing-secrets-store` disappears; everything else stays.

The deny only protects this role. If the same people hold another role that allows those images, they will still see them — check with the effective-access view before assuming a deny is doing its job.

## 5. A group namespace

*A team owns everything under `team/` in one repository.*

| Effect | Actions | Repository | Image |
|---|---|---|---|
| allow | read, scan, delete | `docker-hosted` | `team/**` |

Note `**`, not `*`. `team/*` would match `team/api` but stop at `team/api/edge`, which is rarely what people mean. Use `team/*` deliberately when you want exactly one level.

## 6. A CI token that can only scan one repository

*A pipeline that gates on scan results and nothing else.*

Create role **ci-gate**, mode `scoped`, permissions `scan:read`, `scan:execute`, `repositories:read`:

| Effect | Actions | Repository | Image |
|---|---|---|---|
| allow | read, scan | `ci-images` | `**` |

Create a dedicated user holding **only** that role, then issue an API token from that account with scopes `scan:read` and `scan:execute`.

A token's authority is the intersection of its declared scopes with its owner's live permissions, and it inherits the owner's access rules. Deactivating that account revokes the pipeline instantly.

## 7. Retiring a repo-by-repo setup

If you are coming from per-repository entries — one row per repository, each repeating the same image pattern — collapse them into one rule with a repository wildcard:

| Before | After |
|---|---|
| `prod-eu` / `abrisham*`, `prod-us` / `abrisham*`, `prod-ap` / `abrisham*` | `prod-*` / `abrisham*` |

Delete the old rows only after testing the new one. New production repositories are then covered automatically instead of silently missing.

## Testing before you save

Every role editor has a **Test these rules** panel. Enter a repository and an image name and it reports the actions the role would allow, plus every rule that matched — dimming rules that matched the repository but not the image, which is the usual reason a rule "does nothing".

Two habits worth keeping:

- Test the name you expect to be **denied**, not just the one you expect to be allowed. A pattern that is too broad looks perfect until you check the thing it should have excluded.
- After assigning the role to a real person, check the effective-access view on that user. It resolves every role they hold, which is where union surprises show up.

## When a rule seems to do nothing

| Symptom | Usual cause |
|---|---|
| The user still sees everything | They hold another role that is `unrestricted` with no matching rules. Set it to `scoped` |
| The rule matched but granted nothing | Its image pattern missed. The tester dims these |
| `team/*` misses nested images | `*` stops at `/`. Use `team/**` |
| A scan trigger is refused | The rule grants `read` but not `scan` — the actions are independent |
| Cannot create a retention policy | Repository-wide operations need an image pattern of `**` |
| Rules on `admin` are rejected | Deliberate: a deny there could lock every administrator out. Use a custom role |
