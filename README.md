# RLM-ADK iPhone client

SwiftUI app for Rawley's iPhone that will connect to rlm-adk over the Tailscale tailnet.

- Project: XcodeGen spec in `project.yml` (`xcodegen generate` makes `RLMADK.xcodeproj`).
- Build host: the Mac on hand runs macOS 12.7.6 and cannot run current Xcode. Builds run on
  GitHub-hosted macOS runners (`.github/workflows/cloud-mac.yml`), which build the app for the
  iOS Simulator unsigned, run it, and upload a screenshot artifact.
- Apple Developer account: KeePassXC entry
  "Apple Developer + App Store Connect - Individual - Enrollment 9Z9523324Y". Device builds,
  signing and TestFlight wait for the Team ID and an App Store Connect API key.
