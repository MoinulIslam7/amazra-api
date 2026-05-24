# Contributing

## Branching strategy

- `main` — production-ready, protected
- `develop` — integration branch for ongoing work
- `feature/*` — feature branches cut from `develop`

## Workflow

1. Create a branch from `develop`: `git checkout -b feature/your-feature`
2. Commit with clear messages (Conventional Commits preferred)
3. Open a PR targeting `develop`
4. Merge to `main` only after QA and release sign-off
