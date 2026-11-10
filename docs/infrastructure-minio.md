# MinIO / Object Storage — Local Setup

## Problem (Phase 1)

Pulls of the historic Hub image failed on this machine:

```text
docker pull minio/minio:latest
# Error: pull access denied for minio/minio, repository does not exist or may require 'docker login'

docker pull quay.io/minio/minio:<release>
# Error: 401 UNAUTHORIZED
```

Root cause: MinIO community edition moved to **source-only** distribution; the classic `minio/minio` prebuilt image is no longer reliably available on Docker Hub / Quay for anonymous pulls.

## Verified working solution

Use the CleanStart hardened MinIO image, **pinned by digest** (not `:latest`):

```text
cleanstart/minio@sha256:3377f7b92aabe2bdca25d26c3b37fe7d3fdbf5057e461bbc87af34ebd6252aaf
```

Verification performed locally:

1. `docker pull cleanstart/minio@sha256:3377f7b92aabe2bdca25d26c3b37fe7d3fdbf5057e461bbc87af34ebd6252aaf` succeeded.
2. Image entrypoint is `/usr/bin/minio`; default user is non-root (`65532`).
3. Server starts successfully with `--user 0` writing to a writable data path (`/data` volume owned appropriately, or `/tmp/data` for smoke tests).
4. API listens on `:9000` and reports MinIO Object Storage Server ready.

Compose wiring (`docker-compose.yml`, profile `storage`):

```yaml
minio:
  profiles: ["storage"]
  image: cleanstart/minio@sha256:3377f7b92aabe2bdca25d26c3b37fe7d3fdbf5057e461bbc87af34ebd6252aaf
  user: "0"   # image USER 65532 cannot write an empty named volume without chown
  command: server /data --console-address ":9001"
  healthcheck:
    test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:9000/minio/health/live"]
```

Start:

```bash
docker compose --profile storage up -d minio
# Expect: wget healthcheck → healthy; GET :9000/minio/health/live → 200
```

Verified on this workstation: container starts, API `200` on `/minio/health/live`, compose health reaches `healthy` after wget-based check.

## Alternatives (not selected for Phase 2)

| Option | Status |
|--------|--------|
| Build MinIO from official source Dockerfile | Valid long-term; not required once CleanStart digest verified |
| Chainguard / other commercial hardened images | May require registry auth |
| Unpinned `:latest` tags | Rejected — not reproducible |

## Evidence bucket

Application code creates/uses bucket `kenwer-c3-evidence` (see `.env.example`). Bucket auto-creation lands with the storage client in a later phase; for local MinIO console, create the bucket manually if needed.
