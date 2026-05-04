import { getDeviceId } from './deviceId';
import { clearStoredCredentials } from './secureAuth';
import { clearCloudConfig } from './cloudConfig';
import { clearBackupCache } from './cloudBackup';

/**
 * Unregister device token from backend on logout
 */
export async function unregisterDevice(apiUrl, userId) {
  try {
    const deviceId = await getDeviceId();

    console.log('[Logout] Unregistering device...');
    console.log('[Logout] Device ID:', deviceId);
    console.log('[Logout] User ID:', userId);

    const response = await fetch(`${apiUrl}/device/api/unregister`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_id: deviceId,
        user_id: userId,
      }),
    });

    const data = await response.json();

    if (data.success) {
      console.log('[Logout] Device unregistered successfully');
      return true;
    } else {
      console.warn('[Logout] Device unregister failed:', data.message);
      return false;
    }
  } catch (err) {
    console.error('[Logout] Device unregister error:', err.message);
    return false;
  }
}

/**
 * Clear any locally cached user data (including stored credentials)
 */
export async function clearUserData() {
  try {
    // Clear stored credentials for auto-login
    await clearStoredCredentials();
    // Clear cached Cloudinary config and backup tracking
    await clearCloudConfig();
    await clearBackupCache();
    console.log('[Logout] User data cleared');
    return true;
  } catch (err) {
    console.error('[Logout] Clear user data error:', err.message);
    return false;
  }
}
