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

	// 1. Устанавливаем TCP соединение
	conn, err := net.DialTimeout("tcp", addr, time.Duration(timeout)*time.Second)
	if err != nil {
		return 0
	}
	defer conn.Close()

	// 2. Оборачиваем в uTLS (имитируем Chrome), чтобы обмануть Reality/Vision
	config := &tls.Config{
		ServerName:         sni,
		InsecureSkipVerify: true,
	}

	uconn := utls.UClient(conn, config, utls.HelloChrome_Auto)
	
	// Устанавливаем дедлайн для хендшейка
	uconn.SetDeadline(time.Now().Add(time.Duration(timeout) * time.Second))

	// 3. Выполняем Handshake
	err = uconn.Handshake()
	if err != nil {
		// Если хендшейк сорвался — это либо не Reality, либо мы в бане
		return 0
	}

	return 1 // Жив и прошел проверку отпечатков!
}

func main() {}
