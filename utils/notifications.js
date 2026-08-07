import { useEffect, useRef } from 'react';
import { Platform, Alert } from 'react-native';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import messaging from '@react-native-firebase/messaging';
import { handleReminderFCM } from './reminderSync';

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
export async function registerTokenWithBackend(apiUrl, deviceId, userId, pushToken, instance) {
  console.log('[FCM Backend] Registering token...');

  try {
    const requestBody = {
      device_id: deviceId,
      user_id: userId,
      push_token: pushToken,
      platform: Platform.OS,
    };

    if (instance) {
      requestBody.instance = instance;
    }

    const response = await fetch(`${apiUrl}/device/api/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
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
 * Setup Android notification channel for foreground notifications
 */
async function ensureNotificationChannel() {
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('default', {
      name: 'Default',
      importance: Notifications.AndroidImportance.HIGH,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: '#4a90e2',
      sound: 'default',
    });
  }
}

/**
 * Show an Android system notification (status bar + notification tray)
 */
export async function showLocalNotification(title, body, data, image) {
  try {
    await ensureNotificationChannel();
    await Notifications.scheduleNotificationAsync({
      content: {
        title,
        body,
        data: data || {},
        sound: 'default',
      },
      trigger: null, // Show immediately
    });
    console.log('[FCM] Local system notification shown');
  } catch (err) {
    console.error('[FCM] Failed to show local notification:', err);
  }
}

/**
 * Flag to control in-app notification banner visibility.
 * Set to false to only show Android system notifications.
 * Set to true to show both in-app banner + Android system notification.
 */
let showInAppBanner = false;

export function setShowInAppBanner(value) {
  showInAppBanner = value;
  console.log('[FCM] In-app banner:', value ? 'enabled' : 'disabled');
}

export function getShowInAppBanner() {
  return showInAppBanner;
}

/**
 * Setup Firebase message handlers (call once in App.js or root component)
 * @param {function} onForegroundMessage - callback(title, body) for foreground notifications
 */
export function setupNotificationHandlers(onForegroundMessage) {
  // Foreground messages - show via callback or fallback to Alert
  const unsubscribe = messaging().onMessage(async (remoteMessage) => {
    console.log('[FCM] Foreground message:', JSON.stringify(remoteMessage));

    const data = remoteMessage.data || null;

    // Handle reminder data messages silently (no display notification)
    if (data?.type === 'reminder_sync') {
      await handleReminderFCM(data);
      return;
    }

    const title = remoteMessage.notification?.title || 'Notification';
    const body = remoteMessage.notification?.body || '';

    // Extract image from all possible locations
    const image =
      remoteMessage.notification?.android?.imageUrl ||
      remoteMessage.notification?.image ||
      remoteMessage.data?.image ||
      remoteMessage.data?.imageUrl ||
      null;
    console.log('[FCM] Extracted image URL:', image);

    // Check if Android system notifications are enabled
    const { status } = await Notifications.getPermissionsAsync();
    const systemNotifEnabled = (status === 'granted');

    if (systemNotifEnabled) {
      // System notifications enabled - show only Android system notification
      showLocalNotification(title, body, data, image);
    } else {
      // System notifications disabled - fall back to in-app banner
      if (onForegroundMessage) {
        onForegroundMessage(title, body, image, data);
      }
    }
  });

  // Background/quit message handler is set in index.js
  return unsubscribe;
}

/**
 * Hook to setup notification listeners
 * @param {function} onForegroundMessage - callback(title, body) for foreground notifications
 * @param {function} onNotificationTap - callback(data) when user taps a notification (background/quit)
 */
export function useNotifications(onForegroundMessage, onNotificationTap) {
  const unsubscribeRef = useRef(null);

  useEffect(() => {
    // Configure how notifications appear when app is in foreground
    Notifications.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowAlert: true,
        shouldPlaySound: true,
        shouldSetBadge: false,
      }),
    });

    // Setup foreground handler
    unsubscribeRef.current = setupNotificationHandlers(onForegroundMessage);

    // Handle taps on local notifications (from expo-notifications)
    const localNotifSub = Notifications.addNotificationResponseReceivedListener((response) => {
      console.log('[FCM] Local notification tapped:', response);
      const data = response.notification.request.content.data;
      if (onNotificationTap && data && Object.keys(data).length > 0) {
        onNotificationTap(data);
      }
    });

    // Handle notification that opened the app from quit state
    messaging()
      .getInitialNotification()
      .then((remoteMessage) => {
        if (remoteMessage) {
          console.log('[FCM] App opened from notification:', remoteMessage);
          if (onNotificationTap && remoteMessage.data) {
            onNotificationTap(remoteMessage.data);
          }
        }
      });

    // Handle notification tap when app is in background
    const unsubscribeOpen = messaging().onNotificationOpenedApp((remoteMessage) => {
      console.log('[FCM] Notification tapped (background):', remoteMessage);
      if (onNotificationTap && remoteMessage.data) {
        onNotificationTap(remoteMessage.data);
      }
    });

    return () => {
      if (unsubscribeRef.current) unsubscribeRef.current();
      unsubscribeOpen();
      localNotifSub.remove();
    };
  }, []);
}
