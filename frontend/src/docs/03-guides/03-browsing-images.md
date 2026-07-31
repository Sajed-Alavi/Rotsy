# Browsing images

**Browse Files** has two views, selectable per repository.

## Images view

The default, and the one you usually want. Each image is a folder that expands to its tags, with push time, size and a delete action per tag.

This exists because a Docker repository's raw asset listing is mostly layer blobs:

```
v2/myapp/blobs/sha256:6f2a1c...
v2/myapp/blobs/sha256:9d3e4b...
v2/myapp/manifests/sha256:11ff02...
```

That tells you nothing about which images exist. The Images view resolves components and tags through Nexus's components API instead.

For non-Docker formats, the same view lists components and versions.

## Files view

The raw assets, as an expandable **directory tree** built from their paths rather than a flat list, with a download button per file.

Downloads are proxied through the backend, so your browser never handles Nexus credentials and never needs network access to Nexus itself.

> Asset paths are validated before they reach Nexus. A path attempting to traverse out of its repository prefix is rejected — otherwise a crafted path could reach arbitrary Nexus REST endpoints using the backend's privileged service account.

## Image scoping applies here

If your role is restricted to certain image patterns, both views filter to what you are allowed to see, and the download proxy enforces the same restriction. See [RBAC and image scopes](/docs/rbac-and-image-scopes).
