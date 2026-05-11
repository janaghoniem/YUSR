"""
uiautomator_harness.py — Standalone harness with proven UI automation functions.

This module contains the canonical harness functions used to exercise mobile app
workflows. Each function is carefully tested and can be injected into ChromaDB
as a reusable template after parameterization.

Functions are organized by app:
  - Clock: open_clock_app, open_alarm_settings_screen, click_add_alarm, set_alarm_time, confirm_alarm
  - Gmail: open_gmail_app, click_gmail_compose, fill_gmail_recipient, fill_gmail_subject, fill_gmail_body, send_gmail
  - Contacts: open_contacts_app, click_add_contact, fill_contact_name, fill_contact_phone, save_contact
  - Maps: open_maps_app, click_maps_search_bar, fill_maps_search_query, submit_maps_search
  - Messages: open_messages_app, open_or_start_message_thread, type_message_text, send_message_now
  - WhatsApp: open_whatsapp_app, open_whatsapp_group_chat, type_whatsapp_message, send_whatsapp_message
  - Calendar: open_calendar_app, create_new_calendar_event, set_calendar_event_title, set_calendar_event_date, save_calendar_event
  - Docs: open_docs_app, click_new_docs_document, set_docs_title

Each function takes a connected uiautomator2 device `d` and optional parameters.
The executor pre-declares `d`, so these functions don't need to call u2.connect().
"""

import re
import time
import xml.etree.ElementTree as ET


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def select(d, kind: str, value: str):
    """Helper to create selectors by kind (description, text, resourceId)."""
    mapping = {
        "description": "description",
        "text": "text",
        "resourceId": "resourceId",
    }
    return d(**{mapping[kind]: value})


def dump_screen(d) -> None:
    """Print the UI hierarchy for debugging."""
    print(d.dump_hierarchy())


# ══════════════════════════════════════════════════════════════════════════════
#  CLOCK HARNESS
# ══════════════════════════════════════════════════════════════════════════════

def open_clock_app(d, package: str = "com.google.android.deskclock") -> None:
    """Launch the Clock app."""
    d.app_start(package)
    time.sleep(2.5)


def open_alarm_settings_screen(d) -> None:
    """Navigate to the alarm list/settings screen."""
    if is_alarm_screen_visible(d):
        return
    open_alarm_tab(d)


def is_alarm_screen_visible(d) -> bool:
    """Check if the alarm screen is currently visible."""
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
    """Click on the Alarm tab to show the alarm list."""
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
    """Click the 'Add Alarm' button or FAB."""
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


def set_alarm_time(d, hour: str, minute: str, period: str = "AM") -> None:
    """Set the alarm time in the Material time picker."""
    hour_value = str(int(hour))
    minute_value = minute.zfill(2)
    period_value = period.upper()

    hour_container = d(resourceId="com.google.android.deskclock:id/material_hour_text_input")
    minute_container = d(resourceId="com.google.android.deskclock:id/material_minute_text_input")

    if not hour_container.exists(timeout=3):
        dump_screen(d)
        raise RuntimeError("Hour container not found")
    
    if not minute_container.exists(timeout=3):
        dump_screen(d)
        raise RuntimeError("Minute container not found")

    hour_field = hour_container.child(className="android.widget.EditText")
    if not hour_field.exists(timeout=2):
        all_edits = d(className="android.widget.EditText")
        if all_edits.exists(timeout=2):
            hour_field = all_edits[0]
        else:
            dump_screen(d)
            raise RuntimeError("Hour EditText not found")

    hour_field.click()
    time.sleep(0.2)
    hour_field.clear_text()
    time.sleep(0.1)
    hour_field.set_text(hour_value)
    time.sleep(0.3)

    minute_container.click()
    time.sleep(0.4)

    minute_field = minute_container.child(className="android.widget.EditText")
    if not minute_field.exists(timeout=2):
        minute_container.click()
        time.sleep(0.6)
        minute_field = minute_container.child(className="android.widget.EditText")
    
    if minute_field.exists(timeout=2):
        minute_field.click()
        time.sleep(0.2)
        minute_field.clear_text()
        time.sleep(0.1)
        minute_field.set_text(minute_value)
        time.sleep(0.3)
    else:
        try:
            d.press("tab")
            time.sleep(0.3)
            d.set_input_ime(True)
            d.send_keys(minute_value, clear=True)
        except Exception as e:
            print(f"Warning: Could not set minute value via IME: {e}")

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
    """Click the OK/Save/Done button to confirm the alarm."""
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


