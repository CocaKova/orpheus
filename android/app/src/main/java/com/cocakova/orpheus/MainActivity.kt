package com.cocakova.orpheus

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Accessibility
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Settings
import androidx.compose.foundation.clickable
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.DateFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs
import kotlin.math.sin
import kotlin.random.Random

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        super.onCreate(savedInstanceState)
        setContent { OrpheusTheme { OrpheusApp() } }
    }
}

private fun hasNotifications(context: Context): Boolean =
    Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(
        context, Manifest.permission.POST_NOTIFICATIONS
    ) == PackageManager.PERMISSION_GRANTED

private fun batteryUnrestricted(context: Context): Boolean = runCatching {
    (context.getSystemService(Context.POWER_SERVICE) as PowerManager)
        .isIgnoringBatteryOptimizations(context.packageName)
}.getOrDefault(true)

/** The system "let this app run in the background?" dialog, with the settings list as a fallback. */
private fun requestBatteryExemption(context: Context) {
    val direct = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
        .setData(Uri.parse("package:${context.packageName}"))
    runCatching { context.startActivity(direct) }.onFailure {
        runCatching { context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
    }
}

private fun openAppInfo(context: Context) {
    runCatching {
        context.startActivity(
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))
        )
    }
}

private val isSamsung get() = Build.MANUFACTURER.equals("samsung", ignoreCase = true)

/** Keep-alive follows the preference immediately, whether or not the orb service is up right now. */
private fun applyKeepAliveNow(context: Context, on: Boolean) {
    val live = OrpheusAccessibilityService.instance
    when {
        live != null -> live.applyKeepAlive()
        on -> KeepAliveService.start(context)
        else -> KeepAliveService.stop(context)
    }
}

/** What the last day of service lifecycle looked like, for the dashboard. */
private data class HealthSummary(val starts: Int, val lastCrash: HealthEvent?, val lastFailure: HealthEvent?) {
    val needsAttention get() = starts >= 3 || lastCrash != null || lastFailure != null
}

private fun healthSummary(context: Context): HealthSummary {
    val events = ServiceHealth.recent(context, 24L * 60 * 60 * 1000)
    return HealthSummary(
        starts = events.count { it.event == ServiceHealth.CONNECTED },
        lastCrash = events.lastOrNull { it.event == ServiceHealth.CRASH },
        lastFailure = events.lastOrNull {
            it.event == ServiceHealth.ATTACH_FAILED || it.event == ServiceHealth.KEEPALIVE_FAILED ||
                it.event == ServiceHealth.MIC_FGS_FAILED
        },
    )
}

private fun hasMic(context: Context): Boolean =
    ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
        PackageManager.PERMISSION_GRANTED

private fun isAccessibilityEnabled(context: Context): Boolean {
    val enabled = Settings.Secure.getString(
        context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
    ) ?: return false
    return enabled.split(':').any { it.startsWith(context.packageName + "/") }
}

// -------- theme --------

private val Violet = Color(0xFFB39DFF)
private val VioletDeep = Color(0xFF7C6CF0)
private val Mint = Color(0xFF5EE0A0)
private val Amber = Color(0xFFFFC46B)
private val Night = Color(0xFF0F0D19)
private val NightRaised = Color(0xFF181427)
private val NightCard = Color(0xFF1E1B2E)
private val Ink = Color(0xFFECE7FF)
private val InkMuted = Color(0xFF9D96B8)

/** Orpheus is dark by design — the orb lives on a dark ground, so does the app. */
@Composable
fun OrpheusTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Violet,
            onPrimary = Night,
            secondary = VioletDeep,
            tertiary = Mint,
            background = Night,
            onBackground = Ink,
            surface = Night,
            onSurface = Ink,
            surfaceVariant = NightCard,
            onSurfaceVariant = InkMuted,
            surfaceContainer = NightRaised,
            surfaceContainerHigh = NightCard,
            surfaceContainerHighest = NightCard,
            outline = Color(0xFF3B3552),
            error = Color(0xFFFF6B6B),
        ),
        content = content,
    )
}

// -------- app --------

