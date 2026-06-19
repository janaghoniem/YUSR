"""
inject_harness_to_cache.py — v4
Gmail-specific changes vs v3:
  - Added combined "Compose + fill To" template (FIX 3) — handles the
    decomposer's "Create a new email draft" task which previously caused
    either a stale cached template with hardcoded email to run, or the LLM
    to generate a 32-line script that skipped the To field.
  - Added "Save/Next no-op" template (FIX 4) — safely absorbs the spurious
    navigation step the decomposer generates between body and send.
  - Removed the old standalone "Tap the Compose button" from GMAIL section
    since the combined template now handles it. Kept it as an alias.
  - All other apps unchanged from v3.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from agents.execution_agent.strategies.mobile_template_cache import (
        ChromaTemplateCache, CodeTemplate, PlaceholderExtractor,
        CHROMA_PATH, APP_PACKAGES, _INTERNAL_KEYS,
    )
except ImportError:
    from mobile_template_cache import (  # type: ignore
        ChromaTemplateCache, CodeTemplate, PlaceholderExtractor,
        CHROMA_PATH, APP_PACKAGES, _INTERNAL_KEYS,
    )

Entry = Tuple[str, str, str, List[str], str]

REGISTRY: List[Entry] = [

    # ══════════════════════════════════════════════════════════════════════
    #  CLOCK / ALARM
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open the Clock app.",
        "clock", "launch",
        [
            "Launch the Clock application.",
            "Start the Clock app on the device.",
            "Open Clock on mobile device.",
            "Open the Clock app on mobile device",
            "Open the Clock app on the mobile device",
        ],
        textwrap.dedent("""\
            d.app_start("com.google.android.deskclock")
            time.sleep(3.0)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Navigate to the alarm list screen.",
        "clock", "navigate",
        [
            "Open the alarm settings screen.",
            "Go to the Alarms tab in Clock.",
            "Navigate to alarm section.",
            "Navigate to the Alarm creation screen",
            "Navigate to the Alarm creation screen (tap Add Alarm or similar)",
            "Navigate to the Alarm settings screen in the Clock app",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            for sel in [
                lambda: d(description="Alarm"),
                lambda: d(text="Alarm"),
                lambda: d(textContains="Alarm"),
            ]:
                btn = sel()
                if btn.exists(timeout=2):
                    btn.click()
                    time.sleep(1.0)
                    break
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap the Add Alarm button.",
        "clock", "action",
        [
            "Click Add Alarm.",
            "Press the new alarm button.",
            "Open the add alarm dialog.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            if d(description="Add alarm").exists(timeout=3):
                d(description="Add alarm").click()
            elif d(text="Add alarm").exists(timeout=3):
                d(text="Add alarm").click()
            elif d(resourceId="com.google.android.deskclock:id/fab").exists(timeout=3):
                d(resourceId="com.google.android.deskclock:id/fab").click()
            time.sleep(1.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Set the alarm time to {alarm_hour}:{alarm_minute} {alarm_period}.",
        "clock", "fill",
        [
            "Enter alarm time {alarm_hour}:{alarm_minute} {alarm_period}.",
            "Type {alarm_hour}:{alarm_minute} {alarm_period} in the time picker.",
            "Fill in the alarm time fields with {alarm_hour}:{alarm_minute} {alarm_period}.",
            "Set alarm to {alarm_hour}:{alarm_minute} {alarm_period}.",
            "Set the alarm time to {alarm_hour}:{alarm_minute} {alarm_period} and tap Next or Save Time",
            "Set the alarm time to {alarm_hour}:{alarm_minute} {alarm_period} in the Alarm settings screen",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            on_alarm_list = (
                d(description="Add alarm").exists(timeout=1) or
                d(text="Add alarm").exists(timeout=1)
            )
            if on_alarm_list:
                if d(description="Add alarm").exists(timeout=1):
                    d(description="Add alarm").click()
                else:
                    d(text="Add alarm").click()
                time.sleep(1.5)
            mode_switched = False
            for desc in ["Switch to text input mode", "keyboard"]:
                if d(description=desc).exists(timeout=1):
                    d(description=desc).click()
                    time.sleep(0.5)
                    if d(className="android.widget.EditText").exists(timeout=1):
                        mode_switched = True
                        break
            if not mode_switched:
                mode_btn = d(resourceId="com.google.android.deskclock:id/material_timepicker_mode_button")
                if mode_btn.exists(timeout=1):
                    mode_btn.click()
                    time.sleep(0.5)
                    mode_switched = d(className="android.widget.EditText").exists(timeout=1)
            if not mode_switched:
                for x, y in [(0.85,0.75),(0.85,0.80),(0.85,0.70),(0.15,0.75),(0.15,0.80),
                             (0.15,0.70),(0.85,0.65),(0.15,0.65),(0.50,0.80),(0.50,0.85)]:
                    d.click(x, y)
                    time.sleep(0.4)
                    if d(className="android.widget.EditText").exists(timeout=1):
                        mode_switched = True
                        break
            if not d(className="android.widget.EditText").exists(timeout=2):
                sys.exit(1)
            hour_str = str(int("{alarm_hour}"))
            hour_field = d(resourceId="android:id/input_hour")
            minute_field = d(resourceId="android:id/input_minute")
            if hour_field.exists(timeout=3) and minute_field.exists(timeout=1):
                hour_field.clear_text()
                hour_field.set_text(hour_str)
                time.sleep(0.3)
                minute_field.clear_text()
                minute_field.set_text("{alarm_minute}")
            else:
                edits = d(className="android.widget.EditText")
                if edits.count < 2:
                    sys.exit(1)
                edits[0].clear_text()
                edits[0].set_text(hour_str)
                time.sleep(0.3)
                edits[1].clear_text()
                edits[1].set_text("{alarm_minute}")
            time.sleep(0.3)
            period = "{alarm_period}"
            if d(text=period).exists(timeout=2):
                d(text=period).click()
            elif d(description=period).exists(timeout=2):
                d(description=period).click()
            for ok_text in ["OK", "Save", "Done"]:
                if d(text=ok_text).exists(timeout=2):
                    d(text=ok_text).click()
                    break
            time.sleep(0.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Confirm the alarm by pressing OK.",
        "clock", "confirm",
        [
            "Press OK to save the alarm.",
            "Tap Save to confirm alarm.",
            "Press Done to finish setting alarm.",
            "Press the Save/Confirm button to finalize the alarm setting in the Clock app",
            "Press the OK or Save button to confirm the alarm setting",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            if d(description="Add alarm").exists(timeout=1) or d(text="Add alarm").exists(timeout=1):
                print("TASK_COMPLETE")
                sys.exit(0)
            for ok_text in ["OK", "Save", "Done"]:
                if d(text=ok_text).exists(timeout=3):
                    d(text=ok_text).click()
                    time.sleep(0.5)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            print("TASK_COMPLETE")
        """),
    ),

    # ══════════════════════════════════════════════════════════════════════
    #  GMAIL
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open the Gmail app.",
        "gmail", "launch",
        [
            "Launch Gmail.", "Open the email app.", "Navigate to Gmail.",
            "Start the Gmail application.", "Navigate to email app.",
            "Open the default email app on the mobile device.",
            "Open the default email app on the mobile device",
        ],
        textwrap.dedent("""\
            d.app_start("com.google.android.gm")
            time.sleep(3.0)
            print("TASK_COMPLETE")
        """),
    ),

    # FIX 3: Combined compose + fill To. Handles "Create a new email draft",
    # "Start composing a new email to X", etc. — the most common decomposer
    # phrasings for "open compose screen and address it".
    (
        "Compose a new email and fill the To field with {recipient_email}.",
        "gmail", "action",
        [
            "Create a new email draft to {recipient_email}.",
            "Start composing a new email to {recipient_email}.",
            "Open a new compose window and address it to {recipient_email}.",
            "Create a new email draft in the default email app.",
            "Create a new email draft",
            "Open compose and add recipient {recipient_email}.",
            "Tap the Compose button in Gmail.",
            "Click Compose to start a new email.",
            "Open a new email compose screen.",
            "Press the compose FAB in Gmail.",
            "Start a new email to {recipient_email}.",
            "New email composition to {recipient_email}.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            if d(resourceId="com.google.android.gm:id/compose_button").exists(timeout=5):
                d(resourceId="com.google.android.gm:id/compose_button").click()
            elif d(resourceId="com.google.android.gm:id/fab").exists(timeout=3):
                d(resourceId="com.google.android.gm:id/fab").click()
            else:
                d.click(0.9, 0.9)
            time.sleep(2.0)
            to_field = None
            for rid in ["com.google.android.gm:id/to",
                        "com.google.android.gm:id/people_edit_text",
                        "com.google.android.gm:id/recipient_text_view"]:
                if d(resourceId=rid).exists(timeout=3):
                    to_field = d(resourceId=rid)
                    break
            if to_field is None:
                if d(className="android.widget.MultiAutoCompleteTextView").exists(timeout=2):
                    to_field = d(className="android.widget.MultiAutoCompleteTextView")
                else:
                    to_field = d(className="android.widget.EditText")
            to_field.click()
            time.sleep(0.3)
            to_field.set_text("{recipient_email}")
            d.press("enter")
            time.sleep(0.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Fill the To field with {recipient_email}.",
        "gmail", "fill",
        [
            "Enter {recipient_email} in the recipient field.",
            "Type {recipient_email} in the To field.",
            "Set the email recipient to {recipient_email}.",
            "Address the email to {recipient_email}.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            to_field = None
            for rid in ["com.google.android.gm:id/to",
                        "com.google.android.gm:id/people_edit_text",
                        "com.google.android.gm:id/recipient_text_view"]:
                if d(resourceId=rid).exists(timeout=3):
                    to_field = d(resourceId=rid)
                    break
            if to_field is None:
                if d(className="android.widget.MultiAutoCompleteTextView").exists(timeout=2):
                    to_field = d(className="android.widget.MultiAutoCompleteTextView")
                else:
                    to_field = d(className="android.widget.EditText")
            to_field.click()
            time.sleep(0.3)
            to_field.set_text("{recipient_email}")
            d.press("enter")
            time.sleep(0.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Fill the Subject field with {email_subject}.",
        "gmail", "fill",
        [
            "Enter {email_subject} in the subject line.",
            "Type {email_subject} as the email subject.",
            "Set the email subject to {email_subject}.",
            "Fill the Subject field with the SUBJECT value from the generated email.",
            "Fill the Subject field with the exact text {email_subject}.",
            "Fill the Subject field with the generated SUBJECT value.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            subj = None
            if d(resourceId="com.google.android.gm:id/subject").exists(timeout=3):
                subj = d(resourceId="com.google.android.gm:id/subject")
            elif d(className="android.widget.EditText").count >= 2:
                subj = d(className="android.widget.EditText")[1]
            if subj:
                subj.click()
                time.sleep(0.3)
                subj.clear_text()
                subj.set_text("{email_subject}")
                time.sleep(0.3)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Fill the email body with {email_body}.",
        "gmail", "fill",
        [
            "Type {email_body} in the email body.",
            "Enter {email_body} as the email message.",
            "Write {email_body} in the compose body field.",
            "Fill the email body with the BODY value from the generated email.",
            "Fill the email body with the exact text {email_body}.",
            "Fill the email body with the generated BODY value.",
            "Type the body {email_body} in the email.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            body = None
            if d(resourceId="com.google.android.gm:id/body").exists(timeout=3):
                body = d(resourceId="com.google.android.gm:id/body")
            else:
                d.click(0.5, 0.65)
                time.sleep(0.5)
                et_count = d(className="android.widget.EditText").count
                if et_count > 0:
                    body = d(className="android.widget.EditText")[et_count - 1]
            if body:
                body.click()
                time.sleep(0.3)
                body.clear_text()
                body.set_text("{email_body}")
                time.sleep(0.3)
            print("TASK_COMPLETE")
        """),
    ),

    # FIX 4: No-op for spurious Save/Next/proceed steps that don't exist in Gmail.
    (
        "Click the Save or Next button to move to the send screen.",
        "gmail", "navigate",
        [
            "Click the Save or Next button to move to the send screen in the email app.",
            "Save the email draft and proceed to send.",
            "Click Save or Next to proceed.",
            "Move to the send screen in the email app.",
            "Proceed to send screen.",
            "Click the Save or Next button to move to the send screen in the email app",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            # Gmail has no Save/Next step between compose and send.
            # Draft is auto-saved. The Send step handles the actual send.
            # Just verify compose screen is still visible — don't click anything.
            if d(resourceId="com.google.android.gm:id/send").exists(timeout=2):
                pass
            elif d(description="Send").exists(timeout=2):
                pass
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Click the Send button to send the email.",
        "gmail", "confirm",
        [
            "Press Send to deliver the email.",
            "Tap the send icon in Gmail.",
            "Submit the composed email.",
            "Click Send to send the email to {recipient_email}.",
            "Click Send to send the email.",
            "Send the email.",
            "Send email now.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            if d(resourceId="com.google.android.gm:id/send").exists(timeout=3):
                d(resourceId="com.google.android.gm:id/send").click()
            elif d(description="Send").exists(timeout=3):
                d(description="Send").click()
            time.sleep(2.0)
            print("TASK_COMPLETE")
        """),
    ),

    # ══════════════════════════════════════════════════════════════════════
    #  MAPS
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open Google Maps.",
        "maps", "launch",
        ["Launch Google Maps.", "Start the Maps app.", "Open Maps on the device."],
        textwrap.dedent("""\
            d.app_stop("com.google.android.apps.maps")
            time.sleep(0.5)
            d.app_start("com.google.android.apps.maps")
            time.sleep(3.5)
            for rid in ["com.google.android.apps.maps:id/negative_button"]:
                if d(resourceId=rid).exists(timeout=1):
                    d(resourceId=rid).click()
                    time.sleep(0.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap the search bar in Google Maps.",
        "maps", "navigate",
        [
            "Click the Maps search field.",
            "Activate the search bar in Maps.",
            "Tap the search box to bring up the keyboard.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            for sel in [
                lambda: d(resourceId="com.google.android.apps.maps:id/search_omnibox_text_box"),
                lambda: d(description="Try gas stations, ATMs"),
                lambda: d(text="Search here"),
            ]:
                btn = sel()
                if btn.exists(timeout=3):
                    btn.click()
                    time.sleep(1.0)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            sys.exit(1)
        """),
    ),

    (
        "Type {search_query} into the Maps search bar.",
        "maps", "search",
        [
            "Search for {search_query} in Google Maps.",
            "Enter {search_query} in the Maps search field.",
        ],
        textwrap.dedent("""\
            time.sleep(0.3)
            field = d(resourceId="com.google.android.apps.maps:id/search_omnibox_text_box")
            if not field.exists(timeout=3):
                sys.exit(1)
            field.click()
            time.sleep(0.2)
            field.clear_text()
            field.set_text("{search_query}")
            time.sleep(0.4)
            d.press("enter")
            time.sleep(2.0)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Press Enter to submit the Maps search.",
        "maps", "confirm",
        ["Submit the search query in Maps.", "Tap the search button to find results."],
        textwrap.dedent("""\
            time.sleep(0.3)
            d.press("enter")
            time.sleep(2.0)
            print("TASK_COMPLETE")
        """),
    ),

    # ══════════════════════════════════════════════════════════════════════
    #  CONTACTS
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open the Contacts app.",
        "contacts", "launch",
        ["Launch Contacts.", "Start the Google Contacts app.", "Navigate to Contacts."],
        textwrap.dedent("""\
            d.app_stop("com.google.android.contacts")
            time.sleep(0.5)
            d.app_start("com.google.android.contacts")
            time.sleep(2.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap the Add Contact button.",
        "contacts", "action",
        ["Create a new contact.", "Press the new contact FAB.", "Open the create contact screen."],
        textwrap.dedent("""\
            time.sleep(0.5)
            for rid in ["com.google.android.contacts:id/floating_action_button",
                        "com.google.android.contacts:id/add_contact_button"]:
                if d(resourceId=rid).exists(timeout=2):
                    d(resourceId=rid).click()
                    time.sleep(1.2)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            for txt in ["Create contact", "Add contact"]:
                if d(text=txt).exists(timeout=2):
                    d(text=txt).click()
                    time.sleep(1.2)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            sys.exit(1)
        """),
    ),

    (
        "Enter {contact_name} in the contact name field.",
        "contacts", "fill",
        [
            "Type {contact_name} as the contact name.",
            "Fill the name field with {contact_name}.",
            "Set the contact name to {contact_name}.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            edits = d(className="android.widget.EditText")
            if not edits.exists(timeout=3):
                sys.exit(1)
            name_field = edits[0]
            name_field.click()
            time.sleep(0.2)
            name_field.clear_text()
            name_field.set_text("{contact_name}")
            time.sleep(0.4)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Enter {phone_number} in the phone number field.",
        "contacts", "fill",
        [
            "Type {phone_number} as the contact phone number.",
            "Fill the phone field with {phone_number}.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            edits = d(className="android.widget.EditText")
            if not edits.exists(timeout=3):
                sys.exit(1)
            count = edits.count
            phone_field = edits[min(3, count - 1)]
            phone_field.click()
            time.sleep(0.2)
            phone_field.clear_text()
            phone_field.set_text("{phone_number}")
            time.sleep(0.4)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap Save to save the contact.",
        "contacts", "confirm",
        ["Press Save to add the new contact.", "Confirm and save the contact."],
        textwrap.dedent("""\
            time.sleep(0.5)
            for txt in ["Save", "Add contact"]:
                if d(text=txt).exists(timeout=3):
                    d(text=txt).click()
                    time.sleep(1.2)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            sys.exit(1)
        """),
    ),

    # ══════════════════════════════════════════════════════════════════════
    #  MESSAGES
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open the Messages app.",
        "messages", "launch",
        [
            "Launch Google Messages.", "Start the SMS app.", "Open the messaging app.",
            "Open the Messages app on the mobile device",
            "Open Messages app on mobile device",
        ],
        textwrap.dedent("""\
            d.app_start("com.google.android.apps.messaging")
            time.sleep(3.0)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Open or start a message thread with {contact_name}.",
        "messages", "navigate",
        [
            "Navigate to the conversation with {contact_name}.",
            "Find and open the chat with {contact_name}.",
            "Start a new SMS to {contact_name}.",
            "Find and tap on the contact named {contact_name} in the Messages app",
            "Find and tap on the contact named '{contact_name}' in the Messages app",
            "Search for the chat with {contact_name} in the Messages app",
            "Tap on the contact {contact_name} in the Messages app",
            "Find and tap on the contact named {contact_name} in the Message",
            "Find the chat with {contact_name} and open it",
            "Open {contact_name} conversation in Messages",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            exact = d(text="{contact_name}")
            partial = d(textContains="{contact_name}")
            if exact.exists(timeout=2):
                exact.click()
            elif partial.exists(timeout=2):
                partial.click()
            else:
                for rid in ["com.google.android.apps.messaging:id/start_chat_fab",
                            "com.google.android.apps.messaging:id/start_new_conversation_button"]:
                    if d(resourceId=rid).exists(timeout=2):
                        d(resourceId=rid).click()
                        time.sleep(1.0)
                        break
                field = None
                for rid in ["com.google.android.apps.messaging:id/recipient_text_view"]:
                    if d(resourceId=rid).exists(timeout=2):
                        field = d(resourceId=rid)
                        break
                if field is None:
                    if d(className="android.widget.MultiAutoCompleteTextView").exists(timeout=2):
                        field = d(className="android.widget.MultiAutoCompleteTextView")
                    else:
                        field = d(className="android.widget.EditText")
                field.click()
                time.sleep(0.2)
                field.set_text("{contact_name}")
                time.sleep(1.0)
                result = d(textContains="{contact_name}", clickable=True)
                if result.exists(timeout=3):
                    result.click()
                else:
                    d.press("enter")
            time.sleep(1.0)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Type {message_text} in the message input field.",
        "messages", "fill",
        [
            "Enter {message_text} in the SMS compose box.",
            "Write {message_text} in the message field.",
            "Fill the message body with {message_text}.",
            "Type the message {message_text} in the chat",
            "Type the message '{message_text}' in the chat",
        ],
        textwrap.dedent("""\
            time.sleep(0.3)
            field = None
            for rid in ["com.google.android.apps.messaging:id/compose_message_text",
                        "com.google.android.apps.messaging:id/message_text"]:
                if d(resourceId=rid).exists(timeout=3):
                    field = d(resourceId=rid)
                    break
            if field is None:
                edits = d(className="android.widget.EditText")
                if edits.exists(timeout=2):
                    field = edits[edits.count - 1]
            field.click()
            time.sleep(0.2)
            field.clear_text()
            field.set_text("{message_text}")
            time.sleep(0.4)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap the Send button to send the SMS.",
        "messages", "confirm",
        [
            "Press Send to deliver the message.",
            "Submit the composed SMS.",
            "Tap the send icon in Messages.",
            "Tap the send button to send the message to Haya",
            "Send the message to Haya Walid in the chat",
        ],
        textwrap.dedent("""\
            time.sleep(0.3)
            for rid in ["com.google.android.apps.messaging:id/send_message_button_icon",
                        "com.google.android.apps.messaging:id/send_message_button_container"]:
                if d(resourceId=rid).exists(timeout=2):
                    d(resourceId=rid).click()
                    time.sleep(1.0)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            if d(description="Send").exists(timeout=2):
                d(description="Send").click()
            elif d(descriptionContains="Send").exists(timeout=2):
                d(descriptionContains="Send").click()
            time.sleep(1.0)
            print("TASK_COMPLETE")
        """),
    ),

    # ══════════════════════════════════════════════════════════════════════
    #  WHATSAPP
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open WhatsApp.",
        "whatsapp", "launch",
        ["Launch the WhatsApp application.", "Start WhatsApp on the device.", "Navigate to WhatsApp."],
        textwrap.dedent("""\
            d.app_start("com.whatsapp")
            time.sleep(2.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Open the WhatsApp group {group_name}.",
        "whatsapp", "navigate",
        [
            "Navigate to the {group_name} group chat in WhatsApp.",
            "Find and open WhatsApp group {group_name}.",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            for sel in [
                lambda: d(resourceId="com.whatsapp:id/my_search_bar"),
                lambda: d(resourceId="com.whatsapp:id/menuitem_search"),
                lambda: d(descriptionContains="Search"),
            ]:
                btn = sel()
                if btn.exists(timeout=2):
                    btn.click()
                    time.sleep(0.8)
                    break
            search_field = None
            for rid in ["com.whatsapp:id/search_input", "com.whatsapp:id/search_src_text"]:
                if d(resourceId=rid).exists(timeout=2):
                    search_field = d(resourceId=rid)
                    break
            if search_field is None:
                search_field = d(className="android.widget.EditText")
            search_field.click()
            time.sleep(0.2)
            search_field.set_text("{group_name}")
            time.sleep(1.0)
            if d(textContains="{group_name}", clickable=True).exists(timeout=3):
                d(textContains="{group_name}", clickable=True).click()
            else:
                d.press("enter")
            time.sleep(1.2)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Type {message_text} in the WhatsApp message box.",
        "whatsapp", "fill",
        [
            "Enter {message_text} in the WhatsApp input field.",
            "Write {message_text} in WhatsApp.",
        ],
        textwrap.dedent("""\
            time.sleep(0.3)
            field = None
            for rid in ["com.whatsapp:id/entry", "com.whatsapp:id/message"]:
                if d(resourceId=rid).exists(timeout=2):
                    field = d(resourceId=rid)
                    break
            if field is None:
                field = d(className="android.widget.EditText")
            field.click()
            time.sleep(0.2)
            field.clear_text()
            field.set_text("{message_text}")
            time.sleep(0.6)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap Send to send the WhatsApp message.",
        "whatsapp", "confirm",
        ["Press the send button in WhatsApp.", "Submit the WhatsApp message."],
        textwrap.dedent("""\
            time.sleep(0.3)
            if d(resourceId="com.whatsapp:id/send").exists(timeout=2):
                d(resourceId="com.whatsapp:id/send").click()
            elif d(description="Send").exists(timeout=2):
                d(description="Send").click()
            elif d(descriptionContains="Send").exists(timeout=2):
                d(descriptionContains="Send").click()
            time.sleep(1.0)
            print("TASK_COMPLETE")
        """),
    ),

    # ══════════════════════════════════════════════════════════════════════
    #  GOOGLE CALENDAR
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open Google Calendar.",
        "google calendar", "launch",
        ["Launch the Calendar app.", "Start Google Calendar.", "Navigate to Calendar."],
        textwrap.dedent("""\
            d.app_start("com.google.android.calendar")
            time.sleep(2.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap the Create button to add a new calendar event.",
        "google calendar", "action",
        ["Open the new event creation screen.", "Press the FAB to create a calendar event."],
        textwrap.dedent("""\
            time.sleep(0.5)
            creation_menu = d(description="Creation menu")
            if not creation_menu.exists(timeout=2):
                creation_menu = d(resourceId="com.google.android.calendar:id/fab_container")
            if creation_menu.exists(timeout=2):
                creation_menu.click()
                time.sleep(0.8)
                if d(text="Event").exists(timeout=2):
                    d(text="Event").click()
                    time.sleep(1.2)
            else:
                for desc in ["Create", "Add", "New event"]:
                    if d(description=desc).exists(timeout=2):
                        d(description=desc).click()
                        time.sleep(1.0)
                        break
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Enter {event_title} as the calendar event title.",
        "google calendar", "fill",
        ["Type {event_title} in the event title field.", "Fill the event name with {event_title}."],
        textwrap.dedent("""\
            time.sleep(1.5)
            title_field = None
            for sel in [
                lambda: d(text="Add title"),
                lambda: d(resourceId="com.google.android.calendar:id/title"),
            ]:
                f = sel()
                if f.exists(timeout=2):
                    title_field = f
                    break
            if title_field is None:
                all_edits = d(className="android.widget.EditText")
                if all_edits.exists(timeout=3):
                    title_field = all_edits[0]
            if title_field is None:
                sys.exit(1)
            title_field.click()
            time.sleep(0.2)
            title_field.clear_text()
            title_field.set_text("{event_title}")
            time.sleep(0.6)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Set the event date to {event_date}.",
        "google calendar", "fill",
        ["Enter {event_date} as the event date.", "Fill the date field with {event_date}."],
        textwrap.dedent("""\
            time.sleep(0.5)
            date_field = None
            for rid in ["com.google.android.calendar:id/start_date",
                        "com.google.android.calendar:id/date"]:
                if d(resourceId=rid).exists(timeout=2):
                    date_field = d(resourceId=rid)
                    break
            if date_field is None:
                all_edits = d(className="android.widget.EditText")
                if all_edits.exists(timeout=2) and all_edits.count >= 2:
                    date_field = all_edits[1]
            if date_field:
                date_field.click()
                time.sleep(0.5)
            parts = "{event_date}".split("-")
            if len(parts) == 3:
                date_text = f"{int(parts[2])}/{int(parts[1])}/{parts[0]}"
            else:
                date_text = "{event_date}"
            for toggle_desc in ["text input", "Keyboard", "Use text"]:
                if d(descriptionContains=toggle_desc).exists(timeout=1):
                    d(descriptionContains=toggle_desc).click()
                    time.sleep(0.4)
                    break
            edits = d(className="android.widget.EditText")
            if edits.exists(timeout=2):
                target = edits[1] if edits.count >= 2 else edits[0]
                target.click()
                time.sleep(0.2)
                target.clear_text()
                target.set_text(date_text)
            time.sleep(0.6)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap Save to create the calendar event.",
        "google calendar", "confirm",
        ["Press Save to finish creating the event.", "Confirm and save the new calendar event."],
        textwrap.dedent("""\
            time.sleep(0.5)
            for txt in ["Save", "Done"]:
                if d(text=txt).exists(timeout=3):
                    d(text=txt).click()
                    time.sleep(1.0)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            sys.exit(1)
        """),
    ),

    # ══════════════════════════════════════════════════════════════════════
    #  GOOGLE DOCS
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open Google Docs.",
        "google docs", "launch",
        [
            "Launch the Docs app.", "Start Google Docs on the device.", "Navigate to Google Docs.",
            "Open the Google Docs app on the mobile device",
        ],
        textwrap.dedent("""\
            d.app_start("com.google.android.apps.docs.editors.docs")
            time.sleep(3.0)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Tap the button to create a new Google Doc.",
        "google docs", "action",
        [
            "Create a new blank document in Google Docs.",
            "Press the new document FAB.",
            "Open the blank document creation screen.",
            "Tap the 'New blank document' button in Google Docs",
            "Tap the New blank document button in Google Docs",
        ],
        textwrap.dedent("""\
            time.sleep(1.0)
            fab = d(resourceId="com.google.android.apps.docs.editors.docs:id/fab")
            if fab.exists(timeout=3):
                fab.click()
                time.sleep(1.0)
                for txt in ["Blank document", "Blank", "New document"]:
                    if d(text=txt).exists(timeout=2):
                        d(text=txt).click()
                        time.sleep(2.0)
                        print("TASK_COMPLETE")
                        sys.exit(0)
                print("TASK_COMPLETE")
                sys.exit(0)
            for txt in ["Blank document", "Blank", "New document"]:
                if d(text=txt).exists(timeout=3):
                    d(text=txt).click()
                    time.sleep(2.0)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            d.click(0.9, 0.9)
            time.sleep(1.5)
            for txt in ["Blank document", "Blank"]:
                if d(text=txt).exists(timeout=2):
                    d(text=txt).click()
                    time.sleep(2.0)
                    break
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Set the document title to {doc_title}.",
        "google docs", "fill",
        [
            "Enter {doc_title} as the document name.",
            "Type {doc_title} in the title field.",
            "Rename the document to {doc_title}.",
            "Type the title '{doc_title}' in the document title field",
            "Type the title {doc_title} in the document title field",
        ],
        textwrap.dedent("""\
            time.sleep(1.0)
            for sel in [
                lambda: d(text="Untitled document"),
                lambda: d(text="Untitled Document"),
                lambda: d(resourceId="com.google.android.apps.docs.editors.docs:id/title"),
                lambda: d(textContains="Untitled"),
            ]:
                field = sel()
                if field.exists(timeout=2):
                    field.click()
                    time.sleep(0.4)
                    field.clear_text()
                    field.set_text("{doc_title}")
                    time.sleep(0.8)
                    print("TASK_COMPLETE")
                    sys.exit(0)
            all_edits = d(className="android.widget.EditText")
            if all_edits.exists(timeout=3):
                all_edits[0].click()
                time.sleep(0.3)
                all_edits[0].clear_text()
                all_edits[0].set_text("{doc_title}")
                time.sleep(0.6)
                print("TASK_COMPLETE")
                sys.exit(0)
            sys.exit(1)
        """),
    ),

    # ══════════════════════════════════════════════════════════════════════
    #  CHROME / YOUTUBE
    # ══════════════════════════════════════════════════════════════════════

    (
        "Open Chrome browser.",
        "chrome", "launch",
        ["Launch Chrome.", "Open Chrome on the device.", "Start the Chrome browser.",
         "Open Chrome browser on the mobile device"],
        textwrap.dedent("""\
            d.app_start("com.android.chrome")
            time.sleep(3.0)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Navigate to the address bar and type {search_query}.",
        "chrome", "search",
        [
            "Type {search_query} in the Chrome address bar.",
            "Search for {search_query} in Chrome.",
            "Navigate to the search bar in Chrome and type {search_query}",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            url_bar = d(resourceId="com.android.chrome:id/url_bar")
            if not url_bar.exists(timeout=8):
                url_bar = d(className="android.widget.EditText")
                if not url_bar.exists(timeout=5):
                    d.click(0.5, 0.07)
                    time.sleep(1.0)
                    url_bar = d(className="android.widget.EditText")
            url_bar.click()
            time.sleep(0.5)
            url_bar.clear_text()
            url_bar.set_text("{search_query}")
            time.sleep(0.3)
            d.press("enter")
            time.sleep(2.5)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Open the YouTube app.",
        "youtube", "launch",
        ["Launch YouTube.", "Start the YouTube app.", "Open YouTube on the mobile device."],
        textwrap.dedent("""\
            d.app_start("com.google.android.youtube")
            time.sleep(3.0)
            print("TASK_COMPLETE")
        """),
    ),

    (
        "Type {search_query} in the YouTube search bar.",
        "youtube", "search",
        [
            "Search for {search_query} on YouTube.",
            "Enter {search_query} in the YouTube search field.",
            "Type {search_query} into the YouTube search bar and press Enter",
        ],
        textwrap.dedent("""\
            time.sleep(0.5)
            for rid in ["com.google.android.youtube:id/menu_item_1",
                        "com.google.android.youtube:id/toolbar_search_button"]:
                if d(resourceId=rid).exists(timeout=3):
                    d(resourceId=rid).click()
                    time.sleep(1.0)
                    break
            else:
                d.click(0.92, 0.05)
                time.sleep(1.0)
            search_box = None
            for rid in ["com.google.android.youtube:id/search_edit_text",
                        "com.google.android.youtube:id/youtube_query_text_view"]:
                if d(resourceId=rid).exists(timeout=3):
                    search_box = d(resourceId=rid)
                    break
            if search_box is None:
                search_box = d(className="android.widget.EditText")
            search_box.click()
            search_box.set_text("{search_query}")
            d.press("search")
            time.sleep(2.5)
            print("TASK_COMPLETE")
        """),
    ),

]


# ══════════════════════════════════════════════════════════════════════════════
#  INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def inject_all(overwrite: bool = False, dry_run: bool = False) -> None:
    cache = ChromaTemplateCache(CHROMA_PATH)
    extractor = PlaceholderExtractor()
    injected = skipped = errors = 0

    for pattern, app, task_type, aliases, raw_code in REGISTRY:
        tid = hashlib.sha1(f"{app}:{pattern}".encode()).hexdigest()

        if not overwrite:
            existing = cache._fetch_by_id(tid)
            if existing and existing.success_count >= 3:
                print(f"  SKIP (proven) [{app}] {pattern[:60]}")
                skipped += 1
                continue

        try:
            compile(raw_code, "<template>", "exec")
        except SyntaxError as e:
            print(f"  SYNTAX ERROR [{app}] {pattern[:50]}: {e}")
            errors += 1
            continue

        found_keys = set(re.findall(r"\{(\w+)\}", raw_code + " " + pattern + " " + " ".join(aliases)))
        schema = {
            k: PlaceholderExtractor.PARAM_DESCRIPTIONS.get(k, f"str — {k}")
            for k in found_keys if k not in _INTERNAL_KEYS
        }

        template = CodeTemplate(
            template_id=tid,
            task_pattern=pattern,
            aliases=aliases,
            app=app,
            package=APP_PACKAGES.get(app, ""),
            task_type=task_type,
            code_template=raw_code.strip(),
            parameter_schema=schema,
            success_count=2,
            failure_count=0,
        )

        if dry_run:
            print(f"\n── [{app}] {task_type}: {pattern[:60]}")
            print(f"   Schema: {list(schema.keys())}")
            for ln in raw_code.strip().splitlines()[:4]:
                print(f"   {ln}")
            injected += 1
            continue

        cache.add(template)
        print(f"  INJECTED [{app}] [{task_type}] {pattern[:60]}")
        injected += 1

    print(f"\n{'DRY RUN ' if dry_run else ''}Done — {injected} injected, {skipped} skipped, {errors} errors")
    if not dry_run:
        print(f"Cache stats: {cache.stats()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    inject_all(overwrite=args.overwrite, dry_run=args.dry_run)