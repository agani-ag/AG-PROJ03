import { useEffect, useRef } from 'react';
import { Platform, Alert } from 'react-native';
import * as Device from 'expo-device';
import messaging from '@react-native-firebase/messaging';

/**
 * Request notification permissions and get FCM token (Firebase directly)
 * Returns: { token: string, error: string }
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

    // Request permission
    console.log('[FCM] Requesting permission...');
    const authStatus = await messaging().requestPermission();
    const enabled =
      authStatus === messaging.AuthorizationStatus.AUTHORIZED ||
      authStatus === messaging.AuthorizationStatus.PROVISIONAL;

    console.log('[FCM] Permission status:', authStatus, 'Enabled:', enabled);

    if (!enabled) {
      return { token: null, error: 'Notification permission not granted' };
    }

    // Get FCM token
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
