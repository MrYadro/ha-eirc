# AGENTS.md

## Testing

```bash
uv venv --python 3.14 .venv  # once
uv pip install -r requirements_test.txt
uv run pytest -q
```

## Conventions

- Versions are HA-style CalVer (`год.месяц.патч`), synced in `custom_components/eirc_spb/const.py` (`VERSION`) and `custom_components/eirc_spb/manifest.json` (`"version"`); the UA test in `tests/test_api.py` pins the literal.
- **Bump the version only when pushing a release to GitHub** — do not bump on local bugfix commits.
- No comments in code unless asked.
- No PII in committed files: fixtures use fake values (ЕЛС `1000000001`, account id `910000001`); README examples use the fictional `ivan_els_71000000001_*` entities.
- Integration name: «ЕИРЦ Санкт-Петербурга» (manifest, hacs.json, config-flow title, translations, device manufacturer).
