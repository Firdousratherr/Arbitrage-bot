# Android scanner

Native Kotlin + Jetpack Compose Android application for the independent mobile API.

## Local debug build

The Android emulator debug build defaults to `http://10.0.2.2:8000/`, which points to port 8000 on the development computer. Start the mobile API from the repository root before opening the app:

```bash
python -m pip install -e .
python -m pip install -r mobile_api/requirements.txt
MOBILE_API_DATABASE_PATH=./data/mobile-api.sqlite3 uvicorn mobile_api.app:app --host 0.0.0.0 --port 8000
```

Build with Android SDK, Java 17 and Gradle 8.9:

```bash
gradle assembleDebug
```

Output: `app/build/outputs/apk/debug/app-debug.apk`.

For a physical Android device, use the computer's LAN IP in Settings and make sure the device can reach the development server. Local HTTP is enabled only for debug builds. Do not expose a development API directly to the public internet.

## Release build

Release builds are deliberately blocked until both a real HTTPS API URL and signing credentials are configured. The sample API hostname is deliberately non-working and cannot pass release verification.

Set these variables in the secure build environment; never commit them or print their values in CI logs:

- `ANDROID_KEYSTORE_PATH`: path to the release keystore file.
- `ANDROID_KEYSTORE_PASSWORD`: keystore password.
- `ANDROID_KEY_ALIAS`: key alias.
- `ANDROID_KEY_PASSWORD`: key password.

Then build with the real HTTPS API URL:

```bash
gradle assembleRelease -PMOBILE_API_BASE_URL=https://your-real-api-domain/
```

The release verification task rejects non-HTTPS URLs, placeholder `.invalid` hosts, embedded URL credentials, missing signing variables, and missing keystore files. The API must be deployed and security-reviewed separately before a release build can be useful. Do not put exchange keys, Telegram tokens, or other secrets in the APK.

## App features

- Live cached opportunity list, search, gross-spread and volume filters, sorting, stale-data states.
- Opportunity detail with multi-level order-book sizing, displayed taker fee metadata, trading-fee-only estimate and conservative transfer route verification.
- Device-local watchlist, watchlist notifications while the app is active, and local paper-trade snapshots.
- Exchange status screen and configurable API URL.

This app is read-only. It cannot place exchange orders or initiate withdrawals. Watchlist alerts are local foreground polling; push notifications and multi-device account sync are not configured in this MVP.
