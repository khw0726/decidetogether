from .communities import router as communities_router
from .rules import router as rules_router
from .checklist import router as checklist_router
from .examples import router as examples_router
from .alignment import router as alignment_router
from .decisions import router as decisions_router
from .evaluation import router as evaluation_router
from .health import router as health_router

__all__ = [
    "communities_router",
    "rules_router",
    "checklist_router",
    "examples_router",
    "alignment_router",
    "decisions_router",
    "evaluation_router",
    "health_router",
]
