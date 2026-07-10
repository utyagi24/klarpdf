# Security Policy

## Supported versions

KlarPDF is a small, single-maintainer project. Security fixes land on the **latest release only** —
there are no long-term-support branches. Please make sure you are on the newest version
([Releases](https://github.com/utyagi24/klarpdf/releases/latest)) before reporting an issue.

| Version | Supported          |
|---------|--------------------|
| Latest release | :white_check_mark: |
| Anything older | :x:                |

## Threat model (what to expect)

KlarPDF runs **fully offline** — it makes no network requests at install or runtime and collects **no
telemetry** (see `PLAN.md` §Packaging, dependencies & installer). It opens no sockets of its own, so
the realistic attack surface is **not** a network service. It is **malicious PDF (or image) input**
handled by the underlying parsers — primarily **PyMuPDF (MuPDF)** and **pypdf** — that KlarPDF drives
to render, edit, and save documents. A crafted file that triggers memory corruption or a
denial-of-service in one of those libraries is the most likely class of vulnerability.

The dependencies are pinned with hashes, vendored for an auditable offline build, and continuously
scanned for known advisories (`pip-audit` in CI + Dependabot alerts); upstream library fixes are
pulled in per `RELEASE.md`. If your report is really an upstream MuPDF / pypdf bug, we will still
triage it and bump the pinned version once a fix ships, but the root-cause fix belongs upstream.

## Reporting a vulnerability

**Please do not open a public issue for a security problem, and do not email the maintainer.**

Report privately through GitHub's **private vulnerability reporting**:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability** (under "Advisories").
3. Fill in the advisory form with as much detail as you can — ideally a sample PDF that reproduces
   the problem, the KlarPDF version (Help ▸ About, or the installer filename), your Windows version,
   and what you observed.

> **Maintainer note:** private vulnerability reporting must be enabled for the repository
> (**Settings ▸ Code security ▸ Private vulnerability reporting**) before the "Report a vulnerability"
> button appears. This is a manual repo-settings step.

If, for some reason, you cannot use GitHub's private reporting form, open a normal issue that says
only *"I'd like to report a security issue privately"* — with **no technical details** — so the
maintainer can arrange a private channel.

### What to expect

KlarPDF is maintained by a single unpaid volunteer, so there is **no guaranteed response-time SLA**.
As a realistic expectation, aim for an **acknowledgement within about two weeks**. Fixes are made on
a best-effort basis and shipped in the next release. Please practice responsible disclosure and give
the maintainer a reasonable window to respond before disclosing publicly.

Thank you for helping keep KlarPDF and its users safe.
