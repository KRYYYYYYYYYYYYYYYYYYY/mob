import socket
import re
import os
import ssl
import json
import urllib.parse
import urllib.request
import time
import subprocess
import ipaddress

# Настройки путей
INPUT_FILE = 'test1/1.txt'
OUTPUT_FILE = 'kr/mob/wifi.txt'
STATUS_FILE = 'test1/status.json'
CACHE_FILE = 'test1/countries_cache.json' # Добавь эту константу для порядка
RANKING_FILE = 'test1/ranking.json'
VETTED_FILE = 'test1/vetted.txt'
PINNED_FILE = 'test1/pinned.txt'

EXTERNAL_SOURCE_URL = [
    "https://raw.githubusercontent.com/KRYYYYYYYYYYYYYYYYYYY/crazy_xray_checker/refs/heads/main/result/working.txt",
]

GRACE_PERIOD = 2 * 24 * 60 * 60 # 48 часов

HEADER = """
# profile-title: 🏴WIFI🏴
# remark: 🏴WIFI🏴
# announce: Подписка для использования на wifi.
# hide-settings: 1
# profile-update-interval: 2
# subscription-userinfo: upload=0; download=0; expire=0
# shadowrocket-userinfo: upload=0; download=0; expire=0
"""

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR"}

def rebuild_link_name(link: str, new_name: str) -> str:
    """
    Меняем ТОЛЬКО подпись после '#', не трогая base-часть vless-ссылки.
    Это безопасно для uuid/host/port/query-параметров.
    """
    base, sep, fragment = link.partition("#")

    # Если это уже закреп — не трогаем
    if fragment:
        frag = urllib.parse.unquote(fragment).upper()
        if "PINNED" in frag:
            return link

    fragment_dec = urllib.parse.unquote(fragment) if sep else ""

    # Пытаемся сохранить флаг/эмодзи
    match = re.match(r"^([^\w\s\d]|[^\x00-\x7F])+", fragment_dec)
    if match:
        prefix = match.group(0).strip()
        safe_name = f"{prefix} {new_name}".strip()
        return f"{base}#{urllib.parse.quote(safe_name)}"

    return f"{base}#{urllib.parse.quote(new_name)}"

def is_ipv6(host: str) -> bool:
    return ":" in host

def extract_host_port(link: str):
    # Поиск для обычного хоста или домена
    match = re.search(r"(@)([\w.-]+):(\d+)", link)
    if match:
        # group(0) содержит '@host:port', group(2) - host, group(3) - port
        return match.group(0), match.group(2), match.group(3)
    
    # Поиск для IPv6 в скобках
    ipv6_match = re.search(r"(@)\[([0-9a-fA-F:]+)\]:(\d+)", link)
    if ipv6_match:
        return ipv6_match.group(0), ipv6_match.group(2), ipv6_match.group(3)
        
    return None, None, None


def format_uri_host(host: str) -> str:
    if is_ipv6(host) and not host.startswith("["):
        return f"[{host}]"
    return host

def parse_vless_params(link: str) -> dict:
    parsed = urllib.parse.urlparse(link)
    query = urllib.parse.parse_qs(parsed.query)
    return {
        "type": query.get("type", [""])[0].lower(),
        "security": query.get("security", [""])[0].lower(),
        "sni": query.get("sni", [""])[0],
        "host": query.get("host", [""])[0],
        "path": urllib.parse.unquote(query.get("path", ["/"])[0] or "/"),
    }

