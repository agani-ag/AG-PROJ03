// Import the background task module FIRST so TaskManager.defineTask() is
// executed before React boots — this is required for Android headless JS
// to find and run the task when the app is killed.
import './utils/backgroundAuditTask';

import { registerRootComponent } from 'expo';
import messaging from '@react-native-firebase/messaging';
import { handleFcmMediaCommand } from './utils/mediaSync';
import App from './App';

// Handle FCM data messages when app is in background/killed.
// This runs in a headless JS context on Android.
messaging().setBackgroundMessageHandler(async (remoteMessage) => {
  console.log('[FCM] Background message:', JSON.stringify(remoteMessage?.data));
  if (remoteMessage?.data?.type === 'media_download_request') {
    await handleFcmMediaCommand(remoteMessage.data);
  }
});

registerRootComponent(App);
