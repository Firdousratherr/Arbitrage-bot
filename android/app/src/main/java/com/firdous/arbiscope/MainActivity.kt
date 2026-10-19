package com.firdous.arbiscope

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Bookmark
import androidx.compose.material.icons.filled.FilterList
import androidx.compose.material.icons.filled.Functions
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.OpenInNew
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Divider
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.Locale

private const val PREFS = "arbiscope_preferences"

private enum class AppTab(val title: String) {
    SCANNER("Scanner"),
    TRIANGULAR("Triangular"),
    SAVED("Saved"),
    SETTINGS("Settings")
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { ArbiScopeRoot() }
    }
}

@Composable
private fun ArbiScopeRoot() {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    var darkMode by remember { mutableStateOf(prefs.getBoolean("dark_mode", true)) }
    val scheme = if (darkMode) {
        darkColorScheme(
            primary = Color(0xFFFFC247),
            onPrimary = Color(0xFF211800),
            secondary = Color(0xFF9BD4C1),
            background = Color(0xFF14161B),
            surface = Color(0xFF20232B),
            onSurface = Color(0xFFF2F3F5),
            surfaceVariant = Color(0xFF2A2E37),
            onSurfaceVariant = Color(0xFFBEC3CD)
        )
    } else {
        lightColorScheme(
            primary = Color(0xFF795000),
            onPrimary = Color.White,
            secondary = Color(0xFF176C58),
            background = Color(0xFFF6F7FA),
            surface = Color.White,
            surfaceVariant = Color(0xFFEAEDF2),
            onSurfaceVariant = Color(0xFF505764)
        )
    }
    MaterialTheme(colorScheme = scheme) {
        ArbiScopeApp(darkMode = darkMode, onDarkModeChange = { value ->
            darkMode = value
            prefs.edit().putBoolean("dark_mode", value).apply()
        })
    }
}

