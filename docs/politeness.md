# Politeness & responsible use

`sitesift` fetches pages from servers you do not own. It is built to be a good
citizen, and it asks the same of you.

## What the tool guarantees

- **One request at a time per host**, with a configurable minimum delay between
  requests to the same host (`fetch.min_host_delay`, default 1.0s).
- **robots.txt is consulted** by default (parsed with `protego`): an explicit
  `Disallow` on a reachable (`2xx`) robots is honored, and blocked URLs are
  recorded with `flags.blocked_robots`. An **unretrievable** robots.txt — a
  `5xx`, an unfollowed `3xx` redirect, a timeout, or a connection error —
  **fails open** (the page is fetched anyway), so a reachable page is never lost
  to a hiccuping or redirecting robots endpoint. Set `fetch.respect_robots =
  false` to skip robots entirely — fetch every URL, ignoring even an explicit
  `Disallow` (intended for research crawls that must maximize coverage; the
  responsibility below is then wholly yours).
- **An identifying User-Agent** including a contact address. The tool refuses to
  fetch without `identity.contact` set (override only with
  `--no-contact-i-accept-responsibility`, which you should not need).
- **Hard limits** on redirects, body size, and decompression size.
- **1–3 pages per domain** — `sitesift` is not a recursive crawler.

## What you are responsible for

- Set a real `identity.contact` so site owners can reach you.
- Do not raise concurrency or lower delays to hammer small sites.
- Do not use `fetch.respect_robots = false` or `security.allow_private_ips`
  against systems you are not authorized to access.
- Respect the terms of service of the sites you fetch.

## Security posture

Every input URL is treated as hostile: SSRF-safe fetching (validated + pinned
IPs, re-checked on every redirect) and prompt-injection-safe classification (page
content is untrusted *data*; the classifier has no tools). See `docs/architecture.md`.
