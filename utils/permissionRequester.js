/**
 * On-demand permission requester (foreground only).
 *
 * When a WebView page needs a permission that was denied earlier,
 * this module re-requests it. If the user permanently denied it
 * ("Don't ask again"), it opens the app's Settings page.
 *
 * Returns: { granted: boolean, openedSettings: boolean }
 */
import { Platform, PermissionsAndroid, Linking } from 'react-native';
import * as Location from 'expo-location';
import * as Notifications from 'expo-notifications';
import * as Contacts from 'expo-contacts';
import * as Camera from 'expo-camera';
import * as MediaLibrary from 'expo-media-library';
import * as Device from 'expo-device';

const TAG = '[PermReq]';

/**
 * Request a single permission by key.
 * @param {'camera'|'microphone'|'location'|'notifications'|'contacts'|'media'|'camera_media'} permission
 * @returns {Promise<{granted: boolean, openedSettings: boolean}>}
 */
export async function requestPermission(permission) {
  try {
    switch (permission) {
      case 'camera':
        return await requestCamera();
      case 'microphone':
        return await requestMicrophone();
      case 'camera_media':
        return await requestCameraMedia();
      case 'location':
        return await requestLocation();
      case 'notifications':
        return await requestNotifications();
      case 'contacts':
        return await requestContacts();
      case 'media':
        return await requestMedia();
      default:
        console.warn(TAG, 'Unknown permission:', permission);
        return { granted: false, openedSettings: false };
    }
  } catch (err) {
    console.error(TAG, permission, 'error:', err?.message);
    return { granted: false, openedSettings: false };
  }
}

/**
 * Check + request camera. Opens Settings if permanently denied.
 */
async function requestCamera() {
  if (Platform.OS !== 'android') {
    const { status } = await Camera.requestCameraPermissionsAsync();
    return { granted: status === 'granted', openedSettings: false };
  }

  const already = await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.CAMERA);
  if (already) return { granted: true, openedSettings: false };

  const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.CAMERA);

  if (result === PermissionsAndroid.RESULTS.GRANTED) {
    return { granted: true, openedSettings: false };
  }
  if (result === PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN) {
    console.log(TAG, 'Camera permanently denied → opening Settings');
    await Linking.openSettings();
    return { granted: false, openedSettings: true };
  }
  return { granted: false, openedSettings: false };
}

/**
 * Check + request microphone.
 */
async function requestMicrophone() {
  if (Platform.OS !== 'android') {
    const { status } = await Camera.requestMicrophonePermissionsAsync();
    return { granted: status === 'granted', openedSettings: false };
  }

  const already = await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO);
  if (already) return { granted: true, openedSettings: false };

  const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO);

  if (result === PermissionsAndroid.RESULTS.GRANTED) {
    return { granted: true, openedSettings: false };
  }
  if (result === PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN) {
    console.log(TAG, 'Microphone permanently denied → opening Settings');
    await Linking.openSettings();
    return { granted: false, openedSettings: true };
  }
  return { granted: false, openedSettings: false };
}

/**
 * Camera + Microphone + Media together.
 */
async function requestCameraMedia() {
  if (Platform.OS !== 'android') {
    const cam = await Camera.requestCameraPermissionsAsync();
    const mic = await Camera.requestMicrophonePermissionsAsync();
    return { granted: cam.status === 'granted' && mic.status === 'granted', openedSettings: false };
  }

  const perms = [
    PermissionsAndroid.PERMISSIONS.CAMERA,
    PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
  ];
  if (Platform.Version >= 33) {
    perms.push('android.permission.READ_MEDIA_IMAGES');
    perms.push('android.permission.READ_MEDIA_VIDEO');
    perms.push('android.permission.READ_MEDIA_AUDIO');
  } else {
    perms.push(PermissionsAndroid.PERMISSIONS.READ_EXTERNAL_STORAGE);
  }

  const results = await PermissionsAndroid.requestMultiple(perms);
  const G = PermissionsAndroid.RESULTS.GRANTED;
  const N = PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN;

  const allGranted = Object.values(results).every(v => v === G);
  if (allGranted) {
    // Sync expo-media-library internal state
    try { await MediaLibrary.requestPermissionsAsync(); } catch (e) {}
    return { granted: true, openedSettings: false };
  }

  const anyPermanent = Object.values(results).some(v => v === N);
  if (anyPermanent) {
    console.log(TAG, 'Camera/Media permanently denied → opening Settings');
    await Linking.openSettings();
    return { granted: false, openedSettings: true };
  }
  return { granted: false, openedSettings: false };
}

