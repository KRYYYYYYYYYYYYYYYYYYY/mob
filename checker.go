package main

import "C"
import (
	"crypto/tls"
	"fmt"
	"net"
	"time"

	utls "github.com/refraction-networking/utls"
)

//export CheckReality
func CheckReality(cHost *C.char, port int, cSni *C.char, timeout int) int {
	host := C.GoString(cHost)
	sni := C.GoString(cSni)
	addr := fmt.Sprintf("%s:%d", host, port)

	// 1. Быстрый коннект по TCP
	conn, err := net.DialTimeout("tcp", addr, time.Duration(timeout)*time.Second)
	if err != nil {
		return 0
	}
	defer conn.Close()

	// 2. Настройка uTLS (имитация браузера)
	config := &tls.Config{
		ServerName:         sni, // Тот самый SNI из ссылки!
		InsecureSkipVerify: true,
	}

	uconn := utls.UClient(conn, config, utls.HelloChrome_Auto)
	uconn.SetDeadline(time.Now().Add(time.Duration(timeout) * time.Second))

	// 3. Пытаемся "договориться" с Reality
	err = uconn.Handshake()
	if err != nil {
		return 0 // Сервер отклонил наш "браузерный" запрос
	}

	return 1 // Успех! Сервер признал в нас своего
}

func main() {}
