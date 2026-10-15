# Review of the update-delivery dossier

Reviewer: Codex CLI 0.154.0, model `gpt-5.6-sol`, reasoning effort `xhigh`, live web search, read-only
sandbox (493,401 tokens). Asked for feasibility only: whether each claim of possible or impossible is
correct for this one phone, and which mechanisms were missed; not best practice. Prompt:
[codex-review-prompt.md](codex-review-prompt.md). Full review: [codex-review.md](codex-review.md).

"Sol 6.1" was requested; the Codex catalogue on Spark-A has no 6.1. `gpt-5.6-sol` is the Sol model it
offers (`codex debug models`: gpt-6-astra, gpt-reserve, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna).

## Verdicts and responses

| Claim | Verdict | Response |
|---|---|---|
| C1 TestFlight superseded, kept as fallback | CONFIRMED | Kept. Codex adds that this is a choice, not a technical limit. |
| C2 two direct install methods; the tailnet page is the link route | CONFIRMED | Kept, with Codex's addition that Device Hub, Apple Configurator and `devicectl` are further clients of the paired route. |
| C3 build 18 staged and checks out | UNCERTAIN | Partly accepted: no iPhone has fetched and installed it, so "checks out" now says "consistent on the server side". One point rejected: Codex read `ssl_verify=0` as verification disabled; curl's `%{ssl_verify_result}` of 0 is `X509_V_OK` (verification passed) and the captures ran without `-k` ([evidence/06](evidence/06-phone-usb-state.txt)). Build 8's hosting-then-USB history is now stated only as far as the runbook supports it. |
| C4 a link install needs a tap; an app cannot install or update itself silently | REFUTED (second half) | Accepted. The link route does need Apple's confirmation (Codex cites a Chromium issue quoting "iOS still presents its native system confirmation sheet"), and no public app API installs an IPA, but other system paths update apps with no tap. C4 is rewritten. |
| C5 the four zero-tap mechanisms | REFUTED (not exhaustive; MDM first install conflated with update) | Accepted. Apple's managed-apps guide says updates of command-managed apps "are applied silently. The user is prompted only when the app is running in the foreground", and declarative management updates while the phone is locked; but Apple documents only App Store and Enterprise apps as sources, not an Ad Hoc IPA. App Store, alternative marketplaces and `RequiredAppIDForMDM` added for completeness; none fits a private Ad Hoc build without submitting to Apple. |
| C6 installation service not reachable over Tailscale | REFUTED | Accepted after checking the source myself. The [independent report](https://dev.to/kvnpt/how-to-remotely-iterate-deploy-your-sideloaded-ios-apps-over-tailnet-jak) installs an app on an iOS 26 phone over Tailscale ("Acquired tunnel connection to device", "App installed") by relaying RemotePairing (49152) and the QUIC tunnel ports (55000-55300) from a Mac running `devicectl`, with spoofed Bonjour records. Its limits, which Codex reports in part: lockdownd 62078 resets (as observed here); the phone must be **joined to some Wi-Fi network**, "radio-on-no-association is not sufficient", so cellular alone does not work; one USB session to capture the Bonjour identifiers; the Developer Disk Image staged over USB lasts until the phone reboots; identifiers and ports can rotate. 62811 is now "a strong RemotePairing candidate, not identified". |
| C7 TestFlight ready for no-tap updates | UNCERTAIN | Accepted: `INSTALLED` means only that the tester installed some build; whether Automatic Updates is on is a setting on the phone, and no workflow uploads builds after a merge. |

Further points accepted: O3 is not limited to home if the tunnel route works; O4 is possible, not
"ready"; O5 is possible but needs the phone erased (Apple: "During this process, the device is erased
and all data is lost"), so "not viable" was a judgement; O6's VPN conflict is avoidable with
[tailrpproxy](https://github.com/eseiker/tailrpproxy) (a Linux reflector that routes SideStore's
RPPairing through the tailnet), and stock SideStore asks for a tap before an app update, so a no-tap
SideStore updater would be custom work. The note "no RemotePairing record exists" is now backed by a
check of `~/.pymobiledevice3` ([evidence/06](evidence/06-phone-usb-state.txt)).
