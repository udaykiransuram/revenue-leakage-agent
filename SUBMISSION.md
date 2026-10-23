# Private GitHub submission preparation

The source, supplied challenge JSON, challenge brief, tests, environment templates, dependency lock and prompt log are packaged locally. Earlier invented active records are excluded. Private source repository: https://github.com/udaykiransuram/revenue-leakage-agent. No collaborator invitation has been created.

Before submission:

1. Review the supplied-schema adapter and focused source tests. Original JSON stays immutable; approved changes are sandbox-only.
2. Complete real-model conversational lifecycle acceptance. Groq is configured without an Ollama fallback; existing live read-only checks passed. Explicit data-sharing approval subsequently allowed a fresh lifecycle attempt, but free-tier rate limits and a model omission prevented acceptance. The test proposal was rejected without financial changes. Sponsor-model validation, if required, remains separate. Keep credentials out of Git.
3. Review `candidate_prompts.md`; add any earlier prompts not available in this implementation chat rather than guessing their wording.
4. Keep the GitHub repository **private**. Source uploads exclude real environment files, runtime sandboxes, checkpoints, archives and Python environments.
5. Add the explicitly identified reviewer with **read** access. The reviewer identity is still missing; do not guess.
6. Submit the repository URL through the requested channel. This implementation has not sent messages to the reviewer.

Example preparation commands, only after choosing and authenticating the intended account:

```sh
git init
git add .
git commit -m "Implement conversational revenue leakage investigator"
gh repo create revenue-leakage-agent --private --source=. --push
```

The GitHub CLI was not installed in the implementation environment. Browser-based creation is possible once the intended account and submission details are established; no credentials or reviewer information should be fabricated.
