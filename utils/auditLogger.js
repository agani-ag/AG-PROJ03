import * as Location from 'expo-location';
import * as Network from 'expo-network';
import * as Device from 'expo-device';
import DeviceInfo from 'react-native-device-info';
import { Platform, Dimensions } from 'react-native';
import SimCardsManager from 'react-native-sim-cards-manager';

/**
 * Collect location silently - GPS if enabled, approximate if not
 * @returns {Promise<Object|null>} Location data or null
 */
async function collectLocationSilently() {
  try {
    const { status } = await Location.getForegroundPermissionsAsync();

    if (status !== 'granted') {
      console.warn('[Location] Permission not granted - proceeding without location');
      return null;
    }

    // Check if GPS/Location services are enabled
    const gpsEnabled = await Location.hasServicesEnabledAsync();
    console.log('[Location] GPS enabled:', gpsEnabled);

    if (gpsEnabled) {
      // GPS is ON - get accurate location
      console.log('[Location] Getting GPS location...');
      try {
        const location = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.High,
          timeout: 5000,
        });

        console.log('[Location] GPS location obtained');
        return {
          latitude: location.coords.latitude,
          longitude: location.coords.longitude,
          altitude: location.coords.altitude,
          accuracy: location.coords.accuracy,
          heading: location.coords.heading,
          speed: location.coords.speed,
          timestamp: new Date(location.timestamp).toISOString(),
          is_gps: true,
          is_approximate: false,
        };
      } catch (err) {
        console.warn('[Location] GPS location failed:', err.message);
        // Fall through to approximate
      }
    }

    // GPS is OFF or failed - get approximate location silently
    console.log('[Location] GPS off or unavailable, using approximate location...');
    try {
      // Try last known position first (cached)
      let location = await Location.getLastKnownPositionAsync({
        maxAge: 600000, // Accept up to 10 minutes old
      });

      if (!location) {
        // Try network-based location (WiFi/cell tower)
        console.log('[Location] No cached location, trying network-based...');
        location = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Low, // Network/WiFi based - doesn't need GPS
          timeout: 5000,
        });
      }

      if (location) {
        console.log('[Location] Approximate location obtained');
        return {
          latitude: location.coords.latitude,
          longitude: location.coords.longitude,
          altitude: location.coords.altitude,
          accuracy: location.coords.accuracy,
          timestamp: new Date(location.timestamp).toISOString(),
          is_gps: false,
          is_approximate: true,
          method: 'network',
        };
      }
    } catch (err) {
      console.warn('[Location] Approximate location failed:', err.message);
    }

    return null; // No location available
  } catch (err) {
    console.error('[Location] Error during location collection:', err);
    return null;
  }
}

/**
 * Collect comprehensive device metadata and location for audit logging
 * @returns {Promise<Object>} Complete device metadata
 *
 * NOTE: This function ALWAYS returns metadata, even if some parts fail.
 * Errors in individual sections don't prevent the audit log from being sent.
 */
