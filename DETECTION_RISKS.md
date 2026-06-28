# Detection Risks — GitHub Actions vs Local

The browser automation code is identical between local and GHA runs, but there are differences that could flag traffic as non-human.

## Potential Detection Points

**1. Timing regularity**
GHA runs fire at exact scheduled times (e.g. every hour on the dot). Real users don't behave that way. Adding a random startup delay in the workflow reduces this signal.

**2. No persistent cookies/storage**
Every GHA run starts with a clean browser profile. A returning real user would have cookies, localStorage, and browser history. Local runs have the same issue.

**3. Concurrency pattern**
10 instances starting within seconds of each other from different IPs but all hitting the same locker URLs looks unnatural. Staggering start times helps.

**4. GHA runner IP leak**
GitHub's runner IPs are publicly known. If the target site checks the origin IP before the proxy connection is established (e.g. during DNS), the runner IP could leak. The proxy should cover this fully once connected.

**5. No real dwell time**
The automation completes tasks faster than a human would read and interact with the page.

## What Works in Our Favour

- Evomi residential proxy IPs — look like real users from real countries
- Random device fingerprints per run (53 profiles across iPhone and Android)
- Playwright with a real browser engine (not detectable as a headless bot)
- Country-targeted proxies (high-CPM countries = high-quality residential IPs)
- 60/40 mix of high-CPM and any-country proxies for traffic diversity
