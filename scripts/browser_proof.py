from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright


def _login(page: Page, base_url: str, email: str, password: str) -> None:
    page.goto(f"{base_url}/control/login", wait_until="networkidle")
    page.locator('input[name="email"]').fill(email)
    page.locator('input[name="password"]').fill(password)
    with page.expect_navigation(wait_until="networkidle"):
        page.get_by_role("button", name="Log in", exact=True).click()


def _proposal_row(page: Page, order_code: str) -> Any:
    heading = page.get_by_role("heading", name="Proposed placements", exact=True)
    if heading.count() != 1:
        raise AssertionError("The proposed-placement table is not visible.")
    panel = heading.locator("xpath=ancestor::div[contains(@class, 'panel')]")
    row = panel.locator("tbody tr").filter(has_text=order_code)
    if row.count() != 1:
        raise AssertionError(f"Expected exactly one proposal row for {order_code}.")
    return row


def run_proof(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    planner_url = (
        f"{base_url}/control/event/{args.organizer}/{args.event}/"
        "solverforge-seat-planner/"
    )
    order_url = (
        f"{base_url}/control/event/{args.organizer}/{args.event}/"
        f"orders/{args.lock_order}/"
    )
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=args.chromium,
            headless=not args.headed,
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text)
                if message.type == "error"
                else None
            ),
        )
        _login(page, base_url, args.email, args.password)
        page.goto(planner_url, wait_until="networkidle")
        if page.get_by_role("button", name="Generate proposal", exact=True).count() != 1:
            raise AssertionError(
                "Browser proof requires a fresh demo event with no current proposal."
            )
        if page.locator("#solverforge-seat-map .sf-seat").count() != 48:
            raise AssertionError("The deterministic 48-seat map did not render.")
        page.screenshot(path=output / "before-solve.png", full_page=True)

        with page.expect_navigation(wait_until="networkidle", timeout=120_000):
            page.get_by_role(
                "button",
                name="Generate proposal",
                exact=True,
            ).click()
        if page.locator(".sf-seat--proposed").count() != 15:
            raise AssertionError("The proposal did not contain all 15 ticket positions.")
        score = page.locator("code").filter(has_text="hard").all_inner_texts()
        if "0 hard /" not in " ".join(score):
            raise AssertionError("The native proposal does not have zero hard violations.")
        page.screenshot(path=output / "after-solve.png", full_page=True)

        row = _proposal_row(page, args.lock_order)
        locked_seats = row.locator("td").all_inner_texts()[1]
        lock_button = row.get_by_role("button", name="Lock", exact=True)
        if lock_button.count() != 1:
            raise AssertionError("The selected proposal placement cannot be locked.")
        with page.expect_navigation(wait_until="networkidle"):
            lock_button.click()
        replan = page.get_by_role("button", name="Replan", exact=True)
        if replan.count() != 1:
            raise AssertionError("Replan is unavailable after locking.")
        with page.expect_navigation(wait_until="networkidle", timeout=120_000):
            replan.click()
        replanned_row = _proposal_row(page, args.lock_order)
        if "Locked" not in replanned_row.inner_text():
            raise AssertionError("The placement lock was not preserved by replanning.")
        if locked_seats.splitlines()[0] not in replanned_row.inner_text():
            raise AssertionError("The locked seat block changed during replanning.")
        page.screenshot(path=output / "after-lock-replan.png", full_page=True)

        commit_link = page.get_by_role(
            "link",
            name="Commit proposal…",
            exact=True,
        )
        if commit_link.count() != 1:
            raise AssertionError("The zero-hard proposal is not committable.")
        with page.expect_navigation(wait_until="networkidle"):
            commit_link.click()
        page.get_by_label(
            "I understand that this writes the proposed seats to real order positions.",
            exact=True,
        ).check()
        with page.expect_navigation(wait_until="networkidle", timeout=120_000):
            page.get_by_role(
                "button",
                name="Commit real seat assignments",
                exact=True,
            ).click()
        if page.get_by_text("is committed.", exact=False).count() != 1:
            raise AssertionError("The planner did not confirm a committed proposal.")
        page.screenshot(path=output / "after-commit.png", full_page=True)

        response = page.goto(order_url, wait_until="networkidle")
        if response is None or response.status != 200:
            raise AssertionError("The ordinary pretix order detail did not load.")
        order_text = page.locator("body").inner_text()
        expected_names = [
            f"Stalls, Row A, Seat {number}"
            for number in (7, 8, 9)
        ]
        if not all(value in order_text for value in expected_names):
            raise AssertionError(
                "The committed seats are not visible in the pretix order detail."
            )
        page.screenshot(
            path=output / "pretix-order-after-commit.png",
            full_page=True,
        )
        browser.close()

    return {
        "base_url": base_url,
        "planner_url": planner_url,
        "locked_order": args.lock_order,
        "locked_seats": locked_seats.splitlines()[0],
        "score": score,
        "pretix_order_seats": expected_names,
        "console_errors": console_errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the deterministic SolverForge pretix browser proof.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8345")
    parser.add_argument("--organizer", default="solverforge-demo")
    parser.add_argument("--event", default="assigned-seating")
    parser.add_argument("--lock-order", default="SF003")
    parser.add_argument("--email", default="admin@solverforge.invalid")
    parser.add_argument(
        "--password",
        default=os.environ.get(
            "SOLVERFORGE_PRETIX_DEMO_PASSWORD",
            "solverforge-demo",
        ),
    )
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--output", default="evidence/screenshots")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    result = run_proof(args)
    result_path = Path(args.output).resolve().parent / "runtime" / "browser-proof.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
