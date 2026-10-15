You are reviewing a design dossier for FEASIBILITY ONLY. Read
docs/voice-portal/update-delivery/dossier.md and every file it cites (evidence/*.txt, the probe
scripts, docs/voice-portal/prototype-runbook.md, .github/workflows/direct-install.yml,
.github/workflows/testflight.yml, scripts/direct_install.py, scripts/asc_build_upload.py).

Context: RLM-ADK is a private iOS app for one person's iPhone 12 (iOS 26, not supervised, Developer
Mode on, Tailscale VPN app on, registered in an Ad Hoc profile). The owner wants new builds to reach
the phone automatically while he is away from home: no USB cable, no submitting to Apple, ideally no
tap at all. He chose earlier to use direct Ad Hoc installation instead of TestFlight.

Do NOT judge options by best practice, taste, or what you would recommend. Judge only what is
technically possible and what is not, on this phone, today. For each claim the owner needs to know
whether it is actually the only way, or whether it is wrong.

Do this:
1. For each conclusion C1 to C7: CONFIRMED, REFUTED or UNCERTAIN, with the evidence (file and line,
   or a URL you actually opened, with the sentence quoted). Check that every cited line and file
   says what the dossier claims.
2. For C4 and C5 especially: is it true that an itms-services install always shows Apple's
   confirmation and that no app can install or update an app without it? Is there any mechanism on
   a NON-supervised iPhone that installs or updates an app with zero user taps, other than those in
   C5? Consider at least: MDM on unsupervised devices (including updates of an already-managed app,
   InstallApplication flags, declarative app management on iOS 17+), Apple Configurator, Xcode/
   devicectl wireless, TestFlight automatic updates (is it really zero-tap and when does it run?),
   the SideStore/minimuxer loopback-VPN technique (does it coexist with Tailscale on iOS? does it
   need a development certificate?), Shortcuts or automation, and anything else you know of.
3. For C6 and option O7: can iOS developer services (lockdownd Wi-Fi connections on 62078,
   RemotePairing / remoted / RSD tunnels used by Xcode wireless debugging) be reached through a VPN
   interface such as Tailscale's on iOS, i.e. at the phone's tailnet address from a remote network?
   Cite reports or source code. What is port 62811 likely to be, given the evidence?
4. List any option that is possible but missing from section 2, and any option listed as possible
   that is not.

Rules: read-only. Do not modify files, do not contact the iPhone or any host on the tailnet, do not
call the App Store Connect API. Use web search for public sources and quote what you rely on.
Write your answer in plain English as markdown with headings: Verdicts (C1-C7), Missed or wrong
options, Answers to the questions (items 2 and 3), Sources.
