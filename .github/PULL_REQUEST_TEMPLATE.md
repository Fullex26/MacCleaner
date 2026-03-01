## Summary

What does this PR change and why?

## Type of change

- [ ] Bug fix
- [ ] New cleanup target
- [ ] New feature or flag
- [ ] Refactor (no behavior change)
- [ ] Documentation
- [ ] CI / tooling

## Testing checklist

### CLI (`cleaner.py`)
- [ ] `python3 -m py_compile cleaner.py` passes
- [ ] `python3 cleaner.py --preview` runs without crashing
- [ ] `python3 cleaner.py --json` produces valid JSON (`| python3 -m json.tool`)
- [ ] `python3 cleaner.py --clean` runs interactively and prompts correctly
- [ ] If a new cleanup target was added: tested deletion and size reporting on a real machine

### Menu Bar App (`AppDelegate.swift`)
- [ ] Not applicable (no Swift changes)
- [ ] Built and launched successfully in Xcode
- [ ] Menu bar icon reflects correct reclaimable size
- [ ] `--json` output changes verified against `CleanerReport` / `CleanerTarget` Swift structs

### JSON contract
- [ ] Not applicable (no changes to `--json` output shape)
- [ ] Field names match Swift `Codable` struct property names

## CHANGELOG

Add a one-line entry for the next release:

- `[category]` Description of change
