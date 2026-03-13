import socket
import re
import os
import ssl
import json
from urllib.parse import urlparse, urlunparse, quote, unquote
import urllib.parse
import urllib.request
import time
import subprocess


# Настройки путей
INPUT_FILE = 'test1/1.txt'
OUTPUT_FILE = 'kr/mob/wifi.txt'
STATUS_FILE = 'test1/status.json'
CACHE_FILE = 'test1/countries_cache.json' # Добавь эту константу для порядка
RANKING_FILE = 'test1/ranking.json'

EXTERNAL_SOURCE_URL = [
    "https://raw.githubusercontent.com/zieng2/wl/main/vless_lite.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/WHITE-CIDR-RU-checked.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/WHITE-CIDR-RU-all.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile-2.txt",
    "https://raw.githubusercontent.com/makitaltdriddim-web/vpn-configs-for-russia-/refs/heads/main/WHITE-CIDR-RU-checked.txt",
    "https://raw.githubusercontent.com/makitaltdriddim-web/vpn-configs-for-russia-/refs/heads/main/WHITE-CIDR-RU-all.txt",
    "https://raw.githubusercontent.com/makitaltdriddim-web/vpn-configs-for-russia-/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    "https://raw.githubusercontent.com/makitaltdriddim-web/vpn-configs-for-russia-/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile-2.txt",
    "https://raw.githubusercontent.com/makitaltdriddim-web/vpn-configs-for-russia-/refs/heads/main/BLACK_VLESS_RUS_mobile.txt",
    "https://raw.githubusercontent.com/makitaltdriddim-web/vpn-configs-for-russia-/refs/heads/main/BLACK_VLESS_RUS.txt"
]

GRACE_PERIOD = 2 * 24 * 60 * 60 # 48 часов

HEADER = """# profile-title: 🏳️Мобильный инет🏳️
# remark: 🏳️Мобильный инет🏳️
# announce: Подписка для использования ТОЛЬКО на мобильном интернете в условиях БЕЛЫХ СПИСКОВ!
# profile-update-interval: 2
# subscription-userinfo: upload=0; download=0; expire=0
# shadowrocket-userinfo: upload=0; download=0; expire=0

"""

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}

def download_raw_data(urls):
    """
    Этап 1: Огороженная загрузка с защитой от сбоев DNS.
    """
    all_links = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    print("📥 ЭТАП 1: Загрузка сырых данных (Огороженный режим)")
    
    for url in urls:
        success = False
        # Извлекаем домен (например, raw.githubusercontent.com)
        try:
            hostname = urllib.parse.urlparse(url).netloc
        except:
            hostname = None
            
        for attempt in range(5): 
            try:
                # 1. Прогреваем DNS (пробиваем Errno -3)
                if hostname:
                    try:
                        socket.gethostbyname(hostname)
                    except:
                        pass # Если не вышло тут, попробует urllib

                print(f"📡 Попытка {attempt+1}: {url.split('/')[-1]}...", end=" ")
                req = urllib.request.Request(url.strip(), headers=headers)
                
                # 2. Загружаем данные
                with urllib.request.urlopen(req, timeout=30) as response:
                    content = response.read().decode("utf-8")
                    found = [line.strip() for line in content.splitlines() if "vless://" in line]
                    all_links.extend(found)
                    print(f"✅ Найдено {len(found)} шт.")
                    success = True
                    break
            except Exception as e:
                # 3. Нарастающая пауза: 5с, 10с, 15с, 20с
                wait_time = (attempt + 1) * 5
                print(f"❌ Ошибка: {e}. Ждем {wait_time}с...")
                time.sleep(wait_time)
        
        if not success:
            print(f"⚠️ КРИТИЧЕСКАЯ ОШИБКА: Не удалось загрузить {url}")
            
    return all_links

