# Ibnsina Stock Tracker

A daily buy / sell / hold tracker for Ibnsina Pharma (EGX: ISPH).

- `index.html` is the web app. It loads `data.json` and works in any browser.
- `engine.py` fetches prices (Yahoo Finance) and headlines (Google News), scores nine checks and writes `data.json`. Python standard library only.
- `.github/workflows/update.yml` runs the engine every trading day after the Cairo close and commits the new `data.json`.

Run it yourself:

```bash
cp data.json prev.json && python3 engine.py --prev prev.json --out data.json
```

This is a research aid, not investment advice.
