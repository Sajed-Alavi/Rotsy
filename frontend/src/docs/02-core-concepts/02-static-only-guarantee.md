# The static-only guarantee

Nothing in this system starts a container. This is worth stating precisely, because "we scan your images" usually means "we run them".

## Why it matters

A scanner that pulls an image into a local daemon has to *have* a local daemon, and the container it creates is built from content you have not yet vetted. That is a meaningful attack surface for a security tool: the thing you are inspecting gets to execute before you have finished inspecting it.

Rotsy reads images as data over the Docker Registry v2 API. Manifests and layers are fetched, unpacked and analysed. Nothing is ever executed.

## Four independent enforcement points

| Enforcement | Where |
|---|---|
| Trivy runs with `--image-src remote` | `services/scanning/trivy.py` |
| Grype gets an explicit `registry:` reference and `GRYPE_DEFAULT_IMAGE_PULL_SOURCE=registry` | `services/scanning/grype.py` |
| `_assert_static_ref()` rejects `docker:`, `podman:`, `containerd:`, `dir:` and friends | `services/scanning/base.py` |
| No Docker socket is mounted; the image has no Docker client | `docker-compose.yml`, `backend/Dockerfile` |

They are independent on purpose. Any one of them failing still leaves three.

## The Grype default is the interesting one

Left to itself, Grype tries the local Docker, Podman and containerd daemons *before* the registry. The explicit `registry:` scheme plus the environment variable is what keeps it off them. Without that, a working deployment could quietly start using a daemon if one ever appeared in the image.

## What this costs you

Very little, but be aware of it: the scanners see what the registry serves. If an image is only in a local daemon and was never pushed, Rotsy cannot scan it — correctly, since it is not in your registry.
