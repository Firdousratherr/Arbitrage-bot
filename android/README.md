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

A release build requires the production mobile API to be deployed over HTTPS and configured explicitly:

```bash
gradle assembleRelease -PMOBILE_API_BASE_URL=https://your-real-api-domain/
```

Configure a release signing key in a secure build environment before distribution. Do not commit keystores, credentials or secrets. The sample API hostname is deliberately non-working until a real endpoint is supplied.

## App features

- Live cached opportunity list, search, gross-spread and volume filters, sorting, stale-data states.
- Opportunity detail with multi-level order-book sizing, displayed taker fee metadata, trading-fee-only estimate and conservative transfer route verification.
- Device-local watchlist, watchlist notifications while the app is active, and local paper-trade snapshots.
- Exchange status screen and configurable API URL.

This app is read-only. It cannot place exchange orders or initiate withdrawals. Watchlist alerts are local foreground polling; push notifications and multi-device account sync are not configured in this MVP.
