"""
action_knowledge_base.py
Action Knowledge Base (ActionKB) — Tier 2A + 2B
=================================================

Tier 2A: ACTION CACHE
  Fingerprint current screen → look up a cached action that previously
  succeeded on this exact screen for a similar goal.
  0 tokens, <1 ms.

Tier 2B: PATTERN LIBRARY (seed data from action_KB.json)
  Pre-built element selectors for common Android patterns (time picker,
  Gmail compose, app launch, dialogs).  Loaded once at startup.
  0 tokens, <1 ms.

Learning loop:
  When the LLM (Tier 3) produces an action that SUCCEEDS, we cache
  the (fingerprint, goal_pattern, action) mapping so Tier 2A resolves
  it next time.  Failed actions are **never** cached.

Design rules
─────────────
• element_id is NEVER part of the fingerprint — it changes every session
• selectors are generalised (element_type + text/desc pattern), resolved
  to the current element_id at lookup time
• specific values in goals (times, emails…) are replaced with wildcards
  so one recipe covers all variations
"""

import hashlib
import json
import logging
import os
import re
from typing import Optional, Dict, List, Any, Set

logger = logging.getLogger(__name__)

# Path to the seed JSON shipped with the repo
_SEED_PATH = os.path.join(os.path.dirname(__file__), "action_KB.json")


# ═══════════════════════════════════════════════════════════════════════════
#  SCREEN FINGERPRINT
# ═══════════════════════════════════════════════════════════════════════════

def fingerprint_screen(app_package: str, elements: list) -> str:
    """
    Stable hash of a screen state.

    Components (sorted, lowered):
      • app_package
      • for every element: (type, text[:20], content_description[:20],
        clickable, focusable, scrollable)

    Excludes element_id and bounds — those change across sessions.
    Returns a 12‑char hex digest.
    """
    sigs: list[str] = []
    for e in elements:
        t = _attr(e, "type")
        tx = _attr(e, "text")[:20]
        cd = _attr(e, "content_description")[:20]
        flags = ""
        if _flag(e, "clickable"):  flags += "C"
        if _flag(e, "focusable"):  flags += "F"
        if _flag(e, "scrollable"): flags += "S"
        sigs.append(f"{t}:{tx}:{cd}:{flags}")
    sigs.sort()
    raw = f"{app_package.lower()}|{'|'.join(sigs)}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _attr(e, key: str) -> str:
    """Get a string attribute from a dict or pydantic model."""
    if isinstance(e, dict):
        return (e.get(key) or "").lower()
    return (getattr(e, key, None) or "").lower()


def _flag(e, key: str) -> bool:
    if isinstance(e, dict):
        return bool(e.get(key))
    return bool(getattr(e, key, False))


# ═══════════════════════════════════════════════════════════════════════════
#  GOAL NORMALISATION
# ═══════════════════════════════════════════════════════════════════════════

def normalise_goal(goal: str) -> str:
    """
    Replace task‑specific literals with wildcards so one recipe covers
    all variations.

    "Set alarm to 7:30 PM"  →  "set alarm to T:T _"
    "Send email to john@x"  →  "send email to E"
    """
    g = goal.lower().strip()
    g = re.sub(r'\d{1,2}:\d{2}', 'T:T', g)          # times
    g = re.sub(r'\d+', 'N', g)                        # numbers
    g = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', 'E', g)  # emails
    g = re.sub(r'\b(am|pm)\b', '_', g)                # am/pm
    return g


# ═══════════════════════════════════════════════════════════════════════════
#  ELEMENT MATCHING  — resolve a selector to a live element_id
# ═══════════════════════════════════════════════════════════════════════════