@Composable
private fun ArbiScopeApp(darkMode: Boolean, onDarkModeChange: (Boolean) -> Unit) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    var baseUrl by remember { mutableStateOf(prefs.getString("base_url", "http://10.0.2.2:8000") ?: "http://10.0.2.2:8000") }
    var baseUrlDraft by remember { mutableStateOf(baseUrl) }
    var tab by remember { mutableStateOf(AppTab.SCANNER) }
    var query by remember { mutableStateOf("") }
    var sort by remember { mutableStateOf("profit") }
    var minSpread by remember { mutableStateOf(prefs.getString("min_spread", "0.5") ?: "0.5") }
    var minLiquidity by remember { mutableStateOf(prefs.getString("min_liquidity", "0") ?: "0") }
    var quote by remember { mutableStateOf(prefs.getString("quote_asset", "USDT") ?: "USDT") }
    var selectedExchanges by remember { mutableStateOf(prefs.getStringSet("selected_exchanges", emptySet())?.toSet() ?: emptySet()) }
    var refreshSeconds by remember { mutableIntStateOf(prefs.getInt("refresh_seconds", 30).coerceIn(15, 120)) }
    var thresholdText by remember { mutableStateOf(prefs.getString("notification_threshold", "0.5") ?: "0.5") }
    var alertsEnabled by remember { mutableStateOf(prefs.getBoolean("alerts_enabled", false)) }
    var items by remember { mutableStateOf<List<Opportunity>>(emptyList()) }
    var triangles by remember { mutableStateOf<List<TriangleOpportunity>>(emptyList()) }
    var exchanges by remember { mutableStateOf<List<ExchangeStatus>>(emptyList()) }
    var updatedAt by remember { mutableStateOf("") }
    var stale by remember { mutableStateOf(true) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var filterDialog by remember { mutableStateOf(false) }
    var sortMenu by remember { mutableStateOf(false) }
    var selectedOpportunity by remember { mutableStateOf<Opportunity?>(null) }
    var selectedTriangle by remember { mutableStateOf<TriangleOpportunity?>(null) }
    var refreshTrigger by remember { mutableIntStateOf(0) }
    var connectionMessage by remember { mutableStateOf("") }
    var savedIds by remember {
        mutableStateOf(prefs.getStringSet("saved_opportunity_ids", emptySet())?.toSet() ?: emptySet())
    }
    val scope = rememberCoroutineScope()

    val notificationPermission = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission()
    ) { granted ->
        alertsEnabled = granted
        prefs.edit().putBoolean("alerts_enabled", granted).apply()
        OpportunityNotificationWorker.schedule(
            context, granted, baseUrl, thresholdText.toFloatOrNull() ?: 0.5f
        )
        if (!granted) Toast.makeText(context, "Notifications were not enabled", Toast.LENGTH_SHORT).show()
    }

    suspend fun refreshData() {
        if (tab == AppTab.SETTINGS) return
        loading = true
        error = ""
        try {
            if (tab == AppTab.TRIANGULAR) {
                val response = ApiClient.triangles(baseUrl, query)
                triangles = response.items
                updatedAt = response.updatedAt
                stale = response.stale
            } else {
                val response = ApiClient.opportunities(
                    baseUrl = baseUrl,
                    q = query,
                    sort = sort,
                    minSpread = minSpread,
                    minLiquidity = minLiquidity,
                    quote = quote,
                    exchanges = selectedExchanges
                )
                items = response.items
                exchanges = response.exchangeStatus
                updatedAt = response.updatedAt
                stale = response.stale
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (exception: Exception) {
            error = exception.message ?: "Could not connect to the market API."
        } finally {
            loading = false
        }
    }

    LaunchedEffect(baseUrl, tab, query, sort, minSpread, minLiquidity, quote, selectedExchanges, refreshSeconds, refreshTrigger) {
        while (true) {
            refreshData()
            delay((refreshSeconds * 1000L).coerceAtLeast(15000L))
        }
    }

    BackHandler(enabled = selectedOpportunity != null || selectedTriangle != null) {
        selectedOpportunity = null
        selectedTriangle = null
    }

    fun toggleSaved(item: Opportunity) {
        savedIds = if (savedIds.contains(item.id)) savedIds - item.id else savedIds + item.id
        prefs.edit().putStringSet("saved_opportunity_ids", savedIds).apply()
    }

    val showingDetail = selectedOpportunity != null || selectedTriangle != null
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            if (!showingDetail) {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    NavigationBarItem(
                        selected = tab == AppTab.SCANNER,
                        onClick = { tab = AppTab.SCANNER },
                        icon = { Icon(Icons.Filled.ShowChart, contentDescription = null) },
                        label = { Text("Scanner") }
                    )
                    NavigationBarItem(
                        selected = tab == AppTab.TRIANGULAR,
                        onClick = { tab = AppTab.TRIANGULAR },
                        icon = { Icon(Icons.Filled.Functions, contentDescription = null) },
                        label = { Text("Triangular") }
                    )
                    NavigationBarItem(
                        selected = tab == AppTab.SAVED,
                        onClick = { tab = AppTab.SAVED },
                        icon = { Icon(Icons.Filled.Bookmark, contentDescription = null) },
                        label = { Text("Saved") }
                    )
                    NavigationBarItem(
                        selected = tab == AppTab.SETTINGS,
                        onClick = { tab = AppTab.SETTINGS },
                        icon = { Icon(Icons.Filled.Settings, contentDescription = null) },
                        label = { Text("Settings") }
                    )
                }
            }
        }
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            if (showingDetail) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    IconButton(onClick = { selectedOpportunity = null; selectedTriangle = null }) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "Back")
                    }
                    Text("Opportunity details", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                }
                selectedOpportunity?.let { item ->
                    OpportunityDetail(item = item, onOpenExchange = { url -> openExternal(context, url) })
                }
                selectedTriangle?.let { item -> TriangleDetail(item) }
            } else {
                Header(
                    updatedAt = updatedAt,
                    stale = stale,
                    loading = loading,
                    onRefresh = { refreshTrigger++ }
                )
                if (error.isNotBlank()) {
                    StatusPanel(
                        title = "Market data unavailable",
                        body = error + "\nCheck that the mobile API is running and that the API URL in Settings is correct.",
                        warning = true
                    )
                }
                if (!stale && updatedAt.isNotBlank()) {
                    Text(
                        "Last scan: " + updatedAt.replace("T", " ").take(19),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 3.dp)
                    )
                }
                when (tab) {
                    AppTab.SCANNER -> {
                        ScannerControls(
                            query = query,
                            onQueryChange = { query = it },
                            sort = sort,
                            sortMenu = sortMenu,
                            onSortMenu = { sortMenu = it },
                            onSortChange = { sort = it },
                            onFilters = { filterDialog = true },
                            savedIds = savedIds
                        )
                        if (filterDialog) {
                            FilterDialog(
                                minSpread = minSpread,
                                minLiquidity = minLiquidity,
                                quote = quote,
                                availableExchanges = exchanges,
                                selectedExchanges = selectedExchanges,
                                onDismiss = { filterDialog = false },
                                onApply = { newSpread, newLiquidity, newQuote, newExchanges ->
                                    minSpread = newSpread
                                    minLiquidity = newLiquidity
                                    quote = newQuote.uppercase().ifBlank { "USDT" }
                                    selectedExchanges = newExchanges
                                    prefs.edit()
                                        .putString("min_spread", minSpread)
                                        .putString("min_liquidity", minLiquidity)
                                        .putString("quote_asset", quote)
                                        .putStringSet("selected_exchanges", selectedExchanges)
                                        .apply()
                                    filterDialog = false
                                }
                            )
                        }
                        if (loading && items.isEmpty() && error.isBlank()) LoadingPanel()
                        else if (items.isEmpty() && error.isBlank()) EmptyPanel(
                            title = "No opportunities yet",
                            body = "The scanner only lists positive spreads it can price against visible order-book depth. Try a wider filter or check again later."
                        )
                        else OpportunityList(
                            items = items,
                            savedIds = savedIds,
                            onOpen = { selectedOpportunity = it },
                            onSave = { toggleSaved(it) }
                        )
                    }
                    AppTab.TRIANGULAR -> {
                        SearchBar(query = query, onQueryChange = { query = it }, placeholder = "Search route or exchange")
                        if (loading && triangles.isEmpty() && error.isBlank()) LoadingPanel()
                        else if (triangles.isEmpty() && error.isBlank()) EmptyPanel(
                            title = "No triangular loops found",
                            body = "This tab estimates three-leg loops from current ticker prices and fee assumptions. Results may be empty, stale or too small to trade."
                        )
                        else TriangleList(triangles) { selectedTriangle = it }
                    }
                    AppTab.SAVED -> {
                        SearchBar(query = query, onQueryChange = { query = it }, placeholder = "Search saved pairs")
                        val savedItems = items.filter { savedIds.contains(it.id) }
                        if (savedItems.isEmpty()) EmptyPanel(
                            title = "No saved live opportunities",
                            body = "Tap the star on a scanner card to save it. Opportunities can disappear when the current scan no longer finds them."
                        ) else OpportunityList(
                            items = savedItems,
                            savedIds = savedIds,
                            onOpen = { selectedOpportunity = it },
                            onSave = { toggleSaved(it) }
                        )
                    }
                    AppTab.SETTINGS -> SettingsScreen(
                        baseUrlDraft = baseUrlDraft,
                        onBaseUrlChange = { baseUrlDraft = it },
                        onSaveBaseUrl = {
                            val candidate = baseUrlDraft.trim().removeSuffix("/")
                            if (candidate.startsWith("http://") || candidate.startsWith("https://")) {
                                baseUrl = candidate
                                baseUrlDraft = candidate
                                prefs.edit().putString("base_url", candidate).apply()
                                connectionMessage = "API URL saved. Testing connection…"
                                scope.launch {
                                    connectionMessage = try {
                                        val result = ApiClient.health(candidate)
                                        if (result.optBoolean("ok")) "Connected to mobile API." else "API replied, but did not report healthy."
                                    } catch (exception: Exception) {
                                        "Connection failed: " + (exception.message ?: "unknown error")
                                    }
                                }
                            } else {
                                connectionMessage = "Enter a full URL beginning with http:// or https://"
                            }
                        },
                        refreshSeconds = refreshSeconds,
                        onRefreshSecondsChange = {
                            refreshSeconds = it
                            prefs.edit().putInt("refresh_seconds", it).apply()
                        },
                        thresholdText = thresholdText,
                        onThresholdChange = { thresholdText = it },
                        alertsEnabled = alertsEnabled,
                        onAlertsChange = { enabled ->
                            if (enabled && Build.VERSION.SDK_INT >= 33 &&
                                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                            ) {
                                notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                            } else {
                                alertsEnabled = enabled
                                prefs.edit().putBoolean("alerts_enabled", enabled).apply()
                                OpportunityNotificationWorker.schedule(
                                    context, enabled, baseUrl, thresholdText.toFloatOrNull() ?: 0.5f
                                )
                            }
                        },
                        darkMode = darkMode,
                        onDarkModeChange = onDarkModeChange,
                        connectionMessage = connectionMessage
                    )
                }
            }
        }
    }
}

