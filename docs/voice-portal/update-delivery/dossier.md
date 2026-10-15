# App update delivery: what is possible, with evidence

Prepared 2026-10-15 for Rawley, who asked whether a new build of RLM-ADK can reach his iPhone while
he is away from home, without plugging it into Spark-A and without submitting anything, ideally by a
loop that installs new versions with no tap at all. Every claim below names its evidence: a file in
`evidence/` (captured on Spark-A with the command shown inside it), a line in this repository, or a
public source. "Observed" means we ran it; "documented" means a source says it; "inferred" means
neither, and is marked as such.

## 0. The goal, as Rawley set it

This app is for one person. The $99 developer account exists so that a single developer can change
it freely; nothing here is about distributing it. The goal: when Claude lands a change from a remote
session, the new build reaches the phone **with no USB cable and, ideally, no click**, including
while Rawley is away from home. Section 3 answers that directly. Conclusions were revised after an
independent review by Codex ([review.md](review.md)).

## 1. Conclusions

**C1. TestFlight was superseded as the default delivery route; it is kept only as a fallback.**
PR #8 (`86745b3`, "Direct private installation") made Ad Hoc signing plus direct installation the
default: "This app is permanently private to Rawley's phone, so the default delivery minimises
Apple's part ... there is no upload, processing, review or TestFlight"
([prototype-runbook.md L341-L345](../prototype-runbook.md#L341)). The runbook calls TestFlight "now
the **fallback** route" ([L331](../prototype-runbook.md#L331), also [L158](../prototype-runbook.md#L158),
[L260](../prototype-runbook.md#L260)). `testflight.yml` still exists and runs only on manual dispatch
(`evidence/04-repo-decisions.txt`). This was a choice, not a technical limit. *Review: confirmed.*

**C2. The direct route has two install methods, and the tailnet page is the second one.** The
runbook lists a *paired route* (`pymobiledevice3 apps install` over the USB pairing, or a Wi-Fi
pairing created over the cable) and a *link route* (open `index.html` and tap "Install on this
iPhone"; "It is a tap on the phone, never a silent push") ([L395-L402](../prototype-runbook.md#L395)).
`direct-install.yml` builds the signed IPA and `scripts/direct_install.py package` writes the manifest
and page. The page at `https://rlm-adk-spark.tail0ec4a5.ts.net:8443/` (Tailscale Serve, tailnet only,
Let's Encrypt certificate) is that link route, not new code (`evidence/01-tailnet.txt`,
`evidence/05-build18-link-route.txt`). Device Hub, Apple Configurator and `devicectl` are other
clients of the paired route. *Review: confirmed.*

**C3. Build 18 is staged on the link route and consistent on the server side; no iPhone has
installed from the page yet.** Observed: page 200, manifest asset URL and `bundle-version` 18, the IPA
downloaded over HTTPS with the receipt's sha256 (`ff596a19…`), certificate verification passed
(curl `ssl_verify_result` 0, no `-k`) for a Let's Encrypt certificate on
`rlm-adk-spark.tail0ec4a5.ts.net`, and the embedded profile lists only this phone's UDID and expires
2027-10-13 (`evidence/05-build18-link-route.txt`; run
[38728601676](https://github.com/RawleySM/rlm-adk-ios/actions/runs/38728601676)). The runbook: "The
HTTPS package is hosted but OTA installation has not been exercised" ([L352](../prototype-runbook.md#L352));
build 7 was installed over USB. Whether iOS accepts the manifest, certificate and profile is
observed only on the first real install. *Review: uncertain; wording narrowed.*

**C4. The link route always ends in Apple's install confirmation; no ordinary app can answer it.**
Apple describes the link flow ("When a user taps a web link, the manifest file is downloaded")
in [Distribute proprietary in-house apps](https://support.apple.com/guide/deployment/depce7cefc4d/web);
the review cites a Chromium issue: "iOS still presents its native system confirmation sheet before
any application is installed". There is no public app API that installs an IPA; an app, a Shortcut
or a web page can only open the same link, so a loop in the app can make the confirmation appear
by itself but cannot accept it. Other system paths do update apps without a tap (C5). *Review: the
earlier, broader claim "an app cannot install or update itself silently" was refuted and is
withdrawn.*

**C5. Mechanisms that install or update with no tap on a phone that is not supervised:**

| Mechanism | Source | Fits a private Ad Hoc build without submitting to Apple? |
|---|---|---|
| A paired developer connection (USB; same-LAN Wi-Fi; or relayed over Tailscale, see C6) | runbook [L395](../prototype-runbook.md#L395); Apple [Device Hub](https://developer.apple.com/documentation/xcode/managing-your-simulated-and-physical-devices-in-device-hub); [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | yes; USB proven here for every install so far (builds 7, 13, 14, 16 among them) |
| TestFlight automatic updates, after a one-time install and switching Automatic Updates on | [TestFlight for testers](https://testflight.apple.com/): "have the latest beta builds install automatically"; builds last 90 days | yes for internal testing (no review), but it means uploading each build to Apple (C1) |
| Updates of an app already managed by device management | Apple [Distribute managed apps](https://support.apple.com/en-euro/guide/deployment/dep575bfed86/web): "Updates are applied silently. The user is prompted only when the app is running in the foreground"; declarative management checks "typically every 24 hours" and updates "when the device is locked" | not documented for Ad Hoc builds (Apple's sources are the App Store or a manifest, its examples Enterprise apps); needs enrolling the phone in an MDM service; untested |
| On-device installer through the phone's own installation service (SideStore's technique) | [SideStore](https://github.com/SideStore/SideStore); [tailrpproxy](https://github.com/eseiker/tailrpproxy) avoids the second VPN by reflecting RPPairing through the tailnet | in principle; stock SideStore re-signs with a development certificate and asks for a tap before an app update, so a no-tap updater would be custom |
| App Store automatic updates, alternative marketplaces, `RequiredAppIDForMDM` | Apple ([review.md](review.md)) | no: each needs Apple distribution or is limited to MDM agent apps |
| Supervision with device management | Apple [Content distribution methods](https://support.apple.com/en-au/guide/deployment/dep7cef2e0ea/web): "For supervised devices, apps install silently"; supervising with Apple Configurator erases the phone ([device supervision](https://support.apple.com/en-gb/guide/deployment/dep1d89f0bff/1/web/1.0)) | only after erasing the phone |

*Review: the earlier four-row list was refuted as incomplete; this list includes the review's additions.*

**C6. The developer connection can be carried over Tailscale, with conditions; it is not proven on
this phone.** Observed here: lockdownd (TCP 62078) refused over the tailnet; of 17,413 TCP ports
probed, one (62811) accepted and answered neither HTTP, TLS nor a bare RemotePairing handshake
within 15 s; 45001 is Tailscale's own peer API (`evidence/02-phone-ports-over-tailnet.txt`). Spark-A
has the USB pairing record but no network (RemotePairing) record (`evidence/06-phone-usb-state.txt`).
Documented elsewhere: an [independent report](https://dev.to/kvnpt/how-to-remotely-iterate-deploy-your-sideloaded-ios-apps-over-tailnet-jak)
installs an app on an iOS 26 phone over Tailscale with Xcode's `devicectl` on a Mac, by relaying
RemotePairing (port 49152) and the QUIC tunnel ports (55000-55300) to the phone's tailnet address and
advertising them locally with spoofed Bonjour records ("Acquired tunnel connection to device", "App
installed"). Its conditions: the phone must be **joined to some Wi-Fi network** ("radio-on-no-
association is not sufficient"; cellular alone does not open the ports); one USB session captures
the identifiers; after a phone reboot the cable is needed again; ports and identifiers can rotate.
62811 is a strong candidate for the phone's RemotePairing listener, not identified. pymobiledevice3
(Linux, already on Spark-A) accepts an explicit host and port for RemotePairing, so a Linux version of
the relay is plausible but untried. *Review: the earlier "not reachable" conclusion was refuted.*

**C7. TestFlight is set up on Apple's side; whether it would update with no tap is unverified.**
Observed (GET requests only): app "RLM-ADK Voice Portal", builds 4-6 `VALID`, internal group
"Prototype internal" with `hasAccessToAllBuilds: true`, tester `rawley.stanhope@gmail.com` in state
`INSTALLED` (`evidence/03-app-store-connect.txt`). `INSTALLED` only means some build was installed;
the Automatic Updates switch is on the phone, and no workflow uploads a build after a merge.
*Review: uncertain; wording narrowed.*

## 2. Options

| | Option | Clicks per update | Works away from home | Prerequisites and costs | State |
|---|---|---|---|---|---|
| O1 | Link route: open the page, tap Install, confirm | 2 | yes, on cellular too | Tailscale on | build 18 staged, never installed from the page |
| O2 | O1 plus a watcher in the app that opens the install link itself when a newer build is published | 1 (Apple's confirmation) | yes, on cellular too | build 19; a `latest.json` on the page; only while the app is open | not built |
| O3 | Spark-A installs each new build over the paired developer connection | 0 | only while cabled, or on home Wi-Fi with a Wi-Fi pairing | phone cabled or on the home network | not built; same install step as builds 13, 14, 16 |
| O4 | TestFlight automatic updates: CI uploads each merged build; TestFlight installs it | 0 | yes, on cellular too | one-time TestFlight install and Automatic Updates on; every build goes to Apple; install timing is Apple's | reverses C1; possible, not set up |
| O5 | Supervision with device management | 0 | yes | erases the phone | possible, not proposed |
| O6 | On-device installer (SideStore technique, Tailscale-compatible with tailrpproxy) | 0 if custom-built | yes | pairing file; custom updater; development-certificate re-signing in the stock tool | possible in principle, large custom work |
| O7 | Spark-A installs over a developer tunnel relayed through Tailscale (C6) | 0 | only while the phone is joined to some Wi-Fi network | one USB session to pair and capture identifiers; again after each phone reboot; relay of RemotePairing and QUIC ports; a Mac with `devicectl` (proven elsewhere) or pymobiledevice3 on Spark-A (untried) | possible, demonstrated elsewhere, untested here |
| O8 | Device management without supervision, updating an already-managed app | 0 for updates | yes | enrolling the phone in an MDM service; first install needs consent; Ad Hoc builds not documented as a source | uncertain |

## 3. Zero clicks while away: what is actually possible

- **Possible everywhere, including on cellular: TestFlight automatic updates (O4).** It is the only
  route Apple documents for installing each new private build with no tap wherever the phone is.
  Install timing is Apple's, not immediate. It reverses the choice in C1, so it needs Rawley's yes.
- **Possible only while the phone is on some Wi-Fi network: the Tailscale developer tunnel (O7).**
  It keeps the direct route and Apple out of the loop, but has never run on this phone, needs a USB
  session to set up and after each phone reboot, and depends on ports that can move.
- **Not possible on the link route:** every link install ends in Apple's confirmation (C4). The
  best a loop in the app can do there is one click (O2).
- O5, O6 and O8 are possible only at a cost Rawley has not accepted (erasing the phone, building a
  custom on-device installer, enrolling the phone in device management) or are undocumented for
  this kind of build.

Recommended next steps: install build 18 from the link now (O1). If zero clicks everywhere matters
more than keeping Apple out of the loop, switch on O4. At the next USB session, attempt O7 from
Spark-A with pymobiledevice3; if it works it gives zero clicks whenever the phone is on Wi-Fi, without
TestFlight.

## 4. Evidence index

| File | What it shows |
|---|---|
| [evidence/01-tailnet.txt](evidence/01-tailnet.txt) | Tailnet name, HTTPS certificate domain, the phone online on the tailnet, Tailscale Serve config (`:8443` tailnet-only serving the delivery folder) |
| [evidence/02-phone-ports-over-tailnet.txt](evidence/02-phone-ports-over-tailnet.txt) | lockdownd 62078 refused; 17,413-port probe; 62811 unidentified; 45001 Tailscale peer API ([tailnet_probe.py](evidence/tailnet_probe.py), [rp_handshake.py](evidence/rp_handshake.py)) |
| [evidence/03-app-store-connect.txt](evidence/03-app-store-connect.txt) | TestFlight builds, internal group with access to all builds, tester state |
| [evidence/04-repo-decisions.txt](evidence/04-repo-decisions.txt) | Commits that introduced each route; runbook lines that rank them; `testflight.yml` trigger |
| [evidence/05-build18-link-route.txt](evidence/05-build18-link-route.txt) | Build 18 page, manifest, IPA hash, certificate, profile, receipt |
| [evidence/06-phone-usb-state.txt](evidence/06-phone-usb-state.txt) | Phone not on USB; USB pairing record on Spark-A, no network (RemotePairing) record; app last seen by the backend; tool version |
| [../prototype-runbook.md](../prototype-runbook.md) | Route decisions and install methods (L158, L260, L331, L341-L402) |
| [`.github/workflows/direct-install.yml`](../../../.github/workflows/direct-install.yml), [`scripts/direct_install.py`](../../../scripts/direct_install.py) | The direct route's build and packaging |
| [`.github/workflows/testflight.yml`](../../../.github/workflows/testflight.yml), [`scripts/asc_build_upload.py`](../../../scripts/asc_build_upload.py) | The retained TestFlight fallback |

Public sources: [independent report: remote install over Tailscale, iOS 26](https://dev.to/kvnpt/how-to-remotely-iterate-deploy-your-sideloaded-ios-apps-over-tailnet-jak);
[tailrpproxy](https://github.com/eseiker/tailrpproxy);
[Apple, Distribute managed apps](https://support.apple.com/en-euro/guide/deployment/dep575bfed86/web);
[Apple, Device Hub](https://developer.apple.com/documentation/xcode/managing-your-simulated-and-physical-devices-in-device-hub);
[Apple, device supervision](https://support.apple.com/en-gb/guide/deployment/dep1d89f0bff/1/web/1.0);
[Apple, Distribute proprietary in-house apps](https://support.apple.com/guide/deployment/depce7cefc4d/web);
[Apple, Content distribution methods](https://support.apple.com/en-au/guide/deployment/dep7cef2e0ea/web);
[Apple, Distributing your app to registered devices](https://developer.apple.com/documentation/xcode/distributing-your-app-to-registered-devices);
[TestFlight for testers](https://testflight.apple.com/);
[AppleInsider on TestFlight automatic updates (2020)](https://appleinsider.com/articles/20/11/11/apple-updates-testflight-with-automatic-beta-build-update-option);
[SideStore](https://github.com/SideStore/SideStore);
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3).

## 5. Review

Reviewed by Codex (`gpt-5.6-sol`, reasoning `xhigh`, live web search, read-only) for feasibility
only. Verdicts and responses: [review.md](review.md); full review: [codex-review.md](codex-review.md);
prompt: [codex-review-prompt.md](codex-review-prompt.md).