def rebuild_link_name(link: str, new_label: str) -> str:
    try:
        parsed = urlparse(link.strip())
        # Вытаскиваем старое название (фрагмент)
        old_fragment = unquote(parsed.fragment)
        
        # Пытаемся сохранить флаг, если он был в начале старого названия
        # Ищем эмодзи флагов (региональные индикаторы)
        import re
        emoji_match = re.match(r'^([\U0001F1E6-\U0001F1FF]{2})', old_fragment)
        flag = emoji_match.group(1) if emoji_match else ""
        
        # Собираем новое имя: флаг (если нашли) + твой текст
        full_new_name = f"{flag} {new_label}".strip()
        
        # Заменяем только фрагмент, остальное (хост, параметры) не трогаем
        new_link_obj = parsed._replace(fragment=full_new_name)
        return urlunparse(new_link_obj)
    except Exception as e:
        print(f"⚠️ Ошибка парсинга ссылки: {e}")
        return link # В случае ошибки возвращаем оригинал, чтобы не потерять сервер

def remove_from_input_file(base_to_remove: str):
    """Удаляет конкретную ссылку из 1.txt по её базовой части"""
    if not os.path.exists(INPUT_FILE):
        return
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Оставляем только те строки, которые НЕ содержат этот base_part
        new_lines = [l for l in lines if base_to_remove not in l]
        
        if len(lines) != len(new_lines):
            with open(INPUT_FILE, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
    except Exception as e:
        print(f"⚠️ Ошибка при очистке {INPUT_FILE}: {e}")

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

def get_country_code(host, cache):
    # Если это домен, резолвим в IP для кэша (страна привязана к IP)
    try:
        ip = socket.gethostbyname(host)
    except:
        ip = host

    # Проверяем кэш
    if ip in cache:
        return cache[ip]

    # Если в кэше нет, идем в API (не забываем про лимит 45 зап/мин)
    try:
        # Добавляем небольшую паузу, чтобы не спамить (0.5 сек даст ~120 зап/мин, чуть рискованно, но для 200 серверов пойдет)
        time.sleep(0.5) 
        url = f"http://ip-api.com/json/{ip}?fields=status,countryCode"
        with urllib.request.urlopen(url, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == "success":
                code = data.get("countryCode", "Unknown")
                cache[ip] = code # Сохраняем в память
                return code
    except: 
        pass
    return "Unknown"

def fetch_external_servers() -> list:
    # Приводим к списку в любом случае
    urls = EXTERNAL_SOURCE_URL if isinstance(EXTERNAL_SOURCE_URL, list) else [EXTERNAL_SOURCE_URL]
    
    all_configs = []
    print(f"🌐 Начинаю сбор из {len(urls)} источников...")

    for url in urls:
        url = url.strip()
        if not url: continue
        
        try:
            # Увеличиваем таймаут и добавляем User-Agent, чтобы GitHub не блокировал
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                content = response.read().decode("utf-8")
                configs = [line.strip() for line in content.splitlines() if "vless://" in line]
                
                if configs:
                    all_configs.extend(configs)
                    print(f"✅ {url.split('/')[-1]}: Найдено {len(configs)} шт.")
                else:
                    print(f"⚠️ {url.split('/')[-1]}: Файл пуст или нет vless")
                    
        except Exception as e:
            # Если один URL упал, просто пишем ошибку и идем дальше
            print(f"❌ Ошибка на источнике {url.split('/')[-1]}: {e}")
            continue 

    print(f"📊 Итого загружено извне: {len(all_configs)} потенциальных серверов")
    return all_configs

def safe_gh_call(cmd, token):
    """Безопасно вызывает gh cli, пробуя 3 раза при сетевых сбоях."""
    import subprocess
    import time
    import os
    for attempt in range(3):
        try:
            # Выполняем команду
            return subprocess.check_output(
                cmd, 
                env={**os.environ, "GH_TOKEN": token}, 
                stderr=subprocess.STDOUT
            ).decode()
        except subprocess.CalledProcessError as e:
            err_output = e.output.decode().lower() if e.output else ""
            # Если это сетевая ошибка GitHub, ждем и повторяем
            if any(x in err_output for x in ["connection", "api.github.com", "timeout"]):
                print(f"⏳ Сетевой лаг GitHub (попытка {attempt+1}/3)... Ждем 5 сек.")
                time.sleep(5)
                continue
            # Если ошибка другая (например, нет прав), выходим
            print(f"❌ Ошибка GH CLI: {err_output[:100]}")
            break
    return "[]"

def main():
    import subprocess
    import os
    import json
    import time
    import re
    import socket
    import ssl
    import urllib.parse

    vetted_list = []
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    now = time.time()
    counter = 1  # Счётчик для названий (wifi 1, wifi 2...)

    # --- 1. ЗАГРУЗКА ВСЕХ ФАЙЛОВ ---
    def load_json(path, default):
        if os.path.exists(path):
            try:
                with open(path, 'r') as f: return json.load(f)
            except: return default
        return default

    countries_cache = load_json(CACHE_FILE, {})
    ranking_db = load_json('test1/ranking.json', {})
    history = load_json(STATUS_FILE, {})

    blacklist = set()
    if os.path.exists('test1/blacklist.txt'):
        with open('test1/blacklist.txt', 'r') as f:
            blacklist = {line.strip() for line in f if line.strip()}

    pinned_list = []
    if os.path.exists('test1/pinned.txt'):
        with open('test1/pinned.txt', 'r', encoding='utf-8') as f:
            pinned_list = [line.strip() for line in f if "vless://" in line]

    deferred_base = []
    if os.path.exists('test1/deferred.txt'):
        with open('test1/deferred.txt', 'r', encoding='utf-8') as f:
            deferred_base = [line.strip() for line in f if line.strip()]

    current_base = []
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            current_base = f.read().splitlines()
    
    vetted_list = []
    if os.path.exists('test1/vetted.txt'):
        with open('test1/vetted.txt', 'r', encoding='utf-8') as f:
            # Читаем только уникальные vless ссылки
            vetted_from_file = list(set(line.strip() for line in f if "vless://" in line))
    print(f"📥 Загружено из vetted.txt: {len(vetted_from_file)} серверов")
    
    # --- 2. НАСТРОЙКИ СТРЕСС-ТЕСТА ---
    stress_config = {
        "timeout": 2.5,
        "dpi_sleep": 0.5 if load_json('test1/stress_profile.json', {}).get("mimic_dpi_delay") else 0.1,
        "target_mtu": 1280
    }
    profile = load_json('test1/stress_profile.json', {})
    if profile:
        stress_config["timeout"] = profile.get("max_handshake_ms", 2500) / 1000
        stress_config["target_mtu"] = profile.get("target_mtu", 1280)

    data_pin = None
    data_ctrl = None
    data_unp = None
    working_for_base = []

    # --- БЛОК ЧТЕНИЯ КОМАНД ИЗ GITHUB ---
    if token and repo:
        # 1. ПАНЕЛЬ CONTROL (Обычный черный список)
        try:
            print("🔍 Проверка черного списка в GitHub...")
            cmd_control = ['gh', 'issue', 'list', '--repo', repo, '--label', 'control', '--json', 'body', '--limit', '1']
            out = safe_gh_call(cmd_control, token)
            data = json.loads(out)
            if data:
                checked = re.findall(r'- \[x\] (vless://[^\s]+)', data[0]['body'])
                if checked:
                    for s in checked:
                        blacklist.add(s.split('#')[0].strip())
                    with open('test1/blacklist.txt', 'w', encoding='utf-8') as f:
                        f.write("\n".join(list(blacklist)))
                    print(f"🚫 Обновлено: {len(checked)} в блэклисте.")
        except Exception as e: print(f"⚠️ Ошибка Blacklist: {e}")

        # 2. ПАНЕЛЬ PIN/BAN (Двойные галочки из Кандидатов)
        try:
            print("🔍 Проверка панели кандидатов (PIN/BAN)...")
            cmd_pin = ['gh', 'issue', 'list', '--repo', repo, '--label', 'pin_control', '--json', 'body', '--limit', '1']
            out = safe_gh_call(cmd_pin, token)
            data = json.loads(out)
            if data:
                body = data[0]['body']
                
                # Ищем [x] PIN_vless://...
                to_pin = re.findall(r'- \[x\] PIN_(vless://[^\s\n]+)', body)
                if to_pin:
                    with open('test1/pinned.txt', 'a', encoding='utf-8') as pf:
                        for s in to_pin:
                            base = s.split("#")[0].strip()
                            if all(base != p.split("#")[0].strip() for p in pinned_list):
                                pf.write(s.strip() + "\n")
                                pinned_list.append(s.strip())
                    print(f"💎 Добавлено закрепов: {len(to_pin)}")

                # Ищем [x] BAN_vless://...
                to_ban = re.findall(r'- \[x\] BAN_(vless://[^\s\n]+)', body)
                if to_ban:
                    with open('test1/blacklist.txt', 'a', encoding='utf-8') as bf:
                        for s in to_ban:
                            base = s.split("#")[0].strip()
                            if base not in blacklist:
                                bf.write(base + "\n")
                                blacklist.add(base)
                    print(f"🚫 Отправлено в бан через панель кандидатов: {len(to_ban)}")
        except Exception as e: print(f"⚠️ Ошибка Pin/Ban: {e}")

        # 3. ПАНЕЛЬ UNPIN (Раззакрепление)
        try:
            print("🔍 Проверка раззакрепления...")
            cmd_unpin = ['gh', 'issue', 'list', '--repo', repo, '--label', 'unpin_control', '--json', 'body', '--limit', '1']
            out = safe_gh_call(cmd_unpin, token)
            data = json.loads(out)
            if data:
                to_unpin = re.findall(r'- \[x\] (vless://[^\s]+)', data[0]['body'])
                if to_unpin:
                    to_unpin_bases = [u.split("#")[0].strip() for u in to_unpin]
                    pinned_list = [s for s in pinned_list if s.split("#")[0].strip() not in to_unpin_bases]
                    with open('test1/pinned.txt', 'w', encoding='utf-8') as pf:
                        pf.write("\n".join(pinned_list) + ("\n" if pinned_list else ""))
                    print(f"🔓 Убрано из закрепов: {len(to_unpin)}")
        except Exception as e: print(f"⚠️ Ошибка Unpin: {e}")

    # --- 5. ФОРМИРОВАНИЕ ОЧЕРЕДИ ---
    raw_external = download_raw_data(EXTERNAL_SOURCE_URL)
    combined_queue = pinned_list + deferred_base + raw_external + current_base
    unique_links = []
    seen_bases = set()
    for link in combined_queue:
        base = link.split('#')[0].strip()
        if base not in seen_bases:
            unique_links.append(link)
            seen_bases.add(base)

    # --- 6. ЦИКЛ ПРОВЕРКИ ---
    working_for_sub = []
    working_for_base = []
    new_history = {}
    seen_ips = set()
    idx = 0
    checked_today = 0
    MAX_TO_CHECK = 300
    TARGET_ALIVE = 200

    print(f"📡 Старт: Цель {TARGET_ALIVE}, Лимит {MAX_TO_CHECK}")

    while len(working_for_sub) < TARGET_ALIVE and idx < len(unique_links) and checked_today < MAX_TO_CHECK:
        link = unique_links[idx]
        idx += 1
        base_part = link.split("#")[0].strip()
        
        # 1. Проверка на закреп
        found_pinned = next((p for p in pinned_list if base_part == p.split("#")[0].strip()), None)
        if found_pinned:
            # Закрепы переименовываем отдельно для красоты
            final_pinned = rebuild_link_name(found_pinned, f"💎 FIXED {counter}")
            working_for_sub.append(final_pinned)
            working_for_base.append(base_part)
            counter += 1
            continue
            
        # 2. Фильтры
        if base_part in blacklist: continue
        if any(x in base_part.lower() for x in ["type=ws", "type=grpc"]): continue

        # 3. ТЕСТ
        checked_today += 1
        endpoint, host, port = extract_host_port(base_part)
        if not host: continue

        is_alive = False
        try:
            resolved_ip = socket.gethostbyname(host) if not is_ipv6(host) else host
            if resolved_ip in seen_ips: continue 
    
            with socket.create_connection((resolved_ip, int(port)), timeout=stress_config["timeout"]) as sock:
                use_tls = "security=tls" in base_part.lower() or "security=reality" in base_part.lower()
                if use_tls:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    with context.wrap_socket(sock, server_hostname=host) as ssock:
                        ssock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
                        if stress_config["dpi_sleep"] > 0: time.sleep(stress_config["dpi_sleep"])
                        ssock.settimeout(1.5) 
                        if ssock.recv(1): is_alive = True
                else:
                    sock.sendall(b'\x05\x01\x00')
                    if sock.recv(2): is_alive = True
        except: is_alive = False

        if is_alive:
            seen_ips.add(resolved_ip)
            country = get_country_code(host, countries_cache)
            if country not in ALLOWED_COUNTRIES: continue

            # Чиним SNI
            sub_link = base_part
            if "sni=" not in sub_link.lower() and not is_ipv6(host):
                sep = "&" if "?" in sub_link else "?"
                sub_link += f"{sep}sni={host}"

            final_link = rebuild_link_name(sub_link, f"wifi {counter}")
            working_for_sub.append(final_link)
            working_for_base.append(base_part)
            new_history[base_part] = now
            print(f"✅ ОК {len(working_for_sub)}: {host}")
            counter += 1
        else:
            fail_time = history.get(base_part, now)
            if now - fail_time > 86400: # Сгнил
                with open('test1/blacklist.txt', 'a') as bl: bl.write(base_part + "\n")
            elif now - fail_time < 3600: # Шанс
                temp_link = rebuild_link_name(base_part, f"⏳ wifi {counter}")
                working_for_sub.append(temp_link)
                working_for_base.append(base_part)
                new_history[base_part] = fail_time
                counter += 1

    # --- 7. ФИНАЛЬНЫЙ СБОР И ЛИМИТЫ ---
    new_deferred = unique_links[idx:]
    all_pinned = [l for l in working_for_sub if "💎" in l]
    all_others = [l for l in working_for_sub if "💎" not in l]
    
    final_to_sub = []
    seen_in_final = set()
    
    # Лимит закрепов 50, добираем обычными до 200
    for l in all_pinned[:50]:
        final_to_sub.append(l)
        seen_in_final.add(l.split("#")[0].strip())
        
    for l in all_others:
        if len(final_to_sub) >= 200: break
        if l.split("#")[0].strip() not in seen_in_final:
            final_to_sub.append(l)
            seen_in_final.add(l.split("#")[0].strip())

    # --- 8. СОХРАНЕНИЕ ---
    # Формируем очередь из того, что не влезло в лимит 200
    leftover_from_others = [l for l in all_others if l.split("#")[0].strip() not in seen_in_final]
    deferred_final = new_deferred + leftover_from_others
    
    with open('test1/deferred.txt', "w", encoding="utf-8") as f:
        f.write("\n".join(deferred_final))

    # Записываем основной файл подписки (wifi.txt)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(HEADER.strip() + "\n\n" + "\n".join(final_to_sub))
        
    # Сохраняем рабочую базу для следующего запуска
    with open(INPUT_FILE, "w", encoding="utf-8") as f: 
        f.write("\n".join(working_for_base))
    
    # Сбрасываем кэши и историю
    with open(STATUS_FILE, "w") as f: json.dump(new_history, f)
    with open('test1/ranking.json', "w") as f: json.dump(ranking_db, f)
    with open(CACHE_FILE, 'w') as f: json.dump(countries_cache, f)

    print(f"🏁 Готово! В подписке: {len(final_to_sub)}")

    # --- ОБНОВЛЕНИЕ ИНТЕРФЕЙСА В GITHUB ISSUES ---
    if token and repo:
        try:
            update_time = time.strftime("%d.%m.%Y %H:%M:%S")
            env_gh = {**os.environ, "GH_TOKEN": token}

            # 1. ПАНЕЛЬ CONTROL (Blacklist)
            find_cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'control', '--json', 'number', '--limit', '1']
            out = subprocess.check_output(find_cmd, env=env_gh).decode()
            data = json.loads(out)
            
            if data:
                issue_number = str(data[0]['number'])
                issue_body = f"### 🎮 Панель управления серверами\n🕒 Последнее обновление: `{update_time}`\n\n"
                issue_body += "Отметь [x] и сохрани, чтобы отправить в черный список:\n\n---\n\n"
                
                for i, link in enumerate(working_for_base, 1):
                    status = "[x]" if link in blacklist else "[ ]"
                    issue_body += f"- {status} {link} (wifi {i})\n\n---\n\n"
                
                with open("issue_body.txt", "w", encoding="utf-8") as f: f.write(issue_body)
                subprocess.run(['gh', 'issue', 'edit', issue_number, '--repo', repo, '--body-file', 'issue_body.txt'], env=env_gh)
                print(f"📝 Панель Control #{issue_number} обновлена.")

            # --- ПАНЕЛЬ 2: КАНДИДАТЫ В ЗАКРЕП (Источник: vetted.txt) ---
            if data_pin:
                num_pin = str(data_pin[0]['number'])
                body_pin = f"### 💎 Кандидаты из vetted.txt\n🕒 Обновлено: `{update_time}`\n\n"
                body_pin += "> **Инструкция:** Эти серверы прислал другой бот. Выбери PIN для закрепа или BAN для удаления.\n\n"
                
                # Берем серверы именно из загруженного vetted.txt
                for i, link in enumerate(vetted_from_file, 1):
                    base_only = link.split("#")[0].strip()
                    
                    # Фильтр 1: Не показывать то, что УЖЕ в закрепе
                    is_pinned = any(base_only == p.split("#")[0].strip() for p in pinned_list)
                    # Фильтр 2: Не показывать то, что в черном списке
                    is_banned = base_only in blacklist
                    
                    if not is_pinned and not is_banned:
                        body_pin += f"📡 **Кандидат {i}:** `{base_only}`\n"
                        body_pin += f"- [ ] PIN_{base_only}\n"
                        body_pin += f"- [ ] BAN_{base_only}\n\n---\n\n"
                
                if not vetted_from_file:
                    body_pin += "_Список кандидатов пуст (файл vetted.txt пуст или не найден)._"

                with open("pin_body.txt", "w", encoding="utf-8") as f: f.write(body_pin)
                subprocess.run(['gh', 'issue', 'edit', num_pin, '--repo', repo, '--body-file', 'pin_body.txt'], env=env_gh)

            # 3. ПАНЕЛЬ UNPIN (Текущие закрепы)
            unpin_cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'unpin_control', '--json', 'number', '--limit', '1']
            out_unp = subprocess.check_output(unpin_cmd, env=env_gh).decode()
            data_unp = json.loads(out_unp)
            
            if data_unp:
                num_unp = str(data_unp[0]['number'])
                body_unp = f"### 👑 Ваши закрепленные сервера\n🕒 Обновлено: `{update_time}`\n\n"
                for i, link in enumerate(pinned_list, 1):
                    body_unp += f"- [ ] {link} (FIXED {i})\n\n---\n\n"
                
                with open("unpin_body.txt", "w", encoding="utf-8") as f: f.write(body_unp)
                subprocess.run(['gh', 'issue', 'edit', num_unp, '--repo', repo, '--body-file', 'unpin_body.txt'], env=env_gh)
                print(f"🔓 Панель Unpin #{num_unp} обновлена.")

        except Exception as e:
            print(f"⚠️ Ошибка при обновлении панелей GitHub: {e}")

if __name__ == "__main__":
    main()
