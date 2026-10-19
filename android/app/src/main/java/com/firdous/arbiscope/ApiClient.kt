package com.firdous.arbiscope

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

object ApiClient {
    private fun encode(value: String): String = URLEncoder.encode(value, Charsets.UTF_8.name())

    private suspend fun getJson(url: String): JSONObject = withContext(Dispatchers.IO) {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 6000
            readTimeout = 12000
            setRequestProperty("Accept", "application/json")
        }
        try {
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val body = stream.bufferedReader().use(BufferedReader::readText)
            if (code !in 200..299) {
                val message = runCatching { JSONObject(body).optString("detail") }.getOrDefault(body)
                throw IllegalStateException("API returned HTTP " + code + ": " + message)
            }
            JSONObject(body)
        } finally {
            connection.disconnect()
        }
    }

    suspend fun opportunities(
        baseUrl: String,
        q: String,
        sort: String,
        minSpread: String,
        minLiquidity: String,
        quote: String,
        exchanges: Set<String>
    ): OpportunityResponse {
        val params = mutableListOf(
            "q=" + encode(q),
            "sort=" + encode(sort),
            "min_spread=" + encode(minSpread.toDoubleOrNull()?.coerceAtLeast(0.0)?.toString() ?: "0"),
            "min_liquidity=" + encode(minLiquidity.toDoubleOrNull()?.coerceAtLeast(0.0)?.toString() ?: "0"),
            "limit=200"
        )
        if (quote.isNotBlank()) params += "quote=" + encode(quote.trim().uppercase())
        if (exchanges.isNotEmpty()) params += "exchanges=" + encode(exchanges.joinToString(","))
        val root = getJson(baseUrl.trimEnd('/') + "/api/v1/opportunities?" + params.joinToString("&"))
        val items = root.optJSONArray("items").toOpportunityList()
        val states = root.optJSONArray("exchange_status").toExchangeStatusList()
        return OpportunityResponse(
            items = items,
            updatedAt = root.optString("updated_at"),
            stale = root.optBoolean("stale", true),
            count = root.optInt("count", items.size),
            exchangeStatus = states
        )
    }

    suspend fun triangles(baseUrl: String, q: String = ""): TriangleResponse {
        val root = getJson(baseUrl.trimEnd('/') + "/api/v1/triangular?limit=200&q=" + encode(q))
        val array = root.optJSONArray("items") ?: JSONArray()
        val rows = (0 until array.length()).mapNotNull { index ->
            array.optJSONObject(index)?.let(TriangleOpportunity::fromJson)
        }
        return TriangleResponse(
            items = rows,
            updatedAt = root.optString("updated_at"),
            stale = root.optBoolean("stale", true),
            count = root.optInt("count", rows.size)
        )
    }

    suspend fun health(baseUrl: String): JSONObject = getJson(baseUrl.trimEnd('/') + "/health")

    private fun JSONArray?.toOpportunityList(): List<Opportunity> {
        val array = this ?: return emptyList()
        return (0 until array.length()).mapNotNull { index ->
            array.optJSONObject(index)?.let(Opportunity::fromJson)
        }
    }

    private fun JSONArray?.toExchangeStatusList(): List<ExchangeStatus> {
        val array = this ?: return emptyList()
        return (0 until array.length()).mapNotNull { index ->
            array.optJSONObject(index)?.let(ExchangeStatus::fromJson)
        }
    }
}
