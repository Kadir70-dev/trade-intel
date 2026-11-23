package cache

import (
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

// Cache wraps the Redis-backed broker reply cache. When disabled or unreachable
// it behaves as a transparent no-op so callers never need to special-case it.
type Cache struct {
	conn       *respConn
	enabled    bool
	ttlUrgent  time.Duration
	ttlNormal  time.Duration
	ttlInsight time.Duration
	ver        atomic.Int64 // invalidation namespace; bumped on inventory update
}

// Config carries the cache knobs (mirrors the env-driven worker config).
type Config struct {
	Enabled    bool
	Addr       string
	Timeout    time.Duration
	TTLUrgent  time.Duration
	TTLNormal  time.Duration
	TTLInsight time.Duration
}

// New builds a Cache. If disabled, returns a no-op cache. Otherwise it dials
// Redis once to seed the invalidation version; a failed dial still returns an
// enabled cache that simply misses until Redis comes back (graceful).
func New(cfg Config) *Cache {
	c := &Cache{
		enabled:    cfg.Enabled,
		ttlUrgent:  orDur(cfg.TTLUrgent, 5*time.Minute),
		ttlNormal:  orDur(cfg.TTLNormal, 15*time.Minute),
		ttlInsight: orDur(cfg.TTLInsight, 30*time.Minute),
	}
	if !cfg.Enabled {
		return c
	}
	c.conn = newRespConn(cfg.Addr, cfg.Timeout)
	// Seed the namespace version from Redis so a warm cache survives broker
	// restarts; absent key => version 0.
	if rep, err := c.conn.do("GET", "broker:cachever"); err == nil {
		if s, ok := rep.(string); ok {
			if n, perr := strconv.ParseInt(s, 10, 64); perr == nil {
				c.ver.Store(n)
			}
		}
	}
	return c
}

func orDur(d, def time.Duration) time.Duration {
	if d <= 0 {
		return def
	}
	return d
}

// Enabled reports whether caching is configured on.
func (c *Cache) Enabled() bool { return c != nil && c.enabled }

// Healthy reports whether Redis currently answers PING.
func (c *Cache) Healthy() bool {
	if !c.Enabled() {
		return false
	}
	rep, err := c.conn.do("PING")
	if err != nil {
		return false
	}
	s, _ := rep.(string)
	return strings.EqualFold(s, "PONG")
}

// Key builds the namespaced cache key for a query signature + model. The
// version prefix gives O(1) invalidation: bumping the version orphans every
// prior key (which then expires by TTL) with no SCAN/DEL.
func (c *Cache) Key(sig, model string) string {
	var b strings.Builder
	b.WriteString("broker:c")
	b.WriteString(strconv.FormatInt(c.ver.Load(), 10))
	b.WriteString(":")
	b.WriteString(model)
	b.WriteString(":")
	b.WriteString(sig)
	return b.String()
}

// Get returns the cached value and true on hit; ("", false) on miss, disabled,
// or any Redis error (graceful).
func (c *Cache) Get(key string) (string, bool) {
	if !c.Enabled() {
		return "", false
	}
	rep, err := c.conn.do("GET", key)
	if err != nil || rep == nil {
		return "", false
	}
	s, ok := rep.(string)
	return s, ok
}

// SetEx stores value at key with a TTL. Errors are swallowed (best-effort).
func (c *Cache) SetEx(key, value string, ttl time.Duration) {
	if !c.Enabled() {
		return
	}
	secs := int(ttl.Seconds())
	if secs < 1 {
		secs = 1
	}
	_, _ = c.conn.do("SET", key, value, "EX", strconv.Itoa(secs))
}

// Invalidate bumps the namespace version (INCR in Redis, mirrored in-process),
// so all currently-cached broker replies are immediately orphaned. Called when
// inventory changes. Returns the new version (0 if disabled/failed).
func (c *Cache) Invalidate() int64 {
	if !c.Enabled() {
		return 0
	}
	rep, err := c.conn.do("INCR", "broker:cachever")
	if err != nil {
		return c.ver.Load()
	}
	n, ok := rep.(int64)
	if !ok {
		return c.ver.Load()
	}
	c.ver.Store(n)
	return n
}

// TTLFor picks the TTL for a query: shorter for urgent demand (it goes stale
// fast), longer otherwise.
func (c *Cache) TTLFor(urgent bool) time.Duration {
	if urgent {
		return c.ttlUrgent
	}
	return c.ttlNormal
}

// InsightTTL is the (longer) TTL for market-insight style responses.
func (c *Cache) InsightTTL() time.Duration { return c.ttlInsight }

// Close releases the Redis connection.
func (c *Cache) Close() {
	if c != nil && c.conn != nil {
		c.conn.close()
	}
}

// ---------------------------------------------------------------- signature

// Stopwords are filler the trader sprinkles around a request that must not
// change the cache identity ("need 20 iphone 16 pm" == "20 iphone 16 pm need").
var stopwords = map[string]bool{
	// intent / verbs
	"need": true, "needs": true, "want": true, "wanted": true, "req": true,
	"required": true, "require": true, "looking": true, "look": true, "buy": true,
	"buying": true, "buyer": true, "selling": true, "sell": true, "send": true,
	"share": true, "give": true, "get": true, "got": true, "find": true,
	"show": true, "check": true, "anyone": true, "any": true,
	// question / connective words
	"who": true, "what": true, "which": true, "where": true, "has": true,
	"have": true, "having": true, "do": true, "does": true, "can": true,
	"could": true, "would": true, "will": true, "is": true, "are": true,
	"the": true, "a": true, "an": true, "of": true, "for": true, "to": true,
	"in": true, "on": true, "at": true, "with": true, "and": true, "or": true,
	"it": true, "this": true, "that": true, "these": true, "those": true,
	"my": true, "your": true, "our": true, "their": true, "there": true,
	"here": true, "me": true, "i": true, "we": true, "you": true, "us": true,
	// quantity / stock filler
	"stock": true, "available": true, "avail": true, "availability": true,
	"pcs": true, "pc": true, "unit": true, "units": true, "nos": true,
	"qty": true, "quantity": true, "piece": true, "pieces": true,
	// politeness / chat noise
	"kindly": true, "please": true, "pls": true, "plz": true, "bro": true,
	"bhai": true, "sir": true, "dear": true, "hi": true, "hello": true,
	"hey": true, "salam": true, "salaam": true, "ok": true, "okay": true,
	"ready": true,
}

// urgencyWords flip the urgency flag (and are dropped from the product tokens).
var urgencyWords = map[string]bool{
	"urgent": true, "urgently": true, "asap": true, "rush": true, "now": true,
	"immediately": true, "today": true,
}

// QuerySignature reduces a raw query to a deterministic, fuzzy cache signature
// and an urgency flag — entirely in Go, no LLM. It is insensitive to casing,
// punctuation, word order, and filler words, but PRESERVES the product tokens,
// numbers (qty / storage), and urgency, which all change the ranking. So
// "Need iPhone 16 Pro Max urgent" and "iphone 16 pro max, need it URGENT!!"
// collapse to the same entry, while a different qty makes a distinct entry.
func QuerySignature(query string) (sig string, urgent bool) {
	low := strings.ToLower(query)
	var sb strings.Builder
	for _, r := range low {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			sb.WriteRune(r)
		} else {
			sb.WriteRune(' ')
		}
	}
	fields := strings.Fields(sb.String())

	var words []string
	var nums []string
	for _, f := range fields {
		if urgencyWords[f] {
			urgent = true
			continue
		}
		if stopwords[f] {
			continue
		}
		if isAllDigits(f) {
			nums = append(nums, f)
			continue
		}
		words = append(words, f)
	}
	sort.Strings(words)
	sort.Strings(nums)
	// Dedup adjacent (cheap) so "max max" can't shift the key.
	words = dedup(words)
	sig = strings.Join(words, " ") + "|" + strings.Join(nums, " ")
	if urgent {
		sig += "|u"
	}
	return sig, urgent
}

func isAllDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func dedup(in []string) []string {
	if len(in) < 2 {
		return in
	}
	out := in[:1]
	for _, s := range in[1:] {
		if s != out[len(out)-1] {
			out = append(out, s)
		}
	}
	return out
}
