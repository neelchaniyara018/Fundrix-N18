# Deploying Fundrix N18 for free (GitHub + Cloudflare Pages + Supabase)

```
 AMFI (NAV files) --daily--> GitHub Actions (Python) --> static JSON  --> Cloudflare Pages  <-- visitors' browsers
                                   |                                                          |
                                   +--> `data` branch (saved NAV history)       Supabase: logins, saved screens, watchlists
```
Visitors only download static files from Cloudflare. Supabase is touched only by logged-in users (login, saved screens, watchlist).

## 0. What you need (all free)
GitHub account · Cloudflare account · your existing Supabase project (accounts already set up per `supabase/README.md`).

## 1. Supabase: add the new tables (2 minutes)
SQL Editor -> paste the **whole** `supabase/schema.sql` -> Run. It is safe to re-run; it adds `screens`, `watchlist` and the share function.

## 2. Put the code on GitHub
Create a **public** repository (public repos get free Actions minutes) and push this folder to the `main` branch.
Never commit secrets: only the public Supabase URL and `anon` key ever go in the frontend.

## 3. Cloudflare: token + account id
1. Cloudflare dashboard -> right sidebar shows your **Account ID** (copy it).
2. My Profile -> API Tokens -> Create Token -> *Custom token* -> permission **Account / Cloudflare Pages / Edit** -> create, copy the token.

## 4. GitHub: secrets and variables (repo -> Settings -> Secrets and variables -> Actions)
| Type | Name | Value |
|---|---|---|
| Secret | `CLOUDFLARE_API_TOKEN` | token from step 3 |
| Secret | `CLOUDFLARE_ACCOUNT_ID` | account id from step 3 |
| Variable | `SUPABASE_URL` | e.g. `https://abcd1234.supabase.co` |
| Variable | `SUPABASE_ANON_KEY` | the *anon public* key |
| Variable | `TURNSTILE_SITE_KEY` | optional (free CAPTCHA site key) |
| Variable | `REQUIRE_LOGIN` | optional; default on. Set to `false` to open the site without the sign-in wall |
| Variable | `LOGIN_WALL_SECONDS` | optional; default `30` (free look before the sign-in pop-up) |
| Variable | `CF_PAGES_PROJECT` | optional; default `fundrix-n18` (becomes `fundrix-n18.pages.dev`) |

## 5. First deploy
The site only ever publishes **real** AMFI data, so go straight to step 6: nothing is deployed until real data exists (a push before that fails with a clear message, and nothing is published).
Want to test the site, logins and saved screens first? Add the repository variable `ALLOW_DEMO_DATA` = `true`, push, and a **demo** dataset is published with a visible "Demo dataset" banner at the top. Remove the variable afterwards. With real data the banner never appears.

## 6. Load real data (one time) 
Actions -> *Update data and deploy* -> **Run workflow** -> `backfill_weeks` = `530`.
This downloads today's AMFI NAV file plus ~10 years of weekly history (about 530 small requests, roughly 10-40 minutes) and publishes real data.
From then on the workflow runs by itself every morning at 07:00 IST with `backfill_weeks` = 0 (only today's file is needed).

**If step 6 fails** with a download error, AMFI may be refusing GitHub's servers, or its format changed. The workflow stops without publishing anything, so the site keeps its previous data. Fallback: run the same command on your own computer (a normal home connection usually works):
```bash
pip install numpy
python -m pipeline.build --mode real --state state --out frontend/data --backfill-weeks 530
# then publish state so GitHub can continue daily:
cd state && git init -b data && git add state.json.gz && git commit -m state && git remote add origin <your repo url> && git push -f origin data
```
and re-run the workflow with backfill `0`. (The `data` branch must contain `state.json.gz` at its root.)

## 7. Supabase settings that depend on your site address
* Auth -> URL configuration: **Site URL** = `https://<project>.pages.dev`.
* Edge Function secret `ALLOWED_ORIGIN` = the same origin (`supabase secrets set ALLOWED_ORIGIN=https://<project>.pages.dev`), then redeploy the gateway.
* Optional Turnstile: add `TURNSTILE_SECRET` as an Edge Function secret and `TURNSTILE_SITE_KEY` as the GitHub variable.

## 8. Optional: AUM, expense ratio, exit load, riskometer
NAV files do not contain them. Put what you have in `data/extras.csv` (see `data/extras.example.csv`; keyed by AMFI scheme code), commit, and the next run merges it.
Until then those columns are simply blank and the default screen hides the AUM / expense filters.

## 9. Custom domain (optional, free)
Cloudflare Pages project -> Custom domains. A domain name itself is not free; `*.pages.dev` is.

## Compulsory sign-in (how it behaves)
* A visitor gets `LOGIN_WALL_SECONDS` (30) of free browsing. Then a pop-up in the middle of the screen asks them to log in or sign up; it has no close button, ignores Esc and clicks outside, and the site behind it is blurred and inactive. Reloading does not restart the clock (per browser tab session).
* Before the wall, guests can browse, sort, search, open fund pages and use the calculator, but **cannot edit filters, save screens, open saved screens/presets/shared links, compare funds or use the watchlist**: those show a "sign in to ..." prompt. After signing in they land on the page they were trying to reach.
* Signed-in users are never locked. Logging out after the free look brings the wall back at once.
* The gate only works when Supabase is configured (otherwise nobody could sign in, so it stays off), and it also stays off locally unless you fill `frontend/config.js`.
* **Limits you should know:** this is a front-end gate. The fund data are static files that anyone can download directly (`/data/funds.json`), and a technical visitor can remove the pop-up in their browser's developer tools. What *is* really protected is each user's own saved screens and watchlist (Supabase Row Level Security). If the fund data itself must be private, it would have to be served from behind authentication, which is not possible on free static hosting.
* If Supabase is down or paused, the wall cannot be passed: nobody can sign in until it is restored.

## Keeping it alive
* GitHub disables scheduled workflows after ~60 days with no repository activity: open the Actions tab now and then, or push any small commit.
* Supabase free projects pause after a week of *no activity*; real users logging in keep it awake. If it pauses, restore it from the dashboard.
* Watch the Actions tab: a red run means nothing new was published (the site keeps serving yesterday's data).

## Limits and honesty
* NAVs change once per business day, so "live" = refreshed every morning, like the reference product.
* Cloudflare Pages free limits (files per deployment, file size) should be fine here (~300 files, a few MB) but check current limits in Cloudflare's docs.
* The AMFI download URLs and file layout were written from documentation and could not be tested from the build environment; the first real run is the real test.
* Returns come from weekly NAV samples interpolated to the exact date, so they can differ slightly (typically well under a percentage point for calm funds, more for very volatile ones) from figures computed on daily NAVs.
* This is information, not investment advice, and you are responsible for the legal and privacy side of running a public site (see the notice in `frontend/auth.js`).