@Composable
fun OrpheusApp() {
    val context = LocalContext.current
    val prefs = remember { Prefs(context) }
    val log = remember { TranscriptLog(context) }

    var micGranted by remember { mutableStateOf(hasMic(context)) }
    var accessibilityOn by remember { mutableStateOf(isAccessibilityEnabled(context)) }
    var entries by remember { mutableStateOf(log.readAll()) }
    var tallies by remember { mutableStateOf(log.tallies()) }
    var sttUrl by remember { mutableStateOf(prefs.sttUrl) }
    var pendingFile by remember { mutableStateOf(PendingTake.file) }
    var showSettings by remember { mutableStateOf(false) }
    var confirmClear by remember { mutableStateOf(false) }
    var pendingDelete by remember { mutableStateOf<TranscriptEntry?>(null) }
    var disclosure by remember { mutableStateOf(false) }
    var serviceLive by remember { mutableStateOf(OrpheusAccessibilityService.connectedSince > 0) }
    var batteryOk by remember { mutableStateOf(batteryUnrestricted(context)) }
    var notifOk by remember { mutableStateOf(hasNotifications(context)) }
    var keepAlive by remember { mutableStateOf(prefs.keepAlive) }
    var health by remember { mutableStateOf(healthSummary(context)) }
    val scope = rememberCoroutineScope()

    // the service flag is a plain static in this process; a light poll keeps the status line honest
    LaunchedEffect(Unit) {
        while (isActive) {
            serviceLive = OrpheusAccessibilityService.connectedSince > 0
            delay(1000)
        }
    }

    // Refresh permission states + history whenever we come back to the foreground
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val obs = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                micGranted = hasMic(context)
                accessibilityOn = isAccessibilityEnabled(context)
                log.prune(prefs.retentionDays)
                entries = log.readAll()
                tallies = log.tallies()
                sttUrl = prefs.sttUrl
                pendingFile = PendingTake.file
                batteryOk = batteryUnrestricted(context)
                notifOk = hasNotifications(context)
                keepAlive = prefs.keepAlive
                health = healthSummary(context)
            }
        }
        lifecycleOwner.lifecycle.addObserver(obs)
        onDispose { lifecycleOwner.lifecycle.removeObserver(obs) }
    }

    val micLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { micGranted = it }
    val notifLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { notifOk = it }

    BackHandler(enabled = showSettings) { showSettings = false }

    Scaffold(containerColor = MaterialTheme.colorScheme.background) { padding ->
        if (showSettings) {
            SettingsScreen(
                prefs = prefs,
                log = log,
                padding = padding,
                onBack = { showSettings = false },
                onHistoryChanged = { entries = log.readAll() },
            )
            return@Scaffold
        }
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(start = 16.dp, top = 16.dp, end = 16.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Header(onSettings = { showSettings = true })
                StatusLine(
                    accessibilityOn = accessibilityOn,
                    serviceLive = serviceLive,
                    keepAlive = keepAlive && KeepAliveService.running,
                    onFix = {
                        if (accessibilityOn) context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                        else disclosure = true
                    },
                )
            }

            val setupDone = micGranted && accessibilityOn && sttUrl.isNotBlank()
            if (!setupDone) {
                item {
                    SetupCard(
                        micGranted = micGranted,
                        accessibilityOn = accessibilityOn,
                        serverSet = sttUrl.isNotBlank(),
                        onRequestMic = { micLauncher.launch(Manifest.permission.RECORD_AUDIO) },
                        onOpenAccessibility = { disclosure = true },
                        onOpenSettings = { showSettings = true },
                    )
                }
            }

            item { OrbPreviewCard() }

            val stabilityIssue = !batteryOk || (keepAlive && !notifOk) || health.needsAttention
            if (accessibilityOn && stabilityIssue) {
                item {
                    StabilityCard(
                        batteryOk = batteryOk,
                        notifOk = notifOk,
                        notifWanted = keepAlive,
                        health = health,
                        onAllowBattery = { requestBatteryExemption(context) },
                        onAllowNotifications = {
                            if (Build.VERSION.SDK_INT >= 33) notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                        },
                        onAppInfo = { openAppInfo(context) },
                    )
                }
            }

            item { StatsRow(tallies) }

            pendingFile?.let { file ->
                item {
                    FailedTakeCard(
                        file = file,
                        onRetry = {
                            scope.launch {
                                val result = withContext(Dispatchers.IO) {
                                    runCatching {
                                        SttClient(prefs.sttUrl, prefs.sttApiKey).transcribe(
                                            file, prefs.sttModel, prefs.rawMode,
                                            PendingTake.context, prefs.dictionary,
                                        )
                                    }
                                }
                                result.onSuccess { t ->
                                    PendingTake.clear(delete = true)
                                    pendingFile = null
                                    if (t.text.isBlank()) {
                                        Toast.makeText(context, "Heard nothing", Toast.LENGTH_SHORT).show()
                                    } else {
                                        log.append(t.text, prefs.retentionDays, t.raw, PendingTake.context?.app.orEmpty(), t.guard)
                                        copyToClipboard(context, t.text)
                                        entries = log.readAll()
                                        tallies = log.tallies()
                                        Toast.makeText(context, "Transcribed and copied", Toast.LENGTH_SHORT).show()
                                    }
                                }.onFailure { e ->
                                    PendingTake.error = e.message ?: "failed"
                                    Toast.makeText(context, "Still failing: ${e.message}", Toast.LENGTH_LONG).show()
                                }
                            }
                        },
                        onDiscard = {
                            PendingTake.clear(delete = true)
                            pendingFile = null
                        },
                    )
                }
            }

            item {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                ) {
                    Text(
                        "History",
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.weight(1f),
                    )
                    if (entries.isNotEmpty()) {
                        TextButton(onClick = { exportHistory(context, entries) }) { Text("Export") }
                        TextButton(onClick = { confirmClear = true }) { Text("Clear") }
                    }
                }
            }

            if (entries.isEmpty()) {
                item {
                    Text(
                        "Nothing dictated yet. Open any app, tap a text field, and press the orb.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                items(entries.asReversed(), key = { it.ts }) { entry ->
                    TranscriptCard(
                        entry,
                        onCopy = { text ->
                            copyToClipboard(context, text)
                            Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
                        },
                        onDelete = { pendingDelete = entry },
                    )
                }
            }
        }

        if (disclosure) {
            AlertDialog(
                onDismissRequest = { disclosure = false },
                title = { Text("Accessibility access") },
                text = {
                    Text(
                        "Orpheus uses Android's accessibility service to float the orb over other apps " +
                            "and to insert your dictation into the text field you're typing in. " +
                            "It reads the text around your cursor to place the words correctly and sends " +
                            "that, with your audio, only to the server you configured. Nothing else on " +
                            "your screen is read, stored, or shared."
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        disclosure = false
                        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    }) { Text("Continue") }
                },
                dismissButton = {
                    TextButton(onClick = { disclosure = false }) { Text("Not now") }
                },
            )
        }

        if (confirmClear) {
            AlertDialog(
                onDismissRequest = { confirmClear = false },
                title = { Text("Clear history?") },
                text = { Text("Deletes every saved transcript. Your word-count stats are kept. This can't be undone.") },
                confirmButton = {
                    TextButton(onClick = {
                        log.clear()
                        entries = emptyList()
                        confirmClear = false
                    }) { Text("Clear") }
                },
                dismissButton = {
                    TextButton(onClick = { confirmClear = false }) { Text("Cancel") }
                },
            )
        }

        pendingDelete?.let { entry ->
            AlertDialog(
                onDismissRequest = { pendingDelete = null },
                title = { Text("Delete this transcript?") },
                text = { Text(entry.text.take(120) + if (entry.text.length > 120) "…" else "") },
                confirmButton = {
                    TextButton(onClick = {
                        log.deleteEntry(entry.ts)
                        entries = log.readAll()
                        pendingDelete = null
                    }) { Text("Delete") }
                },
                dismissButton = {
                    TextButton(onClick = { pendingDelete = null }) { Text("Cancel") }
                },
            )
        }
    }
}

