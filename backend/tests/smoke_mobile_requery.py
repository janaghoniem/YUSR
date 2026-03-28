# smoke test to verify that MobileStrategy performs a re-query to TaskMemory with the live screen signature after the first action, and that it sets clear_first=True for type actions when appropriate. This test uses a FakeStrategy and FakeTaskMemory to simulate the behavior without needing a real device or complex setup. The test checks the calls made to TaskMemory and the actions sent to the device, printing out relevant information for verification.
# smoke_mobile_requery.py
import asyncio
import logging

from agents.execution_agent.strategies.mobile_strategy import MobileStrategy
from agents.execution_agent.strategies.task_memory import RetrievalResult, RecipeStep
from agents.utils.device_protocol import MobileTaskRequest, SemanticUITree, UIElement, ActionResult


logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")


class FakeTaskMemory:
    def __init__(self):
        self.calls = []

    def stats(self):
        return "fake-memory"

    def increment_success(self, _):
        pass

    def _build_hint(self, recipes, step):
        return "hint"

    def query(self, step_instruction, overall_goal, app, current_signature, top_k=5):
        self.calls.append(
            {
                "step_instruction": step_instruction,
                "overall_goal": overall_goal,
                "app": app,
                "current_signature": current_signature,
                "top_k": top_k,
            }
        )
        recipe = RecipeStep(
            record_id="rec_1",
            step_instruction="Set the hours to 05",
            overall_goal=overall_goal,
            app=app,
            action_type="type",
            screen_signature="",
            selectors=[{"by": "content_desc", "value": "hour"}],
            param_key=None,
            direction=None,
            typed_value="05",
            expect_screen_change=False,
            similarity=0.99,
            success_count=1,
        )
        if current_signature == "":
            return RetrievalResult(
                band="hint",
                recipes=[recipe],
                hint_text="Go to the first alarm of 4:30 AM",
                best_sim=0.72,
                best_label="Go to first alarm",
            )
        return RetrievalResult(
            band="execute",
            recipes=[recipe],
            hint_text="",
            best_sim=0.99,
            best_label="Set the hours to 05",
        )


def mk_ui(app_name, app_pkg, screen, elements):
    return SemanticUITree(
        device_id="emulator-5554",
        app_name=app_name,
        app_package=app_pkg,
        screen_name=screen,
        screen_width=1080,
        screen_height=2340,
        elements=elements,
    )


home_elements = [
    UIElement(element_id=1, type="text", text="Search", clickable=False),
    UIElement(element_id=2, type="image", text="Camera", clickable=True),
    UIElement(element_id=3, type="image", text="Photos", clickable=True),
    UIElement(element_id=4, type="image", text="Settings", clickable=True),
    UIElement(element_id=5, type="image", text="Gmail", clickable=True),
    UIElement(element_id=6, type="image", text="Maps", clickable=True),
]

clock_elements = [
    UIElement(element_id=101, type="textfield", text="12", content_description="hour", clickable=True, focusable=True),
    UIElement(element_id=102, type="textfield", text="00", content_description="minute", clickable=True, focusable=True),
    UIElement(element_id=103, type="button", text="PM", clickable=True),
    UIElement(element_id=104, type="button", text="OK", clickable=True),
    UIElement(element_id=105, type="button", text="Cancel", clickable=True),
    UIElement(element_id=106, type="text", text="Alarm", clickable=False),
]

home_ui = mk_ui("Android Launcher", "com.google.android.apps.nexuslauncher", "Home", home_elements)
clock_ui = mk_ui("Clock", "com.google.android.deskclock", "Alarm", clock_elements)
clock_ui_after = mk_ui(
    "Clock",
    "com.google.android.deskclock",
    "Alarm",
    [
        UIElement(element_id=101, type="textfield", text="05", content_description="hour", clickable=True, focusable=True),
        UIElement(element_id=102, type="textfield", text="00", content_description="minute", clickable=True, focusable=True),
        UIElement(element_id=103, type="button", text="PM", clickable=True),
        UIElement(element_id=104, type="button", text="OK", clickable=True),
        UIElement(element_id=105, type="button", text="Cancel", clickable=True),
        UIElement(element_id=106, type="text", text="Alarm", clickable=False),
    ],
)


class FakeStrategy(MobileStrategy):
    def __init__(self):
        super().__init__(device_id="emulator-5554")
        self.task_memory = FakeTaskMemory()
        self.sent_actions = []
        self._fetch_calls = []

    async def _execute_action_on_device(self, action):
        payload = action.model_dump()
        self.sent_actions.append(payload)
        print(f"ACTION_PAYLOAD: {payload}")
        return ActionResult(action_id=action.action_id, success=True, execution_time_ms=1)

    async def _fetch_ui_tree_with_retries(self, action_type, max_attempts=3, retry_delay=2.0):
        self._fetch_calls.append(action_type)
        if len(self._fetch_calls) == 1 and action_type == "wait":
            return home_ui
        if action_type in ("global_action", "swipe", "click"):
            return clock_ui
        if action_type == "type":
            return clock_ui_after
        return clock_ui


async def main():
    s = FakeStrategy()
    task = MobileTaskRequest(
        task_id="smoke_alarm",
        ai_prompt="Open the Clock app on mobile device",
        device_id="emulator-5554",
        session_id="s1",
        context={"goal": "Set an alarm for 5:30 PM"},
        extra_params={"app_name": "clock", "goal": "Set an alarm for 5:30 PM"},
        max_steps=6,
        timeout_seconds=30,
    )
    res = await s.execute_task(task)

    print("\n=== SMOKE RESULT ===")
    print("status:", res.status)
    print("completion_reason:", res.completion_reason)
    print("task_memory_calls:", len(s.task_memory.calls))
    for i, c in enumerate(s.task_memory.calls, 1):
        print(
            f"T2_CALL_{i}: sig={'<empty>' if c['current_signature']=='' else '<non-empty>'} "
            f"app={c['app']} step={c['step_instruction']} goal={c['overall_goal']}"
        )

    clear_first_actions = [
        a for a in s.sent_actions
        if a.get("action_type") == "type" and a.get("clear_first") is True
    ]
    print("type_actions_with_clear_first:", len(clear_first_actions))

    assert len(s.task_memory.calls) >= 2, "Expected initial query + in-loop re-query"
    assert s.task_memory.calls[0]["current_signature"] == "", "Initial query should skip signature"
    assert s.task_memory.calls[1]["current_signature"] != "", "In-loop re-query should include live signature"
    assert len(clear_first_actions) >= 1, "Expected at least one type action with clear_first=True"
    print("SMOKE_ASSERTIONS: PASS")


if __name__ == "__main__":
    asyncio.run(main())
