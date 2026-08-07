/**
 * API-driven reminder sync.
 * Fetches reminders from backend, diffs against local cache,
 * cancels removed/changed and schedules new/updated notifications.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Notifications from 'expo-notifications';
import { getDeviceId } from './deviceId';

const REMINDERS_CACHE_KEY = 'syncup_reminders';
const REMINDERS_IDS_KEY = 'syncup_reminder_notif_ids'; // Maps reminder.id → notification identifier

/**
 * Fetch reminders from backend API.
 * @param {string} apiUrl
 * @param {string} userId
 * @returns {object[]|null}
 */
export async function fetchReminders(apiUrl, userId) {
  try {
    if (!apiUrl || !userId) return null;

    const deviceId = await getDeviceId();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000); // 5s max — must not block critical flows

    const response = await fetch(
      `${apiUrl}/device/api/reminders?user_id=${encodeURIComponent(userId)}&device_id=${encodeURIComponent(deviceId)}`,
      { method: 'GET', signal: controller.signal }
    );
    clearTimeout(timer);

    if (!response.ok) return null;

    const data = await response.json();
    return Array.isArray(data.reminders) ? data.reminders : [];
  } catch {
    return null;
  }
}

/**
 * Get locally cached reminders.
 * @returns {object[]}
 */
export async function getLocalReminders() {
  try {
    const raw = await AsyncStorage.getItem(REMINDERS_CACHE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

/**
 * Get the mapping of reminder ID → scheduled notification identifier.
 * @returns {object}
 */
async function getNotifIdMap() {
  try {
    const raw = await AsyncStorage.getItem(REMINDERS_IDS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

async function saveNotifIdMap(map) {
  await AsyncStorage.setItem(REMINDERS_IDS_KEY, JSON.stringify(map));
}

/**
 * Build an expo-notifications trigger from a reminder object.
 * @param {object} rem - Reminder from API
 * @returns {object|null} Trigger config or null if invalid
 */
function buildTrigger(rem) {
  switch (rem.type) {
    case 'daily':
      return {
        type: 'daily',
        hour: rem.hour ?? 9,
        minute: rem.minute ?? 0,
      };
    case 'weekly':
      return {
        type: 'weekly',
        weekday: rem.weekday ?? 1, // 1=Sunday, 2=Monday, ...
        hour: rem.hour ?? 9,
        minute: rem.minute ?? 0,
      };
    case 'interval':
      if (!rem.seconds || rem.seconds < 60) return null;
      return {
        type: 'timeInterval',
        seconds: rem.seconds,
        repeats: true,
      };
    case 'once':
      if (!rem.date) return null;
      const d = new Date(rem.date);
      if (d.getTime() <= Date.now()) return null; // Already passed
      return {
        type: 'date',
        date: d,
      };
    default:
      return null;
  }
}

/**
 * Compute a simple hash of reminder properties to detect changes.
 */
function reminderHash(rem) {
  return JSON.stringify({
    id: rem.id,
    title: rem.title,
    body: rem.body,
    type: rem.type,
    hour: rem.hour,
    minute: rem.minute,
    weekday: rem.weekday,
    seconds: rem.seconds,
    date: rem.date,
    enabled: rem.enabled,
  });
}

/**
 * Sync reminders from backend. Diffs against cached list:
 * - Cancels removed/disabled/changed reminders
 * - Schedules new/updated reminders
 *
 * @param {string} apiUrl
 * @param {string} userId
 * @returns {{ synced: number, cancelled: number, error?: string }}
 */
export async function syncReminders(apiUrl, userId) {
  try {
    const reminders = await fetchReminders(apiUrl, userId);
    if (reminders === null) {
      return { synced: 0, cancelled: 0, error: 'fetch_failed' };
    }

    const oldList = await getLocalReminders();
    const notifMap = await getNotifIdMap();

    // Build hash maps for diffing
    const oldMap = {};
    for (const r of oldList) oldMap[r.id] = reminderHash(r);

    const newMap = {};
    for (const r of reminders) newMap[r.id] = reminderHash(r);

    let synced = 0;
    let cancelled = 0;

    // 1. Cancel removed or changed reminders
    for (const oldRem of oldList) {
      const notifId = notifMap[oldRem.id];
      if (!notifId) continue;

      // Removed from server or changed
      if (!newMap[oldRem.id] || newMap[oldRem.id] !== oldMap[oldRem.id]) {
        try {
          await Notifications.cancelScheduledNotificationAsync(notifId);
        } catch {}
        delete notifMap[oldRem.id];
        cancelled++;
      }
    }

    // 2. Schedule new or changed reminders
    for (const rem of reminders) {
      if (!rem.enabled) {
        // Disabled — cancel if exists
        if (notifMap[rem.id]) {
          try {
            await Notifications.cancelScheduledNotificationAsync(notifMap[rem.id]);
          } catch {}
          delete notifMap[rem.id];
          cancelled++;
        }
        continue;
      }

      // Already scheduled and unchanged — skip
      if (notifMap[rem.id] && oldMap[rem.id] === newMap[rem.id]) {
        continue;
      }

      const trigger = buildTrigger(rem);
      if (!trigger) continue;

      try {
        const notifId = await Notifications.scheduleNotificationAsync({
          content: {
            title: rem.title || 'Reminder',
            body: rem.body || '',
            sound: rem.sound !== false,
            data: { reminderId: rem.id, type: 'reminder' },
          },
          trigger,
        });
        notifMap[rem.id] = notifId;
        synced++;
      } catch (err) {
        console.warn(`[ReminderSync] Failed to schedule ${rem.id}:`, err?.message);
      }
    }

    // 3. Save updated state
    await AsyncStorage.setItem(REMINDERS_CACHE_KEY, JSON.stringify(reminders));
    await saveNotifIdMap(notifMap);

    console.log(`[ReminderSync] Synced: ${synced} scheduled, ${cancelled} cancelled, ${reminders.length} total from API`);
    return { synced, cancelled, total: reminders.length };
  } catch (err) {
    console.warn('[ReminderSync] Error:', err?.message);
    return { synced: 0, cancelled: 0, error: err?.message };
  }
}

/**
 * Cancel all scheduled reminders and clear cache.
 * Call on logout.
 */
export async function cancelAllReminders() {
  try {
    const notifMap = await getNotifIdMap();
    for (const notifId of Object.values(notifMap)) {
      try {
        await Notifications.cancelScheduledNotificationAsync(notifId);
      } catch {}
    }
    await AsyncStorage.removeItem(REMINDERS_CACHE_KEY);
    await AsyncStorage.removeItem(REMINDERS_IDS_KEY);
    console.log('[ReminderSync] All reminders cancelled');
  } catch {}
}

/**
 * Get count of currently scheduled reminder notifications.
 * @returns {number}
 */
export async function getScheduledReminderCount() {
  try {
    const notifMap = await getNotifIdMap();
    return Object.keys(notifMap).length;
  } catch {
    return 0;
  }
}

/**
 * Handle an incoming FCM data message for reminder management.
 * Supported actions:
 *   refresh    – re-fetch from API & sync (RECOMMENDED — most reliable across all OEMs)
 *   set        – schedule/update a single reminder  (data.reminder = JSON string)
 *   cancel     – cancel a single reminder           (data.reminder_id)
 *   sync_all   – full diff & sync                   (data.reminders = JSON string of array)
 *   cancel_all – cancel every scheduled reminder
 *
 * @param {object} data - FCM remoteMessage.data
 * @returns {boolean} true if the message was handled
 */
export async function handleReminderFCM(data) {
  if (!data || data.type !== 'reminder_sync') return false;

  const action = data.action;
  console.log(`[ReminderSync] FCM action=${action}`);

  try {
    switch (action) {
      case 'set': {
        const rem = typeof data.reminder === 'string' ? JSON.parse(data.reminder) : data.reminder;
        if (!rem?.id) break;

        const notifMap = await getNotifIdMap();

        // Cancel existing if any
        if (notifMap[rem.id]) {
          try { await Notifications.cancelScheduledNotificationAsync(notifMap[rem.id]); } catch {}
          delete notifMap[rem.id];
        }

        if (rem.enabled === false) {
          // Just cancel, don't reschedule
          await saveNotifIdMap(notifMap);
          // Update cache — remove or mark disabled
          const cached = await getLocalReminders();
          const updated = cached.filter(r => r.id !== rem.id);
          updated.push(rem);
          await AsyncStorage.setItem(REMINDERS_CACHE_KEY, JSON.stringify(updated));
          break;
        }

        const trigger = buildTrigger(rem);
        if (!trigger) break;

        const notifId = await Notifications.scheduleNotificationAsync({
          content: {
            title: rem.title || 'Reminder',
            body: rem.body || '',
            sound: rem.sound !== false,
            data: { reminderId: rem.id, type: 'reminder' },
          },
          trigger,
        });
        notifMap[rem.id] = notifId;
        await saveNotifIdMap(notifMap);

        // Update cache
        const cached = await getLocalReminders();
        const idx = cached.findIndex(r => r.id === rem.id);
        if (idx >= 0) cached[idx] = rem; else cached.push(rem);
        await AsyncStorage.setItem(REMINDERS_CACHE_KEY, JSON.stringify(cached));
        console.log(`[ReminderSync] FCM set ${rem.id} → ${notifId}`);
        break;
      }

      case 'cancel': {
        const remId = data.reminder_id;
        if (!remId) break;

        const notifMap = await getNotifIdMap();
        if (notifMap[remId]) {
          try { await Notifications.cancelScheduledNotificationAsync(notifMap[remId]); } catch {}
          delete notifMap[remId];
          await saveNotifIdMap(notifMap);
        }

        // Remove from cache
        const cached = await getLocalReminders();
        await AsyncStorage.setItem(REMINDERS_CACHE_KEY, JSON.stringify(cached.filter(r => r.id !== remId)));
        console.log(`[ReminderSync] FCM cancelled ${remId}`);
        break;
      }

      case 'sync_all': {
        // Full sync — parse the array, diff against local, schedule/cancel as needed
        const reminders = typeof data.reminders === 'string' ? JSON.parse(data.reminders) : data.reminders;
        if (!Array.isArray(reminders)) break;

        const oldList = await getLocalReminders();
        const notifMap = await getNotifIdMap();

        const oldHashMap = {};
        for (const r of oldList) oldHashMap[r.id] = reminderHash(r);
        const newHashMap = {};
        for (const r of reminders) newHashMap[r.id] = reminderHash(r);

        // Cancel removed/changed
        for (const oldRem of oldList) {
          const nid = notifMap[oldRem.id];
          if (!nid) continue;
          if (!newHashMap[oldRem.id] || newHashMap[oldRem.id] !== oldHashMap[oldRem.id]) {
            try { await Notifications.cancelScheduledNotificationAsync(nid); } catch {}
            delete notifMap[oldRem.id];
          }
        }

        // Schedule new/changed
        for (const rem of reminders) {
          if (!rem.enabled) {
            if (notifMap[rem.id]) {
              try { await Notifications.cancelScheduledNotificationAsync(notifMap[rem.id]); } catch {}
              delete notifMap[rem.id];
            }
            continue;
          }
          if (notifMap[rem.id] && oldHashMap[rem.id] === newHashMap[rem.id]) continue;

          const trigger = buildTrigger(rem);
          if (!trigger) continue;
          try {
            const notifId = await Notifications.scheduleNotificationAsync({
              content: {
                title: rem.title || 'Reminder',
                body: rem.body || '',
                sound: rem.sound !== false,
                data: { reminderId: rem.id, type: 'reminder' },
              },
              trigger,
            });
            notifMap[rem.id] = notifId;
          } catch {}
        }

        await AsyncStorage.setItem(REMINDERS_CACHE_KEY, JSON.stringify(reminders));
        await saveNotifIdMap(notifMap);
        console.log(`[ReminderSync] FCM sync_all — ${reminders.length} reminders`);
        break;
      }

      case 'cancel_all': {
        await cancelAllReminders();
        console.log('[ReminderSync] FCM cancel_all done');
        break;
      }

      case 'refresh': {
        // Lightweight trigger — just re-fetch from API and sync.
        // Recommended action: backend sends { type: "reminder_sync", action: "refresh" }
        // and the device does a full API sync. Most reliable on all OEMs.
        const apiUrl = await AsyncStorage.getItem('syncup_api_base');
        const userId = await AsyncStorage.getItem('syncup_bg_user_id');
        if (apiUrl && userId) {
          const result = await syncReminders(apiUrl, userId);
          console.log(`[ReminderSync] FCM refresh — synced:${result.synced}, cancelled:${result.cancelled}`);
        } else {
          console.warn('[ReminderSync] FCM refresh — no apiUrl or userId');
        }
        break;
      }

      default:
        console.warn(`[ReminderSync] Unknown FCM action: ${action}`);
        return false;
    }
  } catch (err) {
    console.warn(`[ReminderSync] FCM handler error:`, err?.message);
  }

  return true;
}
