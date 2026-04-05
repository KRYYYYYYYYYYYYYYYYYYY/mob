# Variant 3 implementation recap (P1 + P2 + P3)

This report snapshots what is implemented after the "Variant 3" request, where to find it in code, and what checks were re-run before creating the refresh PR.

## 1) Unified stress profile for all checkers

Implemented:
- Shared Python loader with fallback order: `test1/stress_profile.json` -> `test1/stress_profile.example.json`.
- Go-side runtime loader for reserve checker.
- Both main checker and reserve checker read runtime knobs from the same stress profile family.

Code:
- `stress_profile_loader.py`
- `new_check/stress_config.go`
- `check.py`
- `torture_bot.py`
- `new_check/main.go`

## 2) Web server removed from reserve runtime path

Implemented:
- Reserve checker runtime path executes scan directly; web server startup is no longer part of the default execution path.

Code:
- `new_check/main.go`

## 3) Accuracy hardening in L7 probing

Implemented:
- End-to-end probes require stronger evidence than a single 204 check.
- IP-echo confirmation (`ipify`) is integrated into probe success criteria to reduce false positives.
- Reserve checker mirrors strict checks.

Code:
- `checker.go`
- `new_check/check.go`
- `new_check/config.go`

## 4) Diagnostics and failure reason taxonomy

Implemented:
- Unified reason families and JSON diagnostics outputs for investigation.
- Stage-style diagnostic fields for connectivity pipeline states.

Code:
- `check.py` writes `test1/diagnostics.json` and `test1/diagnostics_v2.json`.
- `new_check/rescan.go` writes `new_check/result/diagnostics.json`.

Reason labels in use include:
- `tcp-open-but-l7-block`
- `probable-provider-dpi`
- `uuid-rejected`
- `wl-deny`
- `ip-block`

## 5) Server-side signal verification (UUID activity path)

Implemented:
- Optional server-side verification module.
- Primary path via `v2ray_python.rpc.V2RayClient`.
- Fallback presence checks (SQLite-oriented) for constrained environments.
- Feature is controlled by stress profile keys and remains opt-in (`server_stats_enabled=false` by default).

Code:
- `xray_stats_client.py`
- `check.py`
- `test1/stress_profile.json`
- `test1/stress_profile.example.json`

## 6) CI/dependency alignment

Implemented:
- Workflows include `v2ray-python` installation where needed so `V2RayClient` path can be used in CI when enabled.
- Reserve checker invocation preserves profile-driven runtime tuning.

Code:
- `.github/workflows/main.yml`
- `.github/workflows/checker.yml`
- `.github/workflows/check.yml`

## 7) Re-validation commands executed for this refresh

- `python -m py_compile check.py torture_bot.py stress_profile_loader.py xray_stats_client.py mobile_vless_checker.py`
- `go build -o /tmp/libchecker.so -buildmode=c-shared checker.go`
- `go build -o /tmp/crazy_checker .` (in `new_check/`)

All commands completed successfully in this environment.
