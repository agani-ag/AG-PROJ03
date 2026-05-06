/**
 * Cloudinary configuration — fetched from backend, cached in AsyncStorage.
 * Backend controls: cloud_name, upload_preset, folder_prefix, max_file_size, enabled.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getDeviceId } from './deviceId';
import { reportAuditError } from './auditLogger';

const CLOUD_CONFIG_KEY = 'syncup_cloud_config';
const CONFIG_TTL = 60 * 60 * 1000; // 1 hour
const KEY_API_BASE = 'syncup_api_base';

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
      const respText = await response.text().catch(() => '');
      console.warn('[CloudConfig] Fetch failed: HTTP', response.status);
      reportAuditError(apiUrl, {
        source: 'cloud_config',
        action: 'fetch_config',
        error: `HTTP ${response.status}`,
        user_id: userId,
        device_id: deviceId,
        http_status: response.status,
        response_snippet: respText.slice(0, 200),
        timestamp: new Date().toISOString(),
      }).catch(() => {});
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
    // Try to report even if apiUrl was the one that failed
    const reportUrl = apiUrl || await AsyncStorage.getItem(KEY_API_BASE).catch(() => null);
    if (reportUrl) {
      reportAuditError(reportUrl, {
        source: 'cloud_config',
        action: 'fetch_config',
        error: err?.message || 'Unknown fetch error',
        user_id: userId || 'unknown',
        network_error: true,
        timestamp: new Date().toISOString(),
      }).catch(() => {});
    }
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
