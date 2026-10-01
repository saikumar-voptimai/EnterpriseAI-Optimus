# Jetson Orin Nano 8 GB: internal installation

The confirmed target is NVIDIA Jetson **Orin Nano 8 GB**, one enterprise per installation. OpenRouter and hosted speech perform remote inference. The application uses CPU containers and does not require CUDA, TensorRT, NVIDIA Container Toolkit, or `--gpus`.

## 1. Prepare the host

Use a supported JetPack/Ubuntu installation for the exact Orin module. Confirm `uname -m` reports `aarch64`. Prefer SSD storage for the database and containers, reliable power, and an independent backup destination. There is no unmeasured simultaneous-user capacity promise: benchmark the pilot workload before expanding.

Install Docker Engine, the Compose plugin and Python 3 using your organization's supported package process. Follow [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/) if Docker is not installed. Do not replace a runtime used by another Jetson application without checking its dependencies.

```bash
docker version
docker compose version
python3 --version
```

The person launching the application must be permitted to use Docker. Membership of the Docker group grants broad host control; use a designated maintainer account.

## 2. Configure Tailscale

Install Tailscale using its [Linux instructions](https://tailscale.com/kb/1031/install-linux), join the approved enterprise tailnet, enable MagicDNS and HTTPS, and configure the intended testers' access. The hostname must be the device's full `name.tailnet.ts.net` DNS name.

```bash
tailscale status
```

Tailscale runs on the host. **Serve** supplies private HTTPS; **Funnel is not enabled**. Provider webhooks cannot call this private address, so background calendar/artifact integrations use outbound requests. Email and Teams notification links open only for users with tailnet connectivity.

The helper expects permission to configure Serve. If your tailnet uses an operator account, configure that through the Tailscale administrator. Existing Serve destinations are checked before reuse.

## 3. Launch

Unpack the repository to a persistent directory on the SSD, then:

```bash
./scripts/start.sh --tailscale
```

Supply the OpenRouter key at the hidden prompt. Copy the first-run token printed in the terminal, visit the printed HTTPS URL, and create the administrator account. The token is not a permanent login: setup closes after the first user exists. There is no default administrator password.

The script generates `.env` with mode 0600. The database password and credential encryption key are generated once and retained. Keep a protected copy separately from routine data backups. Do not rotate the encryption key by editing it: existing encrypted credentials would become unreadable without a migration.

Docker builds Svelte and installs Python dependencies, then starts PostgreSQL/pgvector. Alembic migration completion gates API and worker startup. Scheduler, executor, connectors and meeting extraction are independent processes. A successfully completed `migrate` container is expected to be exited.

## 4. Connect providers

OpenRouter alone enables the AI core. Voice needs a speech-provider key. Outlook requires Microsoft application registration and OAuth. InfluxDB needs its own read-only token and bucket selection. These are separate services and separate access grants. See [operations](OPERATIONS.md) and the Connections page.

For Microsoft, register the exact callback URL displayed by the application, including the private HTTPS origin and path. Complete OAuth on a phone with Tailscale connected. Calendar permission and Teams transcript permission are independent; a calendar invitation does not guarantee an accessible transcript.

## 5. Acceptance checks on the actual device

1. A permitted phone on mobile data can reach the HTTPS app with Tailscale, sign in, and use the microphone after browser permission.
2. A user outside the allowed tailnet cannot reach the app. A signed-in employee cannot access a workspace without its permissions.
3. Create a brief, upload a document, ask a grounded question, and inspect source references and run progress.
4. Create a recurring analytical job, approve it as manager, and verify due work and failure states.
5. Connect the pilot Outlook account and import the expected calendar occurrences. Verify timezone and cancellation changes.
6. Disconnect internet temporarily: notes remain usable, cloud operations show failure/retry state, and recovery does not duplicate accepted actions.
7. Reboot the Jetson and confirm Docker, Tailscale Serve, database, API, and workers recover.
8. Complete the backup and restore drill into a **different Compose project** before accepting operational data.

Inspect memory, disk growth, queue delay, provider usage, and application logs during the engineer/plant-head/executive demonstration. Start with one executor; increasing workers is a measured deployment choice, not an automatic substitute for database and provider capacity.

## Optional managed LAN HTTPS

The existing `compose.tls.yaml` overlay supplies Caddy with an internal CA for a managed LAN. It is not required for Tailscale Serve. If selected, set `COMPOSE_FILE=compose.yaml:compose.tls.yaml`, the real `APP_HOSTNAME`, matching HTTPS `APP_ORIGIN`, and `SESSION_COOKIE_SECURE=true`. Every client must trust the internal CA. Do not run Caddy and Tailscale on the same host port without deliberately choosing their listeners.

## References

- [NVIDIA JetPack installation](https://docs.nvidia.com/jetson/jetpack/install-setup/index.html)
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale HTTPS](https://tailscale.com/docs/how-to/set-up-https-certificates)
- [pgvector official Docker build](https://github.com/pgvector/pgvector/blob/master/Makefile)

The pinned pgvector image is published for ARM64 and AMD64. This repository has a multi-architecture CI build definition; a physical Orin installation and that remote build must still be performed in your environment.
