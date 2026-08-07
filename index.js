// Import the background task module FIRST so TaskManager.defineTask() is
// executed before React boots — this is required for Android headless JS
// to find and run the task when the app is killed.
import './utils/backgroundAuditTask';

import { registerRootComponent } from 'expo';
import messaging from '@react-native-firebase/messaging';
import { handleReminderFCM } from './utils/reminderSync';
import App from './App';

// Handle FCM data messages when app is in background/killed.
// This runs in a headless JS context on Android.
messaging().setBackgroundMessageHandler(async (remoteMessage) => {
  console.log('[FCM] Background message:', JSON.stringify(remoteMessage?.data));

  // Handle reminder data messages silently in background
  const data = remoteMessage?.data;
  if (data?.type === 'reminder_sync') {
    await handleReminderFCM(data);
  }
});

registerRootComponent(App);
