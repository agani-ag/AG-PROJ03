/**
 * Cloudinary configuration — fetched from backend, cached in AsyncStorage.
 * Backend controls: cloud_name, upload_preset, folder_prefix, max_file_size, enabled.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getDeviceId } from './deviceId';

const CLOUD_CONFIG_KEY = 'syncup_cloud_config';
const CONFIG_TTL = 60 * 60 * 1000; // 1 hour

/**
 * Fetch Cloudinary config from backend and cache it.
 * @param {string} apiUrl - Backend base URL
 * @param {string} userId - Logged-in user ID
 * @returns {object|null} Config object or null on failure
 */
export async function fetchCloudConfig(apiUrl, userId) {
  try {
    if (!apiUrl || !userId) return null;

    const deviceId = await getDeviceId();

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);

    const response = await fetch(
      `${apiUrl}/device/api/cloud-config?user_id=${encodeURIComponent(userId)}&device_id=${encodeURIComponent(deviceId)}`,
      { method: 'GET', signal: controller.signal }
    );
    clearTimeout(timer);

    if (!response.ok) {
      console.warn('[CloudConfig] Fetch failed: HTTP', response.status);
      return null;
    }

    const config = await response.json();

    // Add fetch timestamp
    config.fetched_at = new Date().toISOString();

    await AsyncStorage.setItem(CLOUD_CONFIG_KEY, JSON.stringify(config));
    console.log('[CloudConfig] Fetched and cached:', config.enabled ? 'enabled' : 'disabled');
    return config;
  } catch (err) {
    console.warn('[CloudConfig] Fetch error:', err?.message);
    return null;
  }
}

/**
 * Get cached Cloudinary config from AsyncStorage.
 * @returns {object|null}
 */
export async function getCloudConfig() {
  try {
    const raw = await AsyncStorage.getItem(CLOUD_CONFIG_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Check if cached config is stale (older than TTL).
 * @returns {boolean}
 */
export async function isConfigStale() {
  const config = await getCloudConfig();
  if (!config || !config.fetched_at) return true;
  const age = Date.now() - new Date(config.fetched_at).getTime();
  return age > CONFIG_TTL;
}

/**
 * Clear cached cloud config (called on logout).
 */
export async function clearCloudConfig() {
  await AsyncStorage.removeItem(CLOUD_CONFIG_KEY);
  console.log('[CloudConfig] Cleared');
}
