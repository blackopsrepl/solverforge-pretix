from django.utils.translation import gettext_lazy as _
from pretix.base.plugins import PLUGIN_LEVEL_EVENT, PluginConfig

from . import __version__


class SolverForgeSeatingApp(PluginConfig):
    name = "pretix_solverforge_seating"
    verbose_name = _("SolverForge Seat Planner")

    class PretixPluginMeta:
        name = _("SolverForge Seat Planner")
        author = _("SolverForge contributors")
        version = __version__
        category = "FEATURE"
        level = PLUGIN_LEVEL_EVENT
        visible = True
        featured = False
        restricted = False
        description = _(
            "Propose, review, lock, and explicitly commit concrete seat assignments "
            "using native SolverForge."
        )
        compatibility = "pretix==2026.6.1"
        navigation_links: list[
            tuple[tuple[object, object], str, dict[str, object]]
        ] = [
            (
                (_("Orders"), _("SolverForge Seat Planner")),
                "plugins:pretix_solverforge_seating:index",
                {},
            ),
        ]
        settings_links: list[
            tuple[tuple[object, object], str, dict[str, object]]
        ] = [
            (
                (_("SolverForge Seat Planner"), _("Configuration")),
                "plugins:pretix_solverforge_seating:settings",
                {},
            ),
        ]

    def ready(self) -> None:
        from . import signals  # noqa: F401