# ══════════════════════════════════════════════════════════════════════════════
#  GMAIL HARNESS
# ══════════════════════════════════════════════════════════════════════════════

def open_gmail_app(d, package: str = "com.google.android.gm") -> None:
    """Launch the Gmail app."""
    d.app_start(package)
    time.sleep(2.5)


def click_gmail_compose(d) -> None:
    """Click the Compose button to start a new email."""
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
    """Fill the To field with a recipient email."""
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
    """Fill the Subject field with an email subject."""
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
    """Fill the email body with message text."""
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
    """Click the Send button to send the email."""
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


# ══════════════════════════════════════════════════════════════════════════════
#  CONTACTS HARNESS
# ══════════════════════════════════════════════════════════════════════════════

def open_contacts_app(d, package: str = "com.google.android.contacts") -> None:
    """Launch the Contacts app."""
    d.app_stop(package)
    time.sleep(0.5)
    d.app_start(package)
    time.sleep(2.5)


def click_add_contact(d) -> None:
    """Click the Add Contact button."""
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
    """Helper to get all EditText fields."""
    edits = d(className="android.widget.EditText")
    if not edits.exists(timeout=2):
        return []
    return [edits[index] for index in range(edits.count)]


def fill_contact_name(d, name: str) -> None:
    """Fill the contact name field."""
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


def fill_contact_phone(d, phone: str) -> None:
    """Fill the contact phone number field."""
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
    """Click the Save/Add Contact button."""
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


# ══════════════════════════════════════════════════════════════════════════════
#  MAPS HARNESS
# ══════════════════════════════════════════════════════════════════════════════

def open_maps_app(d, package: str = "com.google.android.apps.maps") -> None:
    """Launch Google Maps."""
    d.app_stop(package)
    time.sleep(0.5)
    d.app_start(package)
    time.sleep(3.5)
    negative = d(resourceId="com.google.android.apps.maps:id/negative_button")
    positive = d(resourceId="com.google.android.apps.maps:id/positive_button")
    if negative.exists(timeout=1):
        try:
            negative.click()
            time.sleep(1.0)
        except Exception:
            pass
    elif positive.exists(timeout=1):
        try:
            cancel = d(text="Cancel")
            if cancel.exists(timeout=1):
                cancel.click()
                time.sleep(1.0)
            else:
                positive.click()
                time.sleep(1.0)
        except Exception:
            pass


def click_maps_search_bar(d) -> None:
    """Click the search bar in Google Maps."""
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


def fill_maps_search_query(d, query: str) -> None:
    """Type a search query into the Maps search bar."""
    field = d(resourceId="com.google.android.apps.maps:id/search_omnibox_text_box")
    if not field.exists(timeout=3):
        raise RuntimeError("Maps search input not found")
    field.click()
    time.sleep(0.2)
    try:
        field.clear_text()
        time.sleep(0.05)
        field.set_text(query)
        time.sleep(0.4)
    except Exception:
        pass

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
        esc = query.replace(" ", "%s")
        try:
            d.shell(["input", "text", esc])
            time.sleep(0.6)
        except Exception:
            raise RuntimeError("Failed to enter Maps query")


def submit_maps_search(d) -> None:
    """Submit the Maps search by pressing Enter."""
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


# ══════════════════════════════════════════════════════════════════════════════
#  MESSAGES HARNESS
# ══════════════════════════════════════════════════════════════════════════════

