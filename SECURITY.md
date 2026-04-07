# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main` (latest) | Yes |
| Older tags | No — please upgrade |

## Reporting a vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Report security issues privately via GitHub's built-in mechanism:

1. Go to the [Security tab](https://github.com/ranjan008/agentix/security) of this repository
2. Click **"Report a vulnerability"**
3. Fill out the form with as much detail as possible

If GitHub's private reporting is unavailable, email the maintainers directly (address in the repository profile). Please encrypt sensitive details with our PGP key if available.

### What to include

- Description of the vulnerability and its potential impact
- Steps to reproduce (proof-of-concept code is welcome)
- Affected versions / components
- Any suggested mitigations

## Response timeline

| Milestone | Target |
|-----------|--------|
| Acknowledgement | Within 48 hours |
| Initial assessment | Within 5 business days |
| Fix + coordinated disclosure | Within 90 days (critical: 14 days) |

## Scope

The following are **in scope**:

- Authentication bypass (`agentix/security/`, `agentix/watchdog/auth.py`)
- RBAC policy escape (`agentix/security/rbac.py`)
- Secrets leakage (`agentix/security/secrets.py`)
- Remote code execution via tool execution (`agentix/agent_runtime/tool_executor.py`)
- Prompt injection leading to data exfiltration
- JWT forgery or replay attacks

The following are **out of scope**:

- Vulnerabilities in third-party dependencies (report to them directly)
- Issues requiring physical access to the host
- Social engineering attacks
- Rate limiting / DoS on self-hosted instances

## Disclosure policy

We follow **coordinated disclosure**. We ask that you give us the response timeline above before public disclosure. We will credit reporters in the release notes unless they prefer to remain anonymous.
