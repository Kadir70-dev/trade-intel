// Package cache is a low-overhead, VPS-friendly caching layer for broker
// replies, backed by Redis. It speaks the Redis RESP protocol directly over a
// stdlib net.Conn — no external Go module — so the broker binary stays a small,
// static, dependency-free build. Every operation degrades gracefully: if Redis
// is unreachable, cache reads miss and writes are no-ops, and the broker keeps
// serving uncached.
package cache

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"sync"
	"time"
)

// respConn is a single, mutex-guarded Redis connection with lazy (re)connect.
// A cheap-VPS broker is low-QPS (chat, not a hot loop) and already caps
// concurrent inferences, so one serialized connection is plenty and keeps the
// footprint tiny.
type respConn struct {
	addr    string
	timeout time.Duration
	mu      sync.Mutex
	conn    net.Conn
	rw      *bufio.ReadWriter
}

func newRespConn(addr string, timeout time.Duration) *respConn {
	if timeout <= 0 {
		timeout = 200 * time.Millisecond
	}
	return &respConn{addr: addr, timeout: timeout}
}

func (r *respConn) ensure() error {
	if r.conn != nil {
		return nil
	}
	c, err := net.DialTimeout("tcp", r.addr, r.timeout)
	if err != nil {
		return err
	}
	r.conn = c
	r.rw = bufio.NewReadWriter(bufio.NewReader(c), bufio.NewWriter(c))
	return nil
}

func (r *respConn) reset() {
	if r.conn != nil {
		_ = r.conn.Close()
	}
	r.conn = nil
	r.rw = nil
}

// do sends one command and returns the decoded reply. Any I/O error resets the
// connection (so the next call reconnects) and is returned to the caller, which
// treats it as a cache miss / no-op.
func (r *respConn) do(args ...string) (any, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := r.ensure(); err != nil {
		return nil, err
	}
	_ = r.conn.SetDeadline(time.Now().Add(r.timeout))

	if err := writeCommand(r.rw.Writer, args); err != nil {
		r.reset()
		return nil, err
	}
	if err := r.rw.Writer.Flush(); err != nil {
		r.reset()
		return nil, err
	}
	reply, err := readReply(r.rw.Reader)
	if err != nil {
		r.reset()
		return nil, err
	}
	return reply, nil
}

func (r *respConn) close() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.reset()
}

// writeCommand encodes a command as a RESP array of bulk strings.
func writeCommand(w *bufio.Writer, args []string) error {
	if _, err := fmt.Fprintf(w, "*%d\r\n", len(args)); err != nil {
		return err
	}
	for _, a := range args {
		if _, err := fmt.Fprintf(w, "$%d\r\n", len(a)); err != nil {
			return err
		}
		if _, err := w.WriteString(a); err != nil {
			return err
		}
		if _, err := w.WriteString("\r\n"); err != nil {
			return err
		}
	}
	return nil
}

// readReply decodes one RESP reply (simple string, error, integer, bulk string,
// or array). Only the subset the cache uses is exercised, but all five types
// are handled for safety.
func readReply(r *bufio.Reader) (any, error) {
	line, err := readLine(r)
	if err != nil {
		return nil, err
	}
	if len(line) == 0 {
		return nil, errors.New("empty resp line")
	}
	switch line[0] {
	case '+':
		return line[1:], nil
	case '-':
		return nil, errors.New(line[1:])
	case ':':
		return strconv.ParseInt(line[1:], 10, 64)
	case '$':
		n, err := strconv.Atoi(line[1:])
		if err != nil {
			return nil, err
		}
		if n < 0 {
			return nil, nil // nil bulk string
		}
		buf := make([]byte, n+2) // payload + trailing CRLF
		if _, err := io.ReadFull(r, buf); err != nil {
			return nil, err
		}
		return string(buf[:n]), nil
	case '*':
		n, err := strconv.Atoi(line[1:])
		if err != nil {
			return nil, err
		}
		if n < 0 {
			return nil, nil
		}
		arr := make([]any, n)
		for i := 0; i < n; i++ {
			arr[i], err = readReply(r)
			if err != nil {
				return nil, err
			}
		}
		return arr, nil
	default:
		return nil, fmt.Errorf("unknown resp type %q", line[0])
	}
}

// readLine reads up to and including \n and returns the line without the
// trailing \r\n.
func readLine(r *bufio.Reader) (string, error) {
	s, err := r.ReadString('\n')
	if err != nil {
		return "", err
	}
	s = s[:len(s)-1]
	if len(s) > 0 && s[len(s)-1] == '\r' {
		s = s[:len(s)-1]
	}
	return s, nil
}