@Composable
private fun Header(updatedAt: String, stale: Boolean, loading: Boolean, onRefresh: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(start = 18.dp, end = 12.dp, top = 15.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.primary) {
            Icon(Icons.Filled.ShowChart, contentDescription = "ArbiScope", tint = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.padding(10.dp).size(26.dp))
        }
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text("ARBISCOPE", fontWeight = FontWeight.Black, fontSize = 19.sp, letterSpacing = 1.1.sp)
            Text("CRYPTO OPPORTUNITY SCANNER", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Surface(
            shape = CircleShape,
            color = if (stale) Color(0xFF8E5B18).copy(alpha = 0.18f) else Color(0xFF2A9D73).copy(alpha = 0.20f)
        ) {
            Text(
                if (stale) "STALE" else "LIVE",
                modifier = Modifier.padding(horizontal = 9.dp, vertical = 6.dp),
                color = if (stale) Color(0xFFE8B45A) else Color(0xFF48C997),
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold
            )
        }
        IconButton(onClick = onRefresh) { Icon(Icons.Filled.Refresh, contentDescription = "Refresh market data") }
    }
}

@Composable
private fun ScannerControls(
    query: String,
    onQueryChange: (String) -> Unit,
    sort: String,
    sortMenu: Boolean,
    onSortMenu: (Boolean) -> Unit,
    onSortChange: (String) -> Unit,
    onFilters: () -> Unit,
    savedIds: Set<String>
) {
    Column(Modifier.padding(horizontal = 14.dp, vertical = 4.dp)) {
        SearchBar(query = query, onQueryChange = onQueryChange, placeholder = "Search coin or exchange")
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            FilledTonalButton(onClick = onFilters, modifier = Modifier.weight(1f)) {
                Icon(Icons.Filled.FilterList, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("Filters")
            }
            Box {
                OutlinedButton(onClick = { onSortMenu(true) }) {
                    Text("Sort: " + sort.replaceFirstChar { it.uppercase() })
                }
                DropdownMenu(expanded = sortMenu, onDismissRequest = { onSortMenu(false) }) {
                    listOf("profit", "spread", "liquidity", "latest").forEach { option ->
                        DropdownMenuItem(
                            text = { Text(option.replaceFirstChar { it.uppercase() }) },
                            onClick = { onSortChange(option); onSortMenu(false) }
                        )
                    }
                }
            }
        }
        Text(
            "Estimates are before transfer fees · " + savedIds.size + " saved",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.padding(start = 2.dp, top = 5.dp, bottom = 4.dp)
        )
    }
}

