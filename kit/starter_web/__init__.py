"""starterと下流拡張で共有する家庭学習Web生成機能。"""

from .adapters import StarterMarkdownAdapter
from .model import (
    ActivityItem,
    DashboardData,
    MaterialLink,
    ScheduleItem,
    ScheduleKind,
    Status,
    TestEvent,
)
from .render import render_dashboard

__all__ = [
    "ActivityItem",
    "DashboardData",
    "MaterialLink",
    "ScheduleItem",
    "ScheduleKind",
    "StarterMarkdownAdapter",
    "Status",
    "TestEvent",
    "render_dashboard",
]
