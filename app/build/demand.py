"""
需求映射查表 — demand_map 表播种 + 需求→硬件门槛查表。

D5 核心：game + resolution + quality → min/rec GPU tier + CPU tier + VRAM + RAM。
未收录游戏走 __generic__ 回退档位。
"""

import json
import logging
import sqlite3

from . import DemandHit, FALLBACK_TIERS

logger = logging.getLogger(__name__)

# 内置游戏映射（播种用，幂等）
_GAME_SEEDS = [
    {
        "game": "生化危机9",
        "aliases": ["re9", "biohazard9", "resident evil 9", "residentevil9"],
        "rows": [
            ("1080p", "medium", 60, 80, 100, 70, 90, 8, 16, "参考 RE Engine 同类 3A 2K 需求推定"),
            ("1080p", "high", 60, 110, 135, 100, 120, 10, 16, ""),
            ("2k", "high", 60, 145, 170, 120, 140, 12, 32, "预计 2K 高画质 60fps+"),
            ("4k", "ultra", 60, 200, 220, 160, 180, 20, 32, "4K 极致 60fps 需旗舰卡"),
        ],
    },
    {
        "game": "黑神话悟空",
        "aliases": ["黑神话", "黑神话：悟空", "black myth wukong", "wukong", "bmw"],
        "rows": [
            ("1080p", "medium", 60, 80, 105, 70, 90, 8, 16, "UE5 引擎，参考官方推荐配置"),
            ("1080p", "high", 60, 115, 140, 100, 120, 10, 16, ""),
            ("2k", "high", 60, 155, 180, 130, 150, 12, 32, ""),
            ("4k", "high", 60, 200, 230, 160, 180, 16, 32, ""),
        ],
    },
    {
        "game": "赛博朋克2077",
        "aliases": ["cyberpunk 2077", "cyberpunk2077", "2077", "赛博朋克"],
        "rows": [
            ("1080p", "medium", 60, 80, 100, 70, 90, 8, 16, "REDengine，光追另议"),
            ("1080p", "high", 60, 120, 145, 100, 125, 10, 16, ""),
            ("2k", "high", 60, 160, 185, 130, 150, 12, 32, ""),
            ("4k", "high", 60, 210, 240, 160, 180, 16, 32, ""),
        ],
    },
    {
        "game": "CS2",
        "aliases": ["cs2", "counter-strike 2", "counter strike 2", "csgo2"],
        "rows": [
            ("1080p", "low", 144, 50, 70, 60, 80, 4, 16, "竞技 FPS，CPU 优先，高帧取向"),
            ("1080p", "high", 240, 70, 90, 80, 100, 6, 16, "高刷电竞屏需求"),
            ("2k", "high", 144, 90, 110, 90, 110, 8, 16, ""),
        ],
    },
    {
        "game": "APEX英雄",
        "aliases": ["apex", "apex legends", "apex英雄"],
        "rows": [
            ("1080p", "medium", 144, 60, 80, 70, 90, 6, 16, "Source 引擎，竞技向"),
            ("1080p", "high", 144, 80, 100, 80, 100, 8, 16, ""),
            ("2k", "high", 144, 100, 120, 90, 110, 8, 16, ""),
        ],
    },
    {
        "game": "原神",
        "aliases": ["genshin impact", "genshin", "genshinimpact", "yuanshen"],
        "rows": [
            ("1080p", "medium", 60, 40, 55, 50, 70, 4, 16, "Unity，优化好，门槛低"),
            ("1080p", "high", 60, 50, 70, 60, 80, 6, 16, ""),
            ("2k", "high", 60, 70, 90, 70, 90, 8, 16, ""),
            ("4k", "high", 60, 100, 120, 90, 110, 10, 16, ""),
        ],
    },
]

# 回退档位种子
_GENERIC_SEEDS = [
    ("__generic__", "1080p", "medium", None, 80, 100, 70, 90, 8, 16, "通用回退：1080p 中画质"),
    ("__generic__", "1080p", "high", None, 110, 135, 100, 120, 10, 16, "通用回退：1080p 高 / 2K 中"),
    ("__generic__", "2k", "high", None, 145, 170, 120, 140, 12, 32, "通用回退：2K 高画质"),
    ("__generic__", "4k", "high", None, 180, 200, 140, 160, 16, 32, "通用回退：4K 高 / 极致"),
]


