package com.firdousratherr.arbitragescanner

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

private val Background = Color(0xFF0B1020)
private val Panel = Color(0xFF151D31)
private val Accent = Color(0xFF8B9DFF)
private val Muted = Color(0xFF9CA9C6)

data class OpportunityRow(
    val symbol: String,
    val buyExchange: String,
    val sellExchange: String,
    val buyAsk: Double,
    val sellBid: Double,
    val spread: Double,
    val estimate: Double,
    val warning: String
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(
                primary = Accent,
                background = Background,
                surface = Panel,
                onBackground = Color.White,
                onSurface = Color.White
            )) {
                ScannerHome()
            }
        }
    }
}

@androidx.compose.runtime.Composable
private fun ScannerHome() {
    var rows by remember { mutableStateOf<List<OpportunityRow>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var lastUpdated by remember { mutableStateOf("Not updated yet") }

    suspend fun refresh() {
        loading = true
        error = null
        try {
            val result = withContext(Dispatchers.IO) {
                val connection = (URL(BuildConfig.API_BASE_URL + "api/v1/opportunities?limit=50")
                    .openConnection() as HttpURLConnection)
                connection.connectTimeout = 8_000
                connection.readTimeout = 8_000
                connection.requestMethod = "GET"
                try {
                    val code = connection.responseCode
                    val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                    val body = stream.bufferedReader().use { it.readText() }
                    if (code !in 200..299) error("API returned HTTP $code")
                    val root = JSONObject(body)
                    val array = root.getJSONArray("items")
                    (0 until array.length()).map { index ->
                        val item = array.getJSONObject(index)
                        OpportunityRow(
                            symbol = item.optString("symbol", "Unknown"),
                            buyExchange = item.optString("buy_exchange", "?"),
                            sellExchange = item.optString("sell_exchange", "?"),
                            buyAsk = item.optDouble("buy_ask", 0.0),
                            sellBid = item.optDouble("sell_bid", 0.0),
                            spread = item.optDouble("gross_spread_pct", 0.0),
                            estimate = item.optDouble("provisional_fee_adjusted_spread_pct", 0.0),
                            warning = item.optJSONArray("warnings")?.optString(0)
                                ?: "Provisional estimate; verify details before trading."
                        )
                    }
                } finally {
                    connection.disconnect()
                }
            }
            rows = result
            lastUpdated = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
                .format(java.util.Date())
        } catch (exception: Exception) {
            error = exception.message ?: "Unable to reach mobile API"
        } finally {
            loading = false
        }
    }

    LaunchedEffect(Unit) {
        while (true) {
            refresh()
            delay(30_000)
        }
    }

    Surface(modifier = Modifier.fillMaxSize(), color = Background) {
        Column(modifier = Modifier.fillMaxSize().padding(18.dp)) {
            Text("ARBI SCANNER", color = Accent, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text("Market opportunities", fontSize = 28.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            Text("Read-only market monitor · Updated $lastUpdated", color = Muted, fontSize = 12.sp)
            Spacer(Modifier.height(16.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                MetricCard("LIVE RESULTS", rows.size.toString(), Modifier.weight(1f))
                MetricCard("TOP SPREAD", rows.maxOfOrNull { it.spread }?.let { "%.2f%%".format(it) } ?: "—", Modifier.weight(1f))
            }
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
                Text("Opportunities", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                Button(onClick = { /* LaunchedEffect refreshes; manual retry state is added in the next UI pass. */ }) {
                    Text("Auto refresh")
                }
            }
            Text("Estimates are provisional. Withdrawal fees, transfer status and executable depth are not yet verified.", color = Color(0xFFFFC879), fontSize = 12.sp)
            Spacer(Modifier.height(10.dp))

            if (loading && rows.isEmpty()) {
                Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = Accent)
                }
            } else if (error != null && rows.isEmpty()) {
                Column(Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(16.dp)).padding(16.dp)) {
                    Text("Could not load live data", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(4.dp))
                    Text(error ?: "", color = Muted)
                    Spacer(Modifier.height(8.dp))
                    Button(onClick = { /* retry is wired in the next UI pass */ }) { Text("Retry on next refresh") }
                }
            } else if (rows.isEmpty()) {
                Text("No opportunities returned yet. Check that the API is running and has completed a scan.", color = Muted)
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(rows, key = { "${it.symbol}-${it.buyExchange}-${it.sellExchange}" }) { row ->
                        OpportunityCard(row)
                    }
                }
            }
        }
    }
}

@androidx.compose.runtime.Composable
private fun MetricCard(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier.background(Panel, RoundedCornerShape(16.dp)).padding(14.dp)) {
        Text(label, color = Muted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(6.dp))
        Text(value, fontSize = 22.sp, fontWeight = FontWeight.Bold)
    }
}

@androidx.compose.runtime.Composable
private fun OpportunityCard(row: OpportunityRow) {
    Column(Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(16.dp)).padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
            Text(row.symbol, fontSize = 17.sp, fontWeight = FontWeight.Bold)
            Text("+%.2f%%".format(row.spread), color = Color(0xFF72E0B0), fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.height(8.dp))
        Text("BUY  ${row.buyExchange}  ·  ${row.buyAsk}", color = Color.White, fontSize = 13.sp)
        Text("SELL  ${row.sellExchange}  ·  ${row.sellBid}", color = Color.White, fontSize = 13.sp)
        Spacer(Modifier.height(8.dp))
        Text("Provisional after-fee estimate: %.3f%%".format(row.estimate), color = Accent, fontSize = 13.sp)
        Spacer(Modifier.height(8.dp))
        Text(row.warning, color = Muted, fontSize = 11.sp)
    }
}
