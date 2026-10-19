package com.firdousratherr.arbitragescanner

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private const val PREFS = "arbitrage_scanner_preferences"
private const val CHANNEL_ID = "arbitrage_watchlist_alerts"
private const val ALERT_COOLDOWN = 30L * 60L * 1000L
private val Background = Color(0xFF0B1020)
private val Panel = Color(0xFF151D31)
private val Accent = Color(0xFF8B9DFF)
private val Muted = Color(0xFF9CA9C6)
private val Positive = Color(0xFF72E0B0)
private val Warning = Color(0xFFFFC879)

data class OpportunityRow(
    val symbol: String, val buyExchange: String, val sellExchange: String,
    val buyAsk: Double, val sellBid: Double, val spread: Double, val estimate: Double,
    val buyVolume: Double, val sellVolume: Double,
    val warning: String, val observedAt: String, val ageSeconds: Double?, val coverageComplete: Boolean
) {
    val key: String get() = "$symbol|$buyExchange|$sellExchange"
}
data class ExchangeRow(val id: String, val status: String, val markets: Int?, val tickers: Int?, val error: String?)
data class PaperTradeRow(val id: String, val symbol: String, val route: String, val size: Double, val profit: Double?, val quote: String, val savedAt: String)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Watchlist alerts", NotificationManager.IMPORTANCE_DEFAULT)
                    .apply { description = "Alerts for saved routes above your spread threshold." }
            )
        }
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(
                primary = Accent, background = Background, surface = Panel,
                onBackground = Color.White, onSurface = Color.White, secondary = Positive
            )) { ScannerApp() }
        }
    }
}

private fun normalizeUrl(value: String) = if (value.trim().endsWith("/")) value.trim() else value.trim() + "/"
private fun enc(value: String) = URLEncoder.encode(value, "UTF-8")
private fun fmt(value: Double) = String.format(Locale.US, "%,.8f", value).trimEnd('0').trimEnd('.').ifBlank { "0" }
private fun compactVolume(value: Double): String = when {
    value <= 0 -> "unknown"
    value >= 1_000_000_000 -> String.format(Locale.US, "%.2fB", value / 1_000_000_000)
    value >= 1_000_000 -> String.format(Locale.US, "%.2fM", value / 1_000_000)
    value >= 1_000 -> String.format(Locale.US, "%.2fK", value / 1_000)
    else -> String.format(Locale.US, "%.0f", value)
}
private fun ageLabel(seconds: Double?): String {
    if (seconds == null) return "unknown"
    return when {
        seconds < 60 -> seconds.toInt().toString() + "s"
        seconds < 3600 -> (seconds / 60).toInt().toString() + "m"
        else -> (seconds / 3600).toInt().toString() + "h"
    }
}

private suspend fun getJson(baseUrl: String, path: String): JSONObject = withContext(Dispatchers.IO) {
    val connection = URL(normalizeUrl(baseUrl) + path.trimStart('/')).openConnection() as HttpURLConnection
    connection.connectTimeout = 8_000
    connection.readTimeout = 12_000
    connection.requestMethod = "GET"
    connection.setRequestProperty("Accept", "application/json")
    try {
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
        if (code !in 200..299) {
            val detail = try { JSONObject(body).optString("detail", body) } catch (_: Exception) { body }
            throw IOException("HTTP $code: " + detail.ifBlank { "API request failed" })
        }
        JSONObject(body)
    } finally { connection.disconnect() }
}

private fun parseRow(item: JSONObject) = OpportunityRow(
    symbol = item.optString("symbol", "?"),
    buyExchange = item.optString("buy_exchange", "?"),
    sellExchange = item.optString("sell_exchange", "?"),
    buyAsk = item.optDouble("buy_ask", 0.0),
    sellBid = item.optDouble("sell_bid", 0.0),
    spread = item.optDouble("gross_spread_pct", 0.0),
    estimate = item.optDouble("provisional_fee_adjusted_spread_pct", 0.0),
    buyVolume = item.optDouble("buy_quote_volume_24h", 0.0),
    sellVolume = item.optDouble("sell_quote_volume_24h", 0.0),
    warning = item.optJSONArray("warnings")?.optString(0) ?: "Provisional estimate.",
    observedAt = item.optString("observed_at", ""),
    ageSeconds = if (item.has("data_age_seconds") && !item.isNull("data_age_seconds")) item.optDouble("data_age_seconds") else null,
    coverageComplete = item.optBoolean("coverage_complete", false)
)

