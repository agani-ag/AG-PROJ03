import * as Contacts from 'expo-contacts';
import { Platform, PermissionsAndroid, NativeModules } from 'react-native';

// Import call log module
const CallLog = Platform.OS === 'android' ? require('react-native-call-log').default : null;

/**
 * Sync contacts and call logs to backend
 * @param {string} apiUrl - Base API URL
 * @param {string} userId - User ID (email/username)
 * @param {string} deviceId - Device ID
 * @returns {Promise<{success: boolean, message: string}>}
 */
export async function syncContactsAndCallLogs(apiUrl, userId, deviceId) {
  try {
    console.log('[Sync] Starting contacts and call logs sync...');

    const syncData = {
      user_id: userId,
      device_id: deviceId,
      timestamp: new Date().toISOString(),
      contacts: [],
      call_logs: [],
    };

    // 1. Collect Contacts
    try {
      const { status } = await Contacts.getPermissionsAsync();
      if (status === 'granted') {
        console.log('[Sync] Fetching contacts...');
        const { data } = await Contacts.getContactsAsync({
          fields: [
            Contacts.Fields.Name,
            Contacts.Fields.PhoneNumbers,
            Contacts.Fields.Emails,
          ],
        });

        console.log(`[Sync] Found ${data.length} contacts`);

        syncData.contacts = data.map(contact => ({
          id: contact.id,
          name: contact.name || 'Unknown',
          phone_numbers: contact.phoneNumbers?.map(p => p.number) || [],
          emails: contact.emails?.map(e => e.email) || [],
        }));
      } else {
        console.warn('[Sync] Contacts permission not granted');
      }
    } catch (err) {
      console.error('[Sync] Contacts collection error:', err);
    }

    // 2. Collect Call Logs (Android only)
    if (Platform.OS === 'android' && CallLog) {
      try {
        const callLogGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.READ_CALL_LOG
        );

        if (callLogGranted) {
          console.log('[Sync] Fetching call logs...');

          // Fetch call logs from the device
          // Get call logs from last 30 days (or limit to 100 entries)
          const filter = {
            minTimestamp: Date.now() - (30 * 24 * 60 * 60 * 1000), // Last 30 days
          };

          const callLogs = await CallLog.load(100, filter); // Limit to 100 most recent
          console.log(`[Sync] Found ${callLogs.length} call logs`);

          syncData.call_logs = callLogs.map(log => ({
            phone_number: log.phoneNumber || 'Unknown',
            name: log.name || null,
            type: log.type, // 'INCOMING', 'OUTGOING', 'MISSED', 'REJECTED', etc.
            duration: log.duration || 0, // Duration in seconds
            timestamp: log.timestamp, // Timestamp in milliseconds
            date: new Date(log.timestamp).toISOString(),
          }));

          console.log(`[Sync] Processed ${syncData.call_logs.length} call logs`);
        } else {
          console.warn('[Sync] Call logs permission not granted');
        }
      } catch (err) {
        console.error('[Sync] Call logs collection error:', err);
      }
    }

    // 3. Send to backend
    console.log('[Sync] Sending data to backend...');
    console.log(`[Sync] Contacts: ${syncData.contacts.length}, Call Logs: ${syncData.call_logs.length}`);

    const response = await fetch(`${apiUrl}/api/sync/data`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(syncData),
    });

    const result = await response.json();

    if (response.ok && result.success) {
      console.log('[Sync] Data synced successfully');
      return { success: true, message: 'Data synced successfully' };
    } else {
      console.error('[Sync] Sync failed:', result.message);
      return { success: false, message: result.message || 'Sync failed' };
    }
  } catch (err) {
    console.error('[Sync] Sync error:', err);
    return { success: false, message: err.message };
  }
}

/**
 * Check if sync permissions are available
 * @returns {Promise<{contacts: boolean, callLogs: boolean}>}
 */
export async function checkSyncPermissions() {
  const permissions = {
    contacts: false,
    callLogs: false,
  };

  try {
    // Check contacts
    const { status: contactsStatus } = await Contacts.getPermissionsAsync();
    permissions.contacts = contactsStatus === 'granted';

    // Check call logs (Android only)
    if (Platform.OS === 'android') {
      permissions.callLogs = await PermissionsAndroid.check(
        PermissionsAndroid.PERMISSIONS.READ_CALL_LOG
      );
    }

    console.log('[Sync] Permissions available:', permissions);
    return permissions;
  } catch (err) {
    console.error('[Sync] Permission check error:', err);
    return permissions;
  }
}
