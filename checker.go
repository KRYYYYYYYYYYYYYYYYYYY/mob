package main

import "C"
import (
	"context"
	"fmt"
	"net/http"
	"time"

	"github.com/xtls/xray-core/common/net"
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

	// Формируем конфиг через JSON - это гарантирует отсутствие ошибок типов Go
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

	// Парсим JSON в структуру конфигурации Xray
	serverConfig, err := serial.DecodeJSONConfig(context.Background(), nil, []byte(configJSON))
	if err != nil {
		return 0
	}

	// Запуск ядра
	instance, err := core.New(serverConfig)
	if err != nil {
		return 0
	}

	if err := instance.Start(); err != nil {
		return 0
	}
	defer instance.Close()

	time.Sleep(200 * time.Millisecond)

	// Настройка HTTP клиента через Socks5
	proxyURL, _ := net.ParseProxyURL("socks5://127.0.0.1:10001")
	client := &http.Client{
		Transport: &http.Transport{
			Proxy: http.ProxyURL(proxyURL),
			DisableKeepAlives: true,
		},
		Timeout: time.Duration(timeout) * time.Second,
	}

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
