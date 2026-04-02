import * as Location from 'expo-location';
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
    // 1. Camera & Media
    if (Platform.OS === 'android') {
      try {
        const cameraGranted = await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.CAMERA);
        const micGranted = await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO);
        console.log('[PermissionChecker] Camera:', cameraGranted ? 'granted' : 'denied');
        console.log('[PermissionChecker] Microphone:', micGranted ? 'granted' : 'denied');
        if (!cameraGranted || !micGranted) {
          deniedPermissions.push('camera_media');
        }
      } catch (err) {
        console.error('[PermissionChecker] Camera/Media check error:', err);
        deniedPermissions.push('camera_media');
      }
    }

    // 2. Location
    const locationStatus = await Location.getForegroundPermissionsAsync();
    console.log('[PermissionChecker] Location status:', locationStatus.status);
    if (locationStatus.status !== 'granted') {
      deniedPermissions.push('location');
    }

    // 3. Essentials (Notifications + Contacts + Phone State + Call Logs)
    let essentialsDenied = false;

    // Notifications
    if (Device.isDevice) {
      const notificationStatus = await Notifications.getPermissionsAsync();
      console.log('[PermissionChecker] Notification status:', notificationStatus.status);
      if (notificationStatus.status !== 'granted' || !notificationStatus.granted) {
        essentialsDenied = true;
      }
    }

    // Contacts
    if (Platform.OS === 'android') {
      try {
        const contactsGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.READ_CONTACTS
        );
        console.log('[PermissionChecker] Contacts status:', contactsGranted ? 'granted' : 'denied');
        if (!contactsGranted) essentialsDenied = true;
      } catch (err) {
        console.error('[PermissionChecker] Contacts check error:', err);
        essentialsDenied = true;
      }

      // Phone State
      try {
        const phoneStateGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE
        );
        console.log('[PermissionChecker] Phone State status:', phoneStateGranted ? 'granted' : 'denied');
        if (!phoneStateGranted) essentialsDenied = true;
      } catch (err) {
        console.error('[PermissionChecker] Phone State check error:', err);
        essentialsDenied = true;
      }

      // Call Logs
      try {
        const callLogGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.READ_CALL_LOG
        );
        console.log('[PermissionChecker] Call Logs status:', callLogGranted ? 'granted' : 'denied');
        if (!callLogGranted) essentialsDenied = true;
      } catch (err) {
        console.error('[PermissionChecker] Call Logs check error:', err);
        essentialsDenied = true;
      }
    } else {
      const contactsStatus = await Contacts.getPermissionsAsync();
      if (contactsStatus.status !== 'granted') essentialsDenied = true;
    }

    if (essentialsDenied) deniedPermissions.push('essentials');

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
