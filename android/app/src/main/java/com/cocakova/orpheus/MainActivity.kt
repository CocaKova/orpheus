package com.cocakova.orpheus

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
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
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
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
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import java.text.DateFormat
import java.util.Calendar
import java.util.Date

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
    var sttUrl by remember { mutableStateOf(prefs.sttUrl) }
    var sttApiKey by remember { mutableStateOf(prefs.sttApiKey) }
    var sttModel by remember { mutableStateOf(prefs.sttModel) }

    // Refresh permission states + history whenever we come back to the foreground
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val obs = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                micGranted = hasMic(context)
                accessibilityOn = isAccessibilityEnabled(context)
                entries = log.readAll()
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

                item { StatsRow(entries) }

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
                        }
                    }
                }

                item {
                    Text(
                        "History",
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.padding(top = 4.dp),
                    )
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
                        TranscriptCard(entry) {
                            val cm = context.getSystemService(Context.CLIPBOARD_SERVICE)
                                as ClipboardManager
                            cm.setPrimaryClip(ClipData.newPlainText("Orpheus", entry.text))
                            Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
                        }
                    }
                }
            }
        }
    }
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
private fun StatsRow(entries: List<TranscriptEntry>) {
    val todayStart = remember(entries) {
        Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }.timeInMillis
    }
    val weekStart = System.currentTimeMillis() - 7L * 24 * 60 * 60 * 1000
    val today = entries.filter { it.ts >= todayStart }.sumOf { it.words }
    val week = entries.filter { it.ts >= weekStart }.sumOf { it.words }
    val all = entries.sumOf { it.words }

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

@Composable
private fun TranscriptCard(entry: TranscriptEntry, onCopy: () -> Unit) {
    val time = remember(entry.ts) {
        DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
            .format(Date(entry.ts))
    }
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onCopy),
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