private fun readFavorites(context: Context) =
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getStringSet("favorites", emptySet())?.toSet() ?: emptySet()

private fun readPaperTrades(context: Context): List<PaperTradeRow> {
    val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("paper_trades", "[]") ?: "[]"
    return try {
        val a = JSONArray(raw)
        (0 until a.length()).mapNotNull { index ->
            val o = a.optJSONObject(index) ?: return@mapNotNull null
            PaperTradeRow(
                o.optString("id"), o.optString("symbol"), o.optString("route"), o.optDouble("size"),
                if (o.has("profit") && !o.isNull("profit")) o.optDouble("profit") else null,
                o.optString("quote", "quote currency"), o.optString("saved_at")
            )
        }.reversed()
    } catch (_: Exception) { emptyList() }
}

private fun savePaperTrade(context: Context, detail: JSONObject): List<PaperTradeRow> {
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val arr = try { JSONArray(prefs.getString("paper_trades", "[]") ?: "[]") } catch (_: Exception) { JSONArray() }
    val profit: Any = if (detail.has("profit_after_known_fees") && !detail.isNull("profit_after_known_fees"))
        detail.optDouble("profit_after_known_fees") else JSONObject.NULL
    val symbol = detail.optString("symbol", "Unknown")
    arr.put(JSONObject()
        .put("id", System.currentTimeMillis().toString() + symbol)
        .put("symbol", symbol)
        .put("route", detail.optString("buy_exchange") + " → " + detail.optString("sell_exchange"))
        .put("size", detail.optDouble("requested_trade_size_quote", 0.0))
        .put("profit", profit)
        .put("quote", detail.optString("quote_currency", "quote currency"))
        .put("saved_at", SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date())))
    val bounded = JSONArray()
    for (i in maxOf(0, arr.length() - 100) until arr.length()) bounded.put(arr.get(i))
    prefs.edit().putString("paper_trades", bounded.toString()).apply()
    return readPaperTrades(context)
}

