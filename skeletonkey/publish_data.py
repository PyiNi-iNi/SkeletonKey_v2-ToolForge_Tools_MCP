"""Knowledge bases for the ``pub.*`` publishing tools.

Static, data-driven content: real console/docs URLs, concise steps, the
credential kinds each platform expects, and the ``{{PUB.<id>}}`` placeholder
conventions. This module is the single source of truth — the tools
(``pub.platforms``, ``pub.payments``, ``pub.packaging``) only surface it, so
the agent never carries URLs from memory.

Everything here is stdlib (pure data) and every entry is small enough to fit
in one tool result. Steps are deliberately terse: the agent reads them once
and acts, they are not a tutorial.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Publishing platforms
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict[str, Any]] = {
    "google_play": {
        "name": "Google Play",
        "console": "https://play.google.com/console",
        "docs": "https://developer.android.com/distribute",
        "account": "https://play.google.com/console/about/developers",
        "cost": "one-time US$25 developer registration",
        "steps": [
            "Register as a Google Play developer (one-time US$25) at the account URL.",
            "Create an app in Play Console; the app id (e.g. com.example.app) is fixed forever.",
            "Build an Android App Bundle (.aab) signed with an upload key; let Play manage the app signing key.",
            "Upload the bundle to a production release (staged rollout is the safe default).",
            "Fill the store listing (title, short/full description, screenshots, content rating questionnaire).",
        ],
        "credentials": [
            {"id": "google_play.token", "kind": "oauth_token",
             "how": "Service-account OAuth token or Play Console API key; never paste into the repo"},
        ],
        "placeholders_example": "play_release.aab  # built artifact\nconsole: {{PUB.google_play.token}}\n",
        "notes": "Listing assets are managed in the console, not files. A new app id can never be changed — get it right first.",
    },
    "apple_appstore": {
        "name": "Apple App Store",
        "console": "https://appstoreconnect.apple.com",
        "docs": "https://developer.apple.com/documentation/app-store",
        "account": "https://developer.apple.com/programs/enroll/",
        "cost": "US$99/year Apple Developer Program",
        "steps": [
            "Enroll in the Apple Developer Program (US$99/year) and accept the agreements.",
            "In App Store Connect: create the app record (bundle id must exist in the developer portal).",
            "Build with Xcode (or xcodebuild), archive, and upload via Transporter / Xcode Organizer.",
            "Provide the app icon set, screenshots per device class, privacy-nutrition label, and age rating.",
            "Submit for review; fix reject notes by re-uploading a new build (version numbers are immutable).",
        ],
        "credentials": [
            {"id": "apple.app_id", "kind": "api_key",
             "how": "App Store Connect API key (Issuer + .p8 private key) for App Store Server API"},
            {"id": "apple.team_id", "kind": "api_key", "how": "Developer team id (not a secret, but store it)"},
        ],
        "placeholders_example": "AppStoreConnect API: issuer {{PUB.apple.issuer_id}} key {{PUB.apple.app_id}}\n",
        "notes": "Bundle id and version numbers are immutable once live. TestFlight builds use the same pipeline with a TestFlight group instead of release.",
    },
    "github": {
        "name": "GitHub (repo, releases, packages)",
        "console": "https://github.com",
        "docs": "https://docs.github.com",
        "account": "https://github.com/settings/tokens",
        "cost": "free for public; free tiers for GitHub Packages",
        "steps": [
            "Create or push the repository; pin CODEOWNERS and branch protection for the release branch.",
            "Create a fine-grained personal access token scoped to the repo (or an environment secret for Actions).",
            "Cut a version tag (e.g. v1.2.3) and publish a GitHub Release with the artifacts attached.",
            "For packages: set up GitHub Packages (npm/containers/dotnet) and set the registry auth token.",
            "Optional: Pages for docs, Discussions for user Q&A, GitHub Sponsors for funding.",
        ],
        "credentials": [
            {"id": "github.token", "kind": "token",
             "how": "Fine-grained PAT with repo + packages:read/write on the specific repo only"},
        ],
        "placeholders_example": ".npmrc: //registry.npmjs.org/:_authToken={{PUB.github.token}}\n",
        "notes": "Prefer fine-grained tokens over classic ones; scope to the minimum repos and expire them.",
    },
    "pypi": {
        "name": "PyPI (Python package index)",
        "console": "https://pypi.org",
        "docs": "https://docs.pypi.org",
        "account": "https://pypi.org/manage/account",
        "cost": "free",
        "steps": [
            "Create a PyPI account; use an API token (Settings - API tokens), never the password.",
            "Build the package: python -m build (produces wheel + sdist in dist/).",
            "Check with twine check dist/*, then upload: twine upload dist/*.",
            "For CI: use PyPI trusted publisher (OIDC) so no long-lived token exists at all — see docs/trusted-publishers.",
            "Project names are globally unique and hard to reclaim — verify the name is free before building around it.",
        ],
        "credentials":
        [
            {"id": "pypi.token", "kind": "token", "how": "PyPI API token (pypi-pypi-…); store, never commit"},
            {"id": "pypi.username", "kind": "other", "how": "Account name (twine prompts; can be __token__ with a token upload)"},
        ],
        "placeholders_example": "twine upload --username __token__ --password {{PUB.pypi.token}} dist/*\n",
        "notes": "Versions on PyPI are immutable: a published version cannot be overwritten (withdraw, don't replace).",
    },
    "npm": {
        "name": "npm (JavaScript package registry)",
        "console": "https://www.npmjs.com",
        "docs": "https://docs.npmjs.com",
        "account": "https://www.npmjs.com/settings/tokens",
        "cost": "free",
        "steps": [
            "Create an npm account and an access token (classic publish or granular, repo-scoped).",
            "Set 'name' and 'version' in package.json; run npm pack to sanity-check the tarball.",
            "Publish: npm publish (uses the token from .npmrc or the login session).",
            "For CI: use npm tokens as secrets; granular tokens scoped to the package are the safe default.",
        ],
        "credentials": [
            {"id": "npm.token", "kind": "token", "how": "npm access token (granular, per-package)"},
        ],
        "placeholders_example": ".npmrc: //registry.npmjs.org/:_authToken={{PUB.npm.token}}\n",
        "notes": "Package names are reserved and immutable; a deleted package name can be re-claimed by anyone after a delay.",
    },
    "custom": {
        "name": "Custom / self-hosted (own domain or cloud bucket)",
        "console": "your own domain / cloud console",
        "docs": "https://developer.mozilla.org/en-US/docs/Web/Security",
        "account": "your hosting provider's console",
        "cost": "varies",
        "steps": [
            "Decide the artifact: installer file, zip, deb/rpm, Docker image, or a static site.",
            "Provision storage with a CDN (S3 + CloudFront, Cloudflare R2, GCS + CDN) or a simple web host.",
            "Serve over HTTPS only; add Content-Digest / sha256 sidecar file for checksum verification.",
            "Write a one-page redirector/install script users can run (winget/scoop/choco manifest or curl|bash — be honest about the latter).",
            "Keep a signed changelog; sign installers (EV code-signing cert) where the platform checks signatures.",
        ],
        "credentials": [
            {"id": "custom.bucket_key", "kind": "api_key", "how": "Object-storage write key scoped to the release bucket only"},
            {"id": "custom.domain", "kind": "other", "how": "The release base URL (not a secret)"},
        ],
        "placeholders_example": "release base: {{PUB.custom.domain}}/v1.2.3/\nstorage key: {{PUB.custom.bucket_key}}\n",
        "notes": "Self-hosting shifts the trust problem onto you: checksums + TLS + (ideally) signature are what stand in for a store's review.",
    },
}

# ---------------------------------------------------------------------------
# Payment providers (in-app / subscription billing)
# ---------------------------------------------------------------------------

PAYMENTS: dict[str, dict[str, Any]] = {
    "stripe": {
        "name": "Stripe",
        "console": "https://dashboard.stripe.com",
        "docs": "https://docs.stripe.com/payments",
        "setup": "https://docs.stripe.com/get-started/checkouts",
        "steps": [
            "Create a Stripe account; stay in Test mode while integrating (test cards: 4242 4242 4242 4242).",
            "For one-time or subscription checkout: use Stripe Checkout (hosted page) — least code, PCI SAQ-A.",
            "Create a Product + Price (or Price Table) for each offer; prices live in the dashboard, not code.",
            "Add a webhook endpoint (checkout.session.completed, customer.subscription.*) and verify signatures.",
            "Go live: switch the publishable/secret keys, re-test, then enable real payments.",
        ],
        "credentials": [
            {"id": "stripe.publishable_key", "kind": "api_key", "how": "pk_… key — safe in client code, store anyway for consistency"},
            {"id": "stripe.secret_key", "kind": "api_key", "how": "sk_test_… / sk_live_… — server-side only, never in client code"},
            {"id": "stripe.webhook_secret", "kind": "webhook", "how": "whsec_… for signature verification"},
        ],
        "placeholders_example": "server .env: STRIPE_SECRET={{PUB.stripe.secret_key}} STRIPE_WEBHOOK={{PUB.stripe.webhook_secret}}\n",
        "notes": "Secret keys are bearer credentials: anyone with sk_live_… can move money. Store, don't commit, don't log.",
    },
    "paddle": {
        "name": "Paddle (Merchant of Record)",
        "console": "https://vendors.paddle.com",
        "docs": "https://developer.paddle.com",
        "setup": "https://developer.paddle.com/document/integration-overview",
        "steps": [
            "Sign up as a Paddle vendor; Paddle becomes merchant of record (they handle global sales tax/VAT).",
            "Create Products and Transactional Products; note Paddle's fees are per-transaction, not a % of MRR only.",
            "Use Paddle.js (client) + the API/SDK (server) with test API keys first.",
            "Subscribe to webhooks (transaction.events) and verify signatures with your webhook secret.",
            "Submit products for review if Paddle requires it for your category, then go live.",
        ],
        "credentials": [
            {"id": "paddle.api_key", "kind": "api_key", "how": "Paddle API token (test, then live)"},
            {"id": "paddle.webhook_secret", "kind": "webhook", "how": "Webhook signature secret"},
        ],
        "placeholders_example": "server .env: PADDLE_API_KEY={{PUB.paddle.api_key}}\n",
        "notes": "Merchant-of-record means you do not collect/ remit sales tax yourself — a real reason to pick Paddle over Stripe for global SaaS.",
    },
    "google_play_billing": {
        "name": "Google Play Billing (in-app purchases)",
        "console": "https://play.google.com/console",
        "docs": "https://developer.android.com/google/billing",
        "setup": "https://developer.android.com/google/billing/integrate",
        "steps": [
            "In Play Console: Monetize > In-app products — create each product (one-time or subscription).",
            "Integrate the Play Billing Library in the app; handle entitlements, not just the purchase event.",
            "Test with a license test account (Play Console > Setup > License testing) — test purchases are free.",
            "Products must be in the production release; drafts are not purchasable.",
            "Handle refunds/revocations via the RTDN (real-time developer notifications) endpoint.",
        ],
        "credentials": [
            {"id": "google_play.token", "kind": "oauth_token", "how": "Play Console API/OAuth token for product management"},
        ],
        "placeholders_example": "billing test account: set in Play Console, not a file secret\n",
        "notes": "Google takes its cut per transaction and requires the Play Billing Library for digital goods — side-loading payments for digital content violates Play policy.",
    },
    "apple_iap": {
        "name": "Apple In-App Purchase (StoreKit)",
        "console": "https://appstoreconnect.apple.com",
        "docs": "https://developer.apple.com/documentation/storekit",
        "setup": "https://developer.apple.com/documentation/storekit/in-app_purchase",
        "steps": [
            "In App Store Connect: create In-App Purchases (consumable / non-consumable / auto-renewing / non-renewing).",
            "Integrate StoreKit 2 in the app; the product id in code must match the App Store Connect product exactly.",
            "Test with Sandbox testers (Xcode or TestFlight) — sandbox purchases use the same flow, real money is not charged.",
            "Submit with the app build; IAPs go live when the app version is approved.",
            "Implement receipt/transaction validation server-side (App Store Server API) for entitlements.",
        ],
        "credentials": [
            {"id": "apple.app_id", "kind": "api_key", "how": "App Store Connect API key for the Server API"},
        ],
        "placeholders_example": "storekit.config.json product ids must match App Store Connect exactly\n",
        "notes": "Apple Pay (the payment *sheet*) is a PSP feature — you get it by integrating Stripe/Paddle/Adyen, not a direct Apple API. Apple *IAP* is for digital goods inside the app and is mandatory for them; Apple Pay via PSP is for physical goods / web checkout.",
    },
}

# ---------------------------------------------------------------------------
# Packaging targets
# ---------------------------------------------------------------------------

PACKAGING: dict[str, dict[str, Any]] = {
    "pypi": {
        "name": "PyPI wheel + sdist",
        "tooling": "hatchling/setuptools + build + twine",
        "docs": "https://packaging.python.org",
        "steps": [
            "python -m build  ->  dist/*.whl and dist/*.tar.gz",
            "twine check dist/*",
            "twine upload --username __token__ --password {{PUB.pypi.token}} dist/*",
            "Verify: pip download <pkg>==<ver> --no-deps in a clean venv, import it, run --version.",
        ],
        "verify": "pip index versions <pkg> (or a clean-venv install + import) on a different Python if you support 3.11+ only.",
        "notes": "Wheel tags encode CPython version + platform; check you are not accidentally shipping a platform-specific wheel for pure code.",
    },
    "github_release": {
        "name": "GitHub Release (any binary)",
        "tooling": "git + gh CLI",
        "docs": "https://docs.github.com/en/repositories/releasing-projects-on-github",
        "steps": [
            "git tag -a v1.2.3 -m 'release notes' && git push origin v1.2.3",
            "gh release create v1.2.3 dist/* --title 'v1.2.3' --notes-file RELEASE_NOTES.md",
            "Add a SHA256SUMS file next to the assets (gh release upload v1.2.3 dist/SHA256SUMS).",
        ],
        "verify": "gh release view v1.2.3 (assets listed), then download + sha256sum -c on another machine.",
        "notes": "Release assets are immutable per tag — delete the release and re-create to fix an asset.",
    },
    "windows_installer": {
        "name": "Windows installer (.exe) — Inno Setup",
        "tooling": "Inno Setup (ISCC)",
        "docs": "https://jrsoftware.org/isinfo.php",
        "steps": [
            "Author an .iss script: [Setup] AppVersion, [Files] your app + deps, [Tasks] shortcuts, [Run] postinstall.",
            "Compile: ISCC setup.iss  ->  Output/app-setup-1.2.3.exe",
            "Sign the installer with a code-signing certificate (signtool) — unsigned installers trip SmartScreen.",
        ],
        "verify": "Run the installer on a clean Windows VM (or the user's machine): install, launch, uninstall, confirm no leftover files.",
        "notes": "Inno Setup is free for the compiler; the GUI is optional. Code-signing is what makes Windows trust you, not the installer format.",
    },
    "msi": {
        "name": "MSI (Windows Installer) — WiX",
        "tooling": "WiX Toolset (candle + light)",
        "docs": "https://wixtoolset.org/docs",
        "steps": [
            "Author .wxs source: Product GUID (fixed per product, change = new product to the OS), File/Firewall/Service elements.",
            "candle source.wxs && light obj.wixobj -ext WixUtilExtension -out app-1.2.3.msi",
            "Sign with signtool; MSI supports per-user or per-machine install and real uninstall (Add/Remove Programs).",
        ],
        "verify": "msiexec /i app.msi /qn on a clean VM, then msiexec /x with the ProductCode; verify registry + Program Files cleanup.",
        "notes": "The Product GUID must never change across versions or the OS treats upgrades as a second install. MSI is what Chocolatey/Winget consume underneath.",
    },
    "scoop": {
        "name": "Scoop bucket manifest (Windows, no admin)",
        "tooling": "scoop CLI + a bucket (extras)",
        "docs": "https://scoop.sh",
        "steps": [
            "Write a JSON manifest: version, architecture, url(s) to your release assets, sha256 checksums, installer args.",
            "scoop bucket add <your-bucket> https://github.com/<you>/<repo>-bucket && edit the manifest in the bucket.",
            "Push the bucket PR; users add the bucket and scoop install <app>.",
        ],
        "verify": "scoop install <app> -f on a clean machine; check sha256 matches and the app runs from scoop's shim.",
        "notes": "Scoop installs per-user to %LOCALAPPDATA%\\scoop — no admin required, which is its whole value. The checksum in the manifest is the security boundary.",
    },
    "chocolatey": {
        "name": "Chocolatey package (Windows, admin-grade)",
        "tooling": "choco CLI + choco pack",
        "docs": "https://docs.chocolatey.org",
        "steps": [
            "Create a .nuspec (id, version, releaseNotes) plus a tools/chocolateyinstall.ps1 that installs your MSI/EXE.",
            "choco pack <pkg>  ->  <id>.<ver>.nupkg",
            "Publish to the community repository (requires an account + review) or your own internal feed.",
        ],
        "verify": "choco install <id> --source <your-feed> --force on a clean VM; confirm it appears in choco list and runs.",
        "notes": "Community-repo packages go through human review (days). Internal feeds skip review — use them for corporate distribution.",
    },
    "winget": {
        "name": "Winget (Windows Package Manager, Microsoft)",
        "tooling": "wingetcreate + winget-pkgs PR",
        "docs": "https://learn.microsoft.com/windows/package-manager/winpkg",
        "steps": [
            "wingetcreate validate <id> <version>  (generates the manifest set: installer, locale, default).",
            "Submit a PR to microsoft/winget-pkgs with the manifests + the installer URL + sha256.",
            "Passes automated validation + a human reviewer; the package lands in the winget source.",
        ],
        "verify": "winget install --id <publisher>.<app> --exact on a clean machine; winget show <id> lists the version.",
        "notes": "Winget is the first-party path (no third-party bucket). Ids are <Publisher>.<Product> and immutable — choose the publisher name carefully.",
    },
    "homebrew": {
        "name": "Homebrew formula (macOS/Linux, CLI)",
        "tooling": "brew + a tap (your-brew-repo)",
        "docs": "https://formulae.brew.sh",
        "steps": [
            "Write a Ruby formula: url to a tagged GitHub release, sha256, build steps, test block.",
            "brew tap new <you>/<repo-tap> (or add a formula to an existing tap) and push.",
            "brew install <you>/<repo-tap>/<formula> on a clean machine to prove it.",
        ],
        "verify": "brew install --force-bottle=false (or from source) + brew test <formula> in a clean environment.",
        "notes": "Formulas are Ruby and live in a GitHub tap — updates are just PRs. A formula in the homebrew/core repo requires much higher bar (bottles, CI).",
    },
    "self_hosted": {
        "name": "Self-hosted artifact + redirector",
        "tooling": "any object storage + a small installer script",
        "docs": "https://www.iana.org/assignments/media-types (pick the right Content-Type)",
        "steps": [
            "Upload the signed artifact to your release bucket (S3/R2/GCS) under /v<version>/ with a SHA256SUMS sidecar.",
            "Serve behind HTTPS with long cache for immutable versions, no-cache for the latest redirect.",
            "Ship a one-line install path: a curl/wget to a redirector script, or a winget/scoop/choco manifest pointing at your URL.",
        ],
        "verify": "Download on a clean machine, verify sha256, install, run, uninstall; repeat over a different network (CDN edge).",
        "notes": "This is the 'custom' platform's packaging: you are the store, so the checksum + TLS + signature triple is your review process.",
    },
}

# ---------------------------------------------------------------------------
# AI release-test plans (pub.testers)
# ---------------------------------------------------------------------------

def test_plan(platform: str = "", packaging_target: str = "", version: str = "") -> dict[str, Any]:
    """Build a machine-executable release test plan.

    The plan is a list of steps the agent can run one by one: each is either a
    tool call (name + args) or a shell command, with acceptance lines. Plans
    reference ``{{PUB.<id>}}`` placeholders — they never contain raw secrets.
    The agent is expected to ``pub.inject`` any script it needs before running
    the step that needs the secret.
    """
    version = version or "next"
    steps: list[dict[str, Any]] = []
    steps.append({
        "id": "pre.1", "phase": "preflight",
        "run": {"tool": "pub.placeholders", "args": {}},
        "accept": "every marker in the tree is 'bound' (no 'missing' rows)",
        "on_fail": "run pub.store_put for each missing id and re-scan; do not publish with unbound markers",
    })
    steps.append({
        "id": "pre.2", "phase": "preflight",
        "run": {"tool": "fs.glob", "args": {"pattern": "dist/**"}},
        "accept": "at least one artifact exists in dist/",
        "on_fail": "run the packaging build step for the target first",
    })
    if packaging_target and packaging_target in PACKAGING:
        entry = PACKAGING[packaging_target]
        steps.append({
            "id": "build.1", "phase": "build",
            "run": {"tool": "pub.packaging", "args": {"target": packaging_target}},
            "accept": "steps returned; follow them to produce the artifact",
            "on_fail": "report the exact failing command to the user with the error tail",
        })
        steps.append({
            "id": "build.2", "phase": "build",
            "run": {"tool": "shell.run", "args": {"command": f"cd dist && sha256sum * > SHA256SUMS  # {entry['name']}"}},
            "accept": "SHA256SUMS written next to the artifacts",
            "on_fail": "re-run in dist/; the checksum file is what users verify against",
        })
    if platform and platform in PLATFORMS:
        p = PLATFORMS[platform]
        steps.append({
            "id": "pub.1", "phase": "publish",
            "run": {"tool": "pub.inject", "args": {"dry_run": True}},
            "accept": "dry-run reports the files that would be written and zero missing ids",
            "on_fail": "fix bindings (pub.store_put) then re-run the dry-run",
        })
        steps.append({
            "id": "pub.2", "phase": "publish",
            "run": {"tool": "pub.inject", "args": {"dry_run": False}},
            "accept": "all target files updated; journal entries recorded (undoable)",
            "on_fail": "the journal makes this undoable — inspect, fix, re-inject",
        })
        steps.append({
            "id": "pub.3", "phase": "publish",
            "run": {"command": f"see {p['docs']} for the {p['name']} upload path; artifact is in dist/"},
            "accept": "the release is live at the console URL listed by pub.platforms",
            "on_fail": "screenshot/copy the console error; do not retry more than twice",
        })
    steps.append({
        "id": "verify.1", "phase": "verify",
        "run": {"command": f"on a clean machine/VM: download the v{version} artifact, verify sha256, install, run, uninstall"},
        "accept": "clean install + launch + clean uninstall with no leftover files",
        "on_fail": "capture the failure step and logs; this is the user-facing quality gate",
    })
    steps.append({
        "id": "verify.2", "phase": "verify",
        "run": {"tool": "fs.read", "args": {"path": "dist/SHA256SUMS"}},
        "accept": "checksums published alongside the artifact",
        "on_fail": "re-generate SHA256SUMS and re-upload",
    })
    return {
        "version": version,
        "platform": platform,
        "packaging": packaging_target,
        "generated_from": "publish_data.py (single source of truth)",
        "secret_policy": "steps reference {{PUB.<id>}} only; run pub.inject before any step that needs a secret",
        "steps": steps,
        "stop_rule": "any step failing twice stops the plan; report to the user with the failing step id and error tail",
    }
