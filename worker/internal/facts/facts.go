// Package facts bridges the Go backend to the Python ML/NLP engine, which is
// the SOURCE OF TRUTH for supplier candidates.
//
// The Go side never re-implements semantic matching or product normalization;
// it shells out to scripts/broker_facts.py (running in the project venv) and
// parses the verified, scored candidate facts it prints as JSON. This keeps
// the "Python = ML/NLP only" boundary clean and guarantees the chat answer is
// grounded in exactly what the matcher returned — no hallucination.
package facts

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strconv"
)

// Candidate is one ranked supplier row — a verified DB fact with broker-grade
// reasoning attached by the Python ranking layer (source of truth).
type Candidate struct {
	InventoryID     int      `json:"inventory_id"`
	Seller          string   `json:"seller"`
	SellerPostCount int      `json:"seller_post_count"`
	Product         string   `json:"product"`
	Storage         *string  `json:"storage"`
	Condition       *string  `json:"condition"`
	Quantity        *int     `json:"quantity"`
	PriceAED        *float64 `json:"price_aed"`
	Location        *string  `json:"location"`
	Freshness       string   `json:"freshness"`
	Grade           string   `json:"grade"`      // broker grade A+/A/B/C
	MatchType       string   `json:"match_type"` // exact|similar|probable
	Score           float64  `json:"rank_score"` // weighted trader-priority score
	Reason          string   `json:"reason"`     // one-line business reasoning
	Reasons         []string `json:"reasons"`    // explainability bullets
	SameProduct     bool     `json:"same_product"`
	QtyFit          bool     `json:"qty_fit"`
	PriceKnown      bool     `json:"price_known"`
	Recent          bool     `json:"recent"`
}

// Result is the parsed output of the Python facts bridge.
type Result struct {
	OK        bool        `json:"ok"`
	Error     string      `json:"error"`
	Product   *string     `json:"product"`
	Quantity  *int        `json:"quantity"`
	Storage   *string     `json:"storage"`
	Urgency   *string     `json:"urgency"`
	Candidate []Candidate `json:"candidates"`
}

// Bridge invokes the Python facts script.
type Bridge struct {
	PythonBin string
	Script    string
	WorkDir   string // run from the project root so the venv + DB resolve
}

// Fetch runs the bridge for one query and returns the verified facts. A
// non-nil error means the bridge could not be run or returned unparsable
// output; callers should degrade gracefully (e.g. tell the trader the engine
// is unavailable) rather than invent data.
func (b *Bridge) Fetch(ctx context.Context, query string, topK int) (*Result, error) {
	cmd := exec.CommandContext(ctx, b.PythonBin, b.Script, query, "--k", strconv.Itoa(topK))
	if b.WorkDir != "" {
		cmd.Dir = b.WorkDir
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	runErr := cmd.Run()
	// The script prints a JSON object even on handled failure, so always try
	// to parse stdout before treating the run as fatal.
	var res Result
	if jsonErr := json.Unmarshal(bytes.TrimSpace(stdout.Bytes()), &res); jsonErr != nil {
		if runErr != nil {
			return nil, fmt.Errorf("facts bridge failed: %v (stderr: %s)", runErr, stderr.String())
		}
		return nil, fmt.Errorf("facts bridge produced unparsable output: %v (stdout: %s)", jsonErr, stdout.String())
	}
	if !res.OK {
		return nil, fmt.Errorf("facts bridge error: %s", res.Error)
	}
	return &res, nil
}