@SuppressLint("MissingPermission")
private fun notifySavedRoutes(context: Context, rows: List<OpportunityRow>, saved: Set<String>, threshold: Double) {
    if (Build.VERSION.SDK_INT >= 33 &&
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val times = try { JSONObject(prefs.getString("last_alert_times", "{}") ?: "{}") } catch (_: Exception) { JSONObject() }
    var changed = false
    val now = System.currentTimeMillis()
    rows.filter { it.key in saved && it.spread >= threshold }.forEach { row ->
        if (now - times.optLong(row.key, 0L) < ALERT_COOLDOWN) return@forEach
        val message = row.symbol + ": " + String.format(Locale.US, "%.2f%% gross spread", row.spread) +
            " on " + row.buyExchange + " → " + row.sellExchange + ". Not guaranteed profit."
        try {
            NotificationManagerCompat.from(context).notify(
                row.key.hashCode(),
                NotificationCompat.Builder(context, CHANNEL_ID)
                    .setSmallIcon(android.R.drawable.ic_dialog_info)
                    .setContentTitle("Saved arbitrage route")
                    .setContentText(message)
                    .setStyle(NotificationCompat.BigTextStyle().bigText(message))
                    .setAutoCancel(true).build()
            )
            times.put(row.key, now)
            changed = true
        } catch (_: SecurityException) { }
    }
    if (changed) prefs.edit().putString("last_alert_times", times.toString()).apply()
}

@Composable
private fun ScannerApp() {
    val context = LocalContext.current
    val prefs = remember(context) { context.getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    val scope = rememberCoroutineScope()
    var baseUrl by remember(context) { mutableStateOf(prefs.getString("api_base_url", BuildConfig.API_BASE_URL) ?: BuildConfig.API_BASE_URL) }
    var baseDraft by remember(baseUrl) { mutableStateOf(baseUrl) }
    var tradeSize by remember(context) { mutableStateOf(prefs.getString("trade_size", "100") ?: "100") }
    var threshold by remember(context) { mutableStateOf(prefs.getString("alert_threshold", "1.0") ?: "1.0") }
    var rows by remember { mutableStateOf<List<OpportunityRow>>(emptyList()) }
    var exchangeRows by remember { mutableStateOf<List<ExchangeRow>>(emptyList()) }
    var saved by remember { mutableStateOf(readFavorites(context)) }
    var paperRows by remember { mutableStateOf(readPaperTrades(context)) }
    var tab by remember { mutableStateOf("Home") }
    var selected by remember { mutableStateOf<OpportunityRow?>(null) }
    var detail by remember { mutableStateOf<JSONObject?>(null) }
    var detailLoading by remember { mutableStateOf(false) }
    var detailError by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var stale by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var updated by remember { mutableStateOf("Not updated") }
    var searchDraft by remember { mutableStateOf("") }
    var searchApplied by remember { mutableStateOf("") }
    var spreadDraft by remember { mutableStateOf("0") }
    var spreadApplied by remember { mutableStateOf("0") }
    var volumeDraft by remember { mutableStateOf("0") }
    var volumeApplied by remember { mutableStateOf("0") }
    var buyExchangeDraft by remember { mutableStateOf("") }
    var sellExchangeDraft by remember { mutableStateOf("") }
    var buyExchangeApplied by remember { mutableStateOf("") }
    var sellExchangeApplied by remember { mutableStateOf("") }
    var sortBy by remember { mutableStateOf("provisional_profit") }
    var sortMenu by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Not checked") }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        status = if (granted) "Watchlist notifications enabled" else "Notification permission was not granted"
    }

    suspend fun refresh() {
        loading = true
        try {
            val path = "api/v1/opportunities?limit=200" +
                "&min_spread_pct=" + (spreadApplied.toDoubleOrNull() ?: 0.0).coerceAtLeast(0.0) +
                "&min_volume=" + (volumeApplied.toDoubleOrNull() ?: 0.0).coerceAtLeast(0.0) +
                "&search=" + enc(searchApplied) + "&buy_exchange=" + enc(buyExchangeApplied) +
                "&sell_exchange=" + enc(sellExchangeApplied) + "&sort_by=" + sortBy
            val root = getJson(baseUrl, path)
            val arr = root.optJSONArray("items") ?: JSONArray()
            val parsed = (0 until arr.length()).mapNotNull { arr.optJSONObject(it)?.let(::parseRow) }
            rows = parsed
            stale = root.optBoolean("stale", true)
            val timestamp = root.optString("last_scan_at", "")
            updated = if (timestamp.isBlank()) "Unknown" else timestamp.substringAfter("T", timestamp).take(8)
            notifySavedRoutes(context, parsed, saved, (threshold.toDoubleOrNull() ?: 1.0).coerceAtLeast(0.0))
            status = "API connected"
            error = null
        } catch (e: Exception) {
            error = e.message ?: "API request failed"
            status = "Connection failed"
        } finally { loading = false }
    }

    suspend fun refreshExchanges() {
        try {
            val root = getJson(baseUrl, "api/v1/exchanges")
            val arr = root.optJSONArray("exchanges") ?: JSONArray()
            exchangeRows = (0 until arr.length()).mapNotNull { i ->
                val o = arr.optJSONObject(i) ?: return@mapNotNull null
                ExchangeRow(
                    o.optString("id"), o.optString("status", "unknown"),
                    if (o.has("market_count") && !o.isNull("market_count")) o.optInt("market_count") else null,
                    if (o.has("usable_tickers") && !o.isNull("usable_tickers")) o.optInt("usable_tickers") else null,
                    o.optString("error").takeIf { it.isNotBlank() && it != "null" }
                )
            }
        } catch (e: Exception) { if (exchangeRows.isEmpty()) status = e.message ?: "Status unavailable" }
    }

    LaunchedEffect(tab, baseUrl, searchApplied, spreadApplied, volumeApplied, buyExchangeApplied, sellExchangeApplied, sortBy, saved, threshold) {
        while (true) {
            refresh()
            if (tab == "Exchanges") refreshExchanges()
            delay(30_000)
        }
    }

    LaunchedEffect(selected?.key, baseUrl, tradeSize) {
        val row = selected ?: return@LaunchedEffect
        detail = null
        detailError = null
        detailLoading = true
        try {
            val size = (tradeSize.toDoubleOrNull() ?: 100.0).coerceIn(0.01, 100000.0)
            detail = getJson(baseUrl, "api/v1/opportunity-details?symbol=" + enc(row.symbol) +
                "&buy_exchange=" + enc(row.buyExchange) + "&sell_exchange=" + enc(row.sellExchange) +
                "&trade_size_quote=" + size)
        } catch (e: Exception) { detailError = e.message ?: "Detail request failed" }
        finally { detailLoading = false }
    }

    fun toggleSaved(row: OpportunityRow) {
        saved = if (row.key in saved) saved - row.key else saved + row.key
        prefs.edit().putStringSet("favorites", saved.toSet()).apply()
    }
    fun saveSimulation() {
        detail?.let { paperRows = savePaperTrade(context, it); status = "Paper snapshot saved on this phone" }
    }

    Scaffold(
        containerColor = Background,
        bottomBar = {
            NavigationBar(containerColor = Panel) {
                listOf("Home", "Saved", "Paper", "Exchanges", "Settings").forEach { item ->
                    NavigationBarItem(
                        selected = tab == item,
                        onClick = { selected = null; tab = item },
                        icon = { Text(when (item) { "Home" -> "⌂"; "Saved" -> "☆"; "Paper" -> "▤"; "Exchanges" -> "⇄"; else -> "⚙" }, fontSize = 18.sp) },
                        label = { Text(item, fontSize = 10.sp) }
                    )
                }
            }
        }
    ) { inner ->
        Column(Modifier.fillMaxSize().padding(inner).padding(horizontal = 15.dp)) {
            if (selected != null) {
                DetailScreen(
                    row = selected!!, payload = detail, loading = detailLoading, error = detailError,
                    isSaved = selected!!.key in saved, onBack = { selected = null },
                    onToggleSaved = { toggleSaved(selected!!) }, onSavePaperTrade = ::saveSimulation
                )
            } else when (tab) {
                "Home" -> DashboardScreen(
                    rows, saved, loading, stale, updated, error,
                    searchDraft, { searchDraft = it }, spreadDraft, { spreadDraft = it },
                    volumeDraft, { volumeDraft = it }, buyExchangeDraft, { buyExchangeDraft = it },
                    sellExchangeDraft, { sellExchangeDraft = it }, sortBy, sortMenu,
                    { sortMenu = it }, { sortBy = it },
                    {
                        searchApplied = searchDraft
                        spreadApplied = spreadDraft
                        volumeApplied = volumeDraft
                        buyExchangeApplied = buyExchangeDraft.trim()
                        sellExchangeApplied = sellExchangeDraft.trim()
                        scope.launch { refresh() }
                    },
                    { scope.launch { refresh(); refreshExchanges() } },
                    { selected = it }, ::toggleSaved
                )
                "Saved" -> ListScreen("Saved routes", "Stored locally. Alerts check while the app is open.", rows.filter { it.key in saved }, saved, { selected = it }, ::toggleSaved)
                "Paper" -> PaperScreen(paperRows) {
                    prefs.edit().putString("paper_trades", "[]").apply()
                    paperRows = emptyList()
                }
                "Exchanges" -> ExchangeScreen(exchangeRows, loading) { scope.launch { refreshExchanges() } }
                else -> SettingsScreen(
                    baseDraft, { baseDraft = it }, tradeSize, { tradeSize = it }, threshold, { threshold = it }, status,
                    onSave = {
                        val candidate = normalizeUrl(baseDraft)
                        val localHttp = candidate.startsWith("http://10.") ||
                            candidate.startsWith("http://192.168.") || candidate.startsWith("http://127.0.0.1:") ||
                            candidate.startsWith("http://localhost:") || candidate.startsWith("http://10.0.2.2:")
                        if (!candidate.startsWith("https://") && !localHttp) {
                            status = "Use HTTPS for public APIs; HTTP is only allowed for local/private development."
                        } else {
                            baseUrl = candidate
                            prefs.edit()
                                .putString("api_base_url", candidate)
                                .putString("trade_size", (tradeSize.toDoubleOrNull() ?: 100.0).coerceIn(0.01, 100000.0).toString())
                                .putString("alert_threshold", (threshold.toDoubleOrNull() ?: 1.0).coerceAtLeast(0.0).toString())
                                .apply()
                            status = "Settings saved"
                            scope.launch { refresh(); refreshExchanges() }
                        }
                    },
                    onTest = { scope.launch {
                        status = "Testing API…"
                        try {
                            val health = getJson(normalizeUrl(baseDraft), "healthz")
                            status = "API " + health.optString("status") + " · " + health.optInt("exchange_count") + " exchanges"
                        } catch (e: Exception) { status = e.message ?: "API test failed" }
                    } },
                    onEnableNotifications = {
                        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                        else status = "Notification permission available; alerts check while app is open."
                    }
                )
            }
        }
    }
}

@Composable
private fun DashboardScreen(
    rows: List<OpportunityRow>, saved: Set<String>, loading: Boolean, stale: Boolean, updated: String, error: String?,
    search: String, onSearch: (String) -> Unit, spread: String, onSpread: (String) -> Unit,
    volume: String, onVolume: (String) -> Unit,
    buyExchange: String, onBuyExchange: (String) -> Unit, sellExchange: String, onSellExchange: (String) -> Unit,
    sortBy: String, sortMenu: Boolean,
    onSortMenu: (Boolean) -> Unit, onSort: (String) -> Unit, onApply: () -> Unit, onRefresh: () -> Unit,
    onOpen: (OpportunityRow) -> Unit, onToggleSaved: (OpportunityRow) -> Unit
) {
    Column(Modifier.fillMaxSize().padding(top = 10.dp)) {
        Text("ARBI SCANNER", color = Accent, fontSize = 12.sp, fontWeight = FontWeight.Bold)
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Market opportunities", fontSize = 23.sp, fontWeight = FontWeight.Bold)
                Text("Read-only monitor · Updated $updated", color = Muted, fontSize = 10.sp)
            }
            TextButton(onClick = onRefresh) { Text(if (loading) "Loading…" else "Refresh") }
        }
        Spacer(Modifier.height(7.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            MetricCard("OPPORTUNITIES", rows.size.toString(), Modifier.weight(1f))
            MetricCard("TOP SPREAD", rows.maxOfOrNull { it.spread }?.let { String.format(Locale.US, "%.2f%%", it) } ?: "—", Modifier.weight(1f))
        }
        if (stale) Text("Data is stale or the latest scan has not completed.", color = Warning, fontSize = 10.sp, modifier = Modifier.padding(top = 5.dp))
        Spacer(Modifier.height(5.dp))
        OutlinedTextField(search, onSearch, Modifier.fillMaxWidth(), label = { Text("Search symbol / pair") }, singleLine = true)
        Spacer(Modifier.height(4.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(spread, onSpread, Modifier.weight(1f), label = { Text("Min spread %") }, singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
            OutlinedTextField(volume, onVolume, Modifier.weight(1f), label = { Text("Min 24h volume") }, singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(buyExchange, onBuyExchange, Modifier.weight(1f), label = { Text("Buy exchange (optional)") }, singleLine = true)
            OutlinedTextField(sellExchange, onSellExchange, Modifier.weight(1f), label = { Text("Sell exchange (optional)") }, singleLine = true)
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
            Box {
                TextButton(onClick = { onSortMenu(true) }) {
                    Text("Sort: " + when (sortBy) { "gross_spread" -> "Spread"; "volume" -> "Volume"; "symbol" -> "Symbol"; else -> "Profit estimate" })
                }
                DropdownMenu(expanded = sortMenu, onDismissRequest = { onSortMenu(false) }) {
                    listOf("provisional_profit" to "Profit estimate", "gross_spread" to "Gross spread", "volume" to "24h volume", "symbol" to "Symbol").forEach { pair ->
                        DropdownMenuItem(text = { Text(pair.second) }, onClick = { onSort(pair.first); onSortMenu(false) })
                    }
                }
            }
            Button(onClick = onApply) { Text("Apply") }
        }
        Text("Ticker spread is provisional. Open any route for depth, fees and transfer details.", color = Warning, fontSize = 10.sp)
        Spacer(Modifier.height(5.dp))
        when {
            loading && rows.isEmpty() -> Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) { CircularProgressIndicator(color = Accent) }
            error != null && rows.isEmpty() -> Column(Modifier.padding(10.dp)) {
                Text("Could not load market data", fontWeight = FontWeight.Bold)
                Text(error, color = Muted, fontSize = 11.sp)
                Text("Check Settings and test the API.", color = Muted, fontSize = 11.sp)
            }
            rows.isEmpty() -> Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) { Text("No opportunities match these filters.", color = Muted) }
            else -> LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(rows, key = { it.key }) { row -> OpportunityCard(row, row.key in saved, { onOpen(row) }, { onToggleSaved(row) }) }
            }
        }
    }
}

