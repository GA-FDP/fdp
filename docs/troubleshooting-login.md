# Troubleshooting `fdp login` and tokens

How access works, in one paragraph: `fdp login` runs the `pelican` client,
which signs you in to the DIII-D origin's web page with GitHub, asks the
origin's token issuer for a token, and stores the result in pelican's own
credential file (`~/.config/pelican/credentials/client-credentials.pem`).
The issuer grants read access to members of the **DIII-D GitHub
organization**. `fdp` copies the token to `~/.fdp/cache/<device>.token` and
exports it as `$BEARER_TOKEN` for whatever `fdp run` launches. Tokens last
24 hours; pelican keeps a refresh token so `fdp login` renews silently for
30 days of use before asking you to approve again.

Each entry below is a symptom you will actually see, in the order you are
likely to meet them.

## "Please enter the password you created to protect your local credentials"

**Cause.** Pelican's credential file is encrypted with a password someone
typed at a one-time "create a new password" prompt, usually mistaken for a
site password. It has nothing to do with any FDP token you were given. An
empty answer is treated as a wrong password.

**Fix.** Nothing in that file is worth keeping. Delete it, which needs no
password, and log in again:

    pelican credentials reset-local
    fdp login

At the "create a new password" prompt, press Enter to leave the new file
unprotected (pelican warns about this; that is fine).

## `{"error":"invalid_client","error_description":"Unknown client"}`

**Cause.** Pelican reuses the OAuth client it registered with the issuer the
first time you logged in. The DIII-D issuer was replaced on 2026-10-01, and
knows none of the clients registered before that.

**Fix.** `fdp` 0.10.2 and later clear the stale registration and retry by
themselves, as does the pelican client from 7.25.2 (both are in `fdp-core`
1.24.0 and later). On older versions, remove just that device's registration
and log in again:

    pelican credentials prefix delete /fdp-d3d
    fdp login

Do not delete the whole credential file for this; it also holds other
devices' logins.

## "acquired token not valid for /fdp-d3d (missing scope or namespace/base-path mismatch)"

The login itself succeeded, but the token came back without read scope and
the client refused it. Two causes, in order of likelihood:

**You are not in the DIII-D GitHub organization**, or you approved the login
with a different GitHub account than the one that is. Membership is checked
at https://github.com/orgs/DIII-D/people (visible to members); ask an org
owner to add you, then `fdp login` again.

**You were added, but your browser still holds the old sign-in.** When you
approve a login, the issuer reads your organizations from the sign-in
cookie the origin's web page set, not from GitHub. That cookie lasts 16
hours and carries the memberships as they were when you signed in. Clear
cookies for `fdp-d3d-origin.nationalresearchplatform.org`, or open the next
approval URL in a private window, or log out at
https://fdp-d3d-origin.nationalresearchplatform.org:8000/ first.

No credential reset is needed in either case: the client never stores a
token it has rejected.

## "This program must be run in a terminal to acquire a new token"

**Cause.** Pelican's refresh token has lapsed and it needs you to approve
again, but it will only do that when its output is a terminal. `fdp` before
0.3.1 piped that output.

**Fix.** Update `fdp`. Until then, run pelican yourself once, in a real
terminal, then `fdp login` works again:

    pelican credentials token get read pelican://osg-htc.org:443/fdp-d3d

## A job failed with 401 hours after it started

**Cause.** A process keeps the token it started with; nothing renews it
mid-run. `fdp login` returns the stored token for as long as it is valid,
however little time it has left, so a job started late in a token's life
gets only the remainder.

**Fix.** `fdp login` prints the time left. For a job longer than that, get a
full-lifetime token first; it costs one browser approval:

    fdp login --fresh

`fdp run` warns when the token in use has under four hours left
(`FDP_MIN_TOKEN_HOURS` changes the threshold; `0` silences it). Both need
`fdp` 0.11.0 or later.

## "$BEARER_TOKEN is set but its token is expired"

**Cause.** Either your shell exported a stale `BEARER_TOKEN`, or you have an
old token file at `~/.fdp/token`: importing `toksearch_d3d` copies that file
into `$BEARER_TOKEN` when the variable is unset, before `fdp` resolves a
token. `fdp` ignores the expired value and falls back to your login token,
so this is a warning, not a failure.

**Fix.** `unset BEARER_TOKEN`, and remove `~/.fdp/token` if you no longer
need it. Note that while that file holds a valid token, it is what `fdp run`
actually uses, ahead of your `fdp login` token.

## "not authorized for /fdp-d3d/archives...: Unable to locate ...; permission denied"

**Cause.** The origin says this for a request with no usable token; it reads
like a missing path but is an authentication failure. Usually an expired
token.

**Fix.** Check what you are holding (next entry), then `fdp login`.

## Which token am I actually using?

`fdp run` exports it, so from inside the environment:

    fdp run python -c 'import os,json,base64,time; t=os.environ["BEARER_TOKEN"]; p=t.split(".")[1]; d=json.loads(base64.urlsafe_b64decode(p+"="*(-len(p)%4))); print(d["iss"]); print(d.get("scope")); print("expires in %.1f h" % ((d["exp"]-time.time())/3600))'

A login token has an issuer ending in `/api/v1.0/issuer/ns/fdp-d3d` and
scope `storage.read:/`. An issuer at `t.nationalresearchplatform.org` is a
hand-issued token from `~/.fdp/token`.