def resolve_selector(selector: Dict[str, Any],
                     elements: list,
                     blacklist: Set[int] | None = None,
                     params: Dict[str, str] | None = None) -> Optional[int]:
    """
    Walk the current UI elements and return the first element_id that
    matches *selector*.  Returns None on no match.

    Supported selector keys (all optional, combined with AND):
      element_type     — exact match on element.type
      text             — exact (case‑insensitive) match on text
      text_matches     — regex match on text
      text_contains    — substring match on text (supports {app_name} etc.)
      content_desc_matches — regex on content_description
      hint             — substring match on hint_text
      resource_id_contains — substring on resource_id
      clickable        — bool
      editable         — True → type == "textfield" or "edittext"
    """
    blacklist = blacklist or set()
    params = params or {}

    for e in elements:
        eid = _get_id(e)
        if eid in blacklist:
            continue

        if not _match_one(selector, e, params):
            continue

        return eid
    return None


def _get_id(e) -> int:
    if isinstance(e, dict):
        return e.get("element_id", -1)
    return getattr(e, "element_id", -1)


def _match_one(sel: Dict, e, params: Dict[str, str]) -> bool:
    """Return True if element *e* satisfies every key in *sel*."""

    if "element_type" in sel:
        if _attr(e, "type") != sel["element_type"].lower():
            # also try class_name as fallback
            cn = _attr(e, "class_name")
            if not cn or sel["element_type"].lower() not in cn:
                return False

    if "text" in sel:
        want = _interpolate(sel["text"], params).lower()
        if _attr(e, "text") != want:
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

    if "hint" in sel:
        want = sel["hint"].lower()
        if want not in _attr(e, "hint_text"):
            return False

    if "resource_id_contains" in sel:
        want = sel["resource_id_contains"].lower()
        rid = _attr(e, "resource_id")
        if want not in rid:
            return False

    if "clickable" in sel:
        if _flag(e, "clickable") != sel["clickable"]:
            return False

    if "editable" in sel:
        etype = _attr(e, "type")
        if sel["editable"] and etype not in ("textfield", "edittext"):
            return False

    return True


def _interpolate(template: str, params: Dict[str, str]) -> str:
    """Replace {key} placeholders with values from *params*."""
    for k, v in params.items():
        template = template.replace("{" + k + "}", v)
    return template


# ═══════════════════════════════════════════════════════════════════════════
#  ACTION KNOWLEDGE BASE
# ═══════════════════════════════════════════════════════════════════════════

