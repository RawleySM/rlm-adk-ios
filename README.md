# RLM-ADK iPhone client

SwiftUI app for Rawley's iPhone that will connect to rlm-adk over the Tailscale tailnet.

- Project: XcodeGen spec in `project.yml` (`xcodegen generate` makes `RLMADK.xcodeproj`).
- Build host: the Mac on hand runs macOS 12.7.6 and cannot run current Xcode. Builds run on
  GitHub-hosted macOS runners (`.github/workflows/cloud-mac.yml`), which build the app for the
  iOS Simulator unsigned, run it, and upload a screenshot artifact.
- Apple Developer account: KeePassXC entry
  "Apple Developer + App Store Connect - Individual - Enrollment 9Z9523324Y". Team `4PJBKY2WQR`
  is active with signing assets in place. This is a private app for one phone: builds are signed
  for the registered iPhone and installed directly (`.github/workflows/direct-install.yml`,
  manual dispatch, no App Store Connect upload); TestFlight (`testflight.yml`) is the fallback.
  The phone needs Developer Mode for IPA installs, and the Ad Hoc profile and certificate expire
  (profile expiry is in each build's receipt). Details in
  `docs/voice-portal/prototype-runbook.md`.
- Hands-on cloud Mac over the tailnet: `scripts/cloud-mac start`, then `cloud-mac ssh`. See `AGENTS.md`.

## Voice portal requirements and architecture

The [voice portal documentation](docs/voice-portal/README.md) covers the user needs, native iOS
development direction, and backend organization options for Scripture, prayer, life context,
and durable RLM-ADK work.
