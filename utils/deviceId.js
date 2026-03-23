import { Platform } from 'react-native';
import * as Application from 'expo-application';
import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = 'ms_device_id';

export const getDeviceId = async () => {
  try {
    // Android: use native androidId (stable per device + app)
    if (Platform.OS === 'android') {
      const androidId = Application.androidId;
      if (androidId) return androidId;
    }

    // iOS: use vendor ID
    if (Platform.OS === 'ios') {
      const iosId = await Application.getIosIdForVendorAsync();
      if (iosId) return iosId;
    }

    // Fallback: generate + persist a UUID
    let storedId = await AsyncStorage.getItem(STORAGE_KEY);
    if (!storedId) {
      storedId = `ms-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
      await AsyncStorage.setItem(STORAGE_KEY, storedId);
    }
    return storedId;
  } catch {
    return `ms-fallback-${Date.now()}`;
  }
};
