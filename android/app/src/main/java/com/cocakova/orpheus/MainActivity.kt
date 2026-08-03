package com.cocakova.orpheus

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.isSystemInDarkTheme
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Accessibility
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.DateFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { OrpheusApp() }
    }
}

private fun hasMic(context: Context): Boolean =
    ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
        PackageManager.PERMISSION_GRANTED

private fun isAccessibilityEnabled(context: Context): Boolean {
    val enabled = Settings.Secure.getString(
        context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
    ) ?: return false
    return enabled.contains(context.packageName + "/")
}

@OptIn(ExperimentalMaterial3Api::class)
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
    var sttApiKey by remember { mutableStateOf(prefs.sttApiKey) }
    var sttModel by remember { mutableStateOf(prefs.sttModel) }
    var rawMode by remember { mutableStateOf(prefs.rawMode) }
    var retention by remember { mutableStateOf(prefs.retentionDays) }
    var testing by remember { mutableStateOf(false) }
    var confirmClear by remember { mutableStateOf(false) }
    var pendingDelete by remember { mutableStateOf<TranscriptEntry?>(null) }
    val scope = rememberCoroutineScope()

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
            }
        }
        lifecycleOwner.lifecycle.addObserver(obs)
        onDispose { lifecycleOwner.lifecycle.removeObserver(obs) }
    }

    val micLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { micGranted = it }

    val colors = if (isSystemInDarkTheme()) {
        darkColorScheme(primary = Color(0xFFB39DFF), secondary = Color(0xFF9C8CFF))
    } else {
        lightColorScheme(primary = Color(0xFF6650C9), secondary = Color(0xFF7C6CF0))
    }

    MaterialTheme(colorScheme = colors) {
        Scaffold { padding ->
            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(padding),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                item {
                    Column {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                Icons.Default.GraphicEq, contentDescription = null,
                                tint = MaterialTheme.colorScheme.primary,
                            )
                            Spacer(Modifier.width(8.dp))
                            Text(
                                "Orpheus",
                                style = MaterialTheme.typography.headlineMedium,
                                fontWeight = FontWeight.Bold,
                            )
                        }
                        Text(
                            "Dictate anywhere. Your voice never leaves your network.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                item {
                    SetupCard(
                        micGranted = micGranted,
                        accessibilityOn = accessibilityOn,
                        serverSet = sttUrl.isNotBlank(),
                        onRequestMic = { micLauncher.launch(Manifest.permission.RECORD_AUDIO) },
                        onOpenAccessibility = {
                            context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                        },
                    )
                }

                item { StatsRow(tallies) }

                item {
                    Card {
                        Column(
                            Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Text(
                                "Speech-to-text server",
                                style = MaterialTheme.typography.titleMedium,
                            )
                            Text(
                                "Any OpenAI-compatible endpoint: your Orpheus server, OpenAI, Groq…",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            OutlinedTextField(
                                value = sttUrl,
                                onValueChange = {
                                    sttUrl = it
                                    prefs.sttUrl = it.trim()
                                },
                                label = { Text("Server URL") },
                                placeholder = { Text("http://your-stt-host:8123") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = sttApiKey,
                                onValueChange = {
                                    sttApiKey = it
                                    prefs.sttApiKey = it.trim()
                                },
                                label = { Text("API key (optional)") },
                                singleLine = true,
                                visualTransformation = PasswordVisualTransformation(),
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = sttModel,
                                onValueChange = {
                                    sttModel = it
                                    prefs.sttModel = it.trim()
                                },
                                label = { Text("Model (optional)") },
                                placeholder = { Text("e.g. whisper-1") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        "Skip AI cleanup",
                                        style = MaterialTheme.typography.bodyMedium,
                                    )
                                    Text(
                                        "Paste the raw transcription, unpolished (Orpheus servers only)",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                Switch(
                                    checked = rawMode,
                                    onCheckedChange = {
                                        rawMode = it
                                        prefs.rawMode = it
                                    },
                                )
                            }
                            TextButton(
                                enabled = !testing && sttUrl.isNotBlank(),
                                onClick = {
                                    testing = true
                                    scope.launch {
                                        val result = withContext(Dispatchers.IO) {
                                            runCatching {
                                                SttClient(prefs.sttUrl, prefs.sttApiKey)
                                                    .testConnection()
                                            }
                                        }
                                        testing = false
                                        result.onSuccess { id ->
                                            Toast.makeText(
                                                context,
                                                if (id.isBlank()) "Connected"
                                                else "Connected — $id",
                                                Toast.LENGTH_SHORT,
                                            ).show()
                                        }.onFailure { e ->
                                            Toast.makeText(
                                                context,
                                                "Failed: ${e.message}",
                                                Toast.LENGTH_LONG,
                                            ).show()
                                        }
                                    }
                                },
                            ) { Text(if (testing) "Testing…" else "Test connection") }
                        }
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

                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                "Keep transcripts",
                                style = MaterialTheme.typography.bodyMedium,
                            )
                            Text(
                                "Older entries are deleted automatically. Word counts are kept forever.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        var menuOpen by remember { mutableStateOf(false) }
                        Box {
                            TextButton(onClick = { menuOpen = true }) {
                                Text(
                                    RETENTION_OPTIONS.firstOrNull { it.second == retention }?.first
                                        ?: "$retention days"
                                )
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
                                            entries = log.readAll()
                                        },
                                    )
                                }
                            }
                        }
                    }
                }

                if (entries.isEmpty()) {
                    item {
                        Text(
                            "Nothing dictated yet. Open any app, tap a text field, and press the Orpheus bubble.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                } else {
                    items(entries.asReversed()) { entry ->
                        TranscriptCard(
                            entry,
                            onCopy = {
                                copyToClipboard(context, entry.text)
                                Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
                            },
                            onDelete = { pendingDelete = entry },
                        )
                    }
                }
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

@Composable
private fun SetupCard(
    micGranted: Boolean,
    accessibilityOn: Boolean,
    serverSet: Boolean,
    onRequestMic: () -> Unit,
    onOpenAccessibility: () -> Unit,
) {
    Card {
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
                label = "Accessibility service (floating bubble)",
                done = accessibilityOn,
                actionLabel = "Enable",
                onAction = onOpenAccessibility,
            )
            SetupRow(
                icon = Icons.Default.Cloud,
                label = "STT server configured",
                done = serverSet,
                actionLabel = null,
                onAction = {},
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
                tint = Color(0xFF4CAF50),
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

@Composable
private fun StatsRow(tallies: List<DayTally>) {
    // Stats come from the per-day tallies, which outlive pruned transcripts.
    val fmt = remember { SimpleDateFormat("yyyy-MM-dd", Locale.US) }
    val todayKey = fmt.format(Date())
    val weekFloor = fmt.format(Date(System.currentTimeMillis() - 6L * 24 * 60 * 60 * 1000))
    val today = tallies.firstOrNull { it.day == todayKey }?.words ?: 0
    val week = tallies.filter { it.day >= weekFloor }.sumOf { it.words }
    val all = tallies.sumOf { it.words }

    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        StatCard("Today", today, Modifier.weight(1f))
        StatCard("7 days", week, Modifier.weight(1f))
        StatCard("All time", all, Modifier.weight(1f))
    }
}

@Composable
private fun StatCard(label: String, words: Int, modifier: Modifier = Modifier) {
    Card(modifier = modifier) {
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

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun TranscriptCard(entry: TranscriptEntry, onCopy: () -> Unit, onDelete: () -> Unit) {
    val time = remember(entry.ts) {
        DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
            .format(Date(entry.ts))
    }
    Card(
        // tap = copy, long-press = delete
        modifier = Modifier.fillMaxWidth()
            .combinedClickable(onClick = onCopy, onLongClick = onDelete),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row {
                Text(
                    time,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    "${entry.words} words",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            Spacer(Modifier.height(4.dp))
            Text(entry.text, style = MaterialTheme.typography.bodyMedium)
        }
    }
}
