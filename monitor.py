import socket
import time
import os
import ssl
import re
import json
import requests

# Файлы
WIFI_FILE = 'kr/mob/wifi.txt'
DEFERRED_FILE = 'test1/deferred.txt'
INPUT_FILE = 'test1/1.txt'
BLACKLIST_FILE = 'test1/blacklist.txt'
PINNED_FILE = 'test1/pinned.txt'
COUNTRY_CACHE_FILE = 'test1/countries_cache.json'
PROFILE_FILE = 'test1/stress_profile.json'
RANK_FILE = 'test1/ranking.json'

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}

DEFAULT_MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; SM-A336B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]
DEFAULT_PROBE_PATHS = ["/", "/generate_204", "/favicon.ico"]

country_cache = {}
if os.path.exists(COUNTRY_CACHE_FILE):
    try:
        with open(COUNTRY_CACHE_FILE, 'r', encoding='utf-8') as f:
            country_cache = json.load(f)
    except Exception:
        country_cache = {}

def get_country(host):
    if host in country_cache:
        return country_cache[host]
    
    try:
        if not host or host == "127.0.0.1":
            return "??"

        resp = requests.get(
            f"http://ip-api.com/json/{host}?fields=status,countryCode",
            timeout=2,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                code = data.get("countryCode", "??")
                # Сохраняем в кэш
                country_cache[host] = code
                with open(COUNTRY_CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(country_cache, f)
                return code
        elif resp.status_code == 429:
            print("⚠️ Лимит запросов к IP-API исчерпан (429)")
    except Exception: 
        pass
    
    return "??"

def extract_host_port(link):
    """Извлекает хост и порт, игнорируя всё, что идет после порта"""
    # 1. Сначала ищем стандартный формат @host:port
    # [\w\.-]+ — хост, (\d+) — порт, (?=[/?#]|$) — проверка, что дальше разделитель или конец
    match = re.search(r'@([\w\.-]+):(\d+)(?=[/?#]|$)', link)
    
    if not match:
        # 2. Ищем формат со скобками для IPv6: @[addr]:port
        match = re.search(r'@\[([0-9a-fA-F:]+)\]:(\d+)(?=[/?#]|$)', link)
    
    if match:
        host = match.group(1)
        port = int(match.group(2))
        return host, port
    
    return None, None
        
def add_to_blacklist(base_part):
    """Добавляет сервер в черный список, защищая от дублей и ошибок кодировки"""
    existing = set()
    if os.path.exists(BLACKLIST_FILE):
        # Добавляем encoding='utf-8', чтобы не было проблем с системными символами
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            # strip() обязателен, чтобы не плодить пустые строки и невидимые пробелы
            existing = {line.strip() for line in f if line.strip()}
    
    if base_part not in existing:
        with open(BLACKLIST_FILE, 'a', encoding='utf-8') as f:
            f.write(base_part + "\n")
        print(f"🚫 [BLACKLIST] Добавлен: {base_part[:30]}...")

def remove_from_all(base_part):
    for path in [WIFI_FILE, DEFERRED_FILE, INPUT_FILE]:
        if not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        new_lines = [l for l in lines if l.split('#')[0].strip() != base_part]
        if len(lines) != len(new_lines):
            with open(path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            print(f" 🧹 [УДАЛЕНИЕ] Сервер {base_part[:20]}... вырезан из {path}")
            
def is_ipv6(host):
    return ":" in host if host else False

def load_stress_config():
    stress_config = {
        "timeout": 2.5,
        "dpi_sleep": 0.5,
        "probe_attempts": 4,
        "min_success": 2,
        "recv_timeout": 1.7,
        "between_attempts_sleep": 0.35,
        "user_agents": list(DEFAULT_MOBILE_USER_AGENTS),
        "probe_paths": list(DEFAULT_PROBE_PATHS),
    }

    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            stress_config["timeout"] = data.get("max_handshake_ms", 2500) / 1000
            stress_config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
            stress_config["probe_attempts"] = int(data.get("probe_attempts", stress_config["probe_attempts"]))
            stress_config["min_success"] = int(data.get("min_success", stress_config["min_success"]))
            stress_config["recv_timeout"] = float(data.get("recv_timeout", stress_config["recv_timeout"]))
            stress_config["between_attempts_sleep"] = float(data.get("between_attempts_sleep", stress_config["between_attempts_sleep"]))
            if isinstance(data.get("mobile_user_agents"), list) and data.get("mobile_user_agents"):
                stress_config["user_agents"] = [str(x) for x in data["mobile_user_agents"] if str(x).strip()]
            if isinstance(data.get("probe_paths"), list) and data.get("probe_paths"):
                stress_config["probe_paths"] = [str(x) for x in data["probe_paths"] if str(x).strip()]
            print(f"⚙️ Профиль загружен: Таймаут {stress_config['timeout']}s, попыток {stress_config['probe_attempts']}")
        except Exception:
            print("⚠️ Ошибка профиля, использую дефолты")

    return stress_config

def deep_kill_check(link, stress_config, pinned_bases):
    base_part = link.split("#")[0].strip()
    if base_part in pinned_bases:
        return True, 200, 0, 0

    host, port = extract_host_port(base_part)
    if not host or not port:
        return False, 404, 0, 0

    if is_ipv6(host):
            print(f"🚫 [IPv6 DETECTED] {host} - отправляем в бан")
            return False, 666, 0, 0

    is_tls = "security=tls" in link.lower() or "security=reality" in link.lower()
    sni_match = re.search(r'sni=([^&?#]+)', link)
    server_hostname = sni_match.group(1) if sni_match else host

    attempts = max(1, int(stress_config.get("probe_attempts", 4)))
    min_success = max(1, int(stress_config.get("min_success", 2)))
    user_agents = stress_config.get("user_agents") or DEFAULT_MOBILE_USER_AGENTS
    probe_paths = stress_config.get("probe_paths") or DEFAULT_PROBE_PATHS

    success = 0
    for attempt in range(attempts):
        ua = user_agents[attempt % len(user_agents)]
        path = probe_paths[attempt % len(probe_paths)]
        request_data = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {server_hostname}\r\n"
            f"User-Agent: {ua}\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        ).encode()

        try:
            infos = socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
        except socket.gaierror:
            infos = []

        attempt_ok = False
        for info in infos:
            ip = info[4][0]
            try:
                with socket.create_connection((ip, int(port)), timeout=stress_config["timeout"]) as s:
                    if is_tls:
                        context = ssl.create_default_context()
                        context.check_hostname = False
                        context.verify_mode = ssl.CERT_NONE

                        with context.wrap_socket(s, server_hostname=server_hostname) as ssock:
                            ssock.sendall(request_data)
                            if stress_config["dpi_sleep"] > 0:
                                time.sleep(stress_config["dpi_sleep"])
                            ssock.settimeout(stress_config.get("recv_timeout", 1.7))
                            if ssock.recv(8):
                                attempt_ok = True
                                break
                    else:
                        s.sendall(b'\x05\x01\x00')
                        s.settimeout(stress_config.get("recv_timeout", 1.7))
                        if s.recv(2):
                            attempt_ok = True
                            break
            except (socket.timeout, ConnectionResetError, ssl.SSLError, socket.error):
                continue

        if attempt_ok:
            success += 1
            if success >= min_success:
                return True, 200, success, attempts

        if attempt < attempts - 1:
            time.sleep(stress_config.get("between_attempts_sleep", 0.35))

    return False, 404, success, attempts
    
def main_monitor():
    start_run = time.time()
    stress_config = load_stress_config()

    # --- ЗАГРУЗКА РЕЙТИНГА ---
    ranking_db = {}
    
    if os.path.exists(RANK_FILE):
        try:
            with open(RANK_FILE, 'r', encoding='utf-8') as f:
                ranking_db = json.load(f)
        except Exception:
            ranking_db = {}

    # Цикл работает 10 минут (600 сек)
    while time.time() - start_run < 600:
        # --- ШАГ 1: СОЗДАЕМ "ПАМЯТЬ" ЗАКРЕПОВ ---
        pinned_bases = set()
        if os.path.exists(PINNED_FILE):
            try:
                with open(PINNED_FILE, 'r', encoding='utf-8') as f:
                    # Загружаем только чистую часть vless (до #)
                    pinned_bases = {line.split('#')[0].strip() for line in f if 'vless://' in line}
            except Exception:
                pass
        
        print(f"\n🕵️ ОБХОД В {time.strftime('%H:%M:%S')}")
        
        if not os.path.exists(WIFI_FILE):
            print("📭 Файл wifi.txt не найден, жду...")
            time.sleep(60)
            continue

        with open(WIFI_FILE, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if 'vless://' in l]

        # --- ШАГ 2: РАСПРЕДЕЛЯЕМ СЕРВЕРЫ (БЕЗ ДИСКА) ---
        pinned_in_wifi = []
        others_in_wifi = []

        for l in lines:
            base = l.split("#")[0].strip()
            if base in pinned_bases:
                pinned_in_wifi.append(l)
            else:
                others_in_wifi.append(l)

        # Берем только первые 50 закрепов
        pinned_in_wifi = pinned_in_wifi[:50]
        
        valid_others = []
        for link in others_in_wifi:
            base = link.split("#")[0].strip()
            # ПЕРЕДАЕМ ПАМЯТЬ В ЧЕКЕР
            host, _ = extract_host_port(base)
            is_ok, status_code, success_hits, total_hits = deep_kill_check(link, stress_config, pinned_bases)
            
            if is_ok:
                # дополнительный фильтр страны, чтобы отсекать лишнее
                if host and get_country(host) not in ALLOWED_COUNTRIES:
                    print(f"🌍 {base[:20]}... страна не в whitelist, удален")
                    if base in ranking_db:
                        del ranking_db[base]
                    remove_from_all(base)
                    continue

                valid_others.append(link)
                old_data = ranking_db.get(base, 0)
                old_rank = old_data.get("rank", 0) if isinstance(old_data, dict) else old_data
                new_rank = old_rank + 1
                ranking_db[base] = {"rank": new_rank, "link": link}
                print(f"📈 {base[:20]}... +1 балл ({new_rank}) [{success_hits}/{total_hits}]")
            else:
                # Если упал — удаляем из рейтинга и из файлов
                if base in ranking_db:
                    del ranking_db[base]
                remove_from_all(base)
                print(f"🧊 {base[:20]}... упал. Удален. [{success_hits}/{total_hits}]")

                if status_code in {666, 404}:
                    add_to_blacklist(base)
                    reason = "IPv6" if status_code == 666 else "Н/Д"
                    print(f"💀 БАН ({reason}): {base[:30]}")

        # Сохраняем рейтинг на диск
        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)

        # Формируем итоговый wifi.txt (лимит 200)
        final_list = (pinned_in_wifi + valid_others)[:200]

        # --- УМНАЯ ЗАПИСЬ: СОХРАНЯЕМ ТВОЙ ОРИГИНАЛЬНЫЙ ХЕАДЕР ---
        header_to_keep = []
        with open(WIFI_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip().startswith('#'):
                    header_to_keep.append(line.rstrip())
                elif line.strip():
                    break
                    
        if not header_to_keep:
            header_to_keep = [
                "# profile-title: 🏳️Мобильный инет🏳️",
                "# remark: 🏳️Мобильный инет🏳️",
                "# announce: Подписка для использования ТОЛЬКО на мобильном интернете в условиях БЕЛЫХ СПИСКОВ!",
                "# profile-update-interval: 2",
                "# subscription-userinfo: upload=0; download=0; expire=0",
                "# shadowrocket-userinfo: upload=0; download=0; expire=0",
            ]

        # Записываем всё обратно
        with open(WIFI_FILE, 'w', encoding='utf-8') as f:
            # Склеиваем строки хедера через перенос строки, добавляем отступ и вставляем ссылки
            f.write("\n".join(header_to_keep) + "\n\n")
            f.write("\n".join(final_list))
        
        print(f"📊 ИТОГ: {len(pinned_in_wifi)} закрепов, {len(valid_others)} живых. Жду минуту...")
        time.sleep(60)

if __name__ == "__main__":
    main_monitor()
