# DVC Setup

This project uses DVC for selected input datasets and generated result folders.
Payloads are stored in a private Google Drive remote named `gdrive`; DVC metadata
is committed to Git. Canonical calibration exports remain ordinary Git files.
Run these commands from the project root, `article-alignment-classifier/`.

## What Is Tracked

DVC tracks:

- `data/processed/abstracts_cleaned.csv`;
- selected run folders in `data/results/affinity_benchmark/`, each with its own
  `.dvc` manifest;
- `data/results/ensemble_weight_optimizer/`.

See `data/data-registry.yaml` for the complete target list. Other local
benchmark runs are intentionally outside DVC. The historical
`data/processed/affinity_calibration/to delete/` subtree is ignored by Git and
is not DVC-tracked.

## Install DVC

Use an isolated Python environment, such as the project virtual environment
or `pipx`. With your virtual environment activated, install DVC with Google
Drive support and the compatible dependencies:

```bash
python -m pip install "dvc[gdrive]"
python -m pip install "PyDrive2==1.21.2" "pyOpenSSL==24.2.1" "cryptography==43.0.3" "asyncssh==2.23.1"
python -m pip check
dvc version
```

If using `pipx` instead, install `dvc[gdrive]` with `pipx install` and inject
the four pinned dependencies above with `pipx inject dvc`.

## Configure Your Profile

Use your own Google Drive OAuth client credentials and a profile name unique
to your local setup. Replace the placeholders below; do not commit or share
your client secret or personal authorization token.

```bash
dvc remote modify --local gdrive gdrive_client_id "YOUR_CLIENT_ID"
dvc remote modify --local gdrive gdrive_client_secret "YOUR_CLIENT_SECRET"
dvc remote modify --local gdrive profile article_alignment_profile
```

After authentication, authorization credentials are stored in a JSON file
named after your profile:

- Linux: `~/.cache/pydrive2fs/<your-client-id>/article_alignment_profile.json`
- macOS: `~/Library/Caches/pydrive2fs/<your-client-id>/article_alignment_profile.json`
- Windows: `%LOCALAPPDATA%\pydrive2fs\<your-client-id>\article_alignment_profile.json`

Keep this file private. An explicit `gdrive_user_credentials_file` setting
overrides these default locations.

These settings are local to your checkout. `.dvc/config.local` is ignored by
`.dvc/.gitignore`. The remote URL and `core.remote` are already configured by
the repository; keep the existing Google Drive destination:

```text
remote.gdrive.url=gdrive://1_ak4jZMU9t6bQzpcz61xsRzAdX0xgo8h
core.remote=gdrive
```

Inspect the effective configuration locally:

```bash
dvc config -l
```

The output includes your local credentials; do not share it without redacting
secrets.

## Push and Pull

The committed `.dvc/config` already sets `gdrive` as the default remote;
no `-r` option is needed.

On a fresh checkout, complete **Configure Your Profile** first. Git supplies
the `.dvc` manifests; DVC transfers the datasets:

```bash
git pull
dvc pull       # Download tracked datasets
dvc push       # Upload tracked datasets
dvc status -c  # Compare local data with the remote
```

The first remote operation that requires authentication will print an OAuth
URL. Open it manually if the terminal cannot launch a browser, then sign in
with your Google account and authorize access.

If datasets are missing despite an "up to date" message, check that the
checkout includes their committed `.dvc` manifests.

## Updating a Result Target

After changing a tracked result folder, refresh its individual manifest, commit
only metadata, then push:

```bash
dvc add data/results/affinity_benchmark/RUN_ID
git add data/results/affinity_benchmark/RUN_ID.dvc data/results/affinity_benchmark/.gitignore
git commit -m "Update benchmark run RUN_ID"
dvc push
```

Do not run `git add data/results/affinity_benchmark/RUN_ID/`; the actual result
payload is DVC-managed.

## Compatibility Issue

If a remote operation fails with `module 'lib' has no attribute 'GEN_EMAIL'`,
the Python OpenSSL packages are incompatible. Reinstall the compatible package
set from **Install DVC** in the environment where DVC is installed.

Then check the environment and remote:

```bash
python -m pip check
dvc status -c
```
