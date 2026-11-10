package dev.privateagent.android.web

import dev.privateagent.android.util.objectOrNull
import dev.privateagent.android.util.arrayOrNull
import dev.privateagent.android.util.strings
import dev.privateagent.android.util.text
import org.json.JSONException
import org.json.JSONObject
import java.net.URI
import java.net.URISyntaxException
import java.net.URLDecoder
import java.net.URLEncoder

/** A site of the shared site list (shared/web-capture/sites.json): its search page template and its hosts. */
data class Site(
    val key: String,
    val name: String,
    val home: String,
    val search: String,
    val hosts: List<String>,
)

/** The sites the agent knows, read from the shared sites.json that the build copies into the assets. */
class Sites(val all: List<Site>) {
    val keys: List<String> get() = all.map { it.key }

    fun byKey(key: String?): Site? = all.firstOrNull { it.key == key }

    /** The site a person or the LLM named: its key or its display name, in any letter case. */
    fun resolve(value: String): Site? {
        val name = value.trim()
        if (name.isEmpty()) return null
        return all.firstOrNull { it.key.equals(name, ignoreCase = true) || it.name.equals(name, ignoreCase = true) }
    }

    /** The site a page belongs to, by its host (a subdomain of a listed host counts), or null. */
    fun forUrl(url: String): Site? {
        val host = hostOf(url) ?: return null
        return all.firstOrNull { site -> site.hosts.any { host == it || host.endsWith(".$it") } }
    }

    /** The site's search page for [query]. */
    fun searchUrl(site: Site, query: String): String = site.search.replace(QUERY, encodeQuery(query))

    /** The search words of a site's search page (the parameter its template puts the query in), or null. */
    fun queryFromUrl(site: Site, url: String): String? {
        val parameter = queryParameter(site.search) ?: return null
        val rawQuery = try {
            URI(url).rawQuery
        } catch (_: URISyntaxException) {
            null
        } ?: return null
        for (pair in rawQuery.split('&')) {
            val name = pair.substringBefore('=')
            if (name == parameter && pair.contains('=')) {
                return try {
                    URLDecoder.decode(pair.substringAfter('='), "UTF-8").trim().ifEmpty { null }
                } catch (_: IllegalArgumentException) {
                    null
                }
            }
        }
        return null
    }

    companion object {
        const val QUERY = "{query}"
        private val KEY = Regex("^[a-z0-9_-]{1,32}$")

        /** The site list of a sites.json text; entries without an https search template or home are left out. */
        fun parse(json: String): Sites {
            val root = try {
                JSONObject(json)
            } catch (_: JSONException) {
                return Sites(emptyList())
            }
            val sites = mutableListOf<Site>()
            for (key in root.keys()) {
                val entry = root.objectOrNull(key) ?: continue
                val search = entry.text("search")
                val home = entry.text("home")
                if (!KEY.matches(key) || !search.startsWith("https://") || !search.contains(QUERY)) continue
                if (!home.startsWith("https://")) continue
                val hosts = entry.arrayOrNull("hosts")?.strings()?.map { it.lowercase() }.orEmpty()
                sites += Site(key, entry.text("name").ifEmpty { key }, home, search, hosts)
            }
            return Sites(sites)
        }

        /** A query as a URL query value: UTF-8, with spaces as %20. */
        fun encodeQuery(query: String): String = URLEncoder.encode(query, "UTF-8").replace("+", "%20")

        fun hostOf(url: String): String? = try {
            URI(url).host?.lowercase()?.trimEnd('.')
        } catch (_: URISyntaxException) {
            null
        }

        /** The name of the query parameter whose value is {query} in a search template. */
        fun queryParameter(template: String): String? {
            val query = template.substringAfter('?', "")
            return query.split('&').firstOrNull { it.substringAfter('=', "") == QUERY }?.substringBefore('=')
        }
    }
}
