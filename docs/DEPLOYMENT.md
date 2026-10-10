# Private desktop deployment (issue #8)

The worker prepares files and local evidence only. The owning controller reviews
the exact candidate, installs/activates the user service and adds the dedicated
Serve route. Preparation is not deployment acceptance. Keep issue #8 open until
service restart and actual HTTPS frontend, API, generation and downloads pass.

## Prepare without activation

Use a stable deployment checkout on a local filesystem; moving/removing a
worktree breaks the unit's absolute paths. Use the existing uv executable (its
absolute path when absent from PATH), Python 3.11 or 3.12 and system
FFmpeg/ffprobe. No duplicate uv or GPU runtime is needed. Set `UV` privately to
that executable and work from the deployment checkout:

```sh
umask 077
mkdir -p .runtime .evidence/issue-8
export UV_CACHE_DIR="$PWD/.runtime/uv-cache" UV_PYTHON_DOWNLOADS=never
"$UV" sync --locked --extra dev --extra tts --python 3.11
"$UV" run --extra dev pytest -q
# Restore TTS packages after a dev-only command, which can remove them.
"$UV" sync --locked --extra dev --extra tts --python 3.11
```

Reuse verified model files/provenance as a task-owned private copy in
`$DATA_DIR/models`; never modify retained evidence or download weights again.
For a first installation without verified files, use the explicit README model
setup. Privately set these task-specific shell variables:

- `DATA_DIR`: absolute ignored `.runtime` path or private local storage outside
  Git. Existing directories must be mode 0700. Back up before reusing live data.
- `PRIVATE_HOST`: approved exact HTTPS hostname, without scheme/port/path.
- `LOCAL_PORT`: unused application port (default 8000), checked with `ss -ltnp`.
- `HTTPS_PORT`: separate unused Serve TCP port, selected from a fresh snapshot
  below. No example port is presumed free.

```sh
.venv/bin/python scripts/prepare_service.py --data-dir "$DATA_DIR" \
  --hostname "$PRIVATE_HOST" --port "$LOCAL_PORT"
systemd-analyze --user verify "$DATA_DIR/deployment/chaptercast.service"
```

Review the generated unit and environment file privately. Preparation creates
private uploads, models, audio, logs and deployment directories; it does not
initialise a database, install/start a unit or contact Tailscale. It refuses an
existing bundle. To revise settings, stop first, retain the old bundle privately
and prepare a new one. Never commit generated files.

The unit runs the installed `.venv/bin/python` directly with one server/worker,
private umask, bounded failure restarts and graceful shutdown before killing
remaining children. It does not synchronise dependencies at startup. Logs append
to `$DATA_DIR/logs/service.log`; stderr shares that file. Rotate/archive the log
while stopped. Models run offline with at most four CPU threads. Forwarded
headers are trusted only from the IPv4 loopback proxy; never widen that trust
to `*`. No system trust, global configuration or hardware changes are needed.

## Controller: install and qualify the service

Confirm dependency #7 and the exact final source identity/evidence. Inspect
`systemctl --user cat chaptercast.service` and any existing destination; do not
replace another installed/running service. For a new installation:

```sh
install -d -m 700 "$HOME/.config/systemd/user"
install -m 600 "$DATA_DIR/deployment/chaptercast.service" \
  "$HOME/.config/systemd/user/chaptercast.service"
systemctl --user daemon-reload
systemctl --user enable --now chaptercast.service
systemctl --user status chaptercast.service --no-pager
curl --fail "http://127.0.0.1:$LOCAL_PORT/health"
curl --fail "http://127.0.0.1:$LOCAL_PORT/api/jobs/worker"
ss -ltnp
systemctl --user restart chaptercast.service
curl --fail --retry 10 --retry-connrefused --retry-delay 1 \
  "http://127.0.0.1:$LOCAL_PORT/health"
curl --fail "http://127.0.0.1:$LOCAL_PORT/api/jobs/worker"
```

Require health `ok`, `backend_ready=true`, `worker_owned=true`, a changed process
identity after restart, preserved library/audio and a listener at `127.0.0.1`
only. `Type=simple` active state is not readiness. Inspect the private log for
failures. Fix the cause of a restart-limit failure before
`systemctl --user reset-failed chaptercast.service` and one restart.

A user service normally starts with the user's session. Check
`loginctl show-user "$USER" -p Linger`; unattended boot/logout needs
controller-authorised lingering. If needed, the controller can use
`loginctl enable-linger "$USER"` and verify after logout/reboot. Preparation
does not change user lifecycle policy. Record whether boot/logout was actually
tested. Desktop sleep/power-off still stops access and generation.

## Controller: add one private HTTPS port

