from __future__ import annotations

from collections.abc import Iterator

import pytest
from django_scopes import scope

from pretix_solverforge_seating.demo import DemoResult, create_demo


@pytest.fixture
def demo(db: object) -> Iterator[DemoResult]:
    result = create_demo()
    with scope(organizer=result.organizer):
        yield result
