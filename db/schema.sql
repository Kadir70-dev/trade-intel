-- trade-intel — DB Schema v1
-- Target: SQLite (dev), PostgreSQL-compatible (prod).
-- This file is the design source of truth.
-- Alembic baseline migration (Phase 2 / P2.5) will be generated from the
-- SQLAlchemy models that mirror this schema.
--
-- Allowed types only:  INTEGER, TEXT, REAL, TIMESTAMP, BOOLEAN
-- No SQLite-only constructs (AUTOINCREMENT is OK — SA handles equivalent in PG).

PRAGMA foreign_keys = ON;

-- ============================================================
-- groups: where messages came from
-- ============================================================
CREATE TABLE IF NOT EXISTS groups (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  source_type TEXT NOT NULL,             -- 'whatsapp' | 'csv' | 'excel' | 'manual'
  source_path TEXT,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_groups_source_type ON groups(source_type);

-- ============================================================
-- sellers
-- ============================================================
CREATE TABLE IF NOT EXISTS sellers (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_name  TEXT NOT NULL,
  phone           TEXT,
  aliases         TEXT,                  -- JSON array of name variants
  location        TEXT,
  tags            TEXT,                  -- JSON array
  first_seen      TIMESTAMP,
  last_seen       TIMESTAMP,
  post_count      INTEGER DEFAULT 0,
  trust_score     REAL,                  -- NULL until >=200 msg threshold
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sellers_phone
  ON sellers(phone) WHERE phone IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sellers_canonical_name
  ON sellers(canonical_name);

-- ============================================================
-- buyers
-- ============================================================
CREATE TABLE IF NOT EXISTS buyers (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_name  TEXT NOT NULL,
  phone           TEXT,
  aliases         TEXT,
  location        TEXT,
  tags            TEXT,                  -- e.g. ['repeat','urgent','bulk']
  first_seen      TIMESTAMP,
  last_seen       TIMESTAMP,
  request_count   INTEGER DEFAULT 0,
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_buyers_phone
  ON buyers(phone) WHERE phone IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_buyers_canonical_name
  ON buyers(canonical_name);

-- ============================================================
-- messages
-- ============================================================
CREATE TABLE IF NOT EXISTS messages (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id           INTEGER REFERENCES groups(id),
  sender_name        TEXT,
  sender_phone       TEXT,
  raw_text           TEXT NOT NULL,
  message_ts         TIMESTAMP,
  ingested_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  intent             TEXT,                  -- buyer | seller | broker | unknown
  intent_confidence  REAL,
  language           TEXT,
  hash               TEXT NOT NULL,         -- sha256(sender + ts + raw_text)
  buyer_id           INTEGER REFERENCES buyers(id),
  seller_id          INTEGER REFERENCES sellers(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_hash ON messages(hash);
CREATE INDEX IF NOT EXISTS idx_messages_ts          ON messages(message_ts);
CREATE INDEX IF NOT EXISTS idx_messages_intent      ON messages(intent);
CREATE INDEX IF NOT EXISTS idx_messages_group       ON messages(group_id);

-- ============================================================
-- inventory (seller side — extracted SKUs)
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id          INTEGER REFERENCES messages(id),
  seller_id           INTEGER REFERENCES sellers(id),
  brand               TEXT,
  product_raw         TEXT,
  normalized_product  TEXT NOT NULL,
  variant             TEXT,
  storage             TEXT,
  color               TEXT,
  condition           TEXT,                 -- new | used | refurb | sealed | unknown
  quantity            INTEGER,
  quantity_raw        TEXT,
  price_aed           REAL,
  price_raw           TEXT,
  location            TEXT,
  posted_at           TIMESTAMP,
  is_active           BOOLEAN DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_inventory_product   ON inventory(normalized_product);
CREATE INDEX IF NOT EXISTS idx_inventory_seller    ON inventory(seller_id);
CREATE INDEX IF NOT EXISTS idx_inventory_active    ON inventory(is_active);
CREATE INDEX IF NOT EXISTS idx_inventory_posted_at ON inventory(posted_at);

-- ============================================================
-- demand (buyer side — extracted needs)
-- ============================================================
CREATE TABLE IF NOT EXISTS demand (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id          INTEGER REFERENCES messages(id),
  buyer_id            INTEGER REFERENCES buyers(id),
  brand               TEXT,
  product_raw         TEXT,
  normalized_product  TEXT NOT NULL,
  variant             TEXT,
  storage             TEXT,
  condition           TEXT,
  quantity            INTEGER,
  quantity_raw        TEXT,
  budget_aed          REAL,
  location            TEXT,
  urgency             TEXT,                 -- high | medium | low
  posted_at           TIMESTAMP,
  is_active           BOOLEAN DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_demand_product   ON demand(normalized_product);
CREATE INDEX IF NOT EXISTS idx_demand_buyer     ON demand(buyer_id);
CREATE INDEX IF NOT EXISTS idx_demand_active    ON demand(is_active);
CREATE INDEX IF NOT EXISTS idx_demand_posted_at ON demand(posted_at);

-- ============================================================
-- matches
-- ============================================================
CREATE TABLE IF NOT EXISTS matches (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  demand_id           INTEGER REFERENCES demand(id),
  inventory_id        INTEGER REFERENCES inventory(id),
  match_type          TEXT,                 -- exact | similar | probable
  semantic_score      REAL,
  spec_fit_score      REAL,
  recency_multiplier  REAL,
  evidence_weight     REAL,
  final_score         REAL,
  grade               TEXT,                 -- A+ | A | B | C
  reasoning           TEXT,
  created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_matches_grade     ON matches(grade);
CREATE INDEX IF NOT EXISTS idx_matches_score     ON matches(final_score DESC);
CREATE INDEX IF NOT EXISTS idx_matches_demand    ON matches(demand_id);
CREATE INDEX IF NOT EXISTS idx_matches_inventory ON matches(inventory_id);

-- ============================================================
-- embeddings (FAISS pointer table — actual vectors live on disk)
-- ============================================================
CREATE TABLE IF NOT EXISTS embeddings (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type     TEXT NOT NULL,            -- message | inventory | demand | product
  entity_id       INTEGER NOT NULL,
  model_name      TEXT NOT NULL,
  vector_dim      INTEGER NOT NULL,
  faiss_index_id  INTEGER,                  -- position in FAISS index file
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_embeddings_entity ON embeddings(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_model  ON embeddings(model_name);

-- ============================================================
-- lead_pipeline
-- ============================================================
CREATE TABLE IF NOT EXISTS lead_pipeline (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id          INTEGER REFERENCES matches(id),
  stage             TEXT NOT NULL,          -- NEW | CONTACTED | RESPONDED | NEGOTIATING | READY | CLOSED | LOST
  priority          TEXT,                   -- P1 | P2 | P3 | P4
  close_probability REAL,
  last_action       TEXT,
  last_action_at    TIMESTAMP,
  notes             TEXT,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pipeline_stage    ON lead_pipeline(stage);
CREATE INDEX IF NOT EXISTS idx_pipeline_priority ON lead_pipeline(priority);
CREATE INDEX IF NOT EXISTS idx_pipeline_match    ON lead_pipeline(match_id);