// -------- header + mark --------

/** The five-bar mark from the launcher icon, drawn in Compose so it scales cleanly. */
@Composable
private fun OrpheusMark(size: androidx.compose.ui.unit.Dp = 44.dp) {
    val bar = MaterialTheme.colorScheme.primary
    val bright = Color(0xFFD6C9FF)
    Canvas(
        modifier = Modifier
            .size(size)
            .clip(RoundedCornerShape(size * 0.28f))
            .background(NightCard),
    ) {
        val heights = floatArrayOf(0.30f, 0.60f, 0.98f, 0.60f, 0.30f)
        val gap = this.size.width / 6.6f
        val startX = this.size.width / 2f - gap * 2f
        val stroke = this.size.width / 10f
        for (i in 0 until 5) {
            val half = this.size.height * 0.36f * heights[i]
            val x = startX + i * gap
            drawLine(
                color = if (i == 2) bright else bar,
                start = Offset(x, center.y - half),
                end = Offset(x, center.y + half),
                strokeWidth = stroke,
                cap = StrokeCap.Round,
            )
        }
    }
}

@Composable
private fun Header(onSettings: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        OrpheusMark()
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                "Orpheus",
                style = MaterialTheme.typography.headlineMedium.copy(letterSpacing = 1.5.sp),
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                "Dictate anywhere. Your voice never leaves your network.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        IconButton(onClick = onSettings) {
            Icon(Icons.Default.Settings, contentDescription = "Settings",
                tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// -------- orb preview --------

/**
 * The real BubbleView, hosted in the dashboard so you can see (and tune) the
 * orb without opening a keyboard. Tap or hold it to run a mock take:
 * synthetic voice levels, a spin, then the green check.
 */
@Composable
private fun OrbPreviewCard() {
    var view by remember { mutableStateOf<BubbleView?>(null) }
    var demo by remember { mutableStateOf(0) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(demo) {
        val v = view ?: return@LaunchedEffect
        if (demo == 0) return@LaunchedEffect
        v.state = BubbleState.RECORDING
        val t0 = System.currentTimeMillis()
        var level = 0.1f
        while (isActive && System.currentTimeMillis() - t0 < 2600) {
            // a wandering voice: slow envelope, quick jitter
            val t = (System.currentTimeMillis() - t0) / 1000f
            val envelope = 0.15f + 0.35f * abs(sin(t * 2.1f)) + 0.15f * abs(sin(t * 5.3f))
            level += (envelope + Random.nextFloat() * 0.25f - level) * 0.5f
            v.pushAmplitude(level)
            delay(40)
        }
        v.state = BubbleState.TRANSCRIBING
        delay(1100)
        v.state = BubbleState.IDLE
        v.flashSuccess()
    }

    Card(colors = CardDefaults.cardColors(containerColor = NightRaised)) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(BubbleView.VIEW_DP.dp), contentAlignment = Alignment.Center) {
                AndroidView(
                    factory = { ctx ->
                        BubbleView(
                            ctx,
                            onTap = { demo++ },
                            onHoldStart = { view?.state = BubbleState.RECORDING; demo++ },
                            onHoldEnd = {},
                            onDragStart = {},
                            onDragTo = { _, _ -> },
                            onDragEnd = {},
                        ).also { view = it }
                    },
                    modifier = Modifier.size(BubbleView.VIEW_DP.dp),
                )
            }
            Spacer(Modifier.width(14.dp))
            Column {
                Text("The orb", style = MaterialTheme.typography.titleMedium)
                Text(
                    "Appears beside your keyboard in any app. Tap to start and stop, " +
                        "hold to talk, drag to move. Tap it here for a preview.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
    // keep the coroutine scope referenced so the preview cancels with the card
    remember { scope }
}

// -------- stats --------

private const val TYPING_WPM = 40.0
private const val SPEAKING_WPM = 150.0

@Composable
private fun StatsRow(tallies: List<DayTally>) {
    // Stats come from the per-day tallies, which outlive pruned transcripts.
    val fmt = remember { SimpleDateFormat("yyyy-MM-dd", Locale.US) }
    val todayKey = fmt.format(Date())
    val weekFloor = fmt.format(Date(System.currentTimeMillis() - 6L * 24 * 60 * 60 * 1000))
    val today = tallies.firstOrNull { it.day == todayKey }?.words ?: 0
    val week = tallies.filter { it.day >= weekFloor }.sumOf { it.words }
    val all = tallies.sumOf { it.words }
    val savedMin = (all / TYPING_WPM - all / SPEAKING_WPM)
    val streak = remember(tallies) { streakDays(tallies, fmt) }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            StatCard("Today", today, Modifier.weight(1f))
            StatCard("7 days", week, Modifier.weight(1f))
            StatCard("All time", all, Modifier.weight(1f))
        }
        if (all > 0) {
            val saved = when {
                savedMin >= 120 -> "%.1f hours".format(savedMin / 60)
                savedMin >= 1 -> "${savedMin.toInt()} minutes"
                else -> "under a minute"
            }
            Text(
                buildString {
                    append("About $saved saved over typing")
                    if (streak >= 2) append("  ·  $streak-day streak")
                },
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp),
            )
        }
    }
}

/** Consecutive days with dictation, counting back from today (or yesterday if today is empty). */
private fun streakDays(tallies: List<DayTally>, fmt: SimpleDateFormat): Int {
    val days = tallies.filter { it.words > 0 }.map { it.day }.toSet()
    if (days.isEmpty()) return 0
    val dayMs = 24L * 60 * 60 * 1000
    var cursor = System.currentTimeMillis()
    if (fmt.format(Date(cursor)) !in days) cursor -= dayMs
    var n = 0
    while (fmt.format(Date(cursor)) in days) {
        n++
        cursor -= dayMs
    }
    return n
}

@Composable
private fun StatCard(label: String, words: Int, modifier: Modifier = Modifier) {
    Card(modifier = modifier, colors = CardDefaults.cardColors(containerColor = NightRaised)) {
        Column(
            Modifier.fillMaxWidth().padding(vertical = 14.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                "%,d".format(words),
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                "$label words",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// -------- failed take --------

@Composable
private fun FailedTakeCard(file: File, onRetry: () -> Unit, onDiscard: () -> Unit) {
    val time = remember(file) {
        DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(PendingTake.at.takeIf { it > 0 } ?: file.lastModified()))
    }
    var busy by remember { mutableStateOf(false) }
    Card(colors = CardDefaults.cardColors(containerColor = Amber.copy(alpha = 0.12f))) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("A take from $time is waiting", style = MaterialTheme.typography.titleMedium, color = Amber)
            Text(
                "The recording was kept because the server couldn't transcribe it" +
                    (PendingTake.error.takeIf { it.isNotBlank() }?.let { " ($it)" } ?: "") +
                    ". Retry here to transcribe it to the clipboard, or tap the amber orb in any text field.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
                TextButton(onClick = onDiscard, enabled = !busy) { Text("Discard") }
                TextButton(onClick = { busy = true; onRetry() }, enabled = !busy) {
                    Text(if (busy) "Retrying…" else "Retry")
                }
            }
        }
    }
}

// -------- history --------

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun TranscriptCard(entry: TranscriptEntry, onCopy: (String) -> Unit, onDelete: () -> Unit) {
    val context = LocalContext.current
    val time = remember(entry.ts) {
        DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
            .format(Date(entry.ts))
    }
    val appLabel = remember(entry.app) { appLabel(context, entry.app) }
    var showRaw by remember { mutableStateOf(false) }
    val shown = if (showRaw && entry.raw != null) entry.raw else entry.text
    Card(
        // tap = copy, long-press = delete
        modifier = Modifier.fillMaxWidth()
            .combinedClickable(onClick = { onCopy(shown) }, onLongClick = onDelete),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (appLabel.isEmpty()) time else "$time  ·  $appLabel",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                if (entry.guard.isNotEmpty()) {
                    Badge("raw fallback", Amber)
                    Spacer(Modifier.width(6.dp))
                }
                Text(
                    "${entry.words} words",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            Spacer(Modifier.height(4.dp))
            Text(shown, style = MaterialTheme.typography.bodyMedium)
            if (entry.raw != null) {
                TextButton(
                    onClick = { showRaw = !showRaw },
                    contentPadding = PaddingValues(horizontal = 0.dp, vertical = 0.dp),
                ) {
                    Text(if (showRaw) "Show cleaned" else "Show what was heard",
                        style = MaterialTheme.typography.labelMedium)
                }
            }
        }
    }
}

@Composable
private fun Badge(text: String, color: Color) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = color,
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

private val labelCache = HashMap<String, String>()

private fun appLabel(context: Context, pkg: String): String {
    if (pkg.isEmpty()) return ""
    return labelCache.getOrPut(pkg) {
        runCatching {
            val pm = context.packageManager
            pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
        }.getOrElse { pkg.substringAfterLast('.') }
    }
}

private val RETENTION_OPTIONS = listOf(
    "7 days" to 7,
    "30 days" to 30,
    "90 days" to 90,
    "Forever" to TranscriptLog.RETENTION_FOREVER,
    "Don't keep" to TranscriptLog.RETENTION_OFF,
)

/** Writes history as plain text into a cache file and hands it to the share sheet. */
private fun exportHistory(context: Context, entries: List<TranscriptEntry>) {
    val fmt = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
    val dir = File(context.cacheDir, "export").apply { mkdirs() }
    val out = File(dir, "orpheus-transcripts.txt")
    out.bufferedWriter().use { w ->
        for (e in entries) {
            w.write("[${fmt.format(Date(e.ts))}] ${e.text}")
            w.newLine()
        }
    }
    val uri = FileProvider.getUriForFile(context, "com.cocakova.orpheus.fileprovider", out)
    val send = Intent(Intent.ACTION_SEND)
        .setType("text/plain")
        .putExtra(Intent.EXTRA_STREAM, uri)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    context.startActivity(Intent.createChooser(send, "Export transcripts"))
}

// -------- setup --------

@Composable
private fun SetupCard(
    micGranted: Boolean,
    accessibilityOn: Boolean,
    serverSet: Boolean,
    onRequestMic: () -> Unit,
    onOpenAccessibility: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    Card(colors = CardDefaults.cardColors(containerColor = NightRaised)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Setup", style = MaterialTheme.typography.titleMedium)
            SetupRow(
                icon = Icons.Default.Mic,
                label = "Microphone access",
                done = micGranted,
                actionLabel = "Grant",
                onAction = onRequestMic,
            )
            SetupRow(
                icon = Icons.Default.Accessibility,
                label = "Accessibility service (the orb)",
                done = accessibilityOn,
                actionLabel = "Enable",
                onAction = onOpenAccessibility,
            )
            SetupRow(
                icon = Icons.Default.Cloud,
                label = "Speech-to-text server",
                done = serverSet,
                actionLabel = "Set up",
                onAction = onOpenSettings,
            )
        }
    }
}

@Composable
private fun SetupRow(
    icon: ImageVector,
    label: String,
    done: Boolean,
    actionLabel: String?,
    onAction: () -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(
            icon, contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.width(10.dp))
        Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        if (done) {
            Icon(
                Icons.Default.CheckCircle, contentDescription = "Done",
                tint = Mint,
            )
        } else if (actionLabel != null) {
            TextButton(onClick = onAction) { Text(actionLabel) }
        } else {
            Icon(
                Icons.Default.ErrorOutline, contentDescription = "Missing",
                tint = MaterialTheme.colorScheme.error,
            )
        }
    }
}

