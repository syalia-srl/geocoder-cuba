# Deploying the geocoder to a machine with no internet

*Reach for this when the geocoder has to run on a Linux box that has never been
online and never will be — a client VM, a ministry machine, a demo laptop behind
a firewall. Everything arrives on a USB drive; the target downloads nothing.*

Distinct from a hosted deploy with a public URL.

The general pattern (shared with other offline projects) lives at
`vault/Atlas/Architecture/2026-07-23-airgapped-appliance-pattern.md`. This doc is
the geocoder's instance of it.

## Which producer

| | primary — Docker | secondary — no engine |
|---|---|---|
| build | `deploy/build-airgapped.sh` | `bin/build-app-tarball.sh` |
| output | `geocoder-docker.tar.gz` (~260 MB) | `dist/geocoder-YYYY-MM-DD.tar.gz` (~215 MB) |
| target needs | `docker` + `docker compose` v2 | **Python 3.12 at `/usr/bin/python3.12`** |
| gate | `deploy/airgap-smoke.sh` | `bin/tarball-smoke.sh` (weaker — see below) |
| use it when | almost always | the target has no container engine (an air-gapped VM) |

**Default to Docker.** The tarball exists because some air-gapped VMs have no engine,
and it carries a real constraint: `uv venv --relocatable` makes paths *inside*
the venv relative, but `.venv/bin/python` remains a symlink to
`/usr/bin/python3.12` — **the interpreter is not bundled**. Ubuntu 24.04 ships
3.12, which is why it works there; a box with 3.11 or 3.13 cannot run it at all.

Both producers consume the same payload from `deploy/bake-data.sh`, so the
tarball cannot ship staler data than the image.

## Build (on a host WITH internet)

```bash
make airgap USB=/run/media/$USER/DATOS   # bundle + Docker-offline + smoke + USB
```

`make help` lists the steps it chains (`airgap-bundle`, `airgap-docker`,
`airgap-smoke`, `usb`); run them one by one to redo a single stage. Underneath:

```bash
deploy/build-airgapped.sh            # primary
bin/build-app-tarball.sh             # secondary, only if the target lacks Docker
```

A target without Docker does not force the tarball path: `make airgap-docker`
builds `dist/docker-offline-<ver>.tar.gz` (static Docker binaries + compose,
checksummed) and `make usb` ships it with `install-docker.sh`, which installs
Docker on any x86_64 glibc Linux with no network. Both scripts are copied from
the sibling projects' release scripts. A fresh Ubuntu 26.04 has neither Docker nor Python
3.12, so this is the only path that works there.

Both call `deploy/bake-data.sh` first, which **verifies rather than rebuilds**:
a full-Cuba ETL is ~2 h, so it checks that the database's build stamp matches
`HEAD`, checkpoints the WAL, and asserts the payload is real.

**If it refuses**, that is the point. The ETL and the query path share
`normalize_street_name`, so a change there rewrites `street_norm` keys; a store
built by other code carries keys this code will never look up — a wrong-answer
bug that looks like a working install. Options:

```bash
deploy/bake-data.sh --rebuild            # ~2 h, full Cuba + routing + tiles
deploy/bake-data.sh --allow-stale-data   # pipeline testing ONLY, never ship this
```

Build on a host with real bandwidth. Measure first (a laptop's uplink varies): the
image pulls a Python base plus osmium/shapely wheels.

## Verify before shipping — not optional

```bash
deploy/airgap-smoke.sh        # ~2 min
bin/tarball-smoke.sh          # runs inside geocoder-build:24.04 if the host lacks 3.12
```

The Docker gate has three stages, and **only the third proves airgap-completeness**:

1. `--network none` — the baked DB, tiles and glyphs are asserted *from inside*
   the image.
2. A scratch install answers over HTTP: a precise urban resolve, the v0.4.0
   attractor guard, a route, a region, `/tiles` Range → 206, a glyph range →
   200, the UI renders.
3. The container log is grepped for `pip install` / `apt-get` / `Downloading` /
   `docker pull`. Any hit fails.

Stages 1–2 can pass on a build host where a missing thing is still cached; stage
3 is what catches that.

**The tarball gate is weaker and says so.** With no container to isolate it uses
`unshare -rn` when available and otherwise runs with a network, so a pass means
"it works", not "it is provably self-contained". It prints which mode it ran in.

## Ship

```bash
deploy/update-usb-drive.sh                        # autodetects one mounted USB
USB=/media/$USER/MIUSB deploy/update-usb-drive.sh # explicit
```

Copies to `<mount>/geocoder/`: the bundle, `install.sh`,
`docker-compose.yml.tmpl`, the recipient's Spanish guide (`README.md`, source at
`deploy/usb/README.md`), and when given, the Docker-offline bundle with its
installer and an example spreadsheet (`EXAMPLE_XLSX=`). It writes **only to a
mounted filesystem** — hand it a raw `/dev/sdX` and it aborts. Never `dd`.

## On the target

```bash
./install.sh                 # asks for a port, default 8080
./install.sh --port 8080     # unattended
```

It checks for `docker compose` v2, `docker load`s the image, renders `.env` +
compose, and brings the stack up with **`--no-build`** — without that flag a tag
mismatch makes compose try to *build*, which needs a network the box does not
have and simply hangs.

The tarball equivalent is `make serve` inside the unpacked directory.

**Update:** drop in a newer bundle and re-run `install.sh`. There is nothing to
preserve — the geocoder is stateless, so no volumes, no migration, and no way
for a bad update to eat data.

## Pitfalls

- **"It says deployed but nothing answers."** First start takes ~90 s on
  full-Cuba data. `/health` responds in seconds (the routing graph warms on a
  background thread), but the first `/route` can block until that warm finishes.
  `install.sh` polls and prints progress precisely so this does not read as a
  broken deployment.
- **Blank map, everything else fine.** `GEOCODER_DATA_DIR` is unset or wrong.
  `config.py` derives the data directory from `__file__`, which only works
  inside a checkout; installed it resolves into site-packages (or into `.venv`
  in the tarball) and `/tiles` 404s silently. The image sets it; the tarball's
  `.env` sets it.
- **Map renders but with no labels / no basemap styling.** A vendored asset did
  not survive packaging. `package-data` must be `api/static/**/*` — the earlier
  `api/static/*` matched only the top level and silently dropped the whole
  `vendor/` tree. `tests/test_packaging.py` guards this.
- **Empty or tiny database at the client.** The WAL was not checkpointed, so the
  data was still in `<db>-wal` while only `<db>` shipped. `bake-data.sh`
  checkpoints and then asserts the size; the `.dockerignore` and the tarball
  builder both refuse to carry `-wal`/`-shm` sidecars.
- **Tarball dies with "No such file or directory" running `.venv/bin/python`.**
  The target has no `/usr/bin/python3.12`. That is the no-engine path's hard
  requirement; use the Docker bundle instead.
