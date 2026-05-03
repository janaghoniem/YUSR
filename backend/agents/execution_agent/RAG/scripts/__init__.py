"""Composable script utilities for execution agents."""
from scripts.word_tools import (
    doc_create,
    doc_open,
    doc_find,
    doc_load,
    doc_add_heading,
    doc_add_paragraph,
    doc_add_table,
    doc_save,
    doc_launch,
)

from scripts.excel_tools import (
    xl_create,
    xl_open,
    xl_find,
    xl_load,
    xl_set_cell,
    xl_write_headers,
    xl_write_row,
    xl_set_formula,
    xl_save,
    xl_launch,
)

from scripts.ppt_tools import (
    ppt_create,
    ppt_open,
    ppt_find,
    ppt_load,
    ppt_add_title_slide,
    ppt_add_content_slide,
    ppt_add_bullet_slide,
    ppt_save,
    ppt_launch,
)

from scripts.notepad_tools import (
    notepad_launch,
    notepad_create,
    notepad_open,
    notepad_find,
    notepad_load,
    notepad_save,
    notepad_write,
    notepad_type,
    notepad_append,
    notepad_read,
)

__all__ = [
    # word
    "doc_create", "doc_open", "doc_find", "doc_load",
    "doc_add_heading", "doc_add_paragraph", "doc_add_table",
    "doc_save", "doc_launch",
    # excel
    "xl_create", "xl_open", "xl_find", "xl_load",
    "xl_set_cell", "xl_write_headers", "xl_write_row",
    "xl_set_formula", "xl_save", "xl_launch",
    # powerpoint
    "ppt_create", "ppt_open", "ppt_find", "ppt_load",
    "ppt_add_title_slide", "ppt_add_content_slide",
    "ppt_add_bullet_slide", "ppt_save", "ppt_launch",
    # notepad
    "notepad_launch", "notepad_create", "notepad_open", "notepad_find",
    "notepad_load", "notepad_save", "notepad_write", "notepad_type",
    "notepad_append", "notepad_read",
]