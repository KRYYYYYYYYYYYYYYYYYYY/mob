package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

// держим веб-сервер жить, пока не прервут процесс
func waitIfServeKeep(srv *httpServer) {
	if serveKeep {
		fmt.Println("web server staying up. Press Ctrl+C to stop.")
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
		<-sig
	}
	_ = srv.Shutdown()
}

func main() {
	// единый stress profile для всех чекеров
	applyStressConfigToRuntime()

	// флаги работы чекера
	flag.IntVar(&workers, "workers", workers, "number of parallel workers")
	flag.DurationVar(&bootWait, "boot-wait", bootWait, "wait after xray start")
	flag.DurationVar(&testTimeout, "test-timeout", testTimeout, "HTTP test timeout")
	flag.DurationVar(&xrayRunBudget, "xray-budget", xrayRunBudget, "per-check time budget")
	flag.IntVar(&retrySNI, "retry-sni", retrySNI, "max SNI attempts per config")
	flag.BoolVar(&enableTCPProbe, "tcp-probe", true, "fast TCP probe before starting xray")
	flag.IntVar(&maxWorkCfg, "maxworkcfg", 0, "stop after N working configs (0 = unlimited)")
	flag.BoolVar(&serveKeep, "serve-keep", false, "keep web server running after checks finish")
	flag.Parse()

	// 3. САМОЕ ГЛАВНОЕ: Просто запускаем скан напрямую.
	// Никаких 'go', никаких фонов. Программа будет работать, пока не закончит.
	fmt.Println("--- STARTING SCAN ---")
	RunScanOnce(maxWorkCfg)
	fmt.Println("--- SCAN FINISHED, SAVING RESULTS ---")
	_ = os.Stdout.Sync()

	// Программа завершится сама здесь
}
