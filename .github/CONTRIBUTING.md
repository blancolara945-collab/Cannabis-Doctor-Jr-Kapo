# Contributing

Thanks for contributing!

AI-assisted contributions
- If you used an AI assistant to generate code, documentation or tests, mark the PR with the `ai-assisted` label, and in the PR body include:
  - The prompts you used (short summary).
  - What you reviewed or changed from the AI output.
- Human review is required for all AI-generated content. Ensure you add a human reviewer and explain what checks were run.

How to contribute
1. Fork the repository.
2. Create a branch: `git checkout -b my-feature`.
3. Run tests / linters and ensure changes pass.
4. Open a PR with a clear description. Add `ai-assisted` label if applicable.
5. Add reviewers and link any issues.

Automated assistant
- This repo contains an example workflow that can run an AI assistant on new PRs and issues; it is opt-in and requires `OPENAI_API_KEY` to be added as a repository secret.