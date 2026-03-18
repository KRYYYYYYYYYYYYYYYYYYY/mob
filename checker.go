package main

import "C"
import (
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

	// 2. Настройка конфига ЧЕРЕЗ utls (важно!)
	// Мы убрали "crypto/tls" из импорта, чтобы не было конфликта типов
	config := &utls.Config{
		ServerName:         sni,
		InsecureSkipVerify: true,
	}

	// Создаем клиента с отпечатком Chrome
	uconn := utls.UClient(conn, config, utls.HelloChrome_Auto)
	
	// Устанавливаем общий лимит времени
	uconn.SetDeadline(time.Now().Add(time.Duration(timeout) * time.Second))

	// 3. Выполняем Handshake
	err = uconn.Handshake()
	if err != nil {
		// Если Reality-сервер не принял наш "отпечаток", он разорвет связь здесь
		return 0
	}

	return 1 
}

func main() {}
