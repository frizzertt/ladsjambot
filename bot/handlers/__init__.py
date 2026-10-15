from bot.handlers.private import router as private_router
from bot.handlers.group import router as group_router
from bot.handlers.stats import router as stats_router

__all__ = ["private_router", "group_router", "stats_router"]
