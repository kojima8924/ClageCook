import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")
if (keystorePropertiesFile.exists()) {
    FileInputStream(keystorePropertiesFile).use(keystoreProperties::load)
}

val signingEnvironment = mapOf(
    "storeFile" to System.getenv("CLAGE_ANDROID_KEYSTORE")?.trim(),
    "storePassword" to System.getenv("CLAGE_ANDROID_STORE_PASSWORD")?.trim(),
    "keyAlias" to System.getenv("CLAGE_ANDROID_KEY_ALIAS")?.trim(),
    "keyPassword" to System.getenv("CLAGE_ANDROID_KEY_PASSWORD")?.trim(),
)
val signingEnvironmentRequested = signingEnvironment.values.any { !it.isNullOrEmpty() }
val signingEnvironmentComplete = signingEnvironment.values.all { !it.isNullOrEmpty() }
if (signingEnvironmentRequested && !signingEnvironmentComplete) {
    throw GradleException(
        "Android release signing environment is incomplete. Set all CLAGE_ANDROID_* values.",
    )
}

val signingPropertiesRequired = listOf("storeFile", "storePassword", "keyAlias", "keyPassword")
if (keystorePropertiesFile.exists() && signingPropertiesRequired.any {
        keystoreProperties.getProperty(it).isNullOrBlank()
    }) {
    throw GradleException("android/key.properties is incomplete.")
}

android {
    namespace = "jp.akoji.clage_cook"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "jp.akoji.clage_cook"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (signingEnvironmentComplete || keystorePropertiesFile.exists()) {
            create("release") {
                keyAlias = signingEnvironment["keyAlias"]
                    ?: keystoreProperties.getProperty("keyAlias")
                keyPassword = signingEnvironment["keyPassword"]
                    ?: keystoreProperties.getProperty("keyPassword")
                storeFile = file(
                    signingEnvironment["storeFile"]
                        ?: keystoreProperties.getProperty("storeFile"),
                )
                storePassword = signingEnvironment["storePassword"]
                    ?: keystoreProperties.getProperty("storePassword")
            }
        }
    }

    buildTypes {
        release {
            // Never distribute an APK signed with Flutter's shared debug key.
            // Without android/key.properties Gradle intentionally emits unsigned output.
            signingConfig = signingConfigs.findByName("release")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
