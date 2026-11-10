# MinIO / Object Storage — Local Development Only

## Scope restriction (mandatory)

The third-party MinIO container image used by this repository is **restricted to local development with synthetic / non-sensitive data**.

- Do **not** store production mission media, operator PII, or real flight recordings in this image.
- Do **not** expose ports `9000`/`9001` beyond the local workstation without an approved production object-store design.
- Channel 3’s authoritative cognitive memory remains **PostgreSQL**, not MinIO. MinIO holds evidence **blob references** only.

## Problem (Phase 1)

Pulls of the historic Hub image failed on this machine:

```text
docker pull minio/minio:latest
# Error: pull access denied for minio/minio, repository does not exist or may require 'docker login'

docker pull quay.io/minio/minio:<release>
# Error: 401 UNAUTHORIZED
```

Root cause: MinIO community edition moved to **source-only** distribution; the classic `minio/minio` prebuilt image is no longer reliably available on Docker Hub / Quay for anonymous pulls.

## Provenance of the selected image

| Field | Value |
|-------|--------|
| Image reference | `cleanstart/minio@sha256:3377f7b92aabe2bdca25d26c3b37fe7d3fdbf5057e461bbc87af34ebd6252aaf` |
| Publisher | CleanStart (Docker Hub Verified Publisher: `cleanstart/minio`) |
| Why chosen | Digest-pinned, pullable on this workstation after official `minio/minio` became unavailable |
| Pinning policy | **Digest only** — never floating `:latest` in Compose |
| Runtime user | Image default UID `65532`; Compose runs `user: "0"` so a fresh named volume is writable |

This is a **community/hardened redistributor image**, not an official MinIO, Inc. binary release. Treat it as a local convenience dependency with third-party supply-chain risk.

## Security limitations

1. **Third-party build**: Kenwer does not control CleanStart’s build pipeline, base OS updates, or SBOM cadence. Re-verify digests before any shared or staging use.
2. **Root volume write**: Compose uses `user: "0"` for local volume bootstrap — acceptable only for disposable local volumes.
3. **Credentials**: `.env.example` uses well-known local keys (`kenwerminio`). Never reuse them outside localhost.
4. **No TLS in Compose**: Local HTTP only. Not suitable for network-shared deployments.
5. **Not authoritative memory**: Loss or compromise of MinIO must not corrupt Persistent / Episodic / Consolidated stores in Postgres.
6. **Synthetic data only**: Fixtures and local demos only; production object storage selection is deferred.

## Verified local behavior

1. `docker pull` of the digest succeeded.
2. Server starts; `GET /minio/health/live` returns `200`.
3. Compose healthcheck uses `wget` (present in the image) and reaches `healthy`.

## Compose

```bash
docker compose --profile storage up -d minio
```

See `docker-compose.yml` profile `storage`.

## Alternatives (not selected)

| Option | Status |
|--------|--------|
| Build MinIO from official source Dockerfile | Valid long-term production path |
| Chainguard / commercial hardened images | May require registry auth |
| Unpinned `:latest` tags | Rejected — not reproducible |

## Evidence bucket

Application expects bucket `kenwer-c3-evidence` (see `.env.example`). Auto-creation lands with the storage client in a later phase.
