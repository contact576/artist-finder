# Private Vercel hosting

This repository's hosted surface is intended to remain private. The Vercel
configuration sends every request through `api/index.py`; the generated
dashboard files and data are bundled for that function rather than exposed as
direct public static files. Do not deploy until that function exists and has
been reviewed.

## One-time setup

1. Authenticate locally with `vercel login`, then link this checkout with
   `vercel link`. Choose the intended Vercel account/team and project; do not
   paste tokens into chat or commit them.
2. Generate login material without saving a plaintext password:

   ```text
   python tools/hosted_auth_setup.py --vercel --operator-token
   ```

   Enter the password twice, then paste the repository-scoped GitHub token at
   the hidden prompts. The helper sends the password hash, separate session and
   CSRF secrets, and operator token directly to the linked Vercel Production
   environment over stdin. It prints only variable names and never writes or
   displays the password, token, or generated values.

3. Keep the application login enabled even when Vercel deployment protection is
   also available. An unlisted production URL is not a private access policy.

4. Create a fine-grained GitHub token restricted to `contact576/artist-finder`,
   with Contents read/write and Actions read/write only. Add these variables to
   Vercel Production (and Preview only when intentionally testing private data):

   - `HOSTED_AUTH_PASSWORD_HASH`
   - `HOSTED_AUTH_SESSION_SECRET`
   - `HOSTED_AUTH_CSRF_SECRET`
   - `GITHUB_OPERATOR_TOKEN`
   - `GITHUB_REPOSITORY=contact576/artist-finder`
   - `GITHUB_DATA_REF=main`

   The GitHub token lets the authenticated operator API commit validated roster
   edits and dispatch the monthly workflow. Do not reuse a broad personal CLI
   token, and never expose the token to browser JavaScript.

## Build and monthly refresh

The Vercel build command is:

```text
python tools/build_dashboard.py --mode live --output out/dashboard
```

The GitHub Actions workflow runs on the first day of each month at 10:00 IST
(04:30 UTC), with only one refresh in flight. A manual dashboard refresh
dispatches that same workflow rather than doing Google Ads work in a web request.

Store only these Google Ads values in GitHub Actions encrypted secrets:

- `GOOGLE_ADS_DEVELOPER_TOKEN`
- `GOOGLE_ADS_CLIENT_ID`
- `GOOGLE_ADS_CLIENT_SECRET`
- `GOOGLE_ADS_REFRESH_TOKEN`
- `GOOGLE_ADS_LOGIN_CUSTOMER_ID`
- `GOOGLE_ADS_CUSTOMER_ID`
- `GOOGLE_ADS_API_VERSION` (optional; omit to use the reviewed default)

The `HOSTED_AUTH_*` and `GITHUB_OPERATOR_*` values belong in Vercel, not
GitHub Actions. Google credentials are workflow runtime secrets only. Never put
`data/config.json`, a refresh token, or any credential in git or build output.

Set Vercel's production branch to `main` with the private Git integration.
Each successful workflow data commit then triggers a reviewed redeployment.

## Operator flow

An operator can add an artist, edit its category, or add/replace its one
measurement keyword. The intended lifecycle is:

```text
edit in the private dashboard
  -> validate and persist through the hosted API
  -> commit the reviewed data change
  -> run or wait for the monthly refresh
  -> build and deploy the reviewed revision
```

An operator edit is a candidate until its identity evidence is reviewed. A
keyword idea is discovery evidence, not an artist identity. USA and Canada are
stored and calculated separately; never sum US and CA search volume.

## Recovery and rollback

If a refresh fails, keep the last successful dashboard and inspect the
non-secret job log/status. Do not erase retained measurements or rewrite an
immutable monthly run. Correct the cause, rerun the guarded refresh, and deploy
only after the generated artifact and secret scan pass.

For a bad deployment, use the Vercel project dashboard to promote the last
known-good deployment. If data was edited incorrectly, restore the reviewed
data commit through normal git history, rebuild, and redeploy. Rotate all three
`HOSTED_AUTH_*` values if an auth value may have been exposed; rotate Google
credentials through their provider rather than committing replacements.

## Safety boundaries

- This is a private research dashboard, not a ticket forecast.
- Search volume is Google-estimated demand, not ticket sales or audience size.
- US and CA are never added together; show the stronger labelled geography when
  a representative North America figure is needed.
- Never publish `out/dashboard` directly or expose raw data/configuration files.
