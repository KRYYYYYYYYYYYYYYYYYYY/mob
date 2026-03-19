package main

import "C"
import (
	"bytes"
	"fmt"
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

	// Конфиг JSON (проверенная структура для v1.8.24)
	configJSON := fmt.Sprintf(`{
		"inbounds": [{
			"port": 10001,
			"listen": "127.0.0.1",
			"protocol": "socks",
			"settings": { "auth": "noauth", "udp": false }
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

	// 1. Декодируем JSON
	rawConfig, err := serial.DecodeJSONConfig(bytes.NewReader([]byte(configJSON)))
	if err != nil {
		return 0
	}

	// 2. Строим системный конфиг
	serverConfig, err := rawConfig.Build()
	if err != nil {
		return 0
	}

	// 3. Создаем инстанс ядра
	instance, err := core.New(serverConfig)
	if err != nil {
		return 0
	}

	if err := instance.Start(); err != nil {
		return 0
	}
	defer instance.Close()

	// Небольшая пауза для запуска воркеров внутри Xray
	time.Sleep(250 * time.Millisecond)

	// 4. Проверка через стандартный http.Client
	proxyURL, _ := url.Parse("socks5://127.0.0.1:10001")
	
	client := &http.Client{
		Transport: &http.Transport{
			Proxy: http.ProxyURL(proxyURL),
			DisableKeepAlives: true,
		},
		Timeout: time.Duration(timeout) * time.Second,
	}

	// Делаем реальный запрос
	resp, err := client.Get("http://cp.cloudflare.com/generate_204")
	if err != nil {
		return 0
	}
	defer resp.Body.Close()

	if resp.StatusCode == 204 {
		return 1
	}

	return 0
}

func main() {}
