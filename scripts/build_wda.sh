#!/usr/bin/env bash
# Build only the pinned UI runner. Does not upload an app to Apple or install on a phone.
set -euo pipefail
: "${WDA_COMMIT:?}" "${WDA_TAG:?}" "${WDA_BUNDLE:?}" "${DEVICE_UDID:?}"
: "${WDA_DEV_P12_BASE64:?}" "${WDA_DEV_P12_PASSWORD:?}" "${WDA_DEV_PROFILE_BASE64:?}"
mkdir -p build/wda-publish
ROOT="$PWD"
STAGE="$RUNNER_TEMP/wda-signing"
mkdir -p "$STAGE"
printf '%s' "$WDA_DEV_P12_BASE64" | base64 --decode > "$STAGE/development.p12"
printf '%s' "$WDA_DEV_PROFILE_BASE64" | base64 --decode > "$STAGE/runner.mobileprovision"
security cms -D -i "$STAGE/runner.mobileprovision" > "$STAGE/profile.plist"
export STAGE
python3 - <<'PY'
import datetime,json,os,pathlib,plistlib
p=pathlib.Path(os.environ['STAGE'])
v=plistlib.loads((p/'profile.plist').read_bytes()); e=v['Entitlements']
assert os.environ['DEVICE_UDID'] in v['ProvisionedDevices'], 'phone absent from profile'
assert v['ExpirationDate'] > datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
assert e.get('get-task-allow') is True, 'runner needs development profile'
team=v['TeamIdentifier'][0]; assert team=='4PJBKY2WQR'
app_id=team+'.'+os.environ['WDA_BUNDLE']; allowed=e['application-identifier']
assert app_id==allowed or (allowed.endswith('*') and app_id.startswith(allowed[:-1]))
ent={**e,'application-identifier':app_id,'keychain-access-groups':[app_id]}
(p/'entitlements.plist').write_bytes(plistlib.dumps(ent))
receipt={'team_id':team,'profile_uuid':v['UUID'],'profile_name':v['Name'],
 'profile_expiry':v['ExpirationDate'].isoformat()+'Z','device_udid':os.environ['DEVICE_UDID'],
 'bundle_id':os.environ['WDA_BUNDLE'],'development_signed':True,
 'wda_commit':os.environ['WDA_COMMIT'],'wda_tag':os.environ['WDA_TAG'],
 'repository_commit':os.environ['GITHUB_SHA']}
pathlib.Path('build/wda-publish/receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
PY
KPW=$(uuidgen)
security create-keychain -p "$KPW" "$STAGE/build.keychain"
security set-keychain-settings -lut 7200 "$STAGE/build.keychain"
security unlock-keychain -p "$KPW" "$STAGE/build.keychain"
security import "$STAGE/development.p12" -k "$STAGE/build.keychain" -P "$WDA_DEV_P12_PASSWORD" -T /usr/bin/codesign
curl -fsS -o "$STAGE/wwdr.cer" https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer
security import "$STAGE/wwdr.cer" -k "$STAGE/build.keychain"
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KPW" "$STAGE/build.keychain" >/dev/null
security list-keychains -d user -s "$STAGE/build.keychain" ~/Library/Keychains/login.keychain-db
IDENTITY=$(security find-identity -v -p codesigning "$STAGE/build.keychain" | awk '/Apple Development/ {print $2; exit}')
test -n "$IDENTITY"
git clone --quiet https://github.com/appium/WebDriverAgent.git "$STAGE/source"
git -C "$STAGE/source" checkout --quiet "$WDA_COMMIT"
test "$(git -C "$STAGE/source" rev-parse HEAD)" = "$WDA_COMMIT"
xcodebuild -version > build/wda-publish/xcode-version.txt
xcodebuild build-for-testing -project "$STAGE/source/WebDriverAgent.xcodeproj" \
  -scheme WebDriverAgentRunner -derivedDataPath "$STAGE/derived" \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO ARCHS=arm64 \
  > build/wda-build.log 2>&1 || { tail -60 build/wda-build.log; exit 1; }
mkdir -p "$STAGE/package/Payload"
APP="$STAGE/package/Payload/WebDriverAgentRunner-Runner.app"
cp -R "$STAGE/derived/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app" "$APP"
# Follow pinned upstream Scripts/ci/build-real.sh: device supplies XCTest libraries.
rm -rf "$APP/Frameworks"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $WDA_BUNDLE" "$APP/Info.plist"
cp "$STAGE/runner.mobileprovision" "$APP/embedded.mobileprovision"
# Sign nested code inside-out. Only the executable application receives profile entitlements.
while IFS= read -r -d '' nested; do
  codesign --force --sign "$IDENTITY" --timestamp=none "$nested"
done < <(find "$APP" -depth \( -name '*.framework' -o -name '*.dylib' -o -name '*.xctest' \) -print0)
codesign --force --sign "$IDENTITY" --entitlements "$STAGE/entitlements.plist" --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=2 "$APP" 2> build/wda-publish/codesign-verify.txt
codesign -dvv "$APP" 2> build/wda-publish/codesign-details.txt
codesign -d --entitlements :- "$APP" > build/wda-publish/entitlements.plist 2>/dev/null
(cd "$STAGE/package" && zip -qry "$ROOT/build/wda-publish/WDA.ipa" Payload)
python3 - <<'PY'
import hashlib,json,pathlib
p=pathlib.Path('build/wda-publish'); f=p/'WDA.ipa'
r=json.loads((p/'receipt.json').read_text());r.update(ipa_sha256=hashlib.sha256(f.read_bytes()).hexdigest(),ipa_bytes=f.stat().st_size)
(p/'receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
PY
