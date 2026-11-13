# Ibnsina Stock Tracker

A daily buy / sell / hold tracker for two Egyptian Exchange stocks: Ibnsina Pharma (ISPH) and Act Financial (ACTF).

- `index.html` is the web app. It loads `data-isph.json` and `data-actf.json` and works in any browser. Positions you enter are kept in your own browser and never uploaded.
- `engine.py` fetches prices (Yahoo Finance, StockAnalysis) and headlines (Google News), scores nine checks and writes one data file per stock. Python standard library only.
- `.github/workflows/update.yml` runs the engine every trading day after the Cairo close and commits the new data files.

Run it yourself:

```bash
cp data-actf.json prev.json && python3 engine.py --symbol ACTF --prev prev.json --out data-actf.json
```

This is a research aid, not investment advice.
