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

## Улучшения из crazy_xray_checker

Перенесено из https://github.com/KRYYYYYYYYYYYYYYYYYYY/crazy_xray_checker:

### checker.go
- **`CheckAnyL7`** — новая универсальная функция: vless (reality/tls), vmess, trojan, shadowsocks
- **`buildProxyConfig`** — унифицированный сборщик xray-конфига для всех протоколов
- **`extractSNICandidates`** — извлечение SNI-кандидатов из запутанных полей
- **SNI-retry loop** — перебор кандидатов внутри Go с ограничением в 3 попытки
- **`CheckVlessL7`** — сохранена для обратной совместимости

### check.py
- **Парсеры протоколов**: `parse_vmess_link`, `parse_trojan_link`, `parse_ss_link`, `parse_any_link`
- **`probe_any_l7`** — универсальный Python-пробник через `CheckAnyL7`
- **`l7_multi_probe`** — диспетчеризация: vless→старый путь, остальные→`CheckAnyL7`
- **`download_raw_data`** — теперь забирает vmess/trojan/ss ссылки, не только vless
- **Главный цикл** — фильтры адаптированы под все протоколы

## Data Flow

1. `check.py` pulls VLESS links from external URLs
2. Proxies are validated via `libchecker.so` (Go-based L7 HTTP probing)
3. Performance data stored in `test1/ranking.json`
4. High-performing proxies go through `torture_bot.py`
5. Final curated list written to `kr/mob/wifi.txt`
