import socket, time, os, ssl, re, json, subprocess, requests

# Файлы
WIFI_FILE = 'kr/mob/wifi.txt'
DEFERRED_FILE = 'test1/deferred.txt'
INPUT_FILE = 'test1/1.txt'
BLACKLIST_FILE = 'test1/blacklist.txt'
PINNED_FILE = 'test1/pinned.txt'

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}

def get_country(host):
    """Определяет страну сервера (быстрая проверка)"""
    try:
        # Ограничиваем таймаут, чтобы монитор не зависал на одном сервере
        resp = requests.get(f"http://ip-api.com/json/{host}?fields=status,countryCode", timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return data.get("countryCode", "??")
    except: pass
    return "??"

def extract_host_port(link):
    match = re.search(r'@([\w\.-]+):(\d+)', link)
    if not match:
        match = re.search(r'@\[([0-9a-fA-F:]+)\]:(\d+)', link)
    return (match.group(1), int(match.group(2))) if match else (None, None)

def is_pinned(base_part):
    if not os.path.exists(PINNED_FILE): return False
    with open(PINNED_FILE, 'r', encoding='utf-8') as f:
        # Читаем файл и для каждой строки берем только часть до знака #
        pinned_bases = [line.split('#')[0].strip() for line in f if 'vless://' in line]
        return base_part in pinned_bases
        
def add_to_blacklist(base_part):
    existing = set()
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, 'r') as f:
            existing = {line.strip() for line in f}
    if base_part not in existing:
        with open(BLACKLIST_FILE, 'a') as f:
            f.write(base_part + "\n")

def remove_from_all(base_part):
    # Список файлов, из которых нужно вырезать мертвый сервер
    for path in [WIFI_FILE, DEFERRED_FILE]: 
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Оставляем только те строки, где НЕТ этого сервера
            new_lines = [l for l in lines if base_part not in l]
            
            # Если что-то удалили — перезаписываем файл
            if len(lines) != len(new_lines):
                with open(path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                print(f" 🧹 [УДАЛЕНИЕ] Сервер вырезан из {path}")

def deep_kill_check(link, stress_config):
    base_part = link.split("#")[0].strip()
    if is_pinned(base_part): return True, 200 
    
    host, port = extract_host_port(base_part)
    if not host or not port: return False, 404

    # Имитируем запрос как в основном чекере
    request_data = f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\n\r\n".encode()

    try:
        start = time.time()
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
                        
                    ssock.settimeout(2.0) # Ждем ответ строго
                    data = ssock.recv(1)  # Пытаемся прочитать 1 байт
                    if not data: return False, 403
            else:
                # Для обычных соединений
                s.sendall(b'\x05\x01\x00')
                if not s.recv(2): return False, 403
            
            return True, 200
    except:
        return False, 404
    
def main_monitor():
    start_run = time.time()

    # --- ЧИТАЕМ СТРЕСС-ПРОФИЛЬ (КАК В ОСНОВНОМ БОТЕ) ---
    stress_config = {
        "timeout": 2.5,        # Дефолт, если файла нет
        "dpi_sleep": 0.1       # Дефолт
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
    
    if os.path.exists(RANK_FILE):
        try:
            with open(RANK_FILE, 'r', encoding='utf-8') as f:
                ranking_db = json.load(f)
        except: ranking_db = {}

    # Цикл работает 10 минут (600 сек)
    while time.time() - start_run < 600:
        print(f"\n🕵️ ОБХОД В {time.strftime('%H:%M:%S')}")
        
        if not os.path.exists(WIFI_FILE):
            print("📭 Файл wifi.txt не найден, жду...")
            time.sleep(60)
            continue

        with open(WIFI_FILE, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if 'vless://' in l]

        pinned_in_wifi = [l for l in lines if is_pinned(l.split("#")[0].strip())]
        others_in_wifi = [l for l in lines if not is_pinned(l.split("#")[0].strip())]
        
        # Берем только первые 50 закрепов (лимит)
        pinned_in_wifi = pinned_in_wifi[:50]
        
        valid_others = []
        for link in others_in_wifi:
            base = link.split("#")[0].strip()
            host, port = extract_host_port(base)
            
            if not host: continue

            print(f"🔍 Проверка {host[:20]}...", end=" ", flush=True)
            is_ok, status_code = deep_kill_check(link, stress_config)

            # Получаем данные или создаем новые
            data = ranking_db.get(base, {"rank": 0, "fails": 0, "link": link, "geo": "??"})
            if not isinstance(data, dict): data = {"rank": data, "fails": 0, "link": link, "geo": "??"}
            
            if is_ok:
                # 1. ПОВЫШАЕМ РЕЙТИНГ
                data["rank"] += 1
                data["fails"] = 0 # Сбрасываем ошибки, если ожил
                
                # Гео-проверка (как у тебя была)
                if data.get("geo") in ["??", None, ""]:
                    data["geo"] = get_country(host)
                
                if data["geo"] not in ALLOWED_COUNTRIES:
                    print(f"🌍 МИМО ({data['geo']})")
                    if base in ranking_db: del ranking_db[base]
                    remove_from_all(base)
                    continue

                ranking_db[base] = data
                valid_others.append(link)
                print(f"✅ +1 (Всего: {data['rank']})")

                # --- ЛОГИКА ОТПРАВКИ В RANKED ---
                # Если набрал 3 балла, он достоин попасть в список Кандидатов
                if data["rank"] >= 3:
                    VETTED_FILE = 'test1/vetted.txt'
                    # Читаем текущий vetted, чтобы не дублировать
                    vetted_content = ""
                    if os.path.exists(VETTED_FILE):
                        with open(VETTED_FILE, 'r') as vr: vetted_content = vr.read()
                    
                    if base not in vetted_content:
                        with open(VETTED_FILE, 'a', encoding='utf-8') as vf:
                            vf.write(link + "\n")
                        print(f"🚀 [RANKED CANDIDATE] Добавлен в vetted.txt")

            else:
                # 2. ШТРАФУЕМ ВМЕСТО УДАЛЕНИЯ
                data["fails"] += 1
                data["rank"] = max(0, data["rank"] - 2) # Снимаем 2 балла за провал
                
                print(f"⚠️ ПРОВАЛ (Ранг: {data['rank']} | Ошибки: {data['fails']}/3)")

                # Удаляем только если совсем "сгнил" или 3 раза подряд упал
                if data["rank"] <= 0 or data["fails"] >= 3:
                    if base in ranking_db: del ranking_db[base]
                    remove_from_all(base)
                    if status_code == 404: add_to_blacklist(base)
                    print(f"💀 УДАЛЕН ИЗ БАЗЫ")
                else:
                    ranking_db[base] = data
                    # Пока он не сдох окончательно, оставляем его в wifi.txt
                    valid_others.append(link)

        # Сохраняем прогресс рейтинга
        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)

        # Формируем итоговый список (до 200 серверов)
        final_list = pinned_in_wifi + valid_others
        final_list = final_list[:200] 

        with open(WIFI_FILE, 'w', encoding='utf-8') as f:
            # Используем твой стандартный хедер
            header = "# profile-title: 🏴Мобильный инет🏴\n# profile-update-interval: 2\n\n"
            f.write(header + "\n".join(final_list))
        
        print(f"📊 ИТОГ: {len(pinned_in_wifi)} закрепов, {len(valid_others)} живых. Жду минуту...")
        time.sleep(60)

if __name__ == "__main__":
    main_monitor()
