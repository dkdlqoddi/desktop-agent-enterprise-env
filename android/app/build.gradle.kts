import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// The page scripts and the site list are shared with the desktop app (../shared/web-capture). They are never copied
// into this project by hand: every build copies the current files into a generated assets directory of each variant,
// so the APK always carries what the desktop app runs.
val webCaptureDir: Directory = rootProject.layout.projectDirectory.dir("../shared/web-capture")
val webCaptureFiles = listOf("capture.js", "click-watch.js", "sites.json")

abstract class CopyWebCaptureAssets : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.NAME_ONLY)
    abstract val sources: ConfigurableFileCollection

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val target = outputDir.get().asFile.resolve("web-capture")
        target.deleteRecursively()
        target.mkdirs()
        for (file in sources.files) {
            if (!file.isFile) throw GradleException("Missing shared page script: ${file.path}")
            file.copyTo(target.resolve(file.name), overwrite = true)
        }
    }
}

android {
    namespace = "dev.privateagent.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.privateagent.android"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

androidComponents {
    onVariants { variant ->
        val copyTask = tasks.register<CopyWebCaptureAssets>(
            "copy${variant.name.replaceFirstChar { it.uppercase() }}WebCaptureAssets",
        ) {
            sources.from(webCaptureFiles.map { webCaptureDir.file(it) })
        }
        // Registered as a generated source directory: the variant's merge*Assets task depends on the copy.
        variant.sources.assets?.addGeneratedSourceDirectory(copyTask, CopyWebCaptureAssets::outputDir)
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

// The JVM unit tests read the shared site list itself, not a copy.
tasks.withType<Test>().configureEach {
    inputs.files(webCaptureFiles.map { webCaptureDir.file(it) }).withPathSensitivity(PathSensitivity.NAME_ONLY)
    systemProperty("webCaptureDir", webCaptureDir.asFile.absolutePath)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.work.runtime)
    implementation(libs.kotlinx.coroutines.android)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.core)

    testImplementation(libs.junit)
    // The real org.json for the JVM tests: android.jar only has stubs of it.
    testImplementation(libs.org.json)

    androidTestImplementation(libs.junit)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.rules)
    androidTestImplementation(libs.androidx.test.uiautomator)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    // Declares androidx.activity.ComponentActivity in debug builds, which the instrumented tests host their views in.
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}
