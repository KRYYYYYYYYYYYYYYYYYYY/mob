# proxy_checker

A Python + Go tool for discovering, validating, and managing VLESS proxy servers using the REALITY security protocol.

## Architecture

- **Language**: Python 3.12 (orchestration), Go 1.21 (high-performance validation)
- **Go shared library**: `libchecker.so` — built from `checker.go` via `go build -buildmode=c-shared`
- **Python packages**: `requests`, `psutil` (managed via uv in `.pythonlibs`)

## Project Layout

- `checker.go` — Go source for the L7 validation shared library
- `check.py` — Main entry point: downloads, filters, and checks proxy links
- `torture_bot.py` — Stress tests high-ranking proxies for "Elite" status
- `control_bot.py` — Manages proxy lists via GitHub Issues
- `test1/` — State and data files (ranking.json, status.json, blacklist.txt, etc.)
- `kr/mob/wifi.txt` — Final output of verified working proxies

## Workflow

- **Start application**: `python3 check.py` (console output)

## Build

To rebuild the Go shared library after modifying `checker.go`:
```
go build -o libchecker.so -buildmode=c-shared checker.go
```

## Data Flow

1. `check.py` pulls VLESS links from external URLs
2. Proxies are validated via `libchecker.so` (Go-based L7 HTTP probing)
3. Performance data stored in `test1/ranking.json`
4. High-performing proxies go through `torture_bot.py`
5. Final curated list written to `kr/mob/wifi.txt`
