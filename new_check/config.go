package main

import "time"

// пути/файлы
var (
	inputFile    = "input.txt"
	wifiFile     = "kr/mob/wifi.txt"
	outputDir    = "new_check/result"
	allOutFile   = outputDir + "/result.txt"
	workingFile  = outputDir + "/working.txt"
	firstOKFile  = outputDir + "/first_working.txt"
	timeWifiFile = outputDir + "/time_wifi.txt"

	configJSONPath = "config.json"
)

// флаги (инициализируются в main)
var (
	workers        int
	bootWait       time.Duration
	testTimeout    time.Duration
	xrayRunBudget  time.Duration
	retrySNI       int
	enableTCPProbe bool
	maxWorkCfg     int
	serveKeep      bool // ← держать веб-сервер после завершения проверки
)

// дефолтные значения
const (
	DefaultWorkers       = 16
	DefaultRetrySNI      = 2
	DefaultBootWait      = 700 * time.Millisecond
	DefaultTestTimeout   = 2 * time.Second
	DefaultXrayRunBudget = 6 * time.Second
)

var (
	httpFetchTimeout = 10 * time.Second
	maxRemoteSize    = int64(5 * 1024 * 1024)
	testURLs         = []string{
		"http://connectivitycheck.gstatic.com/generate_204",
	}
)

type AppConfig struct {
	APIKey string `json:"api_key"`
	Bind   string `json:"bind"`
	Title  string `json:"title,omitempty"`
}
