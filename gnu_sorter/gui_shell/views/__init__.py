"""Content views for the Funnel desk.

Each module builds one page of the window's stack. Builders only read and
write state through the window's controller.
"""

from .inputs import build_inputs
from .log import build_log
from .plan import build_plan

__all__ = ["build_plan", "build_inputs", "build_log"]