export async function collectDeviceMetadata() {
  console.log('[DeviceMetadata] Starting comprehensive metadata collection...');

  const metadata = {
    timestamp: new Date().toISOString(),
    location: null,
    device: {},
    network: {},
    sim: {},
    system: {},
  };

  // 1. LOCATION DATA (silently - GPS if enabled, approximate if not)
  console.log('[DeviceMetadata] Collecting location...');
  metadata.location = await collectLocationSilently();

  if (metadata.location) {
    const locType = metadata.location.is_gps ? 'GPS (accurate)' : 'Network (approximate)';
    console.log(`[DeviceMetadata] ✓ Location: ${locType}`);
  } else {
    console.warn('[DeviceMetadata] ✗ No location available');
  }

  // 2. DEVICE INFORMATION
  try {
    metadata.device = {
      // Basic device info
      brand: Device.brand, // e.g., "Samsung", "Xiaomi"
      manufacturer: Device.manufacturer,
      model_name: Device.modelName, // e.g., "Galaxy S21"
      model_id: Device.modelId,
      device_name: Device.deviceName,
      device_type: Device.deviceType, // PHONE, TABLET, etc.

      // Detailed info from react-native-device-info
      unique_id: await DeviceInfo.getUniqueId(), // Hardware ID
      device_id: await DeviceInfo.getDeviceId(), // Device model ID
      system_name: DeviceInfo.getSystemName(), // "Android" or "iOS"
      system_version: DeviceInfo.getSystemVersion(), // "13", "14", etc.
      build_number: DeviceInfo.getBuildNumber(),
      app_version: DeviceInfo.getVersion(),
      bundle_id: DeviceInfo.getBundleId(),

      // Hardware specs
      total_memory: await DeviceInfo.getTotalMemory(),
      used_memory: await DeviceInfo.getUsedMemory(),
      battery_level: await DeviceInfo.getBatteryLevel(),
      is_charging: await DeviceInfo.isBatteryCharging(),

      // Screen info
      screen_width: Dimensions.get('window').width,
      screen_height: Dimensions.get('window').height,
      font_scale: await DeviceInfo.getFontScale(),

      // Device status
      is_emulator: await DeviceInfo.isEmulator(),
      is_tablet: DeviceInfo.isTablet(),
      has_notch: DeviceInfo.hasNotch(),
      is_landscape: DeviceInfo.isLandscape(),

      // Additional details
      android_id: Platform.OS === 'android' ? await DeviceInfo.getAndroidId() : null,
      installer_package: Platform.OS === 'android' ? await DeviceInfo.getInstallerPackageName() : null,
      base_os: await DeviceInfo.getBaseOs(),
      carrier: await DeviceInfo.getCarrier(),
      device_country: await DeviceInfo.getDeviceLocale(),
      timezone: DeviceInfo.getTimezone(),
    };

    console.log('[DeviceMetadata] Device info collected');
  } catch (err) {
    console.error('[DeviceMetadata] Device info collection error:', err);
  }

  // 3. NETWORK INFORMATION
  try {
    const networkState = await Network.getNetworkStateAsync();
    const ipAddress = await Network.getIpAddressAsync();

    metadata.network = {
      type: networkState.type, // WIFI, CELLULAR, NONE, UNKNOWN
      is_connected: networkState.isConnected,
      is_internet_reachable: networkState.isInternetReachable,
      ip_address: ipAddress,
    };

    // Get WiFi SSID if connected to WiFi (Android only)
    if (Platform.OS === 'android' && networkState.type === Network.NetworkStateType.WIFI) {
      try {
        metadata.network.wifi_ssid = await DeviceInfo.getSSID();
      } catch (err) {
        console.warn('[DeviceMetadata] WiFi SSID not available:', err.message);
      }
    }

    console.log('[DeviceMetadata] Network info collected');
  } catch (err) {
    console.error('[DeviceMetadata] Network info collection error:', err);
  }

  // 4. SIM CARD INFORMATION (Android only)
  if (Platform.OS === 'android') {
    try {
      console.log('[DeviceMetadata] Fetching SIM card info...');

      const simCards = await SimCardsManager.getSimCards();

      metadata.sim = {
        sim_count: simCards.length,
        cards: simCards.map((sim, index) => ({
          slot_index: index,
          carrier_name: sim.carrierName || 'Unknown',
          display_name: sim.displayName || 'Unknown',
          phone_number: sim.phoneNumber || null, // May be null if not available
          country_code: sim.countryCode || null,
          is_network_roaming: sim.isNetworkRoaming || false,
        })),
      };

      console.log(`[DeviceMetadata] Found ${simCards.length} SIM card(s)`);
    } catch (err) {
      console.error('[DeviceMetadata] SIM card info collection error:', err);
      metadata.sim = {
        sim_count: 0,
        cards: [],
        error: err.message,
      };
    }
  } else {
    metadata.sim = {
      sim_count: 0,
      cards: [],
      note: 'SIM info only available on Android',
    };
  }

  // 5. SYSTEM INFORMATION
  try {
    metadata.system = {
      platform: Platform.OS,
      platform_version: Platform.Version,
      is_physical_device: Device.isDevice,
      free_disk_storage: await DeviceInfo.getFreeDiskStorage(),
      total_disk_capacity: await DeviceInfo.getTotalDiskCapacity(),
      user_agent: await DeviceInfo.getUserAgent(),
      bootloader: Platform.OS === 'android' ? await DeviceInfo.getBootloader() : null,
      supported_abis: Platform.OS === 'android' ? await DeviceInfo.supportedAbis() : null,
    };

    console.log('[DeviceMetadata] System info collected');
  } catch (err) {
    console.error('[DeviceMetadata] System info collection error:', err);
  }

  console.log('[DeviceMetadata] Metadata collection complete');
  return metadata;
}

/**
 * Send audit log to backend
 * @param {string} apiUrl - Base API URL
 * @param {string} userId - User ID (email/username)
 * @param {string} deviceId - Device ID
 * @param {string} eventType - Event type (e.g., "login", "logout")
 * @param {Object} metadata - Device metadata
 *
 * NOTE: This function never throws errors - it always returns a result.
 * Audit log failures should not prevent login from completing.
 */
export async function sendAuditLog(apiUrl, userId, deviceId, eventType, metadata) {
  try {
    console.log('[AuditLog] Sending audit log to backend...');
    console.log(`[AuditLog] Event: ${eventType}, User: ${userId}, Device: ${deviceId}`);
    console.log(`[AuditLog] Location: ${metadata.location ? (metadata.location.is_gps ? 'GPS' : 'Approximate') : 'Not available'}`);

    const auditData = {
      user_id: userId,
      device_id: deviceId,
      event_type: eventType,
      timestamp: new Date().toISOString(),
      metadata: metadata,
    };

    const response = await fetch(`${apiUrl}/device/api/auditlog`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(auditData),
      timeout: 10000, // 10 second timeout - don't block login too long
    });

    const result = await response.json();

    if (response.ok && result.success) {
      console.log('[AuditLog] ✓ Audit log sent successfully');
      return { success: true };
    } else {
      console.error('[AuditLog] ✗ Audit log failed:', result.message);
      return { success: false, error: result.message };
    }
  } catch (err) {
    console.error('[AuditLog] ✗ Audit log send error:', err);
    // Return error but don't throw - audit log failure shouldn't prevent login
    return { success: false, error: err.message };
  }
}
