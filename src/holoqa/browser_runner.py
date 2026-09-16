from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any


class BlockedBrowserStep(ValueError):
    """A test could not proceed because the runner lacks a safe action adapter."""


class CancelledBrowserRun(RuntimeError):
    """The user requested cancellation while browser execution was active."""


def _dismiss_overlays(page: Any) -> None:
    """Dismiss common consent/onboarding overlays before user-facing actions."""
    patterns = (
        re.compile(r"dismiss cookie message", re.IGNORECASE),
        re.compile(r"^me want it!$", re.IGNORECASE),
        re.compile(r"close welcome banner", re.IGNORECASE),
        re.compile(r"^dismiss$", re.IGNORECASE),
    )
    for pattern in patterns:
        try:
            locator = page.get_by_role("button", name=pattern)
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=1000)
                page.wait_for_timeout(50)
        except Exception:
            # Consent/onboarding UI is optional and may disappear during SPA navigation.
            continue


def _evaluate_expected(expected: str, page: Any, responses: list[dict[str, Any]]) -> tuple[str, str]:
    """Evaluate explicit assertions while retaining compatibility with legacy prose."""
    observed_text = page.locator("body").inner_text()
    normalized = expected.strip()
    if normalized.upper().startswith("PASS_IF "):
        expression = normalized[8:].strip()
        match = re.fullmatch(r"URL\s+contains\s+(.+)", expression, flags=re.IGNORECASE)
        if match:
            passed = match.group(1).strip().lower() in page.url.lower()
        else:
            match = re.fullmatch(r"TEXT\s+(contains|not_contains)\s+(.+)", expression, flags=re.IGNORECASE)
            if match:
                needle = match.group(2).strip().lower()
                contains = needle in observed_text.lower()
                passed = contains if match.group(1).lower() == "contains" else not contains
            else:
                match = re.fullmatch(r"STATUS\s+(?:equals|is)\s+(\d{3})", expression, flags=re.IGNORECASE)
                if not match:
                    raise ValueError(f"unsupported assertion: {normalized}")
                expected_status = int(match.group(1))
                passed = any(response["status"] == expected_status for response in responses)
        return ("PASS" if passed else "FAIL", f"Assertion {expression}: {'satisfied' if passed else 'not satisfied'}")

    expected_tokens = [
        token.lower()
        for token in re.findall(r"[\w@.+-]+", normalized)
        if len(token) > 2 and token.lower() not in {"appears", "visible", "shown", "displayed", "the", "and", "is"}
    ]
    passed = not expected_tokens or all(token in observed_text.lower() for token in expected_tokens)
    return ("PASS" if passed else "FAIL", observed_text[:1000])


