package main

import "C"
import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/xtls/xray-core/common/net"
	"github.com/xtls/xray-core/core"
	"github.com/xtls/xray-core/infra/conf"

	// Эти импорты нужны, чтобы Xray внутри понимал протоколы
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

	// Настройка VLESS Reality Outbound
	vlessOutbound := &conf.OutboundDetourConfig{
		Protocol: "vless",
		Settings: &conf.ConfigRaw{
			"vnext": []interface{}{
				map[string]interface{}{
					"address": addr,
					"port":    cPort,
					"users": []interface{}{
						map[string]interface{}{
							"id":         uuid,
							"encryption": "none",
							"flow":       flow,
						},
					},
				},
			},
		},
		StreamSetting: &conf.StreamConfig{
			Network:  conf.TransportProtocol("tcp"),
			Security: "reality",
			RealitySettings: &conf.RealityConfig{
				Show:        false,
				Fingerprint: "chrome",
				ServerName:  sni,
				PublicKey:   pbk,
				ShortId:     sid,
				SpiderX:     "/",
			},
		},
	}

	// Внутренний Socks5 вход (на нем Xray будет ждать наш HTTP запрос)
	socksInbound := &conf.InboundDetourConfig{
		Protocol: "socks",
		Listen:   &conf.Address{Address: net.ParseAddress("127.0.0.1")},
		Port:     &conf.PortRange{From: 10001, To: 10001},
		Settings: &conf.ConfigRaw{
			"auth": "noauth",
			"udp":  false,
		},
	}

	cfg := &conf.Config{
		InboundConfigs:  []conf.InboundDetourConfig{*socksInbound},
		OutboundConfigs: []conf.OutboundDetourConfig{*vlessOutbound},
	}

	// Строим конфиг
	serverConfig, err := cfg.Build()
	if err != nil {
		return 0
	}

	// Запускаем ядро
	instance, err := core.New(serverConfig)
	if err != nil {
		return 0
	}

	if err := instance.Start(); err != nil {
		return 0
	}
	defer instance.Close()

	// Даем ядру 100мс, чтобы инициализировать сокеты
	time.Sleep(100 * time.Millisecond)

	// Тестируем интернет через созданный инстанс
	proxyUrl, _ := net.ParseProxyURL("socks5://127.0.0.1:10001")
	client := &http.Client{
		Transport: &http.Transport{
			Proxy: http.ProxyURL(proxyUrl),
			// Отключаем Keep-Alive, чтобы чекер не висел
			DisableKeepAlives: true, 
		},
		Timeout: time.Duration(timeout) * time.Second,
	}

	// Проверка через Cloudflare (самый быстрый и стабильный способ)
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