Use [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) and the
[Serve CLI reference](https://tailscale.com/docs/reference/tailscale-cli/serve).
Serve is private to the tailnet; Funnel publishes to the internet. There is no
app login: verify tailnet grants/ACLs limit access to the intended user/devices
and selected port. Resolve HTTPS/MagicDNS/policy prerequisites as controller
before proceeding. Do not request credentials in chat or inspect secret stores.

Save state privately and recheck immediately before the single addition. The
checker inspects node TCP/Web/Funnel entries and foreground sessions. Also
inspect `ss -ltnp` and the full snapshots for service definitions/other port use.
Do not replace routes, use a shared path prefix, reset Serve, import replacement
config or enable Funnel.

```sh
tailscale serve status --json > .evidence/issue-8/serve-before.json
tailscale funnel status --json > .evidence/issue-8/funnel-before.json
.venv/bin/python scripts/check_serve_config.py \
  --before .evidence/issue-8/serve-before.json --https-port "$HTTPS_PORT"
# Owning controller only, after review of fresh state:
tailscale serve --bg --https="$HTTPS_PORT" "http://127.0.0.1:$LOCAL_PORT"
tailscale serve status --json > .evidence/issue-8/serve-after.json
tailscale funnel status --json > .evidence/issue-8/funnel-after.json
.venv/bin/python scripts/check_serve_config.py \
  --before .evidence/issue-8/serve-before.json \
  --after .evidence/issue-8/serve-after.json --hostname "$PRIVATE_HOST" \
  --https-port "$HTTPS_PORT" --local-port "$LOCAL_PORT"
```

Require precisely the new HTTPS TCP listener and root proxy. All other routes,
handlers and Funnel flags must match before; the new port must have no Funnel
enablement. Compare the separate Funnel snapshots too. Unknown schema/changes
need private review, never a weakened check or reset. Recheck the app's loopback
listener and the pre-existing services. HTTPS enablement prompts/errors are
prerequisites, not acceptance.

## Controller: actual HTTPS and real generation

Set `PRIVATE_URL="https://$PRIVATE_HOST:$HTTPS_PORT"` privately. Use the real
trusted certificate/DNS: no `curl -k`, global TLS bypass or system trust change.
From a permitted tailnet device:

```sh
curl --fail "$PRIVATE_URL/" -o .evidence/issue-8/https-shell.html
curl --fail "$PRIVATE_URL/health"
curl --fail "$PRIVATE_URL/api/library"
curl --fail "$PRIVATE_URL/api/jobs/worker"
```

Open that HTTPS URL in native T3 preview and on the phone. Verify the frontend
and same-origin browser POSTs: paste/upload a clearly labelled original chapter,
generate with `bf_emma` and both formats, wait for **completed**, then play,
seek, change rate and reload for resume. A `cross_origin` failure needs
Host/scheme/proxy inspection without relaxing guards. A healthy API or cached
retained chapter is not fresh generation proof. Record original input hash and
length, ordered chunk coverage, job/output IDs, elapsed time, model provenance
and actual completion. Retained original issue #4 audio may be **reused** for
additional player checks with unchanged-source verification.

For the completed `JOB_ID`, save full HTTPS downloads, response headers, HTTP
results, content lengths, hashes and ffprobe duration/size/codec privately.
Check the byte range returns 206 and matches the expected file slice:

```sh
curl --fail -D .evidence/issue-8/download-m4a.headers \
  "$PRIVATE_URL/api/jobs/$JOB_ID/outputs/m4a" -o .evidence/issue-8/downloaded.m4a
curl --fail -D .evidence/issue-8/download-opus.headers \
  "$PRIVATE_URL/api/jobs/$JOB_ID/outputs/opus" -o .evidence/issue-8/downloaded.opus
curl --fail -H 'Range: bytes=1000-1999' -D .evidence/issue-8/range.headers \
  "$PRIVATE_URL/api/jobs/$JOB_ID/outputs/m4a" -o .evidence/issue-8/range.bin
systemctl --user stop chaptercast.service
ffprobe -v error -show_format -show_streams .evidence/issue-8/downloaded.m4a
ffprobe -v error -show_format -show_streams .evidence/issue-8/downloaded.opus
ffmpeg -v error -xerror -nostdin -i .evidence/issue-8/downloaded.m4a -f null -
ffmpeg -v error -xerror -nostdin -i .evidence/issue-8/downloaded.opus -f null -
systemctl --user start chaptercast.service
```

Verify health/readiness after start. Full-file decode after shutdown proves saved
files are independent; buffered streaming does not. Record actual argv/exits
and failures. Local HTTP, proxy simulation and fixtures do not establish deployed
HTTPS acceptance.

## Phone onboarding and manual checks

Connect the intended phone to the approved tailnet and open the exact private
HTTPS URL including port. Use **Install app** / **Add to Home screen** if offered.
Keep hostnames, phone IDs, screenshots and authentication details private. The
shell caches its interface only; library, imports, queue, streaming and new
downloads need the desktop/tailnet. Wait for complete **Download M4A** or
**Download OPUS**, then open it from Downloads in a compatible external player.

Record phone/browser versions privately. Check installation/launch, narrow
controls, both formats, seeking, rate/resume after reload, lock-screen Media
Session controls and physical screen-off continuity. Disconnect from the
tailnet/server and play the complete saved file externally. Check offline UI
limits and absence of mutation replay. Viewport/manifest tests prove no physical
phone behaviour. Mark unperformed checks `MANUAL_UNRUN`. The controller decides
acceptance and never closes while core service/HTTPS/generation is blocked.

## Stop, rollback, update and backup

Use `systemctl --user stop/start/restart chaptercast.service`. For updates, stop,
back up the whole private data directory, update reviewed source, synchronise
locked TTS dependencies and start/qualify again. Do not run dev-only uv sync
while active. Changed paths/settings need a reviewed replacement bundle and
daemon-reload. Restore SQLite, uploads, audio and verified models/provenance
together while stopped. See [operations](OPERATIONS.md) for recovery and quotas.

To withdraw only this route, the controller runs
`tailscale serve --https="$HTTPS_PORT" off`, saves state and verifies it matches
the before snapshot. Never use `serve reset` or unqualified off. Disable only
this service with `systemctl --user disable --now chaptercast.service`; retain
private data. Remove only its installed unit if desired and daemon-reload. Do
not disable lingering when other user services need it. Rollback does not
authorise another route change, library deletion or a false acceptance claim.