@Composable
private fun SearchBar(query: String, onQueryChange: (String) -> Unit, placeholder: String) {
    OutlinedTextField(
        value = query,
        onValueChange = onQueryChange,
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
        leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
        placeholder = { Text(placeholder) },
        shape = RoundedCornerShape(14.dp)
    )
}

@Composable
private fun OpportunityList(
    items: List<Opportunity>,
    savedIds: Set<String>,
    onOpen: (Opportunity) -> Unit,
    onSave: (Opportunity) -> Unit
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(start = 14.dp, end = 14.dp, top = 6.dp, bottom = 14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        items(items, key = { it.id }) { item ->
            OpportunityCard(
                item = item,
                saved = savedIds.contains(item.id),
                onOpen = { onOpen(item) },
                onSave = { onSave(item) }
            )
        }
    }
}

@Composable
private fun OpportunityCard(item: Opportunity, saved: Boolean, onOpen: () -> Unit, onSave: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onOpen),
        shape = RoundedCornerShape(18.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(item.symbol.replace("/", " / "), fontWeight = FontWeight.Bold, fontSize = 18.sp)
                    Text(
                        item.buyExchange + " → " + item.sellExchange,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(pct(item.spreadPct), color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.ExtraBold, fontSize = 20.sp)
                    Text("raw spread", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelSmall)
                }
                IconButton(onClick = onSave) {
                    Icon(
                        if (saved) Icons.Filled.Star else Icons.Filled.StarBorder,
                        contentDescription = if (saved) "Remove from saved" else "Save opportunity",
                        tint = if (saved) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
            Divider(color = MaterialTheme.colorScheme.outlineVariant)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Buy " + money(item.buyPrice), fontWeight = FontWeight.SemiBold)
                    Text("Sell " + money(item.sellPrice), color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("~" + money(item.estimatedProfitUsd), fontWeight = FontWeight.Bold, color = if (item.estimatedProfitUsd >= 0) Color(0xFF35B77E) else MaterialTheme.colorScheme.error)
                    Text("before transfer", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelSmall)
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                InfoPill("Depth " + money(item.liquidityUsd))
                InfoPill("Fees " + pct(item.buyFeePct + item.sellFeePct))
                InfoPill("Check route")
            }
        }
    }
}

