# Kiwoom Auto Trader

Buildable Windows desktop MVP for an automated stock-trading workflow.

This package is intentionally shipped in mock mode. It does not connect to
Kiwoom OpenAPI, fast-order tools, or any live brokerage account. The live
adapter is a placeholder so the application fails closed until a real broker
integration is reviewed and implemented.

## What Is Included

- Tkinter desktop dashboard
- Strategy engine with CCI calculation and bullish-pattern transition logic
- Risk checks for max capital per symbol
- Mock broker, order manager, and sell retry flow
- SQLite order/log storage
- Unit tests
- Windows EXE build script
- GitHub Actions workflow for Windows EXE artifacts

## Local Run

```bat
python -m kiwoom_auto_trader.main
```

## Local EXE Build

```bat
scripts\build_windows_exe.bat
```

The executable is generated at:

```text
dist\KiwoomAutoTrader.exe
```

## GitHub Actions

Push this repository to GitHub, then run the `Build Windows EXE` workflow from
the Actions tab. The workflow uploads `KiwoomAutoTrader-windows-exe` as an
artifact.

## Live Trading Warning

Live order execution must not be enabled until the `BrokerClient` adapter is
implemented, tested in simulation, reviewed against Kiwoom's current API
requirements, and verified with explicit account-level risk controls.
