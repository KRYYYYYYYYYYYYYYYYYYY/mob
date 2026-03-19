package main

import "C"
import (
	"bytes"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"time"

	"github.com/xtls/xray-core/core"
	"github.com/xtls/xray-core/infra/conf/serial"

	_ "github.com/xtls/xray-core/main/distro/all"
)

//export CheckVlessL7
func CheckVlessL7(cAddr *C.char, cPort int, cUuid *C.char, cSni *C.char, cPbk *C.char, cSid *C.char, cFlow *C.char, timeout int) int {
	addr := C.GoString(cAddr)
	uuid := C.GoString(cUuid)
	sni := C.GoString(cSni)
	pbk := C.GoString(cPbk)
	sid := C.GoString(cSid)
	flow := C.GoString(cFlow)

	// 1. БЫСТРЫЙ TCP ПРОБ (из crazy_xray_checker)
	// Если порт закрыт, выходим за 500мс, не запуская Xray
	d := net.Dialer{Timeout: 500 * time.Millisecond}
	conn, err := d.Dial("tcp", net.JoinHostPort(addr, fmt.Sprintf("%d", cPort)))
	if err != nil {
		return 0 
	}
	conn.Close()

	// 2. ГЕНЕРАЦИЯ КОНФИГА (добавили подавление логов)
	configJSON := fmt.Sprintf(`{
		"log": { "loglevel": "none" },
		"inbounds": [{
			"port": 10001,
			"listen": "127.0.0.1",
			"protocol": "socks",
			"settings": { "auth": "noauth", "udp": true }
		}],
		"outbounds": [{
			"protocol": "vless",
			"settings": {
				"vnext": [{
					"address": "%s",
					"port": %d,
					"users": [{ "id": "%s", "encryption": "none", "flow": "%s" }]
				}]
			},
			"streamSettings": {
				"network": "tcp",
				"security": "reality",
				"realitySettings": {
					"show": false,
					"fingerprint": "chrome",
					"serverName": "%s",
					"publicKey": "%s",
					"shortId": "%s",
					"spiderX": "/"
				}
			}
		}]
	}`, addr, cPort, uuid, flow, sni, pbk, sid)

	rawConfig, err := serial.DecodeJSONConfig(bytes.NewReader([]byte(configJSON)))
	if err != nil { return 0 }

	serverConfig, err := rawConfig.Build()
	if err != nil { return 0 }

	instance, err := core.New(serverConfig)
	if err != nil { return 0 }

	if err := instance.Start(); err != nil { return 0 }
	defer instance.Close()

	// Уменьшили паузу до 150мс (этого достаточно после TCP проба)
	time.Sleep(150 * time.Millisecond)

	// 3. ПРОВЕРКА С ЗАМЕРОМ ВРЕМЕНИ
	proxyURL, _ := url.Parse("socks5://127.0.0.1:10001")
	client := &http.Client{
		Transport: &http.Transport{
			Proxy: http.ProxyURL(proxyURL),
			DisableKeepAlives: true,
		},
		Timeout: time.Duration(timeout) * time.Second,
	}

	start := time.Now()
	// Используем Google для проверки, как в твоем коде
	resp, err := client.Get("https://www.gstatic.com/generate_204")
	if err != nil {
		return 0
	}
	defer resp.Body.Close()

	if resp.StatusCode == 204 {
		// Возвращаем пинг в миллисекундах
		return int(time.Since(start).Milliseconds())
	}

	return 0
}

func main() {}
