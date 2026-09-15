from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def execute_cases(cases: list[dict[str, Any]], base_url: str, artifact_dir: Path) -> list[dict[str, Any]]:
    """Execute supported browser cases against a running application."""
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for case in cases:
            case_dir = artifact_dir / case["test_id"]
            case_dir.mkdir(parents=True, exist_ok=True)
            page = browser.new_page()
            evidence: list[str] = []
            try:
                page.goto(f"{base_url.rstrip('/')}/demo", wait_until="networkidle")
                evidence.append(str(case_dir / "initial.png"))
                page.screenshot(path=evidence[-1], full_page=True)
                for step in case.get("steps", []):
                    match = re.search(r"create user\s+(.+)", step, flags=re.IGNORECASE)
                    if match:
                        email = match.group(1).strip()
                        page.get_by_label("Email").fill(email)
                        page.get_by_role("button", name="Create user").click()
                page.wait_for_timeout(100)
                final = case_dir / "final.png"
                page.screenshot(path=final, full_page=True)
                evidence.append(str(final))
                expected = case.get("expected_result", "")
                observed = page.locator("body").inner_text().lower()
                expected_tokens = [
                    token.lower()
                    for token in re.findall(r"[\w@.+-]+", expected)
                    if len(token) > 2 and token.lower() not in {"appears", "visible", "shown", "displayed"}
                ]
                passed = not expected_tokens or all(token in observed for token in expected_tokens)
                results.append({
                    "test_id": case["test_id"],
                    "status": "PASS" if passed else "FAIL",
                    "actual_result": page.locator("body").inner_text()[:1000],
                    "evidence": ";".join(evidence),
                })
            except Exception as error:
                failure = case_dir / "failure.png"
                page.screenshot(path=failure, full_page=True)
                results.append({"test_id": case["test_id"], "status": "FAIL", "actual_result": str(error), "evidence": str(failure)})
            finally:
                page.close()
        browser.close()
    return results