class ActionKB:
    """
    Two‑layer lookup:

    Tier 2A  –  *exact screen cache*
                key = (screen_fingerprint, normalised_goal)
                Populated by caching successful LLM actions.

    Tier 2B  –  *pattern library*
                Loaded from action_KB.json at startup.
                Uses element selectors (not element_ids) so entries
                are session‑independent.

    Both layers return an action dict ready for _json_to_ui_action,
    or None (→ fall through to Tier 3 / LLM).
    """

    def __init__(self):
        # Tier 2A — screen_hash:goal_norm → action dict
        self._cache: Dict[str, Dict[str, Any]] = {}

        # Tier 2B — list of seed entries (from JSON)
        self._patterns: List[Dict[str, Any]] = []

        # Stats
        self.tier2a_hits = 0
        self.tier2b_hits = 0
        self.misses = 0
        self.entries_learned = 0

        self._load_seed_data()

    # ─────── loading ──────────────────────────────────────────────────

    def _load_seed_data(self):
        """Load action_KB.json once at startup."""
        if not os.path.exists(_SEED_PATH):
            logger.warning(f"⚠️  Seed file not found: {_SEED_PATH}")
            return
        try:
            with open(_SEED_PATH, "r") as f:
                data = json.load(f)
            self._patterns = data.get("seed_entries", [])
            logger.info(
                f"📚 ActionKB loaded {len(self._patterns)} seed patterns "
                f"from {os.path.basename(_SEED_PATH)}"
            )
        except Exception as exc:
            logger.error(f"❌ Failed to load seed data: {exc}")

    # ─────── lookup ──────────────────────────────────────────────────

    def lookup(
        self,
        app_package: str,
        elements: list,
        goal: str,
        blacklist: Set[int] | None = None,
        params: Dict[str, str] | None = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Try Tier 2A then Tier 2B.
        Returns a ready‑to‑use action dict or None.
        """
        fp = fingerprint_screen(app_package, elements)
        goal_norm = normalise_goal(goal)

        # ── Tier 2A: exact cache ──────────────────────────────────────
        cache_key = f"{fp}:{goal_norm}"
        if cache_key in self._cache:
            cached = dict(self._cache[cache_key])       # shallow copy
            # Re‑resolve element_id from selector
            sel = cached.pop("_selector", None)
            if sel:
                eid = resolve_selector(sel, elements, blacklist, params)
                if eid is not None:
                    cached["element_id"] = eid
                    self.tier2a_hits += 1
                    logger.info(f"⚡ T2A CACHE HIT  fp={fp}  goal_norm={goal_norm[:40]}")
                    return cached
                # selector didn't match current screen — stale entry
                logger.debug(f"T2A selector miss for {cache_key}")
            else:
                # actions like global_action / scroll that don't need element_id
                self.tier2a_hits += 1
                logger.info(f"⚡ T2A CACHE HIT (no selector)  fp={fp}")
                return cached

        # ── Tier 2B: pattern library ──────────────────────────────────
        result = self._match_pattern(app_package, elements, goal, blacklist, params)
        if result:
            self.tier2b_hits += 1
            logger.info(f"📖 T2B PATTERN HIT  desc={result.get('_pattern_desc','?')}")
            result.pop("_pattern_desc", None)
            return result

        self.misses += 1
        return None

    # ─────── pattern matching (Tier 2B) ──────────────────────────────

    def _match_pattern(
        self,
        app_package: str,
        elements: list,
        goal: str,
        blacklist: Set[int] | None,
        params: Dict[str, str] | None,
    ) -> Optional[Dict[str, Any]]:
        """
        Walk seed_entries and return the first whose fingerprint glob and
        element_selector match the current screen.
        """
        pkg = app_package.lower()

        for entry in self._patterns:
            fp_pattern = entry.get("fingerprint", "")
            # fingerprint format:  "pkg_glob:screen_context:goal_context"
            parts = fp_pattern.split(":")
            if len(parts) < 3:
                continue

            pkg_glob, _screen_ctx, goal_ctx = parts[0], parts[1], parts[2]

            # 1. Package match (supports * wildcard)
            if pkg_glob != "*" and not _glob_match(pkg_glob, pkg):
                continue

            # 2. Goal context match (loose keyword match)
            if goal_ctx != "*":
                goal_lower = goal.lower()
                # "set_alarm" → keywords ["set", "alarm"]
                goal_keywords = goal_ctx.replace("_", " ").split()
                if not all(kw in goal_lower for kw in goal_keywords):
                    continue

            # 3. Selector match — does the screen have a matching element?
            selector = entry.get("action", {}).get("element_selector")
            action_type = entry.get("action", {}).get("action_type", "").upper()

            if selector:
                eid = resolve_selector(selector, elements, blacklist, params)
                if eid is None:
                    continue  # element not on this screen
            else:
                eid = None

            # ── Build the action dict ────────────────────────────────
            action: Dict[str, Any] = {
                "thought": entry.get("action", {}).get("reasoning", "pattern match"),
            }

            if action_type in ("CLICK", "TAP"):
                action["action_type"] = "click"
                action["element_id"] = eid
            elif action_type == "TYPE":
                action["action_type"] = "type"
                action["element_id"] = eid
                # Resolve text from params
                text_template = (entry.get("action", {})
                                      .get("params", {})
                                      .get("text", ""))
                action["text"] = _interpolate(text_template, params or {})
            elif action_type in ("SWIPE_UP", "SCROLL_UP"):
                action["action_type"] = "scroll"
                action["direction"] = "up"
                action["duration"] = 500
            elif action_type in ("SWIPE_DOWN", "SCROLL_DOWN"):
                action["action_type"] = "scroll"
                action["direction"] = "down"
                action["duration"] = 500
            elif action_type == "GLOBAL_ACTION":
                action["action_type"] = "global_action"
                action["global_action"] = entry.get("action", {}).get("global_action", "BACK")
            else:
                continue  # unknown action type

            action["_pattern_desc"] = entry.get("description", "")
            return action

        return None

    # ─────── learning (cache successful LLM results) ─────────────────

    def cache_success(
        self,
        app_package: str,
        elements: list,
        goal: str,
        action: Dict[str, Any],
    ):
        """
        After an LLM‑chosen action SUCCEEDS, store it so Tier 2A
        resolves it next time.

        We store a *generalised selector* instead of the raw element_id
        so the entry works across sessions.
        """
        fp = fingerprint_screen(app_package, elements)
        goal_norm = normalise_goal(goal)
        cache_key = f"{fp}:{goal_norm}"

        # Build a generalised selector from the element we clicked/typed
        entry = dict(action)  # shallow copy

        eid = action.get("element_id")
        if eid is not None:
            selector = self._build_selector(eid, elements)
            if selector:
                entry["_selector"] = selector
            # Don't store the session‑specific element_id
            entry.pop("element_id", None)

        self._cache[cache_key] = entry
        self.entries_learned += 1
        logger.info(
            f"🧠 LEARNED  cache_key={cache_key[:30]}…  "
            f"action_type={action.get('action_type')}  "
            f"(total learned: {self.entries_learned})"
        )

    @staticmethod
    def _build_selector(element_id: int, elements: list) -> Optional[Dict]:
        """
        Build a reusable selector dict from a concrete element_id.
        Uses text / content_description / hint_text — never the id itself.
        """
        target = None
        for e in elements:
            if _get_id(e) == element_id:
                target = e
                break
        if target is None:
            return None

        sel: Dict[str, Any] = {}
        etype = _attr(target, "type")
        if etype:
            sel["element_type"] = etype

        text = _attr(target, "text")
        if text:
            sel["text"] = text
            return sel  # text is the strongest signal — enough

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
            # take last segment  e.g. "com.google.android.gm:id/to" → "to"
            short = rid.rsplit("/", 1)[-1] if "/" in rid else rid
            sel["resource_id_contains"] = short
            return sel

        # Clickable fallback
        if _flag(target, "clickable"):
            sel["clickable"] = True
            return sel

        return None

    # ─────── invalidation ──────────────────────────────────────────

    def invalidate(self, app_package: str, elements: list, goal: str):
        """
        Remove a T2A cache entry that turned out to be unhelpful
        (e.g. replayed 3 times without progress).
        """
        fp = fingerprint_screen(app_package, elements)
        goal_norm = normalise_goal(goal)
        cache_key = f"{fp}:{goal_norm}"
        if cache_key in self._cache:
            del self._cache[cache_key]
            logger.warning(f"🗑️ T2A INVALIDATED  cache_key={cache_key[:30]}…")
            return True
        return False

    # ─────── stats ───────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "seed_patterns": len(self._patterns),
            "learned_entries": self.entries_learned,
            "cache_size": len(self._cache),
            "tier2a_hits": self.tier2a_hits,
            "tier2b_hits": self.tier2b_hits,
            "misses": self.misses,
            "hit_rate": (
                f"{(self.tier2a_hits + self.tier2b_hits) / max(1, self.tier2a_hits + self.tier2b_hits + self.misses) * 100:.1f}%"
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _glob_match(pattern: str, value: str) -> bool:
    """
    Minimal glob: leading '*.' matches any prefix.
    "*.deskclock" matches "com.google.android.deskclock".
    """
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return value.endswith(suffix) or suffix in value
    return pattern in value or value == pattern
