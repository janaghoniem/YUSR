"""
cache_adapter.py — Drop-in shim for ChromaTemplateCache in mobile_strategy_codegen.py

Changes vs previous version:
  - Imports infer_app from mobile_template_cache (FIX A) instead of using
    mobile_strategy's broken _infer_app that was missing "messages"/"whatsapp".
  - store_successful uses the new infer_app to set app correctly on LLM-generated
    scripts that succeeded, so future runs hit cache instead of re-generating.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from agents.execution_agent.strategies.mobile_template_cache import (
        ChromaTemplateCache,
        CodeTemplate,
        PlaceholderExtractor,
        infer_app,
        CHROMA_PATH,
    )
except Exception:
    from mobile_template_cache import (  # type: ignore
        ChromaTemplateCache,
        CodeTemplate,
        PlaceholderExtractor,
        infer_app,
        CHROMA_PATH,
    )


class CacheAdapter:

    def __init__(self, path: Path = CHROMA_PATH) -> None:
        self._inner = ChromaTemplateCache(path)
        self._placeholderizer = PlaceholderExtractor()

    # ── Same interface as old TemplateCache ───────────────────────────────

    def lookup(
        self,
        task_text: str,
        app: str,
        threshold: float = 0.65,
    ) -> Optional[Tuple[CodeTemplate, float]]:
        return self._inner.lookup(task_text, app)

    def add(self, template: CodeTemplate) -> None:
        self._inner.add(template)

    def mark_success(self, template_id: str) -> None:
        self._inner.mark_success(template_id)

    def mark_failure(self, template_id: str) -> None:
        self._inner.mark_failure(template_id)

    def stats(self) -> str:
        return self._inner.stats()

    # ── Storage after successful LLM generation ───────────────────────────

    def store_successful(
        self,
        code:      str,
        task_text: str,
        app:       str,
        package:   str,
        params:    Dict[str, str],
        task_type: str = "action",
        aliases:   Optional[List[str]] = None,
    ) -> None:
        """
        Parameterize and store a successfully-executed LLM-generated script.
        Guards against caching hardcoded scripts and already-stable templates.
        """
        # Re-infer app to correct cases where mobile_strategy passed app="unknown"
        if app == "unknown" or not package:
            app, package = infer_app(task_text)

        template_code, schema = self._placeholderizer.extract(code, params, task_text)

        # Don't cache if task implies params but extraction found none
        field_implies_param = any(
            kw in task_text.lower()
            for kw in ("subject", "body", "to field", "enter ", "type ", "fill ")
        )
        if field_implies_param and "{" not in template_code and params:
            return

        tid = hashlib.sha1(f"{app}:{task_text}".encode()).hexdigest()
        existing = self._inner._fetch_by_id(tid)
        if existing and existing.success_count >= 5:
            return

        template = CodeTemplate(
            template_id=tid,
            task_pattern=task_text,
            aliases=aliases or [],
            app=app,
            package=package,
            task_type=task_type,
            code_template=template_code,
            parameter_schema=schema,
            success_count=1,
            failure_count=0,
        )
        self._inner.add(template)