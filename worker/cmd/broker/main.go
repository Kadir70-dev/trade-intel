// Command broker is the Go primary backend for the trade-intel AI broker
// assistant. It orchestrates the chat turn end-to-end on a cheap, low-RAM,
// CPU-only VPS:
//
//	verified DB facts (Python matcher = source of truth)
//	    -> grounded prompt
//	    -> local LLM reasoning (Ollama, qwen3:4b default, model-agnostic)
//	    -> ranked suppliers + suggested margin + ready-to-send WhatsApp message
//
// Modes:
//
//	broker chat                 interactive REPL (multi-turn memory)
//	broker ask "<request>"      one-shot turn
//	broker serve                HTTP API + SSE streaming
//	broker --healthcheck        one-shot Ollama probe (exit 0 healthy / 1 not)
//	broker --json ...           machine-readable output for chat / ask
//
// All knobs come from the environment (see internal/config); nothing here is
// hard-coded to a particular model.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/trade-intel/worker/internal/broker"
	"github.com/trade-intel/worker/internal/config"
	"github.com/trade-intel/worker/internal/facts"
	"github.com/trade-intel/worker/internal/ollama"
	"github.com/trade-intel/worker/internal/server"
	"github.com/trade-intel/worker/internal/session"
)

func main() {
	healthcheck := flag.Bool("healthcheck", false, "probe Ollama once; exit 0 if reachable, 1 otherwise")
	asJSON := flag.Bool("json", false, "emit machine-readable JSON (chat/ask)")
	workdir := flag.String("workdir", "", "project root to run the Python bridge from (default: cwd)")
	maxConc := flag.Int("max-concurrent", 2, "max concurrent inferences (serve mode)")
	flag.Parse()

	cfg := config.Load()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	llm := ollama.New(cfg.OllamaHost, cfg.Model, cfg.FallbackModel, ollama.Options{
		NumCtx:      cfg.NumCtx,
		NumPredict:  cfg.NumPredict,
		NumThread:   cfg.NumThread,
		Temperature: cfg.Temperature,
		KeepAlive:   cfg.KeepAlive,
	}, cfg.Timeout)

	if *healthcheck {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := llm.Ping(ctx); err != nil {
			fmt.Fprintf(os.Stderr, "ollama unhealthy: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("ollama ok:", cfg.OllamaHost)
		os.Exit(0)
	}

	wd := *workdir
	if wd == "" {
		wd, _ = os.Getwd()
	}
	bridge := &facts.Bridge{PythonBin: cfg.PythonBin, Script: cfg.FactsScript, WorkDir: wd}
	asst := broker.New(cfg, bridge, llm)
	store := session.NewStore(cfg.MaxHistoryTurns)

	mode := flag.Arg(0)
	switch mode {
	case "serve":
		srv := server.New(cfg, asst, llm, store, logger, *maxConc)
		if err := srv.ListenAndServe(); err != nil {
			fmt.Fprintln(os.Stderr, "server error:", err)
			os.Exit(1)
		}
	case "ask":
		query := strings.TrimSpace(strings.Join(flag.Args()[1:], " "))
		if query == "" {
			fmt.Fprintln(os.Stderr, "usage: broker ask \"<trader request>\"")
			os.Exit(2)
		}
		runOnce(asst, query, *asJSON)
	case "chat", "":
		runChat(asst, store, cfg, *asJSON)
	default:
		fmt.Fprintf(os.Stderr, "unknown mode %q (use: chat | ask | serve | --healthcheck)\n", mode)
		os.Exit(2)
	}
}

func runOnce(asst *broker.Assistant, query string, asJSON bool) {
	// No wall-clock cap here: the Ollama client bounds non-stream calls by its
	// total Timeout and streams by its idle-timeout watchdog.
	if _, err := runTurn(context.Background(), asst, query, nil, asJSON); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func runChat(asst *broker.Assistant, store *session.Store, cfg config.Config, asJSON bool) {
	const sid = "cli"
	if !asJSON {
		fmt.Printf("trade-intel chat — UAE wholesale AI broker (Go backend · model=%s · fallback=%s)\n", cfg.Model, cfg.FallbackModel)
		fmt.Println("Type a trader request. 'exit' or Ctrl-D to quit, '/reset' to clear memory.")
		fmt.Println()
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for {
		if !asJSON {
			fmt.Print("> ")
		}
		if !scanner.Scan() {
			fmt.Println()
			return
		}
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		switch strings.ToLower(line) {
		case "exit", "quit", "/quit", "/exit":
			return
		case "/reset":
			store.Reset(sid)
			if !asJSON {
				fmt.Println("[memory cleared]")
			}
			continue
		}

		now := time.Now()
		history := store.History(sid, now)
		resp, err := runTurn(context.Background(), asst, line, history, asJSON)
		if err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			continue
		}
		store.Append(sid, line, resp.Answer, now)
		if !asJSON {
			fmt.Println()
		}
	}
}

// runTurn executes one turn. In JSON mode it returns the full structured
// Response; otherwise it streams the answer live (token-by-token) — the right
// UX on a slow CPU model, and it sidesteps the non-streaming header timeout.
func runTurn(ctx context.Context, asst *broker.Assistant, query string, history []string, asJSON bool) (*broker.Response, error) {
	if asJSON {
		resp, err := asst.Answer(ctx, query, history)
		if err != nil {
			return nil, err
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(resp)
		return resp, nil
	}
	resp, err := asst.AnswerStream(ctx, query, history, func(chunk string) {
		fmt.Print(chunk)
	})
	if err != nil {
		return nil, err
	}
	fmt.Println()
	if resp.Note != "" {
		fmt.Printf("[%s]\n", resp.Note)
	}
	if resp.LLMUsed {
		fmt.Printf("· %s · %.1f tok/s\n", resp.Model, resp.TokensSec)
	}
	return resp, nil
}