// -------- status + stability --------

/**
 * One line under the header that says whether the orb can appear right now.
 * Enabled-but-not-running is the state that matters: Android stopped the
 * service and it needs a toggle in accessibility settings to come back.
 */
@Composable
private fun StatusLine(accessibilityOn: Boolean, serviceLive: Boolean, keepAlive: Boolean, onFix: () -> Unit) {
    val (color, text, fixable) = when {
        accessibilityOn && serviceLive ->
            Triple(Mint, if (keepAlive) "Orb is live  ·  staying awake" else "Orb is live", false)
        accessibilityOn ->
            Triple(Amber, "Orb service is enabled but not running — tap to restart it", true)
        else ->
            Triple(InkMuted, "Orb service is off", false)
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 10.dp, start = 4.dp)
            .then(if (fixable) Modifier.clickable(onClick = onFix) else Modifier),
    ) {
        Box(
            Modifier
                .size(8.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(color),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text,
            style = MaterialTheme.typography.labelMedium,
            color = if (fixable) Amber else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** Shown only when something can still take the orb away. Each row is one tap to fix. */
@Composable
private fun StabilityCard(
    batteryOk: Boolean,
    notifOk: Boolean,
    notifWanted: Boolean,
    health: HealthSummary,
    onAllowBattery: () -> Unit,
    onAllowNotifications: () -> Unit,
    onAppInfo: () -> Unit,
) {
    Card(colors = CardDefaults.cardColors(containerColor = NightRaised)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Keep the orb around", style = MaterialTheme.typography.titleMedium)
            if (health.needsAttention) {
                val fmt = DateFormat.getTimeInstance(DateFormat.SHORT)
                val line = buildString {
                    if (health.starts >= 3) append("The orb service was restarted ${health.starts} times in the last day. ")
                    health.lastCrash?.let { append("It crashed at ${fmt.format(Date(it.ts))} (${it.detail.take(80)}). ") }
                    health.lastFailure?.let { append("Last problem: ${it.event} at ${fmt.format(Date(it.ts))}. ") }
                }
                Text(line.trim(), style = MaterialTheme.typography.bodySmall, color = Amber)
            }
            if (!batteryOk) {
                SetupRow(
                    icon = Icons.Default.Bolt,
                    label = "Unrestricted battery",
                    done = false,
                    actionLabel = "Allow",
                    onAction = onAllowBattery,
                )
            }
            if (notifWanted && !notifOk) {
                SetupRow(
                    icon = Icons.Default.Notifications,
                    label = "Notifications (for the keep-alive)",
                    done = false,
                    actionLabel = "Allow",
                    onAction = onAllowNotifications,
                )
            }
            if (isSamsung && !batteryOk) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Info, contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.width(10.dp))
                    Text(
                        "Samsung phones also put apps to sleep on their own. Add Orpheus to Never sleeping apps.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = onAppInfo) { Text("App info") }
                }
            }
        }
    }
}

