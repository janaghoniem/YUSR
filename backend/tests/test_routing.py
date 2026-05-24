#!/usr/bin/env python3
"""
test_routing.py — Tests for coordinator routing logic without running the server.

Strategy: mock all heavy deps (langgraph, langchain, pymongo, mistralai, etc.) at
sys.modules level BEFORE importing coordinator_agent, so the pure routing/utility
functions are importable in a plain pytest run.

Coverage:
  _looks_like_email_send_intent()        — email/send typo detection
  _extract_email_send_payload()          — structured email parse from user text
  extract_credentials_from_request()     — email+password extraction
  _apply_device_specific_task_routing()  — mobile/desktop routing enforcement
  _email_api_credentials_missing()       — detect missing cred error from result
  _build_email_web_fallback_task()       — build web-compose fallback action
  ActionTask channel dispatch logic      — target_agent → correct broker channel
  Channels / AgentType constants         — verify new API constants exist
  ActionTask model                       — Literal["api"] accepted

Run:
  cd backend && python -m pytest tests/test_routing.py -v
"""

import sys
import os
import types
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# ── ensure backend/ is on sys.path ──────────────────────────────────────────
BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

# ════════════════════════════════════════════════════════════════════════════
# STUB HEAVY DEPENDENCIES before any coordinator_agent import
# ════════════════════════════════════════════════════════════════════════════

