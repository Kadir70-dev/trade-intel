// Package session provides in-memory, concurrency-safe conversation memory for
// the broker chat. Each session keeps a bounded ring of recent turns so the
// LLM has context across a multi-turn negotiation without unbounded RAM growth
// (cheap-VPS target). Sessions are ephemeral by design; durable history can be
// layered on later via the Python DB.
package session

import (
	"sync"
	"time"
)

// Turn is one exchange in a conversation.
type Turn struct {
	Query  string
	Answer string
	At     time.Time
}

// Session is a single chat's bounded memory.
type Session struct {
	ID        string
	turns     []Turn
	maxTurns  int
	createdAt time.Time
	updatedAt time.Time
}

// History renders recent turns as compact lines for prompt context.
func (s *Session) History() []string {
	out := make([]string, 0, len(s.turns)*2)
	for _, t := range s.turns {
		out = append(out, "Trader: "+t.Query)
		if t.Answer != "" {
			out = append(out, "Broker: "+t.Answer)
		}
	}
	return out
}

// Store is a thread-safe map of sessions with a bounded per-session history.
type Store struct {
	mu       sync.Mutex
	sessions map[string]*Session
	maxTurns int
}

// NewStore builds a Store keeping maxTurns turns per session.
func NewStore(maxTurns int) *Store {
	if maxTurns < 1 {
		maxTurns = 1
	}
	return &Store{sessions: make(map[string]*Session), maxTurns: maxTurns}
}

// Get returns the session for id, creating it on first use. The passed `now`
// keeps the store testable and free of hidden clock reads.
func (s *Store) Get(id string, now time.Time) *Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[id]
	if !ok {
		sess = &Session{ID: id, maxTurns: s.maxTurns, createdAt: now, updatedAt: now}
		s.sessions[id] = sess
	}
	return sess
}

// History returns a snapshot of the session's recent turns (safe to read while
// other sessions are being written).
func (s *Store) History(id string, now time.Time) []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[id]
	if !ok {
		return nil
	}
	return sess.History()
}

// Append records a completed turn, evicting the oldest if over capacity.
func (s *Store) Append(id, query, answer string, now time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[id]
	if !ok {
		sess = &Session{ID: id, maxTurns: s.maxTurns, createdAt: now}
		s.sessions[id] = sess
	}
	sess.turns = append(sess.turns, Turn{Query: query, Answer: answer, At: now})
	if len(sess.turns) > sess.maxTurns {
		sess.turns = sess.turns[len(sess.turns)-sess.maxTurns:]
	}
	sess.updatedAt = now
}

// Reset clears a session's history (keeps the entry).
func (s *Store) Reset(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if sess, ok := s.sessions[id]; ok {
		sess.turns = nil
	}
}

// Count returns the number of live sessions (diagnostics).
func (s *Store) Count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.sessions)
}