def execute_cases(cases: list[dict[str, Any]], base_url: str, artifact_dir: Path, on_event: Any | None = None, cancel_check: Any | None = None) -> list[dict[str, Any]]:
    """Execute the documented demo workflow and preserve step-level evidence."""
    from playwright.sync_api import sync_playwright

    results = []
    emit = on_event or (lambda **_: None)
    emit(event_type="execution_started", message=f"Executing {len(cases)} case(s)", payload={"total_cases": len(cases)})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for case in cases:
            if cancel_check and cancel_check():
                raise CancelledBrowserRun("Run cancelled before the next test case")
            case_dir = artifact_dir / case["test_id"]
            case_dir.mkdir(parents=True, exist_ok=True)
            page = browser.new_page()
            evidence: list[str] = []
            step_results: list[dict[str, Any]] = []
            responses: list[dict[str, Any]] = []
            console_messages: list[str] = []
            page.on("response", lambda response: responses.append({"url": response.url, "status": response.status, "method": response.request.method}))
            page.on("console", lambda message: console_messages.append(message.text))
            try:
                emit(event_type="case_started", test_id=case["test_id"], message=case.get("title", ""), payload={"total_cases": len(cases), "completed_cases": len(results)})
                page.goto(f"{base_url.rstrip('/')}/demo", wait_until="domcontentloaded")
                page.wait_for_timeout(200)
                _dismiss_overlays(page)
                evidence.append(str(case_dir / "initial.png"))
                page.screenshot(path=evidence[-1], full_page=True)
                for step in case.get("steps", []):
                    if cancel_check and cancel_check():
                        raise CancelledBrowserRun(f"Run cancelled during {case['test_id']}")
                    emit(event_type="step_started", test_id=case["test_id"], step=step, message="Executing browser step")
                    step_lower = step.lower().strip()
                    before = len(responses)
                    navigation = re.search(r"(?:open|navigate)(?:\s+to)?\s+(https?://\S+|/\S*)", step, flags=re.IGNORECASE)
                    if navigation:
                        target = navigation.group(1).rstrip(".,;")
                        if target.startswith("/"):
                            target = f"{base_url.rstrip('/')}{target}"
                        page.goto(target, wait_until="domcontentloaded")
                        page.wait_for_timeout(200)
                        _dismiss_overlays(page)
                        step_results.append({"step": step, "status": "PASS", "actual_result": f"Navigated to {page.url}"})
                        emit(event_type="step_finished", test_id=case["test_id"], step=step, status="PASS", message=f"Navigated to {page.url}")
                        continue
                    match = re.search(r"(?:create|submit)\s+(?:user\s+)?(.+)", step, flags=re.IGNORECASE)
                    if match and "open" not in step_lower and "navigate" not in step_lower and ("user" in step_lower or step_lower.startswith("create")):
                        email = match.group(1).strip()
                        page.get_by_label("Email").fill(email)
                        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                            page.get_by_role("button", name="Create user").click()
                        else:
                            # Native email validation blocks malformed values. Submit the
                            # form programmatically so the server-side 422 is observable.
                            page.locator("form").evaluate("form => form.submit()")
                        page.wait_for_load_state("networkidle")
                        observed_step = page.locator("body").inner_text()
                        step_responses = responses[before:]
                        step_status = "FAIL" if any(response["status"] >= 500 for response in step_responses) else "PASS"
                        step_results.append({"step": step, "status": step_status, "actual_result": observed_step[:500], "responses": step_responses})
                        emit(event_type="step_finished", test_id=case["test_id"], step=step, status=step_status, message="User creation step completed")
                    elif re.search(r"click\s+(?:the\s+)?create user", step, flags=re.IGNORECASE):
                        page.get_by_role("button", name="Create user").click()
                        page.wait_for_load_state("networkidle")
                        step_results.append({"step": step, "status": "PASS", "actual_result": page.locator("body").inner_text()[:500], "responses": responses[before:]})
                        emit(event_type="step_finished", test_id=case["test_id"], step=step, status="PASS", message="Create-user click completed")
                    elif step_lower.startswith("wait"):
                        page.wait_for_timeout(100)
                        step_results.append({"step": step, "status": "PASS", "actual_result": "Wait completed"})
                        emit(event_type="step_finished", test_id=case["test_id"], step=step, status="PASS", message="Wait completed")
                    else:
                        raise BlockedBrowserStep(f"unsupported browser step: {step}")
                page.wait_for_timeout(100)
                final = case_dir / "final.png"
                page.screenshot(path=final, full_page=True)
                evidence.append(str(final))
                network_path = case_dir / "network.json"
                console_path = case_dir / "console.json"
                dom_path = case_dir / "dom.txt"
                navigation_path = case_dir / "navigation.json"
                network_path.write_text(json.dumps(responses, indent=2), encoding="utf-8")
                console_path.write_text(json.dumps(console_messages, indent=2), encoding="utf-8")
                dom_path.write_text(page.locator("body").inner_text(), encoding="utf-8")
                navigation_path.write_text(json.dumps({"url": page.url}, indent=2), encoding="utf-8")
                evidence.extend(str(path) for path in (network_path, console_path, dom_path, navigation_path))
                expected = str(case.get("expected_result", ""))
                verdict, actual_result = _evaluate_expected(expected, page, responses)
                if any(response["status"] >= 500 for response in responses):
                    verdict = "FAIL"
                results.append({
                    "test_id": case["test_id"],
                    "status": verdict,
                    "actual_result": actual_result,
                    "evidence": ";".join(evidence),
                    "evidence_items": [{"path": path, "kind": kind} for path, kind in zip(evidence, ["screenshot", "screenshot", "network", "console", "dom", "navigation"])],
                    "steps": step_results,
                    "network": responses,
                    "console": console_messages,
                })
                emit(event_type="case_finished", test_id=case["test_id"], status=verdict, message=actual_result[:240], completed_cases=len(results), payload={"evidence": evidence})
            except CancelledBrowserRun:
                raise
            except Exception as error:
                failure = case_dir / "failure.png"
                page.screenshot(path=failure, full_page=True)
                status = "BLOCKED" if isinstance(error, BlockedBrowserStep) else "FAIL"
                result = {"test_id": case["test_id"], "status": status, "actual_result": f"{status}: {error}", "evidence": str(failure), "steps": step_results, "network": responses, "console": console_messages}
                results.append(result)
                emit(event_type="case_finished", test_id=case["test_id"], status=status, message=result["actual_result"][:240], completed_cases=len(results), payload={"evidence": result["evidence"]})
            finally:
                page.close()
        browser.close()
    return results
