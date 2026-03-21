{
  "remarks": "🚀 ГЛОБАЛЬНЫЙ АВТОВЫБОР (14 стран)",
  "log": { "loglevel": "warning" },
  "dns": { "servers": ["1.1.1.1", "8.8.8.8"] },
  "inbounds": [
    {
      "tag": "socks-in",
      "port": 10808,
      "listen": "127.0.0.1",
      "protocol": "socks",
      "settings": { "udp": true, "auth": "noauth" },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": true }
    },
    {
      "tag": "http-in",
      "port": 10809,
      "listen": "127.0.0.1",
      "protocol": "http",
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": true }
    }
  ],
  "outbounds": [
    {
      "tag": "auto-EE",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 1443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "google.com", "shortId": "7ba6c86c7aa8684d", "publicKey": "IY3FoZJxMLG_yyWJ7BkkL6cpfxypkx_XkogW0xbK-Es", "fingerprint": "qq" } }
    },
    {
      "tag": "auto-NL",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 2443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "google.com", "shortId": "4d1548b1f5d4333d", "publicKey": "639zNnbJNoEyG_LScz4MlkpKluImfvkkFddsk8ecjks", "fingerprint": "qq" } }
    },
    {
      "tag": "auto-DE",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 3443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "google.com", "shortId": "d10195d5995c1790", "publicKey": "wwGR_yrkQmb5atW8fWWR376idUXqTD8CnHhoFPTb7yQ", "fingerprint": "qq" } }
    },
    {
      "tag": "auto-PL",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 4443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "google.com", "shortId": "1d6b2e894e42ea3e", "publicKey": "UlBTudqqHkEZ2Lc3jFr4iIXCpHv1Wu-cUn9twS-Xqms", "fingerprint": "qq" } }
    },
    {
      "tag": "auto-FI",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "mosteu.vmelectronics.ru", "port": 5443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "google.com", "shortId": "7c8352c2443d8fff", "publicKey": "Sh2gI-pXPLKvZNw5C8dPW9KYbgixMSBV1OfyL9O3w0o", "fingerprint": "qq" } }
    },
    {
      "tag": "auto-KZ",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "kz.vmelectronics.ru", "port": 4443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "google.com", "shortId": "da1e1d03b8d190da", "publicKey": "BsEyhjR17kFLubYZ54twQWx1sh4wfFS3hmPvBd9K8HQ", "fingerprint": "qq" } }
    },
    {
      "tag": "auto-UK",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "91.228.10.137", "port": 8443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "google.com", "shortId": "66ca1541f047f0b0", "publicKey": "Nb6iNttJchPxyHYUfayQilmt2Fal7FHJUl0slka5kX4", "fingerprint": "qq" } }
    },
    {
      "tag": "auto-US",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "us.vmelectronics.ru", "port": 5252, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "rutube.ru", "shortId": "e239b3a31643cf69", "publicKey": "22AYOD5dsoIWsK1GQKpntnojKJHuWvL3nRAEm6ZfUVM", "fingerprint": "firefox" } }
    },
    {
      "tag": "auto-JP",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "5.253.41.71", "port": 8443, "users": [{ "id": "cb1db21c-cadd-429c-8244-2f852b2025cb", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "google.com", "shortId": "ed1c31cc46aa1433", "publicKey": "jWeCcoek3HYd4HshfnU3-e5l5H7IrCPEQr3F0v4U1z8", "fingerprint": "qq" } }
    },
    {
      "tag": "auto-FR",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "yax3.shukafish.ru", "port": 8443, "users": [{ "id": "034f514d-a3f3-000e-a3eb-b5338e8b93fa", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "pimg.mycdn.me", "shortId": "062f17", "publicKey": "73XglR9lOaXBISd7mGgTEP60v28mz8pmCDivcDAF_WU", "fingerprint": "chrome" } }
    },
    {
      "tag": "auto-IT",
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "yax3.shukafish.ru", "port": 8443, "users": [{ "id": "034f514d-a3f3-000f-a3eb-b5338e8b93fa", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "pimg.mycdn.me", "shortId": "68", "publicKey": "73XglR9lOaXBISd7mGgTEP60v28mz8pmCDivcDAF_WU", "fingerprint": "chrome" } }
    },
    {
      "tag": "direct",
      "protocol": "freedom"
    },
    {
      "tag": "block",
      "protocol": "blackhole"
    }
  ],
  "observatory": {
    "subjectSelector": ["auto-"],
    "probeURL": "https://www.google.com/generate_204",
    "probeInterval": "1m"
  },
  "routing": {
    "domainStrategy": "AsIs",
    "balancers": [
      {
        "tag": "balancer",
        "selector": ["auto-"],
        "strategy": { "type": "leastPing" }
      }
    ],
    "rules": [
      {
        "type": "field",
        "domain": ["domain:ya.ru", "domain:yandex.ru", "domain:vk.com", "domain:gosuslugi.ru"],
        "outboundTag": "direct"
      },
      {
        "type": "field",
        "network": "tcp,udp",
        "balancerTag": "balancer"
      }
    ]
  }
}
