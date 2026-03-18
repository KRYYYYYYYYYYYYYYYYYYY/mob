package main

import "C"
import (
	"fmt"
	"net"
	"strings"
	"time"

	utls "github.com/refraction-networking/utls"
)

//export CheckReality
func CheckReality(cHost *C.char, port int, cSni *C.char, timeout int) int {
	host := C.GoString(cHost)
	sni := C.GoString(cSni)
	addr := fmt.Sprintf("%s:%d", host, port)

	// 1. TCP коннект
	conn, err := net.DialTimeout("tcp", addr, time.Duration(timeout)*time.Second)
	if err != nil {
		return 0
	}
	defer conn.Close()

	config := &utls.Config{
		ServerName:         sni,
		InsecureSkipVerify: true, // Мы сами проверим имя ниже
	}

	uconn := utls.UClient(conn, config, utls.HelloChrome_Auto)
	uconn.SetDeadline(time.Now().Add(time.Duration(timeout) * time.Second))

	// 2. Выполняем Handshake
	err = uconn.Handshake()
	if err != nil {
		return 0
	}

	// --- УЛУЧШЕНИЕ №1: ПРОВЕРКА СЕРТИФИКАТА ---
	// Проверяем, что сервер отдал сертификат именно на тот домен, который в SNI.
	// Если мы просим google.com, а нам дают x5.ru — это фейк.
	state := uconn.ConnectionState()
	if len(state.PeerCertificates) > 0 {
		cert := state.PeerCertificates[0]
		certName := cert.Subject.CommonName
		// Если в сертификате нет нашего SNI — это левый сервер
		if !strings.Contains(strings.ToLower(certName), strings.ToLower(sni)) {
			// Проверяем также альтернативные имена (SANs)
			found := false
			for _, altName := range cert.DNSNames {
				if strings.Contains(strings.ToLower(altName), strings.ToLower(sni)) {
					found = true
					break
				}
			}
			if !found {
				return 0 // Сертификат не совпадает с маскировкой!
			}
		}
	}

	// --- УЛУЧШЕНИЕ №2: ПРОВЕРКА НА МОЛЧАНИЕ (Anti-Bot) ---
	// Даем серверу короткое окно. Если он начнет "болтать" первым — это сайт.
	uconn.SetReadDeadline(time.Now().Add(250 * time.Millisecond))
	buf := make([]byte, 1)
	n, err := uconn.Read(buf)
	
	if n > 0 {
		return 0 // Сервер слишком разговорчив для Reality
	}
	
	if err != nil {
		if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
			// Это идеальный сценарий: сервер промолчал и дождался нашего таймаута
			return 1
		}
		// Если соединение закрылось (EOF) — это был обычный сайт
		return 0
	}

	return 1
}

func main() {}
