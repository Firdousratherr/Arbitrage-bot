import java.net.URI

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val releaseApiBaseUrl = providers.gradleProperty("MOBILE_API_BASE_URL")
    .orElse("https://api.example.invalid/")
    .get()
    .trim()
val escapedReleaseApiBaseUrl = releaseApiBaseUrl
    .replace("\\", "\\\\")
    .replace("\"", "\\\"")

val signingStoreFile = providers.environmentVariable("ANDROID_KEYSTORE_PATH").orNull
val signingStorePassword = providers.environmentVariable("ANDROID_KEYSTORE_PASSWORD").orNull
val signingKeyAlias = providers.environmentVariable("ANDROID_KEY_ALIAS").orNull
val signingKeyPassword = providers.environmentVariable("ANDROID_KEY_PASSWORD").orNull
val releaseSigningConfigured = listOf(
    signingStoreFile, signingStorePassword, signingKeyAlias, signingKeyPassword
).all { !it.isNullOrBlank() }

android {
    namespace = "com.firdousratherr.arbitragescanner"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.firdousratherr.arbitragescanner"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.2.0"
        buildConfigField("String", "API_BASE_URL", "\"https://api.example.invalid/\"")
    }

    signingConfigs {
        create("release") {
            if (releaseSigningConfigured) {
                storeFile = file(signingStoreFile!!)
                storePassword = signingStorePassword
                keyAlias = signingKeyAlias
                keyPassword = signingKeyPassword
            }
        }
    }

    buildTypes {
        debug {
            buildConfigField("String", "API_BASE_URL", "\"http://10.0.2.2:8000/\"")
        }
        release {
            signingConfig = signingConfigs.getByName("release")
            buildConfigField("String", "API_BASE_URL", "\"$escapedReleaseApiBaseUrl\"")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

tasks.register("verifyReleaseConfiguration") {
    group = "verification"
    description = "Rejects release builds without a real HTTPS API and configured signing key."
    doLast {
        val apiUri = try {
            URI(releaseApiBaseUrl)
        } catch (exception: Exception) {
            throw GradleException("MOBILE_API_BASE_URL must be a valid HTTPS URL.", exception)
        }
        require(
            apiUri.scheme.equals("https", ignoreCase = true) &&
                !apiUri.host.isNullOrBlank() &&
                !apiUri.host.endsWith(".invalid", ignoreCase = true) &&
                apiUri.userInfo == null
        ) {
            "Release build blocked: pass -PMOBILE_API_BASE_URL=https://your-real-api-domain/ (HTTPS, no embedded credentials)."
        }
        require(releaseSigningConfigured && file(signingStoreFile!!).isFile) {
            "Release build blocked: configure ANDROID_KEYSTORE_PATH, ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_ALIAS, and ANDROID_KEY_PASSWORD in the secure build environment."
        }
    }
}

tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    dependsOn("verifyReleaseConfiguration")
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.10.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
