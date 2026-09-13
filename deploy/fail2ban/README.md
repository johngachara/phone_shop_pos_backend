# fail2ban for the Alltech API

## What this protects

Sign-in itself happens at Supabase, not here, so there is no password endpoint
on this origin to brute-force. What is exposed and worth protecting:

- `/api/celery-token/` — exchanges a shared API key for a machine token. The
  only secret-guessing target this service has.
- `/api/users/` — manager-only user administration.
- Repeated `401`/`403` anywhere, which is what credential-stuffing looks like
  when the tokens are wrong.

Cloudflare Turnstile guards the Supabase sign-in form; this guards the origin.

## The Cloudflare problem — read before installing

The origin sits behind Cloudflare, so **every request arrives from a Cloudflare
IP address**. A normal fail2ban jail bans the address it sees, which here means
banning Cloudflare — taking the site down for everyone while the actual attacker
is unaffected.

Two things are therefore required, and the jail is useless without both:

1. **nginx must restore the real client IP.** `set_real_ip_from` for each
   Cloudflare range plus `real_ip_header CF-Connecting-IP`, so the access log
   records the client rather than the proxy. Cloudflare publishes the ranges at
   https://www.cloudflare.com/ips/ and they change; refresh them periodically.
   nginx configuration is managed on the server and is deliberately not shipped
   from this repo.

2. **The ban must be applied at Cloudflare, not in iptables.** Blocking a client
   IP locally does nothing when the packets still arrive from Cloudflare. The
   jail below uses fail2ban's `cloudflare` action, which calls the Cloudflare API
   to block the address at the edge.

`banaction = cloudflare` needs Cloudflare credentials in
`/etc/fail2ban/action.d/cloudflare.local`:

    [Init]
    cfuser = <cloudflare account email>
    cftoken = <API token with Zone:Firewall Services:Edit>

Use a scoped API token, not the Global API Key.

## Install

    sudo cp filter.d/alltech-api.conf /etc/fail2ban/filter.d/
    sudo cp jail.d/alltech-api.conf   /etc/fail2ban/jail.d/
    sudo fail2ban-client reload

## Verify — do not assume it works

    # The regex matches real log lines:
    sudo fail2ban-client -d | grep alltech
    sudo fail2ban-regex /var/log/nginx/access.log \
         /etc/fail2ban/filter.d/alltech-api.conf --print-all-matched

    # The jail is live:
    sudo fail2ban-client status alltech-api

If `fail2ban-regex` reports 0 matches, the filter does not match this server's
log format and the jail is decorative. Check `log_format` in nginx first.
