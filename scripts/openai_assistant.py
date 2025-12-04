#!/usr/bin/env python3
"""
Patched OpenAI assistant script for GitHub Actions.

Features:
- Reads GITHUB_EVENT_PATH to detect PR/issue events.
- Loads .github/assistant-config.yaml for system prompt, templates, sensitive paths, and other settings.
- Fetches changed files for PRs and extracts small snippets to include in prompts.
- Calls OpenAI ChatCompletion with retries/backoff.
- Posts suggestions as comments and attempts to add an 'ai-assisted' label (creates it if permitted).
- Explicitly warns and avoids calling OpenAI if OPENAI_API_KEY is not set.
"""

import os
import time
import json
import sys
import traceback
import logging
from typing import Optional, List, Dict

from github import Github
import openai
import pathlib
import yaml

# Logging config
logging.basicConfig(level=os.getenv("ASSISTANT_LOG_LEVEL", "INFO"))
log = logging.getLogger("openai_assistant")

# Env / defaults
EVENT_PATH = os.getenv("GITHUB_EVENT_PATH")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.0"))
ASSISTANT_LABEL = os.getenv("ASSISTANT_LABEL", "ai-assisted")
MAX_RESPONSE_TOKENS = int(os.getenv("MAX_RESPONSE_TOKENS", "600"))
RETRY_ATTEMPTS = int(os.getenv("OPENAI_RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF = float(os.getenv("OPENAI_RETRY_BACKOFF", "2.0"))  # seconds, exponential
MAX_SNIPPET_FILES = int(os.getenv("MAX_SNIPPET_FILES", "5"))

if not EVENT_PATH or not os.path.exists(EVENT_PATH):
    log.error("No GITHUB_EVENT_PATH present; exiting.")
    sys.exit(0)

# Load event payload
with open(EVENT_PATH, "r", encoding="utf-8") as f:
    try:
        event = json.load(f)
    except Exception:
        log.exception("Failed to parse GITHUB_EVENT_PATH JSON")
        event = {}

# Load assistant config if present
cfg_path = pathlib.Path(".github/assistant-config.yaml")
assistant_config: Dict = {}
if cfg_path.exists():
    try:
        assistant_config = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        log.exception("Failed to load .github/assistant-config.yaml; using defaults.")
else:
    log.info("No .github/assistant-config.yaml found; using defaults.")

# Set OpenAI key if present
if not OPENAI_API_KEY:
    log.warning("OPENAI_API_KEY not set. The assistant will not call the OpenAI API.")
else:
    openai.api_key = OPENAI_API_KEY

# Initialize GitHub API client if token present
gh = Github(GITHUB_TOKEN) if GITHUB_TOKEN else None

def call_openai_chat(messages: List[dict]) -> Optional[str]:
    if not OPENAI_API_KEY:
        log.info("Skipping OpenAI call because OPENAI_API_KEY is not set.")
        return None
    last_exc = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = openai.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=OPENAI_TEMPERATURE,
                max_tokens=MAX_RESPONSE_TOKENS,
            )
            content = resp["choices"][0]["message"]["content"].strip()
            return content
        except Exception as e:
            last_exc = e
            wait = RETRY_BACKOFF ** attempt
            log.warning("OpenAI call failed (attempt %d/%d): %s. Retrying in %.1fs", attempt, RETRY_ATTEMPTS, e, wait)
            time.sleep(wait)
    log.error("OpenAI calls failed after %d attempts: %s", RETRY_ATTEMPTS, last_exc)
    traceback.print_exception(type(last_exc), last_exc, last_exc.__traceback__)
    return None

def safe_post_comment_and_label(repo_full: str, number: int, body: str):
    if not gh:
        log.info("GITHUB_TOKEN not available; skipping comment/label creation.")
        return
    try:
        repo = gh.get_repo(repo_full)
        issue = repo.get_issue(number=number)
        issue.create_comment(body)
        # Add label if configured
        try:
            labels = [l.name for l in repo.get_labels()]
            if ASSISTANT_LABEL in labels:
                issue.add_to_labels(ASSISTANT_LABEL)
            else:
                try:
                    repo.create_label(ASSISTANT_LABEL, "f29513", "AI-assisted content")
                    issue.add_to_labels(ASSISTANT_LABEL)
                except Exception:
                    log.info("Could not create label %s, possibly due to permissions.", ASSISTANT_LABEL)
        except Exception:
            log.exception("Failed to add label (non-fatal).")
    except Exception:
        log.exception("Failed to post comment or add label; check permissions and GITHUB_TOKEN.")

def fetch_changed_files_for_pr(repo_obj, pr_number) -> List[str]:
    """Return a list of changed file paths for the PR."""
    try:
        pr = repo_obj.get_pull(pr_number)
        files = [f.filename for f in pr.get_files()]
        return files
    except Exception:
        log.exception("Failed to fetch changed files for PR %s", pr_number)
        return []

def get_file_snippet_for_pr(repo_obj, pr_number, file_path, max_lines=40) -> str:
    """
    Attempt to produce a small snippet for a changed file. Prefer the patch if available,
    otherwise fetch the file content at the PR head.
    """
    try:
        pr = repo_obj.get_pull(pr_number)
        for f in pr.get_files():
            if f.filename == file_path:
                # Prefer patch (diff) if available
                if getattr(f, "patch", None):
                    lines = f.patch.splitlines()
                    excerpt = "\n".join(lines[:max_lines])
                    return excerpt
                else:
                    # Fallback: fetch file content at PR head sha
                    try:
                        head_sha = pr.head.sha
                        content_file = repo_obj.get_contents(file_path, ref=head_sha)
                        content = content_file.decoded_content.decode("utf-8", errors="ignore")
                        excerpt = "\n".join(content.splitlines()[:max_lines])
                        return excerpt
                    except Exception:
                        log.debug("Could not fetch raw content for %s at %s", file_path, pr.head.ref)
                        return ""
        return ""
    except Exception:
        log.exception("Error while extracting snippet for %s", file_path)
        return ""

def build_pr_prompt(title: str, body: str, changed_files: List[str], snippets: Dict[str, str], repo_full: str, pr_number: int, assistant_config: Dict) -> List[dict]:
    # Compose changed_files_list
    changed_files_list = "\n".join(f"- {p}" for p in changed_files) if changed_files else "No file list available."
    sensitive_paths = assistant_config.get("sensitive_paths", [])
    system = assistant_config.get("system_prompt", "You are a cautious, security-minded coding assistant. Prioritize human review and be concise.")
    pr_template = assistant_config.get("pr_prompt", "")
    # Fallback user content if template missing
    if pr_template:
        try:
            user_content = pr_template.format(
                title=title,
                body=body,
                changed_files_list=changed_files_list,
                repo_full=repo_full,
                pr_number=pr_number
            )
        except Exception:
            log.exception("Failed to format pr_prompt template; falling back to default.")
            user_content = f"PR title: {title}\n\nPR body:\n{body}\n\nChanged files:\n{changed_files_list}"
    else:
        user_content = f"PR title: {title}\n\nPR body:\n{body}\n\nChanged files:\n{changed_files_list}\n\nTask: Write a concise, reviewer-friendly PR description and a focused reviewer checklist. Highlight any security-sensitive files and recommend manual checks."

    # Build a context block including small snippets for top changed files
    context_lines = ["Changed files (top-level list):", changed_files_list, "", "Sensitive path patterns:"]
    context_lines.extend(f"- {p}" for p in sensitive_paths)
    if snippets:
        context_lines.append("\nSnippets from changed files (limited):")
        for path, snippet in snippets.items():
            context_lines.append(f"--- {path} ---")
            context_lines.append(snippet or "(no snippet available)")
    context_block = "\n".join(context_lines)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": context_block},
        {"role": "user", "content": user_content}
    ]
    return messages

