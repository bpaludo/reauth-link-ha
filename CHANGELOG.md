# Changelog

## 0.4.0b1

Beta. **Not yet proven with the real Spotify service** (see the README).

- New sensor, **Login status**: up to date, time to renew, received, refused, may have expired, date unknown
  or integration down. It is an estimate by date: the token is never read.
- **Repairs**: 7 days or less without a renewal, a renewal that was refused or whose integration did not come
  back within a day, and a Spotify login that is not in the list. They go away when the problem is over; after
  a renewal made by hand on the Home Assistant screen, ignore the one about the date.
- **Diagnostics**, with no link, account, address or name of a notify action.
- A card example in the README, with the cards that come with Home Assistant.
- The screens say that both fields take notify *actions*, where they come from, and what happens without an
  owner.
- A renewal that the integration only refuses after the page answered "Received" is noticed, and you are told.
- A damaged date in the saved file no longer stops the integration.
- Upgrading from 0.3.0b1 keeps the logins, the links already sent, the dates and the entities. Restart Home
  Assistant after the update.

## 0.3.0b1

First public beta. **Not yet proven with the real Spotify service** (see the README).

- Sends the account owner a link before a Spotify sign-in expires. They approve on their own
  phone, and Home Assistant finishes by itself.
- Works with the official Spotify integration and with SpotifyPlus.
- Several logins, each with its own owner, administrator and number of days.
- Suggests your own domain or Home Assistant Cloud (Nabu Casa) as the address; refuses plain http.
- English and Brazilian Portuguese.
