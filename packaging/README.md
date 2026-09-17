# Fedora packaging (COPR)

`display-tune.spec` installs Display Tune system-wide (`/usr/...`) instead of per-user,
for anyone on the machine, via `dnf`. It was built and verified locally with plain
`rpmbuild` against the real `v1.0.0` GitHub release tarball before these instructions
were written — the file list, the systemd user-preset, and `appstream-util
validate-relax` all pass.

The spec's `Source0` is a normal URL (the GitHub tag tarball), so COPR can build it
directly from this repository with its default **SCM** build type — no extra `Makefile`
needed.

## One-time setup (needs your Fedora Account System login)

1. Sign in at <https://copr.fedorainfracloud.org/> with your Fedora account.
2. **New Project** → name it (e.g. `display-tune`) → pick the chroots you want
   (e.g. `fedora-44-x86_64`, `fedora-rawhide-x86_64`) → Create.
3. Inside the project: **Builds → New Build → SCM**.
   - Clone URL: `https://github.com/Ahmad-Alqattu/display-tune.git`
   - Committish: `v1.0.0` (a release tag) — or `main` to always build the latest commit
   - Subdirectory: leave empty
   - Spec File: `packaging/display-tune.spec`
   - Build method: `rpkg` (the default)
4. Submit. COPR fetches the repo, downloads `Source0` from GitHub itself, and builds.

## For every new release

Push a new tag (e.g. `v1.1.0`), bump `Version:`/add a `%changelog` entry in the spec,
and either start a new COPR build pointed at that tag, or point the SCM committish at
`main` once so every push rebuilds automatically (COPR supports a GitHub webhook for
that, under the project's **Integrations** tab).

## Once it's built, users install with

```sh
sudo dnf copr enable <your-fedora-username>/display-tune
sudo dnf install display-tune
```

Log out and back in once after installing (or after an update that touches the shell
extensions) so GNOME Shell picks them up — same as with `install.sh`.