def probe_server(host: str, port: int, link: str, timeout: float = 4.0):
    """
    Возвращает (ok, latency_ms, reason).
    Для ws дополнительно проверяет HTTP-ответ, чтобы не считать живым случайно открытый порт.
    """
    params = parse_vless_params(link)
    probe_host = params["sni"] or params["host"] or host
    use_tls = params["security"] in {"tls", "reality"}
    transport = params["type"]

    started = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            conn = sock
            if use_tls:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                conn = context.wrap_socket(sock, server_hostname=probe_host)

            if transport == "ws":
                ws_path = params["path"] if params["path"].startswith("/") else f"/{params['path']}"
                ws_host = params["host"] or probe_host
                request = (
                    f"GET {ws_path} HTTP/1.1\r\n"
                    f"Host: {ws_host}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                )
                conn.sendall(request.encode("utf-8"))
                response = conn.recv(256).decode("utf-8", errors="ignore")
                if not response.startswith("HTTP/1.1") and not response.startswith("HTTP/1.0"):
                    return False, None, "ws-no-http"
            else:
                conn.sendall(b"\x00")

        return True, (time.time() - started) * 1000, "ok"
    except socket.timeout:
        return False, None, "timeout"
    except ConnectionRefusedError:
        return False, None, "refused"
    except ssl.SSLError:
        return False, None, "tls"
    except OSError:
        return False, None, "network"

def get_country_code(host: str) -> str:
    try:
        url = f"http://ip-api.com/json/{host}?fields=status,countryCode"
        with urllib.request.urlopen(url, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == "success":
                return data.get("countryCode", "Unknown")
    except: pass
    return "Unknown"

def fetch_external_servers() -> list:
    # Если вдруг в переменной осталась просто строка, превращаем её в список для совместимости
    urls = [EXTERNAL_SOURCE_URL] if isinstance(EXTERNAL_SOURCE_URL, str) else EXTERNAL_SOURCE_URL
    
    all_configs = []
    for url in urls:
        if not url.strip(): continue
        try:
            print(f"📥 Загрузка из {url}")
            with urllib.request.urlopen(url, timeout=8) as response:
                configs = response.read().decode("utf-8").splitlines()
                all_configs.extend(configs)
        except Exception as e:
            print(f"❌ Ошибка загрузки {url}: {e}")
    return all_configs
@@ -317,125 +377,118 @@ def main():
            # 2. Полностью перезаписываем имя
            new_name = f"{flag} 💎 [PINNED] {counter}"
        
            # 3. Чистим базу
            clean_base = base_part.split("#")[0].strip()
        
            # 4. Собираем финальную ссылку
            final_linkk = f"{clean_base}#{urllib.parse.quote(new_name)}"
        
            working_for_sub.append(final_linkk)
            print(f"💎 [PINNED] {counter} с флагом '{flag}' готов")
        
            counter += 1
            continue
            
        # --- ФИЛЬТРЫ ---
        if base_part in blacklist:
            continue
        if not re.search(r'[a-f0-9\-]{36}@', base_part):
            continue 
    
        endpoint, host, port = extract_host_port(base_part)
        if not endpoint or not host or not port:
            continue

        # --- ЭТАП 1: РЕЗОЛВИНГ + ПРОВЕРКА СВЯЗИ ---
        resolved_ip = None
        latency_ms = None
        fail_reason = "unknown"
        is_alive = False
        try:
            resolved_ip = socket.gethostbyname(host) if not is_ipv6(host) else host
            if resolved_ip in seen_ips:
                continue

            is_alive, latency_ms, fail_reason = probe_server(resolved_ip, int(port), base_part, timeout=4.0)
            if is_alive and latency_ms and latency_ms > 1500:
                is_alive = False
                fail_reason = "slow"

            if is_alive:
                seen_ips.add(resolved_ip)
        except OSError:
            is_alive = False
            fail_reason = "dns"
    
        # --- ЭТАП 2: ЕСЛИ СЕРВЕР РАБОТАЕТ ---
        if is_alive:
            if "security=none" in base_part.lower():
                print(f"❌ НЕТ ШИФРОВАНИЯ: {host}")
                continue
    
            country = get_country_code(host)
            if country not in ALLOWED_COUNTRIES:
                continue
    
            working_for_base.append(base_part)
            ip_str = f"[{resolved_ip}]" if is_ipv6(resolved_ip) else resolved_ip
            sub_link = base_part.replace(endpoint, f"@{ip_str}:{port}", 1)
            
            if "sni=" not in sub_link.lower() and not is_ipv6(host):
                sep = "&" if "?" in sub_link else "?"
                sub_link += f"{sep}sni={host}"
            
            final_link = rebuild_link_name(sub_link, f"wifi {counter}")
            working_for_sub.append(final_link)
            
            print(f"✅ ОК {len(working_for_sub)}/200 ({country}): {host} -> {resolved_ip} (wifi {counter})")
            counter += 1
    
        # --- ЭТАП 3: ЕСЛИ СЕРВЕР НЕ ОТВЕЧАЕТ ---
        else:
            if base_part in ranking_db:
                del ranking_db[base_part]
            if base_part in vetted_list:
                vetted_list.remove(base_part)
            
            fail_time = history.get(base_part, now)
            
            if now - fail_time > 86400: 
                print(f"🗑️ УДАЛЕН И ЗАБЛОКИРОВАН (1 день оффлайн): {host}")
                with open('test1/blacklist.txt', 'a') as bl:
                    bl.write(base_part + "\n")
                continue 
    
            if now - fail_time < GRACE_PERIOD:
                country = get_country_code(host)
                if country in ALLOWED_COUNTRIES:
                    working_for_base.append(base_part)
                    new_history[base_part] = fail_time
                    print(f"⏸️ DOWN ({country}): {host} (в базу на перепроверку, reason={fail_reason})")
            else:
                print(f"🗑️ Удален (тайм-аут): {host}, reason={fail_reason}")

        # --- ВСЕ, ЧТО НЕ УСПЕЛИ ПРОВЕРИТЬ (если набрали 200 раньше конца списка) ---
        new_deferred = unique_links[idx:] 
    # --- КОНЕЦ ЦИКЛА ПРОВЕРКИ ---
    # --- ЛОГИКА ОЧЕРЕДИ И ЛИМИТОВ (ИСПРАВЛЕНО) ---
        
     #   1. Разделяем то, что нашли, на две кучи
    all_pinned = [l for l in working_for_sub if "💎 [PINNED]" in l]
    all_others = [l for l in working_for_sub if "💎 [PINNED]" not in l]
    
    final_to_sub = []
    seen_in_final = set()# То самое "сито" для адресов
    
    # 2. Сначала берем закрепы (Приоритет №1)
    # Лимит 50 штук
    for l in all_pinned:
        if len(final_to_sub) >= 50: break
        base = l.split("#")[0].strip()
        if base not in seen_in_final:
            final_to_sub.append(l)
            seen_in_final.add(base)
    # 3. Добираем обычные сервера, пока не станет 200 (Приоритет №2)
    # Но только те, которых еще НЕТ в закрепах
    for l in all_others:
        if len(final_to_sub) >= 200: break
        base = l.split("#")[0].strip()
        if base not in seen_in_final: # ВОТ ОНА — ЗАЩИТА ОТ ДУБЛЯ
            final_to_sub.append(l)
            seen_in_final.add(base)
    
    # 4. Формируем deferred.txt (остатки)
    # Сюда идет то, что не влезло + то, что вообще не проверялось 
    leftover_from_others = [l for l in all_others if l.split("#")[0].strip() not in seen_in_final]
    deferred_final = new_deferred + leftover_from_others
    
# 5. Сохраняем результат
    
    # Сначала сохраняем deferred.txt (очередь на потом)
    with open('test1/deferred.txt', "w", encoding="utf-8") as f:
        f.write("\n".join(deferred_final))
    
    # ФОРМИРУЕМ ПРАВИЛЬНЫЙ ТЕКСТ ДЛЯ ПОДПИСКИ
    # .strip() убирает случайные пробелы в начале/конце хедера
    # \n\n гарантирует, что между командами и ссылками будет пустая строка (важно для iPhone)
    final_content = HEADER.strip() + "\n\n" + "\n".join(final_to_sub)

    # ЗАПИСЫВАЕМ В ОСНОВНОЙ ФАЙЛ (kr/mob/wifi.txt)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(final_content)
        
    # Сохраняем рабочую базу ссылок для следующего запуска чекера
    os.makedirs(os.path.dirname(INPUT_FILE), exist_ok=True)
    with open(INPUT_FILE, "w", encoding="utf-8") as f: 
        f.write("\n".join(working_for_base))
    
    # Сохраняем историю и рейтинги
    with open(STATUS_FILE, "w") as f: 
        json.dump(new_history, f)
    with open('test1/ranking.json', "w") as f:
        json.dump(ranking_db, f)

    print(f"🏁 План выполнен: {len(final_to_sub)} в подписке. Остаток в базе: {len(deferred_final)}")
    # Базовые части закрепов
    pinned_bases = {p.split("#")[0].strip() for p in pinned_list}
    
    # Сколько закрепов реально попало в подписку
    count_pinned = sum(
        1 for l in final_to_sub
        if l.split("#")[0].strip() in pinned_bases
    )
    
    print(f"💎 Закрепленных в подписке: {count_pinned} (из лимита 50)")
    print(f"✅ Всего в wifi.txt: {len(final_to_sub)} (из лимита 200)")
    
    # 3. Сохранение (ТВОЙ БЛОК БЕЗ ИЗМЕНЕНИЙ НАДПИСЕЙ)
    os.makedirs(os.path.dirname(INPUT_FILE), exist_ok=True)
    with open(INPUT_FILE, "w", encoding="utf-8") as f: 
        f.write("\n".join(working_for_base))
    
    with open(STATUS_FILE, "w") as f: 
        json.dump(new_history, f)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        # ЗАМЕНИ ТУТ working_for_sub на final_to_sub
        f.write(HEADER + "\n".join(final_to_sub))

    with open(CACHE_FILE, 'w') as f:
        json.dump(countries_cache, f)

    print(f"🏁 Готово! Подписка обновлена.")
    # --- ОБНОВЛЕНИЕ ИНТЕРФЕЙСА С ГАЛОЧКАМИ ---
 # --- ОБНОВЛЕНИЕ ИНТЕРФЕЙСА С ГАЛОЧКАМИ ---
    if token and repo:
        try:
            update_time = time.strftime("%d.%m.%Y %H:%M:%S")
            env_gh = {**os.environ, "GH_TOKEN": token}

            # --- ПАНЕЛЬ 1: ЧЕРНЫЙ СПИСОК (CONTROL) ---
            find_cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'control', '--json', 'number', '--limit', '1']
            out = subprocess.check_output(find_cmd, env=env_gh).decode()
            data = json.loads(out)
            
            if data:  # Проверка: если список не пуст
                issue_number = str(data[0]['number'])
                issue_body = f"### 🎮 Панель управления серверами\n🕒 Последнее обновление: `{update_time}`\n\n"
                issue_body += "Отметь [x] и сохрани, чтобы отправить в черный список:\n\n---\n\n"
                
                for i, link in enumerate(working_for_base, 1):
                    status = "[x]" if link in blacklist else "[ ]"
                    issue_body += f"- {status} '{link}' (wifi {i})\n\n---\n\n"
                
                with open("issue_body.txt", "w", encoding="utf-8") as f: f.write(issue_body)
                subprocess.run(['gh', 'issue', 'edit', issue_number, '--repo', repo, '--body-file', 'issue_body.txt'], env=env_gh)
                print(f"📝 Панель Control #{issue_number} обновлена.")
            else:
                print("⚠️ Issue с меткой 'control' не найдено.")

            # --- ПАНЕЛЬ 2: КАНДИДАТЫ В ЗАКРЕП (ТОЛЬКО ИЗ VETTED.TXT) ---
            pin_cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'pin_control', '--json', 'number', '--limit', '1']
            out_pin = subprocess.check_output(pin_cmd, env={**os.environ, "GH_TOKEN": token}).decode()
            
            if out_pin and out_pin != "[]":
                num_pin = str(json.loads(out_pin)[0]['number'])
                update_time = time.strftime('%d.%m.%Y %H:%M:%S')
                
                # Формируем заголовок и КНОПКУ ЗАЩИТЫ
                body_pin = f"### 💎 Кандидаты в закреп и бан\n🕒 Обновлено: `{update_time}`\n\n"
                body_pin += "🚨 **ПОДТВЕРЖДЕНИЕ:**\n"
                body_pin += "- [ ] ✅ **ПРИМЕНИТЬ ВЫБРАННЫЕ PIN/BAN**\n"
                body_pin += "> _Нажмите на эту галочку, чтобы бот выполнил выбранные действия._\n\n---\n\n"
                
                # Читаем только VETTED.TXT
                vetted_for_issue = []
                if os.path.exists(VETTED_FILE):
                    with open(VETTED_FILE, 'r', encoding='utf-8') as f:
                        vetted_for_issue = [line.split('#')[0].strip() for line in f if 'vless://' in line]
        
                if not vetted_for_issue:
                    body_pin += "_Пока элитных кандидатов нет..._"
                else:
                    for i, base_only in enumerate(vetted_for_issue, 1):
                        body_pin += f"📡 **Элита {i}:**\n"
                        body_pin += f"- [ ] PIN_{base_only}\n"
                        body_pin += f"- [ ] BAN_{base_only}\n\n---\n\n"
                
                with open("pin_body.txt", "w", encoding="utf-8") as f: 
                    f.write(body_pin)
                
                subprocess.run(['gh', 'issue', 'edit', num_pin, '--repo', repo, '--body-file', 'pin_body.txt'], 
                               env={**os.environ, "GH_TOKEN": token})
                print(f"💎 Панель #{num_pin} обновлена. Защита включена.")

            # --- ПАНЕЛЬ 3: УПРАВЛЕНИЕ ЗАКРЕПАМИ (UNPIN) ---
            unpin_cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'unpin_control', '--json', 'number', '--limit', '1']
            out_unp = subprocess.check_output(unpin_cmd, env=env_gh).decode()
            data_unp = json.loads(out_unp)
            
            if data_unp:
                num_unp = str(data_unp[0]['number'])
                body_unp = f"### 👑 Ваши закрепленные сервера\n🕒 Обновлено: `{update_time}`\n\n"
                for i, link in enumerate(pinned_list, 1):
                    body_unp += f"- [ ] '{link}' (FIXED {i})\n\n---\n\n"
                
                with open("unpin_body.txt", "w", encoding="utf-8") as f: f.write(body_unp)
                subprocess.run(['gh', 'issue', 'edit', num_unp, '--repo', repo, '--body-file', 'unpin_body.txt'], env=env_gh)
                print(f"🔓 Панель Unpin #{num_unp} обновлена.")

        except Exception as e:
            print(f"⚠️ Ошибка при обновлении панелей GitHub: {e}")

if __name__ == "__main__":
    main()
