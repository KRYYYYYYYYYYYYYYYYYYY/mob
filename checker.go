package main

import "C"
import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/xtls/xray-core/core"
	"github.com/xtls/xray-core/infra/conf/serial"

	_ "github.com/xtls/xray-core/main/distro/all"
)

func pickFreeLocalPort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}

//export CheckVlessL7
func CheckVlessL7(cAddr *C.char, cPort int, cUuid *C.char, cSni *C.char, cPbk *C.char, cSid *C.char, cFlow *C.char, timeout int) int {
	addr := strings.TrimSpace(C.GoString(cAddr))
	uuid := strings.TrimSpace(C.GoString(cUuid))
	sni := strings.TrimSpace(C.GoString(cSni))
	pbk := strings.TrimSpace(C.GoString(cPbk))
	sid := strings.TrimSpace(C.GoString(cSid))
	flow := strings.TrimSpace(C.GoString(cFlow))
	if addr == "" || uuid == "" || sni == "" || pbk == "" || cPort <= 0 {
		return 0
	}
	if timeout <= 0 {
		timeout = 5
	}

	// 1. БЫСТРЫЙ TCP ПРОБ (из crazy_xray_checker)
	// Если порт закрыт, выходим за 500мс, не запуская Xray
	d := net.Dialer{Timeout: 500 * time.Millisecond}
	conn, err := d.Dial("tcp", net.JoinHostPort(addr, fmt.Sprintf("%d", cPort)))
	if err != nil {
		return 0
	}
	conn.Close()

	socksPort, err := pickFreeLocalPort()
	if err != nil {
		return 0
	}

	// 2. ГЕНЕРАЦИЯ КОНФИГА (добавили подавление логов)
	configJSON := fmt.Sprintf(`{
		"log": { "loglevel": "none" },
		"inbounds": [{
			"port": %d,
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
	}`, socksPort, addr, cPort, uuid, flow, sni, pbk, sid)

	if !json.Valid([]byte(configJSON)) {
		return 0
	}

	rawConfig, err := serial.DecodeJSONConfig(bytes.NewReader([]byte(configJSON)))
	if err != nil {
		return 0
	}

	serverConfig, err := rawConfig.Build()
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

	// Уменьшили паузу до 150мс (этого достаточно после TCP проба)
	time.Sleep(150 * time.Millisecond)

	// 3. ПРОВЕРКА С ЗАМЕРОМ ВРЕМЕНИ
	proxyURL, err := url.Parse(fmt.Sprintf("socks5://127.0.0.1:%d", socksPort))
	if err != nil {
		return 0
	}
	transport := &http.Transport{
		Proxy:             http.ProxyURL(proxyURL),
		DisableKeepAlives: true,
		IdleConnTimeout:   1 * time.Second,
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Transport: transport,
		Timeout:   time.Duration(timeout) * time.Second,
	}

	start := time.Now()
	probeURLs := []string{
		"https://www.gstatic.com/generate_204",
		"https://connectivitycheck.gstatic.com/generate_204",
		"http://cp.cloudflare.com/generate_204",
	}
	probeUAs := []string{
		"Happ/3.15.1 (com.happproxy; Android 16; Samsung SM-A336B)",
		"okhttp/4.12.0 v2rayNG/1.12.28",
	}
	successHits := 0
	firstSuccessLatency := 0

	for idx, probeURL := range probeURLs {
		req, err := http.NewRequest(http.MethodGet, probeURL, nil)
		if err != nil {
			continue
		}
		req.Header.Set("Accept", "*/*")
		req.Header.Set("Connection", "close")
		req.Header.Set("User-Agent", probeUAs[idx%len(probeUAs)])

		resp, err := client.Do(req)
		if err != nil {
			continue
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		resp.Body.Close()

		if resp.StatusCode == http.StatusNoContent || resp.StatusCode == http.StatusOK {
			successHits++
			if firstSuccessLatency == 0 {
				firstSuccessLatency = int(time.Since(start).Milliseconds())
			}
		}
	}
	if successHits >= 2 {
		return firstSuccessLatency
	}

	return 0
}

func main() {}