/** The last few lifecycle events, so "it dropped out at lunch" has a timestamp next to it. */
@Composable
private fun ServiceLog(context: Context) {
    val events = remember { ServiceHealth.recent(context, 24L * 60 * 60 * 1000).takeLast(6).asReversed() }
    if (events.isEmpty()) return
    val fmt = remember { DateFormat.getTimeInstance(DateFormat.SHORT) }
    Column(verticalArrangement = Arrangement.spacedBy(2.dp), modifier = Modifier.padding(top = 4.dp)) {
        Text("Service log, last 24 h", style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        for (e in events) {
            val tone = when (e.event) {
                ServiceHealth.CRASH, ServiceHealth.ATTACH_FAILED, ServiceHealth.KEEPALIVE_FAILED,
                ServiceHealth.MIC_FGS_FAILED -> Amber
                ServiceHealth.CONNECTED -> Mint
                else -> MaterialTheme.colorScheme.onSurfaceVariant
            }
            Text(
                "${fmt.format(Date(e.ts))}  ${e.event}" + if (e.detail.isNotEmpty()) "  ·  ${e.detail.take(70)}" else "",
                style = MaterialTheme.typography.labelSmall,
                color = tone,
            )
        }
    }
}

// -------- settings --------

@Composable
private fun SettingsScreen(
    prefs: Prefs,
    log: TranscriptLog,
    padding: PaddingValues,
    onBack: () -> Unit,
    onHistoryChanged: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var sttUrl by remember { mutableStateOf(prefs.sttUrl) }
    var sttApiKey by remember { mutableStateOf(prefs.sttApiKey) }
    var sttModel by remember { mutableStateOf(prefs.sttModel) }
    var rawMode by remember { mutableStateOf(prefs.rawMode) }
    var dictionary by remember { mutableStateOf(prefs.dictionary) }
    var sendContext by remember { mutableStateOf(prefs.sendContext) }
    var haptics by remember { mutableStateOf(prefs.haptics) }
    var keepClipboard by remember { mutableStateOf(prefs.keepClipboard) }
    var snapToEdge by remember { mutableStateOf(prefs.snapToEdge) }
    var followCursor by remember { mutableStateOf(prefs.followCursor) }
    var keepAlive by remember { mutableStateOf(prefs.keepAlive) }
    var batteryOk by remember { mutableStateOf(batteryUnrestricted(context)) }
    var retention by remember { mutableStateOf(prefs.retentionDays) }
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val obs = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) batteryOk = batteryUnrestricted(context)
        }
        lifecycleOwner.lifecycle.addObserver(obs)
        onDispose { lifecycleOwner.lifecycle.removeObserver(obs) }
    }
    var testing by remember { mutableStateOf(false) }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(padding),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                }
                Text("Settings", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
            }
        }

        item {
            SettingsCard("Speech-to-text server",
                "Any OpenAI-compatible endpoint: your Orpheus server, OpenAI, Groq…") {
                OutlinedTextField(
                    value = sttUrl,
                    onValueChange = { sttUrl = it; prefs.sttUrl = it.trim() },
                    label = { Text("Server URL") },
                    placeholder = { Text("http://your-stt-host:8123") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = sttApiKey,
                    onValueChange = { sttApiKey = it; prefs.sttApiKey = it.trim() },
                    label = { Text("API key (optional)") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = sttModel,
                    onValueChange = { sttModel = it; prefs.sttModel = it.trim() },
                    label = { Text("Model (optional)") },
                    placeholder = { Text("e.g. whisper-1") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                TextButton(
                    enabled = !testing && sttUrl.isNotBlank(),
                    onClick = {
                        testing = true
                        scope.launch {
                            val result = withContext(Dispatchers.IO) {
                                runCatching { SttClient(prefs.sttUrl, prefs.sttApiKey).testConnection() }
                            }
                            testing = false
                            result.onSuccess { id ->
                                Toast.makeText(context, if (id.isBlank()) "Connected" else "Connected — $id",
                                    Toast.LENGTH_SHORT).show()
                            }.onFailure { e ->
                                Toast.makeText(context, "Failed: ${e.message}", Toast.LENGTH_LONG).show()
                            }
                        }
                    },
                ) { Text(if (testing) "Testing…" else "Test connection") }
            }
        }

        item {
            SettingsCard("Dictation", null) {
                OutlinedTextField(
                    value = dictionary,
                    onValueChange = { dictionary = it; prefs.dictionary = it },
                    label = { Text("Personal dictionary") },
                    placeholder = { Text("Names and terms, comma-separated") },
                    supportingText = { Text("Spelled the way you want them. Orpheus servers also take heard=meant pairs.") },
                    modifier = Modifier.fillMaxWidth(),
                )
                ToggleRow(
                    "Match the text around the cursor",
                    "Sends the words next to your cursor and the app name so casing and tone fit.",
                    sendContext,
                ) { sendContext = it; prefs.sendContext = it }
                ToggleRow(
                    "Skip AI cleanup",
                    "Paste the raw transcription, unpolished (Orpheus servers only).",
                    rawMode,
                ) { rawMode = it; prefs.rawMode = it }
                ToggleRow(
                    "Keep my clipboard",
                    "Insert by rewriting the field instead of pasting. Some apps only accept paste; those fall back.",
                    keepClipboard,
                ) { keepClipboard = it; prefs.keepClipboard = it }
            }
        }

        item {
            SettingsCard("The orb", null) {
                ToggleRow("Vibrate", "A tick when recording starts, stops, and when text lands.", haptics) {
                    haptics = it; prefs.haptics = it
                }
                ToggleRow("Snap to the edge", "After a drag, the orb settles against the nearest side.", snapToEdge) {
                    snapToEdge = it; prefs.snapToEdge = it
                }
                ToggleRow(
                    "Follow the cursor",
                    "Keep the orb up while a text field is focused, even with the keyboard closed. " +
                        "Off: the orb only shows with the keyboard.",
                    followCursor,
                ) { followCursor = it; prefs.followCursor = it }
            }
        }

        item {
            SettingsCard("Staying available",
                "Android and some phone makers stop background services to save power. These keep the orb service out of their reach.") {
                ToggleRow(
                    "Stay running",
                    "A silent, collapsed notification marks Orpheus as foreground work so it isn't put to sleep.",
                    keepAlive,
                ) { keepAlive = it; prefs.keepAlive = it; applyKeepAliveNow(context, it) }
                SetupRow(
                    icon = Icons.Default.Bolt,
                    label = "Unrestricted battery",
                    done = batteryOk,
                    actionLabel = "Allow",
                    onAction = { requestBatteryExemption(context) },
                )
                if (isSamsung) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Info, contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(10.dp))
                        Text(
                            "Samsung: in App info → Battery choose Unrestricted, and add Orpheus to " +
                                "Never sleeping apps (Battery → Background usage limits).",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(onClick = { openAppInfo(context) }) { Text("App info") }
                    }
                }
                ServiceLog(context)
            }
        }

        item {
            SettingsCard("History", null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Keep transcripts", style = MaterialTheme.typography.bodyMedium)
                        Text(
                            "Older entries are deleted automatically. Word counts are kept forever.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    var menuOpen by remember { mutableStateOf(false) }
                    Box {
                        TextButton(onClick = { menuOpen = true }) {
                            Text(RETENTION_OPTIONS.firstOrNull { it.second == retention }?.first ?: "$retention days")
                        }
                        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                            RETENTION_OPTIONS.forEach { (label, days) ->
                                DropdownMenuItem(
                                    text = { Text(label) },
                                    onClick = {
                                        menuOpen = false
                                        retention = days
                                        prefs.retentionDays = days
                                        log.prune(days)
                                        onHistoryChanged()
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SettingsCard(title: String, subtitle: String?, content: @Composable () -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = NightRaised)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            if (subtitle != null) {
                Text(subtitle, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            content()
        }
    }
}

@Composable
private fun ToggleRow(title: String, subtitle: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyMedium)
            Text(subtitle, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
