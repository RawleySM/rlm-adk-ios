# Agent notes: rlm-adk-ios

SwiftUI iPhone client for rlm-adk. The local Mac (MacBook Air, macOS 12.7.6) cannot run current
Xcode, so all Xcode work happens on GitHub-hosted macOS runners.

## Cloud Mac

- Every push runs `.github/workflows/cloud-mac.yml`: unsigned iOS Simulator build, launch, and a
  screenshot artifact (`simulator-screenshot`).
- For hands-on work, use `scripts/cloud-mac` (installed as `cloud-mac` on Spark-A):
  - `cloud-mac start [minutes]`: starts `cloud-mac-session.yml`. The runner joins the tailnet as
    `rlm-adk-ios-mac` (tag `tag:ci-mac`, ephemeral) with Tailscale SSH, runs `xcodegen
    generate`, and waits. The default is 60 minutes; the maximum is 330.
  - `cloud-mac ssh [command]`: opens a shell, or runs a command, in the checkout on the Mac,
    as user `runner` with sudo.
  - `cloud-mac stop`: releases the Mac early. `cloud-mac status`: shows the latest run.
- Each session is a fresh VM. Commit and push anything you want to keep before it ends.
- Nothing needs setting up per session. Tailscale access uses workload identity federation:
  repo variables `TS_OIDC_CLIENT_ID` and `TS_AUDIENCE`, trust credential subject
  `repo:RawleySM@215691718/rlm-adk-ios@1415944198:*`. No secret is stored and nothing expires.
  GitHub's OIDC subject includes the owner and repo IDs. A subject written as
  `repo:RawleySM/rlm-adk-ios:*` gets a 403 from the token exchange. Because the IDs survive a
  rename, the credential keeps working if the repo is renamed.
- Tailnet policy: `tag:ci-mac` is owned by autogroup:admin, and an `ssh` rule lets
  autogroup:member in as `runner` or `root`.

## Apple account

- Apple Developer, Individual: KeePassXC entry
  "Apple Developer + App Store Connect - Individual - Enrollment 9Z9523324Y".
- Membership is active. Team `4PJBKY2WQR`, the Apple Distribution identity, the App Store profile
  and the App Store Connect API key are configured. Reuse the existing certificate across builds;
  replace it only when renewal or an actual signing failure requires it. Do not publish to the
  public App Store.
- This is permanently a private app for Rawley alone. The default delivery is a **direct signed
  install** (Ad Hoc / release-testing export for the registered iPhone) through
  `.github/workflows/direct-install.yml` and `scripts/direct_install.py`; TestFlight
  (`testflight.yml`, last build `0.2.0 (6)`, VALID / IN_BETA_TESTING) is the fallback only and is
  not dispatched for routine builds. Direct install needs the phone's UDID registered to the team
  and an Ad Hoc profile that lists it (`ADHOC_PROFILE_BASE64`); the workflow fails before signing
  when that profile is absent or unsuitable. Developer Mode on the phone is required to run an app
  installed from an IPA (Apple, "Distributing your app to registered devices"); Apple's
  certificate check runs online at first launch. Signing a build and installing it on the phone
  are separate steps. Upgrade the same bundle id without uninstalling; copy retained prayer and
  ambient captures before changing installation channels and verify their bytes afterward.
  Direct Build 7 was installed over paired Spark-A USB on 2026-10-13; build/signer,
  retained audio hashes, remote diagnostics and ongoing recording were verified. Actual OTA
  installation and the full interruption/Bluetooth matrix remain unverified.
  See `docs/voice-portal/prototype-runbook.md`, "Direct private installation".
