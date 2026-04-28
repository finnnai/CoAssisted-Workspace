# Security Policy

## Reporting a Vulnerability

Email security@example.com for security issues.
DO NOT open a public GitHub issue for vulnerabilities.

## Scope

This codebase handles OAuth tokens, Anthropic API keys, and personal data
(emails, calendar events, contacts). Files NEVER committed:
- `token.json`, `credentials.json`, `config.json`
- `merchants.json`, `projects.json`, `awaiting_info.json`
- `brand-voice.md`

These are listed in `.gitignore`.
