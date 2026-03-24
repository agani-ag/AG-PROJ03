import { useEffect, useRef } from 'react';
import { Platform, Alert } from 'react-native';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import messaging from '@react-native-firebase/messaging';

/**
 * Request notification permissions and get FCM token (Firebase directly)
 * Returns: { token: string, error: string }
 *
 * NOTE: On Android 13+, we use expo-notifications to check/request the system permission,
 * then Firebase messaging to get the FCM token.
 */
export async function registerForPushNotifications() {
  try {
    console.log('[FCM] Starting registration...');
    console.log('[FCM] Device.isDevice:', Device.isDevice);

    // Only works on physical devices
    if (!Device.isDevice) {
      console.warn('[FCM] Not a physical device, aborting');
      return { token: null, error: 'Must use physical device for push notifications' };
    }

    // Check Android system notification permission (Android 13+)
    const { status: notificationStatus } = await Notifications.getPermissionsAsync();
    console.log('[FCM] Android notification permission status:', notificationStatus);

    if (notificationStatus !== 'granted') {
      console.warn('[FCM] Android notification permission not granted');
      return { token: null, error: 'Notification permission not granted in Android settings' };
    }

    console.log('[FCM] Android notification permission confirmed granted');

    // Get FCM token from Firebase (no need to request permission again, already checked above)
    console.log('[FCM] Getting FCM token...');
    const token = await messaging().getToken();
    console.log('[FCM] Token obtained:', token.substring(0, 40) + '...');

    return { token, error: null };
  } catch (err) {
    console.error('[FCM] Error:', err.message);
    return { token: null, error: err.message };
  }
}

/**
 * Send FCM token to backend server
 */
export async function registerTokenWithBackend(apiUrl, deviceId, userId, pushToken) {
  console.log('[FCM Backend] Registering token...');

  try {
    const response = await fetch(`${apiUrl}/api/device/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_id: deviceId,
        user_id: userId,
        push_token: pushToken,
        platform: Platform.OS,
      }),
    });

    const data = await response.json();
    console.log('[FCM Backend] Response:', data.success ? 'Success' : 'Failed');
    return data.success;
  } catch (err) {
    console.error('[FCM Backend] Error:', err.message);
    return false;
  }
}

/**
 * Setup Firebase message handlers (call once in App.js or root component)
 */
export function setupNotificationHandlers() {
  // Foreground messages - show alert since Firebase doesn't auto-display
  const unsubscribe = messaging().onMessage(async (remoteMessage) => {
    console.log('[FCM] Foreground message:', remoteMessage);
    Alert.alert(
      remoteMessage.notification?.title || 'Notification',
      remoteMessage.notification?.body || '',
    );
  });

  // Background/quit message handler is set in index.js
  return unsubscribe;
}

/**
 * Hook to setup notification listeners
 */
export function useNotifications() {
  const unsubscribeRef = useRef(null);

  useEffect(() => {
    // Setup foreground handler
    unsubscribeRef.current = setupNotificationHandlers();

    // Handle notification that opened the app from background
    messaging()
      .getInitialNotification()
      .then((remoteMessage) => {
        if (remoteMessage) {
          console.log('[FCM] App opened from notification:', remoteMessage);
        }
      });

    // Handle notification tap when app is in background
    const unsubscribeOpen = messaging().onNotificationOpenedApp((remoteMessage) => {
      console.log('[FCM] Notification tapped (background):', remoteMessage);
    });

    return () => {
      if (unsubscribeRef.current) unsubscribeRef.current();
      unsubscribeOpen();
    };
  }, []);
}