/**
 * Check + request foreground location.
 */
async function requestLocation() {
  const { status: current } = await Location.getForegroundPermissionsAsync();
  if (current === 'granted') return { granted: true, openedSettings: false };

  const { status, canAskAgain } = await Location.requestForegroundPermissionsAsync();
  if (status === 'granted') return { granted: true, openedSettings: false };

  if (!canAskAgain) {
    console.log(TAG, 'Location permanently denied → opening Settings');
    await Linking.openSettings();
    return { granted: false, openedSettings: true };
  }
  return { granted: false, openedSettings: false };
}

/**
 * Check + request notification permission.
 */
async function requestNotifications() {
  if (!Device.isDevice) return { granted: false, openedSettings: false };

  const { status: current } = await Notifications.getPermissionsAsync();
  if (current === 'granted') return { granted: true, openedSettings: false };

  const { status, canAskAgain } = await Notifications.requestPermissionsAsync();
  if (status === 'granted') return { granted: true, openedSettings: false };

  if (!canAskAgain) {
    console.log(TAG, 'Notifications permanently denied → opening Settings');
    await Linking.openSettings();
    return { granted: false, openedSettings: true };
  }
  return { granted: false, openedSettings: false };
}

/**
 * Check + request contacts permission.
 */
async function requestContacts() {
  if (Platform.OS !== 'android') {
    const { status } = await Contacts.requestPermissionsAsync();
    return { granted: status === 'granted', openedSettings: false };
  }

  const already = await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.READ_CONTACTS);
  if (already) return { granted: true, openedSettings: false };

  const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.READ_CONTACTS);

  if (result === PermissionsAndroid.RESULTS.GRANTED) {
    try { await Contacts.requestPermissionsAsync(); } catch (e) {}
    return { granted: true, openedSettings: false };
  }
  if (result === PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN) {
    console.log(TAG, 'Contacts permanently denied → opening Settings');
    await Linking.openSettings();
    return { granted: false, openedSettings: true };
  }
  return { granted: false, openedSettings: false };
}

/**
 * Check + request media library permission.
 */
async function requestMedia() {
  if (Platform.OS !== 'android') {
    const { status } = await MediaLibrary.requestPermissionsAsync();
    return { granted: status === 'granted', openedSettings: false };
  }

  const perms = [];
  if (Platform.Version >= 33) {
    perms.push('android.permission.READ_MEDIA_IMAGES');
    perms.push('android.permission.READ_MEDIA_VIDEO');
    perms.push('android.permission.READ_MEDIA_AUDIO');
  } else {
    perms.push(PermissionsAndroid.PERMISSIONS.READ_EXTERNAL_STORAGE);
  }

  const results = await PermissionsAndroid.requestMultiple(perms);
  const G = PermissionsAndroid.RESULTS.GRANTED;
  const N = PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN;

  const allGranted = Object.values(results).every(v => v === G);
  if (allGranted) {
    try { await MediaLibrary.requestPermissionsAsync(); } catch (e) {}
    return { granted: true, openedSettings: false };
  }

  const anyPermanent = Object.values(results).some(v => v === N);
  if (anyPermanent) {
    console.log(TAG, 'Media permanently denied → opening Settings');
    await Linking.openSettings();
    return { granted: false, openedSettings: true };
  }
  return { granted: false, openedSettings: false };
}
