# DeepSeek Super-agent: isolated pilot (NOT DEPLOYED)

Status: **planning only**. No agent, daemon, webhook, token, or model has been installed.

## Goal
Evaluate [dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype) on non-production coding tasks without access to TOTISH production resources.

## Hard safety boundary
- Run on a separate disposable VM or sandbox, **not** on the TOTISH VPS or a machine holding production credentials.
- Use a dedicated unprivileged OS user and a separate GitHub agent account with minimal repository permissions.
- Never copy production `.env`, Neon credentials, SSH deploy keys, Telegram bot tokens, or GHCR publishing credentials into the sandbox.
- Do not grant GitHub Actions administration, secrets, environments, or workflow editing rights to the agent.
- Agent must not merge PRs, push to `main`, trigger `workflow_dispatch`, deploy, or modify database data.
- Use a dedicated **fork or disposable test repository** for initial execution; repository branch permissions alone do not sandbox shell access.
- Disable auto-merge; keep proactive admission in `observe` mode.
- Treat GitHub comments, issues, PR descriptions and external content as untrusted prompt input.
- Human approval required before granting write access to the real TOTISH repository.

## Important production coupling
`.github/workflows/deploy.yml` deploys automatically after successful `CI` on a push to `main`. Therefore **merging an agent PR into main can deploy production**. Never use an autonomous merge as a pilot test.

## Planned configuration (not applied)
- Source: GitHub only; Feishu disabled; Telegram integration postponed.
- Scope: disposable fork of `mmaximm2017-netizen/football-totalizator`.
- Actor allowlist: operator only.
- Branch prefix: `agent/`.
- Acceptance: `observe` for unsolicited work; explicit tasks only for first tests.
- Auto-merge: off.
- Harness: pinned compatible version from upstream deployment guide, currently `@deepseek-ai/dsh@0.2.1-rc.3`.
- Separate Harness profile `superagent`, state directory isolated from production, web panel bound to localhost.
- Model: undecided; verify a zero-paid-API provider before execution. Open-source code does not imply free inference.

## Pilot gates
1. Provision disposable sandbox with no production secrets or network route to production DB/VPS.
2. Create dedicated agent GitHub account and fork; verify effective repository permissions.
3. Check pinned upstream commit, dependencies, install scripts, and package lock; verify prerequisites and model cost.
4. Run upstream tests/build in sandbox, record results.
5. Start agent with GitHub-only and `observe` policies.
6. Test (a) harmless documentation PR, (b) intentionally failing CI in test fork, (c) stop/restart recovery.
7. Verify no changes to production, no unapproved merges, and no leaked secrets.
8. Report actual costs, runtime, failures, and security findings before requesting permission for further integration.

## Exit / rollback
Stop the sandbox process, revoke agent GitHub authorization, delete disposable fork/VM and temporary credentials. Do not touch production deployment or Neon.

## Sources
- https://github.com/deepseek-ai/dsh-superagent-prototype
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/SETUP.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/006-deployment.md
