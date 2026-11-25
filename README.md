# Reauth Link for Spotify

A Home Assistant integration that lets **the owner of a Spotify account renew its sign-in from
their own phone**, with a link, before it expires. Nobody has to open Home Assistant, sit at the
same computer or type an address.

> **Beta. Not yet proven with the real Spotify service.**
> The tests run Home Assistant itself, with no network and a simulated Spotify. They cover the
> official Spotify integration (its real code) and a stand-in for SpotifyPlus, on Home Assistant
> 2026.9.3 and 2026.11.3.
> **Still unproven:** the real Spotify, the real SpotifyPlus, real phones, reverse proxies, and the
> Nabu Casa tunnel. Please [report](https://github.com/bpaludo/reauth-link-ha/issues) what you find,
> good or bad.

Not affiliated with, endorsed by or sponsored by Spotify.

## Why

Spotify sign-ins made through a developer app now expire **6 months** after the account owner
approves them. Renewing the short-lived access token does not extend that. When it expires, someone
has to sign in again, as the account owner, from inside Home Assistant. If you look after Home
Assistant for other people, that means their password or a visit, twice a year.

With this integration:

1. 30 days before the date, the owner gets a notification with a link, repeated every 7 days.
2. They tap it, sign in to Spotify if asked, and tap **Agree**.
3. Home Assistant finishes the renewal, checks that the integration came back, and shows "All set!".
   You get "renewed until ...".

If they do not, you are told 7 days before the sign-in expires.

## What it does not do

- It does not play music, and it does not replace the Spotify or the SpotifyPlus integration. It
  renews the login one of them already has.
- It does not include a Spotify app. You keep using **your own** Client ID and Client Secret.
- It does not read, store or send tokens or secrets anywhere.

## Requirements

- Home Assistant **2026.9.3** or newer. Before that, the official Spotify integration cannot sign in
  again ([core#182290](https://github.com/home-assistant/core/pull/182290)).
- The Spotify or SpotifyPlus integration, already set up and working.
- An **https** address for Home Assistant that the owner's phone can open: your own domain or Home
  Assistant Cloud (Nabu Casa). Spotify does not accept plain http.
- The My Home Assistant integration loaded (it is part of `default_config`).
- For the notifications: the Home Assistant app on the owner's phone. Without it, see
  [No app on the owner's phone](#no-app-on-the-owners-phone).

## Install

1. In the Marketplace (or HACS), add `https://github.com/bpaludo/reauth-link-ha` as a custom
   repository of type *Integration*, download it and restart Home Assistant. While there are only
   beta releases, it installs the `main` branch.
2. In the [Spotify developer dashboard](https://developer.spotify.com/dashboard), open your app and
   **add** this to its *Redirect URIs*, keeping the one already there:

   `https://YOUR-HOME-ASSISTANT/api/reauth_link/callback`

3. In Home Assistant: **Settings → Devices & services → Add integration → Reauth Link for Spotify**.
   Choose the address and the time of the notices, then the login, who is notified and how many days
   a sign-in lasts (180 by default).

Other logins go in through **Add login**. One address serves them all.

## Notifications

Both fields take `notify` **actions**, not entities. The usual one is
`notify.mobile_app_` followed by the name of the phone: the Home Assistant app creates it by itself
when someone signs in to your Home Assistant on that phone, so there is nothing to set up. Not in the
list? Sign in on that phone first. Notify *entities* do not work yet.

Home Assistant does not always know that a phone is gone: a notice to a phone without the app may count
as sent. The 7-day notice to the administrator covers that.

| Who | Is told |
|---|---|
| The owner | to renew, 30 days before and every 7 days, with the link; and when it is done |
| The administrator | when it is done; 7 days before the date, if not renewed; when no notice to the owner could be sent; when the integration refused the new sign-in or has not come back after it; when a link was turned off |

### No app on the owner's phone

Leave the owner empty and set yourself as the administrator. Then **nobody is sent the link**: you are
told when it is time, once a week, and the Send link button says that no one was chosen. The
**Generate links** action (`reauth_link.generate_link`, administrators only) returns the link, to
send through any chat. It works once, for up to 60 days. A link older than 30 days is replaced when a new
one is sent or generated.

## What you get

For each login, a device with:

- **Login status**, a sensor (see below);
- **Login expires**, a sensor with the date;
- **Renewal due**, a binary sensor (problem) that turns on 30 days before the date;
- **Send link**, a button that sends the link right away.

And the event `reauth_link_finished`, with `domain`, `config_entry_id` and `result`.

### Login status

The first line that applies, from the top:

| Status | State | When |
|---|---|---|
| | `unavailable` | the login was removed from Home Assistant, or disabled |
| Received (waiting for the integration) | `received` | the owner approved, and the integration has not come back yet |
| Integration down | `integration_down` | the owner approved, and a day later the integration still has not come back |
| Refused | `rejected` | the owner approved, but the integration asks to sign in again, as when a permission is missing |
| May have expired | `maybe_expired` | the date has passed |
| Integration down | `integration_down` | the integration failed to start; or, for a minute or two, it has not been loaded, or has been asking to sign in again |
| Date unknown | `date_unknown` | the integration is older than Home Assistant keeping that date; the first renewal fixes it |
| Time to renew | `renewal_due` | 30 days or less |
| Up to date | `up_to_date` | none of the above |

**It is an estimate.** This integration never reads the token: it goes by the date of the last sign-in it
knows of. "Up to date" does not prove that Spotify still accepts the login, and a renewal made by hand on
the Home Assistant screen is not noticed.

### Card example

Only cards that come with Home Assistant. Replace `ACCOUNT` with your account, as it shows in the entity
IDs. For SpotifyPlus they start with `spotifyplus_login_`. If Home Assistant was in Brazilian Portuguese
when the login was added, the IDs are in Portuguese: pick the four entities of the login's device in the
card editor.

```yaml
type: entities
title: Spotify login
entities:
  - entity: sensor.spotify_login_ACCOUNT_login_status
    name: Status
  - entity: sensor.spotify_login_ACCOUNT_login_expires
    name: Expires
    format: date
  - entity: binary_sensor.spotify_login_ACCOUNT_renewal_due
    name: Renewal due
  - entity: button.spotify_login_ACCOUNT_send_link
    name: Send the link to the owner
```

The button sends the notice as soon as it is pressed.

### Repairs

In **Settings → System → Repairs**, one for each problem of each login:

| Repair | Shows up | Goes away |
|---|---|---|
| The login expires, or may have expired, on ... | 7 days before the date, if not renewed. Not if the date is unknown | when it is renewed by link |
| The renewal was refused | when the integration asks to sign in again after the owner approved | when it is renewed, or signed in again by hand |
| Not back since the renewal | a day after the owner approved, if the integration has not loaded | when it loads, or for a day more when another link is approved |
| Not in Reauth Link | when there is a Spotify or SpotifyPlus login that was not added here | when it is added |

All of them also go away when the login, or this integration, is removed or disabled. If you ignore one, it
stays ignored until that problem is over, or until the login or this integration is disabled.

### Diagnostics

**Download diagnostics**, in the menu of the integration, gives dates, counts and states to attach to a
report. This integration puts no link, account, address or name of a notify action in it. Home Assistant
adds its own part, such as versions, the time zone and the list of your custom integrations.

## The public address: what it does and does not do

`GET /api/reauth_link/callback` is open to the internet, like Home Assistant's own
`/auth/external/callback`. It has to be: Spotify sends the owner's browser there.

**It does**, only with a valid link of yours: take the code Spotify issued, hand it to the sign-in
flow of the Spotify integration, and show a page with the result.

**It does not:** start anything without a valid link, accept another Spotify account, show the name
of the integration or of the account to strangers, log in to Home Assistant, or accept the same link
twice.

Where a link can be seen, and how to report a problem: [SECURITY.md](SECURITY.md).

## When it says no

The reason is shown where you add the login, and told to the administrator if it happens later.

| Message about | Usual cause |
|---|---|
| reauthentication failed inside Home Assistant | Home Assistant older than 2026.9.3, or an outdated integration |
| reauthentication refused | the integration did not open its sign-in; the reason is in the message |
| application credentials | the Client ID and Client Secret were removed from Home Assistant |
| PKCE, or no plain authorization page | that kind of sign-in cannot be renewed by link |
| My Home Assistant | the `my` integration is not loaded |

## Limits

- The date counts from when the integration entry was created and from each renewal made **by this
  integration**. A renewal made by hand on the Home Assistant screen is not noticed: it will just ask
  earlier than needed, and its repair has to be ignored.
- Spotify always shows its approval page. There is no renewal without the owner.
- Changing the address invalidates the links already sent, and the new one must be registered in the
  Spotify app.
- Taking a login off the list and adding it again forgets its renewals: the date goes back to when the
  integration entry was created.
- Texts: English and Brazilian Portuguese. Other languages get English.

## Support

This is a spare-time project, shared as it is. There is **no promise of fixes or response times**.
It is tested against each monthly Home Assistant release when possible.

## License

[MIT](LICENSE). Spotify is a trademark of Spotify AB; this project only refers to it.