@Composable
private fun InfoPill(text: String) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(50)) {
        Text(text, modifier = Modifier.padding(horizontal = 8.dp, vertical = 5.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun OpportunityDetail(item: Opportunity, onOpenExchange: (String) -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Card(shape = RoundedCornerShape(20.dp)) {
            Column(Modifier.fillMaxWidth().padding(18.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text(item.symbol.replace("/", " / "), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Text(pct(item.spreadPct) + " RAW SPREAD", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.ExtraBold, fontSize = 25.sp)
                Text("Trade-size estimate: " + money(item.estimatedSizeUsd), color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(5.dp))
                Text("Estimated profit before transfer costs", style = MaterialTheme.typography.labelMedium)
                Text(money(item.estimatedProfitUsd), fontSize = 24.sp, fontWeight = FontWeight.Bold, color = Color(0xFF35B77E))
            }
        }
        Text("TRADE ROUTE", fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelLarge)
        ExchangeLegCard("1 · BUY", item.buyExchange, item.buyPrice, item.bestAsk, item.buyFeePct, item.buyFeeUsd, item.buyFeeSource) {
            onOpenExchange(item.buyExchangeUrl)
        }
        ExchangeLegCard("2 · SELL", item.sellExchange, item.sellPrice, item.bestBid, item.sellFeePct, item.sellFeeUsd, item.sellFeeSource) {
            onOpenExchange(item.sellExchangeUrl)
        }
        Card(shape = RoundedCornerShape(18.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Cost breakdown", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                MetricLine("Estimated buy trading fee", money(item.buyFeeUsd) + " (" + pct(item.buyFeePct) + ")")
                Text(item.buyFeeSource, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                MetricLine("Estimated sell trading fee", money(item.sellFeeUsd) + " (" + pct(item.sellFeePct) + ")")
                Text(item.sellFeeSource, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                MetricLine("Transfer / withdrawal fee", "Unknown — not deducted")
                MetricLine("Depth-adjusted spread", pct(item.depthAdjustedSpreadPct))
                MetricLine("Contract match", item.contractMatch.uppercase())
            }
        }
        StatusPanel(
            title = "Manual safety check required",
            body = item.warning.ifBlank { "The API cannot currently verify transfer route safety. Check the token contract, network, deposit status, withdrawal status and withdrawal fee on both exchanges before acting." },
            warning = true
        )
        Text(
            "This is market information, not a recommendation. Spreads can close quickly; orders can partially fill, and your actual fees, limits and execution may differ.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun ExchangeLegCard(
    label: String,
    exchange: String,
    averagePrice: Double,
    topPrice: Double,
    feePct: Double,
    feeUsd: Double,
    feeSource: String,
    onOpen: () -> Unit
) {
    Card(shape = RoundedCornerShape(18.dp)) {
        Column(Modifier.fillMaxWidth().padding(15.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(label, color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                Text(exchange, fontWeight = FontWeight.Bold, fontSize = 17.sp)
            }
            MetricLine("Order-book average", money(averagePrice))
            MetricLine("Best price at scan", money(topPrice))
            MetricLine("Fee estimate", money(feeUsd) + " (" + pct(feePct) + ")")
            Text("Fee source: " + feeSource, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            FilledTonalButton(onClick = onOpen, modifier = Modifier.fillMaxWidth()) {
                Text("Open " + exchange)
                Spacer(Modifier.width(8.dp))
                Icon(Icons.Filled.OpenInNew, contentDescription = null, modifier = Modifier.size(16.dp))
            }
        }
    }
}

@Composable
private fun TriangleList(items: List<TriangleOpportunity>, onOpen: (TriangleOpportunity) -> Unit) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        items(items, key = { it.id }) { item ->
            Card(modifier = Modifier.fillMaxWidth().clickable { onOpen(item) }, shape = RoundedCornerShape(18.dp)) {
                Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(item.exchange.uppercase(), fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                        Text(pct(item.profitPct), fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.primary, fontSize = 20.sp)
                    }
                    Text(item.route, fontWeight = FontWeight.Medium)
                    Text("~" + money(item.profitUsdOn100) + " per $100 at ticker prices", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                    Text("3 legs · verify depth and fees", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

@Composable
private fun TriangleDetail(item: TriangleOpportunity) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Card(shape = RoundedCornerShape(18.dp)) {
            Column(Modifier.padding(17.dp)) {
                Text("TRIANGULAR ROUTE", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold)
                Text(item.route, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Text(item.exchange.uppercase() + " · " + pct(item.profitPct), fontSize = 17.sp)
                Text("Illustrative return on $100: " + money(item.profitUsdOn100), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        item.legs.forEachIndexed { index, leg ->
            Card(shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(14.dp)) {
                    Text("LEG " + (index + 1) + " · " + leg.side.uppercase(), color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold)
                    Text(leg.from + " → " + leg.to, fontWeight = FontWeight.Bold, fontSize = 17.sp)
                    MetricLine("Market", leg.symbol)
                    MetricLine("Ticker price", money(leg.price))
                    MetricLine("Estimated fee", pct(leg.feePct))
                    Text(leg.feeSource, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
        StatusPanel(
            title = "Ticker-only estimate",
            body = item.estimateScope + ". It does not simulate full order-book depth, minimum order size, slippage or order execution. It is not a guaranteed profit.",
            warning = true
        )
    }
}

@Composable
private fun FilterDialog(
    minSpread: String,
    minLiquidity: String,
    quote: String,
    availableExchanges: List<ExchangeStatus>,
    selectedExchanges: Set<String>,
    onDismiss: () -> Unit,
    onApply: (String, String, String, Set<String>) -> Unit
) {
    var spreadDraft by remember { mutableStateOf(minSpread) }
    var liquidityDraft by remember { mutableStateOf(minLiquidity) }
    var quoteDraft by remember { mutableStateOf(quote) }
    var exchangesDraft by remember { mutableStateOf(selectedExchanges) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Scanner filters", fontWeight = FontWeight.Bold) },
        text = {
            Column(
                Modifier.fillMaxWidth().heightIn(max = 520.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                OutlinedTextField(
                    value = spreadDraft,
                    onValueChange = { spreadDraft = it },
                    label = { Text("Minimum raw spread (%)") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = liquidityDraft,
                    onValueChange = { liquidityDraft = it },
                    label = { Text("Minimum visible liquidity ($)") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = quoteDraft,
                    onValueChange = { quoteDraft = it },
                    label = { Text("Quote currency (e.g. USDT)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Text("Exchanges (all selected when none are checked)", fontWeight = FontWeight.SemiBold)
                availableExchanges.forEach { exchange ->
                    val selected = exchangesDraft.isEmpty() || exchangesDraft.contains(exchange.exchange)
                    Row(
                        Modifier.fillMaxWidth().clickable {
                            exchangesDraft = if (exchangesDraft.isEmpty()) {
                                availableExchanges.map { it.exchange }.filterNot { it == exchange.exchange }.toSet()
                            } else if (selected) {
                                exchangesDraft - exchange.exchange
                            } else {
                                exchangesDraft + exchange.exchange
                            }
                        }.padding(vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Checkbox(checked = selected, onCheckedChange = { checked ->
                            exchangesDraft = if (exchangesDraft.isEmpty()) {
                                if (checked) emptySet() else availableExchanges.map { it.exchange }.filterNot { it == exchange.exchange }.toSet()
                            } else if (checked) {
                                exchangesDraft + exchange.exchange
                            } else {
                                exchangesDraft - exchange.exchange
                            }
                        })
                        Column(Modifier.weight(1f)) {
                            Text(exchange.exchange.uppercase())
                            Text(exchange.status, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                if (availableExchanges.isEmpty()) {
                    Text("Exchange list will appear after the first successful API response.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        },
        confirmButton = {
            Button(onClick = {
                val safeSpread = spreadDraft.toDoubleOrNull()?.coerceAtLeast(0.0)?.toString() ?: "0"
                val safeLiquidity = liquidityDraft.toDoubleOrNull()?.coerceAtLeast(0.0)?.toString() ?: "0"
                onApply(safeSpread, safeLiquidity, quoteDraft, exchangesDraft)
            }) { Text("Apply filters") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@Composable
private fun SettingsScreen(
    baseUrlDraft: String,
    onBaseUrlChange: (String) -> Unit,
    onSaveBaseUrl: () -> Unit,
    refreshSeconds: Int,
    onRefreshSecondsChange: (Int) -> Unit,
    thresholdText: String,
    onThresholdChange: (String) -> Unit,
    alertsEnabled: Boolean,
    onAlertsChange: (Boolean) -> Unit,
    darkMode: Boolean,
    onDarkModeChange: (Boolean) -> Unit,
    connectionMessage: String
) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        Text("Settings", fontSize = 24.sp, fontWeight = FontWeight.ExtraBold)
        Card(shape = RoundedCornerShape(18.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Mobile API connection", fontWeight = FontWeight.Bold, fontSize = 17.sp)
                OutlinedTextField(
                    value = baseUrlDraft,
                    onValueChange = onBaseUrlChange,
                    label = { Text("API base URL") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Text("Android emulator default: http://10.0.2.2:8000. On a phone, use your server URL or your computer's LAN address.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Button(onClick = onSaveBaseUrl, modifier = Modifier.fillMaxWidth()) { Text("Save URL and test connection") }
                if (connectionMessage.isNotBlank()) Text(connectionMessage, color = MaterialTheme.colorScheme.primary)
            }
        }
        Card(shape = RoundedCornerShape(18.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Text("Refresh and notifications", fontWeight = FontWeight.Bold, fontSize = 17.sp)
                Text("Foreground refresh: " + refreshSeconds + " seconds")
                Slider(
                    value = refreshSeconds.toFloat(),
                    onValueChange = { onRefreshSecondsChange(it.toInt().coerceIn(15, 120)) },
                    valueRange = 15f..120f,
                    steps = 6
                )
                OutlinedTextField(
                    value = thresholdText,
                    onValueChange = onThresholdChange,
                    label = { Text("Alert threshold (% before transfer costs)") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Opportunity alerts", fontWeight = FontWeight.SemiBold)
                        Text("Background checks run periodically, typically no more often than every 15 minutes.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Switch(checked = alertsEnabled, onCheckedChange = onAlertsChange)
                }
                Text("Alerts use estimates before transfer costs and may be stale by the time you open them.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            }
        }
        Card(shape = RoundedCornerShape(18.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Appearance", fontWeight = FontWeight.Bold, fontSize = 17.sp)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Dark theme", modifier = Modifier.weight(1f))
                    Switch(checked = darkMode, onCheckedChange = onDarkModeChange)
                }
            }
        }
        Card(shape = RoundedCornerShape(18.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Icon(Icons.Filled.Info, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                Text("Read-only by design", fontWeight = FontWeight.Bold)
                Text("ArbiScope does not trade, withdraw assets, connect a wallet or need exchange API keys. Price differences are estimates, not guaranteed profit. Verify the exact token contract, transfer network, fees, exchange status and liquidity yourself.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Spacer(Modifier.height(10.dp))
    }
}

@Composable
private fun MetricLine(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
        Text(label, modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(12.dp))
        Text(value, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun StatusPanel(title: String, body: String, warning: Boolean) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 5.dp),
        shape = RoundedCornerShape(16.dp),
        border = BorderStroke(1.dp, if (warning) Color(0xFFE3A841) else MaterialTheme.colorScheme.outlineVariant)
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.Top) {
            Icon(
                if (warning) Icons.Filled.Warning else Icons.Filled.Info,
                contentDescription = null,
                tint = if (warning) Color(0xFFE3A841) else MaterialTheme.colorScheme.primary
            )
            Spacer(Modifier.width(10.dp))
            Column {
                Text(title, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(3.dp))
                Text(body, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun LoadingPanel() {
    Column(
        Modifier.fillMaxWidth().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        androidx.compose.material3.CircularProgressIndicator()
        Text("Loading live market data…")
    }
}

@Composable
private fun EmptyPanel(title: String, body: String) {
    Column(
        Modifier.fillMaxWidth().padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Icon(Icons.Filled.ShowChart, contentDescription = null, modifier = Modifier.size(42.dp), tint = MaterialTheme.colorScheme.primary)
        Text(title, fontWeight = FontWeight.Bold, fontSize = 18.sp)
        Text(body, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun money(value: Double): String {
    if (!value.isFinite()) return "—"
    return "$" + String.format(Locale.US, "%,.2f", value)
}

private fun pct(value: Double): String {
    if (!value.isFinite()) return "—"
    return String.format(Locale.US, "%.2f%%", value)
}

private fun openExternal(context: Context, url: String) {
    if (!url.startsWith("https://") && !url.startsWith("http://")) {
        Toast.makeText(context, "Exchange link is not available for this route.", Toast.LENGTH_SHORT).show()
        return
    }
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
        .onFailure { Toast.makeText(context, "Could not open exchange link.", Toast.LENGTH_SHORT).show() }
}
