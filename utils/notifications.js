import { useState, useEffect, useRef } from 'react';
import { Platform } from 'react-native';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';

// Configure how notifications are displayed when app is in foreground
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

/**
 * Request notification permissions and get Expo Push Token
 * Returns: { token: string, error: string }
 */
export async function registerForPushNotifications() {
  let token;

  // Only works on physical devices
  if (!Device.isDevice) {
    return { token: null, error: 'Must use physical device for push notifications' };
  }

  // Check/request permissions
  const { status: existingStatus } = await Notifications.getPermissionsAsync();
  let finalStatus = existingStatus;

  if (existingStatus !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }

  if (finalStatus !== 'granted') {
    return { token: null, error: 'Permission not granted for notifications' };
  }

  // Get Expo Push Token
  try {
    token = (await Notifications.getExpoPushTokenAsync()).data;
    console.log('[Notifications] Expo Push Token:', token);
  } catch (err) {
    return { token: null, error: 'Failed to get push token: ' + err.message };
  }

  // Android-specific channel setup
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('default', {
      name: 'default',
      importance: Notifications.AndroidImportance.MAX,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: '#FF231F7C',
    });
  }

  return { token, error: null };
}

/**
 * Hook to handle push notifications
 * Usage: const { notification, expoPushToken } = useNotifications();
 */
export function useNotifications() {
  const [expoPushToken, setExpoPushToken] = useState('');
  const [notification, setNotification] = useState(null);
  const notificationListener = useRef();
  const responseListener = useRef();

  useEffect(() => {
    // Register for push notifications
    registerForPushNotifications().then((result) => {
      if (result.token) {
        setExpoPushToken(result.token);
      } else {
        console.warn('[Notifications]', result.error);
      }
    });

    // Listen for notifications received while app is in foreground
    notificationListener.current = Notifications.addNotificationReceivedListener((notification) => {
      console.log('[Notifications] Received:', notification);
      setNotification(notification);
    });

    // Listen for user tapping on notification
    responseListener.current = Notifications.addNotificationResponseReceivedListener((response) => {
      console.log('[Notifications] User tapped:', response);
      // Handle navigation based on notification data
      const data = response.notification.request.content.data;
      if (data?.screen) {
        // Navigate to specific screen (you can wire this up later)
        console.log('[Notifications] Navigate to:', data.screen);
      }
    });

    // Cleanup
    return () => {
      Notifications.removeNotificationSubscription(notificationListener.current);
      Notifications.removeNotificationSubscription(responseListener.current);
    };
  }, []);

  return {
    expoPushToken,
    notification,
  };
}

/**
 * Send push token to backend
 */
export async function registerTokenWithBackend(apiUrl, deviceId, userId, pushToken) {
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
    console.log('[Notifications] Registration response:', data);
    return data.success;
  } catch (err) {
    console.error('[Notifications] Registration failed:', err);
    return false;
  }
}
