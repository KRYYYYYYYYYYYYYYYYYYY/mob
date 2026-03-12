import socket, time, os, ssl, re, json, subprocess, requests

# Файлы
WIFI_FILE = 'kr/mob/wifi.txt'
DEFERRED_FILE = 'test1/deferred.txt'
INPUT_FILE = 'test1/1.txt'
BLACKLIST_FILE = 'test1/blacklist.txt'
PINNED_FILE = 'test1/pinned.txt'
VETTED_FILE = 'test1/vetted.txt' # Добавил для полноты

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}

def get_country(host):
    """Определяет страну сервера (быстрая проверка)"""
    try:
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
    """Удаляет сервер из рабочих списков и из vetted.txt"""
    # Добавил VETTED_FILE, чтобы мертвяки не висели в очереди на закреп
    for path in [WIFI_FILE, DEFERRED_FILE, VETTED_FILE]: 
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            new_lines = [l for l in lines if base_part not in l]
            
            if len(lines) != len(new_lines):
                with open(path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                print(f" 🧹 Удален из {path}")

def deep_kill_check(link):
    base_part = link.split("#")[0].strip()
    if is_pinned(base_part): return True, 200 
    
    host, port = extract_host_port(base_part)
    if not host or not port: return False, 404

    headers = [
        b"GET / HTTP/1.1\r\nHost: google.com\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: apple.com\r\nUser-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X)\r\n\r\n"
    ]

    try:
        start = time.time()
        with socket.create_connection((host, int(port)), timeout=4.5) as s:
            if "security=tls" in link.lower() or "security=reality" in link.lower():
                sni_match = re.search(r'sni=([^&?#]+)', link)
                server_hostname = sni_match.group(1) if sni_match else host
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                with context.wrap_socket(s, server_hostname=server_hostname) as ssock:
                    ssock.sendall(headers[int(time.time()) % 2])
                    ssock.settimeout(2.0)
                    data = ssock.recv(10)
                    if not data: return False, 403
            else:
                s.sendall(b'\x05\x01\x00') 
            
            lat = (time.time() - start) * 1000
            if lat > 1500: return False, 1001 
            return True, 200
    except:
        return False, 404
    
def main_monitor():
    start_run = time.time()
    ranking_db = {}
    RANK_FILE = 'test1/ranking.json'
    
    if os.path.exists(RANK_FILE):
        try:
            with open(RANK_FILE, 'r', encoding='utf-8') as f:
                ranking_db = json.load(f)
        except: ranking_db = {}

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
        pinned_in_wifi = pinned_in_wifi[:50]
        
        valid_others = []
        for link in others_in_wifi:
            base = link.split("#")[0].strip()
            host, port = extract_host_port(base)
            if not host: continue

            # Печатаем хост сразу, чтобы видеть активность
            print(f"🔍 {host[:18]}...", end=" ", flush=True)

            is_ok, status_code = deep_kill_check(link)

            data = ranking_db.get(base, {"rank": 0, "fails": 0, "link": link, "geo": "??"})
            if not isinstance(data, dict): data = {"rank": data, "fails": 0, "link": link, "geo": "??"}
            
            if is_ok:
                if data.get("geo") in ["??", None, "?"]:
                    data["geo"] = get_country(host)
                
                if data["geo"] not in ALLOWED_COUNTRIES and data["geo"] != "??":
                    print(f"🌍 МИМО ({data['geo']})")
                    remove_from_all(base)
                    continue

                data["rank"] += 1
                data["fails"] = 0 
                ranking_db[base] = data
                valid_others.append(link)
                print(f"✅ ГУД ({data['rank']} | {data['geo']})")
            else:
                data["fails"] += 1
                if data["fails"] >= 3:
                    if base in ranking_db: del ranking_db[base]
                    remove_from_all(base) # <--- УДАЛЯЕМ ИЗ ФАЙЛОВ
                    if status_code == 404: 
                        add_to_blacklist(base)
                        print(f"💀 БАН (Status: {status_code})")
                    else:
                        print(f"🗑️ КИК (Тормоз/DPI)")
                else:
                    ranking_db[base] = data
                    valid_others.append(link)
                    print(f"⚠️ FAIL {data['fails']}/3 (Код: {status_code})")

        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)

        final_list = pinned_in_wifi + valid_others
        final_list = final_list[:200] 

        with open(WIFI_FILE, 'w', encoding='utf-8') as f:
            header = "# profile-title: 🏴WIFI🏴\n# profile-update-interval: 2\n\n"
            f.write(header + "\n".join(final_list))
        
        print(f"📊 ИТОГ: {len(pinned_in_wifi)} закрепов, {len(valid_others)} живых.")
        time.sleep(60)

if __name__ == "__main__":
    main_monitor()
