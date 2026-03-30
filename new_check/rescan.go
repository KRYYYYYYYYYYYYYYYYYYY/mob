package main

import (
	"bufio"
	"fmt"
	"net/url"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
)

// ---------- состояние и воркер ----------

var (
	scanMu      sync.Mutex
	scanRunning int32 // 0/1
	stopEarly   int32 // для досрочного выхода после достижения лимита
)

// локальный worker для сканирования (использует общие parseLine/checkViaXray)
func worker(jobs <-chan string, results chan<- Result, wg *sync.WaitGroup) {
	defer wg.Done()
	for line := range jobs {
		if atomic.LoadInt32(&stopEarly) == 1 {
			return
		}
		pc, _ := parseLine(line)
		r := Result{Line: line, Parsed: pc}
		reason, ok := checkViaXray(pc)
		r.OK, r.Reason = ok, reason
		results <- r
	}
}

// доступны web-серверу
func IsScanRunning() bool { return atomic.LoadInt32(&scanRunning) == 1 }

// запускает рескан в фоне; вернёт false, если уже идёт
func TriggerRescan(max int) bool {
	if !atomic.CompareAndSwapInt32(&scanRunning, 0, 1) {
		return false
	}
	go func() {
		defer atomic.StoreInt32(&scanRunning, 0)
		RunScanOnce(max)
	}()
	return true
}

// ---------- основной проход сканирования ----------

func RunScanOnce(max int) {
	scanMu.Lock()
	defer scanMu.Unlock()

	// локальный лимит
	if max > 0 {
		maxWorkCfg = max
	}

	stream, err := newStreamer()
	if err != nil {
		fmt.Println("prepare output:", err)
		return
	}
	defer stream.Close()

	var seeds []string
	// 1) сначала уже существующие резервы из wifi (если есть)
	reserves, err := readReserveLinksFromWifi(wifiFile)
	if err != nil {
		fmt.Println("read reserves:", err)
	}
	seeds = append(seeds, reserves...)

	// 2) затем основной input
	inputSeeds, err := readLines(inputFile)
	if err != nil {
		fmt.Println("open input:", err)
		return
	}
	seeds = append(seeds, inputSeeds...)
	seeds = dedupKeepOrder(seeds)

	// раскрываем URL-ы
	var all []string
	for _, s := range seeds {
		if isURL(s) {
			fmt.Println("fetch:", s)
			lines, err := fetchLines(s)
			if err != nil {
				fmt.Println(" fetch-error:", err)
				continue
			}
			all = append(all, lines...)
		} else {
			all = append(all, s)
		}
	}
	if len(all) == 0 {
		fmt.Println("no inputs")
		return
	}

	// стартуем воркеров
	if workers <= 0 {
		workers = runtime.NumCPU() * 2
	}
	jobs := make(chan string, len(all))
	results := make(chan Result, len(all))
	var wg sync.WaitGroup
	atomic.StoreInt32(&stopEarly, 0)

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go worker(jobs, results, &wg)
	}
	for _, l := range all {
		jobs <- l
	}
	close(jobs)
	go func() {
		wg.Wait()
		close(results)
	}()

	// сбор результатов
	okCount := 0
	var okList []string
	for r := range results {
		state := "FAIL"
		if r.OK {
			state = "OK"
			okList = append(okList, r.Line)
			stream.WriteWorkLine(r.Line)

			okCount++
			if maxWorkCfg > 0 && okCount >= maxWorkCfg {
				atomic.StoreInt32(&stopEarly, 1)
				fmt.Printf("limit reached: %d working configs, stopping...\n", maxWorkCfg)
				break
			}
		}
		outLine := fmt.Sprintf("%s | %s | %s", state, r.Reason, r.Line)
		fmt.Println(outLine)
		stream.WriteResultLine(outLine)
	}

	// финализация файлов
	okList = dedup(okList)
	for i, link := range okList {
		tuned := link
		mtuVal := strings.TrimSpace(os.Getenv("RESERVE_MTU_VALUE"))
		if mtuVal != "" {
			tuned = upsertQueryParam(tuned, "mtu", mtuVal)
		}
		okList[i] = reserveRename(tuned, i+1)
	}
	sort.Strings(okList)
	if len(okList) > 0 {
		_ = os.WriteFile(workingFile, []byte(strings.Join(okList, "\n")+"\n"), 0o644)
		if _, err := os.Stat(firstOKFile); os.IsNotExist(err) {
			_ = os.WriteFile(firstOKFile, []byte(okList[0]+"\n"), 0o644)
		}
		fmt.Println("wrote:", workingFile)
		fmt.Println("wrote:", firstOKFile)
	} else {
		fmt.Println("no working configs found")
	}
	fmt.Println("done. full log:", allOutFile)
}

func readLines(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []string
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		if ln := strings.TrimSpace(sc.Text()); ln != "" {
			out = append(out, ln)
		}
	}
	return out, sc.Err()
}

func readReserveLinksFromWifi(path string) ([]string, error) {
	lines, err := readLines(path)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(lines))
	for _, ln := range lines {
		lw := strings.ToLower(ln)
		if !(strings.Contains(lw, "vless://") || strings.Contains(lw, "vmess://") || strings.Contains(lw, "trojan://") || strings.Contains(lw, "ss://")) {
			continue
		}
		if strings.Contains(strings.ToUpper(ln), "RESERVE") {
			out = append(out, ln)
		}
	}
	return out, nil
}

func dedupKeepOrder(in []string) []string {
	seen := make(map[string]struct{}, len(in))
	out := make([]string, 0, len(in))
	for _, s := range in {
		if _, ok := seen[s]; ok {
			continue
		}
		seen[s] = struct{}{}
		out = append(out, s)
	}
	return out
}

func reserveRename(link string, idx int) string {
	base, frag, _ := strings.Cut(link, "#")
	name := "reserve " + strconv.Itoa(idx) + " [RESERVE]"
	if frag != "" {
		decoded, _ := url.QueryUnescape(frag)
		if strings.Contains(strings.ToUpper(decoded), "PINNED") {
			return link
		}
	}
	return base + "#" + url.QueryEscape(name)
}

func upsertQueryParam(link, key, value string) string {
	if strings.TrimSpace(key) == "" || strings.TrimSpace(value) == "" {
		return link
	}
	u, err := url.Parse(link)
	if err != nil {
		return link
	}
	q := u.Query()
	q.Set(key, value)
	u.RawQuery = q.Encode()
	return u.String()
}
