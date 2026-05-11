"""Standalone uiautomator2 harness for coordinator-decomposed mobile tasks.

This file is intentionally small and explicit: it exists to exercise the exact
step sequences the coordinator sends, so the resulting scripts can be cached
and reused by mobile strategy code generation.

The current case covers the Clock alarm flow. Add more app cases by extending
the case registry below, not by duplicating the orchestration code.
"""

from __future__ import annotations

import argparse
import os
import re
import xml.etree.ElementTree as ET
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import uiautomator2 as u2


DEFAULT_PACKAGE = "com.google.android.deskclock"
GMAIL_PACKAGE = "com.google.android.gm"
CONTACT_PACKAGE = "com.google.android.contacts"
MAPS_PACKAGE = "com.google.android.apps.maps"
EMAIL_PACKAGE = "com.google.android.gm"
DOCS_PACKAGE = "com.google.android.apps.docs.editors.docs"
MESSAGES_PACKAGE = "com.google.android.apps.messaging"
WHATSAPP_PACKAGE = "com.whatsapp"
CALENDAR_PACKAGE = "com.google.android.calendar"

@dataclass(frozen=True)
class AlarmTestCase:
    case_id: str
    hour: str
    minute: str
    period: str
    package: str = DEFAULT_PACKAGE


@dataclass(frozen=True)
class GmailTestCase:
    case_id: str
    recipient: str
    subject: str
    body: str
    package: str = GMAIL_PACKAGE


ALARM_CASES: Dict[str, AlarmTestCase] = {
    "clock_5_35_am": AlarmTestCase(
        case_id="clock_5_35_am",
        hour="5",
        minute="35",
        period="AM",
    ),
}


GMAIL_CASES: Dict[str, GmailTestCase] = {
    "gmail_hello_world": GmailTestCase(
        case_id="gmail_hello_world",
        recipient="hayaadawy@icloud.com",
        subject="hello world",
        body="helllllllllooooooooo",
    ),
}


@dataclass(frozen=True)
class ContactTestCase:
    case_id: str
    name: str
    phone: str
    package: str = CONTACT_PACKAGE


@dataclass(frozen=True)
class MapsTestCase:
    case_id: str
    query: str
    package: str = MAPS_PACKAGE


@dataclass(frozen=True)
class EmailTestCase:
    case_id: str
    package: str = EMAIL_PACKAGE


@dataclass(frozen=True)
class DocsTestCase:
    case_id: str
    title: str
    package: str = DOCS_PACKAGE


@dataclass(frozen=True)
class MessageTestCase:
    case_id: str
    recipient: str
    message: str
    package: str = MESSAGES_PACKAGE


@dataclass(frozen=True)
class WhatsAppTestCase:
    case_id: str
    group_name: str
    message: str
    package: str = WHATSAPP_PACKAGE


@dataclass(frozen=True)
class CalendarEventTestCase:
    case_id: str
    title: str
    date: str
    package: str = CALENDAR_PACKAGE


CONTACT_CASES: Dict[str, ContactTestCase] = {
    "contact_john": ContactTestCase(
        case_id="contact_john",
        name="John",
        phone="01122346543",
    ),
}


MAPS_CASES: Dict[str, MapsTestCase] = {
    "maps_coffee_shops": MapsTestCase(
        case_id="maps_coffee_shops",
        query="coffee shops near me",
    ),
}


EMAIL_CASES: Dict[str, EmailTestCase] = {
    "email_inbox": EmailTestCase(
        case_id="email_inbox",
    ),
}


DOCS_CASES: Dict[str, DocsTestCase] = {
    "docs_final_thesis": DocsTestCase(
        case_id="docs_final_thesis",
        title="Final Thesis",
    ),
}


MESSAGE_CASES: Dict[str, MessageTestCase] = {
    "messages_haya_on_my_way": MessageTestCase(
        case_id="messages_haya_on_my_way",
        recipient="Haya",
        message="I'm on my way",
    ),
}


WHATSAPP_CASES: Dict[str, WhatsAppTestCase] = {
    "whatsapp_me4_grad": WhatsAppTestCase(
        case_id="whatsapp_me4_grad",
        group_name="shahd",
        message="THIS IS A MESSAGE FROM HAYA'S AGENT",
    ),
}


CALENDAR_CASES: Dict[str, CalendarEventTestCase] = {
    "calendar_victory_2026_10_07": CalendarEventTestCase(
        case_id="calendar_victory_2026_10_07",
        title="Victory",
        date="2026-10-07",
    ),
}


@dataclass
class DeviceConfig:
    serial: str = ""
    package: str = DEFAULT_PACKAGE


def connect(serial: str = ""):
    return u2.connect(serial) if serial else u2.connect()


def select(d, kind: str, value: str):
    mapping = {
        "description": "description",
        "text": "text",
        "resourceId": "resourceId",
    }
    return d(**{mapping[kind]: value})


def open_clock_app(d, package: str = DEFAULT_PACKAGE) -> None:
    d.app_start(package)
    time.sleep(2.5)


def open_gmail_app(d, package: str = GMAIL_PACKAGE) -> None:
    d.app_start(package)
    time.sleep(2.5)


def open_maps_app(d, package: str = MAPS_PACKAGE) -> None:
    d.app_stop(package)
    time.sleep(0.5)
    d.app_start(package)
    time.sleep(3.5)
    # Dismiss any consent/first-run overlay that blocks the omnibox
    negative = d(resourceId="com.google.android.apps.maps:id/negative_button")
    positive = d(resourceId="com.google.android.apps.maps:id/positive_button")
    if negative.exists(timeout=1):
        try:
            negative.click()
            time.sleep(1.0)
        except Exception:
            pass
    elif positive.exists(timeout=1):
        # Prefer cancelling onboarding when present
        try:
            # if negative not present, try the Cancel text button
            cancel = d(text="Cancel")
            if cancel.exists(timeout=1):
                cancel.click()
                time.sleep(1.0)
            else:
                positive.click()
                time.sleep(1.0)
        except Exception:
            pass


def open_alarm_settings_screen(d) -> None:
    """Step 2 from plan 1: open the alarm list/settings screen."""
    if is_alarm_screen_visible(d):
        return
    open_alarm_tab(d)


def is_alarm_screen_visible(d) -> bool:
    signals = [
        d(description="Add alarm"),
        d(text="Add alarm"),
        d(resourceId="com.google.android.deskclock:id/fab"),
        d(resourceId="com.google.android.deskclock:id/alarm_recycler_view"),
        d(text="Alarm"),
        d(description="Alarm"),
    ]
    return any(sel.exists(timeout=1) for sel in signals)


def open_alarm_tab(d) -> None:
    # Check if we're already at the alarms screen
    if d(text="Alarms").exists(timeout=1):
        return
    
    candidates = [
        ("description", "Alarm"),
        ("text", "Alarm"),
        ("textContains", "Alarm"),
        ("descriptionContains", "Alarm"),
        ("resourceId", "com.google.android.deskclock:id/alarm_tab"),
    ]
    for kind, value in candidates:
        if kind in {"textContains", "descriptionContains"}:
            sel = d(**{kind: value})
        else:
            sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.0)
            return
    if is_alarm_screen_visible(d):
        return
    raise RuntimeError("Alarm tab not found")


