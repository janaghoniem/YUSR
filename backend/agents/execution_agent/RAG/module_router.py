"""
Module Router - Routes tasks to appropriate module based on keywords
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ModuleRouter:
    """Routes tasks to word/excel/powerpoint/general module"""

    MODULES = {
        "word": {
            "keywords": ["word", "document", "docx", "page", "paragraph", "heading", "table"],
            "library": "python-docx"
        },
        "excel": {
            "keywords": ["excel", "spreadsheet", "xlsx", "sheet", "cell", "row", "column"],
            "library": "openpyxl"
        },
        "powerpoint": {
            "keywords": ["powerpoint", "pptx", "slide", "presentation", "bullet"],
            "library": "python-pptx"
        },
        "general": {
            "keywords": [],
            "library": "pyautogui"
        }
    }

    def route_task(self, query: str) -> str:
        """Route task to appropriate module"""
        if not query:
            return "general"

        query_lower = query.lower()
        scores = {}

        for module_name, config in self.MODULES.items():
            if module_name == "general":
                continue
            matches = sum(1 for keyword in config["keywords"] if keyword in query_lower)
            if matches > 0:
                scores[module_name] = matches

        if scores:
            best_module = max(scores, key=scores.get)
            logger.info(f"[ROUTER] Task routed to '{best_module}' ({scores[best_module]} keyword matches)")
            return best_module

        logger.debug(f"[ROUTER] Using 'general' module (no matches)")
        return "general"

    def get_library_name(self, module: str) -> str:
        """Get library name for module"""
        return self.MODULES.get(module, {}).get("library", "pyautogui")


def route_task(query: str) -> str:
    """Quick function to route a task"""
    router = ModuleRouter()
    return router.route_task(query)
