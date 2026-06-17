# Contributing to xForge

## Branch Naming

| Type | Pattern | Example |
|---|---|---|
| New feature | `feat/<short-description>` | `feat/wyscout-adapter` |
| Bug fix | `fix/<short-description>` | `fix/silver-coord-clamp` |
| CI/DevOps | `ci/<short-description>` | `ci/add-push-trigger` |
| Tests | `test/<short-description>` | `test/xg-degenerate-input` |
| Documentation | `docs/<short-description>` | `docs/api-reference` |

## Commit Convention

```
<type>(<scope>): <short description>

feat(dart): add Wyscout adapter
fix(xg): bulk UPDATE for write_xg_values
test(dbt): silver spatial gate assertions
docs(readme): update CI badge
```

Types: `feat`, `fix`, `ci`, `test`, `docs`, `refactor`, `style`

## Pull Request Checklist

- [ ] Tests pass: `python -m pytest tests/ -v`
- [ ] Dart tests pass: `cd dart_ingestion && dart test --reporter expanded`
- [ ] Lint clean: `black --check scripts/ && flake8 scripts/`
- [ ] PR description explains *why*, not just *what*
- [ ] New adapter? Coordinate system documented in `UnifiedEvent.coordSystem`
- [ ] New dbt model? `schema.yml` tests added

## Running Tests Locally

```bash
# Python unit tests (no database required)
python -m pytest tests/ -v

# Dart unit tests (no database required)
cd dart_ingestion
dart pub get
dart test --reporter expanded
```

## Adding a New Data Provider

1. Create `dart_ingestion/lib/adapters/<provider>_adapter.dart`
2. Implement `DataAdapter` interface (`fetchEvents`, `providerName`, `coordSystem`)
3. Add case to `main.dart` switch statement
4. Add the coordinate system to `dbt_project/macros/coord_normalise.sql`
5. Add adapter tests in `dart_ingestion/test/adapters/`

## Local Setup

See [README.md](README.md) — Getting Started section.
