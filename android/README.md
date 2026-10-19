# ArbiScope Android App

An original, read-only cryptocurrency arbitrage scanner client built with Kotlin and Jetpack Compose. It is not affiliated with ArbiHunt and does not copy its branding or assets.

## Features currently implemented in this branch

- Live cross-exchange opportunity cards from the independent mobile API.
- Search by market symbol or exchange; sort by estimated profit before transfer costs, raw spread, liquidity or freshness.
- Filters for minimum spread, visible liquidity, quote currency and enabled exchanges.
- Detail screen with buy/sell legs, order-book average/top prices, fee rate and fee source, estimated size, and explicit unknown transfer/network/contract checks.
- Three-leg same-exchange triangular loop screen using ticker prices and fee assumptions.
- Local saved-opportunity IDs, foreground refresh, theme selection, API URL configuration, and optional periodic notifications.
- Clear connection/error/empty states and risk disclaimers.

## Prerequisites

- Android Studio or JDK 17, Android SDK Platform 35, and Gradle 8.9+.
- A running instance of the independent service in ../mobile-api.

## Build in Android Studio

Open the android/ folder as a project, let Gradle sync, then run the app on an emulator or device.

## Build from command line

If Gradle 8.10.2 is installed and Android SDK is configured:

~~~bash
cd android
gradle :app:assembleDebug
~~~

The debug APK is generated at app/build/outputs/apk/debug/app-debug.apk.

GitHub Actions also builds and uploads an APK artifact for this branch. It is a debug/testing artifact, not a Play Store release or a production-signed package.

## Connect the app

- Android emulator: http://10.0.2.2:8000
- Physical phone: use the backend server's HTTPS URL, or a LAN IP address reachable from the phone for local testing.
- Start the API with instructions in ../mobile-api/README.md.
- In the app, open Settings, enter the API base URL, then select Save URL and test connection.

The manifest allows cleartext HTTP to support local development. Before a public or production release, configure HTTPS and disable cleartext traffic. Never expose an unprotected API on the open internet.

## Notifications

Foreground refresh uses the user-selected interval. Android background notifications use WorkManager, subject to Android battery/network policies, and run at a minimum periodic interval of 15 minutes (not every 30 seconds). Notification permission is requested on Android 13 and later. Notifications describe pre-transfer estimates; they do not mean a trade is safe or profitable.

## Current limitations

- The backend reports cross-exchange profit before withdrawal/transfer costs when current transfer fees are not available. It marks the route as needing manual verification rather than assuming fees or network status are zero/verified.
- Triangular results are three-leg top-of-book estimates, not a full-depth execution simulation; four-leg cycle support is not implemented yet.
- The initial implementation has no login/subscription system and does not synchronize the local saved list between devices.
- The scanner is limited to configured exchanges and candidate caps; enabling 29 exchanges at once requires deliberate rate-limit and resource testing.
- No real trading, wallet connection, withdrawals or exchange API key entry is implemented.
