"""
SQLite schema initialization — 按 .docs/design/02-数据模型详细设计.md 建表。

Usage:
    from app.db.schema import init_db
    init_db("path/to/diypc.db")
"""

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hardware (
  pro_id        TEXT PRIMARY KEY,
  category      TEXT NOT NULL,
  manu_id       TEXT,
  brand         TEXT,
  model         TEXT NOT NULL,
  -- 价格（分位存 int RMB 元；无则 NULL）
  price_jd      INTEGER,
  price_show    INTEGER,
  price_min     INTEGER,
  price_rt      INTEGER,
  price_rt_src  TEXT,
  price_rt_at   TEXT,
  -- 性能
  tier_score    REAL,
  tier_rank     INTEGER,
  -- 其他
  popularity    INTEGER DEFAULT 0,
  specs_json    TEXT,
  active        INTEGER DEFAULT 1,
  deep_fetched_at TEXT,
  fetched_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hw_cat        ON hardware(category, active);
CREATE INDEX IF NOT EXISTS idx_hw_cat_price  ON hardware(category, price_jd);
CREATE INDEX IF NOT EXISTS idx_hw_cat_tier   ON hardware(category, tier_score);

CREATE TABLE IF NOT EXISTS compat (
  pro_id        TEXT PRIMARY KEY REFERENCES hardware(pro_id),
  category      TEXT NOT NULL,
  -- CPU
  socket        TEXT,
  tdp_w         INTEGER,
  mem_type      TEXT,
  igpu          INTEGER,
  -- 主板
  form_factor   TEXT,
  mem_slots     INTEGER,
  mb_chipset    TEXT,
  m2_slots      INTEGER,
  -- 内存
  mem_capacity_gb INTEGER,
  mem_freq      INTEGER,
  -- 显卡
  vram_gb       INTEGER,
  gpu_len_mm    INTEGER,
  gpu_power_pin TEXT,
  gpu_rec_psu_w INTEGER,
  -- 存储
  interface     TEXT,
  ss_form       TEXT,
  capacity_gb   INTEGER,
  size_inch     REAL,
  -- 电源
  rated_w       INTEGER,
  modular       TEXT,
  cert          TEXT,
  -- 散热
  cooler_type   TEXT,
  cooler_h_mm   INTEGER,
  cooler_sockets TEXT,
  -- 机箱
  case_ff       TEXT,
  max_gpu_len_mm INTEGER,
  max_cooler_h_mm INTEGER,
  radiator_support TEXT
);

CREATE INDEX IF NOT EXISTS idx_compat_cat ON compat(category);

CREATE TABLE IF NOT EXISTS perf_tier (
  pro_id     TEXT NOT NULL,
  kind       TEXT NOT NULL,
  dimension  TEXT NOT NULL,
  model      TEXT,
  score      REAL,
  rank       INTEGER,
  ratio      TEXT,
  firm       TEXT,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (pro_id, kind, dimension)
);

CREATE TABLE IF NOT EXISTS demand_map (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  game         TEXT NOT NULL,
  aliases      TEXT,
  resolution   TEXT NOT NULL,
  quality      TEXT NOT NULL,
  fps_target   INTEGER,
  min_cpu_tier REAL,
  rec_cpu_tier REAL,
  min_gpu_tier REAL,
  rec_gpu_tier REAL,
  min_vram_gb  INTEGER,
  min_ram_gb   INTEGER,
  note         TEXT
);

CREATE INDEX IF NOT EXISTS idx_demand_game ON demand_map(game, resolution, quality);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
""".strip()


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize (or migrate) the SQLite database at `db_path`. Idempotent."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    # Set initial schema version if not present
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', '1')"
    )
    conn.commit()

    # 自动播种需求映射表（幂等）
    try:
        from app.build.demand import seed_demand_map
        seed_demand_map(db_path)
    except Exception:
        pass  # 播种失败不阻断服务启动

    return conn
