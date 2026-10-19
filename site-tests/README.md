# Project website

The buildless marketing site is in `site/`. GitHub Pages serves it at
https://ifarra.github.io/HoloQA/. The Project website workflow tests pull requests
and deploys changes on main with GitHub's Pages artifact and deployment actions.

Run the browser checks from the repository root:

```sh
npm ci --prefix desktop
cd desktop && npx playwright install chromium && cd ..
node site-tests/smoke.mjs
```

Set `CHROMIUM_EXECUTABLE` to use an installed Chromium, or
`HOLOQA_PAGES_SCREENSHOTS` to save full-page desktop and mobile screenshots.
The tests serve the site under `/HoloQA/` to exercise GitHub Pages asset paths.
They cover responsive layouts, keyboard tabs, preview dialog, mobile navigation,
clipboard, FAQ, release updates, invalid download URLs, rate limits, and the
no-JavaScript download fallback.

The public GitHub Releases API enhances the verified installer link. If it is
unavailable, the shipped link remains usable. When shipping a desktop release,
update the static fallback URLs and version labels in `site/index.html` so that
visitors without JavaScript also get the latest verified installer. Screenshots
are actual example-project desktop views; the workflow tour is illustrative.
