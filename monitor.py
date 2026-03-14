import socket, time, os, ssl, re, json, subprocess, requests

# Файлы
WIFI_FILE = 'kr/mob/wifi.txt'
DEFERRED_FILE = 'test1/deferred.txt'
INPUT_FILE = 'test1/1.txt'
BLACKLIST_FILE = 'test1/blacklist.txt'
PINNED_FILE = 'test1/pinned.txt'

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}

# В начало файла к остальным переменным
COUNTRY_CACHE_FILE = 'test1/countries_cache.json'
country_cache = {}

# Загружаем кэш при старте
if os.path.exists(COUNTRY_CACHE_FILE):
    try:
        with open(COUNTRY_CACHE_FILE, 'r') as f:
            country_cache = json.load(f)
    except: pass

def get_country(host):
    """Определяет страну с использованием локального кэша"""
    # 1. Сначала ищем в памяти
    if host in country_cache:
        return country_cache[host]
    
    # 2. Если нет в кэше, идем в API (только если хост похож на IP/домен)
    try:
        # Не стучимся в API, если это локальный адрес или мусор
        if not host or host == "127.0.0.1": return "??"
        
        resp = requests.get(
            f"http://ip-api.com/json/{host}?fields=status,countryCode", 
            timeout=2
        )
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                code = data.get("countryCode", "??")
                # Сохраняем в кэш
                country_cache[host] = code
                # Периодически сбрасываем кэш на диск (можно делать реже, но для надежности здесь)
                with open(COUNTRY_CACHE_FILE, 'w') as f:
                    json.dump(country_cache, f)
                return code
        elif resp.status_code == 429:
            print("⚠️ Лимит запросов к IP-API исчерпан (429)")
    except: 
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
    for path in [WIFI_FILE, DEFERRED_FILE]: 
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Сравниваем только левую часть до знака #
            new_lines = [l for l in lines if l.split('#')[0].strip() != base_part]
            
            if len(lines) != len(new_lines):
                with open(path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                print(f" 🧹 [УДАЛЕНИЕ] Сервер {base_part[:20]}... вырезан из {path}")

def is_ip(host):
    """Проверяет, является ли хост IPv4 или IPv6"""
    if not host: return False
    # IPv4 или IPv6 (наличие двоеточия)
    return re.match(r'^(\d{1,3}\.){3}\d{1,3}$', host) or ":" in host

def deep_kill_check(link, stress_config, pinned_bases): # <-- Добавили pinned_bases
    base_part = link.split("#")[0].strip()
    
    # ВМЕСТО is_pinned(base_part) используем быструю проверку по памяти
    if base_part in pinned_bases: return True, 200 
    
    host, port = extract_host_port(base_part)
    if not host or not port: return False, 404

    # --- ФИЛЬТР IPv6 (БАН) ---
    if is_ipv6(host):
        print(f"🚫 [IPv6 DETECTED] {host} - отправляем в бан")
        return False, 666  # Используем свой код для идентификации IPv6

    # Твой оригинальный запрос
    request_data = f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\n\r\n".encode()

    try:
        with socket.create_connection((host, int(port)), timeout=stress_config["timeout"]) as s:
            if "security=tls" in link.lower() or "security=reality" in link.lower():
                sni_match = re.search(r'sni=([^&?#]+)', link)
                server_hostname = sni_match.group(1) if sni_match else host
                
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                
                with context.wrap_socket(s, server_hostname=server_hostname) as ssock:
                    ssock.sendall(request_data)
                    
                    if stress_config["dpi_sleep"] > 0:
                        time.sleep(stress_config["dpi_sleep"])
                        
                    ssock.settimeout(2.0) 
                    data = ssock.recv(1)  
                    if not data: return False, 403
            else:
                s.sendall(b'\x05\x01\x00')
                if not s.recv(2): return False, 403
            
            return True, 200
    except:
        return False, 404

def is_ipv6(host):
    """Проверяет, является ли хост IPv6 адресом"""
    return ":" in host
    
def main_monitor():
    start_run = time.time()

    # --- ЧИТАЕМ СТРЕСС-ПРОФИЛЬ (КАК В ОСНОВНОМ БОТЕ) ---
    stress_config = {
        "timeout": 2.5,        # Дефолт, если файла нет
        "dpi_sleep": 0.5      # Дефолт
    }
    
    if os.path.exists('test1/stress_profile.json'):
        try:
            with open('test1/stress_profile.json', 'r') as f:
                data = json.load(f)
                # Переводим ms в секунды (1800ms -> 1.8s)
                stress_config["timeout"] = data.get("max_handshake_ms", 2500) / 1000
                stress_config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
                print(f"⚙️ Профиль загружен: Таймаут {stress_config['timeout']}s")
        except: 
            print("⚠️ Ошибка профиля, использую дефолты")
    # --------------------------------------------------
    
    # --- ЗАГРУЗКА РЕЙТИНГА ---
    ranking_db = {}
    RANK_FILE = 'test1/ranking.json'
    VETTED_FILE = 'test1/vetted.txt'
    
    if os.path.exists(RANK_FILE):
        try:
            with open(RANK_FILE, 'r', encoding='utf-8') as f:
                ranking_db = json.load(f)
        except: ranking_db = {}

    # Цикл работает 10 минут (600 сек)
    while time.time() - start_run < 600:
        # --- ШАГ 1: СОЗДАЕМ "ПАМЯТЬ" ЗАКРЕПОВ ---
        pinned_bases = set()
        if os.path.exists(PINNED_FILE):
            try:
                with open(PINNED_FILE, 'r', encoding='utf-8') as f:
                    # Загружаем только чистую часть vless (до #)
                    pinned_bases = {line.split('#')[0].strip() for line in f if 'vless://' in line}
            except: pass
        
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
            is_ok, status_code = deep_kill_check(link, stress_config, pinned_bases)
            
            if is_ok:
                valid_others.append(link)

                # Безопасное обновление рейтинга
                old_data = ranking_db.get(base, 0)
                old_rank = old_data.get("rank", 0) if isinstance(old_data, dict) else old_data
                new_rank = old_rank + 1
                ranking_db[base] = {"rank": new_rank, "link": link}
                print(f"📈 {base[:20]}... +1 балл ({new_rank})")
            else:
                # Если упал — удаляем из рейтинга и из файлов
                if base in ranking_db: del ranking_db[base]
                remove_from_all(base)
                print(f"🧊 {base[:20]}... упал. Удален.")
                
                # Если это наш "забаненный" IPv6 или сервер не отвечает
                if status_code == 666:
                    add_to_blacklist(base)
                    print(f"💀 БАН (IPv6): {base[:30]}")
                elif status_code == 404:
                    add_to_blacklist(base)
                    print(f"💀 БАН (Н/Д): {base[:30]}")

        # Сохраняем рейтинг на диск
        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)

        # Формируем итоговый wifi.txt (лимит 200)
        final_list = pinned_in_wifi + valid_others
        final_list = final_list[:200]

        # --- УМНАЯ ЗАПИСЬ: СОХРАНЯЕМ ТВОЙ ОРИГИНАЛЬНЫЙ ХЕАДЕР ---
        header_to_keep = []
        if os.path.exists(WIFI_FILE):
            with open(WIFI_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith('#'):
                        header_to_keep.append(line.rstrip())
                    elif line.strip(): 
                        # Как только пошли ссылки (vless://), хедер закончился
                        break
        
        # Если хедер пустой, создаем твой полный эталонный вариант
        if not header_to_keep:
            header_to_keep = [
                "# profile-title: 🏳️Мобильный инет🏳️",
                "# remark: 🏳️Мобильный инет🏳️",
                "# announce: Подписка для использования ТОЛЬКО на мобильном интернете в условиях БЕЛЫХ СПИСКОВ!",
                "# profile-update-interval: 2",
                "# subscription-userinfo: upload=0; download=0; expire=0",
                "# shadowrocket-userinfo: upload=0; download=0; expire=0"
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