def seed_demand_map(db_path: str) -> int:
    """幂等播种 demand_map 表。返回新增行数。"""
    conn = sqlite3.connect(db_path)
    count = 0

    all_seeds = list(_GENERIC_SEEDS)
    for g in _GAME_SEEDS:
        for row in g["rows"]:
            resolution, quality, fps, min_gpu, rec_gpu, min_cpu, rec_cpu, min_vram, min_ram, note = row
            all_seeds.append((
                g["game"], resolution, quality, fps,
                min_gpu, rec_gpu, min_cpu, rec_cpu, min_vram, min_ram, note,
            ))

    for seed in all_seeds:
        if len(seed) == 8:
            # __generic__ row
            game, resolution, quality, fps, min_gpu, rec_gpu, min_cpu, rec_cpu, min_vram, min_ram, note = seed
        else:
            # specific game row
            game, resolution, quality, fps, min_gpu, rec_gpu, min_cpu, rec_cpu, min_vram, min_ram, note = seed

        # 幂等：不存在才插入
        exists = conn.execute(
            "SELECT 1 FROM demand_map WHERE game=? AND resolution=? AND quality=?",
            (game, resolution, quality),
        ).fetchone()
        if exists:
            continue

        aliases = None
        for g in _GAME_SEEDS:
            if g["game"] == game:
                aliases = json.dumps(g["aliases"], ensure_ascii=False)
                break

        conn.execute(
            """INSERT INTO demand_map
               (game, aliases, resolution, quality, fps_target,
                min_cpu_tier, rec_cpu_tier, min_gpu_tier, rec_gpu_tier,
                min_vram_gb, min_ram_gb, note)
               VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?)""",
            (game, aliases, resolution, quality, fps,
             min_cpu, rec_cpu, min_gpu, rec_gpu, min_vram, min_ram, note),
        )
        count += 1

    conn.commit()
    conn.close()
    if count:
        logger.info("Seeded %d demand_map rows", count)
    return count


def _normalize_game(game: str) -> str:
    """游戏名归一小写。"""
    return game.strip().lower()


def lookup_demand(
    db_path: str,
    game: str | None,
    resolution: str | None,
    quality: str | None,
) -> DemandHit:
    """查 demand_map，未命中走回退档位。

    Args:
        db_path: SQLite 数据库路径。
        game: 游戏名（可 None，直接走回退）。
        resolution: 分辨率 (1080p / 2k / 4k)。
        quality: 画质 (low / medium / high / ultra)。

    Returns:
        DemandHit，source 标记命中类型。
    """
    res = (resolution or "1080p").lower().strip()
    qual = (quality or "medium").lower().strip()

    # 分辨率归一
    res_map = {"1080p": "1080p", "2k": "2k", "4k": "4k", "1440p": "2k", "2160p": "4k"}
    res = res_map.get(res, "1080p")

    # 画质归一
    qual_map = {"low": "medium", "medium": "medium", "high": "high", "ultra": "high",
                "中": "medium", "高": "high", "低": "low", "极致": "high",
                "中高": "high", "中低": "medium"}
    qual = qual_map.get(qual, "medium")

    conn = sqlite3.connect(db_path)

    hit = None

    if game:
        game_norm = _normalize_game(game)
        # 1. 精确 game 匹配
        hit = conn.execute(
            """SELECT * FROM demand_map
               WHERE LOWER(game)=? AND resolution=? AND quality=?
               LIMIT 1""",
            (game_norm, res, qual),
        ).fetchone()

        # 2. 别名匹配
        if not hit:
            all_rows = conn.execute(
                "SELECT * FROM demand_map WHERE aliases IS NOT NULL"
            ).fetchall()
            for row in all_rows:
                try:
                    aliases = json.loads(row[2])
                except (json.JSONDecodeError, TypeError):
                    continue
                if game_norm in [a.lower() for a in aliases]:
                    # 找到别名匹配的游戏，取对应分辨率+画质行
                    hit = conn.execute(
                        """SELECT * FROM demand_map
                           WHERE game=? AND resolution=? AND quality=?
                           LIMIT 1""",
                        (row[1], res, qual),
                    ).fetchone()
                    if hit:
                        break

    # 3. 回退到 __generic__
    if not hit:
        hit = conn.execute(
            """SELECT * FROM demand_map
               WHERE game='__generic__' AND resolution=? AND quality=?
               LIMIT 1""",
            (res, qual),
        ).fetchone()
        if hit:
            conn.close()
            return DemandHit(
                source="fallback",
                game=game or "__generic__",
                resolution=res,
                quality=qual,
                min_gpu_tier=hit[8],
                rec_gpu_tier=hit[9],
                min_cpu_tier=hit[6],
                rec_cpu_tier=hit[7],
                min_vram_gb=hit[10],
                min_ram_gb=hit[11],
                note=hit[12] or "通用回退",
            )

    if hit:
        conn.close()
        return DemandHit(
            source="map" if game else "fallback",
            game=hit[1],
            resolution=res,
            quality=qual,
            min_gpu_tier=hit[8],
            rec_gpu_tier=hit[9],
            min_cpu_tier=hit[6],
            rec_cpu_tier=hit[7],
            min_vram_gb=hit[10],
            min_ram_gb=hit[11],
            note=hit[12] or "",
        )

    # 4. 连 generic 也没有 → 硬编码兜底
    conn.close()
    fallback = FALLBACK_TIERS.get((res, qual), FALLBACK_TIERS[("1080p", "medium")])
    return DemandHit(
        source="fallback_unknown",
        game=game or "unknown",
        resolution=res,
        quality=qual,
        min_gpu_tier=fallback["min_gpu"],
        rec_gpu_tier=fallback["rec_gpu"],
        min_cpu_tier=fallback["min_cpu"],
        rec_cpu_tier=fallback["rec_gpu"],  # CPU rec 带 GPU rec 同值
        min_vram_gb=fallback["min_vram"],
        min_ram_gb=fallback["min_ram"],
        note="硬编码兜底",
    )
