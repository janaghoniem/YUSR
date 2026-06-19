from .local_strategy import LocalStrategy
from .system_strategy import SystemStrategy

try:
	from .web_strategy import WebStrategy
except ImportError:
	WebStrategy = None

__all__ = ['LocalStrategy', 'WebStrategy', 'SystemStrategy']