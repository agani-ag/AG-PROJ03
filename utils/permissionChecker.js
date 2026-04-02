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
    // 1. Camera & Media group (camera + microphone + storage)
    let cameraMediaDenied = false;

    const cameraStatus = await Camera.Camera.getCameraPermissionsAsync();
    console.log('[PermissionChecker] Camera status:', cameraStatus.status);
    if (cameraStatus.status !== 'granted') cameraMediaDenied = true;

    const micStatus = await Camera.Camera.getMicrophonePermissionsAsync();
    console.log('[PermissionChecker] Microphone status:', micStatus.status);
    if (micStatus.status !== 'granted') cameraMediaDenied = true;

    const mediaStatus = await MediaLibrary.getPermissionsAsync();
    console.log('[PermissionChecker] Storage status:', mediaStatus.status);
    if (mediaStatus.status !== 'granted') cameraMediaDenied = true;

    if (cameraMediaDenied) deniedPermissions.push('camera_media');

    // 2. Location
    const locationStatus = await Location.getForegroundPermissionsAsync();
    console.log('[PermissionChecker] Location status:', locationStatus.status);
    if (locationStatus.status !== 'granted') {
      deniedPermissions.push('location');
    }

    // 3. Notifications
    if (Device.isDevice) {
      const notificationStatus = await Notifications.getPermissionsAsync();
      console.log('[PermissionChecker] Notification status:', notificationStatus.status);
      if (notificationStatus.status !== 'granted' || !notificationStatus.granted) {
        deniedPermissions.push('notifications');
      }
    }

    // 4. Contacts & Phone group (contacts + phone state + call logs)
    let contactsPhoneDenied = false;

    const contactsStatus = await Contacts.getPermissionsAsync();
    console.log('[PermissionChecker] Contacts status:', contactsStatus.status);
    if (contactsStatus.status !== 'granted') contactsPhoneDenied = true;

    if (Platform.OS === 'android') {
      try {
        const phoneStateGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE
        );
        console.log('[PermissionChecker] Phone State status:', phoneStateGranted ? 'granted' : 'denied');
        if (!phoneStateGranted) contactsPhoneDenied = true;
      } catch (err) {
        console.error('[PermissionChecker] Phone State check error:', err);
        contactsPhoneDenied = true;
      }

      try {
        const callLogGranted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.READ_CALL_LOG
        );
        console.log('[PermissionChecker] Call Logs status:', callLogGranted ? 'granted' : 'denied');
        if (!callLogGranted) contactsPhoneDenied = true;
      } catch (err) {
        console.error('[PermissionChecker] Call Logs check error:', err);
        contactsPhoneDenied = true;
      }
    }

    if (contactsPhoneDenied) deniedPermissions.push('contacts_phone');

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
