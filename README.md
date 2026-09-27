# card-public-db-bridge

Minimal public GitHub Actions bridge.

- Commands are restricted by an allowlist.
- Required connection values are stored only in GitHub Actions secrets.
- Pull requests do not receive repository secrets.
- Results are not committed to the repository.
- Bridge output is kept only as a short-lived Actions artifact.

See the private project documentation for operational details.
