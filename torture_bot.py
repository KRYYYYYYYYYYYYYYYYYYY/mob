import socket, time, os, ssl, re, json

# Те же настройки путей
RANK_FILE = 'test1/ranking.json'
VETTED_FILE = 'test1/vetted.txt'
THRESHOLD = 50  # Сколько баллов в Мониторе должен набрать сервер для начала пыток
HOST_PORT_RE = re.compile(
    r'@(?P<host>[A-Za-z0-9.-]+):(?P<port>\d+)'  # только домены/IPv4, без []
)

def extract_host_port(link: str) -> tuple[str | None, int | None]:
    m = HOST_PORT_RE.search(link)
    if not m:
        return None, None

    host = m.group("host")
    port_str = m.group("port")

    # Отбрасываем то, что похоже на IPv6 (на всякий случай)
    if ":" in host or "[" in host or "]" in host:
        return None, None

    try:
        port = int(port_str)
    except ValueError:
        return None, None

    # Строгая проверка диапазона порта
    if not (1 <= port <= 65535):
        return None, None

    return host, port

def build_tls_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def torture_check(link):
    host, port = extract_host_port(link)
    if not host or not port:
        return False

    is_tls = "security=tls" in link.lower() or "security=reality" in link.lower()
    sni = re.search(r"sni=([^&?#]+)", link)
    server_hostname = sni.group(1) if sni else host

    # Список разных User-Agent для маскировки под разные устройства
    user_agents = [
        b"GET / HTTP/1.1\r\nHost: google.com\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: bing.com\r\nUser-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/121.0.0.0\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: apple.com\r\nUser-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/605.1.15\r\n\r\n"
    ]

    total_attempts = 20  
    
    for i in range(total_attempts):
        try:
            # Таймаут 7 сек — даем шанс в условиях жестких помех
            with socket.create_connection((host, port), timeout=7) as s:
                if is_tls:
                    ctx = build_tls_context()
                    with ctx.wrap_socket(s, server_hostname=server_hostname) as ssock:
                        # ТЕСТ НА DPI: Каждую 5-ю попытку имитируем реальный запрос данных
                        if (i + 1) % 5 == 0:
                            payload = user_agents[i % len(user_agents)]
                            ssock.sendall(payload)
                            ssock.settimeout(3) # Ждем данные чуть дольше
                            response = ssock.recv(20) # Ждем хотя бы заголовков ответа
                            if not response:
                                raise Exception("DPI Block: Соединение есть, но данные не идут")
                else:
                    s.sendall(b'\x05\x01\x00')
                    s.settimeout(3)
                    resp = s.recv(2)
                    if not resp:
                        raise Exception("Proxy Error: Нет ответа от протокола")

            # Каждые 5 попыток пишем лог
            if (i + 1) % 5 == 0:
                print(f"    ⛓️  Пытка: {i + 1}/{total_attempts} | Трафик проходит успешно")

            # Если это не последняя попытка — отдыхаем
            if i < total_attempts - 1:
                time.sleep(60) 

        except Exception as e:
            print(f"❌ [ПРОВАЛ НА {i+1} ПОПЫТКЕ] Ошибка: {e}")
            return False

    return True
  
def load_ranking():
    if not os.path.exists(RANK_FILE): return {}
    try:
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return {}

def load_vetted():
    if not os.path.exists(VETTED_FILE): return set()
    with open(VETTED_FILE, 'r', encoding='utf-8') as f:
        # Берем только базу (до решетки), чтобы сравнивать уникальность
        return {line.split('#')[0].strip() for line in f if 'vless://' in line}

def main_torturer():
    if not os.path.exists(RANK_FILE):
        print("📭 Рейтинг пуст, пытать некого.")
        return

    ranking_db = load_ranking()
    vetted_set = load_vetted() 

    # Отбираем кандидатов
    candidates = []
    for base, data in ranking_db.items():
        # 1. Определяем ранг
        if isinstance(data, dict):
            rank = data.get("rank", 0)
            link = data.get("link", base) # Берем ссылку из словаря или саму базу
        else:
            rank = data  # Если там просто число
            link = base  # Если данных нет, сама ссылка и есть ключ (base)

        # 2. Проверяем порог
        if rank >= THRESHOLD and base not in vetted_set:
            candidates.append((base, link))

    if not candidates:
        print(f"⌛ Пока нет кандидатов с баллом >= {THRESHOLD}...")
        return

    print(f"🔥 Инквизиция начинается! На проверке {len(candidates)} кандидатов.")

    for base, full_link in candidates:
        print(f"⛓️ Пытаем {base[:30]}...")
        
        if torture_check(full_link):
            # Добавляем инфо о прохождении пыток в ссылку для vetted.txt (по желанию)
            vetted_entry = f"{full_link} # Rank: ELITE | {time.strftime('%Y-%m-%d')}"
            with open(VETTED_FILE, 'a', encoding='utf-8') as f:
                f.write(vetted_entry + "\n")
            
            # В рейтинге обнуляем ранг, но помечаем, что пытки пройдены успешно
            ranking_db[base]['rank'] = 0
            ranking_db[base]['last_torture'] = "PASS"
            print(f"🎖️ СЕРВЕР ПРОШЕЛ ПЫТКИ: Повышен до VETTED!")
        else:
            ranking_db[base]['rank'] = max(0, ranking_db[base]['rank'] - 30)
            ranking_db[base]['last_torture'] = "FAIL"
            print(f"❌ СЛОМАЛСЯ НА ПЫТКАХ. Штраф -30 баллов.")

    # Сохраняем итоги инквизиции
    with open(RANK_FILE, 'w', encoding='utf-8') as f:
        json.dump(ranking_db, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main_torturer()
