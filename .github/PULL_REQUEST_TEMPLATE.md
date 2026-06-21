## What

<!-- One-paragraph description of the change. Link to any relevant issue. -->

## Why

<!-- Business or engineering motivation. What problem does this solve? -->

## Testing

- [ ] `python -m pytest tests/ -v` passes
- [ ] `cd dart_ingestion && dart test --reporter expanded` passes
- [ ] `black --check scripts/ && flake8 scripts/` clean
- [ ] For dbt changes: `dbt run --select <model> && dbt test --select <model>`

## Checklist

- [ ] No hardcoded credentials or API keys
- [ ] New adapter? Coordinate system documented in `UnifiedEvent.coordSystem`
- [ ] New dbt model? `schema.yml` tests added
- [ ] CHANGELOG.md updated if this is a user-visible change
