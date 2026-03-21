import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from typing import Optional, Dict, List, Any, Set

logger = logging.getLogger(__name__)

_SEED_PATH = os.path.join(os.path.dirname(__file__), "action_KB.json")
_DB_PATH   = os.path.join(os.path.dirname(__file__), "action_KB.sqlite")

# Minimum confidence ratio to use a cached entry (successes / attempts)
_MIN_CONFIDENCE = 0.6
# Minimum number of attempts before we trust the confidence score
_MIN_ATTEMPTS   = 2


# ── Fingerprint ────────────────────────────────────────────────────────────

def fingerprint_screen(app_package: str, elements: list) -> str:
    """
    Structural fingerprint — ignores dynamic text content like notification
    counts, carousel titles, and recommendation labels. Only hashes
    interactive and labelled elements so the fingerprint stays stable
    across sessions even when live content changes.
    """
    sigs: list[str] = []
    for e in elements:
        t         = _attr(e, "type")
        clickable = _flag(e, "clickable")
        focusable = _flag(e, "focusable")
        has_desc  = bool(_attr(e, "content_description"))

        # Skip pure non-interactive text — almost always dynamic content
        if t in ("text", "textview") and not clickable and not focusable and not has_desc:
            continue
        # Skip unlabelled placeholders
        if t == "element" and not clickable and not focusable and not has_desc:
            continue

        # Prefer content_description — it's more stable than display text
        label = _attr(e, "content_description") or _attr(e, "text")[:20]

        flags = ""
        if clickable:          flags += "C"
        if focusable:          flags += "F"
        if _flag(e, "scrollable"): flags += "S"

        sigs.append(f"{t}:{label}:{flags}")

    sigs.sort()
    raw = f"{app_package.lower()}|{'|'.join(sigs)}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def normalise_goal(goal: str) -> str:
    g = goal.lower().strip()
    g = re.sub(r'\d{1,2}:\d{2}', 'T:T', g)
    g = re.sub(r'\d+', 'N', g)
    g = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', 'E', g)
    g = re.sub(r'\b(am|pm)\b', '_', g)
    return g


# ── Element matching ───────────────────────────────────────────────────────

def resolve_selector(selector: Dict[str, Any],
                     elements: list,
                     blacklist: Set[int] | None = None,
                     params: Dict[str, str] | None = None) -> Optional[int]:
    blacklist = blacklist or set()
    params    = params    or {}
    for e in elements:
        eid = _get_id(e)
        if eid in blacklist:
            continue
        if _match_one(selector, e, params):
            return eid
    return None


def _get_id(e) -> int:
    return e.get("element_id", -1) if isinstance(e, dict) else getattr(e, "element_id", -1)

def _attr(e, key: str) -> str:
    v = e.get(key) if isinstance(e, dict) else getattr(e, key, None)
    return (v or "").lower()

def _flag(e, key: str) -> bool:
    return bool(e.get(key) if isinstance(e, dict) else getattr(e, key, False))

def _interpolate(template: str, params: Dict[str, str]) -> str:
    for k, v in params.items():
        template = template.replace("{" + k + "}", v)
    return template


def _match_one(sel: Dict, e, params: Dict[str, str]) -> bool:
    if "element_type" in sel:
        if _attr(e, "type") != sel["element_type"].lower():
            cn = _attr(e, "class_name")
            if not cn or sel["element_type"].lower() not in cn:
                return False
    if "text" in sel:
        if _attr(e, "text") != _interpolate(sel["text"], params).lower():
            return False
    if "text_matches" in sel:
        pat = _interpolate(sel["text_matches"], params)
        if not re.search(pat, _attr(e, "text") or "", re.IGNORECASE):
            return False
    if "text_contains" in sel:
        want = _interpolate(sel["text_contains"], params).lower()
        if want not in _attr(e, "text"):
            return False
    if "content_desc_matches" in sel:
        pat = _interpolate(sel["content_desc_matches"], params)
        if not re.search(pat, _attr(e, "content_description") or "", re.IGNORECASE):
            return False
    hint_key = "hint_text" if "hint_text" in sel else "hint"
    if hint_key in sel:
        want = sel[hint_key].lower()
        if want not in (_attr(e, "hint_text") or "").lower():
            return False
    if "resource_id_contains" in sel:
        if sel["resource_id_contains"].lower() not in _attr(e, "resource_id"):
            return False
    if "clickable" in sel:
        if _flag(e, "clickable") != sel["clickable"]:
            return False
    if "editable" in sel:
        if sel["editable"] and _attr(e, "type") not in ("textfield", "edittext"):
            return False
    return True