def click_add_alarm(d) -> None:
    candidates = [
        ("description", "Add alarm"),
        ("text", "Add alarm"),
        ("resourceId", "com.google.android.deskclock:id/fab"),
        ("resourceId", "com.google.android.deskclock:id/add_alarm"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.0)
            return
    d.click(0.9, 0.9)
    time.sleep(1.0)


def click_maps_search_bar(d) -> None:
    candidates = [
        ("resourceId", "com.google.android.apps.maps:id/search_omnibox_text_box"),
        ("description", "Try gas stations, ATMs"),
        ("text", "Search here"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=3):
            sel.click()
            time.sleep(1.0)
            return
    raise RuntimeError("Maps search bar not found")


def open_email_app(d, package: str = EMAIL_PACKAGE) -> None:
    # Start the email app; caller may override package if needed
    d.app_start(package)
    time.sleep(2.5)


def open_docs_app(d, package: str = DOCS_PACKAGE) -> None:
    d.app_start(package)
    time.sleep(3.0)


def click_new_docs_document(d) -> None:
    candidates = [
        ("text", "Blank document"),
        ("text", "Blank"),
        ("textContains", "Blank"),
        ("textContains", "Create"),
        ("description", "Blank document"),
        ("description", "New"),
        ("descriptionContains", "New"),
        ("descriptionContains", "Create"),
        ("resourceId", "com.google.android.apps.docs.editors.docs:id/fab"),
    ]
    for kind, value in candidates:
        if kind in {"textContains", "descriptionContains"}:
            sel = d(**{kind: value})
        else:
            sel = select(d, kind, value)
        if sel.exists(timeout=3):
            sel.click()
            time.sleep(2.0)
            return
    dump_screen(d)
    raise RuntimeError("Could not create a new Google Docs document")


def set_docs_title(d, title: str) -> None:
    title_candidates = [
        d(text="Untitled document"),
        d(text="Untitled Document"),
        d(resourceId="com.google.android.apps.docs.editors.docs:id/title"),
        d(description="Untitled document"),
    ]
    for field in title_candidates:
        if field.exists(timeout=2):
            field.click()
            time.sleep(0.4)
            try:
                field.clear_text()
            except Exception:
                pass
            try:
                field.set_text(title)
            except Exception:
                try:
                    d.send_keys(title, clear=True)
                except Exception:
                    d.shell(["input", "text", title.replace(" ", "%s")])
            time.sleep(0.8)
            return

    # If the visible title field isn't exposed, use the current focused field.
    d.press("tab")
    time.sleep(0.2)
    try:
        d.send_keys(title, clear=True)
    except Exception:
        d.shell(["input", "text", title.replace(" ", "%s")])
    time.sleep(0.8)


def save_docs_document(d) -> None:
    candidates = [
        ("text", "Save"),
        ("text", "Done"),
        ("description", "Save"),
        ("description", "Done"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.2)
            return
    # Docs autosaves in practice, so back out if no save control is visible.
    try:
        d.press("back")
        time.sleep(1.0)
    except Exception:
        pass


def open_messages_app(d, package: str = MESSAGES_PACKAGE) -> None:
    d.app_start(package)
    time.sleep(2.5)


def open_or_start_message_thread(d, recipient: str) -> None:
    # If a thread is already visible in the conversation list, open it.
    exact = d(text=recipient)
    partial = d(textContains=recipient)
    regex = d(textMatches=f"(?i).*{recipient}.*")
    if exact.exists(timeout=2):
        exact.click()
        time.sleep(1.2)
        return
    if partial.exists(timeout=2):
        partial.click()
        time.sleep(1.2)
        return
    if regex.exists(timeout=2):
        regex.click()
        time.sleep(1.2)
        return

    # Otherwise create a new chat.
    new_chat_candidates = [
        ("description", "Start chat"),
        ("text", "Start chat"),
        ("resourceId", "com.google.android.apps.messaging:id/start_chat_fab"),
        ("resourceId", "com.google.android.apps.messaging:id/start_new_conversation_button"),
    ]
    for kind, value in new_chat_candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.0)
            break

    recipient_field = None
    recipient_candidates = [
        d(resourceId="com.google.android.apps.messaging:id/recipient_text_view"),
        d(resourceId="com.google.android.apps.messaging:id/contact_picker_create_group"),
        d(text="To"),
        d(textContains="To"),
    ]
    for sel in recipient_candidates:
        if sel.exists(timeout=2):
            recipient_field = sel
            break

    if recipient_field is None:
        edits = d(className="android.widget.MultiAutoCompleteTextView")
        if edits.exists(timeout=2):
            recipient_field = edits

    if recipient_field is None:
        edits = d(className="android.widget.EditText")
        if edits.exists(timeout=2):
            recipient_field = edits[0]

    if recipient_field is None:
        raise RuntimeError("Recipient field not found in Messages")

    recipient_field.click()
    time.sleep(0.2)
    try:
        recipient_field.set_text(recipient)
    except Exception:
        try:
            d.send_keys(recipient, clear=True)
        except Exception:
            d.shell(["input", "text", recipient.replace(" ", "%s")])
    time.sleep(1.0)

    # In the contact picker flow, typing opens a result list and we need to tap
    # the recipient row (not just press Enter).
    result_candidates = [
        d(resourceId="com.google.android.apps.messaging:id/contact_name", text=recipient),
        d(resourceId="com.google.android.apps.messaging:id/conversation_title", text=recipient),
        d(text=recipient),
        d(textContains=recipient),
        d(textMatches=f"(?i).*{recipient}.*"),
        d(resourceIdMatches=".*contact_row_test_prefix.*"),
    ]
    for sel in result_candidates:
        if sel.exists(timeout=2):
            try:
                sel.click()
                time.sleep(1.0)
                return
            except Exception:
                pass

    # Fallback if list click is unavailable on this build.
    d.press("enter")
    time.sleep(1.0)


def type_message_text(d, message: str) -> None:
    field_candidates = [
        d(resourceId="com.google.android.apps.messaging:id/compose_message_text"),
        d(resourceId="com.google.android.apps.messaging:id/message_text"),
        d(text="Text message"),
        d(textContains="Message"),
    ]
    field = None
    for sel in field_candidates:
        if sel.exists(timeout=2):
            field = sel
            break

    if field is None:
        edits = d(className="android.widget.EditText")
        if edits.exists(timeout=2):
            field = edits[len(edits) - 1]

    if field is None:
        raise RuntimeError("Message input field not found")

    field.click()
    time.sleep(0.2)
    try:
        field.clear_text()
    except Exception:
        pass
    try:
        field.set_text(message)
    except Exception:
        d.send_keys(message, clear=True)
    time.sleep(0.6)


def send_message_now(d) -> None:
    candidates = [
        ("resourceId", "com.google.android.apps.messaging:id/send_message_button_icon"),
        ("resourceId", "com.google.android.apps.messaging:id/send_message_button_container"),
        ("resourceId", "com.google.android.apps.messaging:id/self_send_icon"),
        ("description", "Send"),
        ("text", "Send"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.2)
            return
    # Description variants like "Send SMS" / "Send message"
    for desc in ("Send SMS", "Send message", "Send MMS"):
        sel = d(description=desc)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.2)
            return

    # Loose fallback for variants like "Send message to Haya".
    contains_candidates = [
        d(descriptionContains="Send"),
        d(textContains="Send"),
    ]
    for sel in contains_candidates:
        if sel.exists(timeout=2):
            try:
                sel.click()
                time.sleep(1.2)
                return
            except Exception:
                pass

    dump_screen(d)
    raise RuntimeError("Send button not found in Messages")


def open_whatsapp_app(d, package: str = WHATSAPP_PACKAGE) -> None:
    d.app_start(package)
    time.sleep(2.5)


def open_whatsapp_group_chat(d, group_name: str) -> None:
    # Always use search so this step does not rely on the group being visible.
    group_pattern = "(?i).*" + ".*".join(group_name.split()) + ".*"

    search_candidates = [
        ("resourceId", "com.whatsapp:id/my_search_bar"),
        ("resourceId", "com.whatsapp:id/menuitem_search"),
        ("descriptionContains", "Search"),
        ("descriptionContains", "Meta AI"),
        ("description", "Search"),
        ("description", "Search…"),
        ("text", "Search"),
        ("textContains", "Search"),
        ("textContains", "Meta AI"),
    ]
    for kind, value in search_candidates:
        if kind in {"descriptionContains", "textContains"}:
            sel = d(**{kind: value})
        else:
            sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(0.8)
            break

    search_field = None
    field_candidates = [
        d(resourceId="com.whatsapp:id/search_input"),
        d(resourceId="com.whatsapp:id/search_src_text"),
        d(resourceId="com.whatsapp:id/search_text"),
        d(className="android.widget.EditText", focused=True),
        d(className="android.widget.EditText"),
    ]
    for sel in field_candidates:
        if sel.exists(timeout=2):
            search_field = sel
            break

    if search_field is None:
        # Newer WhatsApp builds can focus search without exposing a stable
        # EditText id. Try global keyboard entry as a fallback.
        try:
            d.send_keys(group_name, clear=True)
            time.sleep(1.0)
        except Exception:
            dump_screen(d)
            raise RuntimeError("WhatsApp search field not found")
    else:
        search_field.click()
        time.sleep(0.2)
        try:
            search_field.set_text(group_name)
        except Exception:
            try:
                d.send_keys(group_name, clear=True)
            except Exception:
                d.shell(["input", "text", group_name.replace(" ", "%s")])
        time.sleep(1.0)

    # Prefer conversation title rows in search results.
    names = d(resourceId="com.whatsapp:id/conversations_row_contact_name")
    if names.exists(timeout=2):
        try:
            count = names.count
        except Exception:
            count = 0
        for i in range(count):
            try:
                name_node = names[i]
                text = (name_node.info.get("text") or "").strip()
            except Exception:
                continue
            if not text:
                continue
            if re.search(group_pattern, text):
                try:
                    name_node.click()
                    time.sleep(1.2)
                    return
                except Exception:
                    pass
                rows = d(resourceId="com.whatsapp:id/contact_row_container")
                try:
                    if rows.exists(timeout=0.5) and rows.count > i:
                        rows[i].click()
                        time.sleep(1.2)
                        return
                except Exception:
                    pass

    for sel in (d(text=group_name), d(textContains=group_name), d(textMatches=group_pattern)):
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.2)
            return

    dump_screen(d)
    raise RuntimeError(f"Could not find WhatsApp group: {group_name}")


def type_whatsapp_message(d, message: str) -> None:
    field_candidates = [
        d(resourceId="com.whatsapp:id/entry"),
        d(resourceId="com.whatsapp:id/message"),
        d(text="Message"),
        d(textContains="message"),
        d(className="android.widget.EditText"),
    ]
    field = None
    for sel in field_candidates:
        if sel.exists(timeout=2):
            field = sel
            break

    if field is None:
        dump_screen(d)
        raise RuntimeError("WhatsApp message input field not found")

    field.click()
    time.sleep(0.2)
    try:
        field.clear_text()
    except Exception:
        pass
    try:
        field.set_text(message)
    except Exception:
        try:
            d.send_keys(message, clear=True)
        except Exception:
            # Escape apostrophes for adb shell input text fallback
            safe = message.replace("'", "\\'").replace(" ", "%s")
            d.shell(["input", "text", safe])
    time.sleep(0.6)


def send_whatsapp_message(d) -> None:
    candidates = [
        ("resourceId", "com.whatsapp:id/send"),
        ("description", "Send"),
        ("text", "Send"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.0)
            return

    sel = d(descriptionContains="Send")
    if sel.exists(timeout=2):
        sel.click()
        time.sleep(1.0)
        return

    dump_screen(d)
    raise RuntimeError("WhatsApp send button not found")


def click_open_latest_email(d) -> None:
    # Gmail often shows onboarding/banner rows above the inbox. Dismiss those
    # first so the first sender row really is the latest email.
    for rid in (
        "com.google.android.gm:id/dismiss_icon",
        "com.google.android.gm:id/got_it_button",
    ):
        banner_button = d(resourceId=rid)
        if banner_button.exists(timeout=0.5):
            try:
                banner_button.click()
                time.sleep(0.8)
            except Exception:
                pass

    sender_rows = d(resourceId="com.google.android.gm:id/senders")
    if sender_rows.exists(timeout=3):
        try:
            count = getattr(sender_rows, "count", 0)
            skip_labels = {"Primary", "Promotions", "Updates", "Social", "Forums"}
            for index in range(count):
                row = sender_rows[index]
                try:
                    sender_text = (row.info.get("text") or "").strip()
                except Exception:
                    sender_text = ""
                if not sender_text or sender_text in skip_labels:
                    continue
                row.click()
                time.sleep(1.2)
                return
        except Exception:
            pass

    # Fallback: click the first visible conversation row under the thread list.
    thread_list = d(resourceId="com.google.android.gm:id/thread_list_view")
    if thread_list.exists(timeout=2):
        try:
            rows = thread_list.xpath("//*[@clickable='true']") if hasattr(thread_list, "xpath") else None
            if rows:
                for row in rows:
                    try:
                        desc = (row.attrib.get("content-desc") or "").strip()
                    except Exception:
                        desc = ""
                    if not desc:
                        continue
                    if desc in {"Meet"}:
                        continue
                    if desc.startswith("Unread") or ", , , " in desc:
                        row.click()
                        time.sleep(1.2)
                        return
        except Exception:
            pass

    raise RuntimeError("Could not open latest email")


def extract_email_text(d) -> str:
    def collect_text_from_node(node: ET.Element, parts: list[str]) -> None:
        text = (node.attrib.get("text") or "").strip()
        content_desc = (node.attrib.get("content-desc") or "").strip()
        resource_id = node.attrib.get("resource-id") or ""
        visible = node.attrib.get("visible-to-user") == "true"

        if visible and text:
            parts.append(text)
        elif visible and not text and content_desc and resource_id.endswith("reply_button"):
            parts.append(content_desc)

        for child in list(node):
            collect_text_from_node(child, parts)

    try:
        dump = d.dump_hierarchy(compressed=False)
        root = ET.fromstring(dump)
    except Exception:
        # If the hierarchy cannot be parsed, fall back to a smaller direct scan.
        parts: list[str] = []
        for cls in ("android.widget.TextView", "android.widget.EditText"):
            try:
                nodes = d(className=cls)
                if not nodes.exists(timeout=0.5):
                    continue
                max_i = min(300, getattr(nodes, "count", 50))
                for i in range(max_i):
                    try:
                        n = nodes[i]
                        txt = (n.info.get("text") or "").strip()
                        if txt:
                            parts.append(txt)
                    except Exception:
                        break
            except Exception:
                continue
        return "\n".join(dict.fromkeys(parts))

    parts: list[str] = []
    conversation_nodes = root.findall(
        ".//*[@resource-id='com.google.android.gm:id/conversation_view_container']"
    )
    if conversation_nodes:
        collect_text_from_node(conversation_nodes[0], parts)
    else:
        # If we fail to find the conversation container, scan the full tree but
        # still return only visible text values and not the raw XML.
        collect_text_from_node(root, parts)

    # De-duplicate while keeping the original order.
    seen = set()
    filtered: list[str] = []
    for item in parts:
        if not item or item in seen:
            continue
        seen.add(item)
        filtered.append(item)

    return "\n".join(filtered)


def fill_maps_search_query(d, query: str) -> None:
    field = d(resourceId="com.google.android.apps.maps:id/search_omnibox_text_box")
    if not field.exists(timeout=3):
        raise RuntimeError("Maps search input not found")
    field.click()
    time.sleep(0.2)
    # Try high-level set_text first
    try:
        field.clear_text()
        time.sleep(0.05)
        field.set_text(query)
        time.sleep(0.4)
    except Exception:
        pass

    # If the field still shows no text, fallback to IME/send_keys then adb input
    try:
        text_now = field.info.get("text")
    except Exception:
        text_now = None

    did_send_keys = False
    if not text_now:
        try:
            d.set_input_ime(True)
            d.send_keys(query, clear=True)
            did_send_keys = True
            time.sleep(0.4)
            try:
                text_now = field.info.get("text")
            except Exception:
                text_now = None
        except Exception:
            text_now = None

    if not text_now and not did_send_keys:
        # Final fallback: use adb input text. `input text` expects `%s` for
        # space characters (passing backslashes will insert literal backslashes
        # into the field). Replace spaces with `%s` so the text reads
        # correctly on the device.
        esc = query.replace(" ", "%s")
        try:
            d.shell(["input", "text", esc])
            time.sleep(0.6)
        except Exception:
            # If even adb input fails, raise
            raise RuntimeError("Failed to enter Maps query")


def submit_maps_search(d) -> None:
    try:
        d.press("enter")
        time.sleep(2.0)
        return
    except Exception:
        pass

    candidates = [
        ("description", "Search"),
        ("text", "Search"),
        ("text", "Done"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(2.0)
            return
    raise RuntimeError("Maps search submit action not found")


def text_input_fields_visible(d) -> bool:
    primary_fields = [
        d(resourceId="android:id/input_hour"),
        d(resourceId="android:id/input_minute"),
    ]
    if all(sel.exists(timeout=1) for sel in primary_fields):
        return True

    generic_edits = d(className="android.widget.EditText")
    try:
        if generic_edits.exists(timeout=1):
            return True
    except Exception:
        pass

    return False


def set_alarm_time(d, hour: str, minute: str, period: str = "AM") -> None:
    hour_value = str(int(hour))
    minute_value = minute.zfill(2)
    period_value = period.upper()

    # Find the hour input container by Material Design resource ID
    hour_container = d(resourceId="com.google.android.deskclock:id/material_hour_text_input")
    minute_container = d(resourceId="com.google.android.deskclock:id/material_minute_text_input")

    if not hour_container.exists(timeout=3):
        dump_screen(d)
        raise RuntimeError("Hour container not found")
    
    if not minute_container.exists(timeout=3):
        dump_screen(d)
        raise RuntimeError("Minute container not found")

    # Find the EditText inside the hour container
    hour_field = hour_container.child(className="android.widget.EditText")
    if not hour_field.exists(timeout=2):
        # Try to find it another way - search all EditTexts
        all_edits = d(className="android.widget.EditText")
        if all_edits.exists(timeout=2):
            hour_field = all_edits[0]
        else:
            dump_screen(d)
            raise RuntimeError("Hour EditText not found")

    # Set the hour value
    hour_field.click()
    time.sleep(0.2)
    hour_field.clear_text()
    time.sleep(0.1)
    hour_field.set_text(hour_value)
    time.sleep(0.3)

    # Now click on the minute container to activate it
    minute_container.click()
    time.sleep(0.4)

    # After clicking, look for the EditText inside the minute container
    minute_field = minute_container.child(className="android.widget.EditText")

    if not minute_field.exists(timeout=2):
        # Try clicking on the container again and wait longer
        minute_container.click()
        time.sleep(0.6)
        minute_field = minute_container.child(className="android.widget.EditText")
    
    # Set the minute value
    if minute_field.exists(timeout=2):
        minute_field.click()
        time.sleep(0.2)
        minute_field.clear_text()
        time.sleep(0.1)
        minute_field.set_text(minute_value)
        time.sleep(0.3)
    else:
        # Fallback: try tabbing and using IME
        try:
            d.press("tab")
            time.sleep(0.3)
            d.set_input_ime(True)
            d.send_keys(minute_value, clear=True)
        except Exception as e:
            print(f"Warning: Could not set minute value via IME: {e}")

    # Set AM/PM
    time.sleep(0.2)
    am_btn = d(resourceId="com.google.android.deskclock:id/material_clock_period_am_button")
    pm_btn = d(resourceId="com.google.android.deskclock:id/material_clock_period_pm_button")
    
    if period_value == "AM" and am_btn.exists(timeout=2):
        am_btn.click()
        time.sleep(0.2)
    elif period_value == "PM" and pm_btn.exists(timeout=2):
        pm_btn.click()
        time.sleep(0.2)
    
    time.sleep(0.3)


def confirm_alarm(d) -> None:
    candidates = [
        ("text", "OK"),
        ("text", "Save"),
        ("text", "Done"),
        ("description", "OK"),
        ("description", "Save"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.5)
            return
    raise RuntimeError("Confirm button not found")


def click_gmail_compose(d) -> None:
    candidates = [
        ("resourceId", "com.google.android.gm:id/compose_button"),
        ("resourceId", "com.google.android.gm:id/fab"),
        ("text", "Compose"),
        ("description", "Compose"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=3):
            sel.click()
            time.sleep(1.8)
            return
    d.click(0.9, 0.9)
    time.sleep(1.8)


def fill_gmail_recipient(d, recipient: str) -> None:
    recipient_field = None
    candidate_ids = [
        "com.google.android.gm:id/to",
        "com.google.android.gm:id/people_edit_text",
        "com.google.android.gm:id/recipient_text_view",
    ]
    for rid in candidate_ids:
        sel = d(resourceId=rid)
        if sel.exists(timeout=2):
            recipient_field = sel
            break

    if recipient_field is None:
        if d(className="android.widget.MultiAutoCompleteTextView").exists(timeout=2):
            recipient_field = d(className="android.widget.MultiAutoCompleteTextView")
        elif d(className="android.widget.EditText").exists(timeout=2):
            recipient_field = d(className="android.widget.EditText")[0]

    if recipient_field is None:
        raise RuntimeError("Recipient field not found")

    recipient_field.click()
    time.sleep(0.2)
    recipient_field.clear_text()
    time.sleep(0.1)
    recipient_field.set_text(recipient)
    d.press("enter")
    time.sleep(0.5)


def fill_gmail_subject(d, subject: str) -> None:
    subject_field = d(resourceId="com.google.android.gm:id/subject")
    if not subject_field.exists(timeout=2):
        edits = d(className="android.widget.EditText")
        if edits.exists(timeout=2):
            subject_field = edits[1] if len(edits) > 1 else edits[0]
        else:
            raise RuntimeError("Subject field not found")

    subject_field.click()
    time.sleep(0.2)
    subject_field.clear_text()
    time.sleep(0.1)
    subject_field.set_text(subject)
    time.sleep(0.4)


def fill_gmail_body(d, body: str) -> None:
    body_field = d(resourceId="com.google.android.gm:id/wc_body")
    if not body_field.exists(timeout=2):
        edits = d(className="android.widget.EditText")
        if edits.exists(timeout=2):
            body_field = edits[len(edits) - 1]
        else:
            d.click(0.5, 0.65)
            time.sleep(0.4)
            edits = d(className="android.widget.EditText")
            if edits.exists(timeout=2):
                body_field = edits[len(edits) - 1]
            else:
                raise RuntimeError("Email body field not found")

    body_field.click()
    time.sleep(0.2)
    body_field.clear_text()
    time.sleep(0.1)
    body_field.set_text(body)
    time.sleep(0.4)


def send_gmail(d) -> None:
    candidates = [
        ("resourceId", "com.google.android.gm:id/send"),
        ("description", "Send"),
        ("text", "Send"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.5)
            return
    raise RuntimeError("Send button not found")


def open_contacts_app(d, package: str = CONTACT_PACKAGE) -> None:
    d.app_stop(package)
    time.sleep(0.5)
    d.app_start(package)
    time.sleep(2.5)


def click_add_contact(d) -> None:
    candidates = [
        ("resourceId", "com.google.android.contacts:id/floating_action_button"),
        ("resourceId", "com.google.android.contacts:id/add_contact_button"),
        ("text", "Create contact"),
        ("text", "Add contact"),
        ("description", "Add contact"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.2)
            return
    raise RuntimeError("Add contact button not found")


def get_edit_texts(d):
    edits = d(className="android.widget.EditText")
    if not edits.exists(timeout=2):
        return []
    return [edits[index] for index in range(edits.count)]


def fill_contact_name(d, name: str) -> None:
    edits = get_edit_texts(d)
    if not edits:
        raise RuntimeError("Name field not found")
    name_field = edits[0]
    name_field.click()
    time.sleep(0.2)
    name_field.clear_text()
    time.sleep(0.1)
    name_field.set_text(name)
    time.sleep(0.4)


def proceed_contact_next(d) -> None:
    candidates = [
        ("text", "Next"),
        ("text", "OK"),
        ("description", "Next"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(0.8)
            return
    # Not fatal: some contact UIs auto-show phone field


def fill_contact_phone(d, phone: str) -> None:
    edits = get_edit_texts(d)
    if len(edits) < 4:
        raise RuntimeError("Phone field not found")
    phone_field = edits[3]
    phone_field.click()
    time.sleep(0.2)
    phone_field.clear_text()
    time.sleep(0.1)
    phone_field.set_text(phone)
    time.sleep(0.4)


def save_contact(d) -> None:
    candidates = [
        ("text", "Save"),
        ("text", "Add contact"),
        ("description", "Save"),
    ]
    for kind, value in candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.2)
            return
    raise RuntimeError("Save/Add contact button not found")


def dump_screen(d) -> None:
    print(d.dump_hierarchy())


def run_open_clock(config: DeviceConfig) -> None:
    d = connect(config.serial)
    open_clock_app(d, config.package)


def run_open_alarm(config: DeviceConfig) -> None:
    d = connect(config.serial)
    open_clock_app(d, config.package)
    open_alarm_settings_screen(d)


def run_add_alarm(config: DeviceConfig) -> None:
    d = connect(config.serial)
    open_clock_app(d, config.package)
    open_alarm_tab(d)
    click_add_alarm(d)


def run_set_time(config: DeviceConfig, hour: str, minute: str, period: str) -> None:
    d = connect(config.serial)
    open_clock_app(d, config.package)
    open_alarm_settings_screen(d)
    click_add_alarm(d)
    set_alarm_time(d, hour=hour, minute=minute, period=period)


def run_set_alarm_case(config: DeviceConfig, case: AlarmTestCase) -> None:
    d = connect(config.serial)
    open_clock_app(d, case.package)
    open_alarm_settings_screen(d)
    click_add_alarm(d)
    set_alarm_time(d, hour=case.hour, minute=case.minute, period=case.period)


def run_alarm_case(config: DeviceConfig, case: AlarmTestCase) -> None:
    d = connect(config.serial)
    open_clock_app(d, case.package)
    open_alarm_settings_screen(d)
    click_add_alarm(d)
    set_alarm_time(d, hour=case.hour, minute=case.minute, period=case.period)
    confirm_alarm(d)


def run_confirm(config: DeviceConfig) -> None:
    d = connect(config.serial)
    confirm_alarm(d)


def run_full(config: DeviceConfig, hour: str, minute: str, period: str) -> None:
    d = connect(config.serial)
    open_clock_app(d, config.package)
    open_alarm_settings_screen(d)
    click_add_alarm(d)
    set_alarm_time(d, hour=hour, minute=minute, period=period)
    confirm_alarm(d)


def run_coordinator_task(task_id: str, config: DeviceConfig) -> None:
    if task_id == "task_1":
        run_open_clock(config)
    elif task_id == "task_2":
        run_open_alarm(config)
    elif task_id == "task_3":
        run_set_alarm_case(config, ALARM_CASES["clock_5_35_am"])
    elif task_id == "task_4":
        run_confirm(config)
    else:
        raise ValueError(f"Unknown plan task_id: {task_id}")


def run_gmail_task(task_id: str, config: DeviceConfig, case: GmailTestCase) -> None:
    d = connect(config.serial)

    if task_id == "task_3":
        open_gmail_app(d, case.package)
        return

    if task_id == "task_4":
        open_gmail_app(d, case.package)
        click_gmail_compose(d)
        fill_gmail_recipient(d, case.recipient)
        return

    if task_id == "task_5":
        fill_gmail_subject(d, case.subject)
        return

    if task_id == "task_6":
        fill_gmail_body(d, case.body)
        return

    if task_id == "task_8":
        send_gmail(d)
        return

    raise ValueError(f"Unknown gmail task_id: {task_id}")


def run_gmail_all(config: DeviceConfig, case: GmailTestCase) -> None:
    d = connect(config.serial)
    open_gmail_app(d, case.package)
    click_gmail_compose(d)
    fill_gmail_recipient(d, case.recipient)
    fill_gmail_subject(d, case.subject)
    fill_gmail_body(d, case.body)
    send_gmail(d)


def run_contact_task(task_id: str, config: DeviceConfig, case: ContactTestCase) -> None:
    d = connect(config.serial)
    if task_id == "task_1":
        open_contacts_app(d, case.package)
        return
    if task_id == "task_2":
        open_contacts_app(d, case.package)
        click_add_contact(d)
        return
    if task_id == "task_3":
        fill_contact_name(d, case.name)
        return
    if task_id == "task_4":
        proceed_contact_next(d)
        return
    if task_id == "task_5":
        fill_contact_phone(d, case.phone)
        return
    if task_id == "task_6":
        save_contact(d)
        return
    raise ValueError(f"Unknown contact task_id: {task_id}")


def run_contact_all(config: DeviceConfig, case: ContactTestCase) -> None:
    d = connect(config.serial)
    open_contacts_app(d, case.package)
    click_add_contact(d)
    fill_contact_name(d, case.name)
    fill_contact_phone(d, case.phone)
    save_contact(d)


def run_maps_task(task_id: str, config: DeviceConfig, case: MapsTestCase) -> None:
    d = connect(config.serial)
    if task_id == "task_1":
        open_maps_app(d, case.package)
        return
    if task_id == "task_2":
        open_maps_app(d, case.package)
        click_maps_search_bar(d)
        return
    if task_id == "task_3":
        fill_maps_search_query(d, case.query)
        return
    if task_id == "task_4":
        submit_maps_search(d)
        return
    raise ValueError(f"Unknown maps task_id: {task_id}")


def run_maps_all(config: DeviceConfig, case: MapsTestCase) -> None:
    d = connect(config.serial)
    open_maps_app(d, case.package)
    click_maps_search_bar(d)
    fill_maps_search_query(d, case.query)
    submit_maps_search(d)


def run_email_task(task_id: str, config: DeviceConfig, case: EmailTestCase) -> None:
    d = connect(config.serial)
    if task_id == "task_1":
        open_email_app(d, case.package)
        return
    if task_id == "task_2":
        open_email_app(d, case.package)
        click_open_latest_email(d)
        return
    if task_id == "task_3":
        # Assumes an email is already opened
        text = extract_email_text(d)
        print(text)
        return
    raise ValueError(f"Unknown email task_id: {task_id}")


def run_email_all(config: DeviceConfig, case: EmailTestCase) -> None:
    d = connect(config.serial)
    open_email_app(d, case.package)
    click_open_latest_email(d)
    text = extract_email_text(d)
    print(text)


def run_docs_task(task_id: str, config: DeviceConfig, case: DocsTestCase) -> None:
    d = connect(config.serial)
    if task_id == "task_1":
        open_docs_app(d, case.package)
        return
    if task_id == "task_2":
        open_docs_app(d, case.package)
        click_new_docs_document(d)
        return
    if task_id == "task_3":
        set_docs_title(d, case.title)
        return
    if task_id == "task_4":
        save_docs_document(d)
        return
    raise ValueError(f"Unknown docs task_id: {task_id}")


def run_docs_all(config: DeviceConfig, case: DocsTestCase) -> None:
    d = connect(config.serial)
    open_docs_app(d, case.package)
    click_new_docs_document(d)
    set_docs_title(d, case.title)
    save_docs_document(d)


def run_messages_task(task_id: str, config: DeviceConfig, case: MessageTestCase) -> None:
    d = connect(config.serial)
    if task_id == "task_1":
        open_messages_app(d, case.package)
        return
    if task_id == "task_2":
        open_messages_app(d, case.package)
        open_or_start_message_thread(d, case.recipient)
        return
    if task_id == "task_3":
        type_message_text(d, case.message)
        return
    if task_id == "task_4":
        send_message_now(d)
        return
    raise ValueError(f"Unknown messages task_id: {task_id}")


def run_messages_all(config: DeviceConfig, case: MessageTestCase) -> None:
    d = connect(config.serial)
    open_messages_app(d, case.package)
    open_or_start_message_thread(d, case.recipient)
    type_message_text(d, case.message)
    send_message_now(d)


def run_whatsapp_task(task_id: str, config: DeviceConfig, case: WhatsAppTestCase) -> None:
    d = connect(config.serial)
    if task_id == "task_1":
        open_whatsapp_app(d, case.package)
        return
    if task_id == "task_2":
        open_whatsapp_app(d, case.package)
        open_whatsapp_group_chat(d, case.group_name)
        return
    if task_id == "task_3":
        type_whatsapp_message(d, case.message)
        return
    if task_id == "task_4":
        send_whatsapp_message(d)
        return
    raise ValueError(f"Unknown whatsapp task_id: {task_id}")


def run_whatsapp_all(config: DeviceConfig, case: WhatsAppTestCase) -> None:
    d = connect(config.serial)
    open_whatsapp_app(d, case.package)
    open_whatsapp_group_chat(d, case.group_name)
    type_whatsapp_message(d, case.message)
    send_whatsapp_message(d)


def open_calendar_app(d, package: str = CALENDAR_PACKAGE) -> None:
    d.app_start(package)
    time.sleep(2.5)


def create_new_calendar_event(d) -> None:
    """Tap the button to create a new event (usually a '+' or 'Add' button).
    
    In Google Calendar, this is the FAB "Creation menu" button at the bottom-right,
    which opens a menu with options: Birthday, Task, Event.
    We need to select "Event" from the menu.
    """
    # Try the "Creation menu" description (Compose-based FAB) or the fab_container
    creation_menu = d(description="Creation menu")
    if not creation_menu.exists(timeout=1):
        creation_menu = d(descriptionContains="Creation")
    if not creation_menu.exists(timeout=1):
        creation_menu = d(resourceId="com.google.android.calendar:id/fab_container")

    if creation_menu.exists(timeout=2):
        try:
            creation_menu.click()
        except Exception:
            try:
                info = creation_menu.info
                bounds = info.get("bounds") or ""
                nums = [int(n) for n in bounds.replace('][', ',').replace('[','').replace(']','').split(',') if n]
                if len(nums) == 4:
                    x1, y1, x2, y2 = nums
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    d.click(cx, cy)
            except Exception:
                pass
        time.sleep(0.8)

        # Now click on "Event" option in the menu. The visible TextView may not be
        # clickable, so click the bounds center if necessary.
        event_option = d(text="Event")
        if event_option.exists(timeout=2):
            try:
                event_option.click()
            except Exception:
                try:
                    info = event_option.info
                    bounds = info.get("bounds") or ""
                    nums = [int(n) for n in bounds.replace('][', ',').replace('[','').replace(']','').split(',') if n]
                    if len(nums) == 4:
                        x1, y1, x2, y2 = nums
                        cx = (x1 + x2) // 2
                        cy = (y1 + y2) // 2
                        d.click(cx, cy)
                except Exception:
                    pass
            time.sleep(1.2)
            return

    # Fallback: try traditional button selectors
    create_candidates = [
        ("resourceId", "com.google.android.calendar:id/fab"),
        ("resourceId", "com.google.android.calendar:id/action_create_event"),
        ("description", "Create"),
        ("description", "Add"),
        ("description", "New event"),
        ("text", "Create"),
        ("text", "Add"),
    ]
    for kind, value in create_candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.0)
            return

    dump_screen(d)
    raise RuntimeError("Calendar create event button not found")


def set_calendar_event_title(d, title: str) -> None:
    """Set the event title by typing in the title field.
    
    The event creation dialog should have a title/event name EditText field.
    """
    # Wait a bit for the event dialog to fully render
    time.sleep(1.5)
    
    # Look for title field by various selectors
    title_candidates = [
        d(resourceId="com.google.android.calendar:id/title"),
        d(resourceId="com.google.android.calendar:id/event_title"),
        d(resourceId="com.google.android.calendar:id/edit_event_title"),
        d(text="Add title"),
        d(textContains="title"),
        d(className="android.widget.EditText"),
    ]
    
    title_field = None
    for sel in title_candidates:
        if sel.exists(timeout=1):
            title_field = sel
            break

    # If not found, try to find the first EditText in the dialog (usually the title field)
    if title_field is None:
        all_edits = d(className="android.widget.EditText")
        if all_edits.exists(timeout=4):
            try:
                count = getattr(all_edits, "count", 0)
                if count > 0:
                    title_field = all_edits[0]  # First EditText is usually the title
            except Exception:
                pass

    if title_field is None:
        dump_screen(d)
        raise RuntimeError("Calendar title field not found")

    title_field.click()
    time.sleep(0.2)
    try:
        title_field.clear_text()
    except Exception:
        pass
    try:
        title_field.set_text(title)
    except Exception:
        try:
            d.send_keys(title, clear=True)
        except Exception:
            d.shell(["input", "text", title])
    time.sleep(0.6)


def set_calendar_event_date(d, date_str: str) -> None:
    """Set the event date to the specified date (format: YYYY-MM-DD or M/D/YYYY).
    
    Per user note: UI is complex. Workflow is: write title, click date field, 
    switch to text input mode, type date.
    """
    # Find date field by various resource IDs or hints
    date_candidates = [
        d(resourceId="com.google.android.calendar:id/date"),
        d(resourceId="com.google.android.calendar:id/event_date"),
        d(resourceId="com.google.android.calendar:id/date_picker"),
        d(resourceId="com.google.android.calendar:id/start_date"),
        d(text="Date"),
        d(textContains="date"),
        d(text="Add date"),
    ]
    date_field = None
    for sel in date_candidates:
        if sel.exists(timeout=2):
            date_field = sel
            break

    if date_field is None:
        # Try to find any field that looks like a date input by scanning EditTexts
        all_edits = d(className="android.widget.EditText")
        try:
            if all_edits.exists(timeout=1):
                count = getattr(all_edits, "count", 0)
                # Second EditText is often the date field (first is title)
                if count >= 2:
                    date_field = all_edits[1]
        except Exception:
            pass

    if date_field is None:
        # Broaden search: look for clickable nodes that look like dates (contain '/'
        # or match numeric date patterns), or buttons labelled Date/Start/End.
        candidates = []
        try:
            candidates.extend([d(textMatches=r"\d{1,2}/\d{1,2}/\d{4}"), d(textContains='/')])
        except Exception:
            # older uiautomator2 may not accept raw regex in this context
            try:
                candidates.append(d(textContains='/'))
            except Exception:
                pass
        candidates.extend([
            d(textContains="Date"),
            d(textContains="date"),
            d(textContains="Start"),
            d(textContains="End"),
        ])
        # Also consider visible buttons/textviews that are clickable
        candidates.append(d(className="android.widget.Button"))

        for sel in candidates:
            try:
                if sel.exists(timeout=0.8):
                    # if it's a collection, iterate
                    try:
                        count = getattr(sel, 'count', 0)
                    except Exception:
                        count = 0
                    if count and count > 1:
                        for i in range(count):
                            node = sel[i]
                            try:
                                if node.info.get('clickable'):
                                    date_field = node
                                    break
                            except Exception:
                                continue
                        if date_field is not None:
                            break
                    else:
                        try:
                            if sel.info.get('clickable'):
                                date_field = sel
                                break
                        except Exception:
                            # single element without .info may still be usable
                            date_field = sel
                            break
            except Exception:
                continue

        if date_field is None:
            dump_screen(d)
            raise RuntimeError("Calendar date field not found")

    # Click the date field to activate it
    date_field.click()
    time.sleep(0.5)

    # Try to set the date text directly (mode may switch to text input)
    try:
        date_field.clear_text()
    except Exception:
        pass

    # Convert ISO format (2026-10-07) to user-friendly format (7/10/2026) if needed
    if date_str == "2026-10-07":
        date_text = "7/10/2026"
    elif "-" in date_str:
        # Try to parse ISO format and convert
        parts = date_str.split("-")
        if len(parts) == 3:
            year, month, day = parts
            date_text = f"{int(day)}/{int(month)}/{year}"
        else:
            date_text = date_str
    else:
        date_text = date_str

    try:
        date_field.set_text(date_text)
    except Exception:
        try:
            d.send_keys(date_text, clear=True)
        except Exception:
            # Escape spaces for adb input
            safe_date = date_text.replace(" ", "%s")
            d.shell(["input", "text", safe_date])

    time.sleep(0.6)

    # If typing into the date field didn't take effect, try alternate flows:
    # 1) If a date picker dialog appeared, try switching to text/keyboard input
    # 2) Find any EditText on screen (excluding the title) and type the date there
    # 3) As last resort, try to type via adb and hope the focused field accepts it
    time.sleep(0.4)

    # Try to find and click a "text input" / "keyboard" toggle in the date picker
    toggle_candidates = [
        d(textContains="text input"),
        d(textContains="Keyboard"),
        d(textContains="Use text"),
        d(descriptionContains="text input"),
        d(descriptionContains="Keyboard"),
    ]
    for toggle in toggle_candidates:
        try:
            if toggle.exists(timeout=0.8):
                try:
                    toggle.click()
                    time.sleep(0.4)
                except Exception:
                    # click bounds center fallback
                    try:
                        info = toggle.info
                        bounds = info.get("bounds") or ""
                        nums = [int(n) for n in bounds.replace('][', ',').replace('[','').replace(']','').split(',') if n]
                        if len(nums) == 4:
                            x1, y1, x2, y2 = nums
                            d.click((x1 + x2)//2, (y1 + y2)//2)
                            time.sleep(0.4)
                    except Exception:
                        pass
                break
        except Exception:
            continue

    # Now try to find an EditText other than the title field and type the date
    try:
        edits = d(className="android.widget.EditText")
        if edits.exists(timeout=1):
            # try to find the edit that is not empty and not the title (usually second)
            try:
                count = getattr(edits, "count", 0)
            except Exception:
                count = 0
            target = None
            if count >= 2:
                target = edits[1]
            elif count == 1:
                target = edits[0]

            if target is not None:
                try:
                    target.click()
                    time.sleep(0.2)
                    target.clear_text()
                except Exception:
                    pass
                try:
                    target.set_text(date_text)
                    time.sleep(0.6)
                    return
                except Exception:
                    try:
                        d.send_keys(date_text, clear=True)
                        time.sleep(0.6)
                        return
                    except Exception:
                        pass

    except Exception:
        pass

    # Final fallback: adb input (focused field)
    try:
        safe_date = date_text.replace(" ", "%s")
        d.shell(["input", "text", safe_date])
        time.sleep(0.6)
        return
    except Exception:
        dump_screen(d)
        raise RuntimeError("Failed to set calendar date")


def save_calendar_event(d) -> None:
    """Save the event by tapping the 'Save' or 'Done' button."""
    save_candidates = [
        ("resourceId", "com.google.android.calendar:id/save"),
        ("resourceId", "com.google.android.calendar:id/action_save"),
        ("description", "Save"),
        ("description", "Done"),
        ("text", "Save"),
        ("text", "Done"),
    ]
    for kind, value in save_candidates:
        sel = select(d, kind, value)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.0)
            return

    # Try text button matches
    for text_val in ["Save", "Done", "Create"]:
        sel = d(text=text_val)
        if sel.exists(timeout=1):
            sel.click()
            time.sleep(1.0)
            return

    dump_screen(d)
    raise RuntimeError("Calendar save/done button not found")


def run_calendar_task(task_id: str, config: DeviceConfig, case: CalendarEventTestCase) -> None:
    d = connect(config.serial)
    if task_id == "task_1":
        open_calendar_app(d, case.package)
        return
    if task_id == "task_2":
        open_calendar_app(d, case.package)
        create_new_calendar_event(d)
        return
    if task_id == "task_3":
        set_calendar_event_title(d, case.title)
        return
    if task_id == "task_4":
        set_calendar_event_date(d, case.date)
        return
    if task_id == "task_5":
        save_calendar_event(d)
        return
    raise ValueError(f"Unknown calendar task_id: {task_id}")


def run_calendar_all(config: DeviceConfig, case: CalendarEventTestCase) -> None:
    d = connect(config.serial)
    open_calendar_app(d, case.package)
    create_new_calendar_event(d)
    set_calendar_event_title(d, case.title)
    set_calendar_event_date(d, case.date)
    save_calendar_event(d)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual alarm decomposition runner")
    parser.add_argument(
        "step",
        choices=[
            "open_clock",
            "open_alarm",
            "add_alarm",
            "set_time",
            "confirm",
            "full",
            "dump",
            "plan_1_task_1",
            "plan_1_task_2",
            "plan_1_task_3",
            "plan_1_task_4",
            "plan_1_all",
            "contact_task_1",
            "contact_task_2",
            "contact_task_3",
            "contact_task_4",
            "contact_task_5",
            "contact_task_6",
            "contact_all",
            "maps_task_1",
            "maps_task_2",
            "maps_task_3",
            "maps_task_4",
            "maps_all",
            "gmail_task_3",
            "gmail_task_4",
            "gmail_task_5",
            "gmail_task_6",
            "gmail_task_8",
            "gmail_all",
            "email_task_1",
            "email_task_2",
            "email_task_3",
            "email_all",
            "docs_task_1",
            "docs_task_2",
            "docs_task_3",
            "docs_task_4",
            "docs_all",
            "messages_task_1",
            "messages_task_2",
            "messages_task_3",
            "messages_task_4",
            "messages_all",
            "whatsapp_task_1",
            "whatsapp_task_2",
            "whatsapp_task_3",
            "whatsapp_task_4",
            "whatsapp_all",
            "calendar_task_1",
            "calendar_task_2",
            "calendar_task_3",
            "calendar_task_4",
            "calendar_task_5",
            "calendar_all",
        ],
    )
    parser.add_argument("--serial", default=os.getenv("ANDROID_SERIAL", ""), help="ADB serial or emulator id")
    parser.add_argument("--package", default=DEFAULT_PACKAGE, help="Clock package name")
    parser.add_argument("--hour", default="7")
    parser.add_argument("--minute", default="00")
    parser.add_argument("--period", default="AM")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = DeviceConfig(serial=args.serial, package=args.package)
    gmail_case = GMAIL_CASES["gmail_hello_world"]
    maps_case = MAPS_CASES["maps_coffee_shops"]

    steps: Dict[str, Callable[[], None]] = {
        "open_clock": lambda: run_open_clock(config),
        "open_alarm": lambda: run_open_alarm(config),
        "add_alarm": lambda: run_add_alarm(config),
        "set_time": lambda: run_set_time(config, args.hour, args.minute, args.period),
        "confirm": lambda: run_confirm(config),
        "full": lambda: run_full(config, args.hour, args.minute, args.period),
        "dump": lambda: dump_screen(connect(config.serial)),
        "plan_1_task_1": lambda: run_coordinator_task("task_1", config),
        "plan_1_task_2": lambda: run_coordinator_task("task_2", config),
        "plan_1_task_3": lambda: run_coordinator_task("task_3", config),
        "plan_1_task_4": lambda: run_coordinator_task("task_4", config),
        "plan_1_all": lambda: run_alarm_case(config, ALARM_CASES["clock_5_35_am"]),
        "contact_task_1": lambda: run_contact_task("task_1", config, CONTACT_CASES["contact_john"]),
        "contact_task_2": lambda: run_contact_task("task_2", config, CONTACT_CASES["contact_john"]),
        "contact_task_3": lambda: run_contact_task("task_3", config, CONTACT_CASES["contact_john"]),
        "contact_task_4": lambda: run_contact_task("task_4", config, CONTACT_CASES["contact_john"]),
        "contact_task_5": lambda: run_contact_task("task_5", config, CONTACT_CASES["contact_john"]),
        "contact_task_6": lambda: run_contact_task("task_6", config, CONTACT_CASES["contact_john"]),
        "contact_all": lambda: run_contact_all(config, CONTACT_CASES["contact_john"]),
        "maps_task_1": lambda: run_maps_task("task_1", config, maps_case),
        "maps_task_2": lambda: run_maps_task("task_2", config, maps_case),
        "maps_task_3": lambda: run_maps_task("task_3", config, maps_case),
        "maps_task_4": lambda: run_maps_task("task_4", config, maps_case),
        "maps_all": lambda: run_maps_all(config, maps_case),
        "gmail_task_3": lambda: run_gmail_task("task_3", config, gmail_case),
        "gmail_task_4": lambda: run_gmail_task("task_4", config, gmail_case),
        "gmail_task_5": lambda: run_gmail_task("task_5", config, gmail_case),
        "gmail_task_6": lambda: run_gmail_task("task_6", config, gmail_case),
        "gmail_task_8": lambda: run_gmail_task("task_8", config, gmail_case),
        "gmail_all": lambda: run_gmail_all(config, gmail_case),
        "email_task_1": lambda: run_email_task("task_1", config, EMAIL_CASES["email_inbox"]),
        "email_task_2": lambda: run_email_task("task_2", config, EMAIL_CASES["email_inbox"]),
        "email_task_3": lambda: run_email_task("task_3", config, EMAIL_CASES["email_inbox"]),
        "email_all": lambda: run_email_all(config, EMAIL_CASES["email_inbox"]),
        "docs_task_1": lambda: run_docs_task("task_1", config, DOCS_CASES["docs_final_thesis"]),
        "docs_task_2": lambda: run_docs_task("task_2", config, DOCS_CASES["docs_final_thesis"]),
        "docs_task_3": lambda: run_docs_task("task_3", config, DOCS_CASES["docs_final_thesis"]),
        "docs_task_4": lambda: run_docs_task("task_4", config, DOCS_CASES["docs_final_thesis"]),
        "docs_all": lambda: run_docs_all(config, DOCS_CASES["docs_final_thesis"]),
        "messages_task_1": lambda: run_messages_task("task_1", config, MESSAGE_CASES["messages_haya_on_my_way"]),
        "messages_task_2": lambda: run_messages_task("task_2", config, MESSAGE_CASES["messages_haya_on_my_way"]),
        "messages_task_3": lambda: run_messages_task("task_3", config, MESSAGE_CASES["messages_haya_on_my_way"]),
        "messages_task_4": lambda: run_messages_task("task_4", config, MESSAGE_CASES["messages_haya_on_my_way"]),
        "messages_all": lambda: run_messages_all(config, MESSAGE_CASES["messages_haya_on_my_way"]),
        "whatsapp_task_1": lambda: run_whatsapp_task("task_1", config, WHATSAPP_CASES["whatsapp_me4_grad"]),
        "whatsapp_task_2": lambda: run_whatsapp_task("task_2", config, WHATSAPP_CASES["whatsapp_me4_grad"]),
        "whatsapp_task_3": lambda: run_whatsapp_task("task_3", config, WHATSAPP_CASES["whatsapp_me4_grad"]),
        "whatsapp_task_4": lambda: run_whatsapp_task("task_4", config, WHATSAPP_CASES["whatsapp_me4_grad"]),
        "whatsapp_all": lambda: run_whatsapp_all(config, WHATSAPP_CASES["whatsapp_me4_grad"]),
        "calendar_task_1": lambda: run_calendar_task("task_1", config, CALENDAR_CASES["calendar_victory_2026_10_07"]),
        "calendar_task_2": lambda: run_calendar_task("task_2", config, CALENDAR_CASES["calendar_victory_2026_10_07"]),
        "calendar_task_3": lambda: run_calendar_task("task_3", config, CALENDAR_CASES["calendar_victory_2026_10_07"]),
        "calendar_task_4": lambda: run_calendar_task("task_4", config, CALENDAR_CASES["calendar_victory_2026_10_07"]),
        "calendar_task_5": lambda: run_calendar_task("task_5", config, CALENDAR_CASES["calendar_victory_2026_10_07"]),
        "calendar_all": lambda: run_calendar_all(config, CALENDAR_CASES["calendar_victory_2026_10_07"]),
    }

    steps[args.step]()


if __name__ == "__main__":
    main()