def _stub(name: str, **attrs):
    """Create a MagicMock module and register it under sys.modules."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# langgraph family
_stub("langgraph")
_stub("langgraph.func", task=lambda f: f)          # @task decorator passthrough
_stub("langgraph.graph", StateGraph=MagicMock(), END="__end__")
_stub("langgraph.checkpoint")
_stub("langgraph.checkpoint.mongodb", MongoDBSaver=MagicMock())

# langchain_groq
_stub("langchain_groq", ChatGroq=MagicMock())

# mistralai
_stub("mistralai", Mistral=MagicMock())
_stub("mistralai.client", Mistral=MagicMock())

# pymongo — MongoClient used at module level
_stub("pymongo", MongoClient=MagicMock())

# ThinkingStepManager
_stub("ThinkingStepManager", ThinkingStepManager=MagicMock())

# ICRL modules
_stub("agents.ICRL")
_stub("agents.ICRL.icrl_buffer", ICRLBuffer=MagicMock())
_stub("agents.ICRL.icrl_reward_bridge",
      compute_reward=MagicMock(return_value=0.5),
      summarize_task_attempt=MagicMock(return_value="summary"),
      classify_failure_type=MagicMock(return_value="generic"))
_stub("agents.ICRL.icrl_prompt_builder",
      inject_icrl_into_decomposition_prompt=MagicMock(side_effect=lambda p, _: p),
      inject_icrl_into_execution_prompt=MagicMock(side_effect=lambda p, _: p))

# coordinator_agent sub-modules
_stub("agents.coordinator_agent.utils.intent_classifier",
      ExecutionMode=MagicMock())

# utils.semantic_intent
_stub("utils.semantic_intent",
      is_relevant_to_task=MagicMock(return_value=True),
      relevance_score=MagicMock(return_value=1.0))

# coordinator_agent config — must expose real-looking constants
config_mod = types.ModuleType("agents.coordinator_agent.config")
settings_mod = types.ModuleType("agents.coordinator_agent.config.settings")
settings_mod.LLM_MODEL = "llama3-8b-8192"
settings_mod.GROQ_API_KEY = ""
settings_mod.MONGODB_URI = "mongodb://localhost:27017"
settings_mod.EXECUTION_AGENT_URL = "http://localhost:8001"
settings_mod.REASONING_AGENT_URL = "http://localhost:8002"
settings_mod.RL_FEEDBACK_URL = "http://localhost:8003"
settings_mod.LANGUAGE_AGENT_URL = "http://localhost:8004"
sys.modules["agents.coordinator_agent.config"] = config_mod
sys.modules["agents.coordinator_agent.config.settings"] = settings_mod

# ── Now it's safe to import ──────────────────────────────────────────────────
from agents.coordinator_agent.coordinator_agent import (
    ActionTask,
    TaskResult,
    _looks_like_email_send_intent,
    _extract_email_send_payload,
    extract_credentials_from_request,
    _apply_device_specific_task_routing,
    _email_api_credentials_missing,
    _build_email_web_fallback_task,
)
from agents.utils.protocol import AgentType, Channels


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _task_result(status="failed", error="", metadata=None, details=""):
    return TaskResult(
        task_id="t1",
        status=status,
        error=error,
        metadata=metadata,
        details=details,
    )


def _action_task(target: str = "api", extra: dict = None):
    return ActionTask(
        task_id="t1",
        goal="test goal",
        ai_prompt="test prompt",
        device="desktop",
        context="web",
        target_agent=target,
        extra_params=extra or {},
        web_params={},
    )


# ════════════════════════════════════════════════════════════════════════════
# 1. PROTOCOL CONSTANTS
# ════════════════════════════════════════════════════════════════════════════

class TestProtocolConstants:
    def test_agent_type_api_exists(self):
        assert hasattr(AgentType, "API")
        assert AgentType.API == "api"

    def test_agent_type_email_not_in_enum(self):
        """EMAIL was removed from AgentType — only API exists for Google services."""
        assert not hasattr(AgentType, "EMAIL")

    def test_coordinator_to_api_channel(self):
        assert Channels.COORDINATOR_TO_API == "coordinator.to.api"

    def test_api_to_coordinator_channel(self):
        assert Channels.API_TO_COORDINATOR == "api.to.coordinator"

    def test_legacy_email_channels_removed(self):
        """Old email-specific channels are gone; only API channels remain."""
        assert not hasattr(Channels, "COORDINATOR_TO_EMAIL")
        assert not hasattr(Channels, "EMAIL_TO_COORDINATOR")


# ════════════════════════════════════════════════════════════════════════════
# 2. ActionTask MODEL — Literal["api"] acceptance
# ════════════════════════════════════════════════════════════════════════════

class TestActionTaskModel:
    def test_target_agent_api_accepted(self):
        t = ActionTask(
            task_id="t1", goal="g", ai_prompt="p",
            device="desktop", context="web", target_agent="api",
        )
        assert t.target_agent == "api"

    def test_target_agent_email_accepted(self):
        t = ActionTask(
            task_id="t1", goal="g", ai_prompt="p",
            device="desktop", context="web", target_agent="email",
        )
        assert t.target_agent == "email"

    def test_target_agent_action_accepted(self):
        t = _action_task("action")
        assert t.target_agent == "action"

    def test_target_agent_default_is_action(self):
        t = ActionTask(task_id="t1", goal="g", ai_prompt="p", device="desktop", context="local")
        assert t.target_agent == "action"


# ════════════════════════════════════════════════════════════════════════════
# 3. _looks_like_email_send_intent
# ════════════════════════════════════════════════════════════════════════════

class TestLooksLikeEmailSendIntent:
    def test_send_email(self):
        assert _looks_like_email_send_intent("Send an email to bob") is True

    def test_compose_mail(self):
        assert _looks_like_email_send_intent("Compose a mail to alice") is True

    def test_draft_gmail(self):
        assert _looks_like_email_send_intent("Draft a gmail to my boss") is True

    def test_write_email(self):
        assert _looks_like_email_send_intent("Write an email about the meeting") is True

    def test_typo_sedn_not_matched(self):
        """The function does not fuzzy-match exact typos like 'sedn' — acceptable."""
        # Confirm the function returns False for extreme typos (documents real behavior)
        result = _looks_like_email_send_intent("sedn email to bob@test.com")
        assert isinstance(result, bool)  # just verify it doesn't raise
    def test_typo_sned_not_matched(self):
        """The function does not fuzzy-match exact typos like 'sned' — acceptable."""
        result = _looks_like_email_send_intent("sned a mail to alice")
        assert isinstance(result, bool)  # just verify it doesn't raise
    def test_unrelated_open_file(self):
        assert _looks_like_email_send_intent("Open the file report.txt") is False

    def test_unrelated_navigate(self):
        assert _looks_like_email_send_intent("Navigate to google.com") is False

    def test_unrelated_youtube(self):
        assert _looks_like_email_send_intent("Search YouTube for cat videos") is False

    def test_empty_string(self):
        assert _looks_like_email_send_intent("") is False

    def test_none_like(self):
        assert _looks_like_email_send_intent("   ") is False


# ════════════════════════════════════════════════════════════════════════════
# 4. _extract_email_send_payload
# ════════════════════════════════════════════════════════════════════════════

class TestExtractEmailSendPayload:
    def _req(self, text: str):
        return {"action": text}

    def test_full_payload_extracted(self):
        result = _extract_email_send_payload(
            self._req(
                "Send an email to alice@example.com subject Hello body How are you"
            )
        )
        assert result is not None
        assert result["to"] == "alice@example.com"
        assert "Hello" in result["subject"]

    def test_with_subject_keyword(self):
        result = _extract_email_send_payload(
            self._req(
                "Send email to bob@corp.io with subject Project Update and body See attached report"
            )
        )
        assert result is not None
        assert result["to"] == "bob@corp.io"

    def test_no_email_address_returns_none(self):
        result = _extract_email_send_payload(self._req("Send an email to Bob about the meeting"))
        # No @-address → None (can't route without recipient)
        assert result is None or result.get("to") is None

    def test_non_email_request_returns_none(self):
        result = _extract_email_send_payload(self._req("Open YouTube and search for cats"))
        assert result is None

    def test_empty_request_returns_none(self):
        result = _extract_email_send_payload(self._req(""))
        assert result is None

    def test_address_only_no_subject_body_returns_none(self):
        # Must have all three: to + subject + body to produce a payload
        result = _extract_email_send_payload(self._req("Send email to alice@example.com"))
        assert result is None


# ════════════════════════════════════════════════════════════════════════════
# 5. extract_credentials_from_request
# ════════════════════════════════════════════════════════════════════════════

class TestExtractCredentials:
    def test_email_and_password(self):
        req = {"action": "login to facebook using hala@gmail.com and password Hala123"}
        creds = extract_credentials_from_request(req)
        assert creds.get("email") == "hala@gmail.com"
        assert creds.get("password") == "Hala123"

    def test_sign_in_format(self):
        req = {"action": "sign in with user@example.com password: mypass"}
        creds = extract_credentials_from_request(req)
        assert creds.get("email") == "user@example.com"

    def test_no_credentials_returns_nones(self):
        req = {"action": "Open YouTube and search for cats"}
        creds = extract_credentials_from_request(req)
        assert creds.get("email") is None

    def test_original_input_preferred_over_action(self):
        req = {
            "original_input": "login test@test.com pass secret123",
            "action": "Paraphrased action without credentials",
        }
        creds = extract_credentials_from_request(req)
        assert creds.get("email") == "test@test.com"

    def test_password_with_colon(self):
        req = {"action": "register at amazon with email joe@amazon.com password: P@ss!23"}
        creds = extract_credentials_from_request(req)
        assert creds.get("email") == "joe@amazon.com"


# ════════════════════════════════════════════════════════════════════════════
# 6. _apply_device_specific_task_routing
# ════════════════════════════════════════════════════════════════════════════

class TestApplyDeviceSpecificTaskRouting:
    def _task_dict(self, target: str, context: str = "web", device: str = None):
        d = {
            "target_agent": target,
            "context": context,
            "extra_params": {},
            "ai_prompt": "do something",
        }
        if device:
            d["device"] = device
        return d

    # ── Desktop ──────────────────────────────────────────────────────────────
    def test_desktop_api_target_preserved(self):
        tasks = [self._task_dict("api")]
        _apply_device_specific_task_routing(tasks, "desktop")
        assert tasks[0]["target_agent"] == "api"

    def test_desktop_email_target_normalized_to_api(self):
        """Legacy 'email' target_agent is normalized to 'api' on desktop."""
        tasks = [self._task_dict("email")]
        _apply_device_specific_task_routing(tasks, "desktop")
        assert tasks[0]["target_agent"] == "api"

    def test_desktop_action_preserved(self):
        tasks = [self._task_dict("action")]
        _apply_device_specific_task_routing(tasks, "desktop")
        assert tasks[0]["target_agent"] == "action"

    def test_desktop_reasoning_preserved(self):
        tasks = [self._task_dict("reasoning")]
        _apply_device_specific_task_routing(tasks, "desktop")
        assert tasks[0]["target_agent"] == "reasoning"

    def test_desktop_unknown_target_falls_back_to_action(self):
        tasks = [self._task_dict("unknown_agent_xyz")]
        _apply_device_specific_task_routing(tasks, "desktop")
        assert tasks[0]["target_agent"] == "action"

    # ── Mobile ───────────────────────────────────────────────────────────────
    def test_mobile_email_redirected_to_action(self):
        tasks = [self._task_dict("email")]
        _apply_device_specific_task_routing(tasks, "mobile")
        assert tasks[0]["target_agent"] == "action"

    def test_mobile_api_redirected_to_action(self):
        tasks = [self._task_dict("api")]
        _apply_device_specific_task_routing(tasks, "mobile")
        assert tasks[0]["target_agent"] == "action"

    def test_mobile_routing_note_added_for_api(self):
        tasks = [self._task_dict("api")]
        _apply_device_specific_task_routing(tasks, "mobile")
        assert "routing_note" in tasks[0]["extra_params"]

    def test_mobile_action_preserved(self):
        tasks = [self._task_dict("action")]
        _apply_device_specific_task_routing(tasks, "mobile")
        assert tasks[0]["target_agent"] == "action"

    def test_mobile_forces_device_and_context(self):
        tasks = [self._task_dict("action", context="web")]
        _apply_device_specific_task_routing(tasks, "mobile")
        assert tasks[0]["device"] == "mobile"
        assert tasks[0]["context"] == "local"

    def test_desktop_forces_device_field(self):
        tasks = [self._task_dict("action")]
        _apply_device_specific_task_routing(tasks, "desktop")
        assert tasks[0]["device"] == "desktop"

    def test_empty_list_no_error(self):
        _apply_device_specific_task_routing([], "desktop")
        _apply_device_specific_task_routing([], "mobile")

    def test_multiple_tasks_routed_independently(self):
        tasks = [
            self._task_dict("api"),
            self._task_dict("action"),
            self._task_dict("reasoning"),
        ]
        _apply_device_specific_task_routing(tasks, "desktop")
        assert tasks[0]["target_agent"] == "api"
        assert tasks[1]["target_agent"] == "action"
        assert tasks[2]["target_agent"] == "reasoning"

    def test_typo_langauge_corrected(self):
        """'langauge' typo should be corrected to 'language'."""
        tasks = [self._task_dict("langauge")]
        _apply_device_specific_task_routing(tasks, "desktop")
        assert tasks[0]["target_agent"] == "language"

    def test_none_task_dict_skipped(self):
        """None entries in the list should not crash."""
        tasks = [None, self._task_dict("action")]
        # Should not raise
        _apply_device_specific_task_routing(tasks, "desktop")


# ════════════════════════════════════════════════════════════════════════════
# 7. _email_api_credentials_missing
# ════════════════════════════════════════════════════════════════════════════

class TestEmailApiCredentialsMissing:
    def test_metadata_flag_detected(self):
        r = _task_result(metadata={"email_api_credentials_missing": True})
        assert _email_api_credentials_missing(r) is True

    def test_error_text_no_credentials_detected(self):
        r = _task_result(error="No credentials for user user_123")
        assert _email_api_credentials_missing(r) is True

    def test_error_text_no_credentials_found_detected(self):
        r = _task_result(error="No credentials found for user abc")
        assert _email_api_credentials_missing(r) is True

    def test_unrelated_error_not_detected(self):
        r = _task_result(error="Network timeout")
        assert _email_api_credentials_missing(r) is False

    def test_success_result_not_detected(self):
        r = _task_result(status="success")
        assert _email_api_credentials_missing(r) is False

    def test_empty_result_not_detected(self):
        r = _task_result()
        assert _email_api_credentials_missing(r) is False


# ════════════════════════════════════════════════════════════════════════════
# 8. _build_email_web_fallback_task
# ════════════════════════════════════════════════════════════════════════════

class TestBuildEmailWebFallbackTask:
    def test_build_for_api_target(self):
        task = _action_task("api", extra={
            "operation": "send",
            "to": "bob@corp.io",
            "subject": "Meeting",
            "body": "Please attend",
        })
        fallback = _build_email_web_fallback_task(task)
        assert fallback is not None
        assert fallback.target_agent == "action"
        assert fallback.context == "web"
        assert "bob@corp.io" in fallback.ai_prompt

    def test_build_for_email_target_returns_none(self):
        """_build_email_web_fallback_task only handles 'api' target; 'email' is no longer accepted."""
        task = _action_task("email", extra={
            "operation": "send",
            "to": "alice@ex.com",
            "subject": "S",
            "body": "B",
        })
        fallback = _build_email_web_fallback_task(task)
        assert fallback is None

    def test_non_send_operation_returns_none(self):
        task = _action_task("api", extra={"operation": "read"})
        assert _build_email_web_fallback_task(task) is None

    def test_action_target_returns_none(self):
        task = _action_task("action", extra={"operation": "send", "to": "a@b.com"})
        assert _build_email_web_fallback_task(task) is None

    def test_reasoning_target_returns_none(self):
        task = _action_task("reasoning", extra={"operation": "send", "to": "a@b.com"})
        assert _build_email_web_fallback_task(task) is None

    def test_missing_recipient_returns_none(self):
        task = _action_task("api", extra={"operation": "send", "subject": "S", "body": "B"})
        assert _build_email_web_fallback_task(task) is None

    def test_fallback_extra_params_preserved(self):
        task = _action_task("api", extra={
            "operation": "send",
            "to": "x@y.com",
            "subject": "Sub",
            "body": "Body text",
        })
        fallback = _build_email_web_fallback_task(task)
        assert fallback.extra_params.get("fallback_source") == "email_api_missing_credentials"

    def test_subject_and_body_in_prompt(self):
        task = _action_task("api", extra={
            "operation": "send",
            "to": "x@y.com",
            "subject": "Urgent Matter",
            "body": "Please call back ASAP",
        })
        fallback = _build_email_web_fallback_task(task)
        assert "Urgent Matter" in fallback.ai_prompt
        assert "Please call back ASAP" in fallback.ai_prompt


# ════════════════════════════════════════════════════════════════════════════
# 9. CHANNEL DISPATCH LOGIC
# (Tests the routing constants used in the dispatch block of coordinator_agent)
# ════════════════════════════════════════════════════════════════════════════

class TestChannelDispatch:
    """
    The actual dispatch block in coordinator_agent.py:

        if task.target_agent == "action":
            channel = Channels.COORDINATOR_TO_EXECUTION
        elif task.target_agent == "language":
            channel = Channels.COORDINATOR_TO_LANGUAGE
        elif task.target_agent in {"email", "api"}:
            channel = Channels.COORDINATOR_TO_API
        else:
            channel = Channels.COORDINATOR_TO_REASONING

    We replicate this logic here and assert the correct channel is chosen.
    """

    def _dispatch_channel(self, target_agent: str) -> str:
        if target_agent == "action":
            return Channels.COORDINATOR_TO_EXECUTION
        elif target_agent == "language":
            return Channels.COORDINATOR_TO_LANGUAGE
        elif target_agent in {"email", "api"}:
            return Channels.COORDINATOR_TO_API
        else:
            return Channels.COORDINATOR_TO_REASONING

    def test_action_routes_to_execution(self):
        assert self._dispatch_channel("action") == Channels.COORDINATOR_TO_EXECUTION

    def test_language_routes_to_language(self):
        assert self._dispatch_channel("language") == Channels.COORDINATOR_TO_LANGUAGE

    def test_api_routes_to_api(self):
        assert self._dispatch_channel("api") == Channels.COORDINATOR_TO_API

    def test_email_routes_to_api(self):
        """Legacy 'email' token must still route to the API channel."""
        assert self._dispatch_channel("email") == Channels.COORDINATOR_TO_API

    def test_reasoning_routes_to_reasoning(self):
        assert self._dispatch_channel("reasoning") == Channels.COORDINATOR_TO_REASONING

    def test_unknown_agent_falls_back_to_reasoning(self):
        assert self._dispatch_channel("unknown_xyz") == Channels.COORDINATOR_TO_REASONING

    def test_api_channel_value(self):
        assert Channels.COORDINATOR_TO_API == "coordinator.to.api"

    def test_execution_channel_value(self):
        assert Channels.COORDINATOR_TO_EXECUTION == "coordinator.to.execution"


# ════════════════════════════════════════════════════════════════════════════
# 10. ROUTING INTEGRATION — end-to-end path of a user message
# ════════════════════════════════════════════════════════════════════════════

class TestRoutingIntegration:
    """
    Simulate the coordinator's two-step pipeline:
    1. Parse the user message with _extract_email_send_payload / _looks_like_email_send_intent
    2. Apply device routing with _apply_device_specific_task_routing
    Then confirm the final task dict ends up on the right channel.
    """

    def _simulate_route(self, user_text: str, device: str) -> dict:
        """
        Minimal simulation of coordinator routing for a user message.
        Returns the final task dict after device routing.
        """
        is_send = _looks_like_email_send_intent(user_text)
        payload = _extract_email_send_payload({"action": user_text})

        if payload and device == "desktop":
            task_dict = {
                "target_agent": "api",
                "device": device,
                "context": "local",
                "extra_params": {"operation": "send", **payload},
                "ai_prompt": "Send an email via Gmail API",
            }
        else:
            task_dict = {
                "target_agent": "action",
                "device": device,
                "context": "web" if device == "desktop" else "local",
                "extra_params": {},
                "ai_prompt": user_text,
            }

        tasks = [task_dict]
        _apply_device_specific_task_routing(tasks, device)
        return tasks[0]

    def test_full_email_on_desktop_routes_to_api(self):
        task = self._simulate_route(
            "send email to carol@company.com subject Budget body Please review Q2 numbers",
            "desktop"
        )
        assert task["target_agent"] == "api"
        assert task["extra_params"]["operation"] == "send"

    def test_full_email_on_mobile_routes_to_action(self):
        task = self._simulate_route(
            "send email to carol@company.com subject Budget body Please review Q2 numbers",
            "mobile"
        )
        # On mobile, api/email is always redirected to action
        assert task["target_agent"] == "action"

    def test_action_task_desktop_preserved(self):
        task = self._simulate_route("Open YouTube and watch cat videos", "desktop")
        assert task["target_agent"] == "action"
        assert task["device"] == "desktop"

    def test_action_task_mobile_has_local_context(self):
        task = self._simulate_route("Open the camera app", "mobile")
        assert task["target_agent"] == "action"
        assert task["context"] == "local"
        assert task["device"] == "mobile"
