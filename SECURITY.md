# Security

## Reporting a problem

Please do not open a public issue. Use **Report a vulnerability** in the *Security* tab of this
repository. This is a spare-time project: there is no promised response time.

Never paste a renewal link, a `code`, a `state`, a token or a Client Secret in an issue.

## What is exposed

One address, without login: `GET /api/reauth_link/callback`. Home Assistant has a similar one,
`/auth/external/callback`.

| Who | Can | Cannot |
|---|---|---|
| Anyone on the internet | find out that the address exists (it answers 400 with its own page) | start a sign-in flow, trigger a login alert, learn the integration or the account |
| Someone who got a renewal link | try codes (10 different ones that fail turn the link off); approve with the **right** account; remove permissions from the link, so that an approval of it leaves the integration asking to sign in again | connect **another** account: the target integration refuses it and the current sign-in stays as it was |
| The account owner | renew their own login | reach anything else in Home Assistant |

A link is a random 256-bit value, compared in constant time, for one login, one successful use and at
most 60 days. One older than 30 days is replaced when another is sent or generated.

## What is stored, and where a link can be seen

- This integration never reads, stores or logs a token, a Client Secret or an authorization code.
  The target integration exchanges the code, as it always does.
- Pending links are kept in `.storage/reauth_link` (owner-only file permissions). Removing the
  integration deletes that file.
- Sending a notice is a Home Assistant action, and Home Assistant records each call as a
  `call_service` event. So **the link is in the recorder database and in your backups**, and in
  the log if you turn on `debug` for `homeassistant.core`. A proxy in front of Home Assistant may
  log the address of the way back, with the code and the state. After a renewal both are spent; after
  a refused attempt (another account, for instance) the state is still valid.
- The `reauth_link.generate_link` action, for administrators only, returns the links.

## Supported versions

Only the newest release, on Home Assistant 2026.9.3 or newer.
