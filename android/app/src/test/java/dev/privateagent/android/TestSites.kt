package dev.privateagent.android

import dev.privateagent.android.web.Sites
import java.io.File

/** The shared site list itself (../shared/web-capture/sites.json), which Gradle names in a system property. */
object TestSites {
    val file: File by lazy {
        val dir = System.getProperty("webCaptureDir") ?: error("webCaptureDir is not set; run the tests with Gradle")
        File(dir, "sites.json")
    }

    val sites: Sites by lazy { Sites.parse(file.readText(Charsets.UTF_8)) }
}