def open_messages_app(d, package: str = "com.google.android.apps.messaging") -> None:
    """Launch the Messages app."""
    d.app_start(package)
    time.sleep(2.5)


def open_or_start_message_thread(d, recipient: str) -> None:
    """Open an existing conversation or start a new one with a recipient."""
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

    d.press("enter")
    time.sleep(1.0)


def type_message_text(d, message: str) -> None:
    """Type a message into the message input field."""
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
    """Click the Send button to send the message."""
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
    for desc in ("Send SMS", "Send message", "Send MMS"):
        sel = d(description=desc)
        if sel.exists(timeout=2):
            sel.click()
            time.sleep(1.2)
            return

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


# ══════════════════════════════════════════════════════════════════════════════
#  WHATSAPP HARNESS
# ══════════════════════════════════════════════════════════════════════════════

def open_whatsapp_app(d, package: str = "com.whatsapp") -> None:
    """Launch WhatsApp."""
    d.app_start(package)
    time.sleep(2.5)


def open_whatsapp_group_chat(d, group_name: str) -> None:
    """Open a WhatsApp group chat by name."""
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
    """Type a message into the WhatsApp message box."""
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
            safe = message.replace("'", "\\'").replace(" ", "%s")
            d.shell(["input", "text", safe])
    time.sleep(0.6)


def send_whatsapp_message(d) -> None:
    """Click the Send button to send the WhatsApp message."""
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


# ══════════════════════════════════════════════════════════════════════════════
#  CALENDAR HARNESS
# ══════════════════════════════════════════════════════════════════════════════

def open_calendar_app(d, package: str = "com.google.android.calendar") -> None:
    """Launch Google Calendar."""
    d.app_start(package)
    time.sleep(2.5)


def create_new_calendar_event(d) -> None:
    """Tap the Create button to start a new calendar event."""
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
    """Set the calendar event title."""
    time.sleep(1.5)
    
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

    if title_field is None:
        all_edits = d(className="android.widget.EditText")
        if all_edits.exists(timeout=4):
            try:
                count = getattr(all_edits, "count", 0)
                if count > 0:
                    title_field = all_edits[0]
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
    """Set the calendar event date."""
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
        all_edits = d(className="android.widget.EditText")
        try:
            if all_edits.exists(timeout=1):
                count = getattr(all_edits, "count", 0)
                if count >= 2:
                    date_field = all_edits[1]
        except Exception:
            pass

    if date_field is None:
        dump_screen(d)
        raise RuntimeError("Calendar date field not found")

    date_field.click()
    time.sleep(0.5)

    try:
        date_field.clear_text()
    except Exception:
        pass

    if date_str == "2026-10-07":
        date_text = "7/10/2026"
    elif "-" in date_str:
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
            safe_date = date_text.replace(" ", "%s")
            d.shell(["input", "text", safe_date])

    time.sleep(0.6)


def save_calendar_event(d) -> None:
    """Save the calendar event by clicking the Save/Done button."""
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

    for text_val in ["Save", "Done", "Create"]:
        sel = d(text=text_val)
        if sel.exists(timeout=1):
            sel.click()
            time.sleep(1.0)
            return

    dump_screen(d)
    raise RuntimeError("Calendar save/done button not found")


# ══════════════════════════════════════════════════════════════════════════════
#  DOCS HARNESS
# ══════════════════════════════════════════════════════════════════════════════

def open_docs_app(d, package: str = "com.google.android.apps.docs.editors.docs") -> None:
    """Launch Google Docs."""
    d.app_start(package)
    time.sleep(3.0)


def click_new_docs_document(d) -> None:
    """Click the button to create a new blank Google Docs document."""
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
    """Set the Google Docs document title."""
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

    d.press("tab")
    time.sleep(0.2)
    try:
        d.send_keys(title, clear=True)
    except Exception:
        d.shell(["input", "text", title.replace(" ", "%s")])
    time.sleep(0.8)
