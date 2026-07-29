from django.urls import re_path

from . import views

BASE = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/solverforge-seat-planner"

urlpatterns = [
    re_path(BASE + r"/$", views.IndexView.as_view(), name="index"),
    re_path(BASE + r"/settings/$", views.SettingsView.as_view(), name="settings"),
    re_path(BASE + r"/generate/$", views.GenerateView.as_view(), name="generate"),
    re_path(
        BASE + r"/proposal/(?P<proposal_id>\d+)/lock/$",
        views.LockView.as_view(),
        name="lock",
    ),
    re_path(
        BASE + r"/lock-existing/$",
        views.LockExistingView.as_view(),
        name="lock.existing",
    ),
    re_path(BASE + r"/unlock/$", views.UnlockView.as_view(), name="unlock"),
    re_path(
        BASE + r"/proposal/(?P<proposal_id>\d+)/discard/$",
        views.DiscardView.as_view(),
        name="discard",
    ),
    re_path(
        BASE + r"/proposal/(?P<proposal_id>\d+)/commit/confirm/$",
        views.CommitConfirmView.as_view(),
        name="commit.confirm",
    ),
    re_path(
        BASE + r"/proposal/(?P<proposal_id>\d+)/commit/$",
        views.CommitView.as_view(),
        name="commit",
    ),
]
