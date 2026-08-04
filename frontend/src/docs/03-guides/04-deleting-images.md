# Deleting images

## Deleting a tag

In **Browse Files → Images**, expand an image and use the delete action on a tag. You are asked to confirm; deletion is not undoable.

Deleting a tag removes the manifest and the tag reference. Layers shared with other tags are untouched.

## The part that surprises everyone

**Deleting does not free disk space immediately.**

Nexus removes the component right away, but the underlying blobs stay allocated until its **Compact blob store** task runs. Storage will look completely unchanged after a successful delete, and this is the single most common "the delete did not work" report.

It did work. The space is reclaimed asynchronously.

Run the task from **Task Manager**, where you can trigger it directly and watch its state. Nexus does not create a compact task by default — if you do not have one, create it in Nexus under **Administration → System → Tasks**, then run it from here.

## Bulk deletion

For anything beyond a handful of tags, use [retention policies](/docs/retention-policies) instead. They express intent ("keep the last 10 tags", "delete anything older than 90 days") and preview what they would remove before doing it.

## Permissions

Deleting requires `repositories:write` **and** an access rule granting the `delete` action on the image. Reading and deleting are separate grants, so a read-only rule is not enough — see [the permission model](/docs/permission-model).
