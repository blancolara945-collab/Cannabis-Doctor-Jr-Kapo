# Cannabis-Doctor-Jr-Kapo

Cannabis diagnostic

This repository contains the Cannabis diagnostic project.

AI collaboration (Codex/ChatGPT)
--------------------------------
This repository includes an optional, opt-in example integration that shows how an AI assistant (e.g., ChatGPT / Codex) can help with:
- Drafting reviewer-friendly PR descriptions
- Triage suggestions for new issues
- Adding an `ai-assisted` label to mark AI-generated content (human review required)

Important: the workflows are examples and are disabled until you add the required repository secrets (see below). The assistant is explicitly opt-in — the action only runs when configured with a secret and you can review its suggestions before merging.

Required repository secrets (add these in Settings → Secrets & variables → Actions):
- OPENAI_API_KEY — API key for OpenAI (required to enable the assistant)
- OPENAI_MODEL — (optional) model name, default: gpt-4o
- OPENAI_TEMPERATURE — (optional) float, 0.0–1.0

See .github/openai-config.example for example values and scripts/openai_assistant.py for the implementation used by the workflow.

Security & review
- All AI-generated content must be reviewed by a human before merging.
- Use the `ai-assisted` label for PRs or issues that contain AI-generated content.
