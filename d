{
  "remarks": "🚀 ГЛОБАЛЬНЫЙ АВТОВЫБОР (Все сервера)",
  "log": {
    "loglevel": "warning"
  },
  "dns": {
    "fallbackStrategy": "disabledIfAnyMatch",
    "servers":
  },
  "inbounds": [
    {
      "listen": "127.0.0.1",
      "port": 10808,
      "protocol": "socks",
      "settings": { "auth": "noauth", "udp": true },
      "tag": "socks-in",
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": true }
    },
    {
      "listen": "127.0.0.1",
      "port": 10809,
      "protocol": "http",
      "tag": "http-in",
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": true }
    }
  ],
  "outbounds": [
    {
      "protocol": "vless",
      "tag": "🇪🇪+Эстония",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 1443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "IY3FoZJxMLG_yyWJ7BkkL6cpfxypkx_XkogW0xbK-Es", "shortId": "7ba6c86c7aa8684d", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "🇳🇱+Нидерланды",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 2443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "639zNnbJNoEyG_LScz4MlkpKluImfvkkFddsk8ecjks", "shortId": "4d1548b1f5d4333d", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "🇩🇪+Германия",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 3443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "wwGR_yrkQmb5atW8fWWR376idUXqTD8CnHhoFPTb7yQ", "shortId": "d10195d5995c1790", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "🇵🇱+Польша",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 4443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "UlBTudqqHkEZ2Lc3jFr4iIXCpHv1Wu-cUn9twS-Xqms", "shortId": "1d6b2e894e42ea3e", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "🇫🇮+Финляндия",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 5443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "Sh2gI-pXPLKvZNw5C8dPW9KYbgixMSBV1OfyL9O3w0o", "shortId": "7c8352c2443d8fff", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "🇰🇿+Казахстан",
      "settings": { "vnext": [{ "address": "kz.vmelectronics.ru", "port": 4443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "BsEyhjR17kFLubYZ54twQWx1sh4wfFS3hmPvBd9K8HQ", "shortId": "da1e1d03b8d190da", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "🇷🇺+Россия",
      "settings": { "vnext": [{ "address": "ru.vmelectronics.ru", "port": 443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "CcHkkkSDp46o1h3ZokyyOY2SNxzLoPUhleW4SoFWaU8", "shortId": "438ed06c1a18f990", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "🇬🇧+Великобритания",
      "settings": { "vnext": [{ "address": "91.228.10.137", "port": 8443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "Nb6iNttJchPxyHYUfayQilmt2Fal7FHJUl0slka5kX4", "shortId": "66ca1541f047f0b0", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "🇺🇸+США",
      "settings": { "vnext": [{ "address": "us.vmelectronics.ru", "port": 5252, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "22AYOD5dsoIWsK1GQKpntnojKJHuWvL3nRAEm6ZfUVM", "shortId": "e239b3a31643cf69", "serverName": "rutube.ru" } }
    },
    {
      "protocol": "vless",
      "tag": "🇯🇵+Япония",
      "settings": { "vnext": [{ "address": "5.253.41.71", "port": 8443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "publicKey": "jWeCcoek3HYd4HshfnU3-e5l5H7IrCPEQr3F0v4U1z8", "shortId": "ed1c31cc46aa1433", "serverName": "google.com" } }
    },
    {
      "protocol": "vless",
      "tag": "vless-WS",
      "settings": { "vnext": [{ "address": "43.162.120.155", "port": 443, "users": [{ "id": "14b02e2a-8930-4afb-8412-ea4a4954ca5b", "encryption": "none" }] }] },
      "streamSettings": { "network": "ws", "security": "tls", "tlsSettings": { "serverName": "zgtwylnhh.cc.cd" }, "wsSettings": { "headers": { "Host": "zgtwylnhh.cc.cd" }, "path": "Telegram🇨🇳@WangCai2" } }
    },
    { "tag": "direct", "protocol": "freedom" },
    { "tag": "block", "protocol": "blackhole" }
  ],
  "observatory": {
    "subjectSelector": ["🇪", "🇳", "🇩", "🇵", "🇫", "🇰", "🇷", "🇬", "🇺", "🇯", "v"],
    "probeURL": "https://www.google.com/generate_204",
    "probeInterval": "1m"
  },
  "routing": {
    "domainStrategy": "AsIs",
    "balancers": [
      {
        "tag": "balancer",
        "selector": ["🇪", "🇳", "🇩", "🇵", "🇫", "🇰", "🇷", "🇬", "🇺", "🇯", "v"],
        "strategy": { "type": "leastPing" }
      }
    ],
    "rules":
  }
}