def build_issue_prompt(title: str, body: str, repo_full: str, issue_number: int, assistant_config: Dict) -> List[dict]:
    system = assistant_config.get("system_prompt", "You are a careful triage assistant. Request reproduction steps and emphasize security when relevant.")
    issue_template = assistant_config.get("issue_prompt", "")
    if issue_template:
        try:
            user_content = issue_template.format(
                title=title,
                body=body,
                repo_full=repo_full,
                issue_number=issue_number
            )
        except Exception:
            log.exception("Failed to format issue_prompt template; falling back to default.")
            user_content = f"Issue title: {title}\n\nBody:\n{body}\n\nTask: Triage this issue, suggest severity and next steps."
    else:
        user_content = f"Issue title: {title}\n\nBody:\n{body}\n\nTask: Triage this issue, suggest severity and next steps."

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content}
    ]
    return messages

def handle_pull_request(payload: dict):
    pr = payload.get("pull_request", {})
    title = pr.get("title", "")
    body = pr.get("body") or ""
    repo_full = payload.get("repository", {}).get("full_name")
    pr_number = pr.get("number")

    if not repo_full or pr_number is None:
        log.error("PR payload missing repository or pr number; aborting.")
        return

    log.info("Handling PR %s#%s", repo_full, pr_number)

    repo_obj = gh.get_repo(repo_full) if gh else None
    changed_files = []
    if repo_obj:
        changed_files = fetch_changed_files_for_pr(repo_obj, pr_number)

    # Prepare snippets for a few top changed files to limit token usage
    max_snip_lines = assistant_config.get("max_file_snippet_lines", 40)
    snippets = {}
    for path in (changed_files[:MAX_SNIPPET_FILES] if changed_files else []):
        try:
            snippets[path] = get_file_snippet_for_pr(repo_obj, pr_number, path, max_lines=max_snip_lines)
        except Exception:
            snippets[path] = ""

    messages = build_pr_prompt(title, body, changed_files, snippets, repo_full, pr_number, assistant_config)
    suggestion = call_openai_chat(messages)
    if not suggestion:
        log.info("No suggestion returned from OpenAI for PR.")
        return

    comment = (
        ":robot: AI assistant suggestion (please review carefully):\n\n"
        f"{suggestion}\n\n"
        "_Note: This is an automated suggestion. All AI-generated content must be reviewed by a human before merging._"
    )
    safe_post_comment_and_label(repo_full, pr_number, comment)

def handle_issue(payload: dict):
    issue = payload.get("issue", {})
    title = issue.get("title", "")
    body = issue.get("body") or ""
    repo_full = payload.get("repository", {}).get("full_name")
    issue_number = issue.get("number")

    if not repo_full or issue_number is None:
        log.error("Issue payload missing repository or issue number; aborting.")
        return

    log.info("Handling issue %s#%s", repo_full, issue_number)
    messages = build_issue_prompt(title, body, repo_full, issue_number, assistant_config)
    suggestion = call_openai_chat(messages)
    if not suggestion:
        log.info("No suggestion returned from OpenAI for issue.")
        return

    comment = (
        ":robot: AI assistant triage suggestion (please review):\n\n"
        f"{suggestion}\n\n"
        "_Note: This is an automated suggestion. All AI-generated content must be reviewed by a human._"
    )
    safe_post_comment_and_label(repo_full, issue_number, comment)

def main():
    try:
        if "pull_request" in event:
            handle_pull_request(event)
        elif "issue" in event:
            handle_issue(event)
        else:
            log.info("Event is not a pull_request or issue; nothing to do.")
    except Exception:
        log.exception("Unhandled exception in assistant main loop.")
        sys.exit(1)

if __name__ == "__main__":
    main()
