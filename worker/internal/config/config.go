// Package config loads the Go broker backend's runtime configuration from the
// environment. It mirrors the knobs in src/trade_intel/config.py so the Go
// service and the Python engine stay in lock-step, while keeping the model
// fully configurable (model-agnostic architecture).
//
// Defaults target a cheap, low-RAM, CPU-only VPS: qwen3:4b primary, hermes3
// fallback, small context window, short keep-alive so the model unloads
// between bursts. Override any field via the matching env var.
package config

import (
	"os"
	"strconv"
	"time"
)

// Config is the resolved broker backend configuration.
type Config struct {
	// Ollama
	OllamaHost    string        // OLLAMA_HOST
	Model         string        // LLM_MODEL          (primary, e.g. qwen3:4b on this box)
	FallbackModel string        // LLM_FALLBACK_MODEL (e.g. hermes3)
	NumCtx        int           // LLM_NUM_CTX
	NumPredict    int           // LLM_NUM_PREDICT
	NumThread     int           // LLM_NUM_THREAD (0 = let Ollama pick)
	KeepAlive     string        // LLM_KEEP_ALIVE
	Temperature   float64       // LLM_TEMPERATURE
	Timeout       time.Duration // LLM_TIMEOUT (seconds in env)
	LLMEnabled    bool          // LLM_ENABLED

	// Python ML/NLP bridge (matcher = source of truth)
	PythonBin   string // TRADE_INTEL_PYTHON     (default .venv/bin/python)
	FactsScript string // TRADE_INTEL_FACTS_SCRIPT (default scripts/broker_facts.py)
	TopK        int    // TRADE_INTEL_TOPK

	// HTTP server
	ServerAddr string // BROKER_ADDR (host:port for `broker serve`)

	// Chat memory
	MaxHistoryTurns int // BROKER_MAX_HISTORY (turns kept per session)

	// Redis cache (Phase 1) — speeds up repeated broker queries.
	CacheEnabled    bool          // CACHE_ENABLED
	RedisAddr       string        // REDIS_ADDR
	RedisTimeout    time.Duration // REDIS_TIMEOUT_MS (short = fast graceful fallback)
	CacheTTLUrgent  time.Duration // CACHE_TTL_URGENT_SEC
	CacheTTLNormal  time.Duration // CACHE_TTL_NORMAL_SEC
	CacheTTLInsight time.Duration // CACHE_TTL_INSIGHT_SEC
}

// Load reads the environment and returns a Config tuned for a low-RAM,
// CPU-only laptop / cheap VPS. Defaults: llama3.2:3b primary (small + fast,
// no chain-of-thought), qwen3:4b optional fallback. The model is fully
// configurable — OLLAMA_MODEL (preferred) or LLM_MODEL.
func Load() Config {
	return Config{
		OllamaHost:    envStr("OLLAMA_HOST", "http://127.0.0.1:11434"),
		Model:         envStrAny([]string{"OLLAMA_MODEL", "LLM_MODEL"}, "llama3.2:3b"),
		FallbackModel: envStrAny([]string{"OLLAMA_FALLBACK_MODEL", "LLM_FALLBACK_MODEL"}, "qwen3:4b"),
		// 2048 ctx halves the KV-cache vs 4096 — snappier first token + less RAM
		// on an 8 GB box. The broker prompt (system + a few candidate rows +
		// short history) fits comfortably.
		NumCtx: envInt("LLM_NUM_CTX", 2048),
		// 320 bounds the worst-case reply time on a slow CPU box — a broker
		// turn (ranking + WhatsApp draft) fits well under this.
		NumPredict:  envInt("LLM_NUM_PREDICT", 320),
		NumThread:   envInt("LLM_NUM_THREAD", 0),
		KeepAlive:   envStr("LLM_KEEP_ALIVE", "5m"),
		Temperature: envFloat("LLM_TEMPERATURE", 0.3),
		// 240s: on a contended CPU-only laptop, prompt-eval (time-to-first-token)
		// for even a small prompt can take 1-2 min. This bounds the idle gap, not
		// per-token speed — once streaming starts, gaps are ~1s.
		Timeout:    time.Duration(envInt("LLM_TIMEOUT", 240)) * time.Second,
		LLMEnabled: envBool("LLM_ENABLED", true),

		PythonBin:   envStr("TRADE_INTEL_PYTHON", ".venv/bin/python"),
		FactsScript: envStr("TRADE_INTEL_FACTS_SCRIPT", "scripts/broker_facts.py"),
		TopK:        envInt("TRADE_INTEL_TOPK", 3),

		ServerAddr:      envStr("BROKER_ADDR", "127.0.0.1:8088"),
		MaxHistoryTurns: envInt("BROKER_MAX_HISTORY", 8),

		CacheEnabled:    envBool("CACHE_ENABLED", true),
		RedisAddr:       envStr("REDIS_ADDR", "127.0.0.1:6379"),
		RedisTimeout:    time.Duration(envInt("REDIS_TIMEOUT_MS", 200)) * time.Millisecond,
		CacheTTLUrgent:  time.Duration(envInt("CACHE_TTL_URGENT_SEC", 300)) * time.Second,
		CacheTTLNormal:  time.Duration(envInt("CACHE_TTL_NORMAL_SEC", 900)) * time.Second,
		CacheTTLInsight: time.Duration(envInt("CACHE_TTL_INSIGHT_SEC", 1800)) * time.Second,
	}
}

func envStr(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

// envStrAny returns the first set, non-empty env var among keys (in order).
func envStrAny(keys []string, def string) string {
	for _, k := range keys {
		if v, ok := os.LookupEnv(k); ok && v != "" {
			return v
		}
	}
	return def
}

func envInt(key string, def int) int {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envFloat(key string, def float64) float64 {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envBool(key string, def bool) bool {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return def
}
