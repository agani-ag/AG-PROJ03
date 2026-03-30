import * as Contacts from 'expo-contacts';

/**
 * Sync contacts to backend
 * @param {string} apiUrl - Base API URL
 * @param {string} userId - User ID (email/username)
 * @param {string} deviceId - Device ID
 * @returns {Promise<{success: boolean, message: string}>}
 */
export async function syncContacts(apiUrl, userId, deviceId) {
  try {
    console.log('[Sync] Starting contacts sync...');

    const syncData = {
      user_id: userId,
      device_id: deviceId,
      timestamp: new Date().toISOString(),
      contacts: [],
    };

    // Collect Contacts
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

    // Send to backend
    console.log('[Sync] Sending data to backend...');
    console.log(`[Sync] Contacts: ${syncData.contacts.length}`);

    const response = await fetch(`${apiUrl}/device/api/sync/data`, {
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
 * @returns {Promise<{contacts: boolean}>}
 */
export async function checkSyncPermissions() {
  const permissions = {
    contacts: false,
  };

  try {
    const { status: contactsStatus } = await Contacts.getPermissionsAsync();
    permissions.contacts = contactsStatus === 'granted';

    console.log('[Sync] Permissions available:', permissions);
    return permissions;
  } catch (err) {
    console.error('[Sync] Permission check error:', err);
    return permissions;
  }
}
