package com.firdous.arbiscope

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

class OpportunityNotificationWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val baseUrl = inputData.getString(KEY_BASE_URL) ?: return@withContext Result.failure()
        val threshold = inputData.getFloat(KEY_THRESHOLD, 0.5f).toDouble()
        if (!NotificationManagerCompat.from(applicationContext).areNotificationsEnabled()) {
            return@withContext Result.success()
        }
        try {
            val response = ApiClient.opportunities(
                baseUrl = baseUrl,
                q = "",
                sort = "profit",
                minSpread = "0",
                minLiquidity = "0",
                quote = "",
                exchanges = emptySet()
            )
            if (response.stale) return@withContext Result.retry()
            val prefs = applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val now = System.currentTimeMillis()
            response.items.asSequence()
                .filter { it.estimatedProfitPct >= threshold }
                .take(30)
                .forEach { item ->
                    val key = "notified_" + item.id
                    val last = prefs.getLong(key, 0L)
                    if (now - last >= SIX_HOURS_MS) {
                        postNotification(item)
                        prefs.edit().putLong(key, now).apply()
                    }
                }
            Result.success()
        } catch (_: Exception) {
            Result.retry()
        }
    }

    private fun postNotification(item: Opportunity) {
        val manager = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Arbitrage alerts", NotificationManager.IMPORTANCE_DEFAULT)
            )
        }
        val intent = Intent(applicationContext, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            applicationContext,
            item.id.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val body = item.symbol + ": ~" + String.format("%.2f", item.estimatedProfitPct) +
            "% before transfer costs. Verify manually."
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_more)
            .setContentTitle(item.buyExchange + " → " + item.sellExchange)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()
        runCatching { NotificationManagerCompat.from(applicationContext).notify(item.id.hashCode(), notification) }
    }

    companion object {
        const val PREFS = "arbiscope_preferences"
        private const val CHANNEL_ID = "arbiscope_opportunities"
        private const val UNIQUE_WORK = "arbiscope_periodic_opportunity_alerts"
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_THRESHOLD = "profit_threshold"
        private const val SIX_HOURS_MS = 6 * 60 * 60 * 1000L

        fun schedule(context: Context, enabled: Boolean, baseUrl: String, threshold: Float) {
            val manager = WorkManager.getInstance(context)
            manager.cancelUniqueWork(UNIQUE_WORK)
            if (!enabled) return
            val data = androidx.work.workDataOf(
                KEY_BASE_URL to baseUrl,
                KEY_THRESHOLD to threshold
            )
            val request = PeriodicWorkRequestBuilder<OpportunityNotificationWorker>(15, TimeUnit.MINUTES)
                .setInputData(data)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            manager.enqueueUniquePeriodicWork(UNIQUE_WORK, ExistingPeriodicWorkPolicy.UPDATE, request)
        }
    }
}
