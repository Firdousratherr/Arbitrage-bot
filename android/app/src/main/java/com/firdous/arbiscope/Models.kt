package com.firdous.arbiscope

import org.json.JSONArray
import org.json.JSONObject

data class Opportunity(
    val id: String,
    val symbol: String,
    val buyExchange: String,
    val sellExchange: String,
    val buyExchangeUrl: String,
    val sellExchangeUrl: String,
    val buyPrice: Double,
    val sellPrice: Double,
    val bestAsk: Double,
    val bestBid: Double,
    val spreadPct: Double,
    val depthAdjustedSpreadPct: Double,
    val estimatedProfitPct: Double,
    val estimatedProfitUsd: Double,
    val grossProfitUsd: Double,
    val estimatedSizeUsd: Double,
    val liquidityUsd: Double,
    val buyFeePct: Double,
    val sellFeePct: Double,
    val buyFeeUsd: Double,
    val sellFeeUsd: Double,
    val buyFeeSource: String,
    val sellFeeSource: String,
    val withdrawalFeeStatus: String,
    val networkVerification: String,
    val contractMatch: String,
    val verificationStatus: String,
    val estimateScope: String,
    val warning: String,
    val timestamp: String
) {
    companion object {
        fun fromJson(o: JSONObject): Opportunity = Opportunity(
            id = o.optString("id"),
            symbol = o.optString("symbol"),
            buyExchange = o.optString("buy_exchange"),
            sellExchange = o.optString("sell_exchange"),
            buyExchangeUrl = o.optString("buy_exchange_url"),
            sellExchangeUrl = o.optString("sell_exchange_url"),
            buyPrice = o.optDouble("buy_price"),
            sellPrice = o.optDouble("sell_price"),
            bestAsk = o.optDouble("best_ask_at_scan"),
            bestBid = o.optDouble("best_bid_at_scan"),
            spreadPct = o.optDouble("spread_pct"),
            depthAdjustedSpreadPct = o.optDouble("depth_adjusted_spread_pct"),
            estimatedProfitPct = o.optDouble("estimated_profit_pct_before_transfer"),
            estimatedProfitUsd = o.optDouble("estimated_profit_usd_before_transfer"),
            grossProfitUsd = o.optDouble("gross_profit_usd"),
            estimatedSizeUsd = o.optDouble("estimated_size_usd"),
            liquidityUsd = o.optDouble("liquidity_usd"),
            buyFeePct = o.optDouble("buy_fee_pct"),
            sellFeePct = o.optDouble("sell_fee_pct"),
            buyFeeUsd = o.optDouble("buy_fee_usd_estimate"),
            sellFeeUsd = o.optDouble("sell_fee_usd_estimate"),
            buyFeeSource = o.optJSONObject("fee_sources")?.optString("buy").orEmpty(),
            sellFeeSource = o.optJSONObject("fee_sources")?.optString("sell").orEmpty(),
            withdrawalFeeStatus = o.optString("withdrawal_fee_status", "unknown"),
            networkVerification = o.optString("network_verification", "unknown"),
            contractMatch = o.optString("contract_match", "unknown"),
            verificationStatus = o.optString("verification_status", "needs_manual_check"),
            estimateScope = o.optString("estimate_scope"),
            warning = o.optString("warning"),
            timestamp = o.optString("timestamp")
        )
    }
}

data class OpportunityResponse(
    val items: List<Opportunity>,
    val updatedAt: String,
    val stale: Boolean,
    val count: Int,
    val exchangeStatus: List<ExchangeStatus> = emptyList()
)

data class TriangleLeg(
    val from: String,
    val to: String,
    val symbol: String,
    val side: String,
    val price: Double,
    val feePct: Double,
    val feeSource: String
)

data class TriangleOpportunity(
    val id: String,
    val exchange: String,
    val route: String,
    val profitPct: Double,
    val profitUsdOn100: Double,
    val estimatedSizeUsd: Double,
    val estimateScope: String,
    val legs: List<TriangleLeg>
) {
    companion object {
        fun fromJson(o: JSONObject): TriangleOpportunity {
            val legsJson = o.optJSONArray("legs") ?: JSONArray()
            val legs = (0 until legsJson.length()).mapNotNull { index ->
                val leg = legsJson.optJSONObject(index) ?: return@mapNotNull null
                TriangleLeg(
                    from = leg.optString("from"),
                    to = leg.optString("to"),
                    symbol = leg.optString("symbol"),
                    side = leg.optString("side"),
                    price = leg.optDouble("price"),
                    feePct = leg.optDouble("fee") * 100.0,
                    feeSource = leg.optString("fee_source")
                )
            }
            return TriangleOpportunity(
                id = o.optString("id"),
                exchange = o.optString("exchange"),
                route = o.optString("route"),
                profitPct = o.optDouble("profit_pct_before_slippage"),
                profitUsdOn100 = o.optDouble("estimated_profit_usd_on_100"),
                estimatedSizeUsd = o.optDouble("estimated_size_usd", 100.0),
                estimateScope = o.optString("estimate_scope"),
                legs = legs
            )
        }
    }
}

data class TriangleResponse(
    val items: List<TriangleOpportunity>,
    val updatedAt: String,
    val stale: Boolean,
    val count: Int
)

data class ExchangeStatus(
    val exchange: String,
    val status: String,
    val marketCount: Int,
    val tickerCount: Int,
    val error: String
) {
    companion object {
        fun fromJson(o: JSONObject) = ExchangeStatus(
            exchange = o.optString("exchange"),
            status = o.optString("status"),
            marketCount = o.optInt("market_count"),
            tickerCount = o.optInt("ticker_count"),
            error = o.optString("error")
        )
    }
}