@Composable
private fun MetricCard(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier.background(Panel, RoundedCornerShape(14.dp)).padding(12.dp)) {
        Text(label, color = Muted, fontSize = 9.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(4.dp))
        Text(value, fontSize = 20.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun OpportunityCard(row: OpportunityRow, isSaved: Boolean, onOpen: () -> Unit, onToggleSaved: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onOpen), shape = RoundedCornerShape(16.dp), colors = CardDefaults.cardColors(containerColor = Panel)) {
        Column(Modifier.padding(13.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(row.symbol, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                    Text(row.buyExchange + " → " + row.sellExchange, color = Muted, fontSize = 11.sp)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(String.format(Locale.US, "%+.2f%%", row.spread), color = Positive, fontWeight = FontWeight.Bold)
                    Text("gross spread", color = Muted, fontSize = 9.sp)
                }
                TextButton(onClick = onToggleSaved) { Text(if (isSaved) "★" else "☆", fontSize = 20.sp) }
            }
            HorizontalDivider(color = Color(0xFF2A3550))
            Spacer(Modifier.height(6.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Column { Text("BUY ASK", color = Muted, fontSize = 9.sp); Text(fmt(row.buyAsk), fontSize = 12.sp) }
                Column(horizontalAlignment = Alignment.End) { Text("SELL BID", color = Muted, fontSize = 9.sp); Text(fmt(row.sellBid), fontSize = 12.sp) }
            }
            Text("Provisional fee-adjusted spread: " + String.format(Locale.US, "%.3f%%", row.estimate), color = Accent, fontSize = 11.sp, modifier = Modifier.padding(top = 6.dp))
            Text("24h quote volume (lower leg): " + compactVolume(minOf(row.buyVolume, row.sellVolume)), color = Muted, fontSize = 10.sp)
            Text("Scan data age: " + ageLabel(row.ageSeconds), color = Muted, fontSize = 10.sp)
            Text(if (row.coverageComplete) "Tap for depth and fee detail" else "Coverage incomplete · tap for details", color = Muted, fontSize = 10.sp)
        }
    }
}

@Composable
private fun Page(title: String, subtitle: String, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxSize().padding(top = 16.dp)) {
        Text(title, fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Text(subtitle, color = Muted, fontSize = 11.sp)
        Spacer(Modifier.height(10.dp))
        content()
    }
}
@Composable
private fun ListScreen(title: String, subtitle: String, rows: List<OpportunityRow>, saved: Set<String>, onOpen: (OpportunityRow) -> Unit, onToggleSaved: (OpportunityRow) -> Unit) {
    Page(title, subtitle) {
        if (rows.isEmpty()) Text("No saved routes yet. Tap ☆ on the dashboard.", color = Muted)
        else LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(rows, key = { it.key }) { r -> OpportunityCard(r, r.key in saved, { onOpen(r) }, { onToggleSaved(r) }) }
        }
    }
}
@Composable
private fun PaperScreen(rows: List<PaperTradeRow>, onClear: () -> Unit) {
    Page("Paper-trade log", "Saved snapshots only; no real orders are placed.") {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { TextButton(onClick = onClear, enabled = rows.isNotEmpty()) { Text("Clear history") } }
        if (rows.isEmpty()) Text("Open an opportunity and save a paper-trade snapshot.", color = Muted)
        else LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(rows, key = { it.id }) { trade ->
                Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
                    Column(Modifier.fillMaxWidth().padding(13.dp)) {
                        Text(trade.symbol, fontWeight = FontWeight.Bold)
                        Text(trade.route, color = Muted, fontSize = 11.sp)
                        Text("Size: " + fmt(trade.size) + " " + trade.quote, fontSize = 12.sp)
                        Text(trade.profit?.let { "P&L after reported fees: " + fmt(it) + " " + trade.quote } ?: "P&L unknown: depth or fee data incomplete.", color = if (trade.profit != null) Accent else Warning, fontSize = 11.sp)
                        Text(trade.savedAt, color = Muted, fontSize = 10.sp)
                    }
                }
            }
        }
    }
}
@Composable
private fun ExchangeScreen(rows: List<ExchangeRow>, loading: Boolean, onRefresh: () -> Unit) {
    Page("Exchanges", "Configured venues and most recent scan diagnostics.") {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { TextButton(onClick = onRefresh) { Text("Refresh") } }
        when {
            loading && rows.isEmpty() -> CircularProgressIndicator(color = Accent)
            rows.isEmpty() -> Text("Exchange diagnostics are not available.", color = Muted)
            else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                items(rows, key = { it.id }) { venue ->
                    Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
                        Column(Modifier.fillMaxWidth().padding(13.dp)) {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text(venue.id, fontWeight = FontWeight.Bold)
                                Text(venue.status.uppercase(Locale.US), color = if (venue.status == "ok") Positive else Warning, fontSize = 10.sp)
                            }
                            Text(listOfNotNull(venue.markets?.let { "$it markets" }, venue.tickers?.let { "$it usable tickers" }).joinToString(" · "), color = Muted, fontSize = 11.sp)
                            venue.error?.let { Text(it, color = Warning, fontSize = 10.sp) }
                        }
                    }
                }
            }
        }
    }
}
@Composable
private fun SettingsScreen(
    baseDraft: String, onBaseDraft: (String) -> Unit, tradeSize: String, onTradeSize: (String) -> Unit,
    threshold: String, onThreshold: (String) -> Unit, status: String, onSave: () -> Unit,
    onTest: () -> Unit, onEnableNotifications: () -> Unit
) {
    Page("Settings", "Mobile API only. Never enter exchange or Telegram secrets here.") {
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item { OutlinedTextField(baseDraft, onBaseDraft, Modifier.fillMaxWidth(), label = { Text("Mobile API base URL") }, singleLine = true) }
            item { OutlinedTextField(tradeSize, onTradeSize, Modifier.fillMaxWidth(), label = { Text("Order-book estimate size") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), singleLine = true) }
            item { OutlinedTextField(threshold, onThreshold, Modifier.fillMaxWidth(), label = { Text("Watchlist notification threshold (%)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), singleLine = true) }
            item { Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onSave, modifier = Modifier.weight(1f)) { Text("Save") }
                OutlinedButton(onClick = onTest, modifier = Modifier.weight(1f)) { Text("Test API") }
            } }
            item { OutlinedButton(onClick = onEnableNotifications, modifier = Modifier.fillMaxWidth()) { Text("Enable watchlist notifications") } }
            item { Text(status, color = Muted, fontSize = 12.sp) }
            item { HorizontalDivider(color = Color(0xFF2A3550)) }
            item { Text("Data and safety", fontWeight = FontWeight.Bold) }
            item { Text("• Read-only: the app does not place orders or withdrawals.", color = Muted, fontSize = 12.sp) }
            item { Text("• List spreads are provisional; inspect live order-book detail.", color = Muted, fontSize = 12.sp) }
            item { Text("• Full-route net profit stays unknown until transfer costs are confirmed.", color = Muted, fontSize = 12.sp) }
            item { Text("• Local alerts check while the app is open; remote push is not configured.", color = Muted, fontSize = 12.sp) }
        }
    }
}
@Composable
private fun DetailScreen(row: OpportunityRow, payload: JSONObject?, loading: Boolean, error: String?, isSaved: Boolean, onBack: () -> Unit, onToggleSaved: () -> Unit, onSavePaperTrade: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(top = 7.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("← Back") }
            Column(Modifier.weight(1f)) {
                Text(row.symbol, fontSize = 21.sp, fontWeight = FontWeight.Bold)
                Text(row.buyExchange + " → " + row.sellExchange, color = Muted, fontSize = 11.sp)
            }
            TextButton(onClick = onToggleSaved) { Text(if (isSaved) "★" else "☆", fontSize = 20.sp) }
        }
        Text("Depth-aware estimate · not a profit guarantee", color = Warning, fontSize = 11.sp)
        Text("Ticker observation: " + row.observedAt, color = Muted, fontSize = 10.sp)
        Spacer(Modifier.height(7.dp))
        if (loading && payload == null) Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) { CircularProgressIndicator(color = Accent) }
        else if (payload == null) Column {
            Text("Could not load route details", fontWeight = FontWeight.Bold)
            Text(error ?: "No detail response.", color = Muted, fontSize = 12.sp)
            Text("The route may have expired or an exchange may be unavailable.", color = Muted, fontSize = 12.sp)
        } else {
            val buy = payload.optJSONObject("buy")
            val sell = payload.optJSONObject("sell")
            val warnings = payload.optJSONArray("warnings")
            val routes = payload.optJSONArray("verified_transfer_routes")
            LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                item {
                    Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
                        Column(Modifier.fillMaxWidth().padding(13.dp)) {
                            Text("Requested size: " + fmt(payload.optDouble("requested_trade_size_quote")) + " " + payload.optString("quote_currency"), fontWeight = FontWeight.SemiBold)
                            MetricRow("Best ask", fmt(buy?.optDouble("best_ask", 0.0) ?: 0.0))
                            MetricRow("Weighted buy price", fmt(buy?.optDouble("volume_weighted_average_price", 0.0) ?: 0.0))
                            MetricRow("Quote spent", fmt(buy?.optDouble("quote_spent", 0.0) ?: 0.0))
                            MetricRow("Base acquired", fmt(buy?.optDouble("base_acquired", 0.0) ?: 0.0))
                            HorizontalDivider(color = Color(0xFF2A3550))
                            MetricRow("Best bid", fmt(sell?.optDouble("best_bid", 0.0) ?: 0.0))
                            MetricRow("Weighted sell price", fmt(sell?.optDouble("volume_weighted_average_price", 0.0) ?: 0.0))
                            MetricRow("Quote received", fmt(sell?.optDouble("quote_received", 0.0) ?: 0.0))
                            MetricRow("Buy-side slippage", optionalPercent(payload, "buy_slippage_pct"))
                            MetricRow("Sell-side slippage", optionalPercent(payload, "sell_slippage_pct"))
                            MetricRow("Requested size fully filled", if (payload.optBoolean("order_book_depth_verified")) "Yes" else "No / incomplete")
                        }
                    }
                }
                item {
                    Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
                        Column(Modifier.fillMaxWidth().padding(13.dp)) {
                            Text("Fees and P&L", fontWeight = FontWeight.Bold)
                            MetricRow("Buy taker fee", feeText(buy))
                            MetricRow("Sell taker fee", feeText(sell))
                            MetricRow("Gross P&L before trading fees", optionalMoney(payload, "gross_pnl_before_trading_fees"))
                            MetricRow("Known trading-fee estimate", optionalMoney(payload, "known_trading_fee_estimate"))
                            MetricRow("P&L after known fees", optionalMoney(payload, "profit_after_known_fees"))
                            MetricRow("Reported withdrawal fee deducted", if (payload.has("withdrawal_fee_deducted_base") && !payload.isNull("withdrawal_fee_deducted_base")) fmt(payload.optDouble("withdrawal_fee_deducted_base")) + " " + row.symbol.substringBefore("/") else "Unknown")
                            MetricRow("Full-route net profit", "Unknown")
                            Text("Withdrawal and transfer costs are not included in full-route profit.", color = Warning, fontSize = 10.sp)
                        }
                    }
                }
                item {
                    Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
                        Column(Modifier.fillMaxWidth().padding(13.dp)) {
                            Text("Transfer route verification", fontWeight = FontWeight.Bold)
                            Text(if (payload.optBoolean("transfer_route_verified")) "Matching enabled network and contract found." else "No route was positively verified.", color = if (payload.optBoolean("transfer_route_verified")) Positive else Warning, fontSize = 12.sp)
                            Text("Unknown network/contract data is never treated as approval.", color = Muted, fontSize = 10.sp)
                            if (routes != null) for (i in 0 until routes.length()) {
                                val route = routes.optJSONObject(i) ?: continue
                                Text("✓ " + route.optString("network") + " · withdrawal fee " + route.optString("buy_exchange_withdrawal_fee", "unknown") + " " + route.optString("withdrawal_fee_asset", ""), color = Positive, fontSize = 11.sp)
                            }
                        }
                    }
                }
                item {
                    Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
                        Column(Modifier.fillMaxWidth().padding(13.dp)) {
                            Text("Warnings", fontWeight = FontWeight.Bold)
                            if (warnings != null) for (i in 0 until warnings.length()) Text("• " + warnings.optString(i), color = Warning, fontSize = 10.sp, modifier = Modifier.padding(top = 4.dp))
                            Spacer(Modifier.height(8.dp))
                            Button(onClick = onSavePaperTrade, modifier = Modifier.fillMaxWidth()) { Text("Save paper-trade snapshot") }
                            Text("Local simulation record only; no actual order is placed.", color = Muted, fontSize = 10.sp)
                        }
                    }
                }
            }
        }
    }
}
@Composable
private fun MetricRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = Muted, fontSize = 11.sp, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(8.dp))
        Text(value, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
    }
}
private fun optionalMoney(o: JSONObject, key: String) = if (o.has(key) && !o.isNull(key)) fmt(o.optDouble(key)) else "Unknown"
private fun optionalPercent(o: JSONObject, key: String) = if (o.has(key) && !o.isNull(key)) String.format(Locale.US, "%.3f%%", o.optDouble(key)) else "Unknown"
private fun feeText(o: JSONObject?): String {
    if (o == null || !o.has("taker_fee_rate") || o.isNull("taker_fee_rate")) return "Unknown"
    return String.format(Locale.US, "%.4f%% (%s)", o.optDouble("taker_fee_rate") * 100.0, o.optString("fee_source"))
}
