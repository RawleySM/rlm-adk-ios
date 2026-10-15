The dossier is right about the ordinary Ad Hoc web-link experience, but it overgeneralizes that experience into claims about iOS installation as a whole. C4, C5, and C6 are therefore wrong as written. The strongest missed result is that an unsupervised iPhone can receive zero-tap updates in several circumstances—including updates to an already-managed app—and current iOS developer services can, with additional discovery/proxy work, be carried over a Tailscale path.

## Verdicts (C1-C7)

### C1 — CONFIRMED

“TestFlight was superseded as the default delivery route; it is kept only as a fallback” is confirmed as a repository decision.

- The runbook says: “TestFlight internal testing is now the **fallback** route” and “The default private route is the direct signed install below.” See [prototype-runbook.md lines 331–333](../prototype-runbook.md#L331).
- It also explains that the direct route avoids upload, processing, review, and TestFlight. See [prototype-runbook.md lines 341–346](../prototype-runbook.md#L341).
- The TestFlight workflow is manual-only: its sole trigger is `workflow_dispatch`. See [testflight.yml lines 9–17](../../../.github/workflows/testflight.yml#L9).
- Commit `86745b3` exists and its commit message describes direct Ad Hoc delivery as the default and TestFlight as the fallback.

This confirms a prior product choice, not a technical limitation. Choosing TestFlight again would reverse that choice, but TestFlight remains technically possible.

### C2 — CONFIRMED, as a description of the repository

The repository currently documents two direct-install methods:

1. A paired-device installation through `pymobiledevice3`.
2. A user-initiated `itms-services` web-link installation.

The runbook says:

> “Paired route: … `pymobiledevice3 apps install RLMADK.ipa`.”

and:

> “Link route … open `index.html` on the phone and tap ‘Install on this iPhone’.”

See [prototype-runbook.md lines 395–402](../prototype-runbook.md#L395).

The implementation matches that account:

- The direct workflow takes an HTTPS `base_url`, while expressly saying it does not host anything. See [direct-install.yml lines 11–22](../../../.github/workflows/direct-install.yml#L11).
- `direct_install.py` creates the manifest, the `itms-services` URL, and an “Install on this iPhone” link. See [direct_install.py lines 222–254](../../../scripts/direct_install.py#L222).
- The Tailnet evidence maps port 8443 to the private-delivery directory. See [01-tailnet.txt lines 32–40](evidence/01-tailnet.txt#L32).

This does not mean those are the only technically possible installers. Device Hub, Apple Configurator, `devicectl`, and other installation-proxy clients are additional clients of the paired-device route.

### C3 — UNCERTAIN

The artifact-staging portion is supported, but “checks out” is too strong, and parts of the installation history are not established by the cited evidence.

Confirmed locally:

- The captured page returned `200`, but the capture explicitly says `ssl_verify=0`. See [05-build18-link-route.txt line 3](evidence/05-build18-link-route.txt#L3). That proves HTTP success with certificate verification disabled, not that iOS accepted the TLS chain.
- The manifest points to build 18; the downloaded IPA hash matches the receipt; and its profile contains the target UDID with `get-task-allow: False`. See [05-build18-link-route.txt lines 4–16](evidence/05-build18-link-route.txt#L4).
- The receipt identifies build 18 and the expected bundle identifier. See [05-build18-link-route.txt lines 18–38](evidence/05-build18-link-route.txt#L18).
- The runbook says: “The HTTPS package is hosted but OTA installation has not been exercised.” See [prototype-runbook.md line 352](../prototype-runbook.md#L352).

Not established:

- No captured evidence shows iOS fetching and accepting this manifest, IPA, TLS chain, and provisioning profile end-to-end.
- The backend last saw build 16, which is consistent with build 18 not having been launched, but it cannot prove that build 18 was never installed and left unopened. See [06-phone-usb-state.txt lines 6–7](evidence/06-phone-usb-state.txt#L6).
- The sentence that builds 7 and 8 were both hosted and installed through USB is not supported by the cited runbook line. The runbook explicitly records build 7’s direct install; separate physical-device documents establish build 8 was installed, but not that build 8 had first been hosted on this page. See [prototype-runbook.md lines 348–352](../prototype-runbook.md#L348) and [device-evaluation-plan.md lines 14–17](../device-evaluation-plan.md#L14).

Therefore: build 18 is staged and internally consistent, but the link route has not “checked out” at the phone-installation layer.

### C4 — REFUTED

C4 combines one correct narrow claim with one false absolute claim.

The narrow claim is correct: a successful manual `itms-services` installation on this nonsupervised phone requires the user-facing iOS installation confirmation.

- Apple describes the manual flow as: “When a user taps a web link, the manifest file is downloaded.” The link initiates the system installation flow; it is not an unattended deployment channel. [Apple, proprietary app website distribution](https://support.apple.com/en-gb/guide/deployment/depce7cefc4d/1/web).
- A recent Chromium issue concerning `itms-services` handling reports that “iOS still presents its native system confirmation sheet before any application is installed.” [Chromium issue 533046298](https://issues.chromium.org/issues/533046298).
- Apple’s Xcode help still supports manifests for over-the-air Ad Hoc, Enterprise, and Development exports, so the problem is not that Ad Hoc OTA is nonexistent. It is user-mediated installation. [Xcode distribution options](https://help.apple.com/xcode/mac/current/en.lproj/devde46df08a.html).

A normal sandboxed application or Shortcut can open the URL and thereby remove the first “open the page/tap the link” action, but it cannot accept the protected system installation sheet. There is no manifest flag that suppresses that sheet.

The absolute statement—“an app cannot install or update itself silently”—is false:

- SideStore is itself a sandboxed iOS application that reaches the device installation service through a device-protocol/VPN technique.
- TestFlight and App Store automatic updates install subsequent versions without a tap after one-time setup.
- MDM can update an already-managed app without a tap under the conditions discussed below.
- A paired developer-service client can install or update an IPA without a phone tap.
- Apple’s alternative-distribution system supports automatic updates in eligible regions after initial authorization.

So the correct conclusion is:

> The manual `itms-services` route cannot silently confirm itself, and an ordinary app has no public “install this IPA silently” API. That does not mean all iOS installation or update mechanisms require a tap.

### C5 — REFUTED

The four mechanisms in C5 are not exhaustive, and its MDM statement improperly equates “silent first install” with “silent update.”

Apple’s current managed-app guide distinguishes the two:

- On an unsupervised device, the first required managed-app installation needs user approval.
- Once the app is managed, declarative management can check for updates periodically—typically about every 24 hours—and update while the phone is locked.
- For command-managed apps, Apple says: “Updates are applied silently. The user is prompted only when the app is running in the foreground.” [Apple, Distribute managed apps](https://support.apple.com/en-euro/guide/deployment/dep575bfed86/web).

That is a zero-tap update mechanism on an unsupervised iPhone after one-time enrollment and app-management consent.

Important limitations for this exact project:

- Declarative app management is available on iOS 17.2 and later, so iOS 26 qualifies.
- Apple’s documented declarative sources for iPhone are App Store apps and Enterprise-program apps supplied through a manifest. The current RLM-ADK IPA is Ad Hoc signed under an Individual developer account, not an Enterprise app.
- Therefore, declarative management is not a documented way to feed this exact Ad Hoc IPA to the phone.
- The legacy `InstallApplication` command accepts `ManifestURL` on iOS and does not require supervision, but Apple’s examples and current declarative documentation describe manifest-hosted iPhone apps as Enterprise apps. Whether iOS 26 will accept this exact Ad Hoc artifact through legacy MDM and then silently update it is not established by the dossier or the public Apple documentation I found.
- Apple’s `InstallApplication` page also contains older/broader prompt wording that conflicts with the newer deployment guide. That conflict should be recorded rather than hidden. [Apple, Install Application](https://developer.apple.com/documentation/devicemanagement/install-application-command).

The `InstallApplication` flags do not provide a “silent” switch. The documented management flag values are:

- `1`: remove the app when MDM management is removed.
- `4`: prevent backup of app data.
- `5`: both.

See [Apple, InstallApplication command data](https://developer.apple.com/documentation/devicemanagement/installapplicationcommand/command-data.dictionary).

C5 also omits:

- Ordinary App Store automatic updates.
- Apple’s special `RequiredAppIDForMDM` management-app exception.
- Alternative-distribution automatic updates in supported regions.
- The now-demonstrated developer-services-over-Tailscale approach described under C6/O7.

### C6 — REFUTED

The local observations are real, but the conclusion drawn from them is wrong.

Confirmed observations:

- TCP 62078 refused the captured connection attempt. See [02-phone-ports-over-tailnet.txt lines 2–5](evidence/02-phone-ports-over-tailnet.txt#L2).
- The TCP scan found one open port, 62811. See [02-phone-ports-over-tailnet.txt lines 7–11](evidence/02-phone-ports-over-tailnet.txt#L7).
- HTTP, ordinary TLS, and the attempted RemotePairing opening exchange produced no response. See [02-phone-ports-over-tailnet.txt lines 17–22](evidence/02-phone-ports-over-tailnet.txt#L17).
- The probe is TCP-only: it calls `asyncio.open_connection`. It says nothing about dynamically allocated UDP/QUIC tunnel ports. See [tailnet_probe.py lines 4–14](evidence/tailnet_probe.py#L4).

Why that does not prove developer services are unreachable:

1. TCP 62078 is the legacy lockdownd path. On iOS 17 and later, the relevant wireless developer path is RemotePairing plus a dynamically created trusted RSD/CoreDevice tunnel. A refused 62078 therefore does not settle O7.

2. `pymobiledevice3` documents that the “same pairing service is also reachable over the LAN” through `_remotepairing._tcp`, after which a paired host requests a trusted tunnel. [pymobiledevice3 RemoteXPC documentation](https://github.com/doronz88/pymobiledevice3/blob/master/misc/RemoteXPC.md).

3. Its current source accepts an explicit host and port with `asyncio.open_connection(self.hostname, self.port)` before pair verification. The library itself does not require the address to be a physical-LAN address. [RemotePairingTunnelService source](https://raw.githubusercontent.com/doronz88/pymobiledevice3/master/pymobiledevice3/remote/tunnel_service.py).

4. Most decisively, a current independent report documents an iOS 26 phone on a different Wi-Fi network, Tailscale active, with Xcode’s RemotePairing and dynamic tunnel ports proxied over the tailnet. Its captured result includes “Acquired tunnel connection to device” followed by “App installed.” [Kevin Paterson, remote iOS installation over Tailscale](https://dev.to/kvnpt/how-to-remotely-iterate-deploy-your-sideloaded-ios-apps-over-tailnet-jak).

The important distinction is discovery versus transport:

- Bonjour discovery does not cross Tailscale.
- macOS `remotepairingd` scopes connections to the interface on which it discovered the Bonjour record.
- But the underlying RemotePairing TCP endpoint and dynamically allocated trusted-tunnel UDP endpoint can be reachable at the phone’s Tailscale address.
- The demonstrated workaround locally publishes proxy Bonjour records on the Mac and relays the resulting TCP/UDP connections over Tailscale.

The dossier’s “no RemotePairing record exists” statement is also unsupported. [06-phone-usb-state.txt lines 8–10](evidence/06-phone-usb-state.txt#L8) lists only `/var/lib/lockdown`. Current `pymobiledevice3` stores RemotePairing records separately as `~/.pymobiledevice3/remote_<udid>.plist`. The cited capture did not check that location.

Port 62811 is most plausibly the dynamically assigned `_remotepairing._tcp` control listener, but that is an inference, not an identification. Current SideStore reports show the RemotePairing port moving from the traditional 49152 to ephemeral ports such as 52639 and 53900. [SideStore issue #1617](https://github.com/SideStore/SideStore/issues/1617). A same-moment Bonjour SRV/TXT capture or successful authenticated pair verification would be needed to prove that 62811 was RemotePairing. The timeout lowers confidence but does not turn it into proof of a different service—especially because the probe did not capture the `pymobiledevice3` version or use an established RemotePairing record.

### C7 — UNCERTAIN

The App Store Connect side is partly prepared, but “ready for no-tap updates” is not established.

The captured evidence confirms:

- Builds 4–6 are valid and unexpired.
- The internal group has access to all builds.
- The tester state is `INSTALLED`.

See [03-app-store-connect.txt lines 1–9](evidence/03-app-store-connect.txt#L1).

But Apple defines `INSTALLED` only as: “The beta tester has installed a build.” It does not mean that automatic updates are enabled, that the build is installed on this particular iPhone rather than another tester device, or that a new build will be installed without interaction. [Apple, BetaTesterState](https://developer.apple.com/documentation/appstoreconnectapi/betatesterstate).

Apple says TestFlight can “have the latest beta builds install automatically,” but the setting must first be enabled globally or for the individual app. [Apple, TestFlight automatic updates](https://testflight.apple.com/).

No cited evidence inspects that on-device setting. In addition:

- The runbook still says installation on the phone is unverified. See [prototype-runbook.md lines 331–333](../prototype-runbook.md#L331).
- The upload workflow is still manual-only. See [testflight.yml lines 9–17](../../../.github/workflows/testflight.yml#L9).
- `asc_build_upload.py` implements upload and polling, but it does not schedule uploads after merges. See [asc_build_upload.py lines 245–264](../../../scripts/asc_build_upload.py#L245).

Therefore TestFlight automatic updates are technically possible, but this repository has not shown that the phone’s automatic-update switch is enabled or that CI will automatically upload each build.

## Missed or wrong options

### Possible mechanisms missing from section 2

| Mechanism | Feasibility on this phone | Important qualification |
|---|---|---|
| Unsupervised MDM updates to an already-managed app | Possible, zero taps for an update while the app is not foregrounded | Initial MDM enrollment and the first installation/management takeover require consent. The exact Ad Hoc IPA is not a documented declarative-management source. |
| Ordinary App Store automatic updates | Possible and zero-tap after initial installation/settings | Requires submitting the app to Apple, contrary to the owner’s stated preference. Apple says App Store apps are automatically updated by default. [Apple, App Store updates](https://support.apple.com/en-euro/guide/iphone/iph98709f167/ios). |
| `RequiredAppIDForMDM` | Possible zero-tap initial install on an unsupervised enrollment | This is a narrow exception for the App Store-hosted MDM management app, not an arbitrary private app. Apple says it works “regardless of the device’s enrollment mode.” [Apple, managed-app management application](https://developer.apple.com/documentation/devicemanagement/displaying-managed-apps-and-packages). |
| Alternative app distribution automatic updates | Conditionally possible | Available only in eligible regions/accounts, requires Apple authorization/notarization, and requires initial user authorization. It therefore does not satisfy “no submitting to Apple.” [Apple, alternative app distribution](https://support.apple.com/en-gb/118110), [MarketplaceKit updates](https://developer.apple.com/documentation/marketplacekit/installing-apps-from-an-alternative-app-marketplace). |
| Device Hub, Apple Configurator, `devicectl`, `cfgutil`, or another installation-proxy client | Possible, zero phone taps after pairing/trust/Developer Mode | These are concrete clients of the paired-device mechanism, not independent remote transports. Apple explicitly supports Device Hub and Apple Configurator for registered-device IPAs. [Apple, managing apps on devices](https://developer.apple.com/documentation/xcode/managing-apps-on-devices). |
| Xcode/CoreDevice over a Tailscale proxy bridge | Possible, conditionally zero-tap | This is essentially a corrected and more specific O7. It needs prior pairing, service discovery/proxying, current dynamic ports, and—according to the independent report—the phone must be associated with some Wi-Fi network. |

### Options whose descriptions or statuses are wrong

- **O1 is possible, but “ready” is overstated.** The artifact is staged, but the evidence used `ssl_verify=0`, and no iPhone has exercised the manifest, TLS chain, download, install, upgrade, and data-retention path.

- **O2 is technically possible only as a prompt generator.** A foreground app can check a version file and call `openURL` on the `itms-services` URL. It cannot answer the resulting iOS confirmation. It also cannot reliably run “every few minutes” while backgrounded; the dossier appropriately limits its loop to the foreground.

- **O3’s “away from home: no” is too absolute.** The normal USB/same-LAN route is not remote, but O7-style RemotePairing and tunnel proxying can extend the same underlying developer transport over Tailscale.

- **O4 is possible, but not currently “ready.”** Apple-side builds exist, but the phone’s automatic-update setting is unknown, and the workflow does not automatically run after merges.

- **O5 is technically possible.** Calling it “not viable” is a preference/outcome judgment, not a feasibility finding. Apple Configurator supervision requires physical possession, a connected Mac, and erases the device: “During this process, the device is erased and all data is lost.” [Apple, device supervision](https://support.apple.com/en-gb/guide/deployment/dep1d89f0bff/1/web/1.0). It cannot be enabled remotely on the already-running phone without that reset.

- **O6’s VPN-conflict claim is incomplete.** Stock LocalDevVPN competes with Tailscale as the active packet-tunnel provider. However, SideStore’s RemotePairing path has been made to work through Tailscale using `tailrpproxy`, which “reflects SideStore RPPairing connections through a Tailscale tailnet.” [tailrpproxy](https://github.com/eseiker/tailrpproxy). Therefore “displaces Tailscale while it runs” is not universally true.

- **O6’s zero-tap new-build claim is not demonstrated by stock SideStore.** SideStore’s background refresh renews development provisioning; its user documentation describes an available app-version update as something the user clicks. A custom SideStore/minimuxer-based updater could be built, but the dossier does not establish that ordinary SideStore automatically discovers and installs every new RLM-ADK build without a tap.

- **O7 is technically feasible, not merely a theory.** What remains unverified is this phone’s exact pairing record, current RemotePairing service identity, dynamic tunnel behavior, and reliable operation after network changes or reboots.

No broad option in O1–O7 is categorically impossible. The errors are in exclusivity, readiness, prerequisites, and the claim that O7’s transport is unavailable.

## Answers to the questions (items 2 and 3)

### Item 2 — Confirmation prompts and zero-tap mechanisms

#### Does an `itms-services` install always show Apple’s confirmation?

For the successful, user-initiated website route on this nonsupervised phone: yes. The web page or an app can initiate the request, but iOS owns the installation confirmation. There is no supported manifest parameter, URL parameter, Shortcut action, or normal application API that presses or suppresses it.

“Always” should be understood as “every successful manual installation attempt requires system approval.” Invalid manifests, bad certificates, unreachable files, or ineligible signatures may fail before displaying the confirmation.

MDM and developer-service installations are different system paths. They do not silently accept an `itms-services` sheet; they bypass that web-link flow entirely.

#### Can a normal app install or update another app without the confirmation?

Not through a public UIKit or SwiftUI installation API. Calling `openURL` merely reaches the same system confirmation.

But “no app can do it” is false. SideStore is the direct counterexample: it uses a specially arranged device-protocol connection rather than a public app-install API. Its README says SideStore “resigns apps with your personal development certificate” and then installs them through its VPN/device-protocol path. [SideStore README](https://github.com/SideStore/SideStore).

#### MDM on this unsupervised iPhone

An unsupervised phone can enroll in device management, although enrollment itself requires user participation.

For an ordinary required managed app:

- The first installation prompts on an unsupervised device.
- Taking over an existing unmanaged app also requires the user formally to accept management.
- Once managed, the current Apple deployment guide says updates can be silent unless the app is foregrounded.
- Declarative management can poll approximately every 24 hours and performs iPhone updates while the device is locked.
- Changing a declarative configuration’s server token can request another update check.

Declarative app management is supported on iOS 17.2 and later, so the phone’s iOS 26 version is sufficient.

The blocker for the current package is not supervision; it is the app source. Apple documents declarative iPhone app management for App Store apps and Enterprise-program manifest apps. It does not document an Ad Hoc IPA as a declarative source. The legacy `InstallApplication` command is broader, but current Apple material does not conclusively establish silent legacy-MDM updates of this exact Ad Hoc artifact on iOS 26.

The MDM flags do not change the prompt rules. They control removal-on-unenrollment and backup behavior, not silent installation.

#### Apple Configurator, Device Hub, Xcode, and `devicectl`

These can install or update a correctly signed registered-device IPA without a tap on the phone once trust, pairing, Developer Mode, and required developer services are established.

By themselves they require an attached or locally discoverable paired device. Apple’s current Device Hub documentation says an already cable-paired device can run apps over Wi-Fi on the same network. [Apple, Device Hub pairing](https://developer.apple.com/documentation/xcode/managing-your-simulated-and-physical-devices-in-device-hub).

They become an away-from-home mechanism only if the RemotePairing/CoreDevice transport is bridged over the VPN, as discussed under O7.

#### TestFlight automatic updates

TestFlight can provide genuine zero-tap subsequent updates when all of these are true:

1. TestFlight is installed.
2. The tester has accepted the beta and installed it once.
3. Automatic Updates is enabled globally for new beta apps or specifically for RLM-ADK.
4. The developer makes a processed build available to the tester/group.

Apple does not publish an immediate-install guarantee or exact cadence. The documentation says the latest available beta builds install automatically and that TestFlight notifies the user after installation. It should therefore be treated as background, system-scheduled delivery—not an instantaneous deployment command.

The captured tester state does not prove the automatic-update switch is enabled.

#### SideStore/minimuxer

There are three separate questions here:

- **Does stock SideStore require a development certificate?** Yes. Its normal workflow re-signs the IPA using the user’s personal development certificate. A paid developer account lengthens the provisioning period but does not change the fact that the stock path is development-signed.

- **Does the underlying minimuxer/installation-proxy technique inherently require a development certificate?** No. The installation service requires the submitted IPA to have a signature and provisioning profile valid for that device. A custom installer could, in principle, submit the already-valid Ad Hoc IPA unchanged. Stock SideStore’s re-signing behavior is its workflow, not a fundamental requirement of the transport.

- **Does it coexist with Tailscale?** Stock LocalDevVPN and Tailscale do not ordinarily remain active as two simultaneous personal packet-tunnel VPNs. However, the newer RemotePairing path can use Tailscale itself plus `tailrpproxy`; this avoids running LocalDevVPN as the active tunnel. SideStore issue #1474 reports both RemotePairing over another VPN and a Tailscale-specific workaround. [SideStore issue #1474](https://github.com/SideStore/SideStore/issues/1474).

SideStore’s documented background refresh is not proof of automatic installation of a newer app version. The SideStore FAQ describes OTA updates as user-initiated: “Just click the update button when it appears.” [SideStore FAQ](https://docs.sidestore.io/docs/faq). A custom background updater remains technically conceivable but is not present evidence.

#### Shortcuts and automations

Shortcuts can run some personal automations without asking; Apple says: “Some personal automations can run without asking you for confirmation.” [Apple Shortcuts automation guide](https://support.apple.com/en-euro/guide/shortcuts/apd602971e63/ios).

That only removes the confirmation for running the Shortcut. A Shortcut can fetch metadata or open the `itms-services` URL, but it has no install-IPA action and cannot approve the subsequent protected iOS install sheet. It is therefore another O2-style prompt launcher, not a zero-tap installer.

#### Other zero-tap update channels on a nonsupervised phone

Yes:

- App Store automatic updates.
- TestFlight automatic updates.
- Updates of an already-managed app through applicable MDM/declarative management.
- Remote paired developer-service installation.
- SideStore/minimuxer-style installation after setup, subject to its scheduling and signing limitations.
- Alternative-marketplace automatic updates in supported regions, after initial authorization and Apple notarization.
- Apple’s narrow promptless installation of the MDM management app through `RequiredAppIDForMDM`.

### Item 3 — Developer services over Tailscale and port 62811

#### Can RemotePairing/RSD developer services be reached through the iPhone’s Tailscale interface?

Yes, conditionally.

The default Apple discovery path does not work across Tailscale:

- `_remotepairing._tcp`, `_remoted._tcp`, and `_apple-mobdev2._tcp` are Bonjour services.
- Bonjour multicast does not traverse an ordinary Layer-3 Tailscale tunnel.
- macOS further scopes the RemotePairing connection to the interface on which the service was discovered.

But the service transport can traverse Tailscale once discovery and interface scoping are handled. The current iOS 26 report accomplished this by:

1. Publishing local proxy Bonjour records on the Mac.
2. Pointing them at local TCP/UDP proxy listeners.
3. Relaying RemotePairing and the dynamically chosen trusted-tunnel ports to the iPhone’s tailnet address.
4. Letting `remotepairingd` and the phone perform their authenticated protocol end-to-end through the relay.

The report then used `devicectl` to install the app successfully.

Current `pymobiledevice3` source independently supports the underlying premise: `RemotePairingTunnelService` takes a supplied hostname and port, opens that TCP connection, performs pair verification, and then negotiates a dynamically allocated tunnel. Nothing in that source restricts the address to a physical Wi-Fi subnet.

Conditions and remaining risks:

- The phone needs an appropriate RemotePairing record created during a prior trusted pairing.
- The current RemotePairing port and privacy-preserving Bonjour information must be discovered or captured.
- The client may need a proxy because Apple’s macOS services enforce interface scoping.
- The independent report found that the phone must be associated with some Wi-Fi SSID; pure cellular or Wi-Fi-enabled-but-unassociated did not expose the CoreDevice ports.
- Dynamic tunnel ports must also be relayed, including UDP/QUIC.
- Reboots, RemotePairing identifier/port rotation, and developer disk image state may require recovery or another local pairing session.
- This exact iPhone’s current path remains untested; feasibility is demonstrated elsewhere, not accepted locally.

So O7 is possible, but the dossier’s proposed test needs to be redesigned around RemotePairing discovery and dynamic TCP/UDP tunnel transport. Merely retrying TCP 62078 is not a meaningful O7 test.

#### What is port 62811 likely to be?

Most likely: the phone’s current dynamic `_remotepairing._tcp` listener.

Reasons:

- It is in the dynamic/ephemeral range.
- Current SideStore reports show `_remotepairing._tcp` moving away from 49152 to other ephemeral ports.
- It accepts TCP.
- It is not the Tailscale peer API; the evidence identifies that separately at 45001.
- RemotePairing is client-speaks-first, so the absence of an unsolicited banner, HTTP response, or ordinary TLS ServerHello is expected.

Why it remains uncertain:

- The RemotePairing opening probe timed out.
- The probe did not use an established pair record or full `connect()` flow.
- The `pymobiledevice3` version used for the capture was not recorded.
- No same-moment Bonjour SRV/TXT record tied `_remotepairing._tcp` to 62811.
- The scan tested only TCP, so it did not identify a dynamically allocated QUIC/UDP trusted-tunnel listener.

The defensible label is therefore:

> **62811 is a strong RemotePairing candidate, not an identified service.**

Finally, [dossier.md lines 127–131](dossier.md#L127) says the review findings are in `review.md`, but that file is absent from the repository. That citation cannot be checked.

## Sources

Local sources read:

- [dossier.md](dossier.md#L1)
- [01-tailnet.txt](evidence/01-tailnet.txt#L1), [02-phone-ports-over-tailnet.txt](evidence/02-phone-ports-over-tailnet.txt#L1), [03-app-store-connect.txt](evidence/03-app-store-connect.txt#L1), [04-repo-decisions.txt](evidence/04-repo-decisions.txt#L1), [05-build18-link-route.txt](evidence/05-build18-link-route.txt#L1), and [06-phone-usb-state.txt](evidence/06-phone-usb-state.txt#L1)
- [tailnet_probe.py](evidence/tailnet_probe.py#L1) and [rp_handshake.py](evidence/rp_handshake.py#L1)
- [prototype-runbook.md](../prototype-runbook.md#L325)
- [direct-install.yml](../../../.github/workflows/direct-install.yml#L1), [testflight.yml](../../../.github/workflows/testflight.yml#L1), [direct_install.py](../../../scripts/direct_install.py#L1), and [asc_build_upload.py](../../../scripts/asc_build_upload.py#L1)

Principal public sources:

- [Apple: Distribute managed apps](https://support.apple.com/en-euro/guide/deployment/dep575bfed86/web)
- [Apple: Install Application MDM command](https://developer.apple.com/documentation/devicemanagement/install-application-command)
- [Apple: Declarative managed-app installation and updates](https://developer.apple.com/documentation/devicemanagement/installing-managing-updating-and-removing-apps)
- [Apple: TestFlight for testers](https://testflight.apple.com/)
- [Apple: Distribute to registered devices](https://developer.apple.com/documentation/xcode/distributing-your-app-to-registered-devices)
- [Apple: Manage devices and wireless pairing in Device Hub](https://developer.apple.com/documentation/xcode/managing-your-simulated-and-physical-devices-in-device-hub)
- [Apple: Ad Hoc/Enterprise/Development OTA manifest option](https://help.apple.com/xcode/mac/current/en.lproj/devde46df08a.html)
- [Apple: Device supervision](https://support.apple.com/en-gb/guide/deployment/dep1d89f0bff/1/web/1.0)
- [pymobiledevice3: RemoteXPC and RemotePairing](https://github.com/doronz88/pymobiledevice3/blob/master/misc/RemoteXPC.md)
- [pymobiledevice3: RemotePairing source](https://raw.githubusercontent.com/doronz88/pymobiledevice3/master/pymobiledevice3/remote/tunnel_service.py)
- [Remote iOS deployment over Tailscale report](https://dev.to/kvnpt/how-to-remotely-iterate-deploy-your-sideloaded-ios-apps-over-tailnet-jak)
- [SideStore prerequisites](https://docs.sidestore.io/docs/installation/prerequisites), [SideStore repository](https://github.com/SideStore/SideStore), and [tailrpproxy](https://github.com/eseiker/tailrpproxy)

