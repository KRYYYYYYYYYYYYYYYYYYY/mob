# Что перенесено из crazy_xray_checker и что еще можно добавить

## Уже перенесено

- Быстрый TCP pre-check перед дорогим L7 (`checker.go`): аналог `quickTCPProbe`.
- L7 проверка через локальный SOCKS + Xray.
- Перебор probe URL с измерением latency и `first-byte` валидацией.
- Повторная попытка probe (retry) для снижения флапов.
- Параллельный pipeline проверки в Python (`ThreadPoolExecutor`).
- Причины отсева/фейла в JSON + потоковый лог (аналог идеи `result/all.txt`).

## Новые служебные файлы в этом проекте

- `test1/reasons.json` — агрегированные причины (ok/fail/skip).
- `test1/check_log.txt` — потоковый лог по каждой проверенной записи.
- `test1/stress_profile.example.json` — шаблон профиля сети с параметрами тюнинга.

## Что можно добавить следующим шагом (по приоритету)

1. **Отдельный web-режим рескана** как в `web.go`/`rescan.go` (триггер ручного рескана).
2. **Более богатые reason-коды из Go** (через отдельный FFI API, не только int latency).
3. **Поддержка URL-источников прямо в очереди** (как `fetchLines` + `isURL`).
4. **Стриминг рабочих/всех результатов в отдельные файлы** с `flush` на каждом шаге.

## Куда добавлять

- Go L7 логика: `checker.go`
- Пайплайн, сортировка, blacklist, параллелизм: `check.py`
- Тюнинг под сеть: `test1/stress_profile.json` (создается по шаблону `test1/stress_profile.example.json`)
- Диагностика: `test1/reasons.json`, `test1/check_log.txt`
