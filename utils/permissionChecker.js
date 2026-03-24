import * as Camera from 'expo-camera';
import * as Location from 'expo-location';
import * as MediaLibrary from 'expo-media-library';
import * as Notifications from 'expo-notifications';
import * as Contacts from 'expo-contacts';
import * as Device from 'expo-device';
import { Platform, PermissionsAndroid } from 'react-native';

/**
 * Check current status of all app permissions
 * Returns: { allGranted: boolean, denied: string[] }
 */
export async function checkAllPermissions() {
  const deniedPermissions = [];

  try {
    // 1. Camera
    const cameraStatus = await Camera.Camera.getCameraPermissionsAsync();
    console.log('[PermissionChecker] Camera status:', cameraStatus.status);
    if (cameraStatus.status !== 'granted') {
      deniedPermissions.push('camera');
    }

    // 2. Microphone
    const micStatus = await Camera.Camera.getMicrophonePermissionsAsync();
    console.log('[PermissionChecker] Microphone status:', micStatus.status);
    if (micStatus.status !== 'granted') {
      deniedPermissions.push('microphone');
    }

    // 3. Location
    const locationStatus = await Location.getForegroundPermissionsAsync();
    console.log('[PermissionChecker] Location status:', locationStatus.status);
    if (locationStatus.status !== 'granted') {
      deniedPermissions.push('location');
    }

    // 4. Notifications (use expo-notifications for proper Android 13+ support)
    if (Device.isDevice) {
      const notificationStatus = await Notifications.getPermissionsAsync();
      console.log('[PermissionChecker] Notification status:', notificationStatus.status);
      console.log('[PermissionChecker] Notification granted:', notificationStatus.granted);

      // Check both status and granted flag
      if (notificationStatus.status !== 'granted' || !notificationStatus.granted) {
        deniedPermissions.push('notifications');
      }
    } else {
      console.log('[PermissionChecker] Emulator - skipping notification check');
    }

    // 5. Storage/Media Library
    const mediaStatus = await MediaLibrary.getPermissionsAsync();
    console.log('[PermissionChecker] Storage status:', mediaStatus.status);
    if (mediaStatus.status !== 'granted') {
      deniedPermissions.push('storage');
    }

    // 6. Contacts
    const contactsStatus = await Contacts.getPermissionsAsync();
    console.log('[PermissionChecker] Contacts status:', contactsStatus.status);
    if (contactsStatus.status !== 'granted') {
      deniedPermissions.push('contacts');
    }

    // 7. Call Logs (Android only - use PermissionsAndroid)
    if (Platform.OS === 'android') {
      try {
        const callLogGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.READ_CALL_LOG
        );
        console.log('[PermissionChecker] Call Log status:', callLogGranted ? 'granted' : 'denied');
        if (!callLogGranted) {
          deniedPermissions.push('calllogs');
        }
      } catch (err) {
        console.error('[PermissionChecker] Call Log check error:', err);
        // If check fails, assume denied
        deniedPermissions.push('calllogs');
      }
    }

    // 8. Phone State (Android only - for SIM info and device metadata)
    if (Platform.OS === 'android') {
      try {
        const phoneStateGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE
        );
        console.log('[PermissionChecker] Phone State status:', phoneStateGranted ? 'granted' : 'denied');
        if (!phoneStateGranted) {
          deniedPermissions.push('phone');
        }
      } catch (err) {
        console.error('[PermissionChecker] Phone State check error:', err);
        // If check fails, assume denied
        deniedPermissions.push('phone');
      }
    }

    const allGranted = deniedPermissions.length === 0;

    console.log('[PermissionChecker] All granted:', allGranted);
    console.log('[PermissionChecker] Denied permissions:', deniedPermissions);

    return {
      allGranted,
      denied: deniedPermissions,
    };
  } catch (err) {
    console.error('[PermissionChecker] Error checking permissions:', err);
    // On error, assume all granted to avoid blocking the app
    return {
      allGranted: true,
      denied: [],
    };
  }
}
