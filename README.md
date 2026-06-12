# Better OSRS Newspost

Mobile-friendly mirror of the latest 3 Old School RuneScape newsposts. A GitHub
Actions cron job rebuilds the site every 3 hours, so a fresh post is ready by
the time you wake up. You bookmark one URL on your phone; the newest post is
always the homepage, with the two previous posts one tap away.

When a brand-new post appears, it pushes a notification to
[`ntfy.sh/OSRSNewsPost`](https://ntfy.sh/OSRSNewsPost) with a direct link — so
you find out the moment the mirror updates instead of having to check.

## How it works

- **`scraper.py build`** reads the official RSS feed, downloads the 3 newest
  posts, extracts the article content, rewrites all URLs to absolute, and
  renders each post into a responsive template (`site/`). It compares the newest
  post against `state.json`; if it changed, it queues a notification
  (`.notify.json`).
- **`scraper.py notify`** sends any queued notification to ntfy. It runs *after*
  deploy in CI, so the link points at an already-live page.
- **`.github/workflows/build-and-deploy.yml`** runs the scraper on a schedule,
  publishes `site/` to GitHub Pages, commits the updated `state.json`, and fires
  the notification.

### New-post detection

`state.json` (committed to the repo) stores the GUID of the most recent post.
Each run compares the newest feed item against it:

- **Different GUID** → new post → notify, then record the new GUID.
- **Same GUID** → nothing to do.
- **First run** (no `state.json`) → record the GUID but *don't* notify, so
  setup doesn't spam you. Set `NOTIFY_ON_FIRST_RUN=1` to notify on first run too.

CI commits `state.json` back to the repo with the built-in `GITHUB_TOKEN`, which
by design does **not** re-trigger the workflow — no infinite loop. The push is
also `paths-ignore`d for `state.json` as a belt-and-suspenders guard.

> Note: if a deploy fails after a new post is detected, that one notification is
> skipped (the state is already recorded, so the next run won't re-detect it).
> Deploys rarely fail; this is an accepted trade-off for keeping the state logic
> simple.

## One-time setup (~5 minutes)

1. Create a new **public** GitHub repo (e.g. `better-osrs-newspost`). Public
   repos get unlimited free Actions minutes; Pages also requires public on the
   free plan.
2. Push this folder to it:

   ```sh
   cd "Better OSRS Newspost"
   git init && git add scraper.py requirements.txt README.md .gitignore .github
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin git@github.com:YOUR_USERNAME/better-osrs-newspost.git
   git push -u origin main
   ```

3. In the repo: **Settings → Pages → Build and deployment → Source: GitHub
   Actions**.
4. Go to **Actions**, pick "Build and deploy newspost site", click **Run
   workflow** (the push in step 2 likely already triggered it).
5. Bookmark `https://YOUR_USERNAME.github.io/better-osrs-newspost/` on your
   phone. On iOS/Android you can "Add to Home Screen" to make it feel like an app.
6. Subscribe to the notification: install the **ntfy** app and subscribe to the
   topic `OSRSNewsPost` (or open `https://ntfy.sh/OSRSNewsPost` in a browser).

That's it — no server, no cost. The cron (`0 */3 * * *`) keeps it within a few
hours of any new post. Note GitHub may delay scheduled runs slightly during peak
load, and disables cron on repos with no commits for 60 days — the automatic
`state.json` commits keep it alive whenever a post changes; a single trivial
commit re-enables it otherwise.

## Local test

On a normal machine:

```sh
pip install -r requirements.txt
python scraper.py build
open site/index.html
```

To test the notification too (sends a real ntfy message):

```sh
python scraper.py notify
```

On NixOS:

```sh
nix-shell -p 'python3.withPackages(ps: with ps; [requests feedparser beautifulsoup4])' \
  --run "python scraper.py build && xdg-open site/index.html"
```

## Tweaks

- **Number of posts:** `NUM_POSTS` in `scraper.py` (or the env var of the same
  name; the workflow sets it to `3`).
- **Schedule:** the `cron:` line in the workflow (times are UTC).
- **ntfy topic:** `NTFY_TOPIC` env var (default `OSRSNewsPost`). Use a private
  server with `NTFY_BASE`.
- **Notification link:** points at the deployed mirror (`SITE_URL`, set
  automatically in CI from the Pages deploy URL); falls back to the original
  RuneScape post when `SITE_URL` is unset (e.g. local runs).
- **Colors/typography:** the `<style>` block in `PAGE_TEMPLATE`.

---

All newspost content is © Jagex Ltd; this is a personal-use reformatter that
links back to the original posts.
