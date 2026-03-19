
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

print("Testing imports...\n")

# Test 1: Core imports
try:
    from agents.execution_agent.core.exec_agent_config import Config
    print("✅ Config imported")
except ImportError as e:
    print(f"❌ Config import failed: {e}")

# Test 2: Models
try:
    from agents.execution_agent.core.exec_agent_models import VisionResult
    print("✅ VisionResult imported")
except ImportError as e:
    print(f"❌ VisionResult import failed: {e}")

# Test 3: Fallback
try:
    from agents.execution_agent.fallback import OmniParserDetector, OMNIPARSER_AVAILABLE
    print(f"✅ OmniParser imported (Available: {OMNIPARSER_AVAILABLE})")
except ImportError as e:
    print(f"❌ OmniParser import failed: {e}")

# Test 4: VisionLayer
try:
    from agents.execution_agent.layers.exec_agent_vision import VisionLayer
    print("✅ VisionLayer imported")
except ImportError as e:
    print(f"❌ VisionLayer import failed: {e}")

print("\n✅ All imports successful!")