def _glob_match(pattern: str, value: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return value.endswith(suffix) or suffix in value
    return pattern in value or value == pattern


# ── ActionKB ───────────────────────────────────────────────────────────────

class ActionKB:
    """
    Two-layer lookup backed by SQLite so learned knowledge survives restarts.

    T2A — learned entries: keyed by (screen_fingerprint, goal_norm).
          Written to DB after every successful LLM action.
          Has success/failure counts so bad entries can be demoted.

    T2B — seed patterns: loaded from action_KB.json at init.
          These are never modified at runtime.

    Both layers are mirrored into a plain dict in RAM at startup so
    every lookup is a sub-millisecond dictionary access, never a DB query
    during task execution. The DB is only written to after a task completes.
    """

    def __init__(self, db_path: str = _DB_PATH, seed_path: str = _SEED_PATH):
        self._db_path   = db_path
        self._seed_path = seed_path

        # In-RAM mirrors — lookups hit these, never the DB directly
        self._t2a: Dict[str, Dict[str, Any]] = {}   # fingerprint:goal_norm → entry
        self._t2b: List[Dict[str, Any]]      = []   # seed patterns

        # Pending writes — accumulated during a task, flushed at the end
        self._pending_writes: List[Dict[str, Any]] = []
        self._pending_updates: List[Dict[str, Any]] = []

        # Stats
        self.tier2a_hits    = 0
        self.tier2b_hits    = 0
        self.misses         = 0
        self.entries_learned = 0

        self._init_db()
        self._load_all()

    # ── Init ──────────────────────────────────────────────────────────────

    def _init_db(self):
        """Create tables if they don't exist yet."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_actions (
                    cache_key    TEXT PRIMARY KEY,
                    app_package  TEXT,
                    goal_norm    TEXT,
                    action_json  TEXT NOT NULL,
                    selector_json TEXT,
                    successes    INTEGER DEFAULT 1,
                    failures     INTEGER DEFAULT 0,
                    last_used    REAL,
                    created_at   REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pkg ON learned_actions(app_package)")
            conn.commit()

    def _load_all(self):
        """Load everything into RAM at startup. Called once."""
        # T2B — seed patterns from JSON
        if os.path.exists(self._seed_path):
            try:
                with open(self._seed_path) as f:
                    data = json.load(f)
                self._t2b = data.get("seed_entries", [])
                logger.info(f"📚 ActionKB loaded {len(self._t2b)} seed patterns")
            except Exception as e:
                logger.error(f"❌ Failed to load seed data: {e}")

        # T2A — learned entries from SQLite
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT cache_key, action_json, selector_json, successes, failures "
                    "FROM learned_actions"
                ).fetchall()

            loaded = 0
            for cache_key, action_json, selector_json, successes, failures in rows:
                attempts = successes + failures
                # Skip entries that have proven unreliable
                if attempts >= _MIN_ATTEMPTS:
                    confidence = successes / attempts
                    if confidence < _MIN_CONFIDENCE:
                        continue
                entry = json.loads(action_json)
                if selector_json:
                    entry["_selector"] = json.loads(selector_json)
                entry["_successes"] = successes
                entry["_failures"]  = failures
                self._t2a[cache_key] = entry
                loaded += 1

            logger.info(f"📚 ActionKB loaded {loaded} learned entries from DB "
                        f"({len(rows) - loaded} pruned for low confidence)")
        except Exception as e:
            logger.error(f"❌ Failed to load learned entries: {e}")

    # ── Lookup ────────────────────────────────────────────────────────────

    def lookup(
        self,
        app_package: str,
        elements: list,
        goal: str,
        blacklist: Set[int] | None = None,
        params: Dict[str, str] | None = None,
    ) -> Optional[Dict[str, Any]]:
        fp        = fingerprint_screen(app_package, elements)
        goal_norm = normalise_goal(goal)
        cache_key = f"{fp}:{goal_norm}"

        # T2A
        if cache_key in self._t2a:
            entry = dict(self._t2a[cache_key])
            sel   = entry.pop("_selector", None)
            entry.pop("_successes", None)
            entry.pop("_failures",  None)

            if sel:
                eid = resolve_selector(sel, elements, blacklist, params)
                if eid is not None:
                    entry["element_id"] = eid
                    self.tier2a_hits += 1
                    logger.info(f"⚡ T2A HIT  fp={fp}  goal={goal_norm[:35]}")
                    return entry
                # selector no longer matches — entry is stale for this screen
                logger.debug(f"T2A selector miss for {cache_key}")
            else:
                # No selector needed (scroll, global_action)
                self.tier2a_hits += 1
                logger.info(f"⚡ T2A HIT (no selector)  fp={fp}")
                return entry

        # T2B
        result = self._match_pattern(app_package, elements, goal, blacklist, params)
        if result:
            self.tier2b_hits += 1
            logger.info(f"📖 T2B HIT  desc={result.pop('_pattern_desc', '?')}")
            return result

        self.misses += 1
        return None

    def _match_pattern(
        self,
        app_package: str,
        elements: list,
        goal: str,
        blacklist: Set[int] | None,
        params: Dict[str, str] | None,
    ) -> Optional[Dict[str, Any]]:
        pkg = app_package.lower()
        for entry in self._t2b:
            fp_pattern = entry.get("fingerprint", "")
            parts = fp_pattern.split(":")
            if len(parts) < 3:
                continue
            pkg_glob, screen_ctx, goal_ctx = parts[0], parts[1], parts[2]

            if pkg_glob != "*" and not _glob_match(pkg_glob, pkg):
                continue
            if goal_ctx != "*":
                goal_lower   = goal.lower()
                goal_keywords = goal_ctx.replace("_", " ").split()
                if not all(kw in goal_lower for kw in goal_keywords):
                    continue
            if screen_ctx != "*":
                screen_keywords = screen_ctx.replace("_", " ").split()
                screen_text = " ".join(
                    (e.text or "") + " " + (e.content_description or "")
                    for e in elements
                ).lower()
                if not all(kw in screen_text for kw in screen_keywords):
                    continue

            selector    = entry.get("action", {}).get("element_selector")
            action_type = entry.get("action", {}).get("action_type", "").upper()

            if selector:
                eid = resolve_selector(selector, elements, blacklist, params)
                if eid is None:
                    continue
            else:
                eid = None

            action: Dict[str, Any] = {
                "thought": entry.get("action", {}).get("reasoning", "pattern match"),
            }
            if action_type in ("CLICK", "TAP"):
                action["action_type"] = "click"
                action["element_id"]  = eid
            elif action_type == "TYPE":
                action["action_type"] = "type"
                action["element_id"]  = eid
                text_template = entry.get("action", {}).get("params", {}).get("text", "")
                action["text"] = _interpolate(text_template, params or {})
            elif action_type in ("SWIPE_UP", "SCROLL_UP"):
                action["action_type"] = "scroll"
                action["direction"]   = "up"
                action["duration"]    = 500
            elif action_type in ("SWIPE_DOWN", "SCROLL_DOWN"):
                action["action_type"] = "scroll"
                action["direction"]   = "down"
                action["duration"]    = 500
            elif action_type == "GLOBAL_ACTION":
                action["action_type"]   = "global_action"
                action["global_action"] = entry.get("action", {}).get("global_action", "BACK")
            else:
                continue

            action["_pattern_desc"] = entry.get("description", "")
            return action

        return None

    # ── Learning ──────────────────────────────────────────────────────────

    def cache_success(
        self,
        app_package: str,
        elements: list,
        goal: str,
        action: Dict[str, Any],
    ):
        """
        Record a successful LLM action. Updates RAM immediately so the
        entry is available for the rest of this session. Queues a DB write
        that is flushed at task completion — no DB writes during execution.
        """
        fp        = fingerprint_screen(app_package, elements)
        goal_norm = normalise_goal(goal)
        cache_key = f"{fp}:{goal_norm}"

        entry = dict(action)
        selector = None

        eid = action.get("element_id")
        if eid is not None:
            selector = self._build_selector(eid, elements)
            if selector:
                entry["_selector"] = selector
            entry.pop("element_id", None)

        entry["_successes"] = self._t2a.get(cache_key, {}).get("_successes", 0) + 1
        entry["_failures"]  = self._t2a.get(cache_key, {}).get("_failures",  0)

        self._t2a[cache_key] = entry
        self.entries_learned += 1

        # Queue DB write — flushed later, not now
        self._pending_writes.append({
            "cache_key":     cache_key,
            "app_package":   app_package,
            "goal_norm":     goal_norm,
            "action_json":   json.dumps({k: v for k, v in entry.items()
                                         if not k.startswith("_")}),
            "selector_json": json.dumps(selector) if selector else None,
            "successes":     entry["_successes"],
            "failures":      entry["_failures"],
            "last_used":     time.time(),
        })

        logger.info(f"🧠 LEARNED  key={cache_key[:30]}…  "
                    f"action={action.get('action_type')}  "
                    f"(total: {self.entries_learned})")

    def record_failure(self, app_package: str, elements: list, goal: str):
        """
        Call this when a T2A-sourced action visibly failed (screen didn't
        change, or wrong outcome). Increments failure count — if confidence
        drops below threshold the entry is removed from RAM and flagged
        for pruning on next load.
        """
        fp        = fingerprint_screen(app_package, elements)
        goal_norm = normalise_goal(goal)
        cache_key = f"{fp}:{goal_norm}"

        if cache_key not in self._t2a:
            return

        entry = self._t2a[cache_key]
        entry["_failures"] = entry.get("_failures", 0) + 1
        successes = entry.get("_successes", 1)
        failures  = entry["_failures"]
        attempts  = successes + failures

        logger.warning(f"⬇️ T2A failure recorded  key={cache_key[:30]}…  "
                       f"confidence={successes/attempts:.0%}")

        if attempts >= _MIN_ATTEMPTS and (successes / attempts) < _MIN_CONFIDENCE:
            logger.warning(f"🗑️ T2A entry below confidence threshold — removing from RAM")
            del self._t2a[cache_key]

        self._pending_updates.append({
            "cache_key": cache_key,
            "failures":  failures,
        })

    def invalidate(self, app_package: str, elements: list, goal: str) -> bool:
        fp        = fingerprint_screen(app_package, elements)
        goal_norm = normalise_goal(goal)
        cache_key = f"{fp}:{goal_norm}"
        if cache_key in self._t2a:
            del self._t2a[cache_key]
            logger.warning(f"🗑️ T2A INVALIDATED  key={cache_key[:30]}…")
            return True
        return False

    def mark_action_ineffective(
        self,
        app_package: str,
        elements_before: list,
        elements_after: list,
        goal: str,
    ) -> bool:
        fp_before = fingerprint_screen(app_package, elements_before)
        fp_after  = fingerprint_screen(app_package, elements_after)
        if fp_before == fp_after:
            self.record_failure(app_package, elements_before, goal)
            return self.invalidate(app_package, elements_before, goal)
        return False

    # ── Flush to DB ───────────────────────────────────────────────────────

    def flush(self):
        """
        Write all pending inserts and updates to SQLite. Call this once
        after a task completes — never during task execution.
        Zero DB access happens during the ReAct loop itself.
        """
        if not self._pending_writes and not self._pending_updates:
            return

        try:
            with sqlite3.connect(self._db_path) as conn:
                for w in self._pending_writes:
                    conn.execute("""
                        INSERT INTO learned_actions
                            (cache_key, app_package, goal_norm, action_json,
                             selector_json, successes, failures, last_used, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            action_json   = excluded.action_json,
                            selector_json = excluded.selector_json,
                            successes     = excluded.successes,
                            last_used     = excluded.last_used
                    """, (
                        w["cache_key"], w["app_package"], w["goal_norm"],
                        w["action_json"], w["selector_json"],
                        w["successes"], w["failures"],
                        w["last_used"], w.get("created_at", time.time()),
                    ))

                for u in self._pending_updates:
                    conn.execute(
                        "UPDATE learned_actions SET failures = ? WHERE cache_key = ?",
                        (u["failures"], u["cache_key"]),
                    )

                conn.commit()

            written = len(self._pending_writes)
            updated = len(self._pending_updates)
            self._pending_writes.clear()
            self._pending_updates.clear()
            logger.info(f"💾 DB flush: {written} written, {updated} updated")

        except Exception as e:
            logger.error(f"❌ DB flush failed: {e}")

    # ── Utilities ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_selector(element_id: int, elements: list) -> Optional[Dict]:
        target = next((e for e in elements if _get_id(e) == element_id), None)
        if not target:
            return None
        sel: Dict[str, Any] = {}
        etype = _attr(target, "type")
        if etype:
            sel["element_type"] = etype
        text = _attr(target, "text")
        if text:
            sel["text"] = text
            return sel
        desc = _attr(target, "content_description")
        if desc:
            sel["content_desc_matches"] = re.escape(desc)
            return sel
        hint = _attr(target, "hint_text")
        if hint:
            sel["hint"] = hint
            return sel
        rid = _attr(target, "resource_id")
        if rid:
            sel["resource_id_contains"] = rid.rsplit("/", 1)[-1] if "/" in rid else rid
            return sel
        if _flag(target, "clickable"):
            sel["clickable"] = True
            return sel
        return None

    def get_stats(self) -> Dict[str, Any]:
        total = self.tier2a_hits + self.tier2b_hits + self.misses
        return {
            "seed_patterns":   len(self._t2b),
            "learned_entries": len(self._t2a),
            "tier2a_hits":     self.tier2a_hits,
            "tier2b_hits":     self.tier2b_hits,
            "misses":          self.misses,
            "hit_rate":        f"{(self.tier2a_hits + self.tier2b_hits) / max(1, total) * 100:.1f}%",
            "pending_writes":  len(self._pending_writes),
        }