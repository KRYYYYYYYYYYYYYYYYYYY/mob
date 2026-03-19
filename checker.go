package main

import "C"
import (
	"fmt"
	"net/http"
	"time"

	"github.com/xtls/xray-core/common/net"
	"github.com/xtls/xray-core/core"
	"github.com/xtls/xray-core/infra/conf"

	// Интеграция всех протоколов
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
					"port":    uint16(cPort),
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
			Network:  conf.TransportProtocolPtr("tcp"),
			Security: "reality",
			REALITYSettings: &conf.RealityConfig{
				Show:        false,
				Fingerprint: "chrome",
				ServerName:  sni,
				PublicKey:   pbk,
				ShortId:     sid,
				SpiderX:     "/",
			},
		},
	}

	// Внутренний Socks5 вход
	listenAddr := conf.NewAddress(net.ParseAddress("127.0.0.1"))
	socksInbound := &conf.InboundDetourConfig{
		Protocol: "socks",
		Listen:   listenAddr,
		PortList: &conf.PortRange{From: 10001, To: 10001},
		Settings: &conf.ConfigRaw{
			"auth": "noauth",
			"udp":  false,
		},
	}

	cfg := &conf.Config{
		InboundConfigs:  []conf.InboundDetourConfig{*socksInbound},
		OutboundConfigs: []conf.OutboundDetourConfig{*vlessOutbound},
	}

	serverConfig, err := cfg.Build()
	if err != nil {
		return 0
	}

	instance, err := core.New(serverConfig)
	if err != nil {
		return 0
	}

	if err := instance.Start(); err != nil {
		return 0
	}
	defer instance.Close()

	// Ожидание инициализации ядра
	time.Sleep(200 * time.Millisecond)

	// Настройка прокси для HTTP клиента
	proxyUrl, _ := http.ProxyFromEnvironment(nil)
	proxyUrl, _ = proxyUrl.Parse("socks5://127.0.0.1:10001")

	client := &http.Client{
		Transport: &http.Transport{
			Proxy:             http.ProxyURL(proxyUrl),
			DisableKeepAlives: true,
		},
		Timeout: time.Duration(timeout) * time.Second,
	}

	// Финальная проверка L7
